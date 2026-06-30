"""Pydantic schemas shared across the debate layer.

`Verdict` is the strict shape the judge agent must emit (CrewAI `output_pydantic`).

NOTE: deliberately no `from __future__ import annotations` here — it would turn the
field annotations into strings, and CrewAI's schema describer chokes on that when
building the `output_pydantic` prompt (`'str' object has no attribute '__name__'`).
"""

from pydantic import BaseModel, Field


class PersonaScore(BaseModel):
    persona: str
    logic: int = Field(ge=1, le=10)
    evidence: int = Field(ge=1, le=10)
    rhetoric: int = Field(ge=1, le=10)
    responsiveness: int = Field(ge=1, le=10)
    note: str  # one sharp line about this persona

    @property
    def total(self) -> int:
        return self.logic + self.evidence + self.rhetoric + self.responsiveness


class Verdict(BaseModel):
    scores: list[PersonaScore]
    winner: str
    punchline: str  # one snarky closing line


class PersonaSpec(BaseModel):
    """Lightweight, ORM-free view of a persona used by the turn engine."""

    name: str
    emoji: str
    role: str
    system_principles: str
    model: str
    temperature: float = 0.8
