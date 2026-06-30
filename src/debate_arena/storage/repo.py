"""Persistence helpers: load personas, record debates/messages/verdicts, leaderboard."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..debate.schemas import PersonaSpec
from ..debate.schemas import Verdict as VerdictSchema
from .db import SessionLocal
from .models import Debate, Message, Persona, Verdict, VerdictScore


async def get_active_personas(limit: int) -> list[PersonaSpec]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(Persona).where(Persona.active.is_(True)).order_by(Persona.id).limit(limit)
        )
        return [
            PersonaSpec(
                name=p.name,
                emoji=p.emoji,
                role=p.role,
                system_principles=p.system_principles,
                model=p.model,
                temperature=p.temperature,
            )
            for p in rows
        ]


async def create_debate(chat_id: int, topic: str) -> int:
    async with SessionLocal() as session:
        debate = Debate(chat_id=chat_id, topic=topic, status="running")
        session.add(debate)
        await session.commit()
        return debate.id


async def add_message(debate_id: int, round_no: int, persona: PersonaSpec, text: str) -> None:
    async with SessionLocal() as session:
        session.add(
            Message(
                debate_id=debate_id,
                round_no=round_no,
                persona_name=persona.name,
                persona_emoji=persona.emoji,
                text=text,
            )
        )
        await session.commit()


async def set_debate_status(debate_id: int, status: str) -> None:
    async with SessionLocal() as session:
        debate = await session.get(Debate, debate_id)
        if debate:
            debate.status = status
            await session.commit()


async def save_verdict(debate_id: int, verdict: VerdictSchema) -> None:
    """Persist the verdict and roll the leaderboard forward in one transaction."""
    async with SessionLocal() as session:
        record = Verdict(debate_id=debate_id, winner=verdict.winner, punchline=verdict.punchline)
        for s in verdict.scores:
            record.scores.append(
                VerdictScore(
                    persona=s.persona,
                    logic=s.logic,
                    evidence=s.evidence,
                    rhetoric=s.rhetoric,
                    responsiveness=s.responsiveness,
                    note=s.note,
                )
            )
        session.add(record)

        # Leaderboard accumulation.
        for s in verdict.scores:
            persona = await session.scalar(select(Persona).where(Persona.name == s.persona))
            if persona is None:
                continue
            persona.debates += 1
            persona.total_score += s.total
            if s.persona == verdict.winner:
                persona.wins += 1

        debate = await session.get(Debate, debate_id)
        if debate:
            debate.status = "finished"
        await session.commit()


async def get_leaderboard() -> list[Persona]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(Persona).order_by(Persona.wins.desc(), Persona.total_score.desc())
        )
        return list(rows)


async def get_debate_with_verdict(debate_id: int) -> Debate | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(Debate)
            .where(Debate.id == debate_id)
            .options(selectinload(Debate.verdict).selectinload(Verdict.scores))
        )
