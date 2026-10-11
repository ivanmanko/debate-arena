"""Pick a ``ChatProvider`` implementation from a ``ModelRef`` (ADR-001)."""

from __future__ import annotations

from ...config import Settings
from ...domain.exceptions import ProviderNotConfigured
from ...domain.ports import ChatProvider
from ...domain.schemas import ModelRef
from .anthropic import AnthropicProvider
from .deepseek import DeepSeekProvider
from .mock import MockProvider


def build_provider(ref: ModelRef, settings: Settings) -> ChatProvider:
    if ref.provider == "mock":
        return MockProvider()
    if ref.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ProviderNotConfigured("ANTHROPIC_API_KEY is not set")
        return AnthropicProvider(api_key=settings.anthropic_api_key)
    if ref.provider == "deepseek":
        if not settings.deepseek_api_key:
            raise ProviderNotConfigured("DEEPSEEK_API_KEY is not set")
        return DeepSeekProvider(settings.deepseek_api_key, settings.deepseek_temperature)
    raise ProviderNotConfigured(f"unknown provider {ref.provider!r}")
