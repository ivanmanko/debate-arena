"""Anthropic provider via the official SDK (ADR-001).

Opus 5.5 specifics: thinking is always on and ``effort`` is the only depth control;
``messages.create`` takes no temperature (SDK 1.13). Structured output for the judge
goes through ``messages.parse(output_format=Verdict)``.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic
from pydantic import ValidationError

from ...domain.exceptions import InvalidVerdict, ProviderRefused
from ...domain.ports import ChatRequest, Completion, ModelT, StructuredCompletion, Usage

logger = logging.getLogger(__name__)


class AnthropicProvider:
    """``ChatProvider`` over ``anthropic.AsyncAnthropic`` (Opus 5.5 request shape)."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, *, client: Any | None = None) -> None:
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    async def complete(self, request: ChatRequest) -> Completion:
        response = await self._client.messages.create(**self._kwargs(request))
        _raise_on_refusal(response)
        text = _text_of(response)
        if getattr(response, "stop_reason", None) == "max_tokens":
            logger.warning("anthropic: reply truncated by max_tokens=%s", request.max_tokens)
        return Completion(text=text, usage=_usage_of(response))

    async def complete_structured(
        self, request: ChatRequest, schema: type[ModelT]
    ) -> StructuredCompletion[ModelT]:
        try:
            response = await self._client.messages.parse(
                **self._kwargs(request), output_format=schema
            )
        except ValidationError as exc:
            # The SDK validates before we see stop_reason; a cut-off JSON is the usual cause.
            raise InvalidVerdict(
                "anthropic: structured reply did not match the schema — most likely truncated "
                f"by max_tokens={request.max_tokens} (thinking counts toward it); raise "
                f"JUDGE_MAX_TOKENS. Detail: {str(exc).splitlines()[-2:]}"
            ) from exc
        _raise_on_refusal(response)
        value = getattr(response, "parsed_output", None)
        if value is None:
            raise InvalidVerdict(
                "anthropic: no parsed_output in structured response "
                f"(stop_reason={getattr(response, 'stop_reason', None)!r})"
            )
        return StructuredCompletion(value=value, usage=_usage_of(response), raw=_text_of(response))

    @staticmethod
    def _kwargs(request: ChatRequest) -> dict[str, Any]:
        return {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": {"effort": request.effort},
        }


def _raise_on_refusal(response: Any) -> None:
    if getattr(response, "stop_reason", None) != "refusal":
        return
    details = getattr(response, "stop_details", None)
    category = getattr(details, "category", None)
    explanation = getattr(details, "explanation", None) or "model refused the request"
    raise ProviderRefused(f"Модель отказалась разбирать этот текст: {explanation}", category)


def _text_of(response: Any) -> str:
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()


def _usage_of(response: Any) -> Usage:
    usage = getattr(response, "usage", None)
    return Usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )
