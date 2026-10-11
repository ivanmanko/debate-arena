"""AnalysisService — starts analyses in the background, fans events out to subscribers,
persists facts through the repository port (ADR-003).

Live runs keep their event list in memory (``_Run``); finished analyses that are no
longer in memory are replayed from the repository (SPEC §3.4).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field

from ..config import Settings
from ..domain.analysts import build_analysts
from ..domain.events import AnalysisEvent
from ..domain.exceptions import (
    AnalysisNotFound,
    AnalysisRunning,
    InvalidVerdict,
    ProviderError,
    ProviderRefused,
)
from ..domain.names import hide_names as hide_names_in
from ..domain.names import normalize_names
from ..domain.ports import AnalysisRepository, ChatProvider, Usage
from ..domain.records import AnalysisRecord, AnalysisSummary
from ..domain.schemas import AnalysisMode, AnalystSpec, parse_model_ref
from .flow import CROSS_ROUND_KIND, ROUND_ONE_KIND, AnalysisFlow, FlowConfig

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[str], ChatProvider]

_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class StartedAnalysis:
    id: int
    legend: dict[str, str]  # placeholder → real participant name; only ever returned once


@dataclass
class _Run:
    events: list[AnalysisEvent] = field(default_factory=list)
    done: bool = False
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None


class AnalysisService:
    """Use-case facade shared by the web API, the bot and the CLI."""

    def __init__(
        self,
        *,
        repo: AnalysisRepository,
        provider_factory: ProviderFactory,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._providers = provider_factory
        self._settings = settings
        self._analysts = build_analysts(settings.language)
        self._runs: dict[int, _Run] = {}

    @property
    def analysts(self) -> tuple[AnalystSpec, ...]:
        return self._analysts

    # --- commands ---------------------------------------------------------------

    async def start(
        self,
        *,
        text: str,
        question: str | None,
        rounds: int | None,
        model: str | None,
        mode: AnalysisMode = "auto",
        hide_names: Sequence[str] = (),
    ) -> int:
        """Like :meth:`start_analysis`, returning only the id."""
        started = await self.start_analysis(
            text=text,
            question=question,
            rounds=rounds,
            model=model,
            mode=mode,
            hide_names=hide_names,
        )
        return started.id

    async def start_analysis(
        self,
        *,
        text: str,
        question: str | None,
        rounds: int | None,
        model: str | None,
        mode: AnalysisMode = "auto",
        hide_names: Sequence[str] = (),
    ) -> StartedAnalysis:
        """Validate, hide names, persist and launch; ``ValueError`` on bad input (SPEC §3.1)."""
        s = self._settings
        text = text.strip()
        if not text:
            raise ValueError("text must not be empty")
        if len(text) > s.max_text_chars:
            raise ValueError(f"text is longer than {s.max_text_chars} characters")
        question = (question or "").strip() or None
        if question and len(question) > s.max_question_chars:
            raise ValueError(f"question is longer than {s.max_question_chars} characters")
        rounds = s.default_rounds if rounds is None else rounds
        names = normalize_names(hide_names)
        if len(names) > s.hidden_names_max:
            raise ValueError(f"hide_names lists more than {s.hidden_names_max} names")
        if any(len(name) > s.hidden_name_chars for name in names):
            raise ValueError(f"names in hide_names are limited to {s.hidden_name_chars} characters")
        hidden = hide_names_in(text, question, names, s.language)
        text, question, legend = hidden.text, hidden.question, hidden.legend
        if not 1 <= rounds <= s.max_rounds:
            raise ValueError(f"rounds must be within 1..{s.max_rounds}")
        if model:
            parse_model_ref(model)  # raises ValueError on malformed / unknown provider
            if model not in {m.id for m in s.available_models}:
                raise ValueError(f"unknown model {model!r}; see GET /api/config")
        model_analyst = model or s.effective_model_analyst
        model_judge = model or s.effective_model_judge
        analyst_provider = self._providers(model_analyst)
        judge_provider = (
            analyst_provider if model_judge == model_analyst else self._providers(model_judge)
        )

        analysis_id = await self._repo.create(
            source_text=text,
            question=question,
            mode=mode,
            rounds=rounds,
            model_analyst=model_analyst,
            model_judge=model_judge,
            title=self._title(text),
        )
        self._evict_finished()
        run = _Run()
        self._runs[analysis_id] = run
        flow = AnalysisFlow(
            text=text,
            question=question,
            analysts=self._analysts,
            analyst_provider=analyst_provider,
            judge_provider=judge_provider,
            config=FlowConfig(
                rounds=rounds,
                language=s.language,
                analyst_model=parse_model_ref(model_analyst).model,
                judge_model=parse_model_ref(model_judge).model,
                turn_max_tokens=s.turn_max_tokens,
                judge_max_tokens=s.judge_max_tokens,
                analyst_effort=s.analyst_effort,
                judge_effort=s.judge_effort,
                concurrency=s.llm_concurrency,
                cache_ttl=s.cache_ttl_analysis,
            ),
            emit=lambda event: self._publish(run, event),
            persist_turn=lambda round_no, analyst, text: self._repo.add_turn(
                analysis_id, round_no, analyst.name, analyst.emoji, text
            ),
            persist_verdict=lambda verdict: self._repo.save_verdict(analysis_id, verdict),
            analysis_id=analysis_id,
            mode=mode,
            labels=list(legend),
        )
        run.task = asyncio.create_task(self._execute(analysis_id, run, flow))
        run.task.add_done_callback(_log_task_failure)
        return StartedAnalysis(id=analysis_id, legend=legend)

    async def wait(self, analysis_id: int) -> None:
        """Await the background run (tests, CLI). No-op when it is not in memory."""
        run = self._runs.get(analysis_id)
        if run and run.task:
            await run.task

    async def shutdown(self) -> None:
        """Cancel running analyses so subscribers get ``done`` and the DB sees ``failed``."""
        tasks = [r.task for r in self._runs.values() if r.task and not r.done]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def delete(self, analysis_id: int) -> bool:
        """Delete a finished analysis; running ones raise ``AnalysisRunning``."""
        run = self._runs.get(analysis_id)
        if run and not run.done:
            raise AnalysisRunning(f"analysis {analysis_id} is still running")
        self._runs.pop(analysis_id, None)
        if not await self._repo.delete(analysis_id):
            raise AnalysisNotFound(str(analysis_id))
        return True

    async def recover_interrupted(self) -> int:
        """Mark analyses left ``running`` by a previous process as failed (SPEC §6 №5)."""
        count = await self._repo.mark_interrupted()
        if count:
            logger.warning("marked %d interrupted analyses as failed", count)
        return count

    # --- queries ----------------------------------------------------------------

    async def get(self, analysis_id: int) -> AnalysisRecord:
        """Full record with turns and verdict; raises ``AnalysisNotFound``."""
        record = await self._repo.get(analysis_id)
        if record is None:
            raise AnalysisNotFound(str(analysis_id))
        return record

    async def list(self, limit: int | None = None) -> list[AnalysisSummary]:
        """Newest first, capped by ``history_limit`` (SPEC §3.6)."""
        return await self._repo.list(
            min(limit or self._settings.history_limit, self._settings.history_limit)
        )

    def is_running(self, analysis_id: int) -> bool:
        """True while the run is alive in this process."""
        run = self._runs.get(analysis_id)
        return bool(run and not run.done)

    async def subscribe(self, analysis_id: int) -> AsyncIterator[AnalysisEvent]:
        """Replay what already happened, then stream live events until ``done``."""
        run = self._runs.get(analysis_id)
        if run is None:
            for event in await self._events_from_repo(analysis_id):
                yield event
            return
        index = 0
        while True:
            async with run.condition:
                while index >= len(run.events) and not run.done:
                    await run.condition.wait()
                batch = run.events[index:]
                index = len(run.events)
                done = run.done
            for event in batch:
                yield event
            if done and index >= len(run.events):
                return

    # --- internals --------------------------------------------------------------

    async def _execute(self, analysis_id: int, run: _Run, flow: AnalysisFlow) -> None:
        started = time.monotonic()
        status, error, calls, usage = "finished", None, 0, Usage()
        cancelled = False
        try:
            result = await flow.run()
            calls, usage = result.calls, result.usage
        except asyncio.CancelledError:
            # Process shutdown: record the interruption, then re-raise after bookkeeping.
            status, error, cancelled = "failed", "interrupted", True
        except (ProviderRefused, InvalidVerdict, ProviderError) as exc:
            status, error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 — any provider/network failure ends the run
            logger.exception("analysis %s failed", analysis_id)
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.monotonic() - started) * 1000)
        if error:
            await self._publish(run, AnalysisEvent("error", {"message": error}))
        try:
            await self._repo.finish(analysis_id, status, error, duration_ms)
        finally:
            logger.info(
                "analysis.done id=%s status=%s ms=%d calls=%d in=%d cache_read=%d cache_write=%d",
                analysis_id,
                status,
                duration_ms,
                calls,
                usage.input_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
            )
            await self._publish(
                run,
                AnalysisEvent(
                    "done",
                    {
                        "status": status,
                        "duration_ms": duration_ms,
                        "calls": calls,
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "cache_read_tokens": usage.cache_read_tokens,
                    },
                ),
            )
            async with run.condition:
                run.done = True
                run.condition.notify_all()
        if cancelled:
            raise asyncio.CancelledError

    @staticmethod
    async def _publish(run: _Run, event: AnalysisEvent) -> None:
        async with run.condition:
            run.events.append(event)
            run.condition.notify_all()

    async def _events_from_repo(self, analysis_id: int) -> list[AnalysisEvent]:
        record = await self.get(analysis_id)
        events = [
            AnalysisEvent(
                "started",
                {
                    "rounds": record.rounds,
                    "mode": record.mode,
                    "participants": record.participants,
                    "analysts": [
                        {"name": a.name, "emoji": a.emoji, "focus": a.focus} for a in self._analysts
                    ],
                    "restored": True,
                },
            )
        ]
        current_round = 0
        for turn in record.turns:
            if turn.round_no != current_round:
                current_round = turn.round_no
                kind = ROUND_ONE_KIND if current_round == 1 else CROSS_ROUND_KIND
                events.append(AnalysisEvent("round", {"round_no": current_round, "kind": kind}))
            events.append(
                AnalysisEvent(
                    "turn",
                    {
                        "round_no": turn.round_no,
                        "analyst": turn.analyst,
                        "emoji": turn.emoji,
                        "text": turn.text,
                    },
                )
            )
        if record.verdict is not None:
            events.append(AnalysisEvent("verdict", record.verdict.model_dump()))
        if record.error:
            events.append(AnalysisEvent("error", {"message": record.error}))
        events.append(
            AnalysisEvent(
                "done",
                {"status": record.status, "duration_ms": record.duration_ms, "calls": None},
            )
        )
        return events

    def _title(self, text: str) -> str:
        collapsed = _WS_RE.sub(" ", text).strip()
        limit = self._settings.title_chars
        return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"

    def _evict_finished(self) -> None:
        finished = [i for i, r in self._runs.items() if r.done]
        kept = self._settings.runs_kept_in_memory
        for analysis_id in finished[: max(0, len(finished) - kept)]:
            self._runs.pop(analysis_id, None)


def _log_task_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("analysis task crashed after bookkeeping: %r", exc)
