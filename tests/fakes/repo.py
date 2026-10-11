"""InMemoryRepository — implements the AnalysisRepository port without a database."""

from __future__ import annotations

from datetime import UTC, datetime

from debate_arena.domain.records import (
    AnalysisRecord,
    AnalysisSummary,
    ChatMessageRecord,
    ShareRecord,
    TurnRecord,
)
from debate_arena.domain.schemas import Verdict


class InMemoryRepository:
    def __init__(self) -> None:
        self.rows: dict[int, AnalysisRecord] = {}
        self._next = 1

    async def create(
        self,
        *,
        source_text: str,
        question: str | None,
        rounds: int,
        model_analyst: str,
        model_judge: str,
        title: str,
        mode: str = "text",
        participants: list[str] | None = None,
    ) -> int:
        analysis_id = self._next
        self._next += 1
        self.rows[analysis_id] = AnalysisRecord(
            id=analysis_id,
            title=title,
            source_text=source_text,
            question=question,
            mode=mode,  # type: ignore[arg-type]
            participants=list(participants or []),
            rounds=rounds,
            model_analyst=model_analyst,
            model_judge=model_judge,
            status="running",
            error=None,
            created_at=datetime.now(UTC),
            finished_at=None,
            duration_ms=None,
            turns=[],
            verdict=None,
        )
        return analysis_id

    async def add_turn(
        self, analysis_id: int, round_no: int, analyst: str, emoji: str, text: str
    ) -> None:
        self.rows[analysis_id].turns.append(
            TurnRecord(
                round_no=round_no,
                analyst=analyst,
                emoji=emoji,
                text=text,
                created_at=datetime.now(UTC),
            )
        )

    async def save_verdict(self, analysis_id: int, verdict: Verdict) -> None:
        self.rows[analysis_id].verdict = verdict

    async def add_chat_exchange(
        self, analysis_id: int, question: str, reply: str
    ) -> tuple[ChatMessageRecord, ChatMessageRecord]:
        now = datetime.now(UTC)
        pair = (
            ChatMessageRecord(role="user", text=question, created_at=now),
            ChatMessageRecord(role="judge", text=reply, created_at=now),
        )
        self.rows[analysis_id].chat.extend(pair)
        return pair

    async def save_share(self, analysis_id: int, key: str, url: str) -> ShareRecord:
        share = ShareRecord(key=key, url=url, created_at=datetime.now(UTC))
        self.rows[analysis_id].share = share
        return share

    async def delete_share(self, analysis_id: int) -> bool:
        row = self.rows.get(analysis_id)
        if row is None or row.share is None:
            return False
        row.share = None
        return True

    async def finish(
        self, analysis_id: int, status: str, error: str | None, duration_ms: int
    ) -> None:
        row = self.rows[analysis_id]
        row.status = status
        row.error = error
        row.duration_ms = duration_ms
        row.finished_at = datetime.now(UTC)

    async def get(self, analysis_id: int) -> AnalysisRecord | None:
        return self.rows.get(analysis_id)

    async def list(self, limit: int) -> list[AnalysisSummary]:
        rows = sorted(self.rows.values(), key=lambda r: r.id, reverse=True)[:limit]
        return [AnalysisSummary.from_record(r) for r in rows]

    async def delete(self, analysis_id: int) -> bool:
        return self.rows.pop(analysis_id, None) is not None

    async def mark_interrupted(self) -> int:
        count = 0
        for row in self.rows.values():
            if row.status == "running":
                row.status, row.error = "failed", "interrupted"
                count += 1
        return count
