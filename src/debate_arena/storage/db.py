"""Async engine/session factory plus first-run bootstrap (create tables + seed personas)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import settings
from .models import Base, Persona
from .seed import default_personas

_engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create tables if missing and seed the default personas once.

    Bootstraps via create_all for zero-config startup; Alembic migrations can be
    layered on later for schema evolution.
    """
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        existing = await session.scalar(select(Persona).limit(1))
        if existing is None:
            session.add_all(Persona(**row) for row in default_personas())
            await session.commit()


async def dispose_db() -> None:
    await _engine.dispose()
