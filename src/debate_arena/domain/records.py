"""Persistence-shaped views of an analysis, independent of the storage engine (SPEC §2)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .schemas import AnalysisMode, Verdict

AnalysisStatus = Literal["running", "finished", "failed"]
ChatRole = Literal["user", "judge"]


class TurnRecord(BaseModel):
    round_no: int
    analyst: str
    emoji: str
    text: str
    created_at: datetime


class ChatMessageRecord(BaseModel):
    """One message of the verdict discussion with the judge (SPEC §2, §4.6)."""

    role: ChatRole
    text: str
    created_at: datetime


class ShareRecord(BaseModel):
    """A published snapshot of an analysis (SPEC §2, §4.7). ``key`` names the stored object."""

    key: str
    url: str
    created_at: datetime


class AnalysisRecord(BaseModel):
    id: int
    title: str
    source_text: str
    question: str | None
    mode: AnalysisMode  # ``auto`` until the verdict resolves it
    participants: list[str]  # named by the verdict (ADR-008)
    rounds: int
    model_analyst: str
    model_judge: str
    status: AnalysisStatus
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    turns: list[TurnRecord]
    verdict: Verdict | None
    chat: list[ChatMessageRecord] = []
    share: ShareRecord | None = None


def resolved_mode(requested: AnalysisMode, verdict: Verdict) -> AnalysisMode:
    """``auto`` becomes ``dialogue`` if the judge named speakers, else ``text`` (ADR-008)."""
    if requested != "auto":
        return requested
    return "dialogue" if verdict.participants else "text"


class AnalysisSummary(BaseModel):
    id: int
    title: str
    status: AnalysisStatus
    created_at: datetime
    mode: AnalysisMode
    rounds: int
    model_analyst: str
    rationality_score: int | None
    duration_ms: int | None

    @classmethod
    def from_record(cls, record: AnalysisRecord) -> "AnalysisSummary":
        return cls(
            id=record.id,
            title=record.title,
            status=record.status,
            created_at=record.created_at,
            mode=record.mode,
            rounds=record.rounds,
            model_analyst=record.model_analyst,
            rationality_score=record.verdict.rationality_score if record.verdict else None,
            duration_ms=record.duration_ms,
        )
