"""aiogram entrypoint: same service wiring as the web app, long polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from ...application.service import AnalysisService
from ...config import get_settings
from ...infrastructure.db.repo import SqlAlchemyAnalysisRepository
from ...infrastructure.db.session import Database
from ...infrastructure.llm.factory import ProviderRegistry
from .handlers import router

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise SystemExit(
            "BOT_TOKEN is not set. Put it in .env or the environment. See .env.example."
        )

    database = Database(settings.database_url)
    await database.init()
    registry = ProviderRegistry(settings)
    service = AnalysisService(
        repo=SqlAlchemyAnalysisRepository(database.sessions),
        provider_factory=registry.get,
        settings=settings,
    )
    await service.recover_interrupted()
    if settings.use_mock:
        logger.warning("No provider key for the selected models (or MOCK_LLM=1) — mock engine")

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(service=service, settings=settings)
    dispatcher.include_router(router)
    try:
        logger.info("Rational Check bot is starting (long polling)…")
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await service.shutdown()
        await registry.aclose()
        await database.dispose()


def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())


if __name__ == "__main__":
    run()
