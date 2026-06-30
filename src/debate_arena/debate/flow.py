"""DebateFlow — owns the round loop, pacing and the judge step.

This is the "Flow on the outside" from the design: it drives rounds, feeds each
persona the accumulated transcript so they react to each other, streams every turn
out via callbacks (for showmanship), then runs the judge. Agents/Crew live *inside*
a single turn (see engine.py).

The presenter is injected as a `DebateStream` of async callbacks, so the flow stays
free of any Telegram knowledge and is trivially testable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ..config import settings
from .engine import TurnEngine
from .schemas import PersonaSpec, Verdict
from .transcript import Transcript


class DebateStream:
    """Async hooks the flow calls at each dramatic beat. Defaults are no-ops."""

    async def on_start(self, topic: str, personas: list[PersonaSpec]) -> None: ...
    async def on_round_header(self, round_no: int, kind: str) -> None: ...
    async def on_typing(self, persona: PersonaSpec) -> None: ...
    async def on_turn(self, round_no: int, persona: PersonaSpec, text: str) -> None: ...
    async def on_verdict(self, verdict: Verdict, personas: list[PersonaSpec]) -> None: ...
    async def on_error(self, error: Exception) -> None: ...


PersistTurn = Callable[[int, PersonaSpec, str], Awaitable[None]]
PersistVerdict = Callable[[Verdict], Awaitable[None]]


_ROUND_KINDS = {1: "Тезисы"}


def _round_kind(round_no: int) -> str:
    return _ROUND_KINDS.get(round_no, "Контраргументы")


class DebateFlow:
    def __init__(
        self,
        topic: str,
        personas: list[PersonaSpec],
        engine: TurnEngine,
        stream: DebateStream,
        *,
        persist_turn: PersistTurn | None = None,
        persist_verdict: PersistVerdict | None = None,
        max_rounds: int | None = None,
        pacing: bool = True,
    ) -> None:
        self.topic = topic
        self.personas = personas
        self.engine = engine
        self.stream = stream
        self.persist_turn = persist_turn
        self.persist_verdict = persist_verdict
        self.max_rounds = max_rounds or settings.max_rounds
        self.pacing = pacing
        self.transcript = Transcript(topic=topic)

    async def run(self) -> Verdict:
        await self.stream.on_start(self.topic, self.personas)

        for round_no in range(1, self.max_rounds + 1):
            await self.stream.on_round_header(round_no, _round_kind(round_no))
            await self._pause(settings.pause_after_header)

            for persona in self.personas:
                await self.stream.on_typing(persona)
                await self._pause(settings.typing_per_turn)

                text = await self.engine.persona_turn(
                    persona,
                    self.topic,
                    self.transcript.render(),
                    round_no,
                )

                self.transcript.add(round_no, persona.name, text)
                if self.persist_turn:
                    await self.persist_turn(round_no, persona, text)
                await self.stream.on_turn(round_no, persona, text)
                await self._pause(settings.pause_between_turns)

        verdict = await self.engine.judge(self.topic, self.transcript.render(), self.personas)
        if self.persist_verdict:
            await self.persist_verdict(verdict)
        await self.stream.on_verdict(verdict, self.personas)
        return verdict

    async def _pause(self, seconds: float) -> None:
        if self.pacing and seconds > 0:
            await asyncio.sleep(seconds)
