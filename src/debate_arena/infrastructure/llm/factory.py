"""Pick a ``ChatProvider`` implementation from a ``ModelRef`` (ADR-001).

``ProviderRegistry`` memoises one provider (one HTTP client pool) per model string for
the process lifetime and closes them on shutdown.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.exceptions import ProviderNotConfigured
from ...domain.ports import ChatProvider
from ...domain.schemas import ModelRef, parse_model_ref
from .anthropic import AnthropicProvider
from .deepseek import DeepSeekProvider
from .mock import MockProvider


def build_provider(ref: ModelRef, settings: Settings) -> ChatProvider:
    """Construct a fresh provider for ``ref``; raises ``ProviderNotConfigured`` without a key."""
    if ref.provider == "mock":
        return MockProvider()
    if ref.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ProviderNotConfigured("ANTHROPIC_API_KEY is not set")
        return AnthropicProvider(api_key=settings.anthropic_api_key)
    if ref.provider == "deepseek":
        if not settings.deepseek_api_key:
            raise ProviderNotConfigured("DEEPSEEK_API_KEY is not set")
        return DeepSeekProvider(
            settings.deepseek_api_key,
            settings.deepseek_temperature,
            max_retries=settings.deepseek_max_retries,
            timeout_s=settings.deepseek_timeout_s,
        )
    raise ProviderNotConfigured(f"unknown provider {ref.provider!r}")


class ProviderRegistry:
    """One provider per model string, shared by every analysis; ``aclose`` on shutdown."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._providers: dict[str, ChatProvider] = {}

    def get(self, model: str) -> ChatProvider:
        provider = self._providers.get(model)
        if provider is None:
            provider = build_provider(parse_model_ref(model), self._settings)
            self._providers[model] = provider
        return provider

    async def aclose(self) -> None:
        for provider in self._providers.values():
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()
        self._providers.clear()
