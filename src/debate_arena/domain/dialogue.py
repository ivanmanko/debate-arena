"""Dialogue parsing and input-mode detection (SPEC §4.0)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

InputMode = Literal["text", "dialogue"]
RequestedMode = Literal["auto", "text", "dialogue"]

DIALOGUE_NAME_CHARS = 40  # SPEC §7
_MAX_NAME_WORDS = 4

# Optional leading timestamp like "[12:03]" / "12:03:15" / "(12:03)", then the speaker.
_TIMESTAMP = r"(?:\[[^\]]{1,24}\]\s*|\(?\d{1,2}:\d{2}(?::\d{2})?\)?\s*)?"
_SPEAKER_PATTERNS = [
    # "Имя: текст", "**Имя:** текст", "Имя — текст", "Имя – текст"
    re.compile(
        rf"^\s*{_TIMESTAMP}(?:\*\*)?"
        rf"(?P<name>[^\s\[\]<>*:—–][^:—–\n]{{0,{DIALOGUE_NAME_CHARS - 1}}}?)"
        r"(?:\*\*)?\s*(?::|—|–)(?:\*\*)?\s+(?P<text>\S.*)$"
    ),
    # "[Имя] текст", "<Имя> текст"
    re.compile(
        rf"^\s*{_TIMESTAMP}[\[<](?P<name>[^\]>]{{1,{DIALOGUE_NAME_CHARS}}})[\]>]\s+(?P<text>\S.*)$"
    ),
]
_URL_RE = re.compile(r"^(https?:|www\.)", re.IGNORECASE)


@dataclass
class Participant:
    name: str
    lines: list[str] = field(default_factory=list)


def _speaker_of(line: str) -> tuple[str, str] | None:
    for pattern in _SPEAKER_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue
        name = match.group("name").strip().strip("*").strip()
        if not name or _URL_RE.match(name) or len(name.split()) > _MAX_NAME_WORDS:
            return None
        if not any(ch.isalpha() for ch in name):
            return None
        return name, match.group("text").strip()
    return None


def parse_dialogue(text: str) -> list[Participant]:
    """Split ``text`` into participants; lines without a speaker continue the previous one."""
    participants: dict[str, Participant] = {}
    current: Participant | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        spoken = _speaker_of(line)
        if spoken:
            name, body = spoken
            current = participants.setdefault(name, Participant(name))
            current.lines.append(body)
        elif current is not None:
            current.lines[-1] = f"{current.lines[-1]}\n{line}"
    return list(participants.values())


def speaker_line_share(text: str) -> float:
    """Share of non-empty lines that start with a recognised speaker."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    return sum(1 for ln in lines if _speaker_of(ln.strip())) / len(lines)


def resolve_mode(
    text: str, mode: RequestedMode, *, line_share: float
) -> tuple[InputMode, list[str]]:
    """Decide text vs dialogue and return the participant names (empty for text).

    Raises ``ValueError`` when ``mode="dialogue"`` but fewer than two speakers are found.
    """
    if mode == "text":
        return "text", []
    names = [p.name for p in parse_dialogue(text)]
    if mode == "dialogue":
        if len(names) < 2:
            raise ValueError(
                "не удалось распознать собеседников: нужны минимум два, формат «Имя: реплика»"
            )
        return "dialogue", names
    if len(names) >= 2 and speaker_line_share(text) >= line_share:
        return "dialogue", names
    return "text", []
