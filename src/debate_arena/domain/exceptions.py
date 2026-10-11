"""Domain exceptions. Interfaces map them to HTTP / chat messages in one place."""

from __future__ import annotations


class RationalCheckError(Exception):
    """Base class for domain errors."""


class ProviderNotConfigured(RationalCheckError):
    """Selected provider has no API key (or unknown provider)."""


class ProviderError(RationalCheckError):
    """Transport or response-shape failure of a provider (not a schema violation)."""


class ProviderRefused(RationalCheckError):
    """The model declined the request (Anthropic ``stop_reason == "refusal"``)."""

    def __init__(self, message: str, category: str | None = None) -> None:
        super().__init__(message)
        self.category = category


class InvalidVerdict(RationalCheckError):
    """The judge did not return a verdict matching the schema / invariants."""


class AnalysisNotFound(RationalCheckError):
    pass


class AnalysisRunning(RationalCheckError):
    """Operation not allowed while the analysis is still running."""
