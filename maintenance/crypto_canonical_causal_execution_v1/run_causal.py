#!/usr/bin/env python3
"""Isolated crypto replay under the precommitted causal execution contract."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from maintenance.crypto_canonical_causal_execution_v1.contract import (  # noqa: E402
    DAY,
    complete_daily_available_at,
    first_causal_hour,
)
from parayabuck.backtest.numeric_history import build_numeric_history  # noqa: E402
from parayabuck.backtest.portfolio_engine import (  # noqa: E402
    OrderSide,
    PortfolioBacktestEngine,
    PortfolioOrder,
)
from parayabuck.backtest.product import _prepared_window, build_product_universe, load_csv_bars  # noqa: E402
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for  # noqa: E402
from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1  # noqa: E402

SOURCE = ROOT / "data/historical/crypto_expanded_1h.csv"
CONFIG = ROOT / "config/settings.yaml"
START = datetime(2021, 4, 1, tzinfo=UTC)
END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
EXPECTED_SOURCE_SHA = "73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e"


def plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return plain(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def write_gzip(path: Path, payload: dict[str, Any]) -> str:
    data = canonical_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(data)
    return hashlib.sha256(data).hexdigest()


class CausalEngine(PortfolioBacktestEngine):
    def _known_average_volume(self, order: PortfolioOrder, timeframe: str) -> float:
        # Daily rows may already be ingested for the current engine cycle. Their
        # start labels cannot be used as proof that their full volume was known.
        history = self._execution_history.get((order.symbol, timeframe), ())
        known = [bar.volume for bar in history if bar.timestamp + DAY <= order.created_at and bar.volume > 0][
            -self.liquidity_lookback_bars :
        ]
        return sum(known) / len(known) if known else 0.0


def price_bar(source: Bar) -> Bar:
    """Reuse engine fill/cost code at a source hourly OPEN, with daily capacity."""
    return Bar(
        timestamp=source.timestamp,
        symbol=source.symbol,
        timeframe="1d",
        open=source.open,
        high=source.open,
        low=source.open,
        close=source.open,
        volume=0.0,
        market=Market.CRYPTO,
    )


def run() -> dict[str, Any]:
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != EXPECTED_SOURCE_SHA:
        raise ValueError("Pinned hourly source hash changed")
    config = load_config(str(CONFIG))
    hourly = load_csv_bars(SOURCE, default_market=Market.CRYPTO)
    daily = aggregate_historical_bars(hourly, "1d", require_complete=True)
    universe = build_product_universe(config, hourly, market=Market.CRYPTO, end=END, timeframe="1h")
    universe_hash = hash_point_in_time_universe(universe)
    selection = config.strategy.crypto
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    if not isinstance(strategy, CryptoLowVarianceHysteresis1dV1):
        raise ValueError("Unexpected canonical crypto strategy")
    filtered, warmup, (ordered, _decision_bars) = _prepared_window(daily, Market.CRYPTO, "1d", START, END)
    engine = CausalEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps,
        spread_bps=config.backtest.spread_bps,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
        benchmark_symbol="BTC/USD",
        universe=universe,
        universe_version=universe_hash,
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=selection.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
    )
    engine._numeric_history = build_numeric_history(warmup + ordered, market=Market.CRYPTO, timeframe="1d")
    for bar in warmup:
        engine._ingest(bar)
        engine._ingest_execution(bar)

    hourly_by_symbol_day: dict[tuple[str, datetime], list[Bar]] = defaultdict(list)
    for bar in hourly:
        day = bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        hourly_by_symbol_day[(bar.symbol, day)].append(bar)
    for series in hourly_by_symbol_day.values():
        series.sort(key=lambda item: item.timestamp)
    universe_data = cast(dict[str, Any], universe.to_dict())
    final_observation_set = universe_data["snapshots"][-1]["observation_set"]
    if not final_observation_set:
        raise ValueError("Final scanner observation set is empty")
    latest_by_symbol = {
        symbol: max(
            bar.timestamp for (name, _day), bars in hourly_by_symbol_day.items() if name == symbol for bar in bars
        )
        for symbol in final_observation_set
    }
    terminal_at = min(latest_by_symbol.values())
    if terminal_at.tzinfo is None:
        raise AssertionError("Terminal boundary must be timezone aware")
    terminal_order_created_at = terminal_at - DAY
    daily_components = hourly_by_symbol_day
    decisions: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    price_events: list[dict[str, Any]] = []
    curve: list[float] = []
    curve_timestamps: list[datetime] = []
    original_decide = CryptoLowVarianceHysteresis1dV1.decide
    original_apply = PortfolioBacktestEngine._apply_target

    def capture_decide(self: Any, context: Any) -> Any:
        target = original_decide(self, context)
        decisions.append(
            {
                "label_time": context.decision_timestamp.isoformat(),
                "history": {symbol: plain(list(bars[-23:])) for symbol, bars in sorted(context.history.items())},
                "eligible_symbols": [symbol for symbol in sorted(context.history) if context.in_universe(symbol)],
                "holdings": plain(context.holdings),
                "prices": plain(context.prices),
                "scores": plain(self._score_candidates(context)),
                "alpha_target": plain(target),
            }
        )
        return target

    def capture_apply(self: Any, target: Any) -> None:
        original_apply(self, target)
        decisions[-1]["target"] = plain(target)

    grouped: dict[datetime, list[Bar]] = defaultdict(list)
    for bar in ordered:
        grouped[bar.timestamp].append(bar)

    terminal_seen = False
    with (
        patch.object(CryptoLowVarianceHysteresis1dV1, "decide", capture_decide),
        patch.object(PortfolioBacktestEngine, "_apply_target", capture_apply),
    ):
        for label in sorted(grouped):
            available_at = label + DAY
            if available_at > terminal_at:
                break
            cycle = grouped[label]
            if available_at == terminal_at:
                terminal_seen = True
                engine._pending.clear()
                for position in list(engine._positions.values()):
                    day = terminal_at.replace(hour=0, minute=0, second=0, microsecond=0)
                    source = next(
                        (
                            item
                            for item in hourly_by_symbol_day.get((position.symbol, day), ())
                            if item.timestamp == terminal_at
                        ),
                        None,
                    )
                    if source is None or source.timestamp <= terminal_order_created_at:
                        raise ValueError(f"No common terminal hourly execution for {position.symbol}")
                    engine._close_position(position, price_bar(source))
                    engine._liquidation_events[-1]["policy"] = "PRECOMMITTED_COMMON_SOURCE_HOUR_OPEN"
                    engine._liquidation_events[-1]["effective_policy"] = "PRECOMMITTED_COMMON_SOURCE_HOUR_OPEN"
                engine._pending.clear()
            for bar in cycle:
                expected = complete_daily_available_at(bar, daily_components.get((bar.symbol, label), ()))
                if expected != available_at:
                    raise AssertionError("Daily component availability mismatch")
                engine._ingest(bar)
                engine._ingest_execution(bar)
                engine._last_price[bar.symbol] = bar.close

            engine._update_risk_state(label)
            if available_at < terminal_at:
                before = len(engine._rebalances)
                engine._decide(label)
                if len(engine._rebalances) > before:
                    decisions[-1]["decision_at"] = available_at.isoformat()
                    for order in engine._pending:
                        if order.decision_timestamp == label:
                            order.created_at = available_at
                            orders.append(plain(order))

            engine._record_holdings_snapshot(label)
            snapshots.append(
                {
                    "label_time": label.isoformat(),
                    "available_at": available_at.isoformat(),
                    "cash": engine._cash,
                    "nav": engine._equity(),
                    "positions": {
                        symbol: {
                            "qty": position.qty,
                            "avg_price": position.avg_price,
                            "entry_fee": position.entry_fee,
                            "mark": engine._last_price.get(symbol, position.avg_price),
                        }
                        for symbol, position in sorted(engine._positions.items())
                    },
                }
            )
            curve.append(engine._equity())
            curve_timestamps.append(available_at)

            if available_at >= terminal_at:
                continue
            for side in (OrderSide.SELL, OrderSide.BUY):
                symbols = sorted({order.symbol for order in engine._pending if order.side == side})
                for symbol in symbols:
                    matching = [order for order in engine._pending if order.symbol == symbol and order.side == side]
                    created_at = min(order.created_at for order in matching)
                    source = first_causal_hour(
                        hourly_by_symbol_day.get((symbol, available_at), ()),
                        boundary=available_at,
                        order_created_at=created_at,
                    )
                    if source is None:
                        continue
                    before = len(engine._fills)
                    engine._fill_side(price_bar(source), side)
                    for fill in engine._fills[before:]:
                        price_events.append(
                            {
                                "client_order_id": fill.client_order_id,
                                "order_created_at": created_at.isoformat(),
                                "source_price_event_time": source.timestamp.isoformat(),
                                "reference_open": source.open,
                            }
                        )

    if not terminal_seen:
        raise AssertionError("No daily cycle reached the common terminal boundary")
    if engine._positions:
        raise AssertionError("Terminal inventory not flat")
    engine.state.holdings = {}
    engine.state.avg_prices = {}
    engine.state.entry_fees = {}
    engine.state.realized_pnl_accumulators = {}
    engine.state.pending_orders = ()
    engine.state.pending_target = {}
    engine.state.cash = engine._cash
    benchmark = tuple(bar for bar in ordered if bar.symbol == "BTC/USD")
    benchmark_return = benchmark[-1].close / benchmark[0].close - 1 if len(benchmark) > 1 else 0.0
    result = engine._result(curve, curve_timestamps, benchmark_return, benchmark)
    metric_names = (
        "starting_equity",
        "ending_equity",
        "total_return",
        "cagr",
        "annualized_volatility",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "turnover",
        "slippage_cost_total",
        "spread_cost_total",
        "fee_total",
        "rebalance_count",
    )
    payload: dict[str, Any] = {
        "schema": "crypto-canonical-causal-execution-v1",
        "contract_git_sha": "2b483b1e5022a23a0c34cc3ab15145da342c89d6",
        "source_sha256": EXPECTED_SOURCE_SHA,
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "universe_sha256": universe_hash,
        "terminal_at": terminal_at.isoformat(),
        "terminal_order_created_at": terminal_order_created_at.isoformat(),
        "final_observation_set": final_observation_set,
        "daily_bars": plain(daily),
        "decisions": decisions,
        "orders": orders,
        "fills": plain(result.fills),
        "price_events": price_events,
        "snapshots": snapshots,
        "curve_timestamps": plain(result.curve_timestamps),
        "equity_curve": plain(result.equity_curve),
        "final_state": plain(result.state),
        "terminal_events": plain(result.terminal_events),
        "metrics": {key: plain(getattr(result, key)) for key in metric_names},
        "counts": {
            "hourly": len(hourly),
            "daily": len(daily),
            "evaluation_daily": len(curve),
            "decisions": len(decisions),
            "orders": len(orders),
            "fills": len(result.fills),
        },
    }
    payload["hashes"] = {
        key: sha(payload[key])
        for key in (
            "daily_bars",
            "decisions",
            "orders",
            "fills",
            "price_events",
            "snapshots",
            "curve_timestamps",
            "equity_curve",
            "final_state",
            "terminal_events",
        )
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = run()
    full_sha = write_gzip(args.out, payload)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "full_payload_sha256": full_sha,
                "counts": payload["counts"],
                "metrics": payload["metrics"],
                "hashes": payload["hashes"],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
