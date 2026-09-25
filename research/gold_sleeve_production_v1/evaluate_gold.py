#!/usr/bin/env python3
"""Compare gold+dividend arms G1–G3 with the shipped dividend sleeve P (pre-registered gates).

    .venv/bin/python research/gold_sleeve_production_v1/evaluate_gold.py \
        --out research/gold_sleeve_production_v1/RESULTS.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research/sleeve_candidates_v2"))
sys.path.insert(0, str(ROOT / "research/dividend_sleeve_production_v1"))

from evaluate_production import _accounting, _load, _summary  # noqa: E402

RUNS = HERE / "runs"
P_RUNS = ROOT / "research/dividend_sleeve_production_v1/runs"
ARMS = ("G1", "G2", "G3")
MULTIPLIERS = ("1.0", "2.0", "3.0")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    p = {m: _summary(_load(P_RUNS / f"P_{m}.json.gz")) for m in MULTIPLIERS}
    results: dict = {"P": p, "arms": {}}
    for arm in ARMS:
        runs = {m: _load(RUNS / f"{arm}_{m}.json.gz") for m in MULTIPLIERS if (RUNS / f"{arm}_{m}.json.gz").exists()}
        if "1.0" not in runs:
            continue
        s = {m: _summary(run) for m, run in runs.items()}
        g1 = s["1.0"]
        gates = {
            "H1_accounting": all(_accounting(run) for run in runs.values()),
            "H2_stream_le_30": all(max(r["structural"]["observation_stream_sizes"]) <= 30 for r in runs.values()),
            "H3_non_sleeve_slots_le_8": all(
                r["structural"]["max_non_etf_slot_claims_in_violations"] <= 8 for r in runs.values()
            ),
            "H4_cagr": g1["full"]["cagr"] >= p["1.0"]["full"]["cagr"],
            "H5_sharpe": g1["full"]["sharpe"] >= p["1.0"]["full"]["sharpe"],
            "H6_max_drawdown": g1["full"]["max_drawdown"] <= p["1.0"]["full"]["max_drawdown"],
            "H7_2022": g1["year_2022"]["total_return"] >= p["1.0"]["year_2022"]["total_return"],
            "H8_cagr_2x_3x": all(m in s for m in ("2.0", "3.0"))
            and all(s[m]["full"]["cagr"] >= p[m]["full"]["cagr"] for m in ("2.0", "3.0")),
            "H9_validation_cagr": g1["validation"]["cagr"] >= p["1.0"]["validation"]["cagr"],
        }
        results["arms"][arm] = {
            "gates": gates,
            "all_pass": all(gates.values()),
            "summary": s,
            "structural": {m: r["structural"] for m, r in runs.items()},
            "sleeve_pnl": {m: r["etf_pnl"] for m, r in runs.items()},
        }
    passing = [a for a, r in results["arms"].items() if r["all_pass"]]
    results["selected"] = max(
        passing, key=lambda a: results["arms"][a]["summary"]["1.0"]["full"]["sharpe"], default=None
    )
    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows = [("P", m, p[m]) for m in MULTIPLIERS] + [
        (a, m, r["summary"][m]) for a, r in results["arms"].items() for m in r["summary"]
    ]
    for label, m, s in rows:
        f = s["full"]
        print(
            f"{label:3s} {m}x CAGR {f['cagr'] * 100:5.2f}%  Sharpe {f['sharpe']:.3f}  MaxDD {f['max_drawdown'] * 100:5.2f}%  "
            f"2022 {s['year_2022']['total_return'] * 100:6.2f}%  dev {s['development']['cagr'] * 100:5.2f}%  "
            f"val {s['validation']['cagr'] * 100:5.2f}%  NAV {s['ending_nav']:,.0f}"
        )
    for a, r in results["arms"].items():
        print(a, "ALL PASS" if r["all_pass"] else "FAIL", {k: v for k, v in r["gates"].items() if not v})
    print("selected:", results["selected"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
