"""Dialogue parsing, input-mode detection and participant anonymisation (SPEC §4.0, §4.0b)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

InputMode = Literal["text", "dialogue"]
RequestedMode = Literal["auto", "text", "dialogue"]

DIALOGUE_NAME_CHARS = 40  # SPEC §7
_MAX_NAME_WORDS = 4

# Optional quote markers / bullets, then an optional timestamp, then the speaker.
_PREFIX = r"(?:>\s*)*(?:[-•]\s+)?"
_TIMESTAMP = r"(?:\[[^\]]{1,24}\]\s*|\(?\d{1,2}:\d{2}(?::\d{2})?\)?\s*)?"
_QUOTE_MARK_RE = re.compile(r"^\s*(?:>\s*)+")
_SPEAKER_PATTERNS = [
    # "Имя: текст", "**Имя:** текст", "Имя — текст", "Имя – текст"
    re.compile(
        rf"^\s*{_PREFIX}{_TIMESTAMP}(?:\*\*)?"
        rf"(?P<name>[^\s\[\]<>*:—–][^:—–\n]{{0,{DIALOGUE_NAME_CHARS - 1}}}?)"
        r"(?:\*\*)?\s*(?::|—|–)(?:\*\*)?(?:\s+(?P<text>\S.*))?\s*$"
    ),
    # "[Имя] текст", "<Имя> текст"
    re.compile(
        rf"^\s*{_PREFIX}{_TIMESTAMP}[\[<](?P<name>[^\]>]{{1,{DIALOGUE_NAME_CHARS}}})[\]>]"
        r"(?:\s+(?P<text>\S.*))?\s*$"
    ),
]
_URL_RE = re.compile(r"^(https?:|www\.)", re.IGNORECASE)

_RU_LETTERS = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЭЮЯ"
_EN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_VOWELS = set("аеёиоуыэюяaeiouy")
# A name stem shorter than this is replaced only as an exact word (SPEC §4.0b).
_MIN_STEM = 3


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
        return name, (match.group("text") or "").strip()
    return None


@dataclass(frozen=True)
class _Candidate:
    name: str
    body: str
    quoted: bool  # line starts with a ">" quote marker
    bare: bool  # name on its own line, body below


def _accepted_speakers(lines: list[str]) -> dict[int, tuple[str, str]]:
    """Speaker lines by index, after a format-consistency filter (SPEC §4.0).

    Chat exports mark every message the same way (quote marker, or a bare ``Name:`` line
    with the body below). When at least two candidates share such a marker and they are
    the majority, candidates without it are ordinary sentences with a colon, not speakers.
    """
    candidates: dict[int, _Candidate] = {}
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        spoken = _speaker_of(line)
        if spoken:
            name, body = spoken
            candidates[index] = _Candidate(name, body, line.startswith(">"), body == "")
    keep = set(candidates)
    for marker in ("quoted", "bare"):
        marked = {i for i, c in candidates.items() if getattr(c, marker)}
        if len(marked) >= 2 and len(marked) > len(candidates) - len(marked):
            keep &= marked
    return {i: (c.name, c.body) for i, c in candidates.items() if i in keep}


def parse_dialogue(text: str) -> list[Participant]:
    """Split ``text`` into participants; lines without a speaker continue the previous one."""
    lines = text.splitlines()
    speakers = _accepted_speakers(lines)
    participants: dict[str, Participant] = {}
    current: Participant | None = None
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if index in speakers:
            name, body = speakers[index]
            current = participants.setdefault(name, Participant(name))
            current.lines.append(body)
        elif current is not None:
            line = _QUOTE_MARK_RE.sub("", line).strip()
            current.lines[-1] = f"{current.lines[-1]}\n{line}" if current.lines[-1] else line
    return list(participants.values())


def speaker_line_share(text: str) -> float:
    """Share of non-empty lines that start with an accepted speaker."""
    lines = text.splitlines()
    non_empty = sum(1 for ln in lines if ln.strip())
    if not non_empty:
        return 0.0
    return len(_accepted_speakers(lines)) / non_empty


def resolve_mode(
    text: str, mode: RequestedMode, *, line_share: float, min_messages: int = 4
) -> tuple[InputMode, list[str]]:
    """Decide text vs dialogue and return the participant names (empty for text).

    ``auto`` → dialogue when ≥ 2 speakers and either most lines carry a speaker or there
    are at least ``min_messages`` messages (SPEC §4.0). Raises ``ValueError`` when
    ``mode="dialogue"`` but fewer than two speakers are found.
    """
    if mode == "text":
        return "text", []
    participants = parse_dialogue(text)
    names = [p.name for p in participants]
    messages = sum(len(p.lines) for p in participants)
    if mode == "dialogue":
        if len(names) < 2:
            raise ValueError(
                "не удалось распознать собеседников: нужны минимум два, формат «Имя: реплика»"
            )
        return "dialogue", names
    if len(names) >= 2 and (speaker_line_share(text) >= line_share or messages >= min_messages):
        return "dialogue", names
    return "text", []


@dataclass(frozen=True)
class AnonymizedDialogue:
    text: str
    participants: list[str]
    legend: dict[str, str]  # placeholder → real name; never persisted server-side


def participant_label(index: int, language: str) -> str:
    """``Собеседник А`` / ``Participant A``; numbers once the alphabet runs out."""
    russian = language.strip().lower().startswith(("рус", "ru"))
    letters = _RU_LETTERS if russian else _EN_LETTERS
    word = "Собеседник" if russian else "Participant"
    suffix = letters[index] if index < len(letters) else str(index + 1)
    return f"{word} {suffix}"


_TRANSLIT = [
    ("shch", "щ"),
    ("sch", "щ"),
    ("yo", "ё"),
    ("zh", "ж"),
    ("kh", "х"),
    ("ts", "ц"),
    ("ch", "ч"),
    ("sh", "ш"),
    ("ya", "я"),
    ("yu", "ю"),
    ("ye", "е"),
    ("ck", "к"),
    ("a", "а"),
    ("b", "б"),
    ("c", "к"),
    ("d", "д"),
    ("e", "е"),
    ("f", "ф"),
    ("g", "г"),
    ("h", "х"),
    ("i", "и"),
    ("j", "й"),
    ("k", "к"),
    ("l", "л"),
    ("m", "м"),
    ("n", "н"),
    ("o", "о"),
    ("p", "п"),
    ("q", "к"),
    ("r", "р"),
    ("s", "с"),
    ("t", "т"),
    ("u", "у"),
    ("v", "в"),
    ("w", "в"),
    ("x", "кс"),
    ("y", "ы"),
    ("z", "з"),
]


def transliterate(latin: str) -> str:
    """Best-effort Latin → Cyrillic for names (Vanya → Ваня, Friska → Фриска)."""
    out, i, low = [], 0, latin.lower()
    while i < len(low):
        for src, dst in _TRANSLIT:
            if low.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            out.append(low[i])
            i += 1
    result = "".join(out)
    return result[:1].upper() + result[1:] if latin[:1].isupper() else result


def _mention_pattern(name: str) -> re.Pattern[str]:
    """Match the name, its inflected forms and a Cyrillic transliteration (SPEC §4.0b)."""
    alternatives: list[str] = [re.escape(name)]
    tokens = name.split()
    if re.search(r"[A-Za-z]", name):
        tokens += [transliterate(t) for t in tokens]
    for token in tokens:
        stem = token[:-1] if token[-1].lower() in _VOWELS else token
        if len(stem) >= _MIN_STEM:
            alternatives.append(rf"{re.escape(stem)}\w{{0,3}}")
        elif len(token) >= _MIN_STEM:
            alternatives.append(re.escape(token))
    alternatives.sort(key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b", re.IGNORECASE)


def anonymize_mentions(text: str, legend: dict[str, str]) -> str:
    """Replace mentions of real names with their labels; ``legend`` is label → real name."""
    for label, name in sorted(legend.items(), key=lambda item: len(item[1]), reverse=True):
        if name.strip():
            text = _mention_pattern(name).sub(label, text)
    return text


def anonymize_dialogue(text: str, participants: list[str], language: str) -> AnonymizedDialogue:
    """Rewrite speaker lines as ``<label>: text`` and replace in-text mentions of names."""
    mapping = {name: participant_label(i, language) for i, name in enumerate(participants)}
    lines = text.splitlines()
    speakers = _accepted_speakers(lines)
    rebuilt: list[str] = []
    for index, raw in enumerate(lines):
        spoken = speakers.get(index)
        if spoken and spoken[0] in mapping:
            label, body = mapping[spoken[0]], spoken[1]
            rebuilt.append(f"{label}: {body}" if body else f"{label}:")
        else:
            rebuilt.append(_QUOTE_MARK_RE.sub("", raw))
    result = "\n".join(rebuilt)
    for name in sorted(mapping, key=len, reverse=True):
        result = _mention_pattern(name).sub(mapping[name], result)
    return AnonymizedDialogue(
        text=result,
        participants=list(mapping.values()),
        legend={label: name for name, label in mapping.items()},
    )
