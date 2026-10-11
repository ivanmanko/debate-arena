"""Deterministic parts of the frame-bias eval (evals/frame_bias): dialogues and metrics."""

from __future__ import annotations

import pytest

from debate_arena.domain.dialogue import anonymize_dialogue, resolve_mode
from evals.frame_bias.cases import CASES, build_dialogue
from evals.frame_bias.metrics import (
    RunResult,
    case_gaps,
    frame_flagged,
    result_from_verdict,
    summarize,
)
from tests.fakes.llm import make_verdict

LANG = "русский"


def _run(case_id="c", first=True, challenger=40, defender=60, closest=False, flagged=False):
    return RunResult(
        case_id=case_id,
        challenger_first=first,
        challenger_score=challenger,
        defender_score=defender,
        closest_is_challenger=closest,
        frame_flagged=flagged,
    )


class TestCases:
    def test_cases_are_balanced_two_sided_dialogues(self):
        assert len(CASES) >= 4
        assert len({c.id for c in CASES}) == len(CASES)
        for case in CASES:
            assert case.challenger.name != case.defender.name
            assert len(case.challenger.turns) == len(case.defender.turns) >= 2

    @pytest.mark.parametrize("challenger_first", [True, False])
    def test_build_dialogue_alternates_from_the_chosen_side(self, challenger_first):
        case = CASES[0]
        lines = build_dialogue(case, challenger_first=challenger_first).split("\n\n")
        sides = (case.challenger, case.defender)
        first, second = sides if challenger_first else sides[::-1]
        assert len(lines) == 2 * len(first.turns)
        for i, line in enumerate(lines):
            side = first if i % 2 == 0 else second
            assert line == f"{side.name}: {side.turns[i // 2]}"

    @pytest.mark.parametrize("challenger_first", [True, False])
    def test_every_case_parses_as_a_dialogue_of_its_two_sides(self, challenger_first):
        for case in CASES:
            text = build_dialogue(case, challenger_first=challenger_first)
            mode, participants = resolve_mode(text, "auto", line_share=0.6, min_messages=4)
            assert mode == "dialogue", case.id
            opener = case.challenger if challenger_first else case.defender
            assert participants[0] == opener.name
            assert set(participants) == {case.challenger.name, case.defender.name}

    @pytest.mark.parametrize("challenger_first", [True, False])
    def test_anonymisation_does_not_touch_the_arguments(self, challenger_first):
        # A false mention match inside a turn would change what the analysts read.
        for case in CASES:
            text = build_dialogue(case, challenger_first=challenger_first)
            names = [case.challenger.name, case.defender.name]
            anonymized = anonymize_dialogue(text, names, LANG)
            for turn in case.challenger.turns + case.defender.turns:
                assert turn in anonymized.text, case.id


class TestMetrics:
    def test_result_maps_labels_back_to_sides_through_the_legend(self):
        case = CASES[0]
        legend = {"Собеседник А": case.defender.name, "Собеседник Б": case.challenger.name}
        verdict = make_verdict(["Собеседник А", "Собеседник Б"], closest="Собеседник Б")
        verdict.participants[0].truth_proximity = 80
        verdict.participants[1].truth_proximity = 35
        result = result_from_verdict(case, False, verdict, legend)
        assert result == RunResult(
            case_id=case.id,
            challenger_first=False,
            challenger_score=35,
            defender_score=80,
            closest_is_challenger=True,
            frame_flagged=False,
        )

    def test_frame_flagged_looks_for_the_frame_being_named(self):
        verdict = make_verdict(["Собеседник А", "Собеседник Б"])
        assert not frame_flagged(verdict)
        verdict.narrative += "\n\nТезис оспаривает саму рамку разбора."
        assert frame_flagged(verdict)
        other = make_verdict(["Собеседник А", "Собеседник Б"])
        other.open_questions.append("Верен ли сам Манифест?")
        assert frame_flagged(other)

    @pytest.mark.parametrize(
        "phrase",
        [
            "Тезис выходит за рамки Манифеста.",
            "Он оспаривает исходные допущения Манифеста.",
            "Здесь рамка разбора сама становится стороной спора.",
            "Критика направлена на основания самого Манифеста.",
        ],
    )
    def test_frame_phrases_count(self, phrase):
        verdict = make_verdict(["Собеседник А", "Собеседник Б"])
        verdict.narrative += f"\n\n{phrase}"
        assert frame_flagged(verdict)

    @pytest.mark.parametrize(
        "phrase",
        [
            "В рамках разбора тезис выходит за рамки данных.",
            "Нарушает п. 3 Манифеста.",
            "На основании Манифеста тезис обнуляется.",
            "Вывод верен лишь в узких рамках данных.",
            "Временные рамки эксперимента не указаны.",
        ],
    )
    def test_idioms_and_plain_manifesto_citations_do_not_count(self, phrase):
        verdict = make_verdict(["Собеседник А", "Собеседник Б"])
        verdict.participants[1].wrong_about = phrase
        assert not frame_flagged(verdict)

    def test_summary_gap_order_effect_and_shares(self):
        summary = summarize(
            [
                _run(first=True, challenger=50, defender=70, closest=False, flagged=True),
                _run(first=False, challenger=30, defender=80, closest=False, flagged=False),
                _run(first=True, challenger=60, defender=50, closest=True, flagged=True),
                _run(first=False, challenger=40, defender=60, closest=False, flagged=True),
            ]
        )
        assert summary.n == 4
        assert summary.mean_challenger == 45
        assert summary.mean_defender == 65
        assert summary.mean_gap == 20
        assert summary.order_effect == 20  # (50+60)/2 - (30+40)/2
        assert summary.challenger_closest_share == 0.25
        assert summary.frame_flag_share == 0.75

    def test_case_gaps_average_both_orders_per_case(self):
        gaps = case_gaps(
            [
                _run("a", True, challenger=50, defender=70),
                _run("a", False, challenger=40, defender=40),
                _run("b", True, challenger=60, defender=50),
            ]
        )
        assert gaps == {"a": 10, "b": -10}

    def test_order_effect_is_none_without_both_orders(self):
        assert summarize([_run(first=True)]).order_effect is None

    def test_summary_of_nothing_is_rejected(self):
        with pytest.raises(ValueError):
            summarize([])
