"""SQLAlchemy models: Analysis, Turn, VerdictRow. Verdict payload is stored as JSON text
exactly as the ``Verdict`` Pydantic model serialises it (SPEC §2)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)  # length bounded by TITLE_CHARS (SPEC §7)
    source_text: Mapped[str] = mapped_column(Text)
    question: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rounds: Mapped[int] = mapped_column(Integer)
    model_analyst: Mapped[str] = mapped_column(String(128))
    model_judge: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    turns: Mapped[list[Turn]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", order_by="Turn.id"
    )
    verdict: Mapped[VerdictRow | None] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", uselist=False
    )


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    round_no: Mapped[int] = mapped_column(Integer)
    analyst: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    analysis: Mapped[Analysis] = relationship(back_populates="turns")


class VerdictRow(Base):
    # Not "verdicts": a v0.1 database file may still hold that table with another shape.
    __tablename__ = "analysis_verdicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), unique=True
    )
    payload: Mapped[str] = mapped_column(Text)
    # Denormalised for the history list (SPEC §3.5) — avoids parsing JSON per row.
    rationality_score: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    analysis: Mapped[Analysis] = relationship(back_populates="verdict")
