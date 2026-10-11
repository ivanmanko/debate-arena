"""Mock provider: canned, deterministic replies so the UI, bot and tests run with no keys."""

from __future__ import annotations

import asyncio

from ...domain.analysts import ANALYSTS
from ...domain.ports import ChatRequest, Completion, ModelT, StructuredCompletion, Usage
from ...domain.schemas import AnalystScore, ClaimAssessment, Fallacy, Verdict

CROSS_MARKER = "Принимаю:"

_FIRST = {
    "ФАКТЫ": (
        "Ядро текста — одно сильное утверждение и один аргумент в его пользу. Проверяемого в нём "
        "мало: автор ссылается на «очевидность», а не на данные. Бремя доказывания лежит на "
        "авторе и не выполнено. Не могу проверить главный тезис без источников; для проверки "
        "нужны измерения, а не впечатления. Мой вывод опровергло бы наличие воспроизводимых "
        "данных в пользу тезиса."
    ),
    "СТРУКТУРА": (
        "Структура аргумента: скрытая посылка «если А выглядит как Б, то А есть Б». Из неё вывод "
        "следует, но сама посылка не обоснована — это обобщение с одного наблюдения. Уловка: "
        "ложная дилемма («либо так, либо все лгут»). Плохой аргумент сам по себе не делает вывод "
        "ложным, но и не поддерживает его."
    ),
    "СИЛЬНЕЙШАЯ": (
        "Сильнейшая версия позиции автора: наблюдение действительно есть, и его стоит объяснить. "
        "Альтернативное объяснение требует меньше допущений и согласуется с известными фактами, "
        "поэтому по бритве Оккама рабочей считается альтернатива. Границы: тезис автора "
        "работает только в узком масштабе наблюдения."
    ),
}

_CROSS = (
    "Принимаю:\nДовод коллеги о скрытой посылке точен — моя первая реплика его не учитывала, и "
    "он сильнее моей формулировки.\n\n"
    "Оспариваю:\nФраза «проверяемого мало» слишком мягкая: фактов в тексте нет вовсе, а это "
    "нарушение пункта 3 Манифеста, а не «недостаток».\n\n"
    "Уточняю:\nТекст содержит одно непроверенное утверждение, подкреплённое наблюдением, "
    "которое лучше объясняется более простой моделью. Опровергло бы это появление "
    "воспроизводимых данных в пользу тезиса автора."
)


class MockProvider:
    name = "mock"

    def __init__(self, delay_s: float = 0.4) -> None:
        self._delay = delay_s

    async def complete(self, request: ChatRequest) -> Completion:
        await asyncio.sleep(self._delay)
        if CROSS_MARKER in request.user:
            text = _CROSS
        else:
            key = next((k for k in _FIRST if k in request.system), "ФАКТЫ")
            text = _FIRST[key]
        return Completion(text=text, usage=Usage(input_tokens=0, output_tokens=0))

    async def complete_structured(
        self, request: ChatRequest, schema: type[ModelT]
    ) -> StructuredCompletion[ModelT]:
        await asyncio.sleep(self._delay)
        verdict = mock_verdict()
        return StructuredCompletion(value=verdict, usage=Usage(), raw=verdict.model_dump_json())  # type: ignore[arg-type]


def mock_verdict() -> Verdict:
    names = [a.name for a in ANALYSTS]
    return Verdict(
        core_question="Подтверждается ли главный тезис текста проверяемыми фактами?",
        conclusion=(
            "Нет. Текст опирается на одно наблюдение и «очевидность»; более простая модель "
            "объясняет то же наблюдение без дополнительных допущений. Самое рациональное — "
            "считать тезис недоказанным и предпочесть альтернативное объяснение."
        ),
        rationality_score=28,
        confidence=82,
        established=["Наблюдение, с которого начинает автор, действительно существует."],
        probable=["Наблюдение объясняется известной более простой моделью."],
        uncertain=["Мотивы автора и контекст написания — вне рассмотрения."],
        claims=[
            ClaimAssessment(
                claim="Главный тезис автора",
                status="unsupported",
                note="Нет данных, только впечатление.",
            ),
            ClaimAssessment(
                claim="Исходное наблюдение верно",
                status="supported",
                note="Воспроизводимо и общеизвестно.",
            ),
        ],
        fallacies=[
            Fallacy(
                name="Ложная дилемма",
                quote="«либо так, либо все лгут»",
                explanation="Спектр объяснений сведён к двум крайностям.",
            )
        ],
        steelman="Наблюдение реально и заслуживает объяснения; автор прав, что его нельзя отмахнуть.",
        falsification="Воспроизводимые измерения, подтверждающие тезис автора.",
        so_what="Не принимать тезис на веру; при желании проверить — измерить, а не смотреть.",
        scores=[
            AnalystScore(
                analyst=names[0],
                rigor=8,
                evidence=9,
                charity=7,
                responsiveness=8,
                note="Точно про бремя доказывания.",
            ),
            AnalystScore(
                analyst=names[1],
                rigor=9,
                evidence=7,
                charity=7,
                responsiveness=8,
                note="Нашёл скрытую посылку.",
            ),
            AnalystScore(
                analyst=names[2],
                rigor=8,
                evidence=7,
                charity=9,
                responsiveness=7,
                note="Лучший стилмен.",
            ),
        ],
        most_rational=names[1],
    )
