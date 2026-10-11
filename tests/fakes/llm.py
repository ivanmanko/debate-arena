"""FakeProvider — scripted ChatProvider with a call journal (ADR-001 port)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from debate_arena.domain.ports import ChatRequest, Completion, StructuredCompletion, Usage
from debate_arena.domain.schemas import ParticipantAssessment, Verdict
from debate_arena.infrastructure.llm.mock import guess_participants


def make_verdict(
    participants: Iterable[str] | None = None, closest: str | None = "__first__"
) -> Verdict:
    """Text-mode verdict by default; pass participant names for a dialogue verdict."""
    names = list(participants or [])
    if closest == "__first__":
        closest = names[0] if names else None
    return Verdict(
        core_question="Тестовый вопрос",
        conclusion="Тестовое заключение",
        narrative="Абзац один обоснования.\n\nАбзац два обоснования.",
        rationality_score=61,
        confidence=70,
        established=["факт 1"],
        probable=["гипотеза 1"],
        uncertain=["белое пятно"],
        claims=[],
        fallacies=[],
        steelman="Сильнейшая версия",
        falsification="Появление данных X",
        so_what="Практический вывод",
        consensus=["аналитики сошлись на X"],
        open_questions=["открыт вопрос Y"],
        participants=[
            ParticipantAssessment(
                name=n,
                truth_proximity=70 - 10 * i,
                position=f"позиция {n}",
                right_about=f"{n} прав в …",
                wrong_about="" if i == 0 else f"{n} ошибается в …",
                note=f"{n}: ок",
            )
            for i, n in enumerate(names)
        ],
        closest_to_truth=closest,
    )


class FakeProvider:
    """Returns scripted replies in order; falls back to a numbered reply."""

    name = "fake"

    def __init__(
        self,
        replies: Iterable[str] | None = None,
        verdict: Verdict | None = None,
        *,
        fail_structured_times: int = 0,
        fail_prewarm: bool = False,
        prewarm_tokens: int = 0,
    ) -> None:
        self.replies: deque[str] = deque(replies or [])
        self.verdict = verdict
        self.fail_structured_times = fail_structured_times
        self.calls: list[ChatRequest] = []
        self.structured_calls: list[ChatRequest] = []
        self.prewarms: list[ChatRequest] = []
        self.fail_prewarm = fail_prewarm
        self.prewarm_tokens = prewarm_tokens

    async def prewarm(self, request: ChatRequest) -> Usage:
        self.prewarms.append(request)
        if self.fail_prewarm:
            raise RuntimeError("scripted prewarm failure")
        return Usage(input_tokens=self.prewarm_tokens)

    async def complete(self, request: ChatRequest) -> Completion:
        self.calls.append(request)
        text = self.replies.popleft() if self.replies else f"reply {len(self.calls)}"
        return Completion(text=text, usage=Usage(input_tokens=10, output_tokens=5))

    async def complete_structured(
        self, request: ChatRequest, schema: type[Verdict]
    ) -> StructuredCompletion[Verdict]:
        self.structured_calls.append(request)
        if self.fail_structured_times > 0:
            self.fail_structured_times -= 1
            from debate_arena.domain.exceptions import InvalidVerdict

            raise InvalidVerdict("scripted failure")
        # Unless scripted, answer like the mock judge: labels or a DIALOGUE marker → dialogue.
        value = self.verdict or make_verdict(guess_participants(request.context))
        return StructuredCompletion(value=value, usage=Usage(), raw=value.model_dump_json())
