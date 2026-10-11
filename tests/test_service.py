"""AnalysisService: background runs, event replay, DB restore, failures (SPEC §3.4, §6)."""

from __future__ import annotations

import pytest

from debate_arena.application.service import AnalysisService
from debate_arena.config import Settings
from debate_arena.domain.exceptions import AnalysisNotFound, AnalysisRunning, ProviderRefused
from debate_arena.domain.ports import ChatRequest, Completion, Usage
from tests.fakes.llm import FakeProvider
from tests.fakes.repo import InMemoryRepository


def _settings(**overrides) -> Settings:
    base = dict(anthropic_api_key="", deepseek_api_key="", mock_llm=True)
    return Settings(_env_file=None, **(base | overrides))


def _service(repo=None, provider=None, settings=None) -> AnalysisService:
    provider = provider or FakeProvider()
    return AnalysisService(
        repo=repo or InMemoryRepository(),
        provider_factory=lambda model: provider,
        settings=settings or _settings(),
    )


async def _collect(service: AnalysisService, analysis_id: int) -> list:
    return [event async for event in service.subscribe(analysis_id)]


class TestStartAndRun:
    async def test_start_returns_id_and_finishes_in_background(self):
        service = _service()
        analysis_id = await service.start(text="Текст.", question=None, rounds=1, model=None)
        assert analysis_id == 1
        await service.wait(analysis_id)
        record = await service.get(analysis_id)
        assert record.status == "finished"
        assert len(record.turns) == 3
        assert record.verdict is not None
        assert record.duration_ms is not None

    async def test_title_is_first_chars_of_text_collapsed(self):
        service = _service(settings=_settings(title_chars=12))
        analysis_id = await service.start(
            text="Первая   строка\nвторая строка и ещё", question=None, rounds=1, model=None
        )
        record = await service.get(analysis_id)
        assert record.title == "Первая стро…"

    async def test_rounds_out_of_range_rejected(self):
        service = _service()
        with pytest.raises(ValueError):
            await service.start(text="t", question=None, rounds=0, model=None)
        with pytest.raises(ValueError):
            await service.start(text="t", question=None, rounds=99, model=None)

    async def test_text_length_rejected(self):
        service = _service(settings=_settings(max_text_chars=5))
        with pytest.raises(ValueError):
            await service.start(text="слишком длинно", question=None, rounds=1, model=None)
        with pytest.raises(ValueError):
            await service.start(text="   ", question=None, rounds=1, model=None)

    async def test_long_text_reaches_every_call_whole(self):
        # SPEC §6 №2: a text within MAX_TEXT_CHARS is never cut on the way to the models.
        provider = FakeProvider()
        service = _service(provider=provider)
        text = "Абзац длинного диалога. " * 7_000 + "ПОСЛЕДНЯЯ ФРАЗА."
        assert 150_000 < len(text) <= 200_000
        analysis_id = await service.start(text=text, question=None, rounds=2, model=None)
        await service.wait(analysis_id)
        assert (await service.get(analysis_id)).source_text == text
        requests = provider.calls + provider.structured_calls
        assert len(requests) == 7
        assert all(text in r.context for r in requests)

    async def test_explicit_model_is_used_for_analysts_and_judge(self):
        seen: list[str] = []
        provider = FakeProvider()

        def factory(model: str):
            seen.append(model)
            return provider

        repo = InMemoryRepository()
        service = AnalysisService(repo=repo, provider_factory=factory, settings=_settings())
        analysis_id = await service.start(text="t", question=None, rounds=1, model="mock/rational")
        await service.wait(analysis_id)
        assert seen == ["mock/rational"]
        assert repo.rows[analysis_id].model_analyst == "mock/rational"

    async def test_unknown_model_rejected(self):
        service = _service()
        with pytest.raises(ValueError):
            await service.start(text="t", question=None, rounds=1, model="openai/gpt")
        with pytest.raises(ValueError):  # well-formed but not in available_models (SPEC §6 №6)
            await service.start(
                text="t", question=None, rounds=1, model="anthropic/claude-opus-5-5"
            )

    async def test_same_model_reuses_one_provider_for_analysts_and_judge(self):
        built: list[str] = []

        def factory(model: str):
            built.append(model)
            return FakeProvider()

        service = AnalysisService(
            repo=InMemoryRepository(), provider_factory=factory, settings=_settings()
        )
        analysis_id = await service.start(text="t", question=None, rounds=1, model=None)
        await service.wait(analysis_id)
        assert built == ["mock/rational"]

    async def test_shutdown_cancels_running_and_marks_interrupted(self):
        import asyncio

        class Slow(FakeProvider):
            async def complete(self, request):
                await asyncio.sleep(10)
                return await super().complete(request)

        repo = InMemoryRepository()
        service = _service(repo=repo, provider=Slow())
        analysis_id = await service.start(text="t", question=None, rounds=1, model=None)
        await asyncio.sleep(0.05)
        await service.shutdown()
        record = await service.get(analysis_id)
        assert record.status == "failed" and record.error == "interrupted"
        events = await _collect(service, analysis_id)
        assert events[-1].type == "done" and events[-1].data["status"] == "failed"


