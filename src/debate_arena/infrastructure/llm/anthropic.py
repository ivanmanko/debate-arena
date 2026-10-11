"""Anthropic provider via the official SDK (ADR-001).

Opus 5.5 specifics: thinking is always on and ``effort`` is the only depth control;
``messages.create`` takes no temperature (SDK 1.13). Structured output for the judge
goes through ``messages.parse(output_format=Verdict)``.

Refusals (SPEC §4.9): with ``refusal_fallback`` every call goes through the beta API with
``fallbacks="default"`` — a request declined by a safety classifier is re-run server-side on
the model Anthropic recommends for that refusal category, in the same round trip.

Prompt caching (SPEC §4.8, ADR-007): ``context`` is its own user block, marked with
``cache_control`` when the request asks for a TTL — the breakpoint caches system + context.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import anthropic
from pydantic import ValidationError

from ...domain.exceptions import InvalidVerdict, ProviderRefused
from ...domain.ports import ChatRequest, Completion, ModelT, StructuredCompletion, Usage

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    """``ChatProvider`` over ``anthropic.AsyncAnthropic`` (Opus 5.5 request shape)."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: Any | None = None,
        refusal_fallback: bool = False,
    ) -> None:
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)
        self.refusal_fallback = refusal_fallback

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    async def complete(self, request: ChatRequest) -> Completion:
        response = await self._messages.create(**self._kwargs(request), **self._fallback)
        self._check(request, response)
        text = _text_of(response)
        if getattr(response, "stop_reason", None) == "max_tokens":
            logger.warning("anthropic: reply truncated by max_tokens=%s", request.max_tokens)
        return Completion(text=text, usage=_usage_of(response))

    async def complete_structured(
        self, request: ChatRequest, schema: type[ModelT]
    ) -> StructuredCompletion[ModelT]:
        try:
            response = await self._messages.parse(
                **self._kwargs(request), **self._fallback, output_format=schema
            )
        except ValidationError as exc:
            # The SDK validates before we see stop_reason; a cut-off JSON is the usual cause.
            raise InvalidVerdict(
                "anthropic: structured reply did not match the schema — most likely truncated "
                f"by max_tokens={request.max_tokens} (thinking counts toward it); raise "
                f"JUDGE_MAX_TOKENS. Detail: {str(exc).splitlines()[-2:]}"
            ) from exc
        self._check(request, response)
        value = getattr(response, "parsed_output", None)
        if value is None:
            raise InvalidVerdict(
                "anthropic: no parsed_output in structured response "
                f"(stop_reason={getattr(response, 'stop_reason', None)!r})"
            )
        return StructuredCompletion(value=value, usage=_usage_of(response), raw=_text_of(response))

    async def prewarm(self, request: ChatRequest) -> Usage:
        """Write system + context to the cache with a ``max_tokens=0`` call (no output billed).

        The placeholder stands in for the task: everything up to the breakpoint matches the
        real requests, including effort, so they read the entry this call writes.
        """
        if request.cache_ttl is None or not request.context:
            return Usage()
        warm = replace(request, user=_PREWARM_PLACEHOLDER, max_tokens=0)
        response = await self._messages.create(**self._kwargs(warm), **self._fallback)
        return _usage_of(response)

    @property
    def _messages(self) -> Any:
        return self._client.beta.messages if self.refusal_fallback else self._client.messages

    @property
    def _fallback(self) -> dict[str, Any]:
        if not self.refusal_fallback:
            return {}
        return {"betas": [FALLBACK_BETA], "fallbacks": "default"}

    def _check(self, request: ChatRequest, response: Any) -> None:
        """Raise on a refusal of the whole chain; log a reply served by a fallback model."""
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            label = f" (категория «{category}»)" if category else ""
            chain = " — резервная модель тоже отказалась" if self.refusal_fallback else ""
            raise ProviderRefused(
                f"Модель отказалась разбирать этот текст{label}{chain}.", category
            )
        served_by = getattr(response, "model", None)
        if isinstance(served_by, str) and served_by != request.model:
            logger.warning(
                "anthropic: %s declined the request; the fallback %s answered",
                request.model,
                served_by,
            )

    @staticmethod
    def _kwargs(request: ChatRequest) -> dict[str, Any]:
        return {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": _content(request)}],
            "output_config": {"effort": request.effort},
        }


_PREWARM_PLACEHOLDER = "Задание будет следующим сообщением."


def _content(request: ChatRequest) -> str | list[dict[str, Any]]:
    if not request.context:
        return request.user
    context: dict[str, Any] = {"type": "text", "text": request.context}
    if request.cache_ttl is not None:
        marker: dict[str, str] = {"type": "ephemeral"}
        if request.cache_ttl != "5m":  # 5 minutes is the API default
            marker["ttl"] = request.cache_ttl
        context["cache_control"] = marker
    return [context, {"type": "text", "text": request.user}]


def _text_of(response: Any) -> str:
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()


def _usage_of(response: Any) -> Usage:
    usage = getattr(response, "usage", None)
    read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return Usage(
        # The API's input_tokens is only the uncached tail; ours is the whole prompt.
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0) + read + written,
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_tokens=read,
        cache_write_tokens=written,
    )
