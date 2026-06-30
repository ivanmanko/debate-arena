"""Console demo: run a full debate to stdout, no Telegram required.

    python -m debate_arena.demo "Свобода воли — иллюзия?"

Uses the same DebateFlow + engine as the bot. With no provider keys it runs on the
mock engine; set keys (and unset MOCK_LLM) to see real philosophers argue.
"""

from __future__ import annotations

import asyncio
import sys

from .bot.presenter import TelegramPresenter
from .config import settings
from .debate.engine import get_engine
from .debate.flow import DebateFlow, DebateStream
from .debate.schemas import PersonaSpec, Verdict
from .storage.seed import default_personas


class ConsoleStream(DebateStream):
    async def on_start(self, topic: str, personas: list[PersonaSpec]) -> None:
        print(f"\n🥊 ТЕМА: {topic}")
        print("Участники: " + ", ".join(f"{p.emoji} {p.name}" for p in personas) + "\n")

    async def on_round_header(self, round_no: int, kind: str) -> None:
        print(f"\n────── 🥊 Раунд {round_no} — {kind} ──────\n")

    async def on_turn(self, round_no: int, persona: PersonaSpec, text: str) -> None:
        print(f"{persona.emoji} {persona.name}: {text}\n")

    async def on_verdict(self, verdict: Verdict, personas: list[PersonaSpec]) -> None:
        card = TelegramPresenter.render_verdict_card(verdict, personas)
        # Strip the few HTML tags for console readability.
        for tag in ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>"):
            card = card.replace(tag, "")
        print("\n" + card)


def _specs() -> list[PersonaSpec]:
    return [PersonaSpec(**row) for row in default_personas()[: settings.max_personas]]


async def _main(topic: str) -> None:
    flow = DebateFlow(
        topic=topic,
        personas=_specs(),
        engine=get_engine(),
        stream=ConsoleStream(),
        pacing=False,  # no dramatic delays in the console demo
    )
    await flow.run()


if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]) or "Свобода воли — это иллюзия?"
    asyncio.run(_main(topic))
