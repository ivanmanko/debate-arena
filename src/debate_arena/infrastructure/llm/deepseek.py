"""DeepSeek provider: OpenAI-compatible chat completions over httpx (ADR-001).

Structured output = ``response_format: json_object`` plus the JSON schema embedded in
the prompt by the caller; the reply is parsed tolerantly (domain.verdict). httpx has no
retries of its own, so transient failures are retried here (SPEC §7 DEEPSEEK_MAX_RETRIES).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ...domain.exceptions import InvalidVerdict, ProviderError
from ...domain.ports import ChatRequest, Completion, ModelT, StructuredCompletion, Usage
from ...domain.verdict import parse_json_model

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_RETRY_STATUSES = {408, 409, 429}
_RETRY_BASE_S = 0.5


class DeepSeekProvider:
    """``ChatProvider`` over DeepSeek's OpenAI-compatible endpoint."""

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        temperature: float,
        *,
        max_retries: int = 2,
        timeout_s: float = 600.0,
        retry_base_s: float = _RETRY_BASE_S,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._temperature = temperature
        self._max_retries = max_retries
        self._retry_base_s = retry_base_s
        self._client = http_client or httpx.AsyncClient(
            base_url=DEEPSEEK_BASE_URL, timeout=httpx.Timeout(timeout_s, connect=10.0)
        )
        self._client.headers["Authorization"] = f"Bearer {api_key}"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, request: ChatRequest) -> Completion:
        payload = await self._post(request, json_mode=False)
        return Completion(text=_content_of(payload), usage=_usage_of(payload))

    async def complete_structured(
        self, request: ChatRequest, schema: type[ModelT]
    ) -> StructuredCompletion[ModelT]:
        payload = await self._post(request, json_mode=True)
        raw = _content_of(payload)
        try:
            value = parse_json_model(raw, schema)
        except InvalidVerdict:
            raise
        except ValueError as exc:
            raise InvalidVerdict(str(exc)) from exc
        return StructuredCompletion(value=value, usage=_usage_of(payload), raw=raw)

    async def prewarm(self, request: ChatRequest) -> Usage:
        """DeepSeek caches repeated prefixes on its own (context first keeps them shared)."""
        return Usage()

    async def _post(self, request: ChatRequest, *, json_mode: bool) -> dict:
        body: dict = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.joined_user()},
            ],
            "max_tokens": request.max_tokens,
            "temperature": self._temperature,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post("/chat/completions", json=body)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ProviderError(f"deepseek: {exc}") from exc
                reason = str(exc)
            else:
                retryable = response.status_code in _RETRY_STATUSES or response.status_code >= 500
                if not retryable or attempt >= self._max_retries:
                    response.raise_for_status()
                    return response.json()
                reason = f"HTTP {response.status_code}"
            delay = self._retry_base_s * 2**attempt
            logger.warning(
                "deepseek: %s, retry %d/%d in %.1fs", reason, attempt + 1, self._max_retries, delay
            )
            await asyncio.sleep(delay)
        raise ProviderError(
            "deepseek: retries exhausted"
        )  # pragma: no cover — loop always returns/raises


def _content_of(payload: dict) -> str:
    try:
        return str(payload["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"deepseek: unexpected response shape: {payload!r}"[:300]) from exc


def _usage_of(payload: dict) -> Usage:
    usage = payload.get("usage") or {}
    return Usage(
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("prompt_cache_hit_tokens", 0) or 0),
    )
