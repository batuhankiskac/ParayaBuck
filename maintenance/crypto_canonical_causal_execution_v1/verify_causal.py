#!/usr/bin/env python3
"""Independent CSV-and-ledger audit of the causal crypto replay payload."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/historical/crypto_expanded_1h.csv"
SOURCE_SHA = "73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e"
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def near(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7):
        raise AssertionError(f"{label}: {actual} != {expected}")


def independent_rank(history: dict[str, list[dict[str, Any]]], eligible: list[str]) -> list[str]:
    ranked: list[tuple[float, str]] = []
    for symbol in sorted(set(eligible).intersection(history)):
        window = history[symbol][-23:]
        if len(window) != 23 or stamp(window[-1]["timestamp"]) - stamp(window[0]["timestamp"]) != 22 * DAY:
            continue
        closes = [float(bar["close"]) for bar in window]
        if any(value <= 0 or not math.isfinite(value) for value in closes):
            continue
        changes = [right / left - 1 for left, right in zip(closes, closes[1:], strict=False)]
        ranked.append((statistics.stdev(changes), symbol))
    return [symbol for _sigma, symbol in sorted(ranked)]


def load_source() -> tuple[dict[tuple[str, datetime], dict[str, float]], dict[tuple[str, datetime], list[datetime]]]:
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA:
        raise AssertionError("Pinned source hash mismatch")
    bars: dict[tuple[str, datetime], dict[str, float]] = {}
    days: dict[tuple[str, datetime], list[datetime]] = defaultdict(list)
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp = stamp(row["timestamp"])
            key = (row["symbol"], timestamp)
            if key in bars:
                raise AssertionError(f"Duplicate source hour {key}")
            bars[key] = {field: float(row[field]) for field in ("open", "high", "low", "close", "volume")}
            day = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            days[(row["symbol"], day)].append(timestamp)
    for hours in days.values():
        hours.sort()
    return bars, days


def verify(payload: dict[str, Any]) -> dict[str, Any]:
    if payload["source_sha256"] != SOURCE_SHA:
        raise AssertionError("Payload source identity mismatch")
    source, source_days = load_source()
    daily_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bar in payload["daily_bars"]:
        label = stamp(bar["timestamp"])
        symbol = str(bar["symbol"])
        components = source_days.get((symbol, label), [])
        if components != [label + index * HOUR for index in range(24)]:
            raise AssertionError(f"Incomplete daily source {symbol} {label}")
        rows = [source[(symbol, time)] for time in components]
        for field, expected in (
            ("open", rows[0]["open"]),
            ("high", max(row["high"] for row in rows)),
            ("low", min(row["low"] for row in rows)),
            ("close", rows[-1]["close"]),
            ("volume", sum(row["volume"] for row in rows)),
        ):
            near(float(bar[field]), expected, f"daily {symbol} {label} {field}")
        daily_by_symbol[symbol].append(bar)

    terminal_at = stamp(payload["terminal_at"])
    terminal_created = stamp(payload["terminal_order_created_at"])
    if terminal_created >= terminal_at:
        raise AssertionError("Terminal instruction not precommitted")
    for symbol in payload["final_observation_set"]:
        latest = max(time for name, time in source if name == symbol)
        if latest < terminal_at or (symbol, terminal_at) not in source:
            raise AssertionError(f"No common source terminal hour for {symbol}")

    snapshot_labels = [stamp(row["label_time"]) for row in payload["snapshots"]]
    if snapshot_labels != sorted(set(snapshot_labels)):
        raise AssertionError("Snapshot labels not monotonic")
    decision_labels = [stamp(row["label_time"]) for row in payload["decisions"]]
    if decision_labels != sorted(set(decision_labels)):
        raise AssertionError("Decision labels not monotonic")
    blocks = [int(time.timestamp()) // (96 * 3600) for time in decision_labels]
    if blocks != sorted(set(blocks)):
        raise AssertionError("96h decision block repeated")
    for row in payload["decisions"]:
        label = stamp(row["label_time"])
        decision_at = stamp(row["decision_at"])
        if decision_at != label + DAY or decision_at >= terminal_at:
            raise AssertionError("Decision time not daily completion")
        for bars in row["history"].values():
            if bars and stamp(bars[-1]["timestamp"]) + DAY > decision_at:
                raise AssertionError("Future daily history reached decision")
        ranked = independent_rank(row["history"], row["eligible_symbols"])
        held = row["holdings"]
        metadata = row["alpha_target"]["metadata"]
        if not ranked:
            if metadata != {"risk_off_detail": "no_qualified_hysteresis"} or row["alpha_target"]["weights"]:
                raise AssertionError(f"Independent empty-rank risk-off mismatch at {label}")
            continue
        retained = [symbol for symbol in ranked[:4] if held.get(symbol, 0) > 1e-12][:2]
        selected = (retained + [symbol for symbol in ranked if symbol not in retained])[:2]
        if selected != metadata["selected_symbols"] or retained != metadata["retained"]:
            raise AssertionError(f"Independent rank/hysteresis mismatch at {label}")

    orders = {row["client_order_id"]: row for row in payload["orders"]}
    if len(orders) != len(payload["orders"]):
        raise AssertionError("Duplicate order id")
    remaining = {name: float(row["qty"]) for name, row in orders.items()}
    residual_created = {name: stamp(row["created_at"]) for name, row in orders.items()}
    fills = payload["fills"]
    events = payload["price_events"]
    if len(fills) != len(events):
        raise AssertionError("Fill/price event count mismatch")
    for fill, event in zip(fills, events, strict=True):
        order_id = fill["client_order_id"]
        if order_id not in orders or event["client_order_id"] != order_id:
            raise AssertionError("Unowned fill")
        symbol = str(fill["symbol"])
        execution_at = stamp(fill["timestamp"])
        creation = stamp(event["order_created_at"])
        original_creation = stamp(orders[order_id]["created_at"])
        if creation != residual_created[order_id] or execution_at <= creation or execution_at <= original_creation:
            raise AssertionError("Noncausal fill ordering")
        if execution_at != stamp(event["source_price_event_time"]):
            raise AssertionError("Fill time differs from source opening event")
        day = execution_at.replace(hour=0, minute=0, second=0, microsecond=0)
        candidates = [time for time in source_days.get((symbol, day), []) if time > day and time > creation]
        if not candidates or execution_at != candidates[0]:
            raise AssertionError("Fill was not at first eligible source hour")
        raw = source[(symbol, execution_at)]
        near(float(fill["reference_price"]), raw["open"], "reference open")
        side = 1 if fill["side"] == "buy" else -1
        qty = float(fill["qty"])
        expected_price = raw["open"] * (1 + side * 3 / 10_000)
        near(float(fill["fill_price"]), expected_price, "fill price")
        near(float(fill["fee"]), qty * expected_price * 25 / 10_000, "taker fee")
        near(float(fill["slippage_cost"]), qty * raw["open"] * 2 / 10_000, "slippage")
        near(float(fill["spread_cost"]), qty * raw["open"] / 10_000, "spread")
        known = [
            float(bar["volume"])
            for bar in daily_by_symbol[symbol]
            if stamp(bar["timestamp"]) + DAY <= creation and float(bar["volume"]) > 0
        ][-20:]
        if not known:
            raise AssertionError("Fill with no known positive daily volume")
        cap = sum(known) / len(known) * 0.1
        if qty > min(remaining[order_id], cap) + 1e-8:
            raise AssertionError("Participation or residual quantity exceeded")
        remaining[order_id] -= qty
        residual_created[order_id] = execution_at

    cash = 100_000.0
    positions: dict[str, dict[str, float]] = {}
    fill_index = 0
    terminal_index = 0
    terminal = payload["terminal_events"]
    for snapshot in payload["snapshots"]:
        observed_at = stamp(snapshot["available_at"])
        while fill_index < len(fills) and stamp(fills[fill_index]["timestamp"]) < observed_at:
            fill = fills[fill_index]
            symbol = str(fill["symbol"])
            qty, price, fee = (float(fill[key]) for key in ("qty", "fill_price", "fee"))
            if fill["side"] == "buy":
                cash -= qty * price + fee
                state = positions.setdefault(symbol, {"qty": 0.0, "avg_price": 0.0, "entry_fee": 0.0})
                total = state["qty"] + qty
                state["avg_price"] = (state["qty"] * state["avg_price"] + qty * price) / total
                state["qty"] = total
                state["entry_fee"] += fee
            else:
                state = positions[symbol]
                cash += qty * price - fee
                state["entry_fee"] *= 1 - qty / state["qty"]
                state["qty"] -= qty
                if state["qty"] <= 1e-12:
                    del positions[symbol]
            fill_index += 1
        while terminal_index < len(terminal) and stamp(terminal[terminal_index]["timestamp"]) <= observed_at:
            event = terminal[terminal_index]
            symbol = str(event["symbol"])
            if stamp(event["timestamp"]) != terminal_at or (symbol, terminal_at) not in source:
                raise AssertionError("Terminal source event invalid")
            raw_open = source[(symbol, terminal_at)]["open"]
            near(float(event["last_close"]), raw_open, "terminal source open")
            expected_price = raw_open * (1 - 3 / 10_000)
            near(float(event["fill_price"]), expected_price, "terminal price")
            qty = positions[symbol]["qty"]
            near(float(event["quantity"]), qty, "terminal quantity")
            fee = qty * expected_price * 25 / 10_000
            near(float(event["fee"]), fee, "terminal fee")
            near(float(event["cash_before"]), cash, "terminal cash before")
            cash += qty * expected_price - fee
            near(float(event["cash_after"]), cash, "terminal cash after")
            del positions[symbol]
            terminal_index += 1
        near(float(snapshot["cash"]), cash, "snapshot cash")
        if set(snapshot["positions"]) != set(positions):
            raise AssertionError("Snapshot positions differ from independent ledger")
        nav = cash
        for symbol, state in positions.items():
            recorded = snapshot["positions"][symbol]
            for field in ("qty", "avg_price", "entry_fee"):
                near(float(recorded[field]), state[field], f"snapshot {symbol} {field}")
            nav += state["qty"] * float(recorded["mark"])
        near(float(snapshot["nav"]), nav, "snapshot NAV")
    if fill_index != len(fills) or terminal_index != len(terminal) or positions:
        raise AssertionError("Unconsumed fills/terminal events or final inventory")
    near(cash, float(payload["final_state"]["cash"]), "final cash")
    near(cash, float(payload["metrics"]["ending_equity"]), "ending equity")
    if len(payload["equity_curve"]) != len(payload["snapshots"]):
        raise AssertionError("Curve/snapshot length mismatch")
    for curve_value, snapshot in zip(payload["equity_curve"], payload["snapshots"], strict=True):
        near(float(curve_value), float(snapshot["nav"]), "curve NAV")
    return {
        "status": "PASS",
        "source_hours": len(source),
        "verified_daily_bars": len(payload["daily_bars"]),
        "verified_decisions": len(payload["decisions"]),
        "verified_orders": len(orders),
        "verified_fills": len(fills),
        "verified_snapshots": len(payload["snapshots"]),
        "verified_terminal_events": len(terminal),
        "strict_order_created_before_source_price_event": True,
        "independent_cash_and_nav_reconciled": True,
        "ending_cash": cash,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with gzip.open(args.input, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    result = verify(payload)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
