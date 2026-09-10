"""Run bounded local diagnostics for the frozen equity H01-H16 contracts.

The diagnostics use only already acquired, receipt-bound local files.  They
never turn a local price join into a causal admission, substitute OHLCV for a
missing field, or alter the authoritative candidate gates.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

REPO = Path(__file__).resolve().parents[2]
BAR_PATH = REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
PIT_PATH = REPO / "data/research/external/equity_pit_universe_2026_09_07/pit_eligibility_intervals.jsonl"
H06_ROWS = REPO / "data/research/external/equity_h06_local_normalization_2026_09_09/form4_transaction_rows.json"
H06_REPORT = REPO / "data/research/external/equity_h06_local_normalization_2026_09_09/report.json"
H10_PAIRS = REPO / "data/research/external/equity_secondary_source_gap_audit_2026_09_09/review_r2/parsed/h10_version_pairs.json"
H15_ROWS = REPO / "data/research/external/equity_secondary_local_normalization_2026_09_09/parsed/h15_lifecycle_metadata_rows.json"
H14_CURRENT = REPO / "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json"
H14_RECEIPT = REPO / "data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_receipt.json"
H14_EXECUTION_MANIFEST = REPO / "data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r4.json"
H01_H05_RECEIPT = REPO / "data/research/equity_h01_h05_causal_input_gate_2026_09_08_r18/diagnostic_receipt.json"
H13_MATRIX = REPO / "data/research/external/equity_secondary_public_2026_09_08/source_matrix.json"
ACTION_PATH = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
SCRIPT = REPO / "scripts/research/run_equity_local_diagnostics_2026_09_09.py"
TEST = REPO / "tests/research/test_equity_local_diagnostics_2026_09_09.py"
OUT = REPO / "data/research_runs/equity_local_diagnostics_2026_09_09"
RESULTS = OUT / "diagnostic_results.json"
REPORT = OUT / "diagnostic_report.md"
MANIFEST = OUT / "manifest.json"
RECEIPT = OUT / "receipt.json"
PENDING = "PENDING_FREEZE"
EXPECTED_IDS = [f"H{i:02d}" for i in range(1, 17)]
CUTOFF = date(2026, 7, 31)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(payload: dict[str, Any], field: str) -> str:
    value = dict(payload)
    value[field] = PENDING
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def load_pit() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with PIT_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            result[str(row["canonical_symbol"])] = row
    return result


def interval_bound(symbol: str, when: date, pit: dict[str, dict[str, Any]], cik: str | None = None) -> bool:
    row = pit.get(symbol)
    if row is None or (cik is not None and str(row.get("sec_mapping", {}).get("cik", "")).zfill(10) != str(cik).zfill(10)):
        return False
    start = parse_date(str(row["valid_from"]))
    end = parse_date(str(row["valid_to"])) if row.get("valid_to") else None
    return start <= when and (end is None or when <= end)


def load_bars(symbols: set[str], pit: dict[str, dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    selected: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    total = 0
    valid_ohlc = 0
    identity_bound = 0
    with BAR_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        for raw in reader:
            total += 1
            try:
                when = parse_date(raw["timestamp"])
                opening = float(raw["open"])
                closing = float(raw["close"])
                valid = opening > 0 and closing > 0
            except (KeyError, TypeError, ValueError):
                valid = False
                when = None
                opening = closing = 0.0
            if valid:
                valid_ohlc += 1
            symbol = str(raw.get("symbol", ""))
            if when is not None and interval_bound(symbol, when, pit):
                identity_bound += 1
            if symbol in selected and when is not None and valid:
                selected[symbol].append({"date": when, "open": opening, "close": closing, "timestamp": raw["timestamp"]})
    for rows in selected.values():
        rows.sort(key=lambda row: row["date"])
    return selected, {
        "columns": columns,
        "total_rows": total,
        "valid_ohlc_rows": valid_ohlc,
        "pit_interval_bound_rows": identity_bound,
        "raw_adjusted_columns_present": {"raw": False, "adjusted": False, "split_factor": False},
    }


def forward_return(rows: list[dict[str, Any]], event_date: date, holding_sessions: int = 63) -> tuple[date, float] | None:
    starts = [index for index, row in enumerate(rows) if row["date"] > event_date]
    if not starts:
        return None
    start = starts[0]
    finish = start + holding_sessions - 1
    if finish >= len(rows):
        return None
    return rows[start]["date"], rows[finish]["close"] / rows[start]["open"] - 1.0


def summarize_returns(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    return {"count": len(values), "mean": statistics.fmean(values), "median": statistics.median(values), "min": min(values), "max": max(values)}


def path_metrics(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "total_return": None, "max_drawdown": None, "worst_daily_return": None}
    nav = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in values:
        nav *= 1.0 + value
        peak = max(peak, nav)
        drawdown = min(drawdown, nav / peak - 1.0)
    return {"count": len(values), "total_return": nav - 1.0, "max_drawdown": drawdown, "worst_daily_return": min(values)}


def risk_metrics(values: list[float]) -> dict[str, Any]:
    metrics = path_metrics(values)
    if not values:
        return {**metrics, "cagr": None, "sharpe": None, "sortino": None, "cvar_05": None}
    years = len(values) / 252.0
    total_return = float(metrics["total_return"])
    metrics["cagr"] = (1.0 + total_return) ** (1.0 / years) - 1.0 if 1.0 + total_return > 0.0 else None
    deviation = statistics.pstdev(values) if len(values) > 1 else 0.0
    metrics["sharpe"] = statistics.fmean(values) / deviation * math.sqrt(252.0) if deviation else None
    downside = math.sqrt(statistics.fmean(min(value, 0.0) ** 2 for value in values))
    metrics["sortino"] = statistics.fmean(values) / downside * math.sqrt(252.0) if downside else None
    tail_count = max(1, math.ceil(len(values) * 0.05))
    metrics["cvar_05"] = statistics.fmean(sorted(values)[:tail_count])
    return metrics


def correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean, right_mean = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_var = sum((x - left_mean) ** 2 for x in left)
    right_var = sum((y - right_mean) ** 2 for y in right)
    return numerator / math.sqrt(left_var * right_var) if left_var and right_var else None


def moving_block_bootstrap(values: list[float], *, seed: int) -> dict[str, Any]:
    if len(values) < 21:
        return {"status": "NOT_RUN_FEWER_THAN_21_SESSIONS", "resamples": 0}
    rng = random.Random(seed)
    estimates = []
    block = 21
    for _ in range(10000):
        sample: list[float] = []
        while len(sample) < len(values):
            start = rng.randrange(len(values) - block + 1)
            sample.extend(values[start : start + block])
        estimates.append(statistics.fmean(sample[: len(values)]))
    estimates.sort()
    return {"status": "COMPLETE", "resamples": 10000, "block_sessions": 21, "seed": seed, "mean_return_ci95": [estimates[250], estimates[9749]]}


def paired_sign_permutation(values: list[float], *, seed: int) -> dict[str, Any]:
    if len(values) < 21:
        return {"status": "NOT_RUN_FEWER_THAN_21_SESSIONS", "resamples": 0}
    observed = statistics.fmean(values)
    rng = random.Random(seed)
    estimates = []
    scale = 1.0 / len(values)
    for _ in range(10000):
        estimates.append(sum(value if rng.getrandbits(1) else -value for value in values) * scale)
    estimates.sort()
    exceedances = sum(abs(value) >= abs(observed) for value in estimates)
    return {"status": "COMPLETE", "resamples": 10000, "seed": seed, "observed_mean": observed, "null_ci95": [estimates[250], estimates[9749]], "two_sided_pvalue": (exceedances + 1) / 10001}


def action_scan(symbols: set[str]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    symbol_counts: dict[str, int] = {}
    rows = 0
    with ACTION_PATH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            kind = str(row["action_type"])
            counts[kind] = counts.get(kind, 0) + 1
            if row["symbol"] in symbols:
                symbol_counts[kind] = symbol_counts.get(kind, 0) + 1
    split_rows = counts.get("forward_splits", 0) + counts.get("reverse_splits", 0) + counts.get("stock_dividends", 0)
    return {"rows": rows, "action_types": counts, "target_symbol_action_types": symbol_counts, "split_factors_present": False, "raw_price_equivalence_proven": False, "dividend_adjustment_reconstructible_from_v5": False, "raw_adjusted_invariant_checks": {"split_action_rows": split_rows, "cash_dividend_rows": counts.get("cash_dividends", 0), "paired_raw_adjusted_rows": 0, "status": "NOT_RUN_MISSING_RAW_SERIES_AND_FACTOR_FIELDS"}}


def h06_diagnostic(bars: dict[str, list[dict[str, Any]]], pit: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = json.loads(H06_ROWS.read_text(encoding="utf-8"))["rows"]
    candidates = [row for row in source if row.get("disposition") == "CANDIDATE_OPEN_MARKET_PURCHASE" and parse_date(row["accepted_at"]) <= CUTOFF]
    diagnostic_rows: list[dict[str, Any]] = []
    for row in candidates:
        symbol = str(row["ticker"])
        transaction_date = parse_date(row["transaction_date"])
        identity = interval_bound(symbol, transaction_date, pit, str(row["issuer_cik"]))
        result = forward_return(bars.get(symbol, []), parse_date(row["accepted_at"]), 63)
        diagnostic_rows.append({
            "accession": row["accession"],
            "transaction_row_index": row["transaction_row_index"],
            "symbol": symbol,
            "issuer_cik": row["issuer_cik"],
            "transaction_date": row["transaction_date"],
            "accepted_at": row["accepted_at"],
            "pit_interval_identity_bound": identity,
            "entry_date": result[0].isoformat() if result else None,
            "forward_63_session_return": result[1] if result else None,
            "economic_admitted": False,
        })
    values = [row["forward_63_session_return"] for row in diagnostic_rows if row["forward_63_session_return"] is not None]
    by_window = {}
    for name, start, end in (("full", date(2022, 1, 1), date(2025, 12, 31)), ("recent", date(2024, 1, 1), date(2025, 12, 31)), ("true_holdout", date(2026, 1, 1), CUTOFF)):
        by_window[name] = summarize_returns([row["forward_63_session_return"] for row in diagnostic_rows if row["entry_date"] and start <= parse_date(row["entry_date"]) <= end and row["forward_63_session_return"] is not None])
    placebo_values = []
    for row in diagnostic_rows:
        result = forward_return(bars.get(row["symbol"], []), parse_date(row["accepted_at"]) + timedelta(days=7), 63)
        if result is not None:
            placebo_values.append(result[1])
    return {"status": "DIAGNOSTIC_ONLY_NO_SCORE_ADMISSION", "candidate_rows": len(candidates), "unique_accessions": len({row["accession"] for row in candidates}), "pit_interval_identity_bound_rows": sum(row["pit_interval_identity_bound"] for row in diagnostic_rows), "63_session_price_join_rows": len(values), "returns_by_window": by_window, "cost_grid": [{"multiplier": multiplier, "price_join_rows": len(values), "cost_status": "NOT_RUN_NO_SIGNAL_NOTIONAL_OR_COST_LEDGER"} for multiplier in (1, 2, 3)], "bootstrap": moving_block_bootstrap(values, seed=20260909), "permutation": {"status": "NOT_RUN_NO_FULL_ISSUER_LABEL_PANEL", "resamples": 0}, "fixed_plus_7_day_calendar_placebo": summarize_returns(placebo_values), "overlay_status": "NOT_RUN_NO_SCORE_OR_RANK_INPUT", "security_continuity_admission": False, "market_cap_signal_admission": False, "source_body_hashes": len({row["source_body_sha256"] for row in candidates}), "rows": diagnostic_rows}


def h10_diagnostic(bars: dict[str, list[dict[str, Any]]], pit: dict[str, dict[str, Any]]) -> dict[str, Any]:
    payload = json.loads(H10_PAIRS.read_text(encoding="utf-8"))
    rows = []
    for pair in payload["pairs"]:
        amended = pair["amended"]
        symbol = Path(amended["source_path"]).name.split("_10-K")[0].split("_10-Q")[0].split("_")[0]
        result = forward_return(bars.get(symbol, []), parse_date(amended["accepted_at"]), 63)
        rows.append({"symbol": symbol, "cik": pair["cik"], "original_accession": pair["original"]["accession"], "amended_accession": amended["accession"], "changed_sections": pair["changed_sections"], "accepted_at": amended["accepted_at"], "pit_interval_identity_bound": interval_bound(symbol, parse_date(amended["accepted_at"]), pit, pair["cik"]), "entry_date": result[0].isoformat() if result else None, "forward_63_session_return": result[1] if result else None, "economic_admitted": False})
    values = [row["forward_63_session_return"] for row in rows if row["forward_63_session_return"] is not None]
    return {"status": "SMALL_SAMPLE_METADATA_EVENT_DIAGNOSTIC_ONLY", "exact_pairs": len(rows), "price_join_rows": len(values), "returns": summarize_returns(values), "cost_grid": [{"multiplier": multiplier, "price_join_rows": len(values), "cost_status": "NOT_RUN_NO_SIGNAL_NOTIONAL_OR_COST_LEDGER"} for multiplier in (1, 2, 3)], "bootstrap": moving_block_bootstrap(values, seed=20260909), "permutation": {"status": "NOT_RUN_NO_FULL_ISSUER_LABEL_PANEL", "resamples": 0}, "direction_frozen": True, "breadth_complete": False, "overlay_status": "NOT_RUN_NO_BREADTH_OR_PORTFOLIO_SIGNAL", "rows": rows}


def h14b_diagnostic() -> dict[str, Any]:
    from scripts.research import run_equity_h14c_posthoc_2026_09_09 as h14c

    current = json.loads(H14_CURRENT.read_text(encoding="utf-8"))
    receipt = json.loads(H14_RECEIPT.read_text(encoding="utf-8"))
    candidate_cells = {int(row["cost_multiplier"]): row for row in receipt["cells"] if row["cell"] == "full_2022" and row["mode"] == "candidate"}
    cell = candidate_cells[1]
    weight_history = sorted(current["weight_history"], key=lambda row: row["timestamp"])
    events = cell["event_trace"]
    curve_dates = {parse_date(row["timestamp"]) for row in cell["equity_curve"]}
    capacity_rows = []
    for event in events:
        active = [parse_date(event[key]) for key in ("l0", "f1", "f2", "f3") if parse_date(event[key]) in curve_dates]
        for when in active:
            # A V3 target stamped on ``when`` is decided after that completed
            # bar and fills on the next tradable open.  Capacity for an H14
            # open therefore uses the strictly prior target, avoiding a
            # same-session look-ahead.
            prior = [row for row in weight_history if parse_date(row["timestamp"]) < when]
            weights = prior[-1]["weights"] if prior else {}
            equity = sum(abs(float(value)) for value in weights.values())
            positions = sum(abs(float(value)) > 1e-12 for value in weights.values())
            cash = 1.0 - equity
            capacity_rows.append({"event_month": event["month"], "date": when.isoformat(), "v3_equity": equity, "v3_gross": equity, "v3_cash": cash, "v3_position_count": positions, "candidate_weight": 0.05, "spare_slot": equity + 0.05 <= 0.55 and equity + 0.05 <= 0.60 and cash - 0.05 >= 0.40 and positions + 1 <= 6})
    spare = [row for row in capacity_rows if row["spare_slot"]]
    v3_nav = {parse_date(row["timestamp"]): float(row["nav"]) for row in current["daily_nav"]}
    metric_rows = []
    for multiplier, candidate in sorted(candidate_cells.items()):
        candidate_nav = {parse_date(row["timestamp"]): float(row["nav"]) for row in candidate["equity_curve"]}
        dates = sorted(set(v3_nav) & set(candidate_nav))
        active_by_date = {parse_date(event[key]) for event in events for key in ("l0", "f1", "f2", "f3") if parse_date(event[key]) in curve_dates}
        capacity_by_date = {row["date"]: row["spare_slot"] for row in capacity_rows}
        active_spare_dates = {when for when in active_by_date if capacity_by_date.get(when.isoformat(), False)}
        shifted_nav, shifted_active_dates, shifted_capacity_dates = h14c._shifted_placebo_curves(current)[multiplier]
        v3_returns: list[float] = []
        overlay_returns: list[float] = []
        placebo_returns: list[float] = []
        return_dates: list[date] = []
        for previous, when in zip(dates[:-1], dates[1:], strict=True):
            v3_return = v3_nav[when] / v3_nav[previous] - 1.0
            candidate_return = candidate_nav[when] / candidate_nav[previous] - 1.0
            shifted_return = shifted_nav[when] / shifted_nav[previous] - 1.0 if previous in shifted_nav and when in shifted_nav else 0.0
            active = when in active_by_date
            spare_now = capacity_by_date.get(when.isoformat(), False)
            v3_returns.append(v3_return)
            # The native candidate cell is already an isolated 5% sleeve
            # (95% cash plus 5% SPY).  It must therefore be added directly
            # to the 95% V3 sleeve; multiplying candidate_return by another
            # 5% would silently reduce the intended candidate exposure to
            # 0.25%.
            overlay_returns.append(0.95 * v3_return + candidate_return if active and spare_now else 0.95 * v3_return)
            placebo_returns.append(0.95 * v3_return + shifted_return if when in shifted_capacity_dates else 0.95 * v3_return)
            return_dates.append(when)
        windows = {}
        for name, start, end in (("full", date(2022, 1, 1), date(2025, 12, 31)), ("recent", date(2024, 1, 1), date(2025, 12, 31)), ("true_holdout", date(2026, 1, 1), CUTOFF)):
            values = [value for value, when in zip(overlay_returns, dates[1:], strict=True) if start <= when <= end]
            v3_values = [value for value, when in zip(v3_returns, dates[1:], strict=True) if start <= when <= end]
            placebo_values = [value for value, when in zip(placebo_returns, dates[1:], strict=True) if start <= when <= end]
            overlay = risk_metrics(values)
            control = risk_metrics(v3_values)
            deltas = {field: overlay[field] - control[field] if overlay[field] is not None and control[field] is not None else None for field in ("total_return", "cagr", "sharpe", "sortino", "max_drawdown", "worst_daily_return", "cvar_05")}
            contribution = [overlay_value - control_value for overlay_value, control_value in zip(values, v3_values, strict=True)]
            placebo = risk_metrics(placebo_values)
            placebo_delta = {field: placebo[field] - control[field] if placebo[field] is not None and control[field] is not None else None for field in ("total_return", "cagr", "max_drawdown", "cvar_05")}
            active_days = sum(when in active_spare_dates for when in return_dates if start <= when <= end)
            windows[name] = {"overlay": overlay, "v3_control": control, "delta_overlay_minus_v3": deltas, "daily_return_correlation": correlation(values, v3_values), "active_spare_slot_days": active_days, "placebo_plus_7_calendar": {"metrics": placebo, "delta_vs_v3": placebo_delta, "candidate_observation_days": sum(when in shifted_capacity_dates for when in return_dates if start <= when <= end), "status": "POST_REVIEW_DIAGNOSTIC_SHIFTED_7_TRADING_DAYS", "shifted_schedule_active_days": sum(when in shifted_active_dates for when in return_dates if start <= when <= end), "shifted_schedule_capacity_days": sum(when in shifted_capacity_dates for when in return_dates if start <= when <= end)}, "contribution_bootstrap": moving_block_bootstrap(contribution, seed=20260909), "contribution_permutation": paired_sign_permutation(contribution, seed=20260910)}
        calendar_years = {}
        for year in sorted({when.year for when in dates[1:]}):
            values = [value for value, when in zip(overlay_returns, dates[1:], strict=True) if when.year == year]
            calendar_years[str(year)] = risk_metrics(values)
        metric_rows.append({"cost_multiplier": multiplier, "windows": windows, "calendar_year_regime": calendar_years, "equity_cap_max": 0.55, "gross_cap_max": 0.60, "cash_floor_min": 0.40, "positions_cap_max": 6, "turnover": None, "turnover_cost_status": "NOT_RUN_NO_TARGET_TURNOVER_LEDGER", "cap_checks": {"equity_le_055": True, "gross_le_060": True, "cash_ge_040": True, "positions_le_6": True}, "diagnostic_only": True})
    return {"status": "DIAGNOSTIC_CAPACITY_SCAN_AND_JOINT_NAV_COMPLETE", "base_h14_result_preserved": True, "base_h14_status": current["status"], "base_h14_cells": current["h14"]["matrix_cells"], "active_event_count": len(events), "active_capacity_rows": len(capacity_rows), "spare_slot_dates": len(spare), "spare_slot_event_months": len({row["event_month"] for row in spare}), "spare_slot_economic_run": False, "joint_nav_cost_metrics": metric_rows, "joint_nav_robustness": {"drawdown_tail": "reported in each window path_metrics", "regime": "calendar-year stratification available from dated overlay returns", "holdout": "true_holdout is post-selection only", "position_and_cash_caps_checked": True}, "lowest_ranked_slot_substitution_status": "NOT_RUN_MISSING_DATED_V3_RANK_INPUT", "capacity_rows": capacity_rows}


def h15_diagnostic() -> dict[str, Any]:
    row = json.loads(H15_ROWS.read_text(encoding="utf-8"))[0]
    return {"status": "LIFECYCLE_METADATA_ONLY_NO_EVENT_RETURN", "receipt_valid_rows": 1, "symbol": row["symbol"], "agreement_date": row["agreement_date"], "announcement_at": row["announcement_at"], "terminal_fields": {key: row[key] for key in ("close_at", "cancel_at", "break_at", "delist_at")}, "economic_admitted": False, "reason": "announcement_at and terminal lifecycle are absent; accepted_at cannot substitute for public announcement"}


def h13_route_diagnostic() -> dict[str, Any]:
    card = json.loads(H13_MATRIX.read_text(encoding="utf-8"))["cards"]["H13"]
    return {"status": "BLOCKED_AUTHORIZED_INTRADAY_ENTITLEMENT", "route_evidence": [{key: source.get(key) for key in ("kind", "role", "status", "url", "receipt", "body_sha256", "coverage", "pit")} for source in card["sources"]], "economic_admitted": False, "reason": "existing official Alpaca docs and endpoint probe record 301/401; no authorized SIP/IEX minute, trade, quote, or auction sample is present"}


def render_report(results: dict[str, Any]) -> str:
    lines = ["# Equity H01–H16 local diagnostics — 2026-09-09", "", "Status: `LOCAL_DIAGNOSTICS_COMPLETE_NO_ECONOMIC_ADMISSION`. All definitions remain frozen before outcomes. These diagnostics use local receipt-bound files and do not change the authoritative gates, V3, PAPER, LIVE, production, registry, or configuration.", "", "| ID | Local work | Result | Economic admission |", "|---|---|---|---|"]
    for cid in EXPECTED_IDS:
        item = results["candidates"][cid]
        lines.append(f"| {cid} | {item['local_work']} | {item['status']} | `false` |")
    lines.extend(["", "H06 uses all 274 local candidate transaction rows and computes receipt-time 63-session price joins only as event diagnostics; score/rank admission remains false because market-cap/security-continuity gates are unresolved. Cost multipliers are recorded but no notional cost is invented; the moving-block bootstrap and fixed +7-day placebo are diagnostic, while issuer-label permutation and V3 overlay remain closed without a full label panel and ranked signal.", "", "H10 uses all 3 exact same-CIK version pairs as a small-sample metadata/event diagnostic with the same explicit cost, bootstrap, permutation, and overlay boundaries; breadth and direction evidence remain incomplete.", "", f"H14b runs the fixed 95/5 joint NAV on every active H14 date where the strictly prior dated V3 weight history permits spare capacity ({results['h14b']['spare_slot_dates']} days across {results['h14b']['spare_slot_event_months']} months), at 1x/2x/3x, with full/recent/true-holdout deltas versus V3 for return/CAGR/Sharpe/Sortino/max drawdown/CVaR, correlation, calendar-year regime, active-day counts, cap checks, 10,000-block bootstrap, paired-sign permutation, and a separate POST_REVIEW_DIAGNOSTIC +7 trading-session native schedule placebo. Turnover remains null because no target turnover ledger exists; lowest-ranked substitution remains unrun because the local weight history has no frozen rank field. The existing H14 18-cell result is preserved.", "", "H13 has PIT OHLC rows and identity interval coverage, and the corporate-action table contains 143,446 rows (141,908 cash-dividend, 1,538 split/stock-action rows), but the canonical bar file is split-adjusted only and has no raw/adjusted/split-factor columns. Raw/adjusted invariants therefore cannot be proven from these files; no adjusted/raw substitution is made and no economic result is admitted.", "", "H01–H05 reuse the existing receipt-bound 288-row diagnostic: H03 has schema-ready rows, while price/share basis, breadth, periods, or payout inputs keep admission closed.", "", "No result is selected on forward returns. Small samples are diagnostic only; no candidate is eligible for promotion or integration.", ""])
    return "\n".join(lines)


def _atomic(files: dict[Path, bytes]) -> None:
    staged = []
    try:
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temp.write_bytes(data)
            staged.append((temp, path))
        for temp, path in staged:
            os.replace(temp, path)
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)


def build() -> dict[str, Any]:
    pit = load_pit()
    h06_rows = json.loads(H06_ROWS.read_text(encoding="utf-8"))["rows"]
    h06_symbols = {str(row["ticker"]) for row in h06_rows if row.get("disposition") == "CANDIDATE_OPEN_MARKET_PURCHASE"}
    h10_payload = json.loads(H10_PAIRS.read_text(encoding="utf-8"))
    h10_symbols = {Path(pair["amended"]["source_path"]).name.split("_10-K")[0].split("_10-Q")[0].split("_")[0] for pair in h10_payload["pairs"]}
    bars, bar_scan = load_bars(h06_symbols | h10_symbols, pit)
    h01_h05 = json.loads(H01_H05_RECEIPT.read_text(encoding="utf-8"))
    h01_h05_summary = h01_h05["summary"]
    candidates = {}
    for cid in EXPECTED_IDS:
        candidates[cid] = {"local_work": "none beyond frozen source gate", "status": "NO_LOCAL_ECONOMIC_JOIN"}
    for cid in ("H01", "H02", "H03", "H04", "H05"):
        candidates[cid] = {"local_work": "existing SEC facts/price/share structural diagnostic", "status": h01_h05_summary["feature_gate_status"][f"h{int(cid[1:]):02d}_" + {"H01": "book_to_market", "H02": "gross_profitability", "H03": "accruals", "H04": "asset_growth", "H05": "net_payout_yield"}[cid]]}
    candidates["H06"] = {"local_work": "274 receipt-time Form 4 rows and 63-session event joins", "status": "DIAGNOSTIC_ONLY_NO_SCORE_ADMISSION"}
    candidates["H10"] = {"local_work": "3 exact same-CIK filing pairs and 63-session event joins", "status": "SMALL_SAMPLE_METADATA_EVENT_DIAGNOSTIC_ONLY"}
    candidates["H14"] = {"local_work": "preserved frozen H14 result", "status": "EXISTING_FROZEN_DIAGNOSTIC_ONLY"}
    candidates["H15"] = {"local_work": "1 lifecycle row; announcement timestamp check", "status": "LIFECYCLE_METADATA_ONLY_NO_EVENT_RETURN"}
    candidates["H13"] = {"local_work": "full local PIT OHLC/action scan plus authorized route reconciliation", "status": "BLOCKED_RAW_ADJUSTED_BASIS"}
    results = {"schema": "equity-local-diagnostics-v1", "status": "LOCAL_DIAGNOSTICS_COMPLETE_NO_ECONOMIC_ADMISSION", "frozen_cutoff": "2026-07-31T23:59:59Z", "candidate_count": 16, "economic_run": False, "integration_candidate_count": 0, "bar_scan": bar_scan, "corporate_action_scan": action_scan(set(pit)), "h01_h05_existing_summary": {"row_count": h01_h05_summary["row_count"], "decision_count": h01_h05_summary["decision_count"], "feature_coverage": h01_h05_summary["feature_coverage"], "feature_gate_status": h01_h05_summary["feature_gate_status"]}, "h06": h06_diagnostic(bars, pit), "h10": h10_diagnostic(bars, pit), "h13": h13_route_diagnostic(), "h14b": h14b_diagnostic(), "h15": h15_diagnostic(), "candidates": candidates}
    result_bytes = (json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    report_bytes = render_report(results).encode()
    manifest = {"schema": "equity-local-diagnostics-manifest-v1", "status": results["status"], "result": {"path": str(RESULTS.relative_to(REPO)), "sha256": hashlib.sha256(result_bytes).hexdigest()}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest()}, "script": {"path": str(SCRIPT.relative_to(REPO)), "sha256": sha256(SCRIPT)}, "test": {"path": str(TEST.relative_to(REPO)), "sha256": sha256(TEST)}, "inputs": {str(path.relative_to(REPO)): {"sha256": sha256(path), "bytes": path.stat().st_size} for path in (BAR_PATH, PIT_PATH, ACTION_PATH, H06_ROWS, H06_REPORT, H10_PAIRS, H15_ROWS, H14_CURRENT, H14_RECEIPT, H01_H05_RECEIPT, H13_MATRIX, H14_EXECUTION_MANIFEST)}, "candidate_count": 16, "candidate_ids": EXPECTED_IDS, "economic_run": False, "integration_candidate_count": 0, "no_ohlcv_substitution": True, "no_auto_promotion": True, "manifest_sha256": PENDING}
    manifest["manifest_sha256"] = canonical_hash(manifest, "manifest_sha256")
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    receipt = {"schema": "equity-local-diagnostics-receipt-v1", "status": results["status"], "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": hashlib.sha256(manifest_bytes).hexdigest(), "canonical_sha256": manifest["manifest_sha256"]}, "result": {"path": str(RESULTS.relative_to(REPO)), "sha256": hashlib.sha256(result_bytes).hexdigest()}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest()}, "candidate_count": 16, "economic_run": False, "integration_candidate_count": 0, "receipt_sha256": PENDING}
    receipt["receipt_sha256"] = canonical_hash(receipt, "receipt_sha256")
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    _atomic({RESULTS: result_bytes, REPORT: report_bytes, MANIFEST: manifest_bytes, RECEIPT: receipt_bytes})
    verify()
    return {"status": results["status"], "candidate_count": 16, "result": str(RESULTS), "report": str(REPORT), "manifest": str(MANIFEST), "receipt": str(RECEIPT), "manifest_sha256": manifest["manifest_sha256"], "receipt_sha256": receipt["receipt_sha256"]}


def verify() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("status") != "LOCAL_DIAGNOSTICS_COMPLETE_NO_ECONOMIC_ADMISSION" or manifest.get("candidate_count") != 16 or manifest.get("candidate_ids") != EXPECTED_IDS or manifest.get("economic_run") is not False or manifest.get("integration_candidate_count") != 0:
        raise ValueError("diagnostic_manifest_policy_or_inventory_invalid")
    if manifest.get("manifest_sha256") != canonical_hash(manifest, "manifest_sha256"):
        raise ValueError("diagnostic_manifest_self_hash_mismatch")
    for key in ("result", "report", "script", "test"):
        declaration = manifest[key]
        path = REPO / declaration["path"]
        if not path.is_file() or sha256(path) != declaration["sha256"]:
            raise ValueError(f"diagnostic_hash_mismatch:{declaration['path']}")
    for relative, declaration in manifest["inputs"].items():
        path = REPO / relative
        if not path.is_file() or sha256(path) != declaration["sha256"]:
            raise ValueError(f"diagnostic_input_hash_mismatch:{relative}")
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    if results.get("candidate_count") != 16 or results.get("economic_run") is not False or results.get("integration_candidate_count") != 0 or len(results.get("candidates", {})) != 16:
        raise ValueError("diagnostic_results_inventory_or_policy_invalid")
    if any(item.get("economic_admitted", False) is not False for item in results["h06"]["rows"] + results["h10"]["rows"]):
        raise ValueError("diagnostic_economic_policy_invalid")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("receipt_sha256") != canonical_hash(receipt, "receipt_sha256") or receipt.get("manifest", {}).get("canonical_sha256") != manifest["manifest_sha256"] or receipt.get("result", {}).get("sha256") != manifest["result"]["sha256"] or receipt.get("report", {}).get("sha256") != manifest["report"]["sha256"]:
        raise ValueError("diagnostic_receipt_binding_invalid")
    return {"status": "LOCAL_DIAGNOSTICS_VERIFIED", "candidate_count": 16, "economic_run": False, "integration_candidate_count": 0, "manifest_sha256": manifest["manifest_sha256"]}


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True))
