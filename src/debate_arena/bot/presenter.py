"""Presenter — the showmanship layer.

Implements `DebateStream`: every dramatic beat the flow emits becomes a nicely
formatted Telegram message with typing indicators, round headers and, as the
climax, a verdict card with ASCII score bars. All Telegram knowledge lives here.
"""

from __future__ import annotations

import html

from aiogram import Bot
from aiogram.enums import ChatAction

from ..config import settings
from ..debate.flow import DebateStream
from ..debate.schemas import PersonaSpec, Verdict

_BAR_WIDTH = 10


def _bar(value: int) -> str:
    value = max(0, min(10, value))
    return "█" * value + "░" * (_BAR_WIDTH - value)


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


class TelegramPresenter(DebateStream):
    def __init__(self, bot: Bot, chat_id: int) -> None:
        self.bot = bot
        self.chat_id = chat_id

    async def _say(self, text: str) -> None:
        await self.bot.send_message(self.chat_id, text, parse_mode="HTML")

    async def on_start(self, topic: str, personas: list[PersonaSpec]) -> None:
        roster = "\n".join(f"{p.emoji} <b>{_esc(p.name)}</b> — {_esc(p.role)}" for p in personas)
        await self._say(
            f"🥊 <b>ДЕБАТЫ НАЧИНАЮТСЯ</b>\n\n"
            f"📜 Тема: <b>{_esc(topic)}</b>\n\n"
            f"Участники:\n{roster}\n\n"
            f"Раундов: {settings.max_rounds}. Судит беспристрастный судья. Погнали!"
        )

    async def on_round_header(self, round_no: int, kind: str) -> None:
        await self._say(f"━━━━━━━━━━━━━━━\n🥊 <b>Раунд {round_no} — {_esc(kind)}</b>")

    async def on_typing(self, persona: PersonaSpec) -> None:
        await self.bot.send_chat_action(self.chat_id, ChatAction.TYPING)

    async def on_turn(self, round_no: int, persona: PersonaSpec, text: str) -> None:
        await self._say(f"{persona.emoji} <b>{_esc(persona.name)}:</b>\n{_esc(text)}")

    async def on_verdict(self, verdict: Verdict, personas: list[PersonaSpec]) -> None:
        await self._say(self.render_verdict_card(verdict, personas))

    async def on_error(self, error: Exception) -> None:
        await self._say(
            "💥 Дебаты сорвались: что-то пошло не так на стороне моделей. "
            "Попробуйте ещё раз чуть позже."
        )

    @staticmethod
    def render_verdict_card(verdict: Verdict, personas: list[PersonaSpec]) -> str:
        emoji_by_name = {p.name: p.emoji for p in personas}
        ranked = sorted(verdict.scores, key=lambda s: s.total, reverse=True)

        lines = ["⚖️ <b>ВЕРДИКТ СУДЬИ</b>", ""]
        for s in ranked:
            emoji = emoji_by_name.get(s.persona, "▫️")
            crown = " 👑" if s.persona == verdict.winner else ""
            lines.append(f"{emoji} <b>{_esc(s.persona)}</b> — {s.total}/40{crown}")
            lines.append(_score_line("Логика  ", s.logic))
            lines.append(_score_line("Факты   ", s.evidence))
            lines.append(_score_line("Риторика", s.rhetoric))
            lines.append(_score_line("Реакция ", s.responsiveness))
            lines.append(f"<i>{_esc(s.note)}</i>")
            lines.append("")

        lines.append(f"🏆 Победитель: <b>{_esc(verdict.winner)}</b>")
        lines.append(f"💬 <i>{_esc(verdict.punchline)}</i>")
        return "\n".join(lines)


def _score_line(label: str, value: int) -> str:
    return f"<code>{label} {_bar(value)} {value:>2}</code>"


def render_standings(personas) -> str:
    """Render the leaderboard for /standings. `personas` are ORM rows (sorted)."""
    played = [p for p in personas if p.debates > 0]
    if not played:
        return "📊 Пока ни одного дебата. Запустите /debate <тема> — и таблица оживёт."

    lines = ["📊 <b>ЛИДЕРБОРД ПЕРСОН</b>", ""]
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(played):
        medal = medals[i] if i < len(medals) else f"{i + 1}."
        avg = p.total_score / p.debates if p.debates else 0
        lines.append(
            f"{medal} {p.emoji} <b>{_esc(p.name)}</b> — "
            f"🏆 {p.wins} побед / {p.debates} дебатов · ср. балл {avg:.1f}/40"
        )
    return "\n".join(lines)
