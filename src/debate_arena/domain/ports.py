"""Ports: what the domain needs from the outside world, as Protocols (ADR-001, ADR-004)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from .records import AnalysisRecord, AnalysisSummary, ChatMessageRecord, ShareRecord
from .schemas import Verdict

ModelT = TypeVar("ModelT", bound=BaseModel)


# How long a cached prompt prefix lives (SPEC §4.8, §7).
CacheTTL = Literal["5m", "1h"]


@dataclass(frozen=True)
class ChatRequest:
    model: str
    system: str
    user: str
    max_tokens: int
    effort: str = "medium"  # Anthropic effort level; other providers ignore it
    # Stable material sent before ``user`` — byte-identical across the calls that share it.
    context: str = ""
    # Cache ``system`` + ``context`` for this long; None — no cache marker (SPEC §4.8).
    cache_ttl: CacheTTL | None = None

    def joined_user(self) -> str:
        """``context`` and ``user`` as one message, for providers without cache markers."""
        return f"{self.context}\n\n{self.user}" if self.context else self.user


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0  # the whole prompt, cached part included
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


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

    async def prewarm(self, request: ChatRequest) -> Usage:
        """Write ``system`` + ``context`` to the cache before parallel calls; may do nothing."""
        ...


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

    async def save_share(self, analysis_id: int, key: str, url: str) -> ShareRecord: ...

    async def delete_share(self, analysis_id: int) -> bool:
        """False when the analysis is missing or not published."""
        ...

    async def get(self, analysis_id: int) -> AnalysisRecord | None: ...

    async def list(self, limit: int) -> list[AnalysisSummary]: ...

    async def delete(self, analysis_id: int) -> bool: ...

    async def mark_interrupted(self) -> int: ...


class SnapshotPublisher(Protocol):
    """Public storage for analysis snapshots (ADR-006). Errors surface as ``PublishError``."""

    backend: Literal["gcs", "local"]  # reported in GET /api/config

    async def publish(self, key: str, html: str) -> str:
        """Store the page under ``key`` and return its public URL."""
        ...

    async def unpublish(self, key: str) -> None:
        """Remove the page; a missing object is not an error."""
        ...
