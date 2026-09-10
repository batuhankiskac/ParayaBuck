#!/usr/bin/env python3
"""Verify and render the completed zero-wait overlay economic receipt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research_runs/zero_wait_overlays_2026_09_10"
RESULTS = OUT / "results.json"
RECEIPT = OUT / "receipt.json"
REPORT = REPO / "docs/research/zero_wait_overlay_results_2026_09_10.md"
IDS = ("ZWO_SMA126_BINARY", "ZWO_SMA200_BINARY", "ZWO_DRAWDOWN10_BINARY", "ZWO_VOL15_TARGET20", "ZWO_SMA200_OR_DRAWDOWN10")
WINDOWS = ("full", "recent", "single_use_true_holdout")
COSTS = (1.0, 2.0, 3.0)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> dict[str, object]:
    result = json.loads(RESULTS.read_text(encoding="utf-8"))
    if result.get("status") != "ECONOMIC_EXECUTION_COMPLETE" or result.get("economic_run") is not True:
        raise SystemExit("FAIL_CLOSED_RESULT_STATUS")
    if result.get("network") != "disabled" or result.get("no_promotion") is not True:
        raise SystemExit("FAIL_CLOSED_NETWORK_OR_PROMOTION")
    cases = result.get("cases", [])
    keys = {(row["candidate_id"], row["window"], float(row["cost_multiplier"])) for row in cases}
    expected = {(candidate, window, cost) for candidate in IDS for window in WINDOWS for cost in COSTS}
    if keys != expected or len(cases) != 45:
        raise SystemExit("FAIL_CLOSED_CASE_MATRIX")
    if set(result.get("primary_true_holdout_2x", {})) != set(IDS):
        raise SystemExit("FAIL_CLOSED_PRIMARY_MATRIX")
    caps = result.get("baseline_combined_cap_audit", {})
    if caps.get("status") != "PASS_STATIC_MONOTONIC" or caps.get("equity_max") != 0.55 or caps.get("gross_max") != 0.60 or caps.get("cash_min") != 0.40 or caps.get("positions_max") != 6:
        raise SystemExit("FAIL_CLOSED_CAP_AUDIT")
    if result.get("holm_20", {}).get("family_size") != 20:
        raise SystemExit("FAIL_CLOSED_HOLM20")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    expected_hashes = {"result_sha256": sha(RESULTS), "daily_nav_sha256": sha(OUT / "daily_nav.csv"), "exposure_ledger_sha256": sha(OUT / "exposure_ledger.jsonl"), "report_sha256": sha(REPORT)}
    for key, value in expected_hashes.items():
        if receipt.get(key) != value:
            raise SystemExit(f"FAIL_CLOSED_RECEIPT_HASH:{key}")
    return {"status": "VERIFIED", "case_count": len(cases), "candidate_count": len(IDS), "holm_family_size": 20, **expected_hashes}


def render_report() -> None:
    result = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = {(item["candidate_id"], item["window"], float(item["cost_multiplier"])): item for item in result["cases"]}
    lines = [
        "# Zero-wait defensive exposure overlay results — 2026-09-10",
        "",
        "Status: `ECONOMIC_EXECUTION_COMPLETE`; research-only, network-disabled, and no PAPER/LIVE integration or promotion.",
        "",
        "All five frozen formulas were run on the exact V3 selection/weight path over full, recent, and single-use true-holdout windows at 1x/2x/3x spread and slippage stress. Commission and regulatory fees used the existing fixed/effective-dated execution seam. The primary inferential cell is true holdout × 2x.",
        "",
        "## Primary true-holdout × 2x",
        "",
        "| Candidate | CAGR | Opportunity cost | Max DD | Daily CVaR95 | V3-down loss reduction | Turnover | Cost | Correlation | Gate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for candidate in IDS:
        m = result["primary_true_holdout_2x"][candidate]["metrics"]
        median_gate = bool(
            m["v3_down_day_loss_reduction"] is not None
            and m["v3_down_day_loss_reduction"] >= 0.05
            and m["v3_down_day_candidate_median_loss"] is not None
            and m["v3_down_day_control_median_loss"] is not None
            and m["v3_down_day_candidate_median_loss"] <= m["v3_down_day_control_median_loss"]
        )
        cap_gate = bool(m["max_exposure"] <= 0.55 + 1e-9 and m["max_holdings"] <= 6)
        gate = bool(
            m["max_drawdown"] <= m["baseline_max_drawdown"] + 0.01
            and m["daily_cvar95"] <= m["baseline_daily_cvar95"] + 0.01
            and median_gate
            and m["return_opportunity_cost"] <= 0.03
            and cap_gate
        )
        lines.append(f"| `{candidate}` | {m['cagr']:.4%} | {m['return_opportunity_cost']:.4%} | {m['max_drawdown']:.4%} | {m['daily_cvar95']:.4%} | {m['v3_down_day_loss_reduction']:.4%} | {m['turnover']:.3f} | {m['realized_cost']:.2f} | {m['correlation']:.4f} | {'PASS' if gate else 'FAIL'} |")
    lines += ["", "## Full/recent/holdout robustness", "", "The complete 45-cell matrix is retained in `results.json`. Each row records candidate and V3 full statistics, paired-day count, drawdown, daily CVaR95, CAGR opportunity cost, V3-down-day loss reduction, correlation, turnover, realized cost, max exposure, and max holdings.", "", "| Window | Cost | Candidate | CAGR | Opportunity cost | Max DD | Daily CVaR95 | V3-down loss reduction | Turnover | Max exposure | Max holdings |", "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for window in WINDOWS:
        for cost in COSTS:
            for candidate in IDS:
                m = rows[(candidate, window, cost)]
                down = m["v3_down_day_loss_reduction"]
                lines.append(f"| {window} | {cost:g}x | `{candidate}` | {m['cagr']:.4%} | {m['return_opportunity_cost']:.4%} | {m['max_drawdown']:.4%} | {m['daily_cvar95']:.4%} | {'n/a' if down is None else f'{down:.4%}'} | {m['turnover']:.3f} | {m['max_exposure']:.4f} | {m['max_holdings']} |")
    lines += ["", "## Inferential diagnostics and gates", "", "The primary cell includes 10,000 paired 20-session circular block-bootstrap resamples (seed `20260910`), 10,000 circular signal permutations/placebos (seed `20260911`), and a one-V3-decision native schedule placebo. The predeclared Holm family is 5 candidates × 4 primary endpoints = 20 tests; the stored adjusted p-values and gate status are in `results.json`.", "", f"Holm family status: `{result['holm_20']}`.", "", "Capacity audit: equity gross <= 0.55, combined gross <= 0.60, cash >= 0.40, and positions <= 6. The static monotonic proof is recorded in the frozen manifest; all executed cases report max exposure and max holdings. No candidate selection or parameter tuning was performed after observing outcomes.", "", "ZERO_WAIT_OVERLAY_ECONOMIC_COMPLETE"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    verification = verify()
    render_report()
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    receipt["report_sha256"] = sha(REPORT)
    RECEIPT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verification["report_sha256"] = receipt["report_sha256"]
    print(json.dumps(verification, sort_keys=True))
