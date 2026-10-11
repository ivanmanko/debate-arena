"""Ports: what the domain needs from the outside world, as Protocols (ADR-001, ADR-004)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .records import AnalysisRecord, AnalysisSummary, ChatMessageRecord
from .schemas import Verdict

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class ChatRequest:
    model: str
    system: str
    user: str
    max_tokens: int
    effort: str = "medium"  # Anthropic effort level; other providers ignore it


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage


@dataclass(frozen=True)
class StructuredCompletion[ModelT: BaseModel]:
    value: ModelT
    usage: Usage
    raw: str


class ChatProvider(Protocol):
    """One LLM vendor. ``complete`` returns prose; ``complete_structured`` a validated model."""

    name: str

    async def complete(self, request: ChatRequest) -> Completion: ...

    async def complete_structured(
        self, request: ChatRequest, schema: type[ModelT]
    ) -> StructuredCompletion[ModelT]: ...


class AnalysisRepository(Protocol):
    """Storage port for analyses (SPEC §2). Implemented by infrastructure.db and test fakes."""

    async def create(
        self,
        *,
        source_text: str,
        question: str | None,
        mode: str,
        participants: list[str],
        rounds: int,
        model_analyst: str,
        model_judge: str,
        title: str,
    ) -> int: ...

    async def add_turn(
        self, analysis_id: int, round_no: int, analyst: str, emoji: str, text: str
    ) -> None: ...

    async def save_verdict(self, analysis_id: int, verdict: Verdict) -> None: ...

    async def add_chat_exchange(
        self, analysis_id: int, question: str, reply: str
    ) -> tuple[ChatMessageRecord, ChatMessageRecord]:
        """Store the user's question and the judge's reply in one transaction."""
        ...

    async def finish(
        self, analysis_id: int, status: str, error: str | None, duration_ms: int
    ) -> None: ...

    async def get(self, analysis_id: int) -> AnalysisRecord | None: ...

    async def list(self, limit: int) -> list[AnalysisSummary]: ...

    async def delete(self, analysis_id: int) -> bool: ...

    async def mark_interrupted(self) -> int: ...
