"""JudgeChatService — discussing a finished verdict with the judge (SPEC §4.6, ADR-005).

One synchronous ``complete`` call per user message with the whole analysis as context;
both messages are stored only after the judge has answered. The stored verdict is never
rewritten: a revision lives in the conversation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from ..config import Settings
from ..domain.dialogue import anonymize_mentions
from ..domain.exceptions import (
    AnalysisNotFound,
    ChatUnavailable,
    ProviderError,
    RationalCheckError,
)
from ..domain.ports import AnalysisRepository, ChatRequest
from ..domain.prompts import judge_chat_context, judge_chat_system_prompt, judge_chat_user
from ..domain.records import AnalysisRecord, ChatMessageRecord
from ..domain.schemas import Verdict, parse_model_ref
from ..domain.transcript import Transcript
from .service import ProviderFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatExchange:
    question: ChatMessageRecord
    reply: ChatMessageRecord


class JudgeChatService:
    """Use case behind ``POST /api/analyses/{id}/chat``."""

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
        self._busy: set[int] = set()  # analyses whose judge is answering right now

    async def ask(
        self, analysis_id: int, text: str, legend: dict[str, str] | None = None
    ) -> ChatExchange:
        """Ask the judge; ``ValueError`` on bad input, ``ChatUnavailable`` when not allowed."""
        s = self._settings
        text = text.strip()
        if not text:
            raise ValueError("message must not be empty")
        if len(text) > s.judge_chat_max_chars:
            raise ValueError(f"message is longer than {s.judge_chat_max_chars} characters")
        # Taken before reading the record, so the history and the limit check are never stale.
        if analysis_id in self._busy:
            raise ChatUnavailable("the judge is still answering the previous message")
        self._busy.add(analysis_id)
        try:
            record = await self._repo.get(analysis_id)
            if record is None:
                raise AnalysisNotFound(str(analysis_id))
            if record.status != "finished" or record.verdict is None:
                raise ChatUnavailable("the judge can be asked only after a verdict")
            if sum(m.role == "user" for m in record.chat) >= s.judge_chat_max_messages:
                raise ChatUnavailable(f"message limit reached ({s.judge_chat_max_messages})")
            text = self._anonymize(record, text, legend or {})
            reply = await self._reply(record, record.verdict, text)
            question, answer = await self._repo.add_chat_exchange(analysis_id, text, reply)
        finally:
            self._busy.discard(analysis_id)
        return ChatExchange(question=question, reply=answer)

    def is_busy(self, analysis_id: int) -> bool:
        """True while the judge is answering a message of this analysis (blocks delete)."""
        return analysis_id in self._busy

    @staticmethod
    def _anonymize(record: AnalysisRecord, text: str, legend: dict[str, str]) -> str:
        """Dialogue only, and only labels that really are this analysis's participants."""
        if record.mode != "dialogue":
            return text
        known = {label: name for label, name in legend.items() if label in record.participants}
        return anonymize_mentions(text, known)

    async def _reply(self, record: AnalysisRecord, verdict: Verdict, text: str) -> str:
        s = self._settings
        provider = self._providers(record.model_judge)
        transcript = Transcript()
        for turn in record.turns:
            transcript.add(turn.round_no, turn.analyst, turn.text)
        request = ChatRequest(
            model=parse_model_ref(record.model_judge).model,
            system=judge_chat_system_prompt(s.language),
            context=judge_chat_context(
                record.source_text,
                record.question,
                transcript.render(),
                record.participants,
                verdict.model_dump_json(),
            ),
            user=judge_chat_user([(m.role, m.text) for m in record.chat], text, s.language),
            max_tokens=s.judge_chat_max_tokens,
            effort=s.judge_chat_effort,
            cache_ttl=s.cache_ttl_chat,
        )
        started = time.monotonic()
        try:
            # A dropped client connection does not cancel the call; the timeout frees the lock.
            async with asyncio.timeout(s.judge_chat_timeout_s):
                completion = await provider.complete(request)
        except RationalCheckError:
            raise
        except TimeoutError as exc:
            raise ProviderError(
                f"the judge did not answer within {s.judge_chat_timeout_s:.0f} s"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — any transport failure becomes a 502
            logger.exception("judge chat failed for analysis %s", record.id)
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        logger.info(
            "llm.call provider=%s model=%s role=chat analysis=%s round=0 ms=%d in=%d out=%d "
            "cache_read=%d cache_write=%d",
            provider.name,
            request.model,
            record.id,
            int((time.monotonic() - started) * 1000),
            completion.usage.input_tokens,
            completion.usage.output_tokens,
            completion.usage.cache_read_tokens,
            completion.usage.cache_write_tokens,
        )
        reply = completion.text.strip()
        if not reply:
            raise ProviderError("the judge returned an empty reply")
        return reply
