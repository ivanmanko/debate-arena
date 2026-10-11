"""SqlAlchemyAnalysisRepository against a throwaway SQLite file (SPEC §2)."""

from __future__ import annotations

import os
import tempfile

import pytest

from debate_arena.infrastructure.db.repo import SqlAlchemyAnalysisRepository
from debate_arena.infrastructure.db.session import Database
from tests.fakes.llm import make_verdict


@pytest.fixture
async def repo():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.init()
    yield SqlAlchemyAnalysisRepository(db.sessions)
    await db.dispose()
    os.unlink(path)


async def _create(repo, text="Текст", title="Текст", participants=None) -> int:
    return await repo.create(
        source_text=text,
        question="вопрос",
        mode="dialogue" if participants else "text",
        participants=list(participants or []),
        rounds=2,
        model_analyst="mock/rational",
        model_judge="mock/rational",
        title=title,
    )


async def test_round_trip_with_turns_and_verdict(repo):
    analysis_id = await _create(repo)
    await repo.add_turn(analysis_id, 1, "Эмпирик", "🔎", "первая")
    await repo.add_turn(analysis_id, 2, "Логик", "🧩", "вторая")
    verdict = make_verdict()
    await repo.save_verdict(analysis_id, verdict)
    await repo.finish(analysis_id, "finished", None, 1234)

    record = await repo.get(analysis_id)
    assert record.status == "finished" and record.duration_ms == 1234
    assert record.finished_at is not None
    assert record.question == "вопрос"
    assert [(t.round_no, t.analyst, t.text) for t in record.turns] == [
        (1, "Эмпирик", "первая"),
        (2, "Логик", "вторая"),
    ]
    assert record.verdict == verdict
    assert record.mode == "text" and record.participants == []


async def test_dialogue_participants_round_trip(repo):
    analysis_id = await _create(repo, participants=["Иван", "Мария"])
    await repo.save_verdict(analysis_id, make_verdict(["Иван", "Мария"]))
    record = await repo.get(analysis_id)
    assert record.mode == "dialogue" and record.participants == ["Иван", "Мария"]
    assert record.verdict.closest_to_truth == "Иван"
    assert (await repo.list(limit=1))[0].mode == "dialogue"


async def test_get_missing_returns_none(repo):
    assert await repo.get(999) is None


async def test_list_is_newest_first_with_score(repo):
    first = await _create(repo, title="a")
    second = await _create(repo, title="b")
    await repo.save_verdict(second, make_verdict())
    summaries = await repo.list(limit=10)
    assert [s.id for s in summaries] == [second, first]
    assert summaries[0].rationality_score == 61
    assert summaries[1].rationality_score is None
    assert len(await repo.list(limit=1)) == 1


async def test_chat_messages_round_trip_in_order(repo):
    analysis_id = await _create(repo)
    assert (await repo.get(analysis_id)).chat == []
    question = await repo.add_chat_message(analysis_id, "user", "Почему?")
    reply = await repo.add_chat_message(analysis_id, "judge", "Потому что.")
    assert (question.role, question.text) == ("user", "Почему?")
    assert reply.created_at.tzinfo is not None
    record = await repo.get(analysis_id)
    assert [(m.role, m.text) for m in record.chat] == [
        ("user", "Почему?"),
        ("judge", "Потому что."),
    ]
    assert record.chat[0].created_at.tzinfo is not None


async def test_delete_cascades(repo):
    analysis_id = await _create(repo)
    await repo.add_turn(analysis_id, 1, "Эмпирик", "🔎", "x")
    await repo.save_verdict(analysis_id, make_verdict())
    await repo.add_chat_message(analysis_id, "user", "q")
    assert await repo.delete(analysis_id) is True
    assert await repo.get(analysis_id) is None
    assert await repo.delete(analysis_id) is False


async def test_mark_interrupted_only_touches_running(repo):
    running = await _create(repo)
    done = await _create(repo)
    await repo.finish(done, "finished", None, 1)
    assert await repo.mark_interrupted() == 1
    assert (await repo.get(running)).error == "interrupted"
    assert (await repo.get(done)).status == "finished"


async def test_datetimes_are_timezone_aware_from_sqlite(repo):
    analysis_id = await _create(repo)
    await repo.add_turn(analysis_id, 1, "Эмпирик", "🔎", "x")
    await repo.finish(analysis_id, "finished", None, 1)
    record = await repo.get(analysis_id)
    assert record.created_at.tzinfo is not None
    assert record.finished_at.tzinfo is not None
    assert record.turns[0].created_at.tzinfo is not None
    assert (await repo.list(limit=1))[0].created_at.tzinfo is not None
