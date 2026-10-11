"""Dialogue parsing and mode detection (SPEC §4.0, §6 №9)."""

from __future__ import annotations

import pytest

from debate_arena.domain.dialogue import parse_dialogue, resolve_mode

DIALOGUE = """Иван: Вакцины вызывают аутизм, мой сосед это видел.
Мария: Один случай — не статистика. Есть исследования на миллионах детей.
Иван: Исследования куплены фармой.
Это продолжение реплики Ивана без автора.
Мария — Это непроверяемое утверждение, докажи."""


class TestParse:
    def test_finds_participants_and_attaches_continuations(self):
        parts = parse_dialogue(DIALOGUE)
        assert [p.name for p in parts] == ["Иван", "Мария"]
        ivan = parts[0]
        assert len(ivan.lines) == 2
        assert "без автора" in ivan.lines[1]
        assert len(parts[1].lines) == 2

    @pytest.mark.parametrize(
        "line",
        [
            "**Аня:** привет",
            "[Аня] привет",
            "[12:03] Аня: привет",
            "Аня — привет",
            "<Аня> привет",
        ],
    )
    def test_speaker_formats(self, line):
        parts = parse_dialogue(f"{line}\nБоб: ответ")
        assert [p.name for p in parts] == ["Аня", "Боб"]

    def test_long_or_url_like_prefixes_are_not_names(self):
        text = (
            "Главная мысль статьи, которую стоит запомнить навсегда: всё относительно.\n"
            "https://example.org: ссылка\n"
            "Вывод: всё сложно"
        )
        assert [p.name for p in parse_dialogue(text)] == ["Вывод"]

    def test_plain_text_has_no_participants(self):
        assert parse_dialogue("Просто текст.\nЕщё строка без двоеточий.") == []


class TestResolveMode:
    def test_auto_detects_dialogue(self):
        mode, names = resolve_mode(DIALOGUE, "auto", line_share=0.6)
        assert mode == "dialogue" and names == ["Иван", "Мария"]

    def test_auto_keeps_text_when_speaker_lines_are_rare(self):
        text = "\n".join(
            ["Обычная строка текста без автора."] * 8 + ["Автор: цитата", "Критик: ответ"]
        )
        assert resolve_mode(text, "auto", line_share=0.6) == ("text", [])

    def test_auto_single_signature_is_text(self):
        assert resolve_mode("Автор: один длинный монолог.", "auto", line_share=0.6) == ("text", [])

    def test_forced_text_ignores_speakers(self):
        assert resolve_mode(DIALOGUE, "text", line_share=0.6) == ("text", [])

    def test_forced_dialogue_requires_two_participants(self):
        with pytest.raises(ValueError, match="Имя: реплика"):
            resolve_mode("Автор: монолог", "dialogue", line_share=0.6)
        mode, names = resolve_mode(DIALOGUE, "dialogue", line_share=0.6)
        assert mode == "dialogue" and len(names) == 2
