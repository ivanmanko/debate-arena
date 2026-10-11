"""Tolerant parsing of a judge reply into ``Verdict`` (for providers without native
structured output — ADR-001)."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from .exceptions import InvalidVerdict
from .schemas import Verdict

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text)


def parse_verdict_text(raw: str) -> Verdict:
    """Accept clean JSON, fenced JSON, or JSON surrounded by prose; strip ``<think>``."""
    text = strip_think(raw).strip()
    candidates = [text]
    candidates += [m.strip() for m in _FENCE_RE.findall(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return Verdict.model_validate_json(candidate)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
    raise InvalidVerdict(f"judge did not return a valid verdict: {last_error}") from last_error
