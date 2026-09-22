"""Independent arithmetic audit; imports no production strategy or replay functions."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Naive bar timestamp")
    return parsed.astimezone(UTC)


def independently_rank(
    history: Mapping[str, Sequence[Mapping[str, Any]]], eligible: Sequence[str]
) -> list[tuple[float, str]]:
    """Sample sigma of 22 simple daily returns, ascending sigma/symbol."""
    result = []
    for symbol in sorted(set(eligible).intersection(history)):
        window = history[symbol][-23:]
        if len(window) != 23:
            continue
        dates = [timestamp(str(bar["timestamp"])) for bar in window]
        if dates[-1] - dates[0] != timedelta(days=22):
            continue
        closes = [float(bar["close"]) for bar in window]
        if any(not math.isfinite(close) or close <= 0 for close in closes):
            continue
        returns = [right / left - 1 for left, right in zip(closes, closes[1:])]
        result.append((statistics.stdev(returns), symbol))
    return sorted(result)


def independently_select(ranked: Sequence[str], holdings: Mapping[str, float]) -> tuple[list[str], list[str]]:
    retained = [symbol for symbol in ranked[:4] if holdings.get(symbol, 0) > 1e-12][:2]
    selected = (retained + [symbol for symbol in ranked if symbol not in retained])[:2]
    return selected, retained


def scheduled(previous: datetime | None, current: datetime, hours: int = 96) -> bool:
    """UTC epoch buckets, not a per-position minimum hold timer."""
    return previous is None or int(previous.timestamp() // (hours * 3600)) != int(current.timestamp() // (hours * 3600))


def close(actual: Any, expected: Any, path: str = "root") -> None:
    if isinstance(expected, float):
        if not isinstance(actual, (int, float)) or not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8):
            raise AssertionError(f"{path}: {actual!r} != {expected!r}")
    elif isinstance(expected, dict):
        if set(actual) != set(expected):
            raise AssertionError(f"{path}: keys {set(actual)} != {set(expected)}")
        for key, value in expected.items():
            close(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, (tuple, list)):
        if len(actual) != len(expected):
            raise AssertionError(f"{path}: lengths {len(actual)} != {len(expected)}")
        for index, value in enumerate(expected):
            close(actual[index], value, f"{path}[{index}]")
    elif actual != expected:
        raise AssertionError(f"{path}: {actual!r} != {expected!r}")


def check_daily_bars(bars: Sequence[Mapping[str, Any]]) -> None:
    keys = set()
    for bar in bars:
        dt = timestamp(str(bar["timestamp"]))
        if (dt.hour, dt.minute, dt.second, dt.microsecond) != (0, 0, 0, 0):
            raise AssertionError(f"Non-UTC-midnight bar: {bar['timestamp']}")
        key = (bar["symbol"], dt)
        if key in keys:
            raise AssertionError(f"Duplicate daily bar: {key}")
        keys.add(key)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def independently_aggregate(path: Path) -> list[dict[str, Any]]:
    buckets: dict[tuple[datetime, str], dict[datetime, dict[str, str]]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            dt = timestamp(row["timestamp"])
            assert (dt.minute, dt.second, dt.microsecond) == (0, 0, 0), "Unaligned hourly bar"
            day = dt.replace(hour=0)
            bucket = buckets.setdefault((day, row["symbol"]), {})
            assert dt not in bucket, "Duplicate hourly source bar"
            bucket[dt] = row
    daily = []
    for (day, symbol), rows in sorted(buckets.items()):
        if len(rows) != 24 or set(rows) != {day + timedelta(hours=i) for i in range(24)}:
            continue
        ordered = [rows[dt] for dt in sorted(rows)]
        daily.append(
            {
                "timestamp": day.isoformat(),
                "symbol": symbol,
                "market": "crypto",
                "timeframe": "1d",
                "open": float(ordered[0]["open"]),
                "close": float(ordered[-1]["close"]),
                "high": max(float(row["high"]) for row in ordered),
                "low": min(float(row["low"]) for row in ordered),
                "volume": sum(float(row["volume"]) for row in ordered),
            }
        )
    return daily


def _targets(
    selected: list[str],
    positions: dict[str, dict[str, float]],
    marks: dict[str, float],
    nav: float,
    previous_nav: float,
    peak: float,
    engine: dict[str, Any],
) -> dict[str, float]:
    if not selected:
        return {}
    limits = engine["risk_limits"]
    halted = max(0.0, (previous_nav - nav) / nav) >= limits["daily_loss_limit"]
    if engine["enforce_drawdown_halt"] and (peak - nav) / peak >= limits["drawdown_halt"]:
        halted = True
    desired = {symbol: min(1 / len(selected), limits["maximum_crypto_asset"]) for symbol in sorted(selected)}
    if halted:
        desired = {
            symbol: min(weight, positions.get(symbol, {}).get("qty", 0) * marks[symbol] / nav)
            for symbol, weight in desired.items()
        }
    priority = sorted(desired, key=lambda symbol: (-desired[symbol], symbol))
    kept = priority[: min(limits["maximum_positions"], limits["maximum_crypto_positions"])]
    desired = {symbol: desired[symbol] for symbol in kept}
    limit = max(
        0,
        min(
            engine["maximum_allocation"], limits["maximum_crypto"], limits["maximum_gross"], 1 - limits["minimum_cash"]
        ),
    )
    total = sum(desired.values())
    if total > limit:
        desired = {symbol: weight * limit / total for symbol, weight in desired.items()}
    return {symbol: weight for symbol, weight in sorted(desired.items()) if weight > 1e-12}


def verify_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct every decision, order, fill and daily balance from inputs.

    Scanner membership is an input contract: this checks causal snapshot use,
    rather than independently reimplementing the scanner's eligibility model.
    """
    data = payload["data"]
    close(file_sha256(Path(data["path"])), data["sha256"], "input_sha256")
    for field, expected_hash in payload.get("hashes", {}).items():
        digest = hashlib.sha256(
            json.dumps(payload[field], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        close(digest, expected_hash, f"artifact_hash.{field}")
    bars = payload["daily_bars"]
    check_daily_bars(bars)
    close(bars, independently_aggregate(Path(data["path"])), "source_to_daily")
    engine = payload["engine"]
    strategy = payload["config"]["strategy"]["crypto"]
    close(strategy["name"], "crypto_low_variance_hysteresis_v1", "strategy.name")
    close(strategy["timeframe"], "1d", "strategy.timeframe")
    close(
        strategy["parameters"], {"rebalance_hours": 96, "top_n": 2, "variance_lookback_days": 22}, "strategy.parameters"
    )
    close(engine["maximum_allocation"], strategy["maximum_allocation"], "engine.config_allocation")
    for field in ("commission", "slippage_bps", "spread_bps", "crypto_taker_fee_bps"):
        close(engine[field], payload["config"]["backtest"][field], f"engine.config.{field}")
    close(engine["starting_cash"], payload["config"]["backtest"]["initial_cash"], "engine.config_capital")
    if "risk" in payload["config"]:
        risk_mapping = {
            "daily_loss_limit": "daily_loss_limit_pct",
            "drawdown_halt": "max_drawdown_pct",
            "maximum_gross": "max_gross_exposure_pct",
            "maximum_crypto": "max_crypto_exposure_pct",
            "maximum_equity": "max_gross_exposure_pct",
            "maximum_equity_asset": "max_position_pct",
            "maximum_crypto_asset": "max_crypto_position_pct",
            "minimum_cash": "cash_buffer_pct",
            "maximum_positions": "max_positions",
            "maximum_crypto_positions": "max_crypto_positions",
        }
        close(
            engine["risk_limits"],
            {key: payload["config"]["risk"][value] for key, value in risk_mapping.items()},
            "risk.config",
        )
        close(
            engine["minimum_order_notional"],
            payload["config"]["execution"]["minimum_order_notional"],
            "execution.notional",
        )
        close(engine["turnover_band"], payload["config"]["execution"]["rebalance_band_pct"], "execution.band")
    close(engine["maximum_allocation"], 0.05, "allocation")
    start, end = timestamp(payload["window"]["start"]), timestamp(payload["window"]["end"])
    decisions = {timestamp(item["timestamp"]): item for item in payload["decisions"]}
    snapshots = {timestamp(item["timestamp"]): item for item in payload["snapshots"]}
    assert len(decisions) == len(payload["decisions"]), "Duplicate decisions"
    assert len(snapshots) == len(payload["snapshots"]), "Duplicate NAV observations"
    history: dict[str, list[dict[str, Any]]] = {}
    grouped: dict[datetime, list[dict[str, Any]]] = {}
    for bar in sorted(bars, key=lambda item: (timestamp(item["timestamp"]), item["symbol"])):
        dt = timestamp(bar["timestamp"])
        if dt < start:
            history.setdefault(bar["symbol"], []).append(bar)
        elif dt <= end:
            grouped.setdefault(dt, []).append(bar)
    close(set(snapshots), set(grouped), "snapshot_timestamps")
    cash = float(engine["starting_cash"])
    previous_nav = peak = cash
    positions: dict[str, dict[str, float]] = {}
    marks: dict[str, float] = {}
    pending: list[dict[str, Any]] = []
    calculated_fills: list[dict[str, Any]] = []
    previous_time: datetime | None = None
    checked_decisions = retained_count = vacancy_count = exits = 0
    realized_pnl = 0.0
    for dt, cycle in grouped.items():
        for bar in cycle:
            history.setdefault(bar["symbol"], []).append(bar)
        for side in ("sell", "buy"):
            for bar in cycle:
                symbol = bar["symbol"]
                remaining = []
                for order in pending:
                    if order["symbol"] != symbol or order["side"] != side or order["created_at"] >= dt:
                        remaining.append(order)
                        continue
                    volumes = [
                        float(row["volume"])
                        for row in history[symbol]
                        if timestamp(row["timestamp"]) <= order["created_at"] and row["volume"] > 0
                    ]
                    volumes = volumes[-engine["liquidity_lookback_bars"] :]
                    capacity = sum(volumes) / len(volumes) * engine["max_participation_pct"] if volumes else 0
                    if capacity <= 0:
                        remaining.append(order)
                        continue
                    qty = min(order["qty"], capacity)
                    if side == "sell":
                        if symbol not in positions:
                            continue
                        qty = min(qty, positions[symbol]["qty"])
                    reference = float(bar["open"])
                    price = reference * (
                        1 + (1 if side == "buy" else -1) * (engine["slippage_bps"] + engine["spread_bps"] / 2) / 10000
                    )
                    fee = engine["commission"] + qty * price * engine["crypto_taker_fee_bps"] / 10000
                    if side == "buy":
                        if qty * price + fee > cash:
                            remaining.append(order)
                            continue
                        cash -= qty * price + fee
                        old = positions.get(
                            symbol, {"qty": 0.0, "avg_price": 0.0, "entry_fee": 0.0, "realized_pnl_accumulator": 0.0}
                        )
                        total = old["qty"] + qty
                        positions[symbol] = {
                            "qty": total,
                            "avg_price": (old["qty"] * old["avg_price"] + qty * price) / total,
                            "entry_fee": old["entry_fee"] + fee,
                            "realized_pnl_accumulator": old["realized_pnl_accumulator"],
                        }
                    else:
                        old = positions[symbol]
                        share = old["entry_fee"] * qty / max(1e-9, old["qty"])
                        delta = (price - old["avg_price"]) * qty - fee - share
                        realized_pnl += delta
                        old["realized_pnl_accumulator"] += delta
                        old["entry_fee"] -= share
                        old["qty"] -= qty
                        cash += qty * price - fee
                        if old["qty"] <= 0:
                            positions.pop(symbol)
                            exits += 1
                    calculated_fills.append(
                        {
                            "timestamp": dt.isoformat(),
                            "symbol": symbol,
                            "side": side,
                            "qty": qty,
                            "reference_price": reference,
                            "fill_price": price,
                            "fee": fee,
                            "slippage_cost": qty * reference * engine["slippage_bps"] / 10000,
                            "spread_cost": qty * reference * engine["spread_bps"] / 20000,
                            "decision_timestamp": order["decision_timestamp"].isoformat(),
                        }
                    )
                    if qty < order["qty"]:
                        remaining.append({**order, "qty": order["qty"] - qty, "created_at": dt})
                pending = remaining
        marks.update({bar["symbol"]: float(bar["close"]) for bar in cycle})
        nav = cash + sum(position["qty"] * marks[symbol] for symbol, position in positions.items())
        peak = max(peak, nav)
        applicable = [row for row in payload["universe"]["snapshots"] if timestamp(row["effective_at"]) <= dt]
        assert applicable, f"No causal universe at {dt}"
        univ = applicable[-1]
        base = univ["observation_set"] or sorted(univ["symbols"])
        capacity = int(univ["maximum_stream_symbols"])
        if capacity:
            reserved = list(
                dict.fromkeys([*univ["pinned_symbols"], *positions, *(order["symbol"] for order in pending)])
            )
            visible = list(dict.fromkeys([*reserved, *base]))[:capacity]
        else:
            visible = sorted(base)
        complete = all(symbol in history and timestamp(history[symbol][-1]["timestamp"]) == dt for symbol in visible)
        should_decide = scheduled(previous_time, dt) and complete
        close(dt in decisions, should_decide, f"{dt}.decision_presence")
        previous_time = dt
        if should_decide:
            observed = decisions[dt]
            expected_history = {symbol: history[symbol][-23:] for symbol in visible if symbol in history}
            close(observed["history"], expected_history, f"{dt}.history")
            eligible = sorted(set(expected_history).intersection(univ["symbols"]))
            close(sorted(observed["eligible_symbols"]), eligible, f"{dt}.eligible")
            holdings = {symbol: position["qty"] for symbol, position in positions.items()}
            close(observed["holdings"], holdings, f"{dt}.holdings")
            close(observed["equity"], nav, f"{dt}.decision_nav")
            scores = independently_rank(expected_history, eligible)
            close(observed["scores"], scores, f"{dt}.scores")
            selected, retained = independently_select([symbol for _, symbol in scores], holdings)
            alpha = observed["alpha_target"]
            close(alpha["metadata"].get("selected_symbols", []), selected, f"{dt}.selection")
            close(alpha["metadata"].get("retained", []), retained, f"{dt}.retention")
            alpha_weights = {symbol: 1 / len(selected) for symbol in sorted(selected)}
            close({row["symbol"]: row["weight"] for row in alpha["weights"]}, alpha_weights, f"{dt}.alpha")
            targets = _targets(selected, positions, marks, nav, previous_nav, peak, engine)
            close({row["symbol"]: row["weight"] for row in observed["target"]["weights"]}, targets, f"{dt}.target")
            close(observed["target"]["cash_weight"], 1 - sum(targets.values()), f"{dt}.target_cash")
            pending = []
            for symbol in dict.fromkeys([*targets, *positions]):
                weight = targets.get(symbol, 0.0)
                qty = positions.get(symbol, {}).get("qty", 0.0)
                current_weight = qty * marks[symbol] / nav
                if abs(weight - current_weight) < engine["turnover_band"]:
                    continue
                delta_qty = weight * nav / marks[symbol] - qty
                if abs(delta_qty) * marks[symbol] < engine["minimum_order_notional"]:
                    continue
                pending.append(
                    {
                        "symbol": symbol,
                        "side": "buy" if delta_qty > 0 else "sell",
                        "qty": abs(delta_qty),
                        "created_at": dt,
                        "decision_timestamp": dt,
                    }
                )
            expected_orders = [
                {**order, "created_at": order["created_at"].isoformat(), "decision_timestamp": dt.isoformat()}
                for order in pending
            ]
            actual_orders = (
                [{key: row[key] for key in expected_orders[0]} for row in observed["orders"]]
                if expected_orders
                else observed["orders"]
            )
            close(actual_orders, expected_orders, f"{dt}.orders")
            retained_count += len(retained)
            vacancy_count += len(selected) - len(retained)
            checked_decisions += 1
        observed_snapshot = snapshots[dt]
        close(observed_snapshot["cash"], cash, f"{dt}.cash")
        close(observed_snapshot["nav"], nav, f"{dt}.nav")
        expected_positions = {
            symbol: {key: position[key] for key in ("qty", "avg_price", "entry_fee")} | {"mark": marks[symbol]}
            for symbol, position in positions.items()
        }
        close(observed_snapshot["positions"], expected_positions, f"{dt}.positions")
        last_day_start = previous_nav
        previous_nav = nav
    pre_liquidation_nav = previous_nav
    actual_fills = (
        [{key: row[key] for key in calculated_fills[0]} for row in payload["fills"]]
        if calculated_fills
        else payload["fills"]
    )
    close(actual_fills, calculated_fills, "all_fills")
    terminal = []
    if engine["liquidate_at_end"]:
        for symbol, position in list(positions.items()):
            qty = position["qty"]
            reference = marks[symbol]
            price = reference * (1 - (engine["slippage_bps"] + engine["spread_bps"] / 2) / 10000)
            fee = engine["commission"] + qty * price * engine["crypto_taker_fee_bps"] / 10000
            proceeds = qty * price - fee
            before = cash
            cash += proceeds
            realized_pnl += (price - position["avg_price"]) * qty - position["entry_fee"] - fee
            terminal.append(
                {
                    "symbol": symbol,
                    "timestamp": history[symbol][-1]["timestamp"],
                    "quantity": qty,
                    "policy": "FINAL_BAR",
                    "effective_policy": "FINAL_BAR",
                    "last_close": reference,
                    "fill_price": price,
                    "proceeds": max(0, proceeds),
                    "fee": fee,
                    "slippage_cost": qty * reference * engine["slippage_bps"] / 10000,
                    "spread_cost": qty * reference * engine["spread_bps"] / 20000,
                    "cash_before": before,
                    "cash_after": cash,
                    "position_removed": True,
                }
            )
            positions.pop(symbol)
        previous_nav = cash
    close(payload["terminal_events"], terminal, "terminal_events")
    final = payload["final_state"]
    close(final["cash"], cash, "final.cash")
    close(final["holdings"], {symbol: position["qty"] for symbol, position in positions.items()}, "final.holdings")
    close(
        final["avg_prices"],
        {symbol: position["avg_price"] for symbol, position in positions.items()},
        "final.avg_prices",
    )
    close(
        final["entry_fees"],
        {symbol: position["entry_fee"] for symbol, position in positions.items()},
        "final.entry_fees",
    )
    close(final["realized_pnl"], realized_pnl, "final.realized_pnl")
    close(payload["metrics"]["ending_equity"], previous_nav, "ending_equity")
    if "schema_version" in final:
        close(final["last_prices"], marks, "final.last_prices")
        close(final["peak_equity"], peak, "final.peak_equity")
        close(final["last_observed_equity"], pre_liquidation_nav, "final.last_observed_equity")
        close(final["day_start_equity"], last_day_start, "final.day_start_equity")
        close(final["last_processed_bar"], dt.isoformat(), "final.last_processed_bar")
        close(final["risk_session_date"], dt.date().isoformat(), "final.risk_session_date")
        close(final["rebalance_count"], checked_decisions, "final.rebalance_count")
        close(final["last_rebalance_at"], max(decisions).isoformat(), "final.last_rebalance_at")
        close(final["last_target"], targets, "final.last_target")
        close(
            final["realized_pnl_accumulators"],
            {symbol: position["realized_pnl_accumulator"] for symbol, position in positions.items()},
            "final.realized_pnl_accumulators",
        )
        close(final["strategy_name"], strategy["name"], "final.strategy_name")
        close(final["strategy_version"], "1.0.0", "final.strategy_version")
        close(final["universe_version"], payload["universe_hash"], "final.universe_version")
        if engine["liquidate_at_end"]:
            close(final["pending_orders"], [], "final.pending_orders")
            close(final["pending_target"], {}, "final.pending_target")
    return {
        "status": "PASS",
        "independent_production_imports": False,
        "input_sha256": data["sha256"],
        "daily_bars": len(bars),
        "decisions": checked_decisions,
        "fills": len(calculated_fills),
        "nav_observations": len(snapshots),
        "retained_holds": retained_count,
        "vacancy_acquisitions": vacancy_count,
        "full_exits": exits,
        "scope": "all decisions, orders, fills, costs, daily cash/positions/NAV and final inventory",
        "scanner_scope": "causal membership/visibility from supplied hashed universe; scanner not reimplemented",
        "numeric_tolerance": {"relative": 1e-10, "absolute": 1e-8},
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("replay", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    raw = args.replay.read_bytes()
    audit = verify_payload(json.loads(gzip.decompress(raw) if args.replay.suffix == ".gz" else raw))
    audit["replay_file_sha256"] = hashlib.sha256(raw).hexdigest()
    text = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text)
    print(text)
