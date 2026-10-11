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
    claim: str = Field(description="Ключевое утверждение, своими словами")
    by: str | None = Field(default=None, description="Кто утверждал (имя собеседника в диалоге)")
    status: ClaimStatus = Field(
        description="supported — доказано; plausible — вероятно по Оккаму; "
        "unsupported — без доказательств; unfalsifiable — непроверяемо; false — опровергнуто"
    )
    note: str = Field(description="Одна фраза: почему такой статус")


class Fallacy(BaseModel):
    name: str = Field(description="Название уловки: ad hominem, соломенное чучело, …")
    quote: str = Field(description="Цитата или точный пересказ места в тексте")
    explanation: str = Field(description="В чём именно ошибка")
    by: str | None = Field(default=None, description="Кто допустил (имя собеседника в диалоге)")


class ParticipantAssessment(BaseModel):
    """How close one dialogue participant's picture is to the truth — not who argued better."""

    name: str = Field(description="Имя собеседника ровно как в диалоге")
    truth_proximity: int = Field(ge=0, le=100, description="Насколько позиция близка к истине")
    position: str = Field(description="Позиция собеседника своими словами, в сильнейшей форме")
    right_about: str = Field(description="Что у него верно (обязательно, даже если в целом неправ)")
    wrong_about: str = Field(description="Главная ошибка; пустая строка, если её нет")
    note: str = Field(description="Одна фраза-итог по этому собеседнику")


class Verdict(BaseModel):
    """Judge's structured verdict — SPEC §3.3. Seeks the truth, never a winner."""

    core_question: str = Field(description="Нейтральная формулировка предмета, без эмоций")
    conclusion: str = Field(
        description="То, что ближе всего к истине по итогам разбора: сам ответ, 2–4 предложения"
    )
    narrative: str = Field(
        default="",
        description="Развёрнутое обоснование вердикта, 400–800 слов в 4–7 абзацах, разделённых "
        "пустой строкой: предмет и де-фрейминг; что установлено фактами и почему; граница между "
        "доказанным и интерпретацией; какие доводы аналитиков оказались решающими и какие отпали; "
        "в чём остаётся неопределённость; почему именно это ближе всего к истине (в диалоге — по "
        "каждому собеседнику: что верно, где ошибка)",
    )
    rationality_score: int = Field(ge=0, le=100, description="Рациональность исходного материала")
    confidence: int = Field(ge=0, le=100, description="Уверенность судьи в заключении")
    established: list[str] = Field(description="Что доказано строго")
    probable: list[str] = Field(description="Рациональная рабочая гипотеза по Оккаму")
    uncertain: list[str] = Field(description="Честные белые пятна")
    claims: list[ClaimAssessment] = Field(description="Ключевые утверждения текста")
    fallacies: list[Fallacy] = Field(description="Найденные уловки; пустой список, если нет")
    steelman: str = Field(description="Сильнейшая версия позиции автора")
    falsification: str = Field(description="Что должно появиться, чтобы вердикт изменился")
    so_what: str = Field(description="Практическое следствие для разумного человека")
    consensus: list[str] = Field(description="На чём аналитики сошлись после перекрёстной проверки")
    open_questions: list[str] = Field(
        description="Где аналитики не сошлись или что осталось открытым"
    )
    participants: list[ParticipantAssessment] = Field(
        description="Диалог: по одной записи на каждого собеседника; текст: пустой список"
    )
    closest_to_truth: str | None = Field(
        description="Диалог: имя собеседника, чья картина ближе всего к истине; текст: null"
    )

    @field_validator("conclusion", "core_question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    def validate_for(self, participant_names: list[str]) -> None:
        """Invariants from SPEC §3.3 that the JSON schema alone cannot express.

        ``participant_names`` is empty in text mode and lists every speaker in dialogue mode.
        """
        assessed = [p.name for p in self.participants]
        if sorted(assessed) != sorted(participant_names):
            raise InvalidVerdict(
                f"participants must cover exactly {participant_names}, got {assessed}"
            )
        if participant_names:
            if self.closest_to_truth not in participant_names:
                raise InvalidVerdict(
                    f"closest_to_truth {self.closest_to_truth!r} is not a participant"
                )
        elif self.closest_to_truth is not None:
            raise InvalidVerdict("closest_to_truth must be null in text mode")

    def participant(self, name: str) -> ParticipantAssessment | None:
        return next((p for p in self.participants if p.name == name), None)
