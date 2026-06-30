"""One short end-to-end debate on the mock engine, persisted to a temp SQLite DB."""

from __future__ import annotations

import pytest

from debate_arena.debate.engine import MockTurnEngine
from debate_arena.debate.flow import DebateFlow, DebateStream
from debate_arena.debate.schemas import PersonaSpec, Verdict
from debate_arena.storage import repo
from debate_arena.storage.db import init_db


class RecordingStream(DebateStream):
    def __init__(self):
        self.turns = []
        self.verdict = None
        self.rounds = []

    async def on_round_header(self, round_no, kind):
        self.rounds.append((round_no, kind))

    async def on_turn(self, round_no, persona, text):
        self.turns.append((round_no, persona.name, text))

    async def on_verdict(self, verdict, personas):
        self.verdict = verdict


@pytest.fixture(scope="module", autouse=True)
async def _db():
    await init_db()


async def test_full_debate_persists_and_scores():
    personas = await repo.get_active_personas(3)
    assert len(personas) == 3

    debate_id = await repo.create_debate(chat_id=-100, topic="Тест темы")
    stream = RecordingStream()

    async def persist_turn(round_no, persona: PersonaSpec, text):
        await repo.add_message(debate_id, round_no, persona, text)

    async def persist_verdict(verdict: Verdict):
        await repo.save_verdict(debate_id, verdict)

    flow = DebateFlow(
        topic="Тест темы",
        personas=personas,
        engine=MockTurnEngine(),
        stream=stream,
        persist_turn=persist_turn,
        persist_verdict=persist_verdict,
        max_rounds=2,
        pacing=False,
    )
    verdict = await flow.run()

    # 3 personas * 2 rounds = 6 turns streamed and stored.
    assert len(stream.turns) == 6
    assert stream.rounds == [(1, "Тезисы"), (2, "Контраргументы")]
    assert verdict.winner in {p.name for p in personas}
    assert stream.verdict is verdict

    # Leaderboard moved: every persona played one debate, winner has a win.
    board = await repo.get_leaderboard()
    by_name = {p.name: p for p in board}
    for p in personas:
        assert by_name[p.name].debates == 1
    assert by_name[verdict.winner].wins == 1


async def test_personas_react_to_transcript_in_later_rounds():
    """Round-2 mock turns differ from round-1 — i.e. the transcript drives the turn."""
    engine = MockTurnEngine()
    personas = await repo.get_active_personas(3)
    p = personas[0]
    r1 = await engine.persona_turn(p, "тема", "", 1)
    r2 = await engine.persona_turn(p, "тема", "что-то сказали оппоненты", 2)
    assert r1 != r2
