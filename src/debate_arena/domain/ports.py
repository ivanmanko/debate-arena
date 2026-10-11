"""Ports: what the domain needs from the outside world, as Protocols (ADR-001, ADR-004)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

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
