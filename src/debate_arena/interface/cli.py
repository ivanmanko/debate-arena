"""Console run: analyse a text and print the rounds and verdict to stdout.

    uv run python -m debate_arena.interface.cli "текст" [--rounds N] [--model provider/model]

Uses the same service, database and providers as the web app (mock without keys).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ..application.service import AnalysisService
from ..config import get_settings
from ..domain.schemas import Verdict
from ..infrastructure.db.repo import SqlAlchemyAnalysisRepository
from ..infrastructure.db.session import Database
from ..infrastructure.llm.factory import ProviderRegistry


def _print_verdict(verdict: Verdict) -> None:
    print("\n⚖️  ВЕРДИКТ СУДЬИ")
    print(f"Вопрос: {verdict.core_question}")
    print(f"Заключение: {verdict.conclusion}")
    print(
        f"Рациональность материала: {verdict.rationality_score}/100 · "
        f"уверенность судьи: {verdict.confidence}%"
    )
    for title, items in (
        ("Установлено", verdict.established),
        ("Вероятно", verdict.probable),
        ("Неопределённо", verdict.uncertain),
    ):
        print(f"\n{title}:")
        for item in items or ["—"]:
            print(f"  • {item}")
    if verdict.claims:
        print("\nУтверждения:")
        for claim in verdict.claims:
            print(f"  [{claim.status}] {claim.claim} — {claim.note}")
    if verdict.fallacies:
        print("\nУловки:")
        for fallacy in verdict.fallacies:
            print(f"  • {fallacy.name}: «{fallacy.quote}» — {fallacy.explanation}")
    print(f"\nСильнейшая версия: {verdict.steelman}")
    print(f"Что изменит вердикт: {verdict.falsification}")
    print(f"Что из этого следует: {verdict.so_what}")
    if verdict.participants:
        print(f"\nБлиже всего к истине: {verdict.closest_to_truth}")
        for p in sorted(verdict.participants, key=lambda x: x.truth_proximity, reverse=True):
            print(f"  {p.truth_proximity:>3} — {p.name}: {p.position}")
            print(f"       ✔ {p.right_about}")
            if p.wrong_about:
                print(f"       ✘ {p.wrong_about}")
    for title, items in (
        ("Аналитики сошлись", verdict.consensus),
        ("Открыто", verdict.open_questions),
    ):
        if items:
            print(f"\n{title}:")
            for item in items:
                print(f"  • {item}")


async def _main(
    text: str, question: str | None, rounds: int | None, model: str | None, mode: str
) -> int:
    settings = get_settings()
    database = Database(settings.database_url)
    await database.init()
    registry = ProviderRegistry(settings)
    service = AnalysisService(
        repo=SqlAlchemyAnalysisRepository(database.sessions),
        provider_factory=registry.get,
        settings=settings,
    )
    if settings.use_mock:
        print("(mock-режим: ключи провайдеров не заданы)\n", file=sys.stderr)
    analysis_id = await service.start(
        text=text,
        question=question,
        rounds=rounds,
        model=model,
        mode=mode,  # type: ignore[arg-type]
    )
    status = "failed"
    try:
        async for event in service.subscribe(analysis_id):
            if event.type == "round":
                print(f"\n────── Раунд {event.data['round_no']} · {event.data['kind']} ──────")
            elif event.type == "turn":
                print(f"\n{event.data['emoji']} {event.data['analyst']}:\n{event.data['text']}")
            elif event.type == "verdict":
                _print_verdict(Verdict.model_validate(event.data))
            elif event.type == "error":
                print(f"\n💥 {event.data['message']}", file=sys.stderr)
            elif event.type == "done":
                status = event.data["status"]
                print(f"\n[{status}] разбор #{analysis_id}, {event.data.get('duration_ms')} мс")
    finally:
        await registry.aclose()
        await database.dispose()
    return 0 if status == "finished" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rational Check — разбор текста из консоли")
    parser.add_argument("text", nargs="+", help="текст для разбора")
    parser.add_argument("--question", "-q", default=None, help="что именно проверить")
    parser.add_argument("--rounds", "-r", type=int, default=None)
    parser.add_argument(
        "--model", "-m", default=None, help="provider/model, например anthropic/claude-opus-5-5"
    )
    parser.add_argument("--mode", choices=["auto", "text", "dialogue"], default="auto")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(
        _main(" ".join(args.text), args.question, args.rounds, args.model, args.mode)
    )


if __name__ == "__main__":
    raise SystemExit(main())
