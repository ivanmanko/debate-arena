"""Commands: /start, /help, /check <text> (or reply to a message with /check)."""

from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ...application.service import AnalysisService
from ...config import Settings
from ...domain.exceptions import ProviderNotConfigured
from ...domain.schemas import Verdict
from . import presenter

logger = logging.getLogger(__name__)
router = Router(name="rational-check")

_active_chats: set[int] = set()

_HELP = (
    "🧭 <b>Проверка здравого смысла</b>\n\n"
    "Пришлите текст — три рациональных аналитика разберут его по Манифесту здравого "
    "смысла, а судья вынесет вердикт: что доказано, что вероятно, что непроверяемо.\n\n"
    "Команды:\n"
    "• <code>/check &lt;текст&gt;</code> — разобрать текст\n"
    "• ответьте на сообщение командой <code>/check</code> — разобрать его\n"
    "• <code>/help</code> — эта справка"
)


@router.message(Command("start"))
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(_HELP, parse_mode="HTML")


@router.message(Command("check"))
async def cmd_check(
    message: Message, command: CommandObject, bot: Bot, service: AnalysisService, settings: Settings
) -> None:
    text = (command.args or "").strip()
    if not text and message.reply_to_message:
        text = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
    if not text:
        await message.answer(
            "Пришлите текст: <code>/check &lt;текст&gt;</code> или ответьте командой на сообщение.",
            parse_mode="HTML",
        )
        return
    chat_id = message.chat.id
    if chat_id in _active_chats:
        await message.answer("⏳ В этом чате уже идёт разбор. Дождитесь вердикта.")
        return
    _active_chats.add(chat_id)
    try:
        await _run(bot, chat_id, text, service, settings)
    finally:
        _active_chats.discard(chat_id)


async def _say(bot: Bot, chat_id: int, text: str) -> None:
    for chunk in presenter.split_message(text):
        await bot.send_message(chat_id, chunk, parse_mode="HTML", disable_web_page_preview=True)


async def _run(
    bot: Bot, chat_id: int, text: str, service: AnalysisService, settings: Settings
) -> None:
    try:
        analysis_id = await service.start(text=text, question=None, rounds=None, model=None)
    except ValueError as exc:
        await bot.send_message(chat_id, f"Не могу разобрать: {presenter.esc(str(exc))}")
        return
    except ProviderNotConfigured as exc:
        await bot.send_message(chat_id, f"Провайдер не настроен: {presenter.esc(str(exc))}")
        return
    record = await service.get(analysis_id)
    link = f"{settings.public_url.rstrip('/')}/#/a/{analysis_id}" if settings.public_url else None
    await _say(
        bot,
        chat_id,
        presenter.render_started(record.title, service.analysts, record.rounds, settings.use_mock),
    )
    async for event in service.subscribe(analysis_id):
        if event.type == "round":
            await _say(
                bot, chat_id, presenter.render_round(event.data["round_no"], event.data["kind"])
            )
        elif event.type == "typing":
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        elif event.type == "turn":
            await _say(
                bot,
                chat_id,
                presenter.render_turn(
                    event.data["emoji"], event.data["analyst"], event.data["text"]
                ),
            )
        elif event.type == "verdict":
            verdict = Verdict.model_validate(event.data)
            await _say(bot, chat_id, presenter.render_verdict(verdict, service.analysts, link))
        elif event.type == "error":
            await _say(bot, chat_id, presenter.render_error(event.data["message"]))
