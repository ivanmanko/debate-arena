"""Pydantic contract shared by every layer: analysts, verdict, model references.

These models are the single source of truth for the judge's structured output
(SPEC §3.3) and for the API DTOs that embed them. No ``from __future__ import
annotations`` here: the Anthropic SDK builds the JSON schema from real annotations.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .exceptions import InvalidVerdict

KNOWN_PROVIDERS = ("anthropic", "deepseek", "mock")

ClaimStatus = Literal["supported", "plausible", "unsupported", "unfalsifiable", "false"]


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}"


def parse_model_ref(value: str) -> ModelRef:
    """``"anthropic/claude-opus-5-5"`` → ``ModelRef``. Raises ValueError when malformed."""
    provider, sep, model = value.partition("/")
    if not sep or not provider or not model:
        raise ValueError(f"model must look like provider/model, got {value!r}")
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; known: {', '.join(KNOWN_PROVIDERS)}")
    return ModelRef(provider=provider, model=model)


class AnalystSpec(BaseModel):
    """One rational analyst: shared manifesto + a methodological lens (ADR-002)."""

    name: str
    emoji: str
    focus: str
    system_prompt: str


class ClaimAssessment(BaseModel):
    claim: str = Field(description="Ключевое утверждение из текста, своими словами")
    status: ClaimStatus = Field(
        description="supported — доказано; plausible — вероятно по Оккаму; "
        "unsupported — без доказательств; unfalsifiable — непроверяемо; false — опровергнуто"
    )
    note: str = Field(description="Одна фраза: почему такой статус")


class Fallacy(BaseModel):
    name: str = Field(description="Название уловки: ad hominem, соломенное чучело, …")
    quote: str = Field(description="Цитата или точный пересказ места в тексте")
    explanation: str = Field(description="В чём именно ошибка")


class AnalystScore(BaseModel):
    analyst: str
    rigor: int = Field(ge=1, le=10, description="Строгость рассуждения")
    evidence: int = Field(ge=1, le=10, description="Работа с фактами и бременем доказывания")
    charity: int = Field(ge=1, le=10, description="Благожелательность и стилменнинг")
    responsiveness: int = Field(ge=1, le=10, description="Отвечал ли коллегам, менял ли позицию")
    note: str = Field(description="Одна ёмкая фраза об этом аналитике")

    @property
    def total(self) -> int:
        return self.rigor + self.evidence + self.charity + self.responsiveness


class Verdict(BaseModel):
    """Judge's structured verdict — SPEC §3.3."""

    core_question: str = Field(description="Нейтральная формулировка предмета, без эмоций")
    conclusion: str = Field(description="Самое рациональное заключение по тексту")
    rationality_score: int = Field(ge=0, le=100, description="Рациональность исходного текста")
    confidence: int = Field(ge=0, le=100, description="Уверенность судьи в заключении")
    established: list[str] = Field(description="Что доказано строго")
    probable: list[str] = Field(description="Рациональная рабочая гипотеза по Оккаму")
    uncertain: list[str] = Field(description="Честные белые пятна")
    claims: list[ClaimAssessment] = Field(description="Ключевые утверждения текста")
    fallacies: list[Fallacy] = Field(description="Найденные уловки; пустой список, если нет")
    steelman: str = Field(description="Сильнейшая версия позиции автора")
    falsification: str = Field(description="Что должно появиться, чтобы вердикт изменился")
    so_what: str = Field(description="Практическое следствие для разумного человека")
    scores: list[AnalystScore] = Field(description="Ровно по одной оценке на аналитика")
    most_rational: str = Field(description="Имя аналитика с самой рациональной линией")

    @field_validator("conclusion", "core_question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    def validate_for(self, analyst_names: list[str]) -> None:
        """Invariants from SPEC §3.3 that the JSON schema alone cannot express."""
        scored = [s.analyst for s in self.scores]
        if sorted(scored) != sorted(analyst_names):
            raise InvalidVerdict(f"scores must cover exactly {analyst_names}, got {scored}")
        if self.most_rational not in analyst_names:
            raise InvalidVerdict(f"most_rational {self.most_rational!r} is not an analyst")

    def score_for(self, analyst: str) -> AnalystScore | None:
        return next((s for s in self.scores if s.analyst == analyst), None)
