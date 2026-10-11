"""Hiding people's names behind labels before the models and the DB (SPEC §4.0b, ADR-008).

There is no speaker parser: the user lists the names to hide, and every mention of them —
declensions and Cyrillic transliterations included — becomes ``Собеседник А``, ``Б``, …
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_RU_LETTERS = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЭЮЯ"
_EN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_VOWELS = set("аеёиоуыэюяaeiouy")
# A name stem shorter than this is replaced only as an exact word (SPEC §4.0b).
_MIN_STEM = 3


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


def normalize_names(names: Sequence[str]) -> list[str]:
    """Strip, drop blanks and case-insensitive duplicates, keep the user's order."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in names:
        name = raw.strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


@dataclass(frozen=True)
class HiddenNames:
    text: str
    question: str | None
    legend: dict[str, str]  # label → real name; never persisted server-side


def hide_names(text: str, question: str | None, names: Sequence[str], language: str) -> HiddenNames:
    """Replace each listed name found in ``text`` or ``question`` with the next label.

    Names that occur nowhere get no label, so the labels stay contiguous (А, Б, …).
    """
    legend: dict[str, str] = {}
    for name in normalize_names(names):
        pattern = _mention_pattern(name)
        if pattern.search(text) or (question and pattern.search(question)):
            legend[participant_label(len(legend), language)] = name
    return HiddenNames(
        text=anonymize_mentions(text, legend),
        question=anonymize_mentions(question, legend) if question else question,
        legend=legend,
    )
