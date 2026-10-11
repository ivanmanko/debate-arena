"""Judge chat: discussing the verdict with the judge (SPEC §3.5, §4.6, §6 №14–17, ADR-005)."""

from __future__ import annotations

import asyncio

import pytest

from debate_arena.application.chat import JudgeChatService
from debate_arena.config import Settings
from debate_arena.domain.exceptions import (
    AnalysisNotFound,
    ChatUnavailable,
    ProviderError,
    ProviderRefused,
)
from debate_arena.domain.names import anonymize_mentions
from debate_arena.domain.ports import ChatRequest, Completion, Usage
from debate_arena.domain.prompts import (
    JUDGE_CHAT_MARKER,
    MANIFEST,
    judge_chat_context,
    judge_chat_system_prompt,
    judge_chat_user,
)
from debate_arena.infrastructure.llm.mock import MockProvider
from tests.fakes.llm import FakeProvider, make_verdict
from tests.fakes.repo import InMemoryRepository

LANG = "русский"
DIALOGUE = ["Собеседник А", "Собеседник Б"]
LEGEND = {"Собеседник А": "Иван", "Собеседник Б": "Мария"}


def _settings(**overrides) -> Settings:
    base = dict(anthropic_api_key="", deepseek_api_key="", mock_llm=True)
    return Settings(_env_file=None, **(base | overrides))


async def _analysis(
    repo: InMemoryRepository,
    *,
    status: str = "finished",
    participants: list[str] | None = None,
    verdict: bool = True,
    model_judge: str = "anthropic/claude-opus-5-5",
) -> int:
    names = list(participants or [])
    # Hidden names live in the text as labels; the chat trusts only labels found there.
    source = "Исходный материал про воду." + "".join(f"\n{n}: реплика." for n in names)
    analysis_id = await repo.create(
        source_text=source,
        question="верен ли вывод?",
        rounds=1,
        model_analyst="anthropic/claude-opus-5-5",
        model_judge=model_judge,
        title="Исходный материал",
        mode="dialogue" if names else "text",
    )
    await repo.add_turn(analysis_id, 1, "Эмпирик", "🔎", "Реплика эмпирика про факты.")
    if verdict:
        await repo.save_verdict(analysis_id, make_verdict(names))
    if status != "running":
        await repo.finish(analysis_id, status, None if status == "finished" else "boom", 10)
    return analysis_id


def _chat(repo, provider, settings=None, models: list[str] | None = None) -> JudgeChatService:
    def factory(model: str):
        if models is not None:
            models.append(model)
        return provider

    return JudgeChatService(repo=repo, provider_factory=factory, settings=settings or _settings())


class TestAnonymizeMentions:
    def test_replaces_names_and_their_declensions_with_labels(self):
        result = anonymize_mentions("Иван прав, а Марию и Ивана не слушайте", LEGEND)
        assert result == "Собеседник А прав, а Собеседник Б и Собеседник А не слушайте"

    def test_empty_legend_is_identity(self):
        assert anonymize_mentions("Иван прав", {}) == "Иван прав"


class TestPrompts:
    def test_system_prompt_carries_manifest_and_anti_sycophancy_rules(self):
        system = judge_chat_system_prompt(LANG)
        assert JUDGE_CHAT_MARKER in system
        assert MANIFEST in system
        assert "Пересматриваю" in system
        assert "не довод" in system
        assert "бремя доказывания" in system.lower()
        assert "80–300" in system
        assert "«русский»" in system

    def test_context_has_material_transcript_and_verdict(self):
        verdict = make_verdict()
        context = judge_chat_context(
            "Исходный материал",
            "верен ли вывод?",
            "[Раунд 1] Эмпирик: факты",
            "text",
            verdict.model_dump_json(),
        )
        for piece in ("Исходный материал", "верен ли вывод?", "[Раунд 1] Эмпирик: факты"):
            assert piece in context
        assert verdict.conclusion in context

    def test_user_prompt_has_history_and_the_new_message_last(self):
        user = judge_chat_user(
            [("user", "Первый вопрос"), ("judge", "Первый ответ")], "Новое возражение", LANG
        )
        for piece in ("Пользователь: Первый вопрос", "Судья: Первый ответ", "Новое возражение"):
            assert piece in user
        assert user.rindex("Новое возражение") > user.rindex("Первый ответ")

    def test_dialogue_mode_lives_in_the_context_and_no_history_means_no_turns(self):
        context = judge_chat_context("т", None, "транскрипт", "dialogue", "{}")
        assert "ДИАЛОГ" in context
        assert "Пользователь:" not in judge_chat_user([], "вопрос", LANG).split("вопрос")[0]


