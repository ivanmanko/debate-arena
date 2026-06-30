"""Run a debate as a background asyncio task with a one-debate-per-chat lock.

The aiogram handler must return instantly, so this kicks the DebateFlow off with
`asyncio.create_task` and never awaits it inline (a blocking CrewAI kickoff would
freeze the bot). A per-chat set prevents two debates colliding in the same chat.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from .bot.presenter import TelegramPresenter
from .config import settings
from .debate.engine import get_engine
from .debate.flow import DebateFlow
from .debate.schemas import PersonaSpec, Verdict
from .storage import repo

logger = logging.getLogger(__name__)

_active_chats: set[int] = set()


def is_debate_active(chat_id: int) -> bool:
    return chat_id in _active_chats


async def start_debate(bot: Bot, chat_id: int, topic: str) -> bool:
    """Launch a debate in the background. Returns False if one is already running."""
    if chat_id in _active_chats:
        return False

    personas = await repo.get_active_personas(settings.max_personas)
    if len(personas) < 2:
        await bot.send_message(
            chat_id, "🤷 Недостаточно активных персон для дебатов (нужно минимум 2)."
        )
        return False

    _active_chats.add(chat_id)
    asyncio.create_task(_run(bot, chat_id, topic, personas))
    return True


async def _run(bot: Bot, chat_id: int, topic: str, personas: list[PersonaSpec]) -> None:
    presenter = TelegramPresenter(bot, chat_id)
    debate_id = await repo.create_debate(chat_id, topic)

    async def persist_turn(round_no: int, persona: PersonaSpec, text: str) -> None:
        await repo.add_message(debate_id, round_no, persona, text)

    async def persist_verdict(verdict: Verdict) -> None:
        await repo.save_verdict(debate_id, verdict)

    flow = DebateFlow(
        topic=topic,
        personas=personas,
        engine=get_engine(),
        stream=presenter,
        persist_turn=persist_turn,
        persist_verdict=persist_verdict,
    )
    try:
        await flow.run()
    except Exception as exc:  # noqa: BLE001 — surface to chat, keep the bot alive
        logger.exception("Debate %s failed", debate_id)
        await repo.set_debate_status(debate_id, "failed")
        await presenter.on_error(exc)
    finally:
        _active_chats.discard(chat_id)
