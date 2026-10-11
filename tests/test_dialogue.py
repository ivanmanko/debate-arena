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


class TestAnonymize:
    def test_rewrites_speakers_and_mentions_with_declensions(self):
        from debate_arena.domain.dialogue import anonymize_dialogue

        text = (
            "Иван: Мария, ты не права.\n"
            "Мария: Один случай — не статистика, Иван.\n"
            "[12:03] **Иван:** Исследования куплены.\n"
            "Мария: Про Ивана и его соседа: это слова Ивана, а с Марией спорить нечего.\n"
            "Это продолжение реплики Марии без автора про ИВАНА."
        )
        result = anonymize_dialogue(text, ["Иван", "Мария"], language="русский")
        assert result.participants == ["Собеседник А", "Собеседник Б"]
        assert result.legend == {"Собеседник А": "Иван", "Собеседник Б": "Мария"}
        for real in ("Иван", "Мария", "Ивана", "Марией", "ИВАНА", "Марии"):
            assert real not in result.text, (real, result.text)
        lines = result.text.splitlines()
        assert lines[0].startswith("Собеседник А: ")
        assert lines[1].startswith("Собеседник Б: ")
        assert lines[2] == "Собеседник А: Исследования куплены."
        assert "Про Собеседник А и его соседа" in result.text
        assert "это слова Собеседник А" in result.text
        assert "с Собеседник Б спорить" in result.text
        assert "без автора про Собеседник А." in result.text

    def test_short_names_are_replaced_only_exactly(self):
        from debate_arena.domain.dialogue import anonymize_dialogue

        text = "Аня: Антон анализировал\nБоб: Аня права, антенна"
        result = anonymize_dialogue(text, ["Аня", "Боб"], language="русский")
        assert "Антон анализировал" in result.text
        assert "антенна" in result.text
        assert "Собеседник А права" in result.text

    def test_english_labels_and_many_participants(self):
        from debate_arena.domain.dialogue import anonymize_dialogue, participant_label

        assert participant_label(0, "English") == "Participant A"
        assert participant_label(1, "русский") == "Собеседник Б"
        assert participant_label(30, "русский") == "Собеседник 31"
        result = anonymize_dialogue("Ann: hi\nBob: hey", ["Ann", "Bob"], language="English")
        assert result.participants == ["Participant A", "Participant B"]
        assert result.text == "Participant A: hi\nParticipant B: hey"
