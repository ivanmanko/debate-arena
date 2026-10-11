"""Per-run results and the summary the frame-bias eval reports."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from debate_arena.domain.schemas import Verdict

from .cases import Case

# Crude lexical proxy for "the judge named the frame as a party to the dispute": frame-specific
# phrases only, never a bare «рамка» («временные рамки», «в рамках данных»). Only the new
# prompts teach this wording, so the share is a compliance check, not a before/after metric.
_FRAME_RE = re.compile(
    r"(?<!в )рамк\w*\s+(?:манифест|разбор|анализ)"
    r"|сам\w*\s+(?:рамк|манифест)"
    r"|рамк\w*[^.]{0,40}сторон\w*\s+спора"
    r"|оспарива\w*\s+(?:сам\w*\s+|исходн\w*\s+)?(?:рамк|манифест|основани|допущени)"
    r"|(?<!на )основани\w*\s+(?:сам\w*\s+)?манифест"
    r"|допущени\w*\s+манифест",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RunResult:
    case_id: str
    challenger_first: bool
    challenger_score: int
    defender_score: int
    closest_is_challenger: bool
    frame_flagged: bool


@dataclass(frozen=True)
class Summary:
    n: int
    mean_challenger: float
    mean_defender: float
    mean_gap: float  # defender − challenger; > 0 means the challenger is scored lower
    # Challenger score speaking first − speaking second. Speaking first also means the
    # anonymised label «Собеседник А», so this mixes order and label effects.
    order_effect: float | None
    challenger_closest_share: float
    frame_flag_share: float


def frame_flagged(verdict: Verdict) -> bool:
    texts = [
        verdict.conclusion,
        verdict.narrative,
        *verdict.uncertain,
        *verdict.open_questions,
        *(p.right_about for p in verdict.participants),
        *(p.wrong_about for p in verdict.participants),
    ]
    return any(_FRAME_RE.search(text) for text in texts)


def result_from_verdict(
    case: Case, challenger_first: bool, verdict: Verdict, legend: dict[str, str]
) -> RunResult:
    """``legend`` maps the judge's labels back to names; empty when anonymisation is off."""
    scores = {legend.get(p.name, p.name): p.truth_proximity for p in verdict.participants}
    closest = legend.get(verdict.closest_to_truth or "", verdict.closest_to_truth)
    return RunResult(
        case_id=case.id,
        challenger_first=challenger_first,
        challenger_score=scores[case.challenger.name],
        defender_score=scores[case.defender.name],
        closest_is_challenger=closest == case.challenger.name,
        frame_flagged=frame_flagged(verdict),
    )


def summarize(results: Sequence[RunResult]) -> Summary:
    if not results:
        raise ValueError("no results to summarize")
    first = [r.challenger_score for r in results if r.challenger_first]
    second = [r.challenger_score for r in results if not r.challenger_first]
    return Summary(
        n=len(results),
        mean_challenger=fmean(r.challenger_score for r in results),
        mean_defender=fmean(r.defender_score for r in results),
        mean_gap=fmean(r.defender_score - r.challenger_score for r in results),
        order_effect=fmean(first) - fmean(second) if first and second else None,
        challenger_closest_share=fmean(r.closest_is_challenger for r in results),
        frame_flag_share=fmean(r.frame_flagged for r in results),
    )


def case_gaps(results: Sequence[RunResult]) -> dict[str, float]:
    """Mean defender − challenger gap per case, over both orders and all repeats.

    The cases have no ground truth, so an absolute gap may be deserved; the eval's primary
    metric is how this per-case gap moves between prompt versions on the same cases.
    """
    by_case: dict[str, list[int]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r.defender_score - r.challenger_score)
    return {case_id: fmean(gaps) for case_id, gaps in by_case.items()}
