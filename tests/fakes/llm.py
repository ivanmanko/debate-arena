"""FakeProvider — scripted ChatProvider with a call journal (ADR-001 port)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from debate_arena.domain.ports import ChatRequest, Completion, StructuredCompletion, Usage
from debate_arena.domain.schemas import AnalystScore, Verdict


def make_verdict(analyst_names: Iterable[str], most_rational: str | None = None) -> Verdict:
    names = list(analyst_names)
    return Verdict(
        core_question="Тестовый вопрос",
        conclusion="Тестовое заключение",
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
        scores=[
            AnalystScore(
                analyst=n, rigor=7, evidence=6, charity=8, responsiveness=7, note=f"{n}: ок"
            )
            for n in names
        ],
        most_rational=most_rational or names[0],
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
    ) -> None:
        self.replies: deque[str] = deque(replies or [])
        self.verdict = verdict
        self.fail_structured_times = fail_structured_times
        self.calls: list[ChatRequest] = []
        self.structured_calls: list[ChatRequest] = []

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
        from debate_arena.domain.analysts import ANALYSTS

        value = self.verdict or make_verdict(a.name for a in ANALYSTS)
        return StructuredCompletion(value=value, usage=Usage(), raw=value.model_dump_json())
