"""Observational continuous-position accounting. No alternative exit simulation."""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Any

LEVELS = (0.05, 0.10, 0.20, 0.30, 0.40)


def days(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400


def reason(row: dict[str, Any], targets: dict[str, Any], strategy: str) -> str:
    if row["type"] == "terminal":
        return "horizon_censored" if row["event"]["policy"] == "FINAL_BAR" else "lifecycle_terminal"
    target = targets.get(str(row.get("decision_timestamp", "")))
    if target is None:
        return "other_unresolved"
    symbol = row["symbol"]
    meta = target.get("metadata", {})
    weights = {w["symbol"]: w["weight"] for w in target["weights"]}
    if symbol in meta.get("selected_symbols", []) and not weights.get(symbol, 0):
        return "risk_overlay"
    if meta.get("risk_off_detail"):
        return "eligibility_gate_failure"
    if not weights.get(symbol, 0):
        return "hysteresis_selection_exit" if "crypto" in strategy else "alpha_no_longer_selected"
    return "rebalance_sizing"


def reconstruct(raw: dict[str, Any]) -> dict[str, Any]:
    strategy = raw["strategy"]
    active: dict[str, dict[str, Any]] = {}
    episodes: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    targets: dict[str, Any] = {}
    portfolio: list[dict[str, Any]] = []
    realized_closed = 0.0
    peak_open = 0.0

    def point(ep: dict[str, Any], ts: str, mark: float) -> None:
        pnl = ep["cashflow"] + ep["qty"] * mark
        ep["path"].append(
            {
                "timestamp": ts,
                "pnl": pnl,
                "realized": ep["realized"],
                "unrealized": pnl - ep["realized"],
                "qty": ep["qty"],
                "mark": mark,
            }
        )

    def finish(ep: dict[str, Any], row: dict[str, Any], ts: str, mark: float) -> None:
        nonlocal realized_closed
        ep["qty"] = 0.0
        point(ep, ts, mark)
        ep["exit_timestamp"] = ts
        ep["exit_reason"] = reason(row, targets, strategy)
        ep["exit_evidence"] = row
        ep["censored"] = ep["exit_reason"] == "horizon_censored"
        realized_closed += ep["cashflow"]
        active.pop(ep["symbol"])
        episodes.append(ep)

    for row in raw["events"]:
        kind = row["type"]
        if kind == "target":
            targets[row["decision_timestamp"]] = row
            for symbol, ep in active.items():
                explanation = row.get("metadata", {}).get("explanations", {}).get(symbol)
                ep["decisions"].append(
                    {
                        "timestamp": row["decision_timestamp"],
                        "explanation": explanation,
                        "selected": symbol in row.get("metadata", {}).get("selected_symbols", []),
                        "retained": symbol in row.get("metadata", {}).get("retained", []),
                        "risk_overlay_reasons": row.get("metadata", {}).get("risk_overlay_reasons", []),
                    }
                )
            continue
        if kind == "action":
            ep = active[row["symbol"]]
            ep["actions"].append(row)
            if row["action_type"] == "dividend":
                credit = ep["qty"] * row["value"]
                ep["cashflow"] += credit
                ep["realized"] += credit
                ep["dividends"] += credit
            elif row["action_type"] == "split":
                ep["qty"] *= row["value"]
            else:
                raise ValueError(f"Unsupported corporate action {row}")
            continue
        if kind in {"fill", "terminal"}:
            fill = row if kind == "fill" else {**row["event"], "qty": row["event"]["quantity"], "side": "sell"}
            symbol, ts = fill["symbol"], fill["timestamp"]
            qty, price, fee = fill["qty"], fill["fill_price"], fill["fee"]
            if fill["side"] == "buy":
                if symbol not in active:
                    counters[symbol] += 1
                    entities = {
                        e["entity_id"]
                        for e in raw.get("entities", [])
                        if e["symbol"] == symbol
                        and e["valid_from"][:10] <= ts[:10]
                        and (not e["valid_to"] or ts[:10] <= e["valid_to"][:10])
                    }
                    if len(entities) > 1:
                        raise ValueError(f"Ambiguous entity {symbol} {ts}: {entities}")
                    active[symbol] = {
                        "strategy": strategy,
                        "symbol": symbol,
                        "entity_id": next(iter(entities), symbol),
                        "episode_id": f"{strategy}:{symbol}:{counters[symbol]}",
                        "entry_timestamp": ts,
                        "qty": 0.0,
                        "capital": 0.0,
                        "cashflow": 0.0,
                        "basis": 0.0,
                        "realized": 0.0,
                        "dividends": 0.0,
                        "fees": 0.0,
                        "slippage_cost": 0.0,
                        "spread_cost": 0.0,
                        "fills": [],
                        "actions": [],
                        "path": [],
                        "decisions": [],
                    }
                ep = active[symbol]
                outlay = qty * price + fee
                ep["qty"] += qty
                ep["capital"] += outlay
                ep["basis"] += outlay
                ep["cashflow"] -= outlay
            else:
                ep = active[symbol]
                if qty > ep["qty"] + 1e-7:
                    raise ValueError("Sell exceeds holdings")
                removed_basis = ep["basis"] * qty / ep["qty"]
                proceeds = qty * price - fee
                ep["realized"] += proceeds - removed_basis
                ep["basis"] -= removed_basis
                ep["cashflow"] += proceeds
                ep["qty"] -= qty
            ep["fills"].append(row)
            for name in ("fee", "slippage_cost", "spread_cost"):
                ep["fees" if name == "fee" else name] += fill.get(name, 0)
            if fill["side"] == "sell" and ep["qty"] <= 1e-9:
                finish(ep, row, ts, price)
            continue
        if kind == "mark":
            unrealized = realized = 0.0
            for symbol, ep in active.items():
                position = row["positions"][symbol]
                if not math.isclose(ep["qty"], position["qty"], abs_tol=1e-7):
                    raise ValueError("Position reconciliation failed")
                point(ep, row["timestamp"], position["mark"])
                unrealized += ep["path"][-1]["unrealized"]
                realized += ep["realized"]
            combined = realized_closed + realized + unrealized
            peak_open = max(peak_open, unrealized)
            portfolio.append(
                {
                    "timestamp": row["timestamp"],
                    "nav": row["nav"],
                    "unrealized": unrealized,
                    "realized": realized_closed + realized,
                    "combined_pnl": combined,
                    "peak_open_unrealized": peak_open,
                    "open_profit_below_running_peak": peak_open - unrealized,
                    "active_episode_giveback": sum(
                        max(p["pnl"] for p in e["path"]) - e["path"][-1]["pnl"] for e in active.values()
                    ),
                }
            )
    if active:
        raise ValueError("Unclosed episodes: canonical final liquidation missing")
    if portfolio:
        portfolio[-1]["pre_final_liquidation"] = {
            k: portfolio[-1][k] for k in ("nav", "unrealized", "realized", "combined_pnl")
        }
        portfolio[-1].update(
            nav=raw["ending_equity"],
            unrealized=0.0,
            realized=realized_closed,
            combined_pnl=realized_closed,
            open_profit_below_running_peak=peak_open,
            active_episode_giveback=0.0,
        )
    for ep in episodes:
        path = ep["path"]
        best, worst = max(path, key=lambda p: p["pnl"]), min(path, key=lambda p: p["pnl"])
        final, mfe, mae = path[-1]["pnl"], best["pnl"], worst["pnl"]
        ep.update(
            final_pnl=final,
            mfe=mfe,
            mae=mae,
            mfe_timestamp=best["timestamp"],
            mae_timestamp=worst["timestamp"],
            final_return=final / ep["capital"],
            mfe_return=mfe / ep["capital"],
            mae_return=mae / ep["capital"],
            giveback=mfe - final if mfe > 0 else None,
            retention=final / mfe if mfe > 0 else None,
            holding_days=days(ep["entry_timestamp"], ep["exit_timestamp"]),
            time_to_mfe_days=days(ep["entry_timestamp"], best["timestamp"]),
            mfe_to_exit_days=days(best["timestamp"], ep["exit_timestamp"]),
            fill_count=len(ep["fills"]),
            rebalance_count=len({f.get("decision_timestamp") for f in ep["fills"] if f["type"] == "fill"}),
        )
        ep["levels"] = {}
        for level in (0.0, *LEVELS):
            crossed = [p for p in path if p["pnl"] / ep["capital"] >= level and (level > 0 or p["pnl"] > 0)]
            time_above = sum(
                days(a["timestamp"], b["timestamp"])
                for a, b in zip(path, path[1:])
                if a["pnl"] / ep["capital"] >= level and (level > 0 or a["pnl"] > 0)
            )
            ep["levels"][str(level)] = {
                "ever_reached": bool(crossed),
                "observations_above": len(crossed),
                "days_above_left_mark": time_above,
                "first_crossing": crossed[0]["timestamp"] if crossed else None,
                "crossing_to_exit_days": days(crossed[0]["timestamp"], ep["exit_timestamp"]) if crossed else None,
            }
        negative = next((p for p in path if p["timestamp"] > best["timestamp"] and p["pnl"] < 0), None)
        ep["peak_to_negative_days"] = days(best["timestamp"], negative["timestamp"]) if mfe > 0 and negative else None
        for key in ("cashflow", "basis", "qty", "realized"):
            ep.pop(key)
    total = sum(e["final_pnl"] for e in episodes)
    residual = raw["ending_equity"] - raw["starting_equity"] - raw.get("cash_yield", 0) - total
    return {
        "schema": "position-exit-diagnostics-v1",
        "strategy": strategy,
        "period": raw.get("period", {}),
        "normalization": "fixed total episode buy outlay including fees; ex-post capital denominator",
        "episodes": episodes,
        "portfolio_path": portfolio,
        "reconciliation": {
            "episode_pnl": total,
            "account_profit": raw["ending_equity"] - raw["starting_equity"],
            "cash_yield": raw.get("cash_yield", 0),
            "residual": residual,
        },
    }


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
            label: quantile(values, q)
            for label, q in (("p10", 0.1), ("p25", 0.25), ("median", 0.5), ("p75", 0.75), ("p90", 0.9))
        },
    }


