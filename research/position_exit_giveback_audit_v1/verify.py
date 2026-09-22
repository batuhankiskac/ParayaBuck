"""Independent average-cost reconstruction of every persisted audit episode.

Deliberately does not import the primary diagnostic implementation. Entry fees
are capitalized into inventory basis; sales release that basis proportionally.
Spread/slippage are already in fill prices and must never be charged twice.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


def verify(raw: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    strategy = diagnostics["strategy"]
    counts: dict[str, int] = defaultdict(int)
    active: dict[str, dict[str, Any]] = {}
    reconstructed: list[dict[str, Any]] = []
    failures: list[str] = []
    last_targets: dict[str, dict[str, Any]] = {}
    realized_events: list[list[Any]] = []
    closed_events: list[list[Any]] = []
    nav_checks = 0
    closed_pnl = 0.0
    dividends = 0.0
    if raw.get("cash_yield", 0):
        failures.append("Nonzero cash yield needs timestamped attribution for independent NAV verification")

    def equal(actual: Any, expected: Any, label: str) -> None:
        if isinstance(expected, (float, int)) and not isinstance(expected, bool):
            ok = (
                isinstance(actual, (float, int))
                and math.isfinite(actual)
                and math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-6)
            )
        else:
            ok = actual == expected
        if not ok:
            failures.append(f"{label}: actual={actual!r}, independently_expected={expected!r}")

    def point(ep: dict[str, Any], timestamp: str, mark: float) -> None:
        unrealized = ep["qty"] * mark - ep["basis"]
        ep["path"].append(
            dict(
                timestamp=timestamp,
                pnl=ep["realized"] + unrealized,
                realized=ep["realized"],
                unrealized=unrealized,
                qty=ep["qty"],
                mark=mark,
            )
        )

    for event in raw["events"]:
        kind = event["type"]
        if kind == "target":
            last_targets[str(event.get("timestamp", event.get("decision_timestamp", "")))] = event
            continue
        if kind == "mark":
            positions = event["positions"]
            equal(set(positions), set(active), f"inventory symbols at {event['timestamp']}")
            for symbol, ep in active.items():
                if symbol not in positions:
                    continue
                p = positions[symbol]
                equal(p["qty"], ep["qty"], f"{symbol} marked quantity")
                equal(p["qty"] * p["avg_price"] + p["entry_fee"], ep["basis"], f"{symbol} marked basis")
                point(ep, event["timestamp"], p["mark"])
            if "starting_equity" in raw and "nav" in event:
                marked = sum(ep["path"][-1]["pnl"] for ep in active.values() if ep["path"])
                equal(
                    event["nav"], raw["starting_equity"] + closed_pnl + marked, f"account NAV at {event['timestamp']}"
                )
                nav_checks += 1
            continue
        if kind == "action":
            if event["symbol"] not in active:
                failures.append("corporate action without an open episode")
                continue
            ep = active[event["symbol"]]
            equal(event["quantity_before"], ep["qty"], "corporate action quantity")
            if event["action_type"] == "dividend":
                credit = ep["qty"] * event["value"]
                ep["realized"] += credit
                dividends += credit
            elif event["action_type"] == "split":
                ep["qty"] *= event["value"]
            else:
                failures.append(f"unknown action {event['action_type']}")
            continue
        terminal = kind == "terminal"
        if kind not in {"fill", "terminal"}:
            failures.append(f"unknown raw event {kind}")
            continue
        row = event["event"] if terminal else event
        symbol, timestamp = row["symbol"], row["timestamp"]
        side = "sell" if terminal else row["side"]
        qty = row["quantity"] if terminal else row["qty"]
        if side == "buy" and symbol not in active:
            counts[symbol] += 1
            active[symbol] = dict(
                episode_id=f"{strategy}:{symbol}:{counts[symbol]}",
                symbol=symbol,
                entry_timestamp=timestamp,
                exit_timestamp=None,
                censored=False,
                qty=0.0,
                basis=0.0,
                realized=0.0,
                trade_realized=0.0,
                capital=0.0,
                path=[],
                fills=[],
            )
            reconstructed.append(active[symbol])
        if symbol not in active:
            failures.append(f"sale without inventory: {symbol} {timestamp}")
            continue
        ep = active[symbol]
        ep["fills"].append(event)
        if "entities" in raw:
            entities = {
                entity["entity_id"]
                for entity in raw["entities"]
                if entity["symbol"] == symbol
                and entity["valid_from"][:10] <= timestamp[:10]
                and (not entity["valid_to"] or timestamp[:10] <= entity["valid_to"][:10])
            }
            if len(entities) != 1:
                failures.append(f"entity lifetime does not uniquely resolve {symbol} at {timestamp}: {entities}")
            else:
                entity_id = next(iter(entities))
                if "entity_id" in ep:
                    equal(entity_id, ep["entity_id"], f"{symbol} continuous entity lifetime")
                ep["entity_id"] = entity_id
        if side == "buy":
            outlay = qty * row["fill_price"] + row["fee"]
            ep["basis"] += outlay
            ep["capital"] += outlay
            ep["qty"] += qty
        else:
            if qty > ep["qty"] + 1e-8:
                failures.append(f"oversale: {symbol} {timestamp}")
                continue
            released = ep["basis"] * qty / ep["qty"]
            delta = qty * row["fill_price"] - row["fee"] - released
            ep["realized"] += delta
            ep["trade_realized"] += delta
            realized_events.append([timestamp, symbol, delta])
            ep["basis"] -= released
            ep["qty"] -= qty
            if ep["qty"] <= 1e-10:
                ep["qty"] = ep["basis"] = 0.0
                ep["exit_timestamp"] = timestamp
                ep["censored"] = terminal and row["policy"] == "FINAL_BAR"
                ep["exit_reason"] = (
                    ("horizon_censored" if ep["censored"] else "lifecycle_terminal")
                    if terminal
                    else "alpha_no_longer_selected"
                )
                if not terminal:
                    target = last_targets.get(row.get("decision_timestamp", ""))
                    if target is None:
                        ep["exit_reason"] = "other_unresolved"
                    else:
                        metadata = target.get("metadata", {})
                        weights = {w["symbol"] for w in target.get("weights", [])}
                        if symbol in metadata.get("selected_symbols", []) and symbol not in weights:
                            ep["exit_reason"] = "risk_overlay"
                        elif metadata.get("risk_off_detail"):
                            ep["exit_reason"] = "eligibility_gate_failure"
                        elif symbol not in weights:
                            ep["exit_reason"] = (
                                "hysteresis_selection_exit" if "crypto" in strategy else "alpha_no_longer_selected"
                            )
                        else:
                            ep["exit_reason"] = "rebalance_sizing"
                point(ep, timestamp, row["fill_price"])
                closed_pnl += ep["realized"]
                closed_events.append([timestamp, symbol, ep["trade_realized"]])
                del active[symbol]
    if active:
        failures.append("Raw replay has unclosed positions")
    if "ending_equity" in raw:
        equal(raw["ending_equity"], raw["starting_equity"] + closed_pnl + raw.get("cash_yield", 0), "ending NAV")
    if "dividend_total" in raw:
        equal(raw["dividend_total"], dividends, "aggregate dividends")
    for key, expected_rows in (("realized_pnl_events", realized_events), ("closed_position_events", closed_events)):
        if key not in raw:
            continue
        equal(len(raw[key]), len(expected_rows), f"{key} count")
        for index, (observed, expected_row) in enumerate(zip(raw[key], expected_rows)):
            equal(observed[:2], expected_row[:2], f"{key}[{index}] identity")
            equal(observed[2], expected_row[2], f"{key}[{index}] net trade PnL excluding dividends")
    equal(len(diagnostics["episodes"]), len(reconstructed), "episode count")
    persisted = {ep["episode_id"]: ep for ep in diagnostics["episodes"]}
    equal(len(persisted), len(diagnostics["episodes"]), "unique episode IDs")
    for ep in reconstructed:
        identity = ep["episode_id"]
        saved = persisted.get(identity)
        if saved is None:
            failures.append(f"missing episode {identity}")
            continue
        for field in ("symbol", "entry_timestamp", "exit_timestamp", "censored", "capital"):
            equal(saved.get(field), ep[field], f"{identity}.{field}")
        if "fills" in saved:
            equal(saved["fills"], ep["fills"], f"{identity}.raw fills and liquidation lineage")
        if "entity_id" in ep:
            equal(saved.get("entity_id"), ep["entity_id"], f"{identity}.entity_id")
        equal(len(saved["path"]), len(ep["path"]), f"{identity}.path length")
        for index, (actual, expected) in enumerate(zip(saved["path"], ep["path"])):
            for field in ("timestamp", "pnl", "realized", "unrealized", "qty", "mark"):
                equal(actual.get(field), expected[field], f"{identity}.path[{index}].{field}")
        if not ep["path"]:
            failures.append(f"empty path: {identity}")
            continue
        peak = max(ep["path"], key=lambda p: p["pnl"])
        trough = min(ep["path"], key=lambda p: p["pnl"])
        final = ep["path"][-1]["pnl"]
        values = dict(
            final_pnl=final,
            mfe=peak["pnl"],
            mae=trough["pnl"],
            mfe_timestamp=peak["timestamp"],
            mae_timestamp=trough["timestamp"],
            giveback=peak["pnl"] - final if peak["pnl"] > 0 else None,
            retention=final / peak["pnl"] if peak["pnl"] > 0 else None,
            final_return=final / ep["capital"],
            mfe_return=peak["pnl"] / ep["capital"],
            mae_return=trough["pnl"] / ep["capital"],
        )
        for field, expected in values.items():
            equal(saved.get(field), expected, f"{identity}.{field}")
        if ep.get("exit_reason"):
            equal(saved.get("exit_reason"), ep["exit_reason"], f"{identity}.exit_reason")
    return {
        "status": "PASS" if not failures else "FAIL",
        "method": "independent_fee_capitalized_average_cost",
        "episodes_checked": len(reconstructed),
        "all_episodes_checked": True,
        "nav_marks_checked": nav_checks,
        "realized_events_checked": len(realized_events) if "realized_pnl_events" in raw else 0,
        "closed_events_checked": len(closed_events) if "closed_position_events" in raw else 0,
        "entity_lifetimes_checked": "entities" in raw,
        "extreme_episodes_included": True,
        "failures": failures,
    }
