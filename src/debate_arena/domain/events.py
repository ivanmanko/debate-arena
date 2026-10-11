"""Events the flow/service emit while an analysis runs (SPEC §3.4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal["started", "round", "typing", "turn", "verdict", "error", "done"]


@dataclass(frozen=True)
class AnalysisEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[AnalysisEvent], Awaitable[None]]
