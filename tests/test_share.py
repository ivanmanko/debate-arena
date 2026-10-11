"""Publishing an analysis as a public snapshot (SPEC §3.6a, §4.7, §6 №18–21, ADR-006)."""

from __future__ import annotations

import re

import pytest

from debate_arena.application.share import ShareService
from debate_arena.domain.analysts import build_analysts
from debate_arena.domain.exceptions import (
    AnalysisNotFound,
    PublishError,
    ShareNotFound,
    ShareUnavailable,
)
from debate_arena.domain.schemas import ClaimAssessment, Fallacy
from debate_arena.interface.share.page import render_snapshot
from tests.fakes.llm import make_verdict
from tests.fakes.repo import InMemoryRepository
from tests.fakes.share import FakePublisher

ANALYSTS = build_analysts("русский")
DIALOGUE = ["Собеседник А", "Собеседник Б"]


async def _analysis(
    repo: InMemoryRepository,
    *,
    status: str = "finished",
    participants: list[str] | None = None,
    verdict: bool = True,
    source_text: str = "Исходный материал про воду.",
) -> int:
    names = list(participants or [])
    analysis_id = await repo.create(
        source_text=source_text,
        question="верен ли вывод?",
        rounds=2,
        model_analyst="anthropic/claude-opus-5-5",
        model_judge="anthropic/claude-opus-5-5",
        title="Исходный материал",
        mode="dialogue" if names else "text",
        participants=names,
    )
    await repo.add_turn(analysis_id, 1, "Эмпирик", "🔎", "Реплика эмпирика про **факты**.")
    await repo.add_turn(
        analysis_id, 2, "Логик", "🧩", "Принимаю: довод Эмпирика.\n\nОспариваю: вывод о воде."
    )
    if verdict:
        await repo.save_verdict(analysis_id, make_verdict(names))
    if status != "running":
        await repo.finish(analysis_id, status, None if status == "finished" else "boom", 10)
    return analysis_id


def _service(repo, publisher, token_bytes: int = 16) -> ShareService:
    return ShareService(
        repo=repo,
        publisher=publisher,
        render=lambda record: render_snapshot(record, ANALYSTS),
        token_bytes=token_bytes,
    )


class TestPublish:
    async def test_publishes_snapshot_and_stores_share(self):
        repo, publisher = InMemoryRepository(), FakePublisher()
        analysis_id = await _analysis(repo)
        share = await _service(repo, publisher).publish(analysis_id)
        assert share.url == f"https://snapshots.test/{share.key}.html"
        assert publisher.objects[share.key].startswith("<!doctype html>")
        assert (await repo.get(analysis_id)).share == share

    async def test_key_is_random_urlsafe_and_not_the_id(self):
        repo, publisher = InMemoryRepository(), FakePublisher()
        first = await _service(repo, publisher).publish(await _analysis(repo))
        second = await _service(repo, publisher).publish(await _analysis(repo))
        for share in (first, second):
            assert re.fullmatch(r"[A-Za-z0-9_-]{22}", share.key)  # 16 bytes → 22 chars
        assert first.key != second.key

    async def test_republish_returns_same_link_without_upload(self):
        repo, publisher = InMemoryRepository(), FakePublisher()
        analysis_id = await _analysis(repo)
        service = _service(repo, publisher)
        first = await service.publish(analysis_id)
        second = await service.publish(analysis_id)
        assert second == first
        assert [op for op, _ in publisher.calls] == ["publish"]

    @pytest.mark.parametrize(
        ("status", "verdict"), [("running", False), ("failed", False), ("failed", True)]
    )
    async def test_only_finished_with_verdict(self, status, verdict):
        repo, publisher = InMemoryRepository(), FakePublisher()
        analysis_id = await _analysis(repo, status=status, verdict=verdict)
        with pytest.raises(ShareUnavailable):
            await _service(repo, publisher).publish(analysis_id)
        assert publisher.calls == []

    async def test_missing_analysis(self):
        with pytest.raises(AnalysisNotFound):
            await _service(InMemoryRepository(), FakePublisher()).publish(42)

    async def test_storage_failure_saves_nothing(self):
        repo, publisher = InMemoryRepository(), FakePublisher()
        analysis_id = await _analysis(repo)
        publisher.fail = True
        with pytest.raises(PublishError):
            await _service(repo, publisher).publish(analysis_id)
        assert (await repo.get(analysis_id)).share is None

    async def test_failed_save_removes_uploaded_object(self):
        repo, publisher = InMemoryRepository(), FakePublisher()
        analysis_id = await _analysis(repo)

        async def broken_save(*args, **kwargs):
            raise RuntimeError("db is down")

        repo.save_share = broken_save
        with pytest.raises(RuntimeError):
            await _service(repo, publisher).publish(analysis_id)
        assert publisher.objects == {}
        assert [op for op, _ in publisher.calls] == ["publish", "unpublish"]


