"""AnalysisFlow — the round loop and the judge step (SPEC §4, ADR-002).

Round 1 is blind and parallel; later rounds are sequential with the full transcript;
the judge returns a structured ``Verdict`` and gets exactly one retry on an invalid one.
Everything external arrives through ports: providers, an event sink, persistence hooks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from ..domain.events import AnalysisEvent, EventSink
from ..domain.exceptions import InvalidVerdict
from ..domain.ports import ChatProvider, ChatRequest, Usage
from ..domain.prompts import (
    cross_round_user,
    judge_system_prompt,
    judge_user,
    round_one_user,
    verdict_json_schema_text,
)
from ..domain.schemas import AnalystSpec, Verdict
from ..domain.transcript import Transcript

logger = logging.getLogger(__name__)

ROUND_ONE_KIND = "Независимый разбор"
CROSS_ROUND_KIND = "Перекрёстная проверка"
JUDGE_NAME = "Судья"
JUDGE_EMOJI = "⚖️"
# One retry on an invalid verdict (SPEC §3.3).
JUDGE_ATTEMPTS = 2

PersistTurn = Callable[[int, AnalystSpec, str], Awaitable[None]]
PersistVerdict = Callable[[Verdict], Awaitable[None]]


@dataclass(frozen=True)
class FlowConfig:
    """Per-analysis knobs, resolved by the service from ``Settings`` (SPEC §7)."""

    rounds: int
    language: str
    analyst_model: str
    judge_model: str
    turn_max_tokens: int
    judge_max_tokens: int
    analyst_effort: str
    judge_effort: str
    concurrency: int


@dataclass(frozen=True)
class FlowResult:
    verdict: Verdict
    calls: int
    usage: Usage


class AnalysisFlow:
    """Runs one analysis: blind round, cross rounds, judge. Emits SPEC §3.4 events."""

    def __init__(
        self,
        *,
        text: str,
        question: str | None,
        analysts: Sequence[AnalystSpec],
        analyst_provider: ChatProvider,
        judge_provider: ChatProvider,
        config: FlowConfig,
        emit: EventSink,
        persist_turn: PersistTurn | None = None,
        persist_verdict: PersistVerdict | None = None,
        analysis_id: int | None = None,
        participants: Sequence[str] = (),
    ) -> None:
        self.text = text
        self.question = question
        self.participants = list(participants)
        self.analysts = list(analysts)
        self.analyst_provider = analyst_provider
        self.judge_provider = judge_provider
        self.config = config
        self.emit = emit
        self.persist_turn = persist_turn
        self.persist_verdict = persist_verdict
        self.analysis_id = analysis_id
        self.transcript = Transcript()
        self._calls = 0
        self._in_tokens = 0
        self._out_tokens = 0

    async def run(self) -> FlowResult:
        await self.emit(
            AnalysisEvent(
                "started",
                {
                    "rounds": self.config.rounds,
                    "mode": "dialogue" if self.participants else "text",
                    "participants": self.participants,
                    "analysts": [
                        {"name": a.name, "emoji": a.emoji, "focus": a.focus} for a in self.analysts
                    ],
                },
            )
        )
        await self._round_one()
        for round_no in range(2, self.config.rounds + 1):
            await self._cross_round(round_no)
        verdict = await self._judge()
        return FlowResult(
            verdict=verdict,
            calls=self._calls,
            usage=Usage(input_tokens=self._in_tokens, output_tokens=self._out_tokens),
        )

    # --- rounds -----------------------------------------------------------------

    async def _round_one(self) -> None:
        await self.emit(AnalysisEvent("round", {"round_no": 1, "kind": ROUND_ONE_KIND}))
        user = round_one_user(
            self.text, self.question, self.config.language, self.participants or None
        )
        semaphore = asyncio.Semaphore(self.config.concurrency)

        async def one(analyst: AnalystSpec) -> tuple[AnalystSpec, str]:
            async with semaphore:
                return analyst, await self._analyst_turn(analyst, user, 1)

        tasks = [asyncio.create_task(one(a)) for a in self.analysts]
        try:
            for future in asyncio.as_completed(tasks):
                analyst, text = await future
                await self._record_turn(1, analyst, text)
        finally:
            for task in tasks:
                task.cancel()
            # Retrieve results of cancelled/failed siblings so nothing is logged at GC time.
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _cross_round(self, round_no: int) -> None:
        await self.emit(AnalysisEvent("round", {"round_no": round_no, "kind": CROSS_ROUND_KIND}))
        for analyst in self.analysts:
            user = cross_round_user(
                self.text,
                self.question,
                self.transcript.render(),
                round_no,
                self.config.language,
                self.participants or None,
            )
            text = await self._analyst_turn(analyst, user, round_no)
            await self._record_turn(round_no, analyst, text)

    async def _analyst_turn(self, analyst: AnalystSpec, user: str, round_no: int) -> str:
        await self.emit(
            AnalysisEvent(
                "typing", {"analyst": analyst.name, "emoji": analyst.emoji, "role": "analyst"}
            )
        )
        request = ChatRequest(
            model=self.config.analyst_model,
            system=analyst.system_prompt,
            user=user,
            max_tokens=self.config.turn_max_tokens,
            effort=self.config.analyst_effort,
        )
        started = time.monotonic()
        completion = await self.analyst_provider.complete(request)
        self._account(
            completion.usage,
            "analyst",
            round_no,
            started,
            self.analyst_provider.name,
            self.config.analyst_model,
        )
        return completion.text.strip()

    async def _record_turn(self, round_no: int, analyst: AnalystSpec, text: str) -> None:
        self.transcript.add(round_no, analyst.name, text)
        if self.persist_turn:
            await self.persist_turn(round_no, analyst, text)
        await self.emit(
            AnalysisEvent(
                "turn",
                {
                    "round_no": round_no,
                    "analyst": analyst.name,
                    "emoji": analyst.emoji,
                    "text": text,
                },
            )
        )

    # --- judge ------------------------------------------------------------------

    async def _judge(self) -> Verdict:
        await self.emit(
            AnalysisEvent("typing", {"analyst": JUDGE_NAME, "emoji": JUDGE_EMOJI, "role": "judge"})
        )
        # Providers without native structured output need the schema in the prompt;
        # Anthropic gets it through ``output_format`` instead.
        schema_text = (
            None if self.judge_provider.name == "anthropic" else verdict_json_schema_text()
        )
        request = ChatRequest(
            model=self.config.judge_model,
            system=judge_system_prompt(self.config.language),
            user=judge_user(
                self.text,
                self.question,
                self.transcript.render(),
                self.participants,
                self.config.language,
                json_schema=schema_text,
            ),
            max_tokens=self.config.judge_max_tokens,
            effort=self.config.judge_effort,
        )
        last_error: InvalidVerdict | None = None
        for attempt in range(1, JUDGE_ATTEMPTS + 1):
            started = time.monotonic()
            try:
                result = await self.judge_provider.complete_structured(request, Verdict)
                self._account(
                    result.usage,
                    "judge",
                    0,
                    started,
                    self.judge_provider.name,
                    self.config.judge_model,
                )
                result.value.validate_for(self.participants)
            except InvalidVerdict as exc:
                last_error = exc
                logger.warning("judge attempt %s invalid: %s", attempt, exc)
                continue
            verdict = result.value
            if self.persist_verdict:
                await self.persist_verdict(verdict)
            await self.emit(AnalysisEvent("verdict", verdict.model_dump()))
            return verdict
        raise InvalidVerdict(
            f"Судья не дал валидный вердикт после {JUDGE_ATTEMPTS} попыток: {last_error}"
        )

    # --- stats ------------------------------------------------------------------

    def _account(
        self, usage: Usage, role: str, round_no: int, started: float, provider: str, model: str
    ) -> None:
        self._calls += 1
        self._in_tokens += usage.input_tokens
        self._out_tokens += usage.output_tokens
        logger.info(
            "llm.call provider=%s model=%s role=%s analysis=%s round=%s ms=%d in=%d out=%d",
            provider,
            model,
            role,
            self.analysis_id,
            round_no,
            int((time.monotonic() - started) * 1000),
            usage.input_tokens,
            usage.output_tokens,
        )
