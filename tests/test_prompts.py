"""Prompt construction for analysts and judge (SPEC §4, ADR-002)."""

from __future__ import annotations

from debate_arena.domain.analysts import ANALYSTS
from debate_arena.domain.prompts import (
    MANIFEST,
    cross_round_user,
    judge_system_prompt,
    judge_user,
    participants_from_prompt,
    round_one_user,
    verdict_json_schema,
)

LANG = "русский"


class TestAnalysts:
    def test_three_analysts_with_distinct_lenses(self):
        assert len(ANALYSTS) == 3
        assert len({a.name for a in ANALYSTS}) == 3
        assert len({a.system_prompt for a in ANALYSTS}) == 3

    def test_every_analyst_carries_the_full_manifest(self):
        for a in ANALYSTS:
            assert MANIFEST in a.system_prompt
            assert "БРИТВА ОККАМА" in a.system_prompt
            assert "ПРИНЦИП БЛАГОЖЕЛАТЕЛЬНОСТИ" in a.system_prompt

    def test_no_philosophical_school_personas(self):
        banned = ("Ницше", "Поппер", "Сократ", "стоик", "марксист", "либертариан")
        for a in ANALYSTS:
            for word in banned:
                assert word not in a.system_prompt, (a.name, word)

    def test_analyst_goal_is_truth_not_victory(self):
        for a in ANALYSTS:
            assert "не «победить»" in a.system_prompt
            assert "одна команда" in a.system_prompt
            assert "соперничеств" in a.system_prompt


class TestRoundPrompts:
    def test_round_one_is_blind_and_structured(self):
        text = round_one_user("Земля плоская, потому что горизонт ровный.", None, LANG)
        assert "Земля плоская" in text
        assert "Независимый разбор" in text
        assert "Принимаю:" not in text  # cross-check blocks belong to later rounds
        assert LANG in text

    def test_round_one_includes_optional_question(self):
        text = round_one_user("тезис", "Правда ли это?", LANG)
        assert "Правда ли это?" in text
        assert "тезис" in text

    def test_cross_round_requires_accept_dispute_refine_blocks(self):
        text = cross_round_user("тезис", None, "[Раунд 1] Эмпирик: ...", 2, LANG)
        assert "[Раунд 1] Эмпирик: ..." in text
        for marker in ("Принимаю:", "Оспариваю:", "Уточняю:"):
            assert marker in text
        assert "Раунд 2" in text or "РАУНД 2" in text

    def test_language_is_injected_everywhere(self):
        assert "English" in round_one_user("t", None, "English")
        assert "English" in cross_round_user("t", None, "x", 2, "English")
        assert "English" in judge_user("t", None, "x", [], "English")

    def test_dialogue_mode_names_participants_and_forbids_winner_framing(self):
        text = round_one_user("Иван: а\nМария: б", None, LANG, participants=["Иван", "Мария"])
        assert "ДИАЛОГ" in text
        assert "Собеседники: Иван, Мария." in text
        assert "победител" in text.lower()  # explicitly told not to pick one
        cross = cross_round_user("t", None, "x", 2, LANG, participants=["Иван", "Мария"])
        assert "Собеседники: Иван, Мария." in cross
        assert "Собеседники:" not in round_one_user("t", None, LANG)


class TestJudgePrompts:
    def test_judge_system_prompt_seeks_truth_not_a_winner(self):
        system = judge_system_prompt(LANG)
        assert "судья" in system.lower()
        assert "ближе всего к истине" in system.lower()
        assert "не выбираешь победителя" in system.lower()
        assert "БРИТВА ОККАМА" in system

    def test_judge_user_text_mode_has_transcript_and_no_participants(self):
        text = judge_user("исходный текст", "вопрос?", "[Раунд 1] A: ...", [], LANG)
        assert "исходный текст" in text
        assert "вопрос?" in text
        assert "[Раунд 1] A: ..." in text
        assert "Собеседники:" not in text
        assert "participants" in text and "closest_to_truth" in text

    def test_judge_user_dialogue_mode_lists_participants(self):
        text = judge_user("Иван: а\nМария: б", None, "x", ["Иван", "Мария"], LANG)
        assert "Собеседники: Иван, Мария." in text
        assert participants_from_prompt(text) == ["Иван", "Мария"]
        assert participants_from_prompt(judge_user("t", None, "x", [], LANG)) == []

    def test_judge_user_embeds_schema_only_when_given(self):
        without = judge_user("t", None, "x", [], LANG)
        with_schema = judge_user("t", None, "x", [], LANG, json_schema='{"type":"object"}')
        assert '{"type":"object"}' not in without
        assert '{"type":"object"}' in with_schema

    def test_verdict_json_schema_matches_pydantic_model(self):
        schema = verdict_json_schema()
        props = schema["properties"]
        for field in ("conclusion", "rationality_score", "claims", "participants", "consensus"):
            assert field in props
        assert "scores" not in props
