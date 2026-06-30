"""Build a persona's LLM + prompts for a single debate turn.

The character lives in the system prompt (`system_principles` from the DB). Round 1
is opening theses; later rounds are rebuttals — the user message changes per round.

We deliberately call the model directly (`LLM.call`) rather than running a CrewAI
Agent/Task: a debate turn is a single-shot generation with no tools, and CrewAI's
ReAct agent executor expects a `Thought/Action/Final Answer` format that chat models
(e.g. DeepSeek) don't follow, which sends it into endless parse-retry loops. We still
go through CrewAI's `LLM`/LiteLLM layer, so multiprovider support is intact.
"""

from __future__ import annotations

from typing import Any

from ..config import settings
from ..llm.providers import build_llm
from .schemas import PersonaSpec

_GOAL = (
    "Убедить аудиторию и судью в своей позиции по теме дебатов, реагируя на "
    "оппонентов и оставаясь в характере и в рамках Манифеста."
)


def build_persona_llm(spec: PersonaSpec) -> Any:
    return build_llm(spec.model, spec.temperature, settings.turn_max_tokens)


def persona_system_prompt(spec: PersonaSpec) -> str:
    return f"{spec.system_principles}\n\nТвоя роль в дебатах: {spec.role}.\nТвоя цель: {_GOAL}"


def turn_description(spec: PersonaSpec, topic: str, transcript_text: str, round_no: int) -> str:
    if round_no == 1:
        instruction = (
            "Это РАУНД 1 — тезисы. Сформулируй свою исходную позицию по теме "
            "и один сильный аргумент в её пользу."
        )
    else:
        instruction = (
            f"Это РАУНД {round_no} — контраргументы. Внимательно прочитай, что "
            "сказали оппоненты, и ударь по самому слабому месту их аргументации. "
            "Можешь усилить или скорректировать свою позицию под весом фактов."
        )

    return (
        f"Тема дебатов: «{topic}».\n\n"
        f"Транскрипт дебатов на данный момент:\n{transcript_text}\n\n"
        f"{instruction}\n\n"
        f"Ответь как {spec.name}, строго в своём характере, СТРОГО не более 4–5 "
        "предложений. Уложись в этот объём целиком — не начинай мысль, которую не "
        "успеешь закончить, и обязательно завершай реплику точкой, а не на полуслове. "
        f"Пиши ИСКЛЮЧИТЕЛЬНО на этом языке: {settings.language}. Без markdown, без "
        "списков, без служебных пометок вроде «Thought:» или «Final Answer:», без "
        "указания своего имени в начале — только сама реплика."
    )
