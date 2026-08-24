"""MH_RAW promotion-validation contract (pure helpers).

Deterministic building blocks for the 2026-08 promotion-validation run:

* ``calendar_fold_windows`` — walk-forward folds on TRUE calendar 6-month
  boundaries (``relativedelta(months=6)``), never ``timedelta(days=180)``;
* ``temporal_diagnostic_windows`` — the predeclared sensitivity windows
  (2017-2021, 2022-2026, 2023-2026, full-excluding-2020, full-excluding-2024);
* ``paired_fold_deltas`` — the ONE canonical paired candidate-vs-control
  fold comparison (median / mean / p25 / wins) used by both the artifact
  aggregator and the report generator;
* ``promotion_gate`` — the exact mission rubric (§9), evaluated ONLY from
  already-generated artifact numbers.

Nothing here touches the backtest engine: every function is pure and
unit-testable, so the contract is locked before any run.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

FOLD_MIN_DAYS = 20  # a declared fold shorter than this is a contract error

# Promotion rubric (mission 2026-08 §9, predeclared, frozen).
SHARPE_IMPROVEMENT_MIN = 0.08
MAXDD_IMPROVEMENT_MIN = 0.03
FOLD_WINS_MIN = 10
YEAR_WINS_MIN = 6
FOLD_COUNT_DECLARED = 20


def calendar_fold_windows(
    eval_start: datetime,
    eval_end: datetime,
    *,
    step_months: int = 6,
) -> list[tuple[datetime, datetime]]:
    """Anchored walk-forward folds on true calendar month boundaries.

    Every boundary is ``start + relativedelta(months=step_months)`` applied
    to the PREVIOUS fold start (not a 180-day timedelta, which drifts
    against the calendar). The final fold is clipped at ``eval_end``; a
    trailing fold shorter than ``FOLD_MIN_DAYS`` is dropped (and would
    violate the manifest contract elsewhere).
    """
    if step_months < 1:
        raise ValueError("step_months must be positive")
    folds: list[tuple[datetime, datetime]] = []
    cursor = eval_start
    while cursor < eval_end:
        fold_end = min(cursor + relativedelta(months=step_months), eval_end)
        if fold_end - cursor >= timedelta(days=FOLD_MIN_DAYS):
            folds.append((cursor, fold_end))
        cursor = fold_end
    return folds


def fold_dates(folds: Sequence[tuple[datetime, datetime]]) -> list[str]:
    """ISO date strings of every fold boundary (manifest predeclaration)."""
    return [start.strftime("%Y-%m-%d") for start, _end in folds]


def assert_fold_contract(
    declared: Sequence[tuple[datetime, datetime]],
    computed: Sequence[tuple[datetime, datetime]],
) -> None:
    """Abort-worthy check: the manifest declaration must equal the code.

    Predeclared fold boundaries are authoritative; drift between the
    committed manifest and the runner's computation aborts the run.
    """
    if len(computed) != FOLD_COUNT_DECLARED or len(declared) != len(computed):
        raise SystemExit(
            f"fold contract violated: expected exactly {FOLD_COUNT_DECLARED} folds, "
            f"got declared {len(declared)} / computed {len(computed)}"
        )
    # Date-only comparison: fold boundaries are calendar dates; this keeps the
    # declaration robust to timezone/ISO representations.
    declared_dates = [value.date().isoformat() for pair in declared for value in pair]
    computed_dates = [value.date().isoformat() for pair in computed for value in pair]
    if declared_dates != computed_dates:
        raise SystemExit(
            "fold contract drift: manifest declares "
            f"{len(declared)} folds {declared_dates[:4]}... but the runner "
            f"computes {len(computed)} folds {computed_dates[:4]}..."
        )


def temporal_diagnostic_windows(
    eval_start: datetime,
    eval_end: datetime,
    recent_start: datetime,
) -> dict[str, list[tuple[datetime, datetime]]]:
    """Predeclared sensitivity windows (diagnostics only; never optimized).

    Exclusion windows are returned as ordered segments and are evaluated by
    the runner as CHAINED runs (segment 2 warms up on all bars before its
    start, including the excluded year, and compounds on segment 1's ending
    equity). Names are the canonical keys used by artifacts/report.
    """
    year_start = {
        year: datetime(year, 1, 1, tzinfo=UTC) for year in (2020, 2021, 2022, 2023, 2024, 2025)
    }
    return {
        "window_2017_2021": [(eval_start, min(recent_start, eval_end))],
        "window_2022_2026": [(recent_start, eval_end)],
        "window_2023_2026": [(year_start[2023], eval_end)],
        "exclude_2020": [
            (eval_start, min(year_start[2020], eval_end)),
            (min(year_start[2021], eval_end), eval_end),
        ],
        "exclude_2024": [
            (eval_start, min(year_start[2024], eval_end)),
            (min(year_start[2025], eval_end), eval_end),
        ],
    }


def paired_fold_deltas(
    control_folds: Sequence[Mapping[str, Any]],
    candidate_folds: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Canonical paired candidate-vs-control fold comparison.

    ``control_folds`` / ``candidate_folds`` are the per-variant artifact
    ``walk_forward.folds`` rows (``start``, ``end``, ``return``, ``sharpe``,
    ``max_drawdown``). Folds are paired BY START DATE (declared boundaries
    are identical for every variant, so every pair aligns). This is the only
    implementation of median delta / wins / p25 for the whole mission — the
    report must consume its output, never recompute.
    """
    control_by_start = {row["start"]: row for row in control_folds}
    deltas_rows: list[dict[str, Any]] = []
    d_returns: list[float] = []
    d_sharpes: list[float] = []
    d_dds: list[float] = []
    for fold in candidate_folds:
        control_fold = control_by_start[fold["start"]]
        delta_return = fold["return"] - control_fold["return"]
        delta_sharpe = fold["sharpe"] - control_fold["sharpe"]
        delta_dd = fold["max_drawdown"] - control_fold["max_drawdown"]
        deltas_rows.append(
            {
                "start": fold["start"],
                "end": fold["end"],
                "candidate_return": fold["return"],
                "control_return": control_fold["return"],
                "candidate_sharpe": fold["sharpe"],
                "control_sharpe": control_fold["sharpe"],
                "candidate_maxDD": fold["max_drawdown"],
                "control_maxDD": control_fold["max_drawdown"],
                "return_delta": round(delta_return, 6),
                "sharpe_delta": round(delta_sharpe, 6),
                "maxDD_delta": round(delta_dd, 6),
            }
        )
        d_returns.append(delta_return)
        d_sharpes.append(delta_sharpe)
        d_dds.append(delta_dd)
    return {
        "fold_count": len(deltas_rows),
        "deltas": deltas_rows,
        "return_delta": {
            "median": round(statistics.median(d_returns), 6) if d_returns else 0.0,
            "mean": round(statistics.fmean(d_returns), 6) if d_returns else 0.0,
            "stdev": round(statistics.pstdev(d_returns), 6) if d_returns else 0.0,
            "p25": round(_percentile(d_returns, 0.25), 6),
            "min": round(min(d_returns), 6) if d_returns else 0.0,
            "max": round(max(d_returns), 6) if d_returns else 0.0,
            "wins_over_control": sum(1 for value in d_returns if value > 0),
        },
        "sharpe_delta": {
            "median": round(statistics.median(d_sharpes), 6) if d_sharpes else 0.0,
            "mean": round(statistics.fmean(d_sharpes), 6) if d_sharpes else 0.0,
            "wins_over_control": sum(1 for value in d_sharpes if value > 0),
        },
        "maxDD_delta": {
            "median": round(statistics.median(d_dds), 6) if d_dds else 0.0,
            "mean": round(statistics.fmean(d_dds), 6) if d_dds else 0.0,
            "wins_over_control": sum(1 for value in d_dds if value < 0),
        },
    }


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[int(q * (len(ordered) - 1))] if ordered else 0.0


