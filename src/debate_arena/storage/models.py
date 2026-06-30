"""SQLAlchemy models: Persona, Debate, Round, Message, Verdict (+ scores).

The persona *character* lives in `Persona.system_principles` in the DB, not in
code — so you can retune a voice without redeploying.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    emoji: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(128))
    # The "character": manifesto-bound principles that become the agent backstory.
    system_principles: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128))
    temperature: Mapped[float] = mapped_column(Float, default=0.8)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Leaderboard accumulators.
    wins: Mapped[int] = mapped_column(Integer, default=0)
    debates: Mapped[int] = mapped_column(Integer, default=0)
    total_score: Mapped[int] = mapped_column(Integer, default=0)


class Debate(Base):
    __tablename__ = "debates"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer)
    topic: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|finished|failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    messages: Mapped[list[Message]] = relationship(
        back_populates="debate", cascade="all, delete-orphan"
    )
    verdict: Mapped[Verdict | None] = relationship(
        back_populates="debate", cascade="all, delete-orphan", uselist=False
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    debate_id: Mapped[int] = mapped_column(ForeignKey("debates.id"))
    round_no: Mapped[int] = mapped_column(Integer)
    persona_name: Mapped[str] = mapped_column(String(64))
    persona_emoji: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    debate: Mapped[Debate] = relationship(back_populates="messages")


class Verdict(Base):
    __tablename__ = "verdicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    debate_id: Mapped[int] = mapped_column(ForeignKey("debates.id"), unique=True)
    winner: Mapped[str] = mapped_column(String(64))
    punchline: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    debate: Mapped[Debate] = relationship(back_populates="verdict")
    scores: Mapped[list[VerdictScore]] = relationship(
        back_populates="verdict", cascade="all, delete-orphan"
    )


class VerdictScore(Base):
    __tablename__ = "verdict_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    verdict_id: Mapped[int] = mapped_column(ForeignKey("verdicts.id"))
    persona: Mapped[str] = mapped_column(String(64))
    logic: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[int] = mapped_column(Integer)
    rhetoric: Mapped[int] = mapped_column(Integer)
    responsiveness: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text)

    verdict: Mapped[Verdict] = relationship(back_populates="scores")

    @property
    def total(self) -> int:
        return self.logic + self.evidence + self.rhetoric + self.responsiveness
