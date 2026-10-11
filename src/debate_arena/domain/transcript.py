"""Running transcript rendered as context for later rounds and the judge."""

from __future__ import annotations

from dataclasses import dataclass, field

EMPTY_MARKER = "(пока никто не высказался)"


@dataclass(frozen=True)
class Turn:
    round_no: int
    analyst: str
    text: str


@dataclass
class Transcript:
    turns: list[Turn] = field(default_factory=list)

    def add(self, round_no: int, analyst: str, text: str) -> None:
        self.turns.append(Turn(round_no, analyst, text))

    def render(self) -> str:
        if not self.turns:
            return EMPTY_MARKER
        return "\n\n".join(f"[Раунд {t.round_no}] {t.analyst}: {t.text}" for t in self.turns)
