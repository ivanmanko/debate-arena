"""Hiding names behind labels (SPEC §4.0b, §6 №12–13a, ADR-008)."""

from __future__ import annotations

from debate_arena.domain.names import (
    anonymize_mentions,
    hide_names,
    normalize_names,
    participant_label,
    transliterate,
)

LEGEND = {"Собеседник А": "Иван", "Собеседник Б": "Мария"}


class TestHideNames:
    def test_names_and_declensions_become_labels_in_list_order(self):
        text = (
            "Иван: Мария, ты не права.\n"
            "Мария: Один случай — не статистика, Иван.\n"
            "[12:03] **Иван.** Исследования куплены.\n"
            "Мария: Про Ивана и его соседа: это слова Ивана, а с Марией спорить нечего.\n"
            "Продолжение реплики Марии про ИВАНА."
        )
        result = hide_names(text, "Кто прав, Иван или Мария?", ["Иван", "Мария"], "русский")
        assert result.legend == LEGEND
        for real in ("Иван", "Мария", "Ивана", "Марией", "ИВАНА", "Марии"):
            assert real not in result.text, (real, result.text)
        assert result.text.splitlines()[0] == "Собеседник А: Собеседник Б, ты не права."
        assert "**Собеседник А.** Исследования куплены." in result.text
        assert "Про Собеседник А и его соседа" in result.text
        assert result.question == "Кто прав, Собеседник А или Собеседник Б?"

    def test_lines_without_speakers_are_kept_as_they_are(self):
        # No parser any more: headings, dashes and lists pass through untouched.
        text = "# Спор\n\n**Психолог.** Тело бежать — гнев.\n- список литературы"
        result = hide_names(text, None, ["Психолог"], "русский")
        assert result.text == "# Спор\n\n**Собеседник А.** Тело бежать — гнев.\n- список литературы"
        assert result.question is None

    def test_names_missing_from_the_material_get_no_label(self):
        result = hide_names("Мария: привет", None, ["Иван", "Мария"], "русский")
        assert result.legend == {"Собеседник А": "Мария"}
        assert result.text == "Собеседник А: привет"

    def test_a_name_only_in_the_question_still_counts(self):
        result = hide_names("текст", "Прав ли Иван?", ["Иван"], "русский")
        assert result.legend == {"Собеседник А": "Иван"}
        assert result.question == "Прав ли Собеседник А?"

    def test_no_names_means_no_changes(self):
        result = hide_names("Иван: а", "q", [], "русский")
        assert (result.text, result.question, result.legend) == ("Иван: а", "q", {})

    def test_short_names_are_replaced_only_exactly(self):
        result = hide_names("Аня: Антон анализировал\nБоб: Аня права, антенна", None, ["Аня"], "ru")
        assert "Антон анализировал" in result.text
        assert "антенна" in result.text
        assert "Собеседник А права" in result.text

    def test_latin_names_and_their_cyrillic_forms(self):
        text = "Vanya: мысль\nFriska Sosiska: да\nKira: Ваня, а Фриска права. Отвечаю Фриске."
        result = hide_names(text, None, ["Vanya", "Friska Sosiska", "Kira"], "русский")
        for leaked in ("Vanya", "Friska", "Kira", "Ваня", "Фриска", "Фриске"):
            assert leaked not in result.text, (leaked, result.text)
        assert "Собеседник А, а Собеседник Б права" in result.text

    def test_english_labels(self):
        result = hide_names("Ann: hi\nBob: hey", None, ["Ann", "Bob"], "English")
        assert result.text == "Participant A: hi\nParticipant B: hey"


class TestHelpers:
    def test_labels(self):
        assert participant_label(0, "English") == "Participant A"
        assert participant_label(1, "русский") == "Собеседник Б"
        assert participant_label(30, "русский") == "Собеседник 31"

    def test_normalize_strips_drops_blanks_and_duplicates(self):
        assert normalize_names([" Иван ", "", "иван", "Мария", "  "]) == ["Иван", "Мария"]

    def test_transliterate(self):
        assert transliterate("Vanya") == "Ваня"

    def test_anonymize_mentions_with_legend(self):
        assert anonymize_mentions("Ивана не слушай", LEGEND) == "Собеседник А не слушай"
        assert anonymize_mentions("Иван прав", {}) == "Иван прав"
