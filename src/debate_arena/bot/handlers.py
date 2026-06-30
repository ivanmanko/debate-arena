"""Command handlers: /start, /help, /debate <topic>, /standings."""

from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..runner import is_debate_active, start_debate
from ..storage import repo
from .presenter import render_standings

router = Router(name="debate-arena")

_HELP = (
    "🏛 <b>Debate Arena</b>\n\n"
    "Три философа спорят на твою тему по правилам Манифеста, а судья решает, "
    "кто был убедительнее.\n\n"
    "Команды:\n"
    "• <code>/debate &lt;тема&gt;</code> — запустить дебаты\n"
    "• <code>/standings</code> — лидерборд персон\n"
    "• <code>/help</code> — эта справка\n\n"
    "Пример: <code>/debate Свобода воли — иллюзия?</code>"
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(_HELP, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(_HELP, parse_mode="HTML")


@router.message(Command("standings"))
async def cmd_standings(message: Message) -> None:
    personas = await repo.get_leaderboard()
    await message.answer(render_standings(personas), parse_mode="HTML")


@router.message(Command("debate"))
async def cmd_debate(message: Message, command: CommandObject, bot: Bot) -> None:
    topic = (command.args or "").strip()
    if not topic:
        await message.answer(
            "Укажи тему: <code>/debate &lt;тема&gt;</code>\n"
            "Например: <code>/debate Можно ли доверять интуиции?</code>",
            parse_mode="HTML",
        )
        return

    chat_id = message.chat.id
    if is_debate_active(chat_id):
        await message.answer("⏳ В этом чате уже идут дебаты. Дождись вердикта.")
        return

    started = await start_debate(bot, chat_id, topic)
    if started:
        await message.answer("🎬 Дебаты начинаются…")