class TestCaching:
    async def test_chat_context_is_cached_for_an_hour_and_stable_between_messages(self):
        # SPEC §4.8: material + transcript + verdict are one cached block; history goes after.
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        provider = FakeProvider(replies=["Первый ответ.", "Второй ответ."])
        chat = _chat(repo, provider)
        await chat.ask(analysis_id, "Первый вопрос")
        await chat.ask(analysis_id, "Второй вопрос")
        first, second = provider.calls
        assert first.cache_ttl == second.cache_ttl == "1h"
        assert first.context == second.context
        assert "Исходный материал про воду." in first.context
        assert "Первый вопрос" not in second.context
        assert "Первый вопрос" in second.user and "Второй вопрос" in second.user


class TestAsk:
    async def test_reply_is_returned_and_both_messages_persisted(self):
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        chat = _chat(repo, FakeProvider(replies=["Ответ судьи."]))
        exchange = await chat.ask(analysis_id, "  Почему так?  ")
        assert (exchange.question.role, exchange.question.text) == ("user", "Почему так?")
        assert (exchange.reply.role, exchange.reply.text) == ("judge", "Ответ судьи.")
        record = await repo.get(analysis_id)
        assert [(m.role, m.text) for m in record.chat] == [
            ("user", "Почему так?"),
            ("judge", "Ответ судьи."),
        ]

    async def test_judge_model_effort_budget_and_context(self):
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo, model_judge="deepseek/deepseek-chat")
        provider = FakeProvider(replies=["ok"])
        models: list[str] = []
        settings = _settings(judge_chat_effort="low", judge_chat_max_tokens=2048)
        await _chat(repo, provider, settings, models).ask(analysis_id, "Вопрос пользователя")
        assert models == ["deepseek/deepseek-chat"]
        request = provider.calls[0]
        assert request.model == "deepseek-chat"
        assert (request.effort, request.max_tokens) == ("low", 2048)
        assert JUDGE_CHAT_MARKER in request.system
        assert "Исходный материал про воду." in request.context
        assert "Реплика эмпирика про факты." in request.context
        assert "Тестовое заключение" in request.context
        assert "Вопрос пользователя" in request.user

    async def test_previous_exchange_reaches_the_next_prompt(self):
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        provider = FakeProvider(replies=["Первый ответ", "Второй ответ"])
        chat = _chat(repo, provider)
        await chat.ask(analysis_id, "Первый вопрос")
        await chat.ask(analysis_id, "Второй вопрос")
        second = provider.calls[1].user
        assert "Пользователь: Первый вопрос" in second
        assert "Судья: Первый ответ" in second
        assert len((await repo.get(analysis_id)).chat) == 4

    async def test_missing_analysis(self):
        with pytest.raises(AnalysisNotFound):
            await _chat(InMemoryRepository(), FakeProvider()).ask(42, "вопрос")

    @pytest.mark.parametrize(
        "status,verdict", [("running", False), ("failed", False), ("failed", True)]
    )
    async def test_only_finished_analysis_with_verdict(self, status, verdict):
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo, status=status, verdict=verdict)
        provider = FakeProvider()
        with pytest.raises(ChatUnavailable):
            await _chat(repo, provider).ask(analysis_id, "вопрос")
        assert provider.calls == []

    @pytest.mark.parametrize("text", ["", "   ", "x" * 11])
    async def test_empty_or_too_long_message(self, text):
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        with pytest.raises(ValueError):
            await _chat(repo, FakeProvider(), _settings(judge_chat_max_chars=10)).ask(
                analysis_id, text
            )

    async def test_message_limit(self):
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        chat = _chat(repo, FakeProvider(), _settings(judge_chat_max_messages=2))
        await chat.ask(analysis_id, "раз")
        await chat.ask(analysis_id, "два")
        with pytest.raises(ChatUnavailable):
            await chat.ask(analysis_id, "три")

    async def test_second_message_while_judge_answers_is_rejected(self):
        gate = asyncio.Event()

        class GatedProvider(FakeProvider):
            async def complete(self, request: ChatRequest) -> Completion:
                await gate.wait()
                return Completion(text="готово", usage=Usage())

        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        chat = _chat(repo, GatedProvider())
        first = asyncio.create_task(chat.ask(analysis_id, "первое"))
        await asyncio.sleep(0)
        with pytest.raises(ChatUnavailable):
            await chat.ask(analysis_id, "второе")
        gate.set()
        assert (await first).reply.text == "готово"
        # the lock is released afterwards
        await chat.ask(analysis_id, "третье")

    async def test_provider_failure_persists_nothing_and_releases_lock(self):
        class Broken(FakeProvider):
            async def complete(self, request: ChatRequest) -> Completion:
                raise RuntimeError("network down")

        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        with pytest.raises(ProviderError, match="network down"):
            await _chat(repo, Broken()).ask(analysis_id, "вопрос")
        assert (await repo.get(analysis_id)).chat == []
        await _chat(repo, FakeProvider()).ask(analysis_id, "снова")

    async def test_hanging_reply_times_out_and_releases_lock(self):
        class Hanging(FakeProvider):
            async def complete(self, request: ChatRequest) -> Completion:
                await asyncio.sleep(10)
                return Completion(text="поздно", usage=Usage())

        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        chat = _chat(repo, Hanging(), _settings(judge_chat_timeout_s=0.01))
        with pytest.raises(ProviderError, match="did not answer"):
            await chat.ask(analysis_id, "вопрос")
        assert not chat.is_busy(analysis_id)
        assert (await repo.get(analysis_id)).chat == []

    async def test_refusal_propagates(self):
        class Refusing(FakeProvider):
            async def complete(self, request: ChatRequest) -> Completion:
                raise ProviderRefused("refused")

        repo = InMemoryRepository()
        analysis_id = await _analysis(repo)
        with pytest.raises(ProviderRefused):
            await _chat(repo, Refusing()).ask(analysis_id, "вопрос")
        assert (await repo.get(analysis_id)).chat == []


