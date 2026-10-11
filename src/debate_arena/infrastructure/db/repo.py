"""SQLAlchemy implementation of the ``AnalysisRepository`` port."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ...domain.records import (
    AnalysisRecord,
    AnalysisSummary,
    ChatMessageRecord,
    ShareRecord,
    TurnRecord,
)
from ...domain.schemas import Verdict
from .models import Analysis, JudgeMessage, ShareRow, Turn, VerdictRow


class SqlAlchemyAnalysisRepository:
    """``AnalysisRepository`` over an async SQLAlchemy session factory."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        source_text: str,
        question: str | None,
        mode: str,
        participants: list[str],
        rounds: int,
        model_analyst: str,
        model_judge: str,
        title: str,
    ) -> int:
        async with self._sessions() as session:
            row = Analysis(
                title=title,
                source_text=source_text,
                question=question,
                mode=mode,
                participants=json.dumps(participants, ensure_ascii=False),
                rounds=rounds,
                model_analyst=model_analyst,
                model_judge=model_judge,
                status="running",
            )
            session.add(row)
            await session.commit()
            return row.id

    async def add_turn(
        self, analysis_id: int, round_no: int, analyst: str, emoji: str, text: str
    ) -> None:
        async with self._sessions() as session:
            session.add(
                Turn(
                    analysis_id=analysis_id,
                    round_no=round_no,
                    analyst=analyst,
                    emoji=emoji,
                    text=text,
                )
            )
            await session.commit()

    async def save_verdict(self, analysis_id: int, verdict: Verdict) -> None:
        async with self._sessions() as session:
            session.add(
                VerdictRow(
                    analysis_id=analysis_id,
                    payload=verdict.model_dump_json(),
                    rationality_score=verdict.rationality_score,
                )
            )
            await session.commit()

    async def add_chat_exchange(
        self, analysis_id: int, question: str, reply: str
    ) -> tuple[ChatMessageRecord, ChatMessageRecord]:
        async with self._sessions() as session:
            rows = (
                JudgeMessage(analysis_id=analysis_id, role="user", text=question),
                JudgeMessage(analysis_id=analysis_id, role="judge", text=reply),
            )
            session.add_all(rows)
            await session.commit()
            return _to_chat_message(rows[0]), _to_chat_message(rows[1])

    async def save_share(self, analysis_id: int, key: str, url: str) -> ShareRecord:
        async with self._sessions() as session:
            row = ShareRow(analysis_id=analysis_id, key=key, url=url)
            session.add(row)
            await session.commit()
            return _to_share(row)

    async def delete_share(self, analysis_id: int) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                delete(ShareRow).where(ShareRow.analysis_id == analysis_id)
            )
            await session.commit()
            return bool(result.rowcount)

    async def finish(
        self, analysis_id: int, status: str, error: str | None, duration_ms: int
    ) -> None:
        async with self._sessions() as session:
            await session.execute(
                update(Analysis)
                .where(Analysis.id == analysis_id)
                .values(
                    status=status,
                    error=error,
                    duration_ms=duration_ms,
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def get(self, analysis_id: int) -> AnalysisRecord | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(Analysis)
                .where(Analysis.id == analysis_id)
                .options(
                    selectinload(Analysis.turns),
                    selectinload(Analysis.verdict),
                    selectinload(Analysis.chat),
                    selectinload(Analysis.share),
                )
            )
            return _to_record(row) if row else None

    async def list(self, limit: int) -> list[AnalysisSummary]:
        async with self._sessions() as session:
            result = await session.execute(
                select(Analysis, VerdictRow.rationality_score)
                .outerjoin(VerdictRow, VerdictRow.analysis_id == Analysis.id)
                .order_by(Analysis.id.desc())
                .limit(limit)
            )
            return [
                AnalysisSummary(
                    id=row.id,
                    title=row.title,
                    status=row.status,
                    created_at=_utc(row.created_at),
                    mode=row.mode,
                    rounds=row.rounds,
                    model_analyst=row.model_analyst,
                    rationality_score=score,
                    duration_ms=row.duration_ms,
                )
                for row, score in result.all()
            ]

    async def delete(self, analysis_id: int) -> bool:
        async with self._sessions() as session:
            row = await session.get(Analysis, analysis_id)
            if row is None:
                return False
            await session.delete(row)  # cascades to turns, verdict, chat and share via ORM
            await session.commit()
            return True

    async def mark_interrupted(self) -> int:
        async with self._sessions() as session:
            result = await session.execute(
                update(Analysis)
                .where(Analysis.status == "running")
                .values(status="failed", error="interrupted", finished_at=datetime.now(UTC))
            )
            await session.commit()
            return int(result.rowcount or 0)


def _utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone-aware columns; they are UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _to_record(row: Analysis) -> AnalysisRecord:
    return AnalysisRecord(
        id=row.id,
        title=row.title,
        source_text=row.source_text,
        question=row.question,
        mode=row.mode,
        participants=list(json.loads(row.participants or "[]")),
        rounds=row.rounds,
        model_analyst=row.model_analyst,
        model_judge=row.model_judge,
        status=row.status,
        error=row.error,
        created_at=_utc(row.created_at),
        finished_at=_utc(row.finished_at),
        duration_ms=row.duration_ms,
        turns=[
            TurnRecord(
                round_no=t.round_no,
                analyst=t.analyst,
                emoji=t.emoji,
                text=t.text,
                created_at=_utc(t.created_at),
            )
            for t in row.turns
        ],
        verdict=Verdict.model_validate_json(row.verdict.payload) if row.verdict else None,
        chat=[_to_chat_message(m) for m in row.chat],
        share=_to_share(row.share) if row.share else None,
    )


def _to_chat_message(row: JudgeMessage) -> ChatMessageRecord:
    return ChatMessageRecord(role=row.role, text=row.text, created_at=_utc(row.created_at))


def _to_share(row: ShareRow) -> ShareRecord:
    return ShareRecord(key=row.key, url=row.url, created_at=_utc(row.created_at))
