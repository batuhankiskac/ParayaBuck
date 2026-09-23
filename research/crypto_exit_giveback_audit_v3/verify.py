"""Independent event-ledger check; imports neither primary episodes nor strategy code."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any


def verify(raw: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    def check(a: Any, b: Any, label: str) -> None:
        equal = (
            math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-7)
            if isinstance(a, float) and isinstance(b, (float, int))
            else a == b
        )
        if not equal:
            errors.append(f"{label}: {a!r} != {b!r}")

    events = [(f["timestamp"], 0, i, f) for i, f in enumerate(raw["fills"])]
    events += [
        (t["timestamp"], 1, i, {"type": "terminal", **t, "side": "sell", "qty": t["quantity"]})
        for i, t in enumerate(raw["terminal_events"])
    ]
    events.sort(key=lambda row: row[:3])
    active: dict[str, dict[str, Any]] = {}
    ordinal: defaultdict[str, int] = defaultdict(int)
    closed: list[dict[str, Any]] = []
    closed_pnl = 0.0
    index = marks = 0

    def mark(symbol: str, price: float, stamp: str) -> None:
        nonlocal marks
        e = active[symbol]
        e["path"].append((stamp, e["proceeds"] - e["outlay"] + e["qty"] * price, e["qty"], price))
        marks += 1

    def apply(f: dict[str, Any]) -> None:
        nonlocal closed_pnl
        symbol = f["symbol"]
        qty = f["qty"]
        fee = f["fee"]
        value = qty * f["fill_price"]
        if f["side"] == "buy":
            if symbol not in active:
                ordinal[symbol] += 1
                active[symbol] = {
                    "id": f"{raw['final_state']['strategy_name']}:{symbol}:{ordinal[symbol]}",
                    "symbol": symbol,
                    "entry": f["timestamp"],
                    "qty": 0.0,
                    "outlay": 0.0,
                    "proceeds": 0.0,
                    "path": [],
                    "fills": [],
                }
            e = active[symbol]
            e["qty"] += qty
            e["outlay"] += value + fee
        else:
            if symbol not in active:
                errors.append(f"orphan sale {symbol}")
                return
            e = active[symbol]
            if qty > e["qty"] + 1e-9:
                errors.append(f"oversale {symbol}")
            e["qty"] -= qty
            e["proceeds"] += value - fee
        e["fills"].append(f)
        if f["side"] == "sell" and abs(e["qty"]) < 1e-9:
            e["exit"] = f["timestamp"]
            e["terminal"] = f.get("type") == "terminal"
            e["pnl"] = e["proceeds"] - e["outlay"]
            closed_pnl += e["pnl"]
            closed.append(e)
            del active[symbol]

    for snap in raw["snapshots"]:
        stamp = snap["available_at"]
        while index < len(events) and events[index][0] <= stamp:
            _, kind, _, f = events[index]
            apply(f)
            index += 1
        check(set(active), set(snap["positions"]), f"inventory {stamp}")
        pnl = closed_pnl
        for symbol, position in snap["positions"].items():
            check(active[symbol]["qty"], position["qty"], f"quantity {stamp}/{symbol}")
            mark(symbol, position["mark"], stamp)
            e = active[symbol]
            pnl += e["proceeds"] - e["outlay"] + e["qty"] * position["mark"]
        check(pnl, snap["nav"] - raw["metrics"]["starting_equity"], f"NAV {stamp}")
    check(index, len(events), "event count")
    check(len(active), 0, "remaining inventory")
    observed = {e["episode_id"]: e for e in report["episodes"]}
    check(len(observed), len(closed), "episode count")
    for e in closed:
        key = e["id"]
        o = observed.get(key)
        if o is None:
            errors.append(f"missing {key}")
            continue
        for a, b, label in [
            (o["entry_timestamp"], e["entry"], "entry"),
            (o["exit_timestamp"], e["exit"], "exit"),
            (o["censored"], e["terminal"], "censored"),
            (o["capital"], e["outlay"], "capital"),
            (o["final_pnl"], e["pnl"], "final P&L"),
            (len(o["fills"]), len(e["fills"]), "fill count"),
            (len(o["path"]), len(e["path"]), "mark count"),
        ]:
            check(a, b, f"{key}/{label}")
        for i, (a, b) in enumerate(zip(o["path"], e["path"])):
            for field, v in [("timestamp", b[0]), ("pnl", b[1]), ("qty", b[2]), ("mark", b[3])]:
                check(a[field], v, f"{key}/mark{i}/{field}")
        for i, (a, b) in enumerate(zip(o["fills"], e["fills"])):
            for field in ("timestamp", "symbol", "side", "qty", "fill_price", "fee"):
                check(a[field], b[field], f"{key}/fill{i}/{field}")
        peak = max(e["path"], key=lambda row: row[1])
        mfe = peak[1]
        for field, value in [
            ("mfe", mfe),
            ("mfe_timestamp", peak[0]),
            ("mfe_return", mfe / e["outlay"]),
            ("final_return", e["pnl"] / e["outlay"]),
            ("retention", e["pnl"] / mfe if mfe > 0 else None),
        ]:
            check(o[field], value, f"{key}/{field}")
        check(
            o["levels"]["0.3"]["ever_reached"],
            any(p[1] / e["outlay"] >= 0.30 for p in e["path"]),
            f"{key}/30pct cohort",
        )
        duration = (datetime.fromisoformat(e["exit"]) - datetime.fromisoformat(e["entry"])).total_seconds() / 86400
        check(o["holding_days"], duration, f"{key}/holding")
    check(
        sum(e["pnl"] for e in closed), raw["metrics"]["ending_equity"] - raw["metrics"]["starting_equity"], "final P&L"
    )
    return {
        "status": "PASS" if not errors else "FAIL",
        "method": "independent event ledger with separate grouping, mark and return calculations",
        "production_imports": False,
        "primary_module_imports": False,
        "episodes_checked": len(closed),
        "fills_checked": len(raw["fills"]),
        "terminal_events_checked": len(raw["terminal_events"]),
        "marks_checked": marks,
        "snapshots_checked": len(raw["snapshots"]),
        "natural_30pct_checked": sum(
            not e["terminal"] and any(p[1] / e["outlay"] >= 0.30 for p in e["path"]) for e in closed
        ),
        "failures": errors,
    }