class TestAnonymization:
    async def test_dialogue_message_names_replaced_before_model_and_db(self):
        repo = InMemoryRepository()
        analysis_id = await _analysis(repo, participants=DIALOGUE)
        provider = FakeProvider(replies=["ok"])
        exchange = await _chat(repo, provider).ask(
            analysis_id, "Иван не прав, а Марию вы недооценили", legend=LEGEND
        )
        expected = "Собеседник А не прав, а Собеседник Б вы недооценили"
        assert exchange.question.text == expected
        assert expected in provider.calls[0].user
        assert "Иван" not in provider.calls[0].user + provider.calls[0].context
        assert (await repo.get(analysis_id)).chat[0].text == expected

    async def test_legend_ignored_for_text_and_for_unknown_labels(self):
        repo = InMemoryRepository()
        text_id = await _analysis(repo)
        chat = _chat(repo, FakeProvider())
        exchange = await chat.ask(text_id, "Иван прав", legend=LEGEND)
        assert exchange.question.text == "Иван прав"

        dialogue_id = await _analysis(repo, participants=DIALOGUE)
        exchange = await chat.ask(dialogue_id, "вода мокрая", legend={"Собеседник Я": "вода"})
        assert exchange.question.text == "вода мокрая"


class TestMarker:
    def test_chat_marker_appears_in_no_other_prompt(self):
        from debate_arena.domain.analysts import ANALYSTS
        from debate_arena.domain.prompts import judge_system_prompt

        assert JUDGE_CHAT_MARKER not in MANIFEST
        assert JUDGE_CHAT_MARKER not in judge_system_prompt(LANG)
        assert all(JUDGE_CHAT_MARKER not in a.system_prompt for a in ANALYSTS)


class TestMock:
    async def test_mock_answers_chat_as_the_judge(self):
        request = ChatRequest(
            model="rational",
            system=judge_chat_system_prompt(LANG),
            user="Новое сообщение пользователя: почему?",
            max_tokens=1024,
        )
        reply = await MockProvider(delay_s=0).complete(request)
        assert reply.text
        assert "Принимаю:" not in reply.text
        assert "Ядро текста" not in reply.text  # not an analyst's round-one reply
