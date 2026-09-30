#!/usr/bin/env python3
"""ARCHIVE-ONLY -- the committed ``RESULTS.json`` cannot be regenerated here.

Stage B metrics and preregistered gates for paper_config_v2.

The script itself imports and runs from this checkout. Its inputs do not exist
here: the ``S{0,1,2}_{1.0,2.0}.json.gz`` run dumps written by the archive-only
``run_stage_b.py`` (harness modules in tag
``archive/research/sleeve-candidates-v2-v3-2026-09``) and the gitignored
canonical v5 equity CSVs. It also OVERWRITES the committed ``RESULTS.json``;
do not run it casually.

Usage:

    python research/paper_config_v2/evaluate.py --runs <dir with S{0,1,2}_{1.0,2.0}.json.gz>

Metric definitions follow ``research/sleeve_candidates_v2/evaluate.py``
(session-close NAV path).
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
FULL = (date(2017, 1, 3), date(2026, 7, 30))
DEVELOPMENT = (date(2017, 1, 3), date(2021, 12, 31))
VALIDATION = (date(2022, 1, 3), date(2026, 7, 30))
ARMS = ("S0", "S1", "S2")
COSTS = ("1.0", "2.0")


def _returns(run: dict) -> list[tuple[date, float]]:
    rows = [(datetime.fromisoformat(ts).date(), float(nav)) for ts, nav in run["session_nav"]]
    return [(rows[i][0], rows[i][1] / rows[i - 1][1] - 1.0) for i in range(1, len(rows))]


def _metrics(returns: list[tuple[date, float]], window: tuple[date, date]) -> dict[str, float]:
    series = [value for day, value in returns if window[0] <= day <= window[1]]
    nav, peak, max_dd = 1.0, 1.0, 0.0
    for value in series:
        nav *= 1.0 + value
        peak = max(peak, nav)
        max_dd = max(max_dd, 1.0 - nav / peak)
    years = (window[1] - window[0]).days / 365.25
    mean, stdev = statistics.fmean(series), statistics.stdev(series)
    return {
        "cagr": nav ** (1.0 / years) - 1.0,
        "sharpe": mean / stdev * math.sqrt(252),
        "max_drawdown": max_dd,
    }


def _accounting(run: dict) -> bool:
    inv = run["invariants"]
    return bool(
        inv["cash_conserved"]
        and inv["nav_matches_ledger"]
        and inv["position_quantities_reconciled"]
        and inv["duplicate_fill_count"] == 0
        and inv["unowned_fill_count"] == 0
        and inv["no_future_data_access"]
    )


def _summary(run: dict) -> dict:
    series = _returns(run)
    navs = [float(nav) for _ts, nav in run["session_nav"]]
    years = (FULL[1] - FULL[0]).days / 365.25
    notional = run.get("traded_notional", {})
    mean_nav = statistics.fmean(navs)
    return {
        "full": _metrics(series, FULL),
        "cagr_2017_2021": _metrics(series, DEVELOPMENT)["cagr"],
        "cagr_2022_2026": _metrics(series, VALIDATION)["cagr"],
        "ending_nav": run["ending_nav"],
        "turnover_per_year": sum(notional.values()) / mean_nav / years,
        "traded_notional": notional,
        "fills": run["counts"]["fills"],
        "costs": run["costs"],
        "accounting_ok": _accounting(run),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", required=True)
    args = parser.parse_args()
    runs = Path(args.runs)
    summary: dict[str, dict[str, dict]] = {}
    for arm in ARMS:
        for cost in COSTS:
            path = runs / f"{arm}_{cost}.json.gz"
            if path.exists():
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    summary.setdefault(arm, {})[cost] = _summary(json.load(handle))
    s0, s1, s2 = (summary.get(arm, {}) for arm in ARMS)
    gates: dict[str, bool | None] = {}
    if "1.0" in s0 and "1.0" in s1:
        gates["S1_cagr_ge_S0_minus_0.5pp_1x"] = s1["1.0"]["full"]["cagr"] >= s0["1.0"]["full"]["cagr"] - 0.005
        gates["S1_maxdd_le_S0_plus_2pp_1x"] = (
            s1["1.0"]["full"]["max_drawdown"] <= s0["1.0"]["full"]["max_drawdown"] + 0.02
        )
    if all(cost in s1 and cost in s2 for cost in COSTS):
        for cost in COSTS:
            gates[f"S2_cagr_gt_S1_{cost}x"] = s2[cost]["full"]["cagr"] > s1[cost]["full"]["cagr"]
            gates[f"S2_maxdd_le_S1_plus_0.5pp_{cost}x"] = (
                s2[cost]["full"]["max_drawdown"] <= s1[cost]["full"]["max_drawdown"] + 0.005
            )
    adopt_s1 = all(value for key, value in gates.items() if key.startswith("S1_")) and bool(gates)
    adopt_s2 = (
        adopt_s1
        and all(value for key, value in gates.items() if key.startswith("S2_"))
        and any(key.startswith("S2_") for key in gates)
    )
    out = {
        "gates": gates,
        "adopt_S1": adopt_s1,
        "adopt_S2": adopt_s2,
        "accounting_ok": all(s["accounting_ok"] for by in summary.values() for s in by.values()),
        "summary": summary,
    }
    (HERE / "RESULTS.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for arm, by in summary.items():
        for cost, s in by.items():
            f = s["full"]
            print(
                f"{arm} {cost}x CAGR {f['cagr'] * 100:6.2f}%  Sharpe {f['sharpe']:.3f}  MaxDD {f['max_drawdown'] * 100:5.2f}%  "
                f"17-21 {s['cagr_2017_2021'] * 100:6.2f}%  22-26 {s['cagr_2022_2026'] * 100:6.2f}%  "
                f"NAV {s['ending_nav']:,.0f}  turnover {s['turnover_per_year']:.2f}x/yr  fills {s['fills']}"
                f"  acct {'ok' if s['accounting_ok'] else 'FAIL'}"
            )
    for name, value in gates.items():
        print(f"{name}: {'PASS' if value else 'FAIL'}")
    print(f"adopt S1: {adopt_s1}  adopt S2: {adopt_s2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