def summarize(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [e for e in episodes if e["mfe"] > 0]
    out: dict[str, Any] = {"count": len(episodes), "positive_mfe_count": len(positive)}
    for metric in (
        "final_pnl",
        "final_return",
        "mfe",
        "mae",
        "mfe_return",
        "mae_return",
        "holding_days",
        "time_to_mfe_days",
        "mfe_to_exit_days",
        "giveback",
        "retention",
    ):
        out[metric] = distribution([e[metric] for e in episodes if e[metric] is not None])
    out["retention_fractions"] = {
        str(x): sum(e["retention"] >= x for e in positive) / len(positive) if positive else None
        for x in (0.75, 0.5, 0.25, 0)
    }
    out["positive_mfe_to_loss_fraction"] = (
        sum(e["final_pnl"] < 0 for e in positive) / len(positive) if positive else None
    )
    out["profit_levels"] = {}
    for level in LEVELS:
        selected = [e for e in episodes if e["levels"][str(level)]["ever_reached"]]
        out["profit_levels"][str(level)] = {
            "count": len(selected),
            "final_mean_return": sum(e["final_return"] for e in selected) / len(selected) if selected else None,
            "final_median_return": quantile([e["final_return"] for e in selected], 0.5),
            "profitable_exit_fraction": sum(e["final_pnl"] > 0 for e in selected) / len(selected) if selected else None,
            "exit_nonpositive_fraction": sum(e["final_pnl"] <= 0 for e in selected) / len(selected)
            if selected
            else None,
            "median_retention": quantile([e["retention"] for e in selected], 0.5),
            "median_crossing_to_exit_days": quantile(
                [e["levels"][str(level)]["crossing_to_exit_days"] for e in selected], 0.5
            ),
        }
    winners = sorted((e for e in episodes if e["final_pnl"] > 0), key=lambda e: -e["final_pnl"])
    positive_total = sum(e["final_pnl"] for e in winners)
    net = sum(e["final_pnl"] for e in episodes)
    out["right_tail"] = {}
    for label, n in (("top1", 1), ("top5", 5), ("top10", 10), ("top10pct", math.ceil(len(episodes) * 0.1))):
        pnl = sum(e["final_pnl"] for e in winners[:n])
        out["right_tail"][label] = {
            "episode_count": min(n, len(winners)),
            "pnl": pnl,
            "share_positive_pnl": pnl / positive_total if positive_total else None,
            "share_net_pnl": pnl / net if net else None,
            "episodes": [
                {
                    "episode_id": e["episode_id"],
                    "final_pnl": e["final_pnl"],
                    "mfe_return": e["mfe_return"],
                    "final_return": e["final_return"],
                }
                for e in winners[:n]
            ],
        }
    return out
