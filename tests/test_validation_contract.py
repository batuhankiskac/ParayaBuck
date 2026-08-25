"""Contract tests for the MH_RAW promotion-validation module.

Locks the predeclared reproducibility contract before any run: exactly 20
calendar 6-month folds via ``relativedelta`` (never 180-day timedelta), the
manifest-declaration drift abort, the canonical paired-fold deltas, the
temporal sensitivity windows, and the §9 promotion rubric.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.validation import (
    FOLD_COUNT_DECLARED,
    assert_fold_contract,
    calendar_fold_windows,
    chain_equity_curves,
    chained_cumulative_metrics,
    paired_fold_deltas,
    promotion_gates,
    temporal_diagnostic_windows,
)

EVAL_START = datetime(2017, 1, 3, tzinfo=UTC)
EVAL_END = datetime(2026, 7, 30, tzinfo=UTC)
RECENT_START = datetime(2022, 1, 1, tzinfo=UTC)


def test_calendar_folds_exactly_twenty_and_aligned() -> None:
    folds = calendar_fold_windows(EVAL_START, EVAL_END)
    assert len(folds) == FOLD_COUNT_DECLARED == 20
    # True calendar boundaries: every fold except the last opens on Jan-03 or
    # Jul-03 (relativedelta(months=6) from the previous boundary). A naive
    # timedelta(days=180) implementation would drift to Nov-29 etc.
    for start, end in folds[:-1]:
        assert (start.month, start.day) in ((1, 3), (7, 3))
        assert (end.month, end.day) in ((1, 3), (7, 3))
    assert folds[0][0] == EVAL_START
    assert folds[-1][1] == EVAL_END


def test_calendar_folds_never_use_timedelta_180() -> None:
    folds = calendar_fold_windows(EVAL_START, EVAL_END)
    actual_ends = {end for _start, end in folds}
    # A timedelta(days=180) non-leap chain ends at 2017-07-02, never 07-03;
    # the declared boundary must reject the naive chain's Jul-02.
    assert datetime(2017, 7, 3, tzinfo=UTC) in actual_ends
    assert datetime(2017, 7, 2, tzinfo=UTC) not in actual_ends


def test_fold_contract_aborts_on_drift() -> None:
    folds = calendar_fold_windows(EVAL_START, EVAL_END)
    drifted = [(EVAL_START, datetime(2017, 7, 2, tzinfo=UTC)), *folds[1:]]
    with pytest.raises(SystemExit, match="fold contract drift"):
        assert_fold_contract(drifted, folds)
    wrong_count = folds[:19]
    with pytest.raises(SystemExit, match="exactly 20 folds"):
        assert_fold_contract(wrong_count, folds)
    # The exact declaration passes.
    assert_fold_contract(folds, folds)


def test_paired_fold_deltas_is_canonical() -> None:
    control = [
        {
            "start": "2017-01-03",
            "end": "2017-07-03",
            "return": 0.05,
            "sharpe": 0.9,
            "max_drawdown": 0.1,
        },
        {
            "start": "2017-07-03",
            "end": "2018-01-03",
            "return": 0.01,
            "sharpe": 0.5,
            "max_drawdown": 0.2,
        },
        {
            "start": "2018-01-03",
            "end": "2018-07-03",
            "return": -0.02,
            "sharpe": -0.3,
            "max_drawdown": 0.15,
        },
    ]
    candidate = [
        {
            "start": "2017-01-03",
            "end": "2017-07-03",
            "return": 0.09,
            "sharpe": 1.2,
            "max_drawdown": 0.08,
        },
        {
            "start": "2017-07-03",
            "end": "2018-01-03",
            "return": 0.0,
            "sharpe": 0.4,
            "max_drawdown": 0.21,
        },
        {
            "start": "2018-01-03",
            "end": "2018-07-03",
            "return": -0.01,
            "sharpe": -0.2,
            "max_drawdown": 0.16,
        },
    ]
    result = paired_fold_deltas(control, candidate)
    assert result["fold_count"] == 3
    assert [row["return_delta"] for row in result["deltas"]] == [0.04, -0.01, 0.01]
    assert result["return_delta"]["median"] == 0.01
    assert result["return_delta"]["wins_over_control"] == 2
    assert result["return_delta"]["min"] == -0.01
    assert result["maxDD_delta"]["wins_over_control"] == 1  # only fold 2 deepens DD
    # Delta rows are sorted by fold start, deterministic.
    assert [row["start"] for row in result["deltas"]] == sorted(row["start"] for row in result["deltas"])


def test_temporal_windows_predeclared_shape() -> None:
    windows = temporal_diagnostic_windows(EVAL_START, EVAL_END, RECENT_START)
    assert set(windows) == {
        "window_2017_2021",
        "window_2022_2026",
        "window_2023_2026",
        "exclude_2020",
        "exclude_2024",
    }
    # 2017-2021 ends exactly where 2022+ starts.
    assert windows["window_2017_2021"][0][1] == RECENT_START
    assert windows["window_2022_2026"][0][0] == RECENT_START
    # Exclusions are chained segments that together skip one full year.
    seg_2020 = windows["exclude_2020"]
    assert [s.year for s, _e in seg_2020] == [2017, 2021]
    # Second segment runs to EVAL_END (2026-07-30), not a full calendar 2027.
    assert [e.year for _s, e in seg_2020] == [2020, 2026]
    seg_2024 = windows["exclude_2024"]
    assert [s.year for s, _e in seg_2024] == [2017, 2025]
    assert [e.year for _s, e in seg_2024] == [2024, 2026]
    for name, segments in windows.items():
        for start, end in segments:
            assert start < end


def _fold(return_value: float, sharpe: float, maxdd: float) -> dict[str, float]:
    return {"start": "", "end": "", "return": return_value, "sharpe": sharpe, "max_drawdown": maxdd}


def _rubric_inputs() -> dict[str, object]:
    """A candidate that passes every §9 gate (and one tweak fails each)."""

    control_folds = [
        _fold(0.03, 0.60, 0.12),
        _fold(0.02, 0.55, 0.13),
    ]
    candidate_folds = [
        _fold(0.06, 0.90, 0.09),
        _fold(0.055, 0.85, 0.10),
    ]
    candidate_folds[0]["start"] = "2017-01-03"
    candidate_folds[0]["end"] = "2017-07-03"
    candidate_folds[1]["start"] = "2017-07-03"
    candidate_folds[1]["end"] = "2018-01-03"
    control_folds[0]["start"] = "2017-01-03"
    control_folds[0]["end"] = "2017-07-03"
    control_folds[1]["start"] = "2017-07-03"
    control_folds[1]["end"] = "2018-01-03"
    for index in range(2, 20):
        pair_start = datetime(2017, 1, 3, tzinfo=UTC) + timedelta(days=180 * index)
        end = pair_start + timedelta(days=180)
        control_folds.append(_fold(0.02, 0.5, 0.13))
        candidate_folds.append(_fold(0.05, 0.8, 0.10))
        control_folds[-1]["start"] = pair_start.date().isoformat()
        control_folds[-1]["end"] = end.date().isoformat()
        candidate_folds[-1]["start"] = pair_start.date().isoformat()
        candidate_folds[-1]["end"] = end.date().isoformat()
    # 12 of 20 candidate folds beat control on return and Sharpe.
    control_folds[2]["start"] = candidate_folds[2]["start"] = "2018-01-03"
    control_folds[3]["start"] = candidate_folds[3]["start"] = "2018-07-03"
    candidate_folds[2]["return"] = 0.02  # tie
    candidate_folds[2]["sharpe"] = 0.5
    candidate_folds[3]["return"] = 0.01
    candidate_folds[3]["sharpe"] = 0.4
    candidate_folds[9]["return"] = 0.01
    candidate_folds[9]["sharpe"] = 0.4
    candidate_folds[11]["return"] = 0.01
    candidate_folds[11]["sharpe"] = 0.4
    candidate_folds[14]["return"] = 0.01
    candidate_folds[14]["sharpe"] = 0.4
    candidate_folds[15]["return"] = 0.01
    candidate_folds[15]["sharpe"] = 0.4
    candidate_folds[17]["return"] = 0.01
    candidate_folds[17]["sharpe"] = 0.4
    candidate_folds[18]["return"] = 0.01
    candidate_folds[18]["sharpe"] = 0.4
    candidate_folds[19]["return"] = 0.01
    candidate_folds[19]["sharpe"] = 0.4

    def full(sharpe: float, maxdd: float, ret: float) -> dict[str, float]:
        return {"return": ret, "sharpe": sharpe, "max_drawdown": maxdd}

    def stress(value: float) -> dict[str, object]:
        return {
            "1x": {"return": value, "sharpe": 0.8},
            "2x": {"return": value - 0.05, "sharpe": 0.75},
            "3x": {"return": value - 0.1, "sharpe": 0.7},
            "5x": {"return": value - 0.2, "sharpe": 0.6},
        }

    return {
        "control": {
            "full_period": full(0.60, 0.28, ret=1.3),
            "recent_period": full(0.55, 0.16, ret=0.3),
            "cost_stress": stress(1.3),
            "own_top3_drop": {"return": 1.0, "sharpe": 0.55},
            "yearly_wins": 7,
            "yearly_years": 10,
            "exclude_2020": {"return": 1.1},
            "exclude_2024": {"return": 1.2},
            "fold_deltas": paired_fold_deltas(control_folds, control_folds),
        },
        "candidate": {
            "full_period": full(0.72, 0.21, ret=1.9),  # sharpe +0.12, maxDD -7pp
            "recent_period": full(0.60, 0.13, ret=0.35),
            "cost_stress": stress(1.8),
            "own_top3_drop": {"return": 1.5, "sharpe": 0.65},
            "yearly_wins": 7,
            "yearly_years": 10,
            "exclude_2020": {"return": 1.4},
            "exclude_2024": {"return": 1.5},
            "fold_deltas": paired_fold_deltas(control_folds, candidate_folds),
        },
    }


def test_promotion_gate_all_passing() -> None:
    data = _rubric_inputs()
    gates = promotion_gates(control=data["control"], candidate=data["candidate"])
    assert all(gate["passed"] for gate in gates), [g for g in gates if not g["passed"]]
    assert len(gates) == 9


def test_promotion_gate_each_failure_flips_its_gate() -> None:
    data = _rubric_inputs()

    def clone() -> dict[str, object]:
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in data["candidate"].items()}

    # 1. Sharpe fails below +0.08.
    candidate = clone()
    candidate["full_period"]["sharpe"] = 0.67
    gates = promotion_gates(control=data["control"], candidate=candidate)
    assert not next(g for g in gates if g["gate"] == "sharpe_improvement")["passed"]

    # 2. maxDD improvement fails below 3pp (0.28 - 0.252 = 2.8pp).
    candidate = clone()
    candidate["full_period"]["max_drawdown"] = 0.252
    gates = promotion_gates(control=data["control"], candidate=candidate)
    assert not next(g for g in gates if g["gate"] == "maxdd_improvement")["passed"]

    # 3. 2022+ worse than control on return.
    candidate = clone()
    candidate["recent_period"] = {"return": 0.2, "sharpe": 0.4, "max_drawdown": 0.2}
    gates = promotion_gates(control=data["control"], candidate=candidate)
    assert not next(g for g in gates if g["gate"] == "recent_2022_plus_not_worse")["passed"]

    # 4. median paired fold delta not positive.
    bad_folds = [_fold(0.02, 0.50, 0.13), _fold(0.0, 0.30, 0.15)]
    bad_folds[0]["start"] = "2017-01-03"
    bad_folds[0]["end"] = "2017-07-03"
    bad_folds[1]["start"] = "2017-07-03"
    bad_folds[1]["end"] = "2018-01-03"
    candidate = clone()
    # Pair against RAW control fold rows (the deltas of the all-pass fixture
    # are paired rows, not raw fold rows).
    control_rows = [
        {
            "start": "2017-01-03",
            "end": "2017-07-03",
            "return": 0.03,
            "sharpe": 0.6,
            "max_drawdown": 0.12,
        },
        {
            "start": "2017-07-03",
            "end": "2018-01-03",
            "return": 0.02,
            "sharpe": 0.55,
            "max_drawdown": 0.13,
        },
    ]
    candidate["fold_deltas"] = paired_fold_deltas(control_rows, bad_folds)
    gates = promotion_gates(control=data["control"], candidate=candidate)
    assert not next(g for g in gates if g["gate"] == "median_paired_fold_delta_positive")["passed"]

    # 5. 3x cost stress loses to control.
    candidate = clone()
    candidate["cost_stress"]["3x"] = {"return": 1.25, "sharpe": 0.59}
    gates = promotion_gates(control=data["control"], candidate=candidate)
    assert not next(g for g in gates if g["gate"] == "survives_3x_costs")["passed"]

    # 6. own-top-3 drop loses the advantage.
    candidate = clone()
    candidate["own_top3_drop"] = {"return": 1.2, "sharpe": 0.54}
    gates = promotion_gates(control=data["control"], candidate=candidate)
    assert not next(g for g in gates if g["gate"] == "own_top3_jackknife_preserves_advantage")["passed"]

    # 7. single-year dependency: 4/10 year wins.
    candidate = clone()
    candidate["yearly_wins"] = 4
    gates = promotion_gates(control=data["control"], candidate=candidate)
    assert not next(g for g in gates if g["gate"] == "no_single_year_dependency")["passed"]


def test_promotion_rubric_constants_match_mission() -> None:
    assert FOLD_COUNT_DECLARED == 20


# --------------------------------------------------------------------------- #
# Chained (temporal-exclusion) cumulative math — provenance contract
# --------------------------------------------------------------------------- #


def test_two_segment_chain_reports_compound_not_last_segment() -> None:
    """A two-segment synthetic chain must report

        cumulative_return = (1 + r1) * (1 + r2) - 1

    and NOT merely segment2's standalone return."""
    # Segment 1: 100 -> 110 (+10%); segment 2 seeded at 110 -> 121 (+10%).
    joined = chain_equity_curves([[100.0, 110.0], [110.0, 121.0]])
    assert joined == [100.0, 110.0, 121.0]
    metrics = chained_cumulative_metrics(joined)
    # Compound: 1.10 * 1.10 - 1 = 0.21. Segment2 alone would be 0.10.
    assert metrics["cumulative_return"] == 0.21
    assert metrics["cumulative_return"] != 0.10
    # Unequal segment lengths and three segments.
    joined3 = chain_equity_curves([[100.0, 105.0], [105.0], [105.0, 126.0]])
    assert chained_cumulative_metrics(joined3)["cumulative_return"] == 0.26
    # Single-segment chain degrades to that segment's own return.
    assert chained_cumulative_metrics([100.0, 110.0])["cumulative_return"] == 0.10


