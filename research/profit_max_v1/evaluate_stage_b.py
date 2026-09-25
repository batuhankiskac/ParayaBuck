#!/usr/bin/env python3
"""Stage B gates for profit maximization v1 (see PREREGISTRATION.md)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research/sleeve_candidates_v2"))
sys.path.insert(0, str(ROOT / "research/dividend_sleeve_production_v1"))

from evaluate_production import _accounting, _load, _summary  # noqa: E402

RUNS = HERE / "runs"
MULTIPLIERS = ("1.0", "2.0", "3.0")
ARMS = ("H_VOO", "Q2", "VT35", "Q2BH", "Q3", "V3full", "Pfix", "G3fix")


def main() -> int:
    runs = {
        arm: {m: _load(RUNS / f"{arm}_{m}.json.gz") for m in MULTIPLIERS if (RUNS / f"{arm}_{m}.json.gz").exists()}
        for arm in ARMS
    }
    summary = {arm: {m: _summary(run) for m, run in by.items()} for arm, by in runs.items() if by}
    h, q = summary["H_VOO"], summary["Q2"]
    gates = {
        "B1_accounting": all(_accounting(run) for run in runs["Q2"].values()),
        "B2_stream_le_30": all(max(r["structural"]["observation_stream_sizes"]) <= 30 for r in runs["Q2"].values()),
        "B3_cagr_every_cost": all(q[m]["full"]["cagr"] > h[m]["full"]["cagr"] for m in MULTIPLIERS),
        "B4_cagr_2017_2021": q["1.0"]["development"]["cagr"] > h["1.0"]["development"]["cagr"],
        "B5_cagr_2022_2026": q["1.0"]["validation"]["cagr"] > h["1.0"]["validation"]["cagr"],
    }
    out = {"gates": gates, "all_pass": all(gates.values()), "summary": summary}
    (HERE / "RESULTS_STAGE_B.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for arm, by in summary.items():
        for m, s in by.items():
            f = s["full"]
            print(
                f"{arm:7s} {m}x CAGR {f['cagr'] * 100:6.2f}%  Sharpe {f['sharpe']:.3f}  MaxDD {f['max_drawdown'] * 100:5.1f}%  "
                f"2022 {s['year_2022']['total_return'] * 100:6.1f}%  17-21 {s['development']['cagr'] * 100:6.2f}%  "
                f"22-26 {s['validation']['cagr'] * 100:6.2f}%  NAV {s['ending_nav']:,.0f}"
            )
    for name, value in gates.items():
        print(f"{name}: {'PASS' if value else 'FAIL'}")
    print("Q2 years:", {y: round(v * 100, 1) for y, v in q["1.0"]["calendar_years"].items()})
    print("H  years:", {y: round(v * 100, 1) for y, v in h["1.0"]["calendar_years"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