class TestDialogueMode:
    DIALOGUE = "Иван: Земля плоская.\nМария: Нет, есть фото из космоса.\nИван: Они подделаны."

    async def test_auto_detects_dialogue_and_persists_participants(self):
        repo = InMemoryRepository()
        service = _service(repo=repo)
        analysis_id = await service.start(
            text=self.DIALOGUE, question=None, rounds=1, model=None, mode="auto"
        )
        await service.wait(analysis_id)
        record = await service.get(analysis_id)
        assert record.mode == "dialogue"
        assert record.participants == ["Собеседник А", "Собеседник Б"]  # anonymised by default
        assert [p.name for p in record.verdict.participants] == record.participants
        assert record.verdict.closest_to_truth in record.participants
        events = await _collect(service, analysis_id)
        assert events[0].data["mode"] == "dialogue"

    async def test_forced_text_mode_ignores_speakers(self):
        service = _service()
        analysis_id = await service.start(
            text=self.DIALOGUE, question=None, rounds=1, model=None, mode="text"
        )
        await service.wait(analysis_id)
        record = await service.get(analysis_id)
        assert record.mode == "text" and record.participants == []
        assert record.verdict.participants == []

    async def test_forced_dialogue_without_speakers_is_rejected(self):
        service = _service()
        with pytest.raises(ValueError, match="Имя: реплика"):
            await service.start(
                text="монолог", question=None, rounds=1, model=None, mode="dialogue"
            )

    async def test_dialogue_is_anonymized_before_models_and_storage(self):
        provider = FakeProvider()
        repo = InMemoryRepository()
        service = _service(repo=repo, provider=provider)
        started = await service.start_analysis(
            text="Иван: Мария, ты не права.\nМария: Иван, один случай — не статистика.",
            question=None,
            rounds=1,
            model=None,
            mode="auto",
        )
        assert started.legend == {"Собеседник А": "Иван", "Собеседник Б": "Мария"}
        await service.wait(started.id)
        record = await service.get(started.id)
        assert record.participants == ["Собеседник А", "Собеседник Б"]
        assert "Иван" not in record.source_text and "Мария" not in record.source_text
        assert "Иван" not in record.title
        for req in provider.calls + provider.structured_calls:
            prompt = req.context + req.user
            assert "Иван" not in prompt and "Мария" not in prompt
        assert [p.name for p in record.verdict.participants] == record.participants

    async def test_anonymization_can_be_disabled(self):
        repo = InMemoryRepository()
        service = _service(repo=repo, settings=_settings(anonymize_participants=False))
        started = await service.start_analysis(
            text="Иван: а\nМария: б", question=None, rounds=1, model=None, mode="auto"
        )
        assert started.legend == {}
        assert (await service.get(started.id)).participants == ["Иван", "Мария"]

    async def test_text_mode_has_empty_legend(self):
        service = _service()
        started = await service.start_analysis(
            text="просто текст", question=None, rounds=1, model=None, mode="auto"
        )
        assert started.legend == {}
        await service.wait(started.id)

    async def test_restored_events_carry_mode(self):
        repo = InMemoryRepository()
        service = _service(repo=repo)
        analysis_id = await service.start(text=self.DIALOGUE, question=None, rounds=1, model=None)
        await service.wait(analysis_id)
        events = await _collect(_service(repo=repo), analysis_id)
        assert events[0].data["mode"] == "dialogue"
        assert events[0].data["participants"] == ["Собеседник А", "Собеседник Б"]