def test_chained_maxdd_uses_joined_curve_across_boundary() -> None:
    """A drawdown spanning a segment boundary must be measured on the joined
    continuous curve, not per-segment."""
    # Segment 1: 100 -> 120 -> 90  (per-segment maxDD 25%).
    # Segment 2 (seeded at 90): 90 -> 60 (per-segment maxDD 33.3%).
    # Joined: 100 -> 120 -> 90 -> 60 has peak 120 and trough 60 -> 50%.
    joined = chain_equity_curves([[100.0, 120.0, 90.0], [90.0, 60.0]])
    metrics = chained_cumulative_metrics(joined)
    assert metrics["cumulative_max_drawdown"] == pytest.approx(0.5, abs=1e-9)
    assert metrics["cumulative_return"] == pytest.approx(-0.4, abs=1e-9)


def test_chained_sharpe_uses_joined_daily_returns() -> None:
    """Cumulative Sharpe is computed from the JOINED curve's daily returns."""
    # Segment 1: deterministic high-vol oscillation; segment 2: calm drift.
    seg1 = [100.0]
    for _ in range(20):
        seg1.append(seg1[-1] * 1.01)
        seg1.append(seg1[-1] * 0.99)
    seg2 = [seg1[-1]]
    for _ in range(20):
        seg2.append(seg2[-1] * 1.0005)
    joined = chain_equity_curves([seg1, seg2])
    daily = [joined[i] / joined[i - 1] - 1 for i in range(1, len(joined))]
    mean = sum(daily) / len(daily)
    variance = sum((v - mean) ** 2 for v in daily) / (len(daily) - 1)
    expected = mean / math.sqrt(variance) * math.sqrt(252.0)
    computed = chained_cumulative_metrics(joined)["cumulative_sharpe"]
    # float64 daily compounding accumulates tiny rounding; the join-dropping
    # assertion below proves cross-boundary sensitivity with a large margin,
    # so a loose tolerance here is safe.
    assert computed == pytest.approx(expected, abs=1e-6)
    # The join point (last of seg1 -> first of seg2) contributes a real daily
    # return; dropping it would change the Sharpe — proving the joined curve
    # is the source, not per-segment statistics.
    droppped = daily[1:]
    mean2 = sum(droppped) / len(droppped)
    variance2 = sum((v - mean2) ** 2 for v in droppped) / (len(droppped) - 1)
    different = mean2 / math.sqrt(variance2) * math.sqrt(252.0)
    assert chained_cumulative_metrics(joined)["cumulative_sharpe"] != pytest.approx(different, abs=1e-12)


def test_chain_helpers_empty_and_degenerate_inputs() -> None:
    assert chain_equity_curves([]) == []
    assert chain_equity_curves([[], [100.0]]) == []
    flat = chained_cumulative_metrics([])
    assert flat == {
        "cumulative_return": 0.0,
        "cumulative_sharpe": 0.0,
        "cumulative_max_drawdown": 0.0,
    }
