"""Observed continuous-inventory episode economics; no alternative exits."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any

LEVELS = (0.05, 0.10, 0.20, 0.30, 0.40, 0.50)


def days(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q
    i = int(pos)
    return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * (pos - i)


def distribution(values: list[float]) -> dict[str, float | None]:
    return {
        "mean": sum(values) / len(values) if values else None,
        **{
            key: quantile(values, q)
            for key, q in [("p10", 0.1), ("p25", 0.25), ("median", 0.5), ("p75", 0.75), ("p90", 0.9)]
        },
    }


def reconstruct(raw: dict[str, Any]) -> dict[str, Any]:
    strategy = raw["final_state"]["strategy_name"]
    active: dict[str, dict[str, Any]] = {}
    episodes: list[dict[str, Any]] = []
    sequence: Counter[str] = Counter()
    fill_index = 0
    closed_pnl = 0.0
    max_nav_error = 0.0
    portfolio = []

    def apply(f: dict[str, Any]) -> None:
        nonlocal closed_pnl
        symbol = f["symbol"]
        qty = f["qty"]
        cost = qty * f["fill_price"]
        fee = f["fee"]
        if f["side"] == "buy":
            if symbol not in active:
                sequence[symbol] += 1
                active[symbol] = {
                    "episode_id": f"{strategy}:{symbol}:{sequence[symbol]}",
                    "strategy": strategy,
                    "symbol": symbol,
                    "entry_timestamp": f["timestamp"],
                    "capital": 0.0,
                    "cashflow": 0.0,
                    "qty": 0.0,
                    "basis": 0.0,
                    "realized": 0.0,
                    "fills": [],
                    "path": [],
                    "fees": 0.0,
                    "slippage_cost": 0.0,
                    "spread_cost": 0.0,
                }
            e = active[symbol]
            e["capital"] += cost + fee
            e["cashflow"] -= cost + fee
            e["basis"] += cost + fee
            e["qty"] += qty
        else:
            e = active[symbol]
            if qty > e["qty"] + 1e-10:
                raise ValueError("Oversold episode inventory")
            basis = e["basis"] * qty / e["qty"]
            e["basis"] -= basis
            e["qty"] -= qty
            e["cashflow"] += cost - fee
            e["realized"] += cost - fee - basis
        e["fills"].append(f)
        e["fees"] += fee
        e["slippage_cost"] += f.get("slippage_cost", 0)
        e["spread_cost"] += f.get("spread_cost", 0)
        if f["side"] == "sell" and e["qty"] <= 0:
            e["exit_timestamp"] = f["timestamp"]
            e["exit_event"] = f
            e["final_pnl"] = e["cashflow"]
            e["censored"] = f.get("type") == "terminal"
            if e["censored"]:
                e["terminal_price_timestamp"] = f["timestamp"]
            closed_pnl += e["final_pnl"]
            episodes.append(e)
            del active[symbol]

    for snap in raw["snapshots"]:
        stamp = snap["available_at"]
        while fill_index < len(raw["fills"]) and raw["fills"][fill_index]["timestamp"] <= stamp:
            apply({"type": "fill", **raw["fills"][fill_index]})
            fill_index += 1
        if stamp == raw["terminal_at"]:
            for t in raw["terminal_events"]:
                apply({"type": "terminal", **t, "qty": t["quantity"], "side": "sell"})
        if set(active) != set(snap["positions"]):
            raise ValueError("Marked inventory symbols differ")
        combined = closed_pnl
        for symbol, e in active.items():
            p = snap["positions"][symbol]
            if not math.isclose(e["qty"], p["qty"], rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError("Marked inventory quantity differs")
            pnl = e["cashflow"] + e["qty"] * p["mark"]
            e["path"].append(
                {
                    "timestamp": stamp,
                    "pnl": pnl,
                    "realized": e["realized"],
                    "unrealized": pnl - e["realized"],
                    "qty": e["qty"],
                    "mark": p["mark"],
                }
            )
            combined += pnl
        residual = combined - (snap["nav"] - raw["metrics"]["starting_equity"])
        max_nav_error = max(max_nav_error, abs(residual))
        portfolio.append({"timestamp": stamp, "nav": snap["nav"], "economic_pnl": combined, "residual": residual})
    if fill_index != len(raw["fills"]):
        raise ValueError("Unprocessed fills beyond mark horizon")
    if active:
        raise ValueError("Unsettled final positions")
    for e in episodes:
        path = e["path"]
        if not path:
            raise ValueError("No active completed-bar marks: extrema unavailable")
        best = max(path, key=lambda p: p["pnl"])
        worst = min(path, key=lambda p: p["pnl"])
        mfe = best["pnl"]
        mae = worst["pnl"]
        end = e["exit_timestamp"]
        cap = e["capital"]
        e.update(
            mfe=mfe,
            mae=mae,
            mfe_timestamp=best["timestamp"],
            mae_timestamp=worst["timestamp"],
            mfe_return=mfe / cap,
            mae_return=mae / cap,
            final_return=e["final_pnl"] / cap,
            giveback=mfe - e["final_pnl"] if mfe > 0 else None,
            retention=e["final_pnl"] / mfe if mfe > 0 else None,
            holding_days=days(e["entry_timestamp"], end),
            time_to_mfe_days=days(e["entry_timestamp"], best["timestamp"]),
            mfe_to_exit_days=days(best["timestamp"], end),
            fill_count=len(e["fills"]),
            rebalance_count=len({f.get("decision_timestamp") for f in e["fills"] if f["type"] == "fill"}),
        )
        e["levels"] = {}
        for level in LEVELS:
            hit = next((p for p in path if p["pnl"] / cap >= level), None)
            e["levels"][str(level)] = {
                "ever_reached": hit is not None,
                "first_crossing": hit["timestamp"] if hit else None,
                "crossing_to_mfe_days": days(hit["timestamp"], best["timestamp"]) if hit else None,
                "crossing_to_exit_days": days(hit["timestamp"], end) if hit else None,
            }
        after = [p for p in path if p["timestamp"] >= best["timestamp"]]
        below_peak = positive = 0.0
        for i, p in enumerate(after):
            next_ts = after[i + 1]["timestamp"] if i + 1 < len(after) else end
            duration = days(p["timestamp"], next_ts)
            if p["pnl"] < mfe:
                below_peak += duration
            if p["pnl"] > 0:
                positive += duration
        e["days_below_peak_after_mfe"] = below_peak
        e["days_positive_after_mfe"] = positive
        e["profit_erosion_times"] = {}
        # Final settlement can realize deterioration; distinguish it from a daily mark.
        observations = [*after, {"timestamp": end, "pnl": e["final_pnl"], "settlement": True}]
        for fraction in (0.25, 0.50, 0.75, 1.0):
            hit = (
                next(
                    (
                        p
                        for p in observations
                        if p["timestamp"] > best["timestamp"] and p["pnl"] <= mfe * (1 - fraction)
                    ),
                    None,
                )
                if mfe > 0
                else None
            )
            e["profit_erosion_times"][str(fraction)] = {
                "timestamp": hit["timestamp"] if hit else None,
                "days": days(best["timestamp"], hit["timestamp"]) if hit else None,
                "observation": "actual_exit_settlement"
                if hit and hit.get("settlement")
                else "completed_bar"
                if hit
                else None,
            }
        for key in ("cashflow", "qty", "basis", "realized"):
            e.pop(key)
    total = sum(e["final_pnl"] for e in episodes)
    return {
        "schema": "crypto-position-giveback-v3",
        "strategy": strategy,
        "normalization": "fixed ex-post total gross purchase outlay including buy fees",
        "extrema": "active completed daily marks only; actual flat settlement excluded",
        "episodes": episodes,
        "portfolio_path": portfolio,
        "reconciliation": {
            "episode_pnl": total,
            "account_profit": raw["metrics"]["ending_equity"] - raw["metrics"]["starting_equity"],
            "final_residual": total - (raw["metrics"]["ending_equity"] - raw["metrics"]["starting_equity"]),
            "maximum_daily_nav_residual": max_nav_error,
        },
    }


def summarize(episodes: list[dict[str, Any]], *, net_strategy_pnl: float | None = None) -> dict[str, Any]:
    positive = [e for e in episodes if e["mfe"] > 0]
    result: dict[str, Any] = {
        "count": len(episodes),
        "positive_mfe_count": len(positive),
        "net_pnl": sum(e["final_pnl"] for e in episodes),
        "winner_to_loser_count": sum(e["final_pnl"] < 0 for e in positive),
        "winner_to_nonpositive_count": sum(e["final_pnl"] <= 0 for e in positive),
        "near_flat_definition": "abs(final_return)<=0.01 descriptive, not an exit rule",
        "positive_mfe_near_flat_count": sum(abs(e["final_return"]) <= 0.01 for e in positive),
    }
    for field in (
        "holding_days",
        "time_to_mfe_days",
        "mfe_to_exit_days",
        "mfe",
        "mae",
        "mfe_return",
        "mae_return",
        "final_pnl",
        "final_return",
        "retention",
        "giveback",
        "days_below_peak_after_mfe",
        "days_positive_after_mfe",
    ):
        result[field] = distribution([e[field] for e in episodes if e[field] is not None])
    bounds: dict[str, Callable[[float], bool]] = {
        "at_least_75pct": lambda r: r >= 0.75,
        "50_to_75pct": lambda r: 0.5 <= r < 0.75,
        "25_to_50pct": lambda r: 0.25 <= r < 0.5,
        "positive_below_25pct": lambda r: 0 < r < 0.25,
        "nonpositive": lambda r: r <= 0,
    }
    result["retention_bins"] = {
        k: {
            "count": sum(test(e["retention"]) for e in positive),
            "fraction": sum(test(e["retention"]) for e in positive) / len(positive) if positive else None,
        }
        for k, test in bounds.items()
    }
    result["profit_levels"] = {}
    for level in LEVELS:
        group = [e for e in episodes if e["levels"][str(level)]["ever_reached"]]
        n = len(group)
        result["profit_levels"][str(level)] = {
            "count": n,
            "mean_final_return": sum(e["final_return"] for e in group) / n if n else None,
            "median_final_return": quantile([e["final_return"] for e in group], 0.5),
            "profitable_fraction": sum(e["final_pnl"] > 0 for e in group) / n if n else None,
            "nonpositive_fraction": sum(e["final_pnl"] <= 0 for e in group) / n if n else None,
            "retention": distribution([e["retention"] for e in group]),
            "crossing_to_mfe_days": quantile([e["levels"][str(level)]["crossing_to_mfe_days"] for e in group], 0.5),
            "mfe_to_exit_days": quantile([e["mfe_to_exit_days"] for e in group], 0.5),
        }
    winners = sorted((e for e in episodes if e["final_pnl"] > 0), key=lambda e: -e["final_pnl"])
    total_positive = sum(e["final_pnl"] for e in winners)
    net = net_strategy_pnl if net_strategy_pnl is not None else result["net_pnl"]
    result["right_tail"] = {}
    for label, n in [
        ("top1", 1),
        ("top3", 3),
        ("top5", 5),
        ("top10", 10),
        ("top10pct", math.ceil(len(episodes) * 0.1)),
    ]:
        selected = winners[:n]
        pnl = sum(e["final_pnl"] for e in selected)
        result["right_tail"][label] = {
            "count": len(selected),
            "pnl": pnl,
            "share_positive_pnl": pnl / total_positive if total_positive else None,
            "share_net_strategy_pnl": pnl / net if net else None,
            "episode_ids": [e["episode_id"] for e in selected],
        }
    result["profit_erosion_times"] = {
        str(f): distribution(
            [
                e["profit_erosion_times"][str(f)]["days"]
                for e in episodes
                if e["profit_erosion_times"][str(f)]["days"] is not None
            ]
        )
        for f in (0.25, 0.5, 0.75, 1.0)
    }
    return result
