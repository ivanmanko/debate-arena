"""aiogram entrypoint: build the bot/dispatcher, init DB, start long polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from ..config import settings
from ..storage.db import dispose_db, init_db
from .handlers import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit(
            "BOT_TOKEN is not set. Put it in .env or the environment. See .env.example."
        )

    await init_db()
    if settings.use_mock:
        logger.warning("No provider API keys found (or MOCK_LLM=1) — running with the MOCK engine.")

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    try:
        logger.info("Debate Arena bot is starting (long polling)…")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await dispose_db()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
