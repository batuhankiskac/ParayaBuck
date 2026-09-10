"""Run the separately frozen, post-hoc H14c capacity diagnostic.

H14c keeps the dated V3 weights unchanged and adds at most a five percent
candidate sleeve only on dates with measured equity, gross, cash, and position
capacity.  It is an exploratory amendment defined after H14b results and is
never an economic admission or promotion decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from scripts.research_h14_turn_of_month import (
    END as H14_END,
)
from scripts.research_h14_turn_of_month import (
    H14SchedulePolicy,
    H14TurnOfMonthStrategy,
    _sessions,
    event_tuples,
    load_native_spy_bars,
    load_spy_dividend_actions,
    validate_execution_manifest,
)

try:
    from .run_equity_local_diagnostics_2026_09_09 import (
        CUTOFF,
        H14_CURRENT,
        H14_RECEIPT,
        canonical_hash,
        correlation,
        moving_block_bootstrap,
        paired_sign_permutation,
        parse_date,
        risk_metrics,
        sha256,
    )
except ImportError:
    from run_equity_local_diagnostics_2026_09_09 import (
        CUTOFF,
        H14_CURRENT,
        H14_RECEIPT,
        canonical_hash,
        correlation,
        moving_block_bootstrap,
        paired_sign_permutation,
        parse_date,
        risk_metrics,
        sha256,
    )

REPO = Path(__file__).resolve().parents[2]
H14B_RESULTS = REPO / "data/research_runs/equity_local_diagnostics_2026_09_09/diagnostic_results.json"
SCRIPT = REPO / "scripts/research/run_equity_h14c_posthoc_2026_09_09.py"
TEST = REPO / "tests/research/test_equity_h14c_posthoc_2026_09_09.py"
OUT = REPO / "data/research_runs/equity_h14c_posthoc_2026_09_09"
PROTOCOL = REPO / "docs/research/equity_h14c_posthoc_capacity_protocol_2026_09_09.md"
H14_EXECUTION_MANIFEST = REPO / "data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r4.json"
RESULTS = OUT / "results.json"
REPORT = OUT / "report.md"
MANIFEST = OUT / "manifest.json"
RECEIPT = OUT / "receipt.json"
PENDING = "PENDING_FREEZE"
CANDIDATE_WEIGHT = 0.05
EXPECTED_COSTS = (1, 2, 3)
PLACEBO_SHIFT_TRADING_DAYS = 7


def _risk_delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float | None]:
    fields = ("total_return", "cagr", "sharpe", "sortino", "max_drawdown", "worst_daily_return", "cvar_05")
    return {field: left[field] - right[field] if left[field] is not None and right[field] is not None else None for field in fields}


def _capacity(current: dict[str, Any], receipt: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    weight_history = sorted(current["weight_history"], key=lambda row: row["timestamp"])
    candidate = next(row for row in receipt["cells"] if row["cell"] == "full_2022" and row["mode"] == "candidate" and row["cost_multiplier"] == 1.0)
    candidate_symbol = {fill["symbol"] for fill in candidate["fills"]}
    if candidate_symbol != {"SPY"}:
        raise ValueError("h14c_candidate_identity_not_frozen")
    curve_dates = {parse_date(row["timestamp"]) for row in candidate["equity_curve"]}
    rows: list[dict[str, Any]] = []
    for event in candidate["event_trace"]:
        for key in ("l0", "f1", "f2", "f3"):
            when = parse_date(event[key])
            if when not in curve_dates:
                continue
            # V3 decisions stamped on ``when`` occur after that completed
            # bar and fill on the next open.  The candidate open must use the
            # strictly prior V3 target to avoid same-session look-ahead.
            prior = [row for row in weight_history if parse_date(row["timestamp"]) < when]
            weights = prior[-1]["weights"] if prior else {}
            equity = sum(abs(float(value)) for value in weights.values())
            gross = equity
            positions = sum(abs(float(value)) > 1e-12 for value in weights.values())
            cash = 1.0 - equity
            rows.append({"date": when.isoformat(), "event_month": event["month"], "candidate_symbol": "SPY", "v3_equity": equity, "v3_gross": gross, "v3_cash": cash, "v3_positions": positions, "candidate_weight": CANDIDATE_WEIGHT, "capacity_pass": equity + CANDIDATE_WEIGHT <= 0.55 and gross + CANDIDATE_WEIGHT <= 0.60 and cash - CANDIDATE_WEIGHT >= 0.40 and positions + 1 <= 6})
    return rows, "SPY"


def _shifted_placebo_curves(current: dict[str, Any]) -> dict[int, tuple[dict[date, float], set[date], set[date]]]:
    """Run the native H14 exposure rule on a fixed +7-session schedule.

    This is a post-review diagnostic only.  It uses the frozen native engine,
    bars, corporate-action mapping, and cost model, while shifting every
    original event tuple by a prespecified seven NYSE sessions.  Capacity is
    evaluated against the strictly prior V3 target at the shifted dates.
    """
    manifest = validate_execution_manifest(H14_EXECUTION_MANIFEST)
    bars_path = next(item["path"] for item in manifest["inputs"] if "equity_pit_symmetric" in item["path"] and item["path"].endswith(".csv"))
    actions_path = next(item["path"] for item in manifest["inputs"] if "corporate_actions_v5.csv" in item["path"] and not item["path"].endswith("manifest.json"))
    bars = load_native_spy_bars(bars_path, date(2016, 1, 4), H14_END)
    actions = load_spy_dividend_actions(actions_path, end=H14_END)
    sessions = _sessions(date(2022, 1, 1), H14_END + timedelta(days=20))
    session_index = {session: index for index, session in enumerate(sessions)}
    events = event_tuples(date(2022, 1, 3), H14_END)
    shifted_events = []
    for event in events:
        shifted = {key: sessions[session_index[getattr(event, key)] + PLACEBO_SHIFT_TRADING_DAYS] for key in ("p0", "l0", "f1", "f2", "f3", "f4") if session_index[getattr(event, key)] + PLACEBO_SHIFT_TRADING_DAYS < len(sessions)}
        if len(shifted) == 6 and shifted["f4"] <= H14_END:
            shifted_events.append(shifted)
    entry_dates = {item["p0"] for item in shifted_events}
    exit_dates = {item["f3"] for item in shifted_events}
    curve_start = min(entry_dates)
    selected = [bar for bar in bars if curve_start <= parse_date(bar.timestamp.isoformat()) <= H14_END]
    history = sorted(current["weight_history"], key=lambda row: row["timestamp"])
    shifted_active = {item[key] for item in shifted_events for key in ("l0", "f1", "f2", "f3")}
    shifted_curve_dates = {bar.timestamp.astimezone(bar.timestamp.tzinfo).date() for bar in selected}
    capacity_dates = set()
    for when in shifted_active & shifted_curve_dates:
        prior = [row for row in history if parse_date(row["timestamp"]) < when]
        weights = prior[-1]["weights"] if prior else {}
        equity = sum(abs(float(value)) for value in weights.values())
        positions = sum(abs(float(value)) > 1e-12 for value in weights.values())
        if equity + CANDIDATE_WEIGHT <= 0.55 and equity + CANDIDATE_WEIGHT <= 0.60 and 1.0 - equity - CANDIDATE_WEIGHT >= 0.40 and positions + 1 <= 6:
            capacity_dates.add(when)
    output: dict[int, tuple[dict[date, float], set[date], set[date]]] = {}
    for multiplier in EXPECTED_COSTS:
        strategy = H14TurnOfMonthStrategy(entry_dates, exit_dates, mode="candidate")
        engine = PortfolioBacktestEngine(strategy, H14SchedulePolicy(entry_dates | exit_dates), starting_equity=100_000.0, slippage_bps=2.0 * multiplier, spread_bps=2.0 * multiplier, commission=0.0, equity_taf_per_share=0.000195, equity_taf_cap=9.79, equity_cat_per_share=0.000003, equity_sec_fee_rate=0.0000206, equity_use_effective_dated_fees=True, benchmark_symbol="SPY", max_participation_pct=1.0, liquidity_lookback_bars=1, turnover_band=0.0, minimum_order_notional=0.0, maximum_allocation=1.0, allow_static_universe=True)
        result = engine.run(selected, corporate_actions=list(actions), liquidate_at_end=True)
        curve = {timestamp.astimezone(timestamp.tzinfo).date(): float(nav) for timestamp, nav in zip(result.curve_timestamps, result.equity_curve, strict=True)}
        output[multiplier] = (curve, shifted_active & set(curve), capacity_dates)
    return output


def _build_metrics(current: dict[str, Any], receipt: dict[str, Any], capacity_rows: list[dict[str, Any]], placebo_data: dict[int, tuple[dict[date, float], set[date], set[date]]]) -> list[dict[str, Any]]:
    v3_nav = {parse_date(row["timestamp"]): float(row["nav"]) for row in current["daily_nav"]}
    active_dates = {parse_date(row["date"]) for row in capacity_rows if row["capacity_pass"]}
    output: list[dict[str, Any]] = []
    for multiplier in EXPECTED_COSTS:
        candidate = next(row for row in receipt["cells"] if row["cell"] == "full_2022" and row["mode"] == "candidate" and row["cost_multiplier"] == float(multiplier))
        candidate_nav = {parse_date(row["timestamp"]): float(row["nav"]) for row in candidate["equity_curve"]}
        shifted_nav, shifted_active_dates, shifted_capacity_dates = placebo_data[multiplier]
        dates = sorted(set(v3_nav) & set(candidate_nav))
        v3_values: list[float] = []
        overlay_values: list[float] = []
        placebo_values: list[float] = []
        return_dates: list[date] = []
        for previous, when in zip(dates[:-1], dates[1:], strict=True):
            v3_return = v3_nav[when] / v3_nav[previous] - 1.0
            candidate_return = candidate_nav[when] / candidate_nav[previous] - 1.0
            shifted_return = shifted_nav[when] / shifted_nav[previous] - 1.0 if previous in shifted_nav and when in shifted_nav else 0.0
            v3_values.append(v3_return)
            # The receipt-bound native cell is already a 5% SPY sleeve
            # (95% cash plus 5% SPY).  Adding it directly funds the sleeve
            # from V3 cash; applying CANDIDATE_WEIGHT again would make the
            # capacity-aware overlay a 0.25% sleeve.
            overlay_values.append(v3_return + candidate_return if when in active_dates else v3_return)
            placebo_values.append(v3_return + shifted_return if when in shifted_capacity_dates else v3_return)
            return_dates.append(when)
        windows: dict[str, Any] = {}
        for name, start, end in (("full", date(2022, 1, 1), date(2025, 12, 31)), ("recent", date(2024, 1, 1), date(2025, 12, 31)), ("true_holdout", date(2026, 1, 1), CUTOFF)):
            overlay = [value for value, when in zip(overlay_values, return_dates, strict=True) if start <= when <= end]
            control = [value for value, when in zip(v3_values, return_dates, strict=True) if start <= when <= end]
            placebo = [value for value, when in zip(placebo_values, return_dates, strict=True) if start <= when <= end]
            overlay_metrics = risk_metrics(overlay)
            control_metrics = risk_metrics(control)
            placebo_metrics = risk_metrics(placebo)
            contribution = [left - right for left, right in zip(overlay, control, strict=True)]
            windows[name] = {"overlay": overlay_metrics, "v3_control": control_metrics, "delta_overlay_minus_v3": _risk_delta(overlay_metrics, control_metrics), "daily_return_correlation": correlation(overlay, control), "active_day_contribution": sum(when in active_dates for when in return_dates if start <= when <= end), "bootstrap": moving_block_bootstrap(contribution, seed=20260909), "permutation": paired_sign_permutation(contribution, seed=20260910), "placebo_plus_7_calendar": {"metrics": placebo_metrics, "delta_vs_v3": _risk_delta(placebo_metrics, control_metrics), "candidate_observation_days": sum(when in shifted_capacity_dates for when in return_dates if start <= when <= end), "status": "POST_REVIEW_DIAGNOSTIC_SHIFTED_7_TRADING_DAYS", "shifted_schedule_active_days": sum(when in shifted_active_dates for when in return_dates if start <= when <= end), "shifted_schedule_capacity_days": sum(when in shifted_capacity_dates for when in return_dates if start <= when <= end)}}
        output.append({"cost_multiplier": multiplier, "windows": windows, "calendar_year_regime": {str(year): risk_metrics([value for value, when in zip(overlay_values, return_dates, strict=True) if when.year == year]) for year in sorted({when.year for when in return_dates})}, "turnover": None, "turnover_cost_status": "NOT_RUN_NO_TARGET_TURNOVER_LEDGER", "candidate_weight_max": CANDIDATE_WEIGHT, "caps": {"equity_max": 0.55, "gross_max": 0.60, "cash_min": 0.40, "positions_max": 6, "all_pass": True}, "diagnostic_only": True})
    return output


def _atomic(files: dict[Path, bytes]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            staged.append((temporary, path))
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def render_report(results: dict[str, Any]) -> str:
    lines = ["# H14c post-hoc capacity diagnostic — 2026-09-09", "", "Status: `POST_HOC_EXPLORATORY_DIAGNOSTIC_ONLY`. This amendment was specified after H14b results, has no independent holdout, preserves every dated V3 weight, and cannot establish economic eligibility.", "", "The candidate is SPY from the receipt-bound H14 cell. Its five percent sleeve is funded from measured V3 cash only on dates where adding one position passes equity <=55%, gross <=60%, cash >=40%, and positions <=6. Inactive dates use unchanged V3 returns. Costs are the existing 1x/2x/3x candidate curves; turnover remains null because no target turnover ledger is available. Capacity uses the strictly prior V3 decision, because a target stamped on the event date fills on the next open.", "", "Full, recent, and true-holdout results include return/CAGR/Sharpe/Sortino/max drawdown/CVaR deltas, correlation, active-day contribution, calendar-year regime, block bootstrap, paired-sign permutation, and a separately labeled POST_REVIEW_DIAGNOSTIC +7 trading-session shifted native schedule. The actual schedule has thirty-one active dates across fourteen months; true holdout has zero actual active dates. The shifted schedule has 37 full-window capacity observations (19 recent, zero true-holdout), and is diagnostic only. H14a/H14b remains preserved separately and no integration or promotion is inferred.", ""]
    for row in results["metrics"]:
        lines.append(f"- {row['cost_multiplier']}x: full delta return={row['windows']['full']['delta_overlay_minus_v3']['total_return']:.9f}, CAGR={row['windows']['full']['delta_overlay_minus_v3']['cagr']:.9f}, maxDD={row['windows']['full']['delta_overlay_minus_v3']['max_drawdown']:.9f}; active days={row['windows']['full']['active_day_contribution']}; caps pass={row['caps']['all_pass']}")
    return "\n".join(lines) + "\n"


def build() -> dict[str, Any]:
    current = json.loads(H14_CURRENT.read_text(encoding="utf-8"))
    receipt = json.loads(H14_RECEIPT.read_text(encoding="utf-8"))
    capacity_rows, symbol = _capacity(current, receipt)
    placebo_data = _shifted_placebo_curves(current)
    metrics = _build_metrics(current, receipt, capacity_rows, placebo_data)
    results = {"schema": "equity-h14c-posthoc-v1", "status": "POST_HOC_EXPLORATORY_DIAGNOSTIC_ONLY", "definition_timing": "POST_HOC_AFTER_H14B_RESULTS", "candidate_symbol": symbol, "candidate_weight_max": CANDIDATE_WEIGHT, "capacity_reference": "strictly_prior_v3_decision", "placebo_shift_trading_days": PLACEBO_SHIFT_TRADING_DAYS, "placebo_status": "POST_REVIEW_DIAGNOSTIC_SHIFTED_7_TRADING_DAYS", "active_capacity_days": sum(row["capacity_pass"] for row in capacity_rows), "active_capacity_months": len({row["event_month"] for row in capacity_rows if row["capacity_pass"]}), "true_holdout_active_days": sum(row["capacity_pass"] and row["date"] >= "2026-01-01" for row in capacity_rows), "v3_weights_preserved": True, "inactive_v3_unchanged": True, "economic_run": False, "integration_candidate_count": 0, "metrics": metrics, "capacity_rows": capacity_rows}
    result_bytes = (json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    report_bytes = render_report(results).encode()
    manifest = {"schema": "equity-h14c-posthoc-manifest-v1", "status": results["status"], "result": {"path": str(RESULTS.relative_to(REPO)), "sha256": hashlib.sha256(result_bytes).hexdigest()}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest()}, "script": {"path": str(SCRIPT.relative_to(REPO)), "sha256": sha256(SCRIPT)}, "test": {"path": str(TEST.relative_to(REPO)), "sha256": sha256(TEST)}, "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": sha256(PROTOCOL)}, "inputs": {str(path.relative_to(REPO)): {"sha256": sha256(path), "bytes": path.stat().st_size} for path in (H14_CURRENT, H14_RECEIPT, H14B_RESULTS, H14_EXECUTION_MANIFEST)}, "candidate_symbol": symbol, "candidate_weight_max": CANDIDATE_WEIGHT, "placebo_shift_trading_days": PLACEBO_SHIFT_TRADING_DAYS, "economic_run": False, "integration_candidate_count": 0, "manifest_sha256": PENDING}
    manifest["manifest_sha256"] = canonical_hash(manifest, "manifest_sha256")
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    receipt_out = {"schema": "equity-h14c-posthoc-receipt-v1", "status": results["status"], "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": hashlib.sha256(manifest_bytes).hexdigest(), "canonical_sha256": manifest["manifest_sha256"]}, "result": {"path": str(RESULTS.relative_to(REPO)), "sha256": hashlib.sha256(result_bytes).hexdigest()}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest()}, "economic_run": False, "integration_candidate_count": 0, "receipt_sha256": PENDING}
    receipt_out["receipt_sha256"] = canonical_hash(receipt_out, "receipt_sha256")
    receipt_bytes = (json.dumps(receipt_out, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    _atomic({RESULTS: result_bytes, REPORT: report_bytes, MANIFEST: manifest_bytes, RECEIPT: receipt_bytes})
    verify()
    return {"status": results["status"], "active_capacity_days": results["active_capacity_days"], "manifest_sha256": manifest["manifest_sha256"], "receipt_sha256": receipt_out["receipt_sha256"]}


def verify() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("status") != "POST_HOC_EXPLORATORY_DIAGNOSTIC_ONLY" or manifest.get("candidate_symbol") != "SPY" or manifest.get("candidate_weight_max") != CANDIDATE_WEIGHT or manifest.get("economic_run") is not False or manifest.get("integration_candidate_count") != 0:
        raise ValueError("h14c_manifest_policy_invalid")
    if manifest.get("manifest_sha256") != canonical_hash(manifest, "manifest_sha256"):
        raise ValueError("h14c_manifest_self_hash_mismatch")
    for key in ("result", "report", "script", "test", "protocol"):
        declaration = manifest[key]
        path = REPO / declaration["path"]
        if not path.is_file() or sha256(path) != declaration["sha256"]:
            raise ValueError(f"h14c_hash_mismatch:{declaration['path']}")
    for relative, declaration in manifest["inputs"].items():
        path = REPO / relative
        if not path.is_file() or sha256(path) != declaration["sha256"]:
            raise ValueError(f"h14c_input_hash_mismatch:{relative}")
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    if results.get("active_capacity_days") != 31 or results.get("active_capacity_months") != 14 or results.get("true_holdout_active_days") != 0 or results.get("capacity_reference") != "strictly_prior_v3_decision" or results.get("placebo_shift_trading_days") != 7 or results.get("placebo_status") != "POST_REVIEW_DIAGNOSTIC_SHIFTED_7_TRADING_DAYS" or results.get("v3_weights_preserved") is not True or results.get("economic_run") is not False:
        raise ValueError("h14c_capacity_or_policy_invalid")
    if len(results.get("metrics", [])) != 3 or any(row.get("diagnostic_only") is not True or row.get("caps", {}).get("all_pass") is not True for row in results["metrics"]):
        raise ValueError("h14c_metrics_invalid")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("receipt_sha256") != canonical_hash(receipt, "receipt_sha256") or receipt.get("manifest", {}).get("canonical_sha256") != manifest["manifest_sha256"] or receipt.get("result", {}).get("sha256") != manifest["result"]["sha256"] or receipt.get("report", {}).get("sha256") != manifest["report"]["sha256"]:
        raise ValueError("h14c_receipt_binding_invalid")
    return {"status": "H14C_POSTHOC_VERIFIED", "active_capacity_days": 31, "active_capacity_months": 14, "economic_run": False}


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True))
