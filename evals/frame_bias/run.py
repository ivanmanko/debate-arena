"""Run the frame-bias eval and write measured results.

    uv run python -m evals.frame_bias.run --label before [--repeats 1] [--rounds 2]
        [--model provider/model] [--concurrency 2]

Every case runs in both speaking orders through the real ``AnalysisService``: with provider
keys that means live models and real money (2 × cases × repeats analyses of 3×rounds+1
calls each); without keys it falls back to mock, which only proves the plumbing. Analyses
go to a separate SQLite file, not the app database; results go to
``evals/frame_bias/results/<label>.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from debate_arena.application.service import AnalysisService
from debate_arena.config import get_settings
from debate_arena.infrastructure.db.repo import SqlAlchemyAnalysisRepository
from debate_arena.infrastructure.db.session import Database
from debate_arena.infrastructure.llm.factory import ProviderRegistry

from .cases import CASES, Case, build_dialogue
from .metrics import RunResult, Summary, result_from_verdict, summarize

HERE = Path(__file__).parent
DB_URL = f"sqlite+aiosqlite:///{HERE / 'runs.db'}"


async def _one(
    service: AnalysisService,
    case: Case,
    challenger_first: bool,
    rounds: int,
    model: str | None,
    gate: asyncio.Semaphore,
) -> tuple[RunResult, dict]:
    async with gate:
        started = await service.start_analysis(
            text=build_dialogue(case, challenger_first=challenger_first),
            question=None,
            rounds=rounds,
            model=model,
            mode="dialogue",
        )
        await service.wait(started.id)
        record = await service.get(started.id)
    if record.verdict is None:
        raise RuntimeError(f"{case.id}: analysis #{record.id} {record.status}: {record.error}")
    result = result_from_verdict(case, challenger_first, record.verdict, started.legend)
    detail = {
        **asdict(result),
        "analysis_id": record.id,
        "legend": started.legend,
        "duration_ms": record.duration_ms,
        "verdict": record.verdict.model_dump(),
    }
    return result, detail


def _print_table(results: list[RunResult], summary: Summary) -> None:
    print(f"\n{'case':<16}{'order':<8}{'chal':>6}{'def':>6}{'gap':>6}  closest  frame")
    for r in results:
        order = "C→D" if r.challenger_first else "D→C"
        gap = r.defender_score - r.challenger_score
        closest = "chal" if r.closest_is_challenger else "def"
        print(
            f"{r.case_id:<16}{order:<8}{r.challenger_score:>6}{r.defender_score:>6}{gap:>6}"
            f"  {closest:<7}  {'yes' if r.frame_flagged else 'no'}"
        )
    order = "n/a" if summary.order_effect is None else f"{summary.order_effect:+.1f}"
    print(
        f"\nn={summary.n}  challenger={summary.mean_challenger:.1f}  "
        f"defender={summary.mean_defender:.1f}  gap={summary.mean_gap:+.1f}  "
        f"order_effect={order}  challenger_closest={summary.challenger_closest_share:.0%}  "
        f"frame_named={summary.frame_flag_share:.0%}"
    )


async def _main(label: str, repeats: int, rounds: int, model: str | None, concurrency: int) -> int:
    settings = get_settings()
    database = Database(DB_URL)
    await database.init()
    registry = ProviderRegistry(settings)
    service = AnalysisService(
        repo=SqlAlchemyAnalysisRepository(database.sessions),
        provider_factory=registry.get,
        settings=settings,
    )
    if settings.use_mock:
        print("(mock-режим: числа ничего не измеряют, проверяется только обвязка)", file=sys.stderr)
    gate = asyncio.Semaphore(concurrency)
    jobs = [
        _one(service, case, first, rounds, model, gate)
        for _ in range(repeats)
        for case in CASES
        for first in (True, False)
    ]
    try:
        outcomes = await asyncio.gather(*jobs, return_exceptions=True)
    finally:
        await registry.aclose()
        await database.dispose()

    failures = [repr(o) for o in outcomes if isinstance(o, BaseException)]
    done = [o for o in outcomes if not isinstance(o, BaseException)]
    for failure in failures:
        print(f"💥 {failure}", file=sys.stderr)
    if not done:
        return 1
    results = [result for result, _ in done]
    summary = summarize(results)
    _print_table(results, summary)

    out = HERE / "results" / f"{label}.json"
    out.parent.mkdir(exist_ok=True)
    payload = {
        "label": label,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mock": settings.use_mock,
        "model_analyst": model or settings.effective_model_analyst,
        "model_judge": model or settings.effective_model_judge,
        "rounds": rounds,
        "repeats": repeats,
        "summary": asdict(summary),
        "failures": failures,
        "runs": [detail for _, detail in done],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n→ {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frame-bias eval (live models cost money)")
    parser.add_argument("--label", required=True, help="results file name, e.g. before / after")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--model", default=None, help="provider/model for analysts and judge")
    parser.add_argument("--concurrency", type=int, default=2, help="analyses at once")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(_main(args.label, args.repeats, args.rounds, args.model, args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
