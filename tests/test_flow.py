"""AnalysisFlow: blind round 1, cross-check rounds, judge with one retry (SPEC §4)."""

from __future__ import annotations

import pytest

from debate_arena.application.flow import AnalysisFlow, FlowConfig
from debate_arena.domain.analysts import ANALYSTS
from debate_arena.domain.events import AnalysisEvent
from debate_arena.domain.exceptions import InvalidVerdict
from tests.fakes.llm import FakeProvider

NAMES = [a.name for a in ANALYSTS]


def _config(rounds: int = 2) -> FlowConfig:
    return FlowConfig(
        rounds=rounds,
        language="русский",
        analyst_model="fake/a",
        judge_model="fake/j",
        turn_max_tokens=100,
        judge_max_tokens=200,
        analyst_effort="medium",
        judge_effort="high",
        concurrency=3,
    )


async def _run(rounds: int = 2, provider: FakeProvider | None = None, judge=None):
    provider = provider or FakeProvider()
    judge = judge or provider
    events: list[AnalysisEvent] = []
    persisted: dict = {"turns": [], "verdict": None}

    async def emit(event: AnalysisEvent) -> None:
        events.append(event)

    async def persist_turn(round_no, analyst, text):
        persisted["turns"].append((round_no, analyst.name, text))

    async def persist_verdict(verdict):
        persisted["verdict"] = verdict

    flow = AnalysisFlow(
        text="Исходный текст.",
        question=None,
        analysts=ANALYSTS,
        analyst_provider=provider,
        judge_provider=judge,
        config=_config(rounds),
        emit=emit,
        persist_turn=persist_turn,
        persist_verdict=persist_verdict,
    )
    result = await flow.run()
    return result, events, persisted, provider


class TestRounds:
    async def test_round_one_is_blind_and_cross_rounds_see_transcript(self):
        _, _, _, provider = await _run(rounds=2)
        first = provider.calls[:3]
        second = provider.calls[3:]
        assert len(provider.calls) == 6
        for req in first:
            assert "Независимый разбор" in req.user
            assert "[Раунд 1]" not in req.user
        for req in second:
            assert "Перекрёстная проверка" in req.user
            assert "[Раунд 1]" in req.user
            assert "Принимаю:" in req.user

    async def test_each_analyst_gets_its_own_system_prompt_and_config(self):
        _, _, _, provider = await _run(rounds=1)
        systems = {req.system for req in provider.calls}
        assert systems == {a.system_prompt for a in ANALYSTS}
        for req in provider.calls:
            assert req.model == "fake/a"
            assert req.max_tokens == 100
            assert req.effort == "medium"

    async def test_cross_round_is_sequential_so_later_analysts_see_earlier_ones(self):
        _, _, _, provider = await _run(rounds=2)
        last_cross = provider.calls[-1].user
        # The last analyst of round 2 must see at least one round-2 reply of a colleague.
        assert "[Раунд 2]" in last_cross

    async def test_single_round_means_only_blind_analysis(self):
        _, events, _, provider = await _run(rounds=1)
        assert len(provider.calls) == 3
        assert [e.data["round_no"] for e in events if e.type == "round"] == [1]


class TestEvents:
    async def test_event_sequence(self):
        _, events, _, _ = await _run(rounds=2)
        types = [e.type for e in events]
        assert types[0] == "started"
        assert types[-1] == "verdict"
        assert types.count("round") == 2
        assert types.count("turn") == 6
        assert types.count("typing") == 7  # 6 analyst turns + judge
        assert "error" not in types and "done" not in types  # service's job, not the flow's

    async def test_started_lists_analysts_and_rounds(self):
        _, events, _, _ = await _run(rounds=2)
        started = events[0].data
        assert started["rounds"] == 2
        assert [a["name"] for a in started["analysts"]] == NAMES
        assert all(a["emoji"] and a["focus"] for a in started["analysts"])

    async def test_turn_events_carry_text_round_and_emoji(self):
        _, events, _, _ = await _run(rounds=1)
        turns = [e.data for e in events if e.type == "turn"]
        assert {t["analyst"] for t in turns} == set(NAMES)
        assert all(t["round_no"] == 1 and t["text"] and t["emoji"] for t in turns)

    async def test_round_kinds(self):
        _, events, _, _ = await _run(rounds=3)
        kinds = [e.data["kind"] for e in events if e.type == "round"]
        assert kinds[0] == "Независимый разбор"
        assert kinds[1] == kinds[2] == "Перекрёстная проверка"


class TestJudge:
    async def test_judge_gets_transcript_and_all_names(self):
        _, _, _, provider = await _run(rounds=1)
        assert len(provider.structured_calls) == 1
        req = provider.structured_calls[0]
        assert req.model == "fake/j" and req.max_tokens == 200 and req.effort == "high"
        assert "[Раунд 1]" in req.user
        assert ", ".join(NAMES) in req.user

    async def test_invalid_verdict_is_retried_once(self):
        judge = FakeProvider(fail_structured_times=1)
        result, events, persisted, _ = await _run(rounds=1, judge=judge)
        assert len(judge.structured_calls) == 2
        assert result.verdict.most_rational in NAMES
        assert persisted["verdict"] is result.verdict
        assert events[-1].type == "verdict"

    async def test_two_invalid_verdicts_fail_the_flow(self):
        judge = FakeProvider(fail_structured_times=2)
        with pytest.raises(InvalidVerdict):
            await _run(rounds=1, judge=judge)

    async def test_verdict_with_wrong_names_is_invalid(self):
        from tests.fakes.llm import make_verdict

        judge = FakeProvider(verdict=make_verdict(["Кто-то", "Ещё кто-то", "Третий"]))
        with pytest.raises(InvalidVerdict):
            await _run(rounds=1, judge=judge)


class TestPersistenceAndStats:
    async def test_turns_and_verdict_are_persisted_in_order(self):
        result, _, persisted, _ = await _run(rounds=2)
        assert len(persisted["turns"]) == 6
        assert [t[0] for t in persisted["turns"]] == [1, 1, 1, 2, 2, 2]
        assert persisted["verdict"] is result.verdict

    async def test_result_counts_calls_and_tokens(self):
        result, _, _, _ = await _run(rounds=2)
        assert result.calls == 7
        assert result.usage.input_tokens == 6 * 10  # FakeProvider: 10 in / 5 out per prose call
        assert result.usage.output_tokens == 6 * 5
