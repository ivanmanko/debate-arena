"""Public snapshot page of an analysis (SPEC §4.7, ADR-006).

One self-contained HTML document: inline CSS, no JS, no external resources, ``noindex``.
Every piece of text goes through ``html.escape``; the only markup taken from model output
is ``**bold**`` and the «Принимаю/Оспариваю/Уточняю» blocks, rebuilt here. The judge chat
is never part of the page.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from html import escape
from pathlib import Path

from ...application.flow import CROSS_ROUND_KIND, ROUND_ONE_KIND
from ...domain.dialogue import participant_label
from ...domain.records import AnalysisRecord, TurnRecord
from ...domain.schemas import AnalystSpec, Verdict

_CSS = (Path(__file__).parent / "snapshot.css").read_text(encoding="utf-8")
_FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
    "%3Ctext y='.9em' font-size='90'%3E%E2%9A%96%EF%B8%8F%3C/text%3E%3C/svg%3E"
)
_MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
_CLAIM_LABELS = {
    "supported": "доказано",
    "plausible": "вероятно",
    "unsupported": "без доказательств",
    "unfalsifiable": "непроверяемо",
    "false": "опровергнуто",
}
_BLOCKS = {"Принимаю:": "accept", "Оспариваю:": "dispute", "Уточняю:": "refine"}
_ANALYST_COLORS = ("var(--a1)", "var(--a2)", "var(--a3)")
_PERSON_COLORS = ("var(--a1)", "var(--a2)", "var(--a3)", "var(--judge)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_HEADING_RE = re.compile(r"(^|\n)#{1,4}\s*")
_BOLD_MARKER_RE = re.compile(r"\*\*(Принимаю|Оспариваю|Уточняю):\*\*")
_PARA_RE = re.compile(r"\n\s*\n")
_DESCRIPTION_CHARS = 200
# Defence in depth: the page needs nothing but its inline style and the data: favicon.
_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"


def render_snapshot(record: AnalysisRecord, analysts: Sequence[AnalystSpec]) -> str:
    """The whole page; ``record`` must have a verdict (the service checks status first)."""
    verdict = record.verdict
    if verdict is None:
        raise ValueError("a snapshot needs a verdict")
    title = f"{record.title} — Проверка здравого смысла"
    description = _clip(verdict.conclusion, _DESCRIPTION_CHARS)
    body = "".join(
        (
            _brand(),
            _source(record),
            _verdict(verdict),
            _transcript(record.turns, analysts),
            _foot(record),
        )
    )
    return (
        "<!doctype html>\n"
        '<html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        '<meta name="referrer" content="no-referrer">'
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">'
        f"<title>{_e(title)}</title>"
        f'<meta name="description" content="{_e(description)}">'
        f'<meta property="og:title" content="{_e(record.title)}">'
        f'<meta property="og:description" content="{_e(description)}">'
        '<meta property="og:type" content="article">'
        f'<link rel="icon" href="{_FAVICON}">'
        f"<style>{_CSS}</style></head>"
        f'<body><main class="page">{body}</main></body></html>\n'
    )


# --- sections ------------------------------------------------------------------------


def _brand() -> str:
    return (
        '<header class="brand"><span class="brand-mark">⚖️</span><span>'
        '<span class="brand-title">Проверка здравого смысла</span>'
        '<span class="brand-sub">три рациональных аналитика ищут истину</span>'
        "</span></header>"
    )


def _source(record: AnalysisRecord) -> str:
    dialogue = record.mode == "dialogue"
    meta = [
        "Диалог" if dialogue else "Текст",
        f"Модель: {record.model_analyst}",
        f"Раундов: {record.rounds}",
        f"Символов: {len(record.source_text)}",
    ]
    parts = [
        '<section class="card">',
        f'<div class="eyebrow">Разбор · {_date(record.created_at)}</div>',
        f'<h1 class="source-title">{_e(record.title)}</h1>',
        '<div class="meta">' + "".join(f"<span>{_e(m)}</span>" for m in meta) + "</div>",
    ]
    if dialogue and record.participants:
        chips = "".join(f'<span class="pchip">{_e(p)}</span>' for p in record.participants)
        parts.append(
            f'<div class="participants"><span class="muted small">Диалог:</span>{chips}</div>'
        )
    if record.question:
        parts.append(f'<p class="question">Вопрос: {_e(record.question)}</p>')
    summary = "Исходный диалог" if dialogue else "Исходный текст"
    parts.append(
        f'<details class="v-section"><summary>{summary}</summary>'
        f'<p class="source-text">{_e(record.source_text)}</p></details>'
    )
    parts.append("</section>")
    return "".join(parts)


def _verdict(v: Verdict) -> str:
    color = (
        "var(--bad)"
        if v.rationality_score < 40
        else "var(--warn)"
        if v.rationality_score < 70
        else "var(--ok)"
    )
    parts = [
        '<section class="card verdict"><div class="verdict-head">',
        f'<div class="gauge" style="--g:{v.rationality_score};--g-color:{color}">'
        f'<div><div class="g-val">{v.rationality_score}</div>'
        '<div class="g-lbl">рациональность<br>материала</div></div></div>',
        '<div class="verdict-main"><h2>⚖️ Вердикт судьи · что ближе всего к истине</h2>',
        f'<p class="core">{_e(v.core_question)}</p>',
        f'<p class="conclusion">{_e(v.conclusion)}</p>',
        f'<div class="confidence">Уверенность судьи: {v.confidence}%'
        f'<div class="bar"><i style="width:{v.confidence}%"></i></div></div>',
        "</div></div>",
    ]
    if v.narrative.strip():
        parts.append(
            '<div class="narrative"><h3>Развёрнутое обоснование</h3>'
            + "".join(f"<p>{_inline(p)}</p>" for p in _paragraphs(v.narrative))
            + "</div>"
        )
    parts.append(
        '<div class="v-grid">'
        + _column("est", "Установлено", v.established)
        + _column("prob", "Вероятно", v.probable)
        + _column("unc", "Неопределённо", v.uncertain)
        + "</div>"
    )
    if v.claims:
        rows = "".join(
            f"<tr><td>{_e(c.claim)}{_by(c.by)}</td>"
            f'<td><span class="cs cs-{_e(c.status)}">{_claim_label(c.status)}</span></td>'
            f'<td class="muted">{_e(c.note)}</td></tr>'
            for c in v.claims
        )
        parts.append(
            '<div class="v-section"><h3>Утверждения</h3><table class="claims"><thead><tr>'
            "<th>Утверждение</th><th>Статус</th><th>Почему</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )
    fallacies = "".join(
        f'<div class="fallacy"><div class="f-name">{_e(f.name)}{_by(f.by)}</div>'
        f"<blockquote>{_e(f.quote)}</blockquote><p>{_e(f.explanation)}</p></div>"
        for f in v.fallacies
    )
    heading = f"Уловки и ошибки ({len(v.fallacies)})" if v.fallacies else "Уловки и ошибки"
    parts.append(
        f'<div class="v-section"><h3>{heading}</h3>'
        + (fallacies or '<p class="muted small">Демагогических приёмов не найдено.</p>')
        + "</div>"
    )
    parts.append(
        '<div class="v-section v-three">'
        + _mini("Сильнейшая версия", v.steelman)
        + _mini("Что изменит вердикт", v.falsification)
        + _mini("Что из этого следует", v.so_what)
        + "</div>"
    )
    if v.participants:
        people = []
        ranked = sorted(v.participants, key=lambda p: p.truth_proximity, reverse=True)
        for i, p in enumerate(ranked):
            closest = " closest" if p.name == v.closest_to_truth else ""
            wrong = f'<p class="bad">{_e(p.wrong_about)}</p>' if p.wrong_about else ""
            color = _PERSON_COLORS[i % len(_PERSON_COLORS)]
            people.append(
                f'<div class="person{closest}" style="--p-color:{color}">'
                f'<div class="p-head"><span>{_e(p.name)}</span>'
                f'<span class="p-score">{p.truth_proximity}</span></div>'
                f'<div class="bar"><i style="width:{p.truth_proximity}%"></i></div>'
                f'<p>{_e(p.position)}</p><p class="ok">{_e(p.right_about)}</p>{wrong}'
                f'<p class="p-note">{_e(p.note)}</p></div>'
            )
        parts.append(
            '<div class="v-section"><h3>Собеседники · близость к истине</h3>'
            '<p class="truth-banner">Ближе всего к истине: '
            f"<span>{_e(v.closest_to_truth or '—')}</span></p>"
            f'<div class="people">{"".join(people)}</div></div>'
        )
    parts.append(
        '<div class="v-section v-three">'
        + _mini_list("На чём сошлись аналитики", v.consensus)
        + _mini_list("Осталось открытым", v.open_questions)
        + "</div>"
    )
    parts.append("</section>")
    return "".join(parts)


def _transcript(turns: Sequence[TurnRecord], analysts: Sequence[AnalystSpec]) -> str:
    if not turns:
        return ""
    by_name = {
        a.name: (a, _ANALYST_COLORS[i % len(_ANALYST_COLORS)]) for i, a in enumerate(analysts)
    }
    parts = ['<section class="card"><details><summary>', f"Реплики аналитиков ({len(turns)})"]
    parts.append('</summary><div class="transcript">')
    current_round = 0
    for turn in turns:
        if turn.round_no != current_round:
            current_round = turn.round_no
            kind = ROUND_ONE_KIND if current_round == 1 else CROSS_ROUND_KIND
            parts.append(f'<div class="round-head"><h3>Раунд {current_round} · {kind}</h3></div>')
        spec, color = by_name.get(turn.analyst, (None, "var(--border)"))
        lens = f'<span class="lens">· {_e(spec.focus)}</span>' if spec else ""
        parts.append(
            f'<div class="card turn" style="--a-color:{color}"><div class="turn-head">'
            f'<span>{_e(turn.emoji)}</span><span class="who">{_e(turn.analyst)}</span>{lens}</div>'
            f"{_turn_body(turn.text)}</div>"
        )
    parts.append("</div></details></section>")
    return "".join(parts)


def _foot(record: AnalysisRecord) -> str:
    lines = [
        "Снимок разбора: аналитики и судья — языковые модели, они работают только с текстом и "
        "своими знаниями, без поиска по источникам.",
    ]
    if record.mode == "dialogue" and _are_labels(record.participants):
        lines.append("Имена собеседников заменены метками.")
    return '<footer class="foot">' + "".join(f"<p>{_e(t)}</p>" for t in lines) + "</footer>"


# --- pieces --------------------------------------------------------------------------


def _turn_body(text: str) -> str:
    """Mirror of the web UI's renderTurnBody: marker paragraphs open a coloured block."""
    out: list[str] = []
    block_open = False
    for para in _paragraphs(_BOLD_MARKER_RE.sub(r"\1:", text)):
        marker = next((m for m in _BLOCKS if para.startswith(m)), None)
        if marker:
            if block_open:
                out.append("</div>")
            rest = para[len(marker) :].strip()
            out.append(
                f'<div class="block {_BLOCKS[marker]}"><span class="tag">{marker[:-1]}</span>'
                + (f"<p>{_inline(rest)}</p>" if rest else "")
            )
            block_open = True
        else:
            out.append(f"<p>{_inline(para)}</p>")
    if block_open:
        out.append("</div>")
    return '<div class="turn-body">' + "".join(out) + "</div>"


