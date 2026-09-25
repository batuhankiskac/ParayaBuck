#!/usr/bin/env python3
"""Compare the production dividend-sleeve runs with shipped A and research arm C.

    .venv/bin/python research/dividend_sleeve_production_v1/evaluate_production.py \
        --out research/dividend_sleeve_production_v1/RESULTS.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research/sleeve_candidates_v2"))

from evaluate import DEVELOPMENT, FULL, STRESS_2022, VALIDATION, _metrics, _yearly  # noqa: E402

RUNS = HERE / "runs"
ARCHIVED_A = ROOT / "research/sleeve_candidates_v2/stage_b/runs/A_1.0.json.gz"
ARCHIVE = ROOT / "research/sleeve_candidates_v2/stage_b/runs/v1_archive"
RESEARCH_C = ROOT / "research/sleeve_candidates_v3/stage_b/runs"
MULTIPLIERS = ("1.0", "2.0", "3.0")


def _load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _returns(run: dict) -> list[tuple[date, float]]:
    rows = [(datetime.fromisoformat(ts).date(), float(nav)) for ts, nav in run["session_nav"]]
    return [(rows[i][0], rows[i][1] / rows[i - 1][1] - 1.0) for i in range(1, len(rows))]


def _summary(run: dict) -> dict:
    series = _returns(run)
    return {
        "full": _metrics(series, FULL),
        "development": _metrics(series, DEVELOPMENT),
        "validation": _metrics(series, VALIDATION),
        "year_2022": _metrics(series, STRESS_2022),
        "calendar_years": _yearly(series),
        "ending_nav": run["ending_nav"],
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    archived_a = _load(ARCHIVED_A)
    fresh_a = _load(RUNS / "A_1.0.json.gz")
    production = {m: _load(RUNS / f"P_{m}.json.gz") for m in MULTIPLIERS if (RUNS / f"P_{m}.json.gz").exists()}
    research = {m: _load(RESEARCH_C / f"dividend_growth_v1_C_{m}.json.gz") for m in MULTIPLIERS}
    a = _summary(archived_a)
    a_costs = {m: _summary(_load(ARCHIVE / f"A_{m}.json.gz")) for m in MULTIPLIERS}
    p = {m: _summary(run) for m, run in production.items()}
    c = {m: _summary(run) for m, run in research.items()}
    p1 = p["1.0"]
    gates = {
        "R0_new_code_reproduces_shipped_A": fresh_a["hashes"] == archived_a["hashes"]
        and fresh_a["ending_nav"] == archived_a["ending_nav"],
        "R1_accounting_invariants": all(_accounting(run) for run in (fresh_a, *production.values())),
        "R2_stream_within_iex_30": all(
            max(run["structural"]["observation_stream_sizes"]) <= 30 for run in production.values()
        ),
        "R3_non_sleeve_slots_within_8": all(
            run["structural"]["max_non_etf_slot_claims_in_violations"] <= 8 for run in production.values()
        ),
        "R4_cagr_not_below_A": p1["full"]["cagr"] >= a["full"]["cagr"],
        "R5_sharpe_not_below_A": p1["full"]["sharpe"] >= a["full"]["sharpe"],
        "R6_max_drawdown_not_above_A": p1["full"]["max_drawdown"] <= a["full"]["max_drawdown"],
        "R7_2022_not_worse_than_A": p1["year_2022"]["total_return"] >= a["year_2022"]["total_return"],
        "R8_cagr_not_below_A_at_every_cost": all(p[m]["full"]["cagr"] >= a_costs[m]["full"]["cagr"] for m in p),
    }
    results = {
        "gates": gates,
        "shipped_A": a,
        "shipped_A_by_cost": a_costs,
        "production_P": p,
        "research_C_dividend_growth_v1": c,
        "structural": {m: run["structural"] for m, run in production.items()},
        "sleeve_pnl": {m: run["etf_pnl"] for m, run in production.items()},
        "delta_P_vs_A_1x": {
            key: p1["full"][key] - a["full"][key] for key in ("cagr", "sharpe", "max_drawdown", "volatility")
        },
        "delta_P_vs_C_1x": {
            key: p1["full"][key] - c["1.0"]["full"][key] for key in ("cagr", "sharpe", "max_drawdown", "volatility")
        },
    }
    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, value in gates.items():
        print(f"{name}: {'PASS' if value else 'FAIL'}")
    for label, summary in (("A", a), *((f"P {m}x", s) for m, s in p.items()), *((f"C {m}x", s) for m, s in c.items())):
        full = summary["full"]
        print(
            f"{label:7s} CAGR {full['cagr'] * 100:5.2f}%  Sharpe {full['sharpe']:.3f}  "
            f"MaxDD {full['max_drawdown'] * 100:5.2f}%  2022 {summary['year_2022']['total_return'] * 100:6.2f}%  "
            f"NAV {summary['ending_nav']:,.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
