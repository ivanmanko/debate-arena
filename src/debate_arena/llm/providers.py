"""Map a persona/judge to a CrewAI LLM object (LiteLLM under the hood).

Kept tiny and lazy: CrewAI is only imported when a real LLM is actually built,
so the bot and the test-suite run fine without crewai installed (mock engine).
"""

from __future__ import annotations

from typing import Any

from ..config import settings


def build_llm(
    model: str,
    temperature: float,
    max_tokens: int,
    response_format: Any | None = None,
) -> Any:
    """Construct a CrewAI `LLM`. Provider keys are read from the environment by
    LiteLLM; we also pass them explicitly when present for robustness.

    `response_format` (a Pydantic model) turns on the provider's JSON mode — used
    by the judge to get a strict structured verdict.
    """
    from crewai import LLM  # lazy: optional dependency

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    # LiteLLM picks keys from env vars; passing api_key explicitly covers the
    # case where the process env differs from our Settings source (.env).
    key = _api_key_for(model)
    if key:
        kwargs["api_key"] = key
    return LLM(**kwargs)


def _api_key_for(model: str) -> str:
    prefix = model.split("/", 1)[0]
    return {
        "openai": settings.openai_api_key,
        "openrouter": settings.openrouter_api_key,
        "deepseek": settings.deepseek_api_key,
        "gemini": settings.gemini_api_key,
        "anthropic": settings.anthropic_api_key,
    }.get(prefix, "")
