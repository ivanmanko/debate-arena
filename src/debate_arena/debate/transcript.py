"""Running transcript: accumulates turns and renders them as context for the next agent."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Turn:
    round_no: int
    persona_name: str
    text: str


@dataclass
class Transcript:
    topic: str
    turns: list[Turn] = field(default_factory=list)

    def add(self, round_no: int, persona_name: str, text: str) -> None:
        self.turns.append(Turn(round_no, persona_name, text))

    def is_empty(self) -> bool:
        return not self.turns

    def render(self, exclude_persona: str | None = None) -> str:
        """Plain-text transcript fed to agents so they can react to each other.

        `exclude_persona` lets a persona see everyone *but* itself if desired; by
        default it sees the full record (including its own prior turns).
        """
        if not self.turns:
            return "(пока никто не высказался)"
        lines: list[str] = []
        for t in self.turns:
            if exclude_persona and t.persona_name == exclude_persona:
                continue
            lines.append(f"[Раунд {t.round_no}] {t.persona_name}: {t.text}")
        return "\n\n".join(lines) if lines else "(пока никто не высказался)"
