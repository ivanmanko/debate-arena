"""Neutral judge LLM + structured Pydantic verdict.

The judge runs on its own model (a separate, neutral voice) and scores purely from
the transcript against a fixed rubric — logic / evidence / rhetoric / responsiveness.
Like the personas, it's a direct `LLM.call` (no ReAct agent). We ask for raw JSON in
the prompt and parse it ourselves rather than passing a Pydantic `response_format`:
not every provider supports schema-typed response formats (e.g. DeepSeek rejects it
with "This response_format type is unavailable now").
"""

from __future__ import annotations

from typing import Any

from ..config import settings
from ..llm.providers import build_llm
from .schemas import PersonaSpec, Verdict

JUDGE_BACKSTORY = """
Ты — беспристрастный судья интеллектуальных дебатов. Ты НЕ участник и не имеешь своей
позиции по теме. Ты судишь СТРОГО по транскрипту и только по нему, по фиксированной рубрике:
- logic (логика): связность и валидность рассуждения, отсутствие логических ошибок;
- evidence (факты): опора на проверяемые факты, бремя доказывания, бритва Оккама;
- rhetoric (риторика): ясность, сила и убедительность подачи;
- responsiveness (реакция): насколько участник реально отвечал оппонентам, а не монологизировал.
Каждый критерий — целое число 1..10. Демагогия (ad hominem, соломенное чучело, ложная
дилемма) штрафуется. Будь честен и конкретен.
""".strip()


def build_judge_llm() -> Any:
    return build_llm(
        settings.model_judge,
        temperature=settings.judge_temperature,
        max_tokens=settings.judge_max_tokens,
    )


def judge_description(topic: str, transcript_text: str, personas: list[PersonaSpec]) -> str:
    names = ", ".join(p.name for p in personas)
    return (
        f"Тема дебатов: «{topic}».\n"
        f"Участники: {names}.\n\n"
        f"Полный транскрипт:\n{transcript_text}\n\n"
        "Оцени КАЖДОГО участника по рубрике (logic, evidence, rhetoric, responsiveness, "
        "каждый 1..10) и дай по нему одну ёмкую фразу-вывод (note). Затем выбери winner "
        "(имя самого убедительного участника) и напиши punchline — одну ехидную, остроумную "
        "фразу-итог всего спора. Имена участников бери ровно как в транскрипте. "
        f"Поля note и punchline пиши на этом языке: {settings.language}.\n\n"
        "Верни ТОЛЬКО валидный JSON-объект ровно такой формы:\n"
        '{"scores": [{"persona": "...", "logic": 1-10, "evidence": 1-10, '
        '"rhetoric": 1-10, "responsiveness": 1-10, "note": "..."}], '
        '"winner": "...", "punchline": "..."}\n'
        "Без markdown, без пояснений до или после JSON."
    )


__all__ = ["Verdict", "build_judge_llm", "judge_description", "judge_system_prompt"]


def judge_system_prompt() -> str:
    return JUDGE_BACKSTORY
