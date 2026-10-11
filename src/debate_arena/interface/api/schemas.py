"""API DTOs (SPEC §3). Embeds domain models rather than redefining their fields."""

from datetime import datetime

from pydantic import BaseModel, Field

from ...config import ModelOption
from ...domain.dialogue import InputMode, RequestedMode
from ...domain.records import AnalysisSummary, TurnRecord
from ...domain.schemas import Verdict


class AnalysisRequest(BaseModel):
    text: str = Field(min_length=1)
    question: str | None = None  # length validated by the service against settings
    rounds: int | None = Field(default=None, ge=1)
    model: str | None = None
    mode: RequestedMode = "auto"


class AnalysisCreated(BaseModel):
    id: int
    legend: dict[str, str] = Field(
        default_factory=dict, description="placeholder → real name; returned once, never stored"
    )


class AnalystOut(BaseModel):
    name: str
    emoji: str
    focus: str


class AnalysisDetail(BaseModel):
    id: int
    title: str
    source_text: str
    question: str | None
    mode: InputMode
    participants: list[str]
    rounds: int
    model_analyst: str
    model_judge: str
    status: str
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    analysts: list[AnalystOut]
    turns: list[TurnRecord]
    verdict: Verdict | None


AnalysisSummaryOut = AnalysisSummary


class ConfigOut(BaseModel):
    models: list[ModelOption]
    default_model: str
    default_rounds: int
    max_rounds: int
    max_text_chars: int
    max_question_chars: int
    mock: bool
    analysts: list[AnalystOut]


class HealthOut(BaseModel):
    status: str
    version: str
    mock: bool
