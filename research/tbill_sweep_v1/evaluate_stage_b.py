#!/usr/bin/env python3
"""tbill_sweep_v1 Stage B metrics: E2 vs E2 + T-bill sweep (S1, S2_5, S2_10) at 1x/2x costs.

Metric definitions from the archived research/paper_config_v2/evaluate.py (copied
to stage_b/research/paper_config_v2/evaluate.py from tag
archive/research/trend-robustness-v2-2026-10; session-close NAV path, window
2017-01-03 -> 2026-07-30). Reads stage_b/runs/{arm}_{cost}.json.gz and the
_audit files; writes STAGE_B_RESULTS.json next to this file.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "stage_b" / "runs"
_spec = importlib.util.spec_from_file_location("pc2_eval", HERE / "stage_b/research/paper_config_v2/evaluate.py")
pc2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc2)

ARMS = ("E2", "S1", "S2_5", "S2_10")
COSTS = ("1.0", "2.0")
E2_REFERENCE = {"1.0": 1329642.62, "2.0": 1313969.74}  # e2_integration_v1 STAGE_B_RESULTS


def _load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _bill_stats(audit: dict) -> dict:
    rows = audit["decide_log"]
    bil = [dict(row["weights"]).get("BIL", 0.0) for row in rows]
    qld = [dict(row["weights"]).get("QLD", 0.0) for row in rows]
    fills = [e for e in audit["equity_events"] if e["event"] == "fill" and e.get("symbol") == "BIL"]
    divs = [e for e in audit["equity_events"] if e["event"] == "corporate_action" and e.get("symbol") == "BIL"]
    return {
        "decisions": len(rows),
        "decisions_with_bil_target": sum(1 for b in bil if b > 0),
        "decisions_downtrend": sum(1 for r in rows if r["trend_state"] == "downtrend"),
        "max_target_gross": max(min(q, 0.98) + b for q, b in zip(qld, bil, strict=True)),
        "bil_fills": len(fills),
        "bil_corporate_action_events": len(divs),
    }


def main() -> None:
    results: dict[str, dict] = {}
    for arm in ARMS:
        for cost in COSTS:
            run = _load(RUNS / f"{arm}_{cost}.json.gz")
            s = pc2._summary(run)
            s["strategy"] = run["strategy"]
            s["sweep"] = run.get("sweep")
            s["hashes"] = run["hashes"]
            s["decisions"] = run["counts"]["decisions"]
            s["invariants"] = run["invariants"]
            s["etf_pnl"] = run.get("etf_pnl")
            s["bills"] = _bill_stats(_load(RUNS / f"{arm}_{cost}_audit.json.gz"))
            results.setdefault(arm, {})[cost] = s
    check = {c: {"expected": E2_REFERENCE[c], "got": round(results["E2"][c]["ending_nav"], 2),
                 "match": round(results["E2"][c]["ending_nav"], 2) == E2_REFERENCE[c]} for c in COSTS}
    diffs = {}
    for arm in ARMS[1:]:
        for cost in COSTS:
            a, e = results[arm][cost], results["E2"][cost]
            diffs[f"{arm}_{cost}"] = {
                "cagr_pp": (a["full"]["cagr"] - e["full"]["cagr"]) * 100,
                "max_dd_pp": (a["full"]["max_drawdown"] - e["full"]["max_drawdown"]) * 100,
                "cagr_2017_2021_pp": (a["cagr_2017_2021"] - e["cagr_2017_2021"]) * 100,
                "cagr_2022_2026_pp": (a["cagr_2022_2026"] - e["cagr_2022_2026"]) * 100,
            }
    payload = {"window": ["2017-01-03", "2026-07-30"], "e2_reference_check": check, "results": results,
               "diff_vs_E2": diffs}
    (HERE / "STAGE_B_RESULTS.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    print("E2 reference check:", check)
    for arm in ARMS:
        for cost in COSTS:
            s = results[arm][cost]
            b = s["bills"]
            print(
                f"{arm:6s} {cost}x NAV {s['ending_nav']:>13,.2f} CAGR {s['full']['cagr'] * 100:6.2f}%"
                f" DD {s['full']['max_drawdown'] * 100:5.2f}% Sharpe {s['full']['sharpe']:.3f}"
                f" turn/yr {s['turnover_per_year']:.2f} fills {s['fills']} BILfills {b['bil_fills']}"
                f" BILnotional {s['traded_notional'].get('BIL', 0):,.0f} 17-21 {s['cagr_2017_2021'] * 100:.2f}%"
                f" 22-26 {s['cagr_2022_2026'] * 100:.2f}% costs {sum(s['costs'].values()):,.0f}"
                f" acct_ok {s['accounting_ok']} maxgross {b['max_target_gross']:.4f}"
            )
    for k, v in diffs.items():
        print(k, {kk: round(vv, 3) for kk, vv in v.items()})


if __name__ == "__main__":
    main()
