"""Verdict schema invariants and tolerant JSON parsing (SPEC §3.3, ADR-001)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from debate_arena.domain.exceptions import InvalidVerdict
from debate_arena.domain.schemas import ClaimAssessment, ParticipantAssessment, Verdict
from debate_arena.domain.verdict import parse_verdict_text
from tests.fakes.llm import make_verdict

PEOPLE = ["Иван", "Мария"]


class TestSchema:
    def test_rejects_out_of_range_numbers(self):
        with pytest.raises(ValidationError):
            Verdict.model_validate(make_verdict().model_dump() | {"rationality_score": 101})
        with pytest.raises(ValidationError):
            ParticipantAssessment(
                name="A",
                truth_proximity=101,
                position="p",
                right_about="r",
                wrong_about="",
                note="",
            )

    def test_claim_status_is_closed_set_and_by_is_optional(self):
        ClaimAssessment(claim="c", status="unfalsifiable", note="n")
        ClaimAssessment(claim="c", status="supported", note="n", by="Иван")
        with pytest.raises(ValidationError):
            ClaimAssessment(claim="c", status="maybe", note="n")

    def test_conclusion_must_not_be_blank(self):
        with pytest.raises(ValidationError):
            Verdict.model_validate(make_verdict().model_dump() | {"conclusion": "   "})

    def test_narrative_defaults_to_empty_for_old_rows(self):
        payload = make_verdict().model_dump()
        payload.pop("narrative", None)
        assert Verdict.model_validate(payload).narrative == ""
        assert "narrative" in Verdict.model_json_schema()["properties"]

    def test_no_analyst_scoring_fields(self):
        fields = set(Verdict.model_fields)
        assert "scores" not in fields and "most_rational" not in fields
        assert {"consensus", "open_questions", "participants", "closest_to_truth"} <= fields


class TestInvariants:
    """SPEC §3.3: the mode fixes the shape of ``participants``; the names are the judge's."""

    def test_text_mode_requires_no_participants(self):
        make_verdict().validate_for("text")
        with pytest.raises(InvalidVerdict):
            make_verdict(PEOPLE).validate_for("text")

    def test_dialogue_mode_requires_two_distinct_participants(self):
        make_verdict(PEOPLE).validate_for("dialogue")
        make_verdict(PEOPLE + ["Пётр"]).validate_for("dialogue")
        with pytest.raises(InvalidVerdict):
            make_verdict().validate_for("dialogue")
        with pytest.raises(InvalidVerdict):
            make_verdict(PEOPLE[:1]).validate_for("dialogue")
        with pytest.raises(InvalidVerdict):
            make_verdict(PEOPLE + ["иван"]).validate_for("dialogue")

    def test_auto_mode_accepts_text_or_dialogue_shape(self):
        make_verdict().validate_for("auto")
        make_verdict(PEOPLE).validate_for("auto")
        with pytest.raises(InvalidVerdict):
            make_verdict(PEOPLE[:1]).validate_for("auto")

    def test_closest_to_truth_must_be_a_participant(self):
        with pytest.raises(InvalidVerdict):
            make_verdict(PEOPLE, closest="Кто-то").validate_for("dialogue")
        with pytest.raises(InvalidVerdict):
            make_verdict(PEOPLE, closest=None).validate_for("auto")
        with pytest.raises(InvalidVerdict):
            Verdict.model_validate(
                make_verdict().model_dump() | {"closest_to_truth": "Иван"}
            ).validate_for("text")

    def test_participant_lookup(self):
        verdict = make_verdict(PEOPLE)
        assert verdict.participant("Мария").name == "Мария"
        assert verdict.participant("Нет") is None


class TestParseVerdictText:
    def _payload(self) -> str:
        return make_verdict(PEOPLE).model_dump_json()

    def test_parses_clean_json(self):
        assert parse_verdict_text(self._payload()).conclusion == "Тестовое заключение"

    def test_parses_fenced_json_with_prose(self):
        raw = f"Вот вердикт:\n```json\n{self._payload()}\n```\nГотово."
        assert parse_verdict_text(raw).confidence == 70

    def test_strips_think_blocks(self):
        raw = f"<think>долго думал</think>{self._payload()}"
        assert parse_verdict_text(raw).rationality_score == 61

    def test_garbage_raises_invalid_verdict(self):
        with pytest.raises(InvalidVerdict):
            parse_verdict_text("никакого json здесь нет")

    def test_schema_violation_raises_invalid_verdict(self):
        broken = json.loads(self._payload())
        broken["rationality_score"] = 500
        with pytest.raises(InvalidVerdict):
            parse_verdict_text(json.dumps(broken))
