"""Persistence-shaped views of an analysis, independent of the storage engine (SPEC §2)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .dialogue import InputMode
from .schemas import Verdict

AnalysisStatus = Literal["running", "finished", "failed"]


class TurnRecord(BaseModel):
    round_no: int
    analyst: str
    emoji: str
    text: str
    created_at: datetime


class AnalysisRecord(BaseModel):
    id: int
    title: str
    source_text: str
    question: str | None
    mode: InputMode
    participants: list[str]
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


class AnalysisSummary(BaseModel):
    id: int
    title: str
    status: AnalysisStatus
    created_at: datetime
    mode: InputMode
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
