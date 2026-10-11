"""DeepSeek provider: OpenAI-compatible chat completions over httpx (ADR-001).

Structured output = ``response_format: json_object`` plus the JSON schema embedded in
the prompt by the caller; the reply is parsed tolerantly (domain.verdict).
"""

from __future__ import annotations

import httpx

from ...domain.exceptions import InvalidVerdict
from ...domain.ports import ChatRequest, Completion, ModelT, StructuredCompletion, Usage
from ...domain.verdict import parse_verdict_text

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# Reasoner replies can take minutes; generous read timeout, SDK-like connect timeout.
_TIMEOUT = httpx.Timeout(600.0, connect=10.0)


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        temperature: float,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._temperature = temperature
        self._client = http_client or httpx.AsyncClient(
            base_url=DEEPSEEK_BASE_URL, timeout=_TIMEOUT
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
        if schema.__name__ != "Verdict":
            try:
                value = schema.model_validate_json(raw)
            except ValueError as exc:
                raise InvalidVerdict(str(exc)) from exc
        else:
            value = parse_verdict_text(raw)  # type: ignore[assignment]
        return StructuredCompletion(value=value, usage=_usage_of(payload), raw=raw)

    async def _post(self, request: ChatRequest, *, json_mode: bool) -> dict:
        body: dict = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": request.max_tokens,
            "temperature": self._temperature,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        response = await self._client.post("/chat/completions", json=body)
        response.raise_for_status()
        return response.json()


def _content_of(payload: dict) -> str:
    try:
        return str(payload["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise InvalidVerdict(f"deepseek: unexpected response shape: {payload!r}"[:300]) from exc


def _usage_of(payload: dict) -> Usage:
    usage = payload.get("usage") or {}
    return Usage(
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
    )
