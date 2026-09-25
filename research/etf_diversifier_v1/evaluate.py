#!/usr/bin/env python3
"""Combine sleeve curves and evaluate the pre-registered ETF diversifier gates.

    .venv/bin/python research/etf_diversifier_v1/evaluate.py \
        --out research/etf_diversifier_v1/RESULTS.json
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
V3_PATH = HERE / "V3_CURVES.json.gz"
ETF_PATH = HERE / "ETF_CURVES.json.gz"
CRYPTO_PATH = ROOT / "maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz"

V3_CONTROL_ALLOCATION = 0.55
V3_TREATMENT_ALLOCATION = 0.40
DEVELOPMENT = (date(2017, 1, 3), date(2021, 12, 31))
VALIDATION = (date(2022, 1, 3), date(2026, 7, 30))
FULL = (date(2017, 1, 3), date(2026, 7, 30))
STRESS_2022 = (date(2022, 1, 1), date(2022, 12, 31))
CANDIDATES = ("etf_static_defensive_v1", "etf_trend_12m_v1")
MULTIPLIERS = ("1.0", "2.0", "3.0")


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
    downside = math.sqrt(statistics.fmean([min(0.0, value) ** 2 for value in series]))
    cagr = nav ** (1.0 / years) - 1.0
    return {
        "total_return": nav - 1.0,
        "cagr": cagr,
        "volatility": stdev * math.sqrt(252),
        "sharpe": mean / stdev * math.sqrt(252),
        "sortino": mean / downside * math.sqrt(252) if downside else math.inf,
        "max_drawdown": max_dd,
        "calmar": cagr / max_dd if max_dd else math.inf,
        "days": len(series),
    }


def _drawdown_days_over(returns: list[tuple[date, float]], threshold: float) -> int:
    nav, peak, count = 1.0, 1.0, 0
    for _day, value in returns:
        nav *= 1.0 + value
        peak = max(peak, nav)
        count += 1.0 - nav / peak > threshold
    return count


def _correlation(left: list[float], right: list[float]) -> float:
    return statistics.correlation(left, right)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    v3 = _load(V3_PATH)
    etf = _load(ETF_PATH)
    if v3["maximum_allocation"] != V3_CONTROL_ALLOCATION:
        raise ValueError("V3 curves must come from the shipped 0.55 allocation")

    results: dict[str, object] = {"arms": {}, "gates": {}, "diagnostics": {}}
    for multiplier in MULTIPLIERS:
        v3_run = v3["runs"][multiplier]
        v3_returns = _daily_returns(v3_run["curve_timestamps"], v3_run["equity_curve"])
        sessions = sorted({datetime.fromisoformat(ts).date() for ts in v3_run["curve_timestamps"]})
        crypto_returns = _crypto_returns(sessions)
        days = sessions[1:]

        control = [(day, v3_returns[day] + crypto_returns[day]) for day in days]
        arms = {"control": control}
        for candidate in CANDIDATES:
            run = etf["runs"][f"{candidate}|{multiplier}"]
            etf_returns = _daily_returns(run["curve_timestamps"], run["equity_curve"])
            if set(etf_returns) != set(v3_returns):
                raise ValueError(f"calendar mismatch for {candidate}")
            scale = V3_TREATMENT_ALLOCATION / V3_CONTROL_ALLOCATION
            arms[candidate] = [(day, scale * v3_returns[day] + etf_returns[day] + crypto_returns[day]) for day in days]
            arms[f"{candidate}__sensitivity_gross075"] = [
                (day, v3_returns[day] + etf_returns[day] + crypto_returns[day]) for day in days
            ]
            if multiplier == "1.0":
                results["diagnostics"][f"{candidate}__corr_with_v3"] = _correlation(
                    [v3_returns[day] for day in days], [etf_returns[day] for day in days]
                )
                results["diagnostics"][f"{candidate}__corr_with_v3_2022"] = _correlation(
                    [v3_returns[day] for day in days if day.year == 2022],
                    [etf_returns[day] for day in days if day.year == 2022],
                )
        for name, series in arms.items():
            results["arms"][f"{name}|{multiplier}"] = {
                "full": _metrics(series, FULL),
                "development": _metrics(series, DEVELOPMENT),
                "validation": _metrics(series, VALIDATION),
                "year_2022": _metrics(series, STRESS_2022),
                "days_in_drawdown_over_5pct": _drawdown_days_over(series, 0.05),
            }

    arms = results["arms"]
    for candidate in CANDIDATES:
        run = etf["runs"][f"{candidate}|1.0"]
        c, a = arms[f"{candidate}|1.0"], arms["control|1.0"]
        contributions = run["contributions"]
        absolute_total = sum(abs(value) for value in contributions.values())
        largest_share = max(abs(value) for value in contributions.values()) / absolute_total
        gates = {
            "G1_validity": all(value == 1.0 for value in etf["coverage"].values())
            and all(etf["runs"][f"{candidate}|{m}"]["fills_after_decisions"] for m in MULTIPLIERS),
            "G2_full_sharpe_and_drawdown": c["full"]["sharpe"] >= a["full"]["sharpe"] + 0.05
            and c["full"]["max_drawdown"] <= a["full"]["max_drawdown"],
            "G3_cagr_guard": c["full"]["cagr"] >= a["full"]["cagr"] - 0.005,
            "G4_validation": c["validation"]["sharpe"] >= a["validation"]["sharpe"]
            and c["validation"]["max_drawdown"] <= a["validation"]["max_drawdown"] + 0.01,
            "G5_development": c["development"]["sharpe"] >= a["development"]["sharpe"] - 0.05,
            "G6_2022": c["year_2022"]["total_return"] >= a["year_2022"]["total_return"],
            "G7_costs_3x": arms[f"{candidate}|3.0"]["full"]["sharpe"] >= arms["control|3.0"]["full"]["sharpe"],
            "G8_concentration": largest_share <= 0.60,
        }
        results["gates"][candidate] = {
            "gates": gates,
            "passed": all(gates.values()),
            "largest_etf_pnl_share": largest_share,
            "etf_contributions": contributions,
        }
    passing = [name for name in CANDIDATES if results["gates"][name]["passed"]]
    results["verdict"] = {
        "passing_candidates": passing,
        "preferred": max(passing, key=lambda name: arms[f"{name}|1.0"]["full"]["sharpe"]) if passing else None,
    }
    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"gates": results["gates"], "verdict": results["verdict"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
