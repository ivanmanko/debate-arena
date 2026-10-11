"""Prompt construction for analysts and judge (SPEC §4, ADR-002)."""

from __future__ import annotations

from debate_arena.domain.analysts import ANALYSTS
from debate_arena.domain.prompts import (
    MANIFEST,
    cross_round_user,
    judge_system_prompt,
    judge_user,
    round_one_user,
    source_context,
    verdict_json_schema,
)

LANG = "русский"


class TestAnalysts:
    def test_three_analysts_with_distinct_lenses(self):
        assert len(ANALYSTS) == 3
        assert len({a.name for a in ANALYSTS}) == 3
        assert len({a.lens for a in ANALYSTS}) == 3

    def test_analysts_share_one_system_prompt_so_the_prefix_caches(self):
        # SPEC §4.8: the lens moves out of ``system``; all calls of an analysis share the prefix.
        assert len({a.system_prompt for a in ANALYSTS}) == 1
        for a in ANALYSTS:
            assert a.lens not in a.system_prompt
            assert a.lens.startswith("Твоя линза — ")

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


LENS = ANALYSTS[0].lens


class TestRoundPrompts:
    def test_material_lives_in_the_context_not_in_the_task(self):
        context = source_context("Земля плоская, потому что горизонт ровный.", None)
        assert "Земля плоская" in context
        task = round_one_user(LENS, LANG)
        assert "Земля плоская" not in task
        assert task.startswith(LENS)

    def test_round_one_is_blind_and_structured(self):
        text = round_one_user(LENS, LANG)
        assert "Независимый разбор" in text
        assert "Принимаю:" not in text  # cross-check blocks belong to later rounds
        assert LANG in text

    def test_context_includes_optional_question(self):
        context = source_context("тезис", "Правда ли это?")
        assert "Правда ли это?" in context
        assert "тезис" in context

    def test_context_is_identical_for_identical_input(self):
        same = ("т", "в", "dialogue", ["Собеседник А"])
        assert source_context(*same) == source_context(*same)

    def test_cross_round_requires_accept_dispute_refine_blocks(self):
        text = cross_round_user(LENS, "[Раунд 1] Эмпирик: ...", 2, LANG)
        assert text.startswith(LENS)
        assert "[Раунд 1] Эмпирик: ..." in text
        for marker in ("Принимаю:", "Оспариваю:", "Уточняю:"):
            assert marker in text
        assert "Раунд 2" in text or "РАУНД 2" in text

    def test_language_is_injected_everywhere(self):
        assert "English" in round_one_user(LENS, "English")
        assert "English" in cross_round_user(LENS, "x", 2, "English")
        assert "English" in judge_user("x", "text", "English")

    def test_dialogue_mode_marks_the_material_and_asks_for_each_position(self):
        # ADR-008: no parsed names — the models identify the speakers themselves.
        context = source_context("**Психолог.** а\n**Автор.** б", None, "dialogue")
        assert "ДИАЛОГ" in context
        text = round_one_user(LENS, LANG, mode="dialogue")
        assert "победител" in text.lower()  # explicitly told not to pick one
        assert "определи собеседников" in text.lower()
        assert "упомина" in text.lower()  # quoted or mentioned people are not speakers
        assert "Помни" in cross_round_user(LENS, "x", 2, LANG, mode="dialogue")

    def test_auto_mode_makes_the_dialogue_task_conditional(self):
        assert "ДИАЛОГ" not in source_context("т", None, "auto")
        text = round_one_user(LENS, LANG, mode="auto")
        assert "Если материал — диалог" in text
        assert "определи собеседников" in text.lower()

    def test_text_mode_has_no_dialogue_task(self):
        assert "собеседник" not in round_one_user(LENS, LANG, mode="text").lower()
        assert "ДИАЛОГ" not in source_context("т", None, "text")

    def test_hidden_name_labels_are_announced_in_the_context(self):
        context = source_context("Собеседник А: а", None, "auto", ["Собеседник А"])
        assert "заменены метками: Собеседник А" in context
        assert "заменены метками" not in source_context("т", None, "auto")


class TestJudgePrompts:
    def test_judge_system_prompt_seeks_truth_not_a_winner(self):
        system = judge_system_prompt(LANG)
        assert "судья" in system.lower()
        assert "ближе всего к истине" in system.lower()
        assert "не выбираешь победителя" in system.lower()
        assert "БРИТВА ОККАМА" in system

    def test_judge_is_asked_for_a_full_narrative_not_a_brief(self):
        system = judge_system_prompt(LANG)
        assert "narrative" in system
        assert "400–800 слов" in system
        assert "3–8 пунктов" in system
        assert "кратко и конкретно" not in system
        user = judge_user("x", "text", LANG)
        assert "narrative" in user

    def test_judge_user_text_mode_has_transcript_and_empty_participants(self):
        text = judge_user("[Раунд 1] A: ...", "text", LANG)
        assert "[Раунд 1] A: ..." in text
        assert "participants — пустой список" in text and "closest_to_truth — null" in text

    def test_judge_user_dialogue_mode_requires_two_speakers_it_names_itself(self):
        text = judge_user("x", "dialogue", LANG)
        assert "не меньше двух" in text
        assert "ровно как в материале" in text

    def test_judge_user_auto_mode_lets_the_judge_decide(self):
        text = judge_user("x", "auto", LANG)
        assert "participants — пустой список" in text
        assert "не меньше двух" in text

    def test_judge_user_embeds_schema_only_when_given(self):
        without = judge_user("x", "text", LANG)
        with_schema = judge_user("x", "text", LANG, json_schema='{"type":"object"}')
        assert '{"type":"object"}' not in without
        assert '{"type":"object"}' in with_schema

    def test_verdict_json_schema_matches_pydantic_model(self):
        schema = verdict_json_schema()
        props = schema["properties"]
        for field in ("conclusion", "narrative", "rationality_score", "claims", "participants"):
            assert field in props
        assert "scores" not in props
