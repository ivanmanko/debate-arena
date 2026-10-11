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
                model="m", system=analyst.system_prompt, user="Независимый разбор", max_tokens=1
            )
            replies.add((await provider.complete(request)).text)
        assert len(replies) == 3

    async def test_structured_returns_verdict_for_all_analysts(self):
        provider = MockProvider()
        result = await provider.complete_structured(_request(), Verdict)
        result.value.validate_for(NAMES)
        assert result.value.most_rational in NAMES


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
        payload = make_verdict(NAMES).model_dump_json()

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
        verdict = make_verdict(NAMES)
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
