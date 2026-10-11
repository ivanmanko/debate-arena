"""Verdict schema invariants and tolerant JSON parsing (SPEC §3.3, ADR-001)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from debate_arena.domain.exceptions import InvalidVerdict
from debate_arena.domain.schemas import AnalystScore, ClaimAssessment, Verdict
from debate_arena.domain.verdict import parse_verdict_text
from tests.fakes.llm import make_verdict

NAMES = ["Эмпирик", "Логик", "Стилмен"]


class TestSchema:
    def test_score_total(self):
        s = AnalystScore(
            analyst="A", rigor=10, evidence=10, charity=10, responsiveness=10, note="x"
        )
        assert s.total == 40

    def test_rejects_out_of_range_numbers(self):
        with pytest.raises(ValidationError):
            Verdict.model_validate(make_verdict(NAMES).model_dump() | {"rationality_score": 101})
        with pytest.raises(ValidationError):
            AnalystScore(analyst="A", rigor=11, evidence=5, charity=5, responsiveness=5, note="")

    def test_claim_status_is_closed_set(self):
        ClaimAssessment(claim="c", status="unfalsifiable", note="n")
        with pytest.raises(ValidationError):
            ClaimAssessment(claim="c", status="maybe", note="n")

    def test_conclusion_must_not_be_blank(self):
        with pytest.raises(ValidationError):
            Verdict.model_validate(make_verdict(NAMES).model_dump() | {"conclusion": "   "})


class TestInvariants:
    def test_valid_verdict_passes(self):
        make_verdict(NAMES).validate_for(NAMES)

    def test_missing_analyst_score_is_invalid(self):
        verdict = make_verdict(NAMES[:2])
        with pytest.raises(InvalidVerdict):
            verdict.validate_for(NAMES)

    def test_duplicate_scores_are_invalid(self):
        verdict = make_verdict(NAMES + [NAMES[0]])
        with pytest.raises(InvalidVerdict):
            verdict.validate_for(NAMES)

    def test_unknown_most_rational_is_invalid(self):
        verdict = make_verdict(NAMES, most_rational="Кто-то")
        with pytest.raises(InvalidVerdict):
            verdict.validate_for(NAMES)


class TestParseVerdictText:
    def _payload(self) -> str:
        return make_verdict(NAMES).model_dump_json()

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
