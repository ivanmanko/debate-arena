"""Telegram presenter: pure rendering, no Telegram I/O."""

from __future__ import annotations

from debate_arena.domain.analysts import ANALYSTS
from debate_arena.interface.bot import presenter
from tests.fakes.llm import make_verdict

NAMES = [a.name for a in ANALYSTS]


def test_verdict_card_contains_key_sections_and_crown():
    verdict = make_verdict(NAMES, most_rational=NAMES[1])
    card = presenter.render_verdict(verdict, ANALYSTS, link="https://example.org/#/a/7")
    assert "ВЕРДИКТ СУДЬИ" in card
    assert "61/100" in card
    assert "👑" in card and NAMES[1] in card
    assert "█" in card and "░" in card
    assert 'href="https://example.org/#/a/7"' in card
    assert "Что изменит вердикт" in card


def test_turn_escapes_html_and_bolds_markers():
    text = presenter.render_turn("🔎", "Эмпирик", "Принимаю: <b>довод</b>\n\nОспариваю: нет")
    assert "&lt;b&gt;" in text
    assert "<b>Принимаю:</b>" in text and "<b>Оспариваю:</b>" in text


def test_split_message_respects_limit_on_paragraphs():
    text = "\n\n".join(["абзац " * 50] * 6)
    chunks = presenter.split_message(text, limit=700)
    assert len(chunks) > 1
    assert all(len(c) <= 700 for c in chunks)
    assert "".join(c.replace("\n\n", "") for c in chunks) == text.replace("\n\n", "")


def test_split_message_hard_splits_a_single_huge_paragraph():
    chunks = presenter.split_message("x" * 1500, limit=700)
    assert [len(c) for c in chunks] == [700, 700, 100]


def test_split_never_cuts_inside_a_tag_and_prefers_spaces():
    words = " ".join(f"<b>слово{i}</b>" for i in range(400))  # ~5 500 chars, no newlines
    chunks = presenter.split_message(words, limit=700)
    assert all(len(c) <= 700 for c in chunks)
    for chunk in chunks:
        assert chunk.count("<") == chunk.count(">")
        assert not chunk.startswith(" ") and not chunk.endswith(" ")