class TestEvents:
    async def test_subscribe_streams_until_done(self):
        service = _service()
        analysis_id = await service.start(text="t", question=None, rounds=1, model=None)
        events = await _collect(service, analysis_id)
        types = [e.type for e in events]
        assert types[0] == "started" and types[-1] == "done"
        assert "verdict" in types
        assert events[-1].data["status"] == "finished"
        assert events[-1].data["calls"] == 4

    async def test_replay_after_finish_from_memory(self):
        service = _service()
        analysis_id = await service.start(text="t", question=None, rounds=1, model=None)
        await service.wait(analysis_id)
        events = await _collect(service, analysis_id)
        assert [e.type for e in events].count("turn") == 3
        assert events[-1].type == "done"

    async def test_restore_from_repository_after_restart(self):
        repo = InMemoryRepository()
        service = _service(repo=repo)
        analysis_id = await service.start(text="t", question=None, rounds=1, model=None)
        await service.wait(analysis_id)

        fresh = _service(repo=repo)  # new process: nothing in memory
        events = await _collect(fresh, analysis_id)
        types = [e.type for e in events]
        assert types[0] == "started"
        assert types.count("turn") == 3
        assert "verdict" in types
        assert types[-1] == "done"

    async def test_subscribe_unknown_raises(self):
        service = _service()
        with pytest.raises(AnalysisNotFound):
            await _collect(service, 404)


class _RefusingProvider(FakeProvider):
    async def complete(self, request: ChatRequest) -> Completion:
        if len(self.calls) >= 2:
            raise ProviderRefused("Модель отказалась", category="x")
        self.calls.append(request)
        return Completion(text="ok", usage=Usage())


class TestFailures:
    async def test_provider_refusal_marks_failed_and_keeps_turns(self):
        repo = InMemoryRepository()
        service = _service(repo=repo, provider=_RefusingProvider())
        analysis_id = await service.start(text="t", question=None, rounds=1, model=None)
        events = await _collect(service, analysis_id)
        types = [e.type for e in events]
        assert "error" in types and types[-1] == "done"
        assert events[-1].data["status"] == "failed"
        record = await service.get(analysis_id)
        assert record.status == "failed"
        assert "отказалась" in record.error
        assert len(record.turns) == 2  # the two replies before the refusal are kept

    async def test_invalid_verdict_twice_fails(self):
        service = _service(provider=FakeProvider(fail_structured_times=2))
        analysis_id = await service.start(text="t", question=None, rounds=1, model=None)
        await service.wait(analysis_id)
        record = await service.get(analysis_id)
        assert record.status == "failed"
        assert "вердикт" in record.error.lower()


class TestCrud:
    async def test_list_and_delete(self):
        service = _service()
        first = await service.start(text="один", question=None, rounds=1, model=None)
        second = await service.start(text="два", question=None, rounds=1, model=None)
        await service.wait(first)
        await service.wait(second)
        summaries = await service.list(limit=10)
        assert [s.id for s in summaries] == [second, first]
        assert summaries[0].rationality_score is not None
        assert await service.delete(first) is True
        with pytest.raises(AnalysisNotFound):
            await service.get(first)

    async def test_delete_running_is_refused(self):
        service = _service()
        analysis_id = await service.start(text="t", question=None, rounds=1, model=None)
        with pytest.raises(AnalysisRunning):
            await service.delete(analysis_id)
        await service.wait(analysis_id)

    async def test_recover_interrupted_marks_running_rows_failed(self):
        repo = InMemoryRepository()
        await repo.create(
            source_text="t", question=None, rounds=1, model_analyst="m", model_judge="m", title="t"
        )
        service = _service(repo=repo)
        assert await service.recover_interrupted() == 1
        record = await service.get(1)
        assert record.status == "failed" and record.error == "interrupted"