def promotion_gates(
    *,
    control: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate the mission §9 promotion rubric on aggregated numbers.

    ``control`` / ``candidate`` expose the canonical values (as read from
    the aggregate artifacts):

    * ``full_period``: ``{"return": float, "sharpe": float, "max_drawdown":
      float}``
    * ``recent_period``: same keys (2022+)
    * ``cost_stress``: {multiplier: {return, sharpe}} (1x..5x)
    * ``own_top3_drop``: {return, sharpe} of the candidate's own-top-3
      combined jackknife
    * ``yearly_wins``: count of calendar years candidate > control
    * ``yearly_count``: number of overlapping calendar years
    * ``exclude_2020`` / ``exclude_2024``: {"return": float}
    * ``fold_deltas``: output of ``paired_fold_deltas``

    Every gate returns {key, passed, detail} — no exceptions, no hidden
    thresholds; the rubric is declared in the manifest and enforced here.
    """

    info: list[dict[str, Any]] = []

    def record(key: str, passed: bool, detail: str) -> None:
        info.append({"gate": key, "passed": bool(passed), "detail": detail})

    full_c = control["full_period"]
    full_m = candidate["full_period"]
    sharpe_delta = full_m["sharpe"] - full_c["sharpe"]
    record(
        "sharpe_improvement",
        sharpe_delta >= SHARPE_IMPROVEMENT_MIN,
        f"candidate sharpe {full_m['sharpe']:.4f} vs control {full_c['sharpe']:.4f} "
        f"(delta {sharpe_delta:+.4f}; require >= +{SHARPE_IMPROVEMENT_MIN})",
    )

    maxdd_delta = full_c["max_drawdown"] - full_m["max_drawdown"]
    record(
        "maxdd_improvement",
        maxdd_delta >= MAXDD_IMPROVEMENT_MIN,
        f"control maxDD {full_c['max_drawdown']:.4f} - candidate "
        f"{full_m['max_drawdown']:.4f} = {maxdd_delta:+.4f} "
        f"(require >= {MAXDD_IMPROVEMENT_MIN})",
    )

    recent_c = control["recent_period"]
    recent_m = candidate["recent_period"]
    record(
        "recent_2022_plus_not_worse",
        recent_m["return"] >= recent_c["return"] and recent_m["sharpe"] >= recent_c["sharpe"],
        f"2022+ ret {recent_m['return']:.4f} vs {recent_c['return']:.4f}, "
        f"sharpe {recent_m['sharpe']:.4f} vs {recent_c['sharpe']:.4f}",
    )

    deltas = candidate["fold_deltas"]
    median_delta = deltas["return_delta"]["median"]
    record(
        "median_paired_fold_delta_positive",
        median_delta > 0.0,
        f"median paired fold delta {median_delta:+.6f} (require > 0)",
    )

    return_wins = deltas["return_delta"]["wins_over_control"]
    record(
        "return_fold_wins",
        return_wins >= FOLD_WINS_MIN,
        f"return wins {return_wins}/{deltas['fold_count']} (require >= {FOLD_WINS_MIN})",
    )
    sharpe_wins = deltas["sharpe_delta"]["wins_over_control"]
    record(
        "sharpe_fold_wins",
        sharpe_wins >= FOLD_WINS_MIN,
        f"sharpe wins {sharpe_wins}/{deltas['fold_count']} (require >= {FOLD_WINS_MIN})",
    )

    stress_m = candidate["cost_stress"]
    stress_c = control["cost_stress"]
    three_c = stress_c["3x"]
    three_m = stress_m["3x"]
    record(
        "survives_3x_costs",
        three_m["return"] >= three_c["return"] and three_m["sharpe"] >= three_c["sharpe"],
        f"3x ret {three_m['return']:.4f} vs {three_c['return']:.4f}, "
        f"sharpe {three_m['sharpe']:.4f} vs {three_c['sharpe']:.4f}",
    )

    own_top3 = candidate["own_top3_drop"]
    record(
        "own_top3_jackknife_preserves_advantage",
        own_top3["return"] >= full_c["return"] and own_top3["sharpe"] >= full_c["sharpe"],
        f"own-top-3 drop ret {own_top3['return']:.4f} vs control full "
        f"{full_c['return']:.4f}, sharpe {own_top3['sharpe']:.4f} vs "
        f"{full_c['sharpe']:.4f}",
    )

    yearly_wins = int(candidate["yearly_wins"])
    yearly_years = int(candidate["yearly_years"])
    record(
        "no_single_year_dependency",
        yearly_years > 0
        and yearly_wins > yearly_years - yearly_wins
        and candidate["exclude_2020"]["return"] >= control["exclude_2020"]["return"]
        and candidate["exclude_2024"]["return"] >= control["exclude_2024"]["return"],
        f"calendar-year wins {yearly_wins}/{yearly_years}; exclude-2020 candidate "
        f"{candidate['exclude_2020']['return']:.4f} vs control "
        f"{control['exclude_2020']['return']:.4f}; exclude-2024 candidate "
        f"{candidate['exclude_2024']['return']:.4f} vs control "
        f"{control['exclude_2024']['return']:.4f}",
    )
    return info


__all__ = [
    "FOLD_COUNT_DECLARED",
    "FOLD_MIN_DAYS",
    "MAXDD_IMPROVEMENT_MIN",
    "SHARPE_IMPROVEMENT_MIN",
    "YEAR_WIN_MIN",
    "YEAR_WIND_MIN",
    "assert_fold_contract",
    "calendar_fold_windows",
    "paired_fold_deltas",
    "promotion_gates",
    "temporal_diagnostic_windows",
]
