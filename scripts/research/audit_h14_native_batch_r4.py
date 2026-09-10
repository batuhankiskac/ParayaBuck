#!/usr/bin/env python3
"""Independent, read-only audit of the frozen H14 native r4 receipt.

This parser never imports or executes the H14 runner.  It validates the
persisted receipt, frozen manifest/source hashes, event/fill timing, terminal
NAV/cash accounting, dividend entitlement, cost-stress components, and
descriptive annual/event diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any


REPO = Path(__file__).resolve().parents[2]
DEFAULT_RECEIPT = REPO / "data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_receipt.json"
DEFAULT_MANIFEST = REPO / "data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r4.json"
DEFAULT_OUTPUT = REPO / "data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_independent_audit.json"
EXPECTED_MODES = ("candidate", "passive", "calendar_control")
EXPECTED_CELLS = ("full_2022", "recent_2024")
EXPECTED_COSTS = (1.0, 2.0, 3.0)
EXPECTED_RECEIPT_SHA = "dd2de0467c2c7ddea0f506d4856fdcee66d7274e9547ee44a5dca70f2a74510f"
EXPECTED_LAUNCH_LOG_SHA = "acd2d94619d7de53b591c29c87dd66f923234cd2fcd752dcef9c64ba95a736fe"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timezone-naive timestamp: {value}")
    return parsed


def ts_date(value: str) -> date:
    return parse_ts(value).date()


def finite_tree(value: Any, path: str = "root") -> list[str]:
    problems: list[str] = []
    if isinstance(value, float) and not math.isfinite(value):
        problems.append(path)
    elif isinstance(value, dict):
        for key, child in value.items():
            problems.extend(finite_tree(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            problems.extend(finite_tree(child, f"{path}[{index}]"))
    return problems


def close(a: float, b: float, tolerance: float = 1e-7) -> bool:
    return math.isclose(float(a), float(b), rel_tol=tolerance, abs_tol=tolerance)


def annual_returns(curve: list[dict[str, Any]]) -> dict[str, float]:
    # The native engine may append a terminal liquidation row at the same
    # endpoint timestamp.  Keep the last row for that date.
    by_day: dict[date, float] = {}
    for point in curve:
        by_day[ts_date(point["timestamp"])] = float(point["nav"])
    by_year: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for day, nav in sorted(by_day.items()):
        by_year[day.year].append((day, nav))
    return {
        str(year): values[-1][1] / values[0][1] - 1.0
        for year, values in sorted(by_year.items())
    }


def event_stats(cell: dict[str, Any]) -> dict[str, Any] | None:
    fills = cell["fills"]
    if cell["mode"] == "passive":
        return None
    returns: list[float] = []
    for index in range(0, len(fills), 2):
        buy, sell = fills[index : index + 2]
        buy_cash = float(buy["qty"]) * float(buy["fill_price"]) + float(buy["fee"])
        sell_cash = float(sell["qty"]) * float(sell["fill_price"]) - float(sell["fee"])
        returns.append(sell_cash / buy_cash - 1.0)
    return {
        "count": len(returns),
        "mean": mean(returns),
        "median": median(returns),
        "win_fraction": sum(value > 0 for value in returns) / len(returns),
        "min": min(returns),
        "max": max(returns),
    }


def audit(receipt_path: Path, manifest_path: Path) -> dict[str, Any]:
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    manifest = json.loads(manifest_path.read_text())
    checks: dict[str, bool] = {}
    errors: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks[name] = bool(condition)
        if not condition:
            errors.append(f"{name}: {detail or 'failed'}")

    check("receipt_sha_matches_authoritative", sha256(receipt_path) == EXPECTED_RECEIPT_SHA)
    launch_log = receipt_path.parent / "h14_native_batch_launch.log"
    check("launch_log_present", launch_log.exists())
    if launch_log.exists():
        check("launch_log_sha_matches_authoritative", sha256(launch_log) == EXPECTED_LAUNCH_LOG_SHA)
    check("receipt_complete", receipt.get("status") == "COMPLETE")
    check("schema_expected", receipt.get("schema_version") == "h14-native-research-v1")
    check("research_only", receipt.get("economic_claim") is False)
    check("independent_pass_required", receipt.get("canonical_run_requires_independent_pass") is True)
    check("manifest_frozen", manifest.get("status") == "FROZEN_FOR_INDEPENDENT_PASS")
    check("manifest_sha", sha256(manifest_path) == "014c232e59bd3d6c19ffb00c39e5ad45fdb27d3c1e16c1804f4bd29c6c0bfd0a")
    check("receipt_binds_manifest", receipt.get("execution_manifest", {}).get("sha256") == sha256(manifest_path))
    check("receipt_binds_runner", receipt.get("source_pins", {}).get("runner_sha256") == "1b5a33a90e34895f961ff81b9a62847f0e7a96a20d7d7273b0efd0690e287599")

    source_hashes: dict[str, str] = {}
    for item in manifest.get("inputs", []) + manifest.get("frozen_sources", []):
        path = REPO / item["path"]
        exists = path.exists()
        check(f"source_present:{item['path']}", exists)
        if exists:
            actual = sha256(path)
            source_hashes[item["path"]] = actual
            check(f"source_hash:{item['path']}", actual == item["sha256"], f"{actual} != {item['sha256']}")

    matrix = receipt.get("matrix", {})
    expected_keys = {(cell, mode, cost) for cell in EXPECTED_CELLS for mode in EXPECTED_MODES for cost in EXPECTED_COSTS}
    actual_keys = {(c.get("cell"), c.get("mode"), float(c.get("cost_multiplier"))) for c in receipt.get("cells", [])}
    check("exact_18_cell_matrix", len(receipt.get("cells", [])) == 18 and actual_keys == expected_keys)
    check("matrix_declaration", matrix.get("cell_count") == 18 and tuple(matrix.get("cells", [])) == EXPECTED_CELLS and tuple(matrix.get("modes", [])) == EXPECTED_MODES and tuple(matrix.get("cost_multipliers", [])) == EXPECTED_COSTS)

    cells_out: list[dict[str, Any]] = []
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    expected_starts = {"full_2022": "2022-01-03", "recent_2024": "2024-01-02"}
    expected_event_counts = {"full_2022": 54, "recent_2024": 30}
    for cell in receipt.get("cells", []):
        key = (cell.get("cell"), cell.get("mode"))
        by_group[key].append(cell)
        events = cell.get("event_trace", [])
        fills = cell.get("fills", [])
        curve = cell.get("equity_curve", [])
        summary = cell.get("summary", {})
        final = cell.get("final_state", {})
        cell_errors: list[str] = []

        def ccheck(name: str, condition: bool, detail: str = "") -> None:
            if not condition:
                cell_errors.append(f"{name}: {detail or 'failed'}")

        ccheck("status_pass", cell.get("status") == "PASS")
        ccheck("starting_equity", close(summary.get("starting_equity", 0), 100000.0))
        ccheck("first_day_sentinel", bool(curve) and close(curve[0]["nav"], 100000.0))
        ccheck("events_match_preflight", events == receipt.get("preflight", {}).get(cell.get("cell"), {}).get("events", []))
        ccheck("event_count", len(events) == expected_event_counts.get(cell.get("cell"), -1))
        for event in events:
            ordered = [event.get(key) for key in ("p0", "l0", "f1", "f2", "f3", "f4")]
            try:
                dates = [date.fromisoformat(value) for value in ordered]
                ccheck("event_order", dates == sorted(dates) and len(set(dates)) == 6)
                ccheck("event_month", event["month"] == event["l0"][:7])
                ccheck("event_end", dates[-1] <= date(2026, 7, 30))
            except (KeyError, TypeError, ValueError):
                ccheck("event_dates_parse", False)

        if cell.get("mode") in {"candidate", "calendar_control"}:
            ccheck("paired_fill_count", len(fills) == 2 * len(events))
            ccheck("marked_position_count", len(cell.get("marked_positions", [])) == len(fills))
            ccheck("rebalance_count", len(cell.get("rebalances", [])) == len(fills))
            session_dates = sorted({ts_date(point["timestamp"]) for point in curve})
            for index, event in enumerate(events):
                pair = fills[2 * index : 2 * index + 2]
                if len(pair) != 2:
                    continue
                buy, sell = pair
                if cell.get("mode") == "candidate":
                    buy_decision, buy_fill = date.fromisoformat(event["p0"]), date.fromisoformat(event["l0"])
                    sell_decision, sell_fill = date.fromisoformat(event["f3"]), date.fromisoformat(event["f4"])
                else:
                    # Fixed-calendar control shifts the four-session holding
                    # window after F4: M0=F4 and M1..M3 are the next three
                    # sessions, with exit at M4 open.
                    f4 = date.fromisoformat(event["f4"])
                    following = [day for day in session_dates if day > f4]
                    if len(following) < 4:
                        ccheck("control_following_sessions", False)
                        continue
                    buy_decision, buy_fill = date.fromisoformat(event["f3"]), f4
                    sell_decision, sell_fill = following[2], following[3]
                ccheck("buy_timing", buy.get("side") == "buy" and ts_date(buy["decision_timestamp"]) == buy_decision and ts_date(buy["timestamp"]) == buy_fill)
                ccheck("sell_timing", sell.get("side") == "sell" and ts_date(sell["decision_timestamp"]) == sell_decision and ts_date(sell["timestamp"]) == sell_fill)
                ccheck("pair_symbol", buy.get("symbol") == "SPY" and sell.get("symbol") == "SPY")
        elif cell.get("mode") == "passive":
            ccheck("passive_single_initial_fill", len(fills) == 1 and fills[0].get("side") == "buy")
            if fills:
                ccheck("passive_fill_precedes_window", ts_date(fills[0]["decision_timestamp"]) < date.fromisoformat(expected_starts[cell["cell"]]))
                ccheck("passive_first_supported_open", ts_date(fills[0]["timestamp"]) == date.fromisoformat(expected_starts[cell["cell"]]))
            ccheck("passive_event_count", len(events) == expected_event_counts[cell["cell"]])

        finite_problems = finite_tree(cell)
        ccheck("all_numeric_values_finite", not finite_problems, ", ".join(finite_problems[:3]))
        ccheck("curve_nonempty", bool(curve))
        if curve:
            ccheck("curve_nav_positive", all(float(point["nav"]) > 0 for point in curve))
            # Duplicate endpoint is the expected terminal liquidation form for
            # passive cells; all other curves must have unique timestamps.
            timestamps = [parse_ts(point["timestamp"]) for point in curve]
            duplicate_count = len(timestamps) - len(set(timestamps))
            ccheck("curve_timestamp_shape", duplicate_count == (1 if cell.get("mode") == "passive" else 0))
            sessions = receipt["preflight"][cell["cell"]]["spy_sessions"]
            expected_curve_length = sessions + (2 if cell.get("mode") == "passive" else 0)
            ccheck("native_daily_curve_length", len(curve) == expected_curve_length)
            ccheck("ending_nav_matches_summary", close(curve[-1]["nav"], summary.get("ending_equity", math.nan)))

        ccheck("final_cash_matches_ending_nav", close(final.get("cash", math.nan), summary.get("ending_equity", math.nan)))
        ccheck("no_final_holdings", final.get("holdings") == {})
        ccheck("no_pending_orders", final.get("pending_orders") == [])
        ccheck("terminal_last_bar", ts_date(final.get("last_processed_bar", "1970-01-01T00:00:00+00:00")) == date(2026, 7, 30))

        actions = cell.get("corporate_actions", [])
        ccheck("40_dividend_actions", len(actions) == 40 and all(a.get("action_type") == "dividend" and a.get("symbol") == "SPY" for a in actions))
        action_dates = {date.fromisoformat(a["timestamp"][:10]) for a in actions}
        if cell.get("mode") == "candidate":
            holding_dates = {date.fromisoformat(event[key]) for event in events for key in ("l0", "f1", "f2", "f3")}
        elif cell.get("mode") == "calendar_control":
            session_dates = sorted({ts_date(point["timestamp"]) for point in curve})
            holding_dates = set()
            for event in events:
                f4 = date.fromisoformat(event["f4"])
                following = [day for day in session_dates if day > f4]
                holding_dates.update([f4, *following[:3]])
        else:
            holding_dates = set()
        if cell.get("mode") in {"candidate", "calendar_control"}:
            ccheck("candidate_dividend_entitlement", summary.get("dividend_total") == 0.0 and not action_dates.intersection(holding_dates))
        else:
            fill_date = ts_date(fills[0]["timestamp"])
            end_date = ts_date(summary["end_date"])
            quantity = float(fills[0]["qty"])
            expected_dividend = quantity * sum(float(a["value"]) for a in actions if fill_date <= ts_date(a["timestamp"]) <= end_date)
            ccheck("passive_dividend_reconciliation", close(summary.get("dividend_total", math.nan), expected_dividend))

        if cell.get("mode") == "passive" and len(curve) >= 2 and fills:
            entry_cost = sum(float(fills[0].get(name, 0.0)) for name in ("fee", "spread_cost", "slippage_cost"))
            total_cost = sum(float(summary.get(name, 0.0)) for name in ("fee_total", "spread_cost_total", "slippage_cost_total"))
            terminal_gap = float(curve[-2]["nav"]) - float(curve[-1]["nav"])
            ccheck("passive_terminal_liquidation_reconciliation", close(terminal_gap, total_cost - entry_cost, 1e-6))
        if cell.get("mode") != "passive":
            ccheck("no_terminal_liquidation_gap", close(float(curve[-1]["nav"]), float(final.get("cash", math.nan))))

        event = event_stats(cell)
        cell_result = {
            "mode": cell["mode"],
            "cell": cell["cell"],
            "cost_multiplier": float(cell["cost_multiplier"]),
            "status": "PASS" if not cell_errors else "FAIL",
            "errors": cell_errors,
            "event_count": len(events),
            "trade_count": len(fills),
            "event_exposure_fraction": (4 * len(events) / receipt["preflight"][cell["cell"]]["spy_sessions"] if cell["mode"] != "passive" else 1.0),
            "summary": summary,
            "annual_returns": annual_returns(curve),
            "event_return_stats": event,
            "terminal": {
                "curve_last_nav": curve[-1]["nav"] if curve else None,
                "final_cash": final.get("cash"),
                "last_observed_equity": final.get("last_observed_equity"),
                "holdings": final.get("holdings"),
                "terminal_liquidation_gap": (float(curve[-2]["nav"]) - float(curve[-1]["nav"])) if cell["mode"] == "passive" and len(curve) >= 2 else 0.0,
                "terminal_liquidation_recorded_as_fill": cell["mode"] != "passive",
                "terminal_liquidation_inferred_from_engine": cell["mode"] == "passive",
            },
        }
        cells_out.append(cell_result)

    for group, group_cells in by_group.items():
        check(f"cost_triplet:{group[0]}:{group[1]}", {float(c["cost_multiplier"]) for c in group_cells} == set(EXPECTED_COSTS))
        ordered = sorted(group_cells, key=lambda c: float(c["cost_multiplier"]))
        spreads = [float(c["summary"]["spread_cost_total"]) for c in ordered]
        slips = [float(c["summary"]["slippage_cost_total"]) for c in ordered]
        check(f"cost_components_monotonic:{group[0]}:{group[1]}", spreads[0] <= spreads[1] <= spreads[2] and slips[0] <= slips[1] <= slips[2])

    check("all_cells_individually_pass", all(c["status"] == "PASS" for c in cells_out))
    result = {
        "schema_version": "h14-independent-audit-v1",
        "status": "PASS" if not errors else "FAIL",
        "economic_run": False,
        "reconciliation_status": "PASS" if not errors else "FAIL",
        "transaction_ledger_status": "PASS_CANDIDATE_CONTROL; PASSIVE_TERMINAL_LIQUIDATION_INFERRED_NOT_RECORDED_AS_FILL",
        "ledger_scope": {
            "recorded_entry_exit_fill_cells": 12,
            "passive_terminal_close_inferred_cells": 6,
            "nav_cash_reconciled_cells": 18,
        },
        "receipt": {"path": str(receipt_path.relative_to(REPO)), "sha256": sha256(receipt_path)},
        "launch_log": {"path": str(launch_log.relative_to(REPO)), "sha256": sha256(launch_log) if launch_log.exists() else None},
        "manifest": {"path": str(manifest_path.relative_to(REPO)), "sha256": sha256(manifest_path)},
        "checks": checks,
        "errors": errors,
        "cells": cells_out,
        "interpretation": {
            "scope": "Descriptive isolated SPY diagnostics only; no V3 daily NAV exists for exact complementarity alignment.",
            "passive_terminal_row": "Passive cells include a same-endpoint terminal liquidation row; its cost is reconciled from summary components but no terminal PortfolioFill or decision timestamp is recorded.",
            "passive_terminal_policy": "This is a deterministic end-of-sample valuation convention under the pinned runner's liquidate_at_end=True call, not a claim of an executable next-open trade after the endpoint.",
            "cost_stress": "Spread and slippage scale monotonically; native fee totals are reported as emitted and are not assumed to scale linearly.",
            "complementarity": "NOT_COMPARABLE to current V3; fixed calendar and passive controls establish timing/exposure diagnostics, not portfolio improvement.",
        },
        "source_hashes": source_hashes,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = audit(args.receipt, args.manifest)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "errors": result["errors"], "output": str(args.output)}, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
