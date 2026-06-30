"""Turn engine: the thing that actually produces a persona's reply and the verdict.

Two implementations behind one async interface:
- `CrewAITurnEngine` — real LLM calls via CrewAI's `LLM`/LiteLLM (a direct chat
  completion per turn), run in a worker thread so the blocking call never stalls
  the aiogram loop. We call the model directly rather than via a CrewAI Agent/Task:
  a debate turn is single-shot with no tools, and the ReAct agent executor loops on
  `Thought/Action/Final Answer` parse errors with plain chat models like DeepSeek.
- `MockTurnEngine` — canned, deterministic output so the bot, demo and tests run
  with no API keys.

`get_engine()` picks based on config (mock if forced or no provider keys present).
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Protocol

from ..config import settings
from .judge import build_judge_llm, judge_description, judge_system_prompt
from .personas import build_persona_llm, persona_system_prompt, turn_description
from .schemas import PersonaScore, PersonaSpec, Verdict


class TurnEngine(Protocol):
    async def persona_turn(
        self,
        persona: PersonaSpec,
        topic: str,
        transcript_text: str,
        round_no: int,
    ) -> str: ...

    async def judge(
        self,
        topic: str,
        transcript_text: str,
        personas: list[PersonaSpec],
    ) -> Verdict: ...


class CrewAITurnEngine:
    """One direct `LLM.call` per turn via CrewAI/LiteLLM. Real models, real money."""

    async def persona_turn(
        self,
        persona: PersonaSpec,
        topic: str,
        transcript_text: str,
        round_no: int,
    ) -> str:
        return await asyncio.to_thread(self._run_turn, persona, topic, transcript_text, round_no)

    def _run_turn(
        self, persona: PersonaSpec, topic: str, transcript_text: str, round_no: int
    ) -> str:
        llm = build_persona_llm(persona)
        messages = [
            {"role": "system", "content": persona_system_prompt(persona)},
            {"role": "user", "content": turn_description(persona, topic, transcript_text, round_no)},
        ]
        return _clean_turn(str(llm.call(messages)))

    async def judge(self, topic: str, transcript_text: str, personas: list[PersonaSpec]) -> Verdict:
        return await asyncio.to_thread(self._run_judge, topic, transcript_text, personas)

    def _run_judge(self, topic: str, transcript_text: str, personas: list[PersonaSpec]) -> Verdict:
        llm = build_judge_llm()
        messages = [
            {"role": "system", "content": judge_system_prompt()},
            {"role": "user", "content": judge_description(topic, transcript_text, personas)},
        ]
        raw = str(llm.call(messages))
        return _parse_verdict(raw)


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?…]+[»\"')]?\s*$")
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?…]+[»\"')]?(?=\s|$)")


def _clean_turn(text: str) -> str:
    """Strip reasoning-model <think> blocks, stray ReAct scaffolding, and a
    dangling half-sentence left by hitting the max_tokens budget mid-thought."""
    text = _THINK_RE.sub("", text)
    for marker in ("Final Answer:", "Финальный ответ:"):
        if marker in text:
            text = text.split(marker, 1)[1]
    return _trim_to_sentence_boundary(text.strip())


def _trim_to_sentence_boundary(text: str) -> str:
    """If max_tokens cut the reply off mid-sentence, drop the dangling fragment
    and end on the last complete sentence instead of a broken half-thought."""
    if not text or _SENTENCE_END_RE.search(text):
        return text
    matches = list(_SENTENCE_BOUNDARY_RE.finditer(text))
    if not matches:
        return text  # no sentence boundary found at all — better than nothing
    trimmed = text[: matches[-1].end()].strip()
    return trimmed or text


def _parse_verdict(raw: str) -> Verdict:
    """Parse the judge's JSON verdict, tolerating code fences / surrounding prose."""
    raw = _THINK_RE.sub("", raw).strip()
    try:
        return Verdict.model_validate_json(raw)
    except Exception:
        pass
    # Fall back to the first balanced {...} block in the text.
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return Verdict.model_validate(json.loads(raw[start : end + 1]))
    raise ValueError(f"Judge did not return valid JSON verdict: {raw[:200]!r}")


_OPENERS = {
    "Ницше": "Ваша «истина» здесь — лишь самая удобная из иллюзий. Разберём её на части: {topic} — это вопрос воли, а не уютной морали стада.",
    "Поппер": "Прежде чем спорить о «{topic}», спрошу главное: каким наблюдением вашу позицию вообще можно было бы опровергнуть? Если ничем — это не аргумент, а вера.",
    "Сократ": "Скажи мне, друг: когда ты говоришь о «{topic}», что именно ты имеешь в виду? Боюсь, под одним словом мы прячем три разных смысла.",
}
_REBUTTALS = {
    "Ницше": "Ты прячешься за осторожностью, как за щитом. Но кто не рискует утверждать — тот и не мыслит.",
    "Поппер": "Красивая фраза — но непроверяемая. Дай мне предсказание, которое можно провалить, иначе это поэзия, а не довод.",
    "Сократ": "Любопытно. А не противоречит ли это тому, что ты сам сказал минутой раньше? Договорим твою мысль до конца.",
}


class MockTurnEngine:
    """Deterministic-ish canned output. No network, no keys, no cost."""

    async def persona_turn(
        self,
        persona: PersonaSpec,
        topic: str,
        transcript_text: str,
        round_no: int,
    ) -> str:
        await asyncio.sleep(0.05)  # mimic a little latency
        if round_no == 1:
            tmpl = _OPENERS.get(
                persona.name,
                "Моя позиция по «{topic}» проста и опирается на проверяемые факты.",
            )
        else:
            tmpl = _REBUTTALS.get(
                persona.name,
                "Отвечу оппонентам: их главный довод держится на недоказанном допущении.",
            )
        return tmpl.format(topic=topic)

    async def judge(self, topic: str, transcript_text: str, personas: list[PersonaSpec]) -> Verdict:
        await asyncio.sleep(0.05)
        rng = random.Random(f"{topic}|{len(transcript_text)}")
        scores: list[PersonaScore] = []
        for p in personas:
            scores.append(
                PersonaScore(
                    persona=p.name,
                    logic=rng.randint(5, 10),
                    evidence=rng.randint(5, 10),
                    rhetoric=rng.randint(5, 10),
                    responsiveness=rng.randint(5, 10),
                    note=f"{p.name}: держал характер и оставался в рамках Манифеста.",
                )
            )
        winner = max(scores, key=lambda s: s.total).persona
        return Verdict(
            scores=scores,
            winner=winner,
            punchline=f"Все спорили красиво, но {winner} убедил судью, не нарушив бритву Оккама.",
        )


def get_engine() -> TurnEngine:
    if settings.use_mock:
        return MockTurnEngine()
    return CrewAITurnEngine()
