"""Compare two frame-bias results files case by case — the eval's primary metric.

    uv run python -m evals.frame_bias.compare before after

The cases have no ground truth, so an absolute gap may be deserved; what the eval reads is
how each case's gap (defender − challenger, averaged over both orders) moves between prompt
versions run on the same cases.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean

RESULTS = Path(__file__).parent / "results"


def _load(label: str) -> dict:
    return json.loads((RESULTS / f"{label}.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-case gap delta between two eval runs")
    parser.add_argument("base", help="results label, e.g. before")
    parser.add_argument("new", help="results label, e.g. after")
    args = parser.parse_args(argv)
    base, new = _load(args.base), _load(args.new)
    for run in (base, new):
        print(
            f"{run['label']}: {run.get('git_revision', '?')} "
            f"prompts={str(run.get('prompts_sha256', '?'))[:12]} "
            f"model={run['model_judge']} rounds={run['rounds']} repeats={run['repeats']}"
        )
    shared = [case for case in base["case_gaps"] if case in new["case_gaps"]]
    print(f"\n{'case':<16}{args.base:>10}{args.new:>10}{'delta':>10}")
    deltas = []
    for case in shared:
        before, after = base["case_gaps"][case], new["case_gaps"][case]
        deltas.append(after - before)
        print(f"{case:<16}{before:>+10.1f}{after:>+10.1f}{after - before:>+10.1f}")
    if deltas:
        print(f"{'mean':<16}{'':>20}{fmean(deltas):>+10.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