def _column(cls: str, title: str, items: Iterable[str]) -> str:
    return f'<div class="v-col {cls}"><h4>{title}</h4>{_list(items)}</div>'


def _mini(title: str, text: str) -> str:
    return f'<div class="v-mini"><h4>{title}</h4><p>{_e(text)}</p></div>'


def _mini_list(title: str, items: Iterable[str]) -> str:
    return f'<div class="v-mini"><h4>{title}</h4>{_list(items)}</div>'


def _list(items: Iterable[str]) -> str:
    lis = "".join(f"<li>{_e(i)}</li>" for i in items)
    return f"<ul>{lis}</ul>" if lis else '<p class="muted small">—</p>'


def _are_labels(participants: Sequence[str]) -> bool:
    """True when the names are §4.0b labels, not real names (ANONYMIZE_PARTICIPANTS=false)."""
    return bool(participants) and any(
        list(participants) == [participant_label(i, lang) for i in range(len(participants))]
        for lang in ("русский", "english")
    )


def _claim_label(status: str) -> str:
    return _CLAIM_LABELS.get(status) or _e(status)


def _by(name: str | None) -> str:
    return f' <span class="muted small">— {_e(name)}</span>' if name else ""


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARA_RE.split(text) if p.strip()]


def _inline(text: str) -> str:
    return _HEADING_RE.sub(r"\1", _BOLD_RE.sub(r"<b>\1</b>", _e(text)))


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _date(value: datetime) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"