class TestWithdraw:
    async def test_removes_object_and_record(self):
        repo, publisher = InMemoryRepository(), FakePublisher()
        analysis_id = await _analysis(repo)
        service = _service(repo, publisher)
        share = await service.publish(analysis_id)
        await service.withdraw(analysis_id)
        assert share.key not in publisher.objects
        assert (await repo.get(analysis_id)).share is None

    async def test_not_shared(self):
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        with pytest.raises(ShareNotFound):
            await _service(repo, FakePublisher()).withdraw(analysis_id)

    async def test_missing_analysis(self):
        with pytest.raises(AnalysisNotFound):
            await _service(InMemoryRepository(), FakePublisher()).withdraw(42)

    async def test_storage_failure_keeps_record(self):
        repo, publisher = InMemoryRepository(), FakePublisher()
        analysis_id = await _analysis(repo)
        service = _service(repo, publisher)
        share = await service.publish(analysis_id)
        publisher.fail = True
        with pytest.raises(PublishError):
            await service.withdraw(analysis_id)
        assert (await repo.get(analysis_id)).share == share

    async def test_withdraw_if_shared_is_quiet_for_unshared_and_missing(self):
        repo, publisher = InMemoryRepository(), FakePublisher()
        analysis_id = await _analysis(repo)
        service = _service(repo, publisher)
        await service.withdraw_if_shared(analysis_id)
        await service.withdraw_if_shared(42)
        assert publisher.calls == []


class TestSnapshotPage:
    async def _record(self, **kwargs):
        repo = InMemoryRepository()
        return await repo.get(await _analysis(repo, **kwargs))

    async def test_self_contained_and_not_indexed(self):
        page = render_snapshot(await self._record(), ANALYSTS)
        assert page.startswith("<!doctype html>")
        assert '<meta name="robots" content="noindex' in page
        assert "<script" not in page
        assert not re.search(r"""(src|href)=["']?(https?:)?//""", page)  # no external resources

    async def test_contains_verdict_material_and_transcript(self):
        record = await self._record()
        verdict = record.verdict.model_copy(
            update={
                "claims": [ClaimAssessment(claim="вода мокрая", status="supported", note="опыт")],
                "fallacies": [Fallacy(name="Подмена тезиса", quote="цитата", explanation="так")],
            }
        )
        page = render_snapshot(record.model_copy(update={"verdict": verdict}), ANALYSTS)
        for text in (
            "Тестовый вопрос",
            "Тестовое заключение",
            "Абзац один обоснования.",
            "Абзац два обоснования.",
            "факт 1",
            "гипотеза 1",
            "белое пятно",
            "вода мокрая",
            "Подмена тезиса",
            "Сильнейшая версия",
            "Появление данных X",
            "Практический вывод",
            "аналитики сошлись на X",
            "открыт вопрос Y",
            "Исходный материал про воду.",
            "верен ли вывод?",
            "Эмпирик",
            "<b>факты</b>",
            "довод Эмпирика.",
            "Раунд 2",
        ):
            assert text in page, text

    async def test_dialogue_shows_labels_and_proximity(self):
        page = render_snapshot(await self._record(participants=DIALOGUE), ANALYSTS)
        assert "Собеседник А" in page and "Собеседник Б" in page
        assert "Собеседник А прав в …" in page
        assert "Ближе всего к истине" in page

    async def test_escapes_html_in_every_text(self):
        attack = "<script>alert(1)</script>"
        record = await self._record(source_text=f"Текст {attack}")
        record.turns[0].text = f"Реплика {attack}"
        verdict = record.verdict.model_copy(update={"conclusion": attack, "narrative": attack})
        page = render_snapshot(record.model_copy(update={"verdict": verdict}), ANALYSTS)
        assert "<script" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page

    async def test_chat_is_not_published(self):
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        await repo.add_chat_exchange(analysis_id, "личный вопрос судье", "личный ответ судьи")
        page = render_snapshot(await repo.get(analysis_id), ANALYSTS)
        assert "личный вопрос судье" not in page
        assert "личный ответ судьи" not in page
