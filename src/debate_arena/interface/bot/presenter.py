"""Render analysis events as Telegram HTML messages. No Telegram I/O here."""

from __future__ import annotations

import html
from collections.abc import Iterable

from ...domain.schemas import AnalystSpec, Verdict

# Telegram caps a message at 4096 characters; keep headroom for HTML tags.
MESSAGE_LIMIT = 3900
_BAR_WIDTH = 10
_STATUS_LABELS = {
    "supported": "доказано",
    "plausible": "вероятно",
    "unsupported": "без доказательств",
    "unfalsifiable": "непроверяемо",
    "false": "опровергнуто",
}


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def _bar(value: int) -> str:
    value = max(0, min(_BAR_WIDTH, value))
    return "█" * value + "░" * (_BAR_WIDTH - value)


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split so no chunk exceeds ``limit``: on blank lines, then lines, then spaces.

    A hard cut never lands inside an HTML tag, so Telegram can parse every chunk.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = ""
        for piece in _split_long(paragraph, limit):
            candidate = piece if not current else f"{current}\n\n{piece}"
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_long(paragraph: str, limit: int) -> list[str]:
    """Cut an over-long paragraph at line breaks, then spaces, never inside ``<…>``."""
    if len(paragraph) <= limit:
        return [paragraph]
    pieces: list[str] = []
    rest = paragraph
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        open_tag = rest.rfind("<", 0, cut)
        if open_tag != -1 and rest.find(">", open_tag, cut) == -1:
            cut = open_tag if open_tag > 0 else cut
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return pieces


def render_started(title: str, analysts: Iterable[AnalystSpec], rounds: int, mock: bool) -> str:
    roster = "\n".join(f"{a.emoji} <b>{esc(a.name)}</b> — {esc(a.focus)}" for a in analysts)
    note = "\n\n<i>mock-режим: ключи провайдеров не заданы</i>" if mock else ""
    return (
        f"🧭 <b>Проверка здравого смысла</b>\n\n"
        f"📄 {esc(title)}\n\n"
        f"Аналитики:\n{roster}\n\nРаундов: {rounds}. Судья подведёт итог.{note}"
    )


def render_round(round_no: int, kind: str) -> str:
    return f"━━━━━━━━━━━━━━━\n🔁 <b>Раунд {round_no} — {esc(kind)}</b>"


def render_turn(emoji: str, analyst: str, text: str) -> str:
    body = esc(text)
    for marker in ("Принимаю:", "Оспариваю:", "Уточняю:"):
        body = body.replace(marker, f"<b>{marker}</b>")
    return f"{emoji} <b>{esc(analyst)}</b>\n{body}"


def render_verdict(verdict: Verdict, analysts: Iterable[AnalystSpec], link: str | None) -> str:
    emoji_by_name = {a.name: a.emoji for a in analysts}
    lines = [
        "⚖️ <b>ВЕРДИКТ СУДЬИ</b>",
        "",
        f"<i>{esc(verdict.core_question)}</i>",
        "",
        esc(verdict.conclusion),
        "",
        f"📊 Рациональность текста: <b>{verdict.rationality_score}/100</b> "
        f"· уверенность судьи {verdict.confidence}%",
    ]
    for title, items in (
        ("✅ Установлено", verdict.established),
        ("🟦 Вероятно", verdict.probable),
        ("🟨 Неопределённо", verdict.uncertain),
    ):
        if items:
            lines += ["", f"<b>{title}</b>"] + [f"• {esc(i)}" for i in items]
    if verdict.claims:
        lines += ["", "<b>Утверждения</b>"]
        lines += [
            f"• {esc(c.claim)} — <i>{_STATUS_LABELS.get(c.status, c.status)}</i>"
            for c in verdict.claims
        ]
    if verdict.fallacies:
        lines += ["", "<b>Уловки</b>"]
        lines += [f"• <b>{esc(f.name)}</b>: «{esc(f.quote)}»" for f in verdict.fallacies]
    lines += [
        "",
        f"<b>Сильнейшая версия:</b> {esc(verdict.steelman)}",
        f"<b>Что изменит вердикт:</b> {esc(verdict.falsification)}",
        f"<b>Что из этого следует:</b> {esc(verdict.so_what)}",
        "",
        "<b>Аналитики</b>",
    ]
    for s in sorted(verdict.scores, key=lambda x: x.total, reverse=True):
        crown = " 👑" if s.analyst == verdict.most_rational else ""
        lines.append(
            f"{emoji_by_name.get(s.analyst, '▫️')} <b>{esc(s.analyst)}</b> — {s.total}/40{crown}"
        )
        lines.append(f"<code>Строгость {_bar(s.rigor)} {s.rigor:>2}</code>")
        lines.append(f"<code>Факты     {_bar(s.evidence)} {s.evidence:>2}</code>")
        lines.append(f"<code>Благожел. {_bar(s.charity)} {s.charity:>2}</code>")
        lines.append(f"<code>Отзывчив. {_bar(s.responsiveness)} {s.responsiveness:>2}</code>")
    if link:
        lines += ["", f'🔗 <a href="{esc(link)}">Полный разбор в веб-интерфейсе</a>']
    return "\n".join(lines)


def render_error(message: str) -> str:
    return f"💥 Разбор не завершён: {esc(message)}"
