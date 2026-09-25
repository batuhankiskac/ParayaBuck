#!/usr/bin/env python3
"""Combine sleeve curves and evaluate the pre-registered Stage A gates.

    .venv/bin/python research/sleeve_candidates_v2/evaluate.py \
        --out research/sleeve_candidates_v2/RESULTS_STAGE_A.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from bisect import bisect_right
from datetime import UTC, date, datetime, time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
V3_PATH = HERE / "V3_CURVES.json.gz"
CURVES_DIR = HERE / "curves"
CRYPTO_PATH = ROOT / "maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz"

V3_CONTROL_ALLOCATION = 0.55
V3_TREATMENT_ALLOCATION = 0.40
DEVELOPMENT = (date(2017, 1, 3), date(2021, 12, 31))
VALIDATION = (date(2022, 1, 3), date(2026, 7, 30))
FULL = (date(2017, 1, 3), date(2026, 7, 30))
STRESS_2022 = (date(2022, 1, 1), date(2022, 12, 31))
MULTIPLIERS = ("1.0", "2.0", "3.0")
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_MEAN_BLOCK = 20
BOOTSTRAP_SEED = 20260925


def _load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _daily_returns(timestamps: list[str], curve: list[float]) -> dict[date, float]:
    dates = [datetime.fromisoformat(ts).date() for ts in timestamps]
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate curve dates")
    return {dates[i]: curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))}


def _crypto_returns(sessions: list[date]) -> dict[date, float]:
    """Crypto account return between consecutive NY sessions (equity close ~21:00 UTC)."""
    payload = _load(CRYPTO_PATH)
    stamps = [datetime.fromisoformat(ts) for ts in payload["curve_timestamps"]]
    curve = payload["equity_curve"]

    def value_at(day: date) -> float | None:
        index = bisect_right(stamps, datetime.combine(day, time(21), tzinfo=UTC))
        return None if index == 0 else curve[index - 1]

    returns: dict[date, float] = {}
    for previous, current in zip(sessions, sessions[1:], strict=False):
        before, after = value_at(previous), value_at(current)
        returns[current] = 0.0 if before is None or after is None else after / before - 1.0
    return returns


def _metrics(returns: list[tuple[date, float]], window: tuple[date, date]) -> dict[str, float]:
    series = [value for day, value in returns if window[0] <= day <= window[1]]
    nav, peak, max_dd = 1.0, 1.0, 0.0
    for value in series:
        nav *= 1.0 + value
        peak = max(peak, nav)
        max_dd = max(max_dd, 1.0 - nav / peak)
    years = (window[1] - window[0]).days / 365.25
    mean, stdev = statistics.fmean(series), statistics.stdev(series)
    cagr = nav ** (1.0 / years) - 1.0
    return {
        "total_return": nav - 1.0,
        "cagr": cagr,
        "volatility": stdev * math.sqrt(252),
        "sharpe": mean / stdev * math.sqrt(252),
        "max_drawdown": max_dd,
        "calmar": cagr / max_dd if max_dd else math.inf,
        "days": len(series),
    }


def _yearly(returns: list[tuple[date, float]]) -> dict[str, float]:
    years: dict[int, float] = {}
    for day, value in returns:
        years[day.year] = (1.0 + years.get(day.year, 0.0)) * (1.0 + value) - 1.0
    return {str(year): value for year, value in sorted(years.items())}


def _stationary_indices(rng: np.random.Generator, length: int) -> np.ndarray:
    """Politis-Romano stationary bootstrap index path."""
    indices = np.empty(length, dtype=np.int64)
    restart = rng.random(length) < 1.0 / BOOTSTRAP_MEAN_BLOCK
    starts = rng.integers(0, length, size=length)
    current = starts[0]
    for position in range(length):
        if position and not restart[position]:
            current = (current + 1) % length
        elif position:
            current = starts[position]
        indices[position] = current
    return indices


def _sharpe(values: np.ndarray) -> float:
    return float(values.mean() / values.std(ddof=1) * math.sqrt(252))


def _bootstrap_p(candidate: np.ndarray, reference: np.ndarray, statistic: str, seed: int) -> float:
    """Share of paired stationary-bootstrap replicates where the candidate does not win."""
    rng = np.random.default_rng(seed)
    losses = 0
    for _ in range(BOOTSTRAP_RESAMPLES):
        index = _stationary_indices(rng, len(candidate))
        left, right = candidate[index], reference[index]
        if statistic == "sharpe":
            difference = _sharpe(left) - _sharpe(right)
        else:
            difference = float(left.mean() - right.mean())
        losses += difference <= 0.0
    return losses / BOOTSTRAP_RESAMPLES


def _holm(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - rank) * value))
        adjusted[name] = running
    return adjusted


def _in_full(series: list[tuple[date, float]]) -> np.ndarray:
    return np.array([value for day, value in series if FULL[0] <= day <= FULL[1]])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    v3 = _load(V3_PATH)
    if v3["maximum_allocation"] != V3_CONTROL_ALLOCATION:
        raise ValueError("V3 curves must come from the shipped 0.55 allocation")
    sleeves = {path.name.removesuffix(".json.gz"): _load(path) for path in sorted(CURVES_DIR.glob("*.json.gz"))}
    scale = V3_TREATMENT_ALLOCATION / V3_CONTROL_ALLOCATION

    results: dict[str, object] = {"arms": {}, "gates": {}, "diagnostics": {}}
    series_1x: dict[str, list[tuple[date, float]]] = {}
    for multiplier in MULTIPLIERS:
        v3_run = v3["runs"][multiplier]
        v3_returns = _daily_returns(v3_run["curve_timestamps"], v3_run["equity_curve"])
        sessions = sorted({datetime.fromisoformat(ts).date() for ts in v3_run["curve_timestamps"]})
        crypto_returns = _crypto_returns(sessions)
        days = sessions[1:]
        arms = {
            "A": [(day, v3_returns[day] + crypto_returns[day]) for day in days],
            "B": [(day, scale * v3_returns[day] + crypto_returns[day]) for day in days],
        }
        for name, payload in sleeves.items():
            run = payload["runs"][f"{name}|{multiplier}"]
            sleeve_returns = _daily_returns(run["curve_timestamps"], run["equity_curve"])
            if set(sleeve_returns) != set(v3_returns):
                raise ValueError(f"calendar mismatch for {name}")
            v3_weight = scale if payload["family"] == "S" else 1.0
            arms[name] = [
                (day, v3_weight * v3_returns[day] + sleeve_returns[day] + crypto_returns[day]) for day in days
            ]
            if multiplier == "1.0":
                results["diagnostics"][f"{name}__corr_with_v3"] = statistics.correlation(
                    [v3_returns[day] for day in days], [sleeve_returns[day] for day in days]
                )
                results["diagnostics"][f"{name}__corr_with_v3_2022"] = statistics.correlation(
                    [v3_returns[day] for day in days if day.year == 2022],
                    [sleeve_returns[day] for day in days if day.year == 2022],
                )
        for name, series in arms.items():
            results["arms"][f"{name}|{multiplier}"] = {
                "full": _metrics(series, FULL),
                "development": _metrics(series, DEVELOPMENT),
                "validation": _metrics(series, VALIDATION),
                "year_2022": _metrics(series, STRESS_2022),
                "yearly": _yearly([(day, value) for day, value in series if FULL[0] <= day <= FULL[1]]),
            }
            if multiplier == "1.0":
                series_1x[name] = series

    arms = results["arms"]
    a, b = arms["A|1.0"], arms["B|1.0"]
    p_values: dict[str, float] = {}
    for index, (name, payload) in enumerate(sleeves.items()):
        c = arms[f"{name}|1.0"]
        runs = [payload["runs"][f"{name}|{m}"] for m in MULTIPLIERS]
        validity = all(value == 1.0 for value in payload["coverage"].values()) and all(
            run["fills_after_decisions"] for run in runs
        )
        contributions = payload["runs"][f"{name}|1.0"]["contributions"]
        absolute_total = sum(abs(value) for value in contributions.values())
        largest_share = max(abs(value) for value in contributions.values()) / absolute_total
        if payload["family"] == "S":
            gates = {
                "G1_validity": validity,
                "G2_full_sharpe_and_drawdown": c["full"]["sharpe"] >= a["full"]["sharpe"] + 0.05
                and c["full"]["max_drawdown"] <= a["full"]["max_drawdown"],
                "G3_cagr_guard": c["full"]["cagr"] >= a["full"]["cagr"] - 0.005,
                "G4_validation": c["validation"]["sharpe"] >= a["validation"]["sharpe"]
                and c["validation"]["max_drawdown"] <= a["validation"]["max_drawdown"] + 0.01,
                "G5_development": c["development"]["sharpe"] >= a["development"]["sharpe"] - 0.05,
                "G6_2022": c["year_2022"]["total_return"] >= a["year_2022"]["total_return"],
                "G7_costs_3x": arms[f"{name}|3.0"]["full"]["sharpe"] >= arms["A|3.0"]["full"]["sharpe"],
                "G8_concentration": largest_share <= 0.60 if payload["concentration_gate"] else True,
                "G9a_sharpe_vs_cash": c["full"]["sharpe"] >= b["full"]["sharpe"] + 0.05,
                "G9b_calmar_vs_cash": c["full"]["calmar"] >= b["full"]["calmar"],
                "G9c_validation_sharpe_vs_cash": c["validation"]["sharpe"] >= b["validation"]["sharpe"],
            }
            p_values[name] = _bootstrap_p(
                _in_full(series_1x[name]), _in_full(series_1x["B"]), "sharpe", BOOTSTRAP_SEED + index
            )
        else:
            gates = {
                "XG1_validity": validity,
                "XG2_cagr_uplift": c["full"]["cagr"] >= a["full"]["cagr"] + 0.0025,
                "XG3_drawdown": c["full"]["max_drawdown"] <= a["full"]["max_drawdown"] + 0.0025,
                "XG4_sharpe": c["full"]["sharpe"] >= a["full"]["sharpe"],
                "XG5_validation_cagr": c["validation"]["cagr"] >= a["validation"]["cagr"],
                "XG6_2022": c["year_2022"]["total_return"] >= a["year_2022"]["total_return"] - 0.0025,
                "XG7_costs_3x": arms[f"{name}|3.0"]["full"]["cagr"] >= arms["A|3.0"]["full"]["cagr"],
            }
            p_values[name] = _bootstrap_p(
                _in_full(series_1x[name]), _in_full(series_1x["A"]), "mean", BOOTSTRAP_SEED + index
            )
        years_beating_a = sum(c["yearly"][year] >= a["yearly"][year] for year in a["yearly"] if year in c["yearly"])
        results["gates"][name] = {
            "family": payload["family"],
            "gates": gates,
            "passed": all(gates.values()),
            "largest_etf_pnl_share": largest_share,
            "etf_contributions": contributions,
            "years_at_or_above_A": years_beating_a,
            "years_total": len(a["yearly"]),
        }
    adjusted = _holm(p_values)
    for name in sleeves:
        results["gates"][name]["bootstrap_p"] = p_values[name]
        results["gates"][name]["bootstrap_p_holm"] = adjusted[name]

    passing_s = [name for name in sleeves if sleeves[name]["family"] == "S" and results["gates"][name]["passed"]]
    passing_x = [name for name in sleeves if sleeves[name]["family"] == "X" and results["gates"][name]["passed"]]
    results["verdict"] = {
        "stage_a_pass_s": passing_s,
        "stage_a_pass_x": passing_x,
        "stage_b_s": sorted(passing_s, key=lambda name: -arms[f"{name}|1.0"]["full"]["sharpe"])[:2],
        "stage_b_x": passing_x,
    }
    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    header = f"{'arm':24} {'total':>7} {'cagr':>7} {'sharpe':>7} {'maxdd':>7} {'valSh':>7} {'2022':>7} {'3xSh':>7}"
    print(header)
    for name in ("A", "B", *sleeves):
        row, row3 = arms[f"{name}|1.0"], arms[f"{name}|3.0"]
        print(
            f"{name:24} {row['full']['total_return']:7.3f} {row['full']['cagr']:7.4f} {row['full']['sharpe']:7.3f} "
            f"{row['full']['max_drawdown']:7.4f} {row['validation']['sharpe']:7.3f} "
            f"{row['year_2022']['total_return']:7.4f} {row3['full']['sharpe']:7.3f}"
        )
    for name in sleeves:
        entry = results["gates"][name]
        failed = [gate for gate, ok in entry["gates"].items() if not ok]
        print(
            f"{name:24} pass={entry['passed']!s:5} p={entry['bootstrap_p']:.3f} holm={entry['bootstrap_p_holm']:.3f} "
            f"years>=A={entry['years_at_or_above_A']}/{entry['years_total']} failed={failed}"
        )
    print(json.dumps(results["verdict"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
