"""Provider routing and the two real providers behind the ChatProvider port (ADR-001)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from debate_arena.config import Settings
from debate_arena.domain.analysts import ANALYSTS
from debate_arena.domain.exceptions import ProviderNotConfigured, ProviderRefused
from debate_arena.domain.ports import ChatRequest
from debate_arena.domain.schemas import ModelRef, Verdict, parse_model_ref
from debate_arena.infrastructure.llm.anthropic import AnthropicProvider
from debate_arena.infrastructure.llm.deepseek import DeepSeekProvider
from debate_arena.infrastructure.llm.factory import build_provider
from debate_arena.infrastructure.llm.mock import MockProvider
from tests.fakes.llm import make_verdict

NAMES = [a.name for a in ANALYSTS]


def _settings(**overrides) -> Settings:
    base = dict(anthropic_api_key="", deepseek_api_key="", mock_llm=False)
    return Settings(_env_file=None, **(base | overrides))


def _request(user: str = "привет") -> ChatRequest:
    return ChatRequest(model="m", system="s", user=user, max_tokens=100, effort="low")


class TestModelRef:
    def test_parses_provider_and_model(self):
        assert parse_model_ref("anthropic/claude-opus-5-5") == ModelRef(
            provider="anthropic", model="claude-opus-5-5"
        )

    def test_model_part_may_contain_slashes(self):
        assert parse_model_ref("deepseek/org/model").model == "org/model"

    @pytest.mark.parametrize("bad", ["claude-opus-5-5", "/x", "anthropic/", "openai/gpt"])
    def test_rejects_malformed_or_unknown(self, bad):
        with pytest.raises(ValueError):
            parse_model_ref(bad)


class TestFactory:
    def test_mock_prefix_builds_mock_provider(self):
        assert isinstance(build_provider(parse_model_ref("mock/any"), _settings()), MockProvider)

    def test_anthropic_requires_key(self):
        with pytest.raises(ProviderNotConfigured):
            build_provider(parse_model_ref("anthropic/claude-opus-5-5"), _settings())

    def test_anthropic_with_key(self):
        provider = build_provider(
            parse_model_ref("anthropic/claude-opus-5-5"), _settings(anthropic_api_key="k")
        )
        assert isinstance(provider, AnthropicProvider)

    def test_deepseek_requires_key(self):
        with pytest.raises(ProviderNotConfigured):
            build_provider(parse_model_ref("deepseek/deepseek-chat"), _settings())

    def test_deepseek_with_key(self):
        provider = build_provider(
            parse_model_ref("deepseek/deepseek-chat"), _settings(deepseek_api_key="k")
        )
        assert isinstance(provider, DeepSeekProvider)


class TestMockProvider:
    async def test_round_one_and_cross_round_replies_differ(self):
        provider = MockProvider()
        first = await provider.complete(_request("... Независимый разбор ..."))
        cross = await provider.complete(_request("... Принимаю: ... Оспариваю: ..."))
        assert first.text and cross.text
        assert first.text != cross.text
        assert "Принимаю:" in cross.text

    async def test_round_one_replies_differ_per_analyst_lens(self):
        provider = MockProvider(delay_s=0)
        replies = set()
        for analyst in ANALYSTS:
            request = ChatRequest(
                model="m",
                system=analyst.system_prompt,
                user=f"{analyst.lens}\n\nНезависимый разбор",
                max_tokens=1,
            )
            replies.add((await provider.complete(request)).text)
        assert len(replies) == 3

    async def test_structured_returns_text_or_dialogue_verdict(self):
        provider = MockProvider(delay_s=0)
        text_result = await provider.complete_structured(_request(), Verdict)
        text_result.value.validate_for([])
        dialogue = await provider.complete_structured(
            ChatRequest(
                model="m",
                system="s",
                context="Материал — ДИАЛОГ.\nСобеседники: Иван, Мария.\n«««",
                user="вердикт",
                max_tokens=1,
            ),
            Verdict,
        )
        dialogue.value.validate_for(["Иван", "Мария"])
        assert dialogue.value.closest_to_truth == "Иван"

    async def test_prewarm_is_a_no_op(self):
        usage = await MockProvider(delay_s=0).prewarm(_request())
        assert usage.input_tokens == 0


class TestDeepSeekProvider:
    def _provider(self, handler) -> DeepSeekProvider:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport, base_url="https://api.deepseek.com")
        return DeepSeekProvider(api_key="sk-test", temperature=0.3, http_client=client)

    async def test_complete_posts_openai_shape_and_returns_text(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ответ модели"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                },
            )

        result = await self._provider(handler).complete(
            ChatRequest(model="deepseek-chat", system="sys", user="usr", max_tokens=50)
        )
        assert result.text == "ответ модели"
        assert result.usage.input_tokens == 12 and result.usage.output_tokens == 3
        assert seen["url"].endswith("/chat/completions")
        assert seen["auth"] == "Bearer sk-test"
        body = seen["body"]
        assert body["model"] == "deepseek-chat"
        assert body["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ]
        assert body["max_tokens"] == 50 and body["temperature"] == 0.3
        assert "response_format" not in body

    async def test_structured_uses_json_mode_and_parses_verdict(self):
        seen: dict = {}
        payload = make_verdict().model_dump_json()

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": f"```json\n{payload}\n```"}}]},
            )

        result = await self._provider(handler).complete_structured(
            ChatRequest(model="deepseek-chat", system="sys", user="usr", max_tokens=50), Verdict
        )
        assert seen["body"]["response_format"] == {"type": "json_object"}
        assert result.value.conclusion == "Тестовое заключение"

    async def test_http_error_surfaces_as_status_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad key"})

        with pytest.raises(httpx.HTTPStatusError):
            await self._provider(handler).complete(
                ChatRequest(model="deepseek-chat", system="s", user="u", max_tokens=5)
            )

    async def test_context_goes_before_the_task_in_one_user_message(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ок"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

        request = ChatRequest(
            model="deepseek-chat",
            system="sys",
            context="материал",
            user="задача",
            max_tokens=5,
            cache_ttl="5m",
        )
        provider = self._provider(handler)
        await provider.complete(request)
        assert seen["body"]["messages"][1] == {"role": "user", "content": "материал\n\nзадача"}
        # DeepSeek caches repeated prefixes on its own; there is nothing to warm.
        assert (await provider.prewarm(request)).input_tokens == 0


class _StubMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _stub_client(response):
    return SimpleNamespace(messages=_StubMessages(response))


class TestAnthropicProvider:
    async def test_complete_reads_text_blocks_and_passes_effort(self):
        response = SimpleNamespace(
            stop_reason="end_turn",
            stop_details=None,
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text="часть 1 "),
                SimpleNamespace(type="text", text="часть 2"),
            ],
            usage=SimpleNamespace(input_tokens=7, output_tokens=2),
        )
        client = _stub_client(response)
        provider = AnthropicProvider(client=client)
        result = await provider.complete(
            ChatRequest(model="claude-opus-5-5", system="s", user="u", max_tokens=9, effort="high")
        )
        assert result.text == "часть 1 часть 2"
        assert result.usage.input_tokens == 7
        kwargs = client.messages.kwargs
        assert kwargs["model"] == "claude-opus-5-5"
        assert kwargs["output_config"] == {"effort": "high"}
        assert kwargs["system"] == "s"
        assert kwargs["messages"] == [{"role": "user", "content": "u"}]
        assert "temperature" not in kwargs

    async def test_refusal_raises_domain_error(self):
        response = SimpleNamespace(
            stop_reason="refusal",
            stop_details=SimpleNamespace(category="general_harms", explanation="nope"),
            content=[],
            usage=SimpleNamespace(input_tokens=1, output_tokens=0),
        )
        provider = AnthropicProvider(client=_stub_client(response))
        with pytest.raises(ProviderRefused):
            await provider.complete(ChatRequest(model="m", system="s", user="u", max_tokens=9))

    async def test_structured_uses_parse_and_parsed_output(self):
        verdict = make_verdict()
        response = SimpleNamespace(
            stop_reason="end_turn",
            stop_details=None,
            content=[SimpleNamespace(type="text", text=verdict.model_dump_json())],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            parsed_output=verdict,
        )
        client = _stub_client(response)
        provider = AnthropicProvider(client=client)
        result = await provider.complete_structured(
            ChatRequest(model="m", system="s", user="u", max_tokens=9), Verdict
        )
        assert result.value is verdict
        assert client.messages.kwargs["output_format"] is Verdict


class TestAnthropicCaching:
    """SPEC §4.8: system + context are the cached prefix; the task follows uncached."""

    def _response(self, **usage):
        return SimpleNamespace(
            stop_reason="end_turn",
            stop_details=None,
            content=[SimpleNamespace(type="text", text="ок")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=2, **usage),
        )

    async def test_context_is_a_separate_block_with_cache_control(self):
        client = _stub_client(self._response())
        request = ChatRequest(
            model="m", system="s", context="материал", user="задача", max_tokens=9, cache_ttl="5m"
        )
        await AnthropicProvider(client=client).complete(request)
        assert client.messages.kwargs["messages"] == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "материал", "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "задача"},
                ],
            }
        ]

    async def test_one_hour_ttl_is_passed_through(self):
        client = _stub_client(self._response())
        request = ChatRequest(
            model="m", system="s", context="материал", user="задача", max_tokens=9, cache_ttl="1h"
        )
        await AnthropicProvider(client=client).complete(request)
        block = client.messages.kwargs["messages"][0]["content"][0]
        assert block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    async def test_uncached_context_has_no_marker(self):
        client = _stub_client(self._response())
        request = ChatRequest(
            model="m", system="s", context="материал", user="задача", max_tokens=9
        )
        await AnthropicProvider(client=client).complete(request)
        blocks = client.messages.kwargs["messages"][0]["content"]
        assert [b["text"] for b in blocks] == ["материал", "задача"]
        assert all("cache_control" not in b for b in blocks)

    async def test_usage_counts_cached_tokens_into_the_prompt_size(self):
        client = _stub_client(
            self._response(cache_read_input_tokens=900, cache_creation_input_tokens=90)
        )
        result = await AnthropicProvider(client=client).complete(
            ChatRequest(model="m", system="s", user="u", max_tokens=9)
        )
        usage = result.usage
        assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (
            1000,
            900,
            90,
        )

    async def test_prewarm_sends_the_same_prefix_with_zero_max_tokens(self):
        client = _stub_client(
            SimpleNamespace(
                stop_reason="max_tokens",
                stop_details=None,
                content=[],
                usage=SimpleNamespace(
                    input_tokens=3, output_tokens=0, cache_creation_input_tokens=500
                ),
            )
        )
        request = ChatRequest(
            model="m",
            system="s",
            context="материал",
            user="задача",
            max_tokens=9,
            effort="medium",
            cache_ttl="5m",
        )
        usage = await AnthropicProvider(client=client).prewarm(request)
        kwargs = client.messages.kwargs
        assert kwargs["max_tokens"] == 0
        assert (kwargs["system"], kwargs["output_config"]) == ("s", {"effort": "medium"})
        content = kwargs["messages"][0]["content"]
        assert content[0] == {
            "type": "text",
            "text": "материал",
            "cache_control": {"type": "ephemeral"},
        }
        assert content[1]["text"].strip() and content[1]["text"] != "задача"
        assert usage.cache_write_tokens == 500

    async def test_prewarm_without_cache_sends_nothing(self):
        client = _stub_client(self._response())
        usage = await AnthropicProvider(client=client).prewarm(
            ChatRequest(model="m", system="s", context="материал", user="u", max_tokens=9)
        )
        assert client.messages.kwargs is None
        assert usage.input_tokens == 0


class TestAnthropicTruncation:
    async def test_truncated_structured_reply_is_invalid_verdict(self):
        from pydantic import ValidationError

        class Raising:
            async def parse(self, **kwargs):
                raise ValidationError.from_exception_data("Verdict", [])

        provider = AnthropicProvider(client=SimpleNamespace(messages=Raising()))
        from debate_arena.domain.exceptions import InvalidVerdict

        with pytest.raises(InvalidVerdict, match="JUDGE_MAX_TOKENS"):
            await provider.complete_structured(
                ChatRequest(model="m", system="s", user="u", max_tokens=900), Verdict
            )


class TestDeepSeekRetries:
    async def test_retries_transient_status_then_succeeds(self):
        statuses = iter([503, 429, 200])

        def handler(request: httpx.Request) -> httpx.Response:
            status = next(statuses)
            if status != 200:
                return httpx.Response(status, json={"error": "later"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
        provider = DeepSeekProvider(
            api_key="k", temperature=0.1, max_retries=2, retry_base_s=0, http_client=client
        )
        result = await provider.complete(ChatRequest(model="m", system="s", user="u", max_tokens=5))
        assert result.text == "ok"

    async def test_gives_up_after_max_retries(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, json={"error": "down"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
        provider = DeepSeekProvider(
            api_key="k", temperature=0.1, max_retries=1, retry_base_s=0, http_client=client
        )
        with pytest.raises(httpx.HTTPStatusError):
            await provider.complete(ChatRequest(model="m", system="s", user="u", max_tokens=5))

    async def test_client_error_is_not_retried(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(400, json={"error": "bad"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
        provider = DeepSeekProvider(
            api_key="k", temperature=0.1, max_retries=3, retry_base_s=0, http_client=client
        )
        with pytest.raises(httpx.HTTPStatusError):
            await provider.complete(ChatRequest(model="m", system="s", user="u", max_tokens=5))
        assert calls == 1


class TestProviderRegistry:
    async def test_memoises_per_model_and_closes(self):
        from debate_arena.infrastructure.llm.factory import ProviderRegistry

        registry = ProviderRegistry(_settings(deepseek_api_key="k"))
        first = registry.get("deepseek/deepseek-chat")
        assert registry.get("deepseek/deepseek-chat") is first
        assert registry.get("mock/rational") is not first
        await registry.aclose()
        assert registry.get("deepseek/deepseek-chat") is not first
