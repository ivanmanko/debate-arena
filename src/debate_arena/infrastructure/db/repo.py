"""SQLAlchemy implementation of the ``AnalysisRepository`` port."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ...domain.records import AnalysisRecord, AnalysisSummary, TurnRecord
from ...domain.schemas import Verdict
from .models import Analysis, Turn, VerdictRow


class SqlAlchemyAnalysisRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        source_text: str,
        question: str | None,
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
                .options(selectinload(Analysis.turns), selectinload(Analysis.verdict))
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
                    created_at=row.created_at,
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
            await session.delete(row)  # cascades to turns and verdict via ORM
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


def _to_record(row: Analysis) -> AnalysisRecord:
    return AnalysisRecord(
        id=row.id,
        title=row.title,
        source_text=row.source_text,
        question=row.question,
        rounds=row.rounds,
        model_analyst=row.model_analyst,
        model_judge=row.model_judge,
        status=row.status,
        error=row.error,
        created_at=row.created_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        turns=[
            TurnRecord(
                round_no=t.round_no,
                analyst=t.analyst,
                emoji=t.emoji,
                text=t.text,
                created_at=t.created_at,
            )
            for t in row.turns
        ],
        verdict=Verdict.model_validate_json(row.verdict.payload) if row.verdict else None,
    )
