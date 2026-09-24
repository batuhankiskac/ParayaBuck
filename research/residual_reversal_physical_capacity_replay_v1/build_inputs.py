"""Pinned canonical inputs for a no-return physical shared-account replay."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from parayabuck.backtest.lifetimes import load_entity_lifetimes
from parayabuck.backtest.numeric_history import build_numeric_history
from parayabuck.backtest.product import (
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import aggregate_historical_bars
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.base import PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3

from .candidate import frozen_reversal_parameters
from .composite_equity import CompositeEquity
from .physical_scheduler import MarketReplayInput, SharedAccountReplay, _SharedEngine
from .recording_candidate import RecordingReversal
from .timing import equity_action_effective_open, equity_daily_available_at, equity_session_open

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("PARAYABUCK_CAPACITY_DATA_ROOT", ROOT / "data/historical"))
EQUITY_BARS = DATA_ROOT / "equity_pit_symmetric_2016_2026_v5.csv"
EQUITY_LIFETIMES = DATA_ROOT / "equity_entity_lifetimes_full_v5.csv"
EQUITY_ACTIONS = DATA_ROOT / "equity_survivorship_full_corporate_actions_v5.csv"
CRYPTO_HOURLY = DATA_ROOT / "crypto_expanded_1h.csv"
EQUITY_START = datetime(2017, 1, 3, tzinfo=UTC)
EQUITY_END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)
CRYPTO_START = datetime(2021, 4, 1, tzinfo=UTC)
CRYPTO_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
CRYPTO_TERMINAL = datetime(2026, 7, 31, tzinfo=UTC)
EXPECTED_SHA = {
    EQUITY_BARS: "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    EQUITY_LIFETIMES: "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    EQUITY_ACTIONS: "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
    CRYPTO_HOURLY: "73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e",
}


def checked_source(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != EXPECTED_SHA[path]:
        raise ValueError(f"Pinned canonical source changed: {path.name}")
    return actual


def frozen_config(cap: int) -> Config:
    if cap not in (6, 8):
        raise ValueError("Only frozen caps 6 and 8 are permitted")
    config = load_config(str(ROOT / "config/settings.yaml"))
    if config.live.enabled or config.strategy.equity.name != "equity_residual_momentum_v3":
        raise ValueError("Production authority changed")
    if config.strategy.crypto.name != "crypto_low_variance_hysteresis_v1":
        raise ValueError("Crypto authority changed")
    return replace(config, risk=replace(config.risk, max_positions=cap))


def equity_strategy(config: Config, kind: str) -> PortfolioStrategy:
    v3 = cast(
        EquityResidualMomentum1dV3,
        portfolio_strategy_from(
            config.strategy.equity.name,
            config.strategy.equity.parameters,
            timeframe=config.strategy.equity.timeframe,
        ),
    )
    if kind == "incumbent":
        return v3
    reversal = RecordingReversal(frozen_reversal_parameters())
    if kind == "isolated_reversal":
        return reversal
    if kind == "treatment":
        return CompositeEquity(v3, reversal)
    raise ValueError(f"Unknown equity replay kind: {kind}")


def _engine(
    replay: SharedAccountReplay,
    config: Config,
    strategy: PortfolioStrategy,
    universe: Any,
    universe_version: str,
    *,
    terminal_lifecycles: Any = None,
    maximum_allocation: float,
) -> _SharedEngine:
    return _SharedEngine(
        replay.account,
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps,
        spread_bps=config.backtest.spread_bps,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        equity_regulatory_fee_multiplier=1.0,
        benchmark_symbol="SPY" if strategy.market == Market.EQUITY else "BTC/USD",
        universe=universe,
        universe_version=universe_version,
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=maximum_allocation,
        ordinary_alpha_excluded_symbols=(
            ()
            if isinstance(strategy, RecordingReversal)
            else config.universe.equity.ordinary_alpha_excluded_symbols
            if strategy.market == Market.EQUITY
            else ()
        ),
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        enforce_drawdown_halt=False,
        terminal_lifecycles=terminal_lifecycles,
    )


def _opening_event(source: Bar, at: datetime) -> Bar:
    return Bar(at, source.symbol, "1d", source.open, source.open, source.open, source.open, 0.0, source.market)


def equity_input(config: Config, replay: SharedAccountReplay, kind: str) -> tuple[MarketReplayInput, dict[str, object]]:
    for path in (EQUITY_BARS, EQUITY_LIFETIMES, EQUITY_ACTIONS):
        checked_source(path)
    bars = load_csv_bars(EQUITY_BARS, default_market=Market.EQUITY)
    lifetimes = load_entity_lifetimes(EQUITY_LIFETIMES)
    actions = load_csv_corporate_actions(EQUITY_ACTIONS)
    strategy = equity_strategy(config, kind)
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=EQUITY_END,
        timeframe=strategy.timeframe,
        observation_strategy=strategy,
    )
    _filtered, warmup, (ordered, decision_bars) = _prepared_window(
        bars, Market.EQUITY, strategy.timeframe, EQUITY_START, EQUITY_END
    )
    version = hash_point_in_time_universe(universe)
    engine = _engine(
        replay,
        config,
        strategy,
        universe,
        version,
        terminal_lifecycles=lifetimes.engine_terminal_lifecycles(),
        maximum_allocation=0.55,
    )
    engine._numeric_history = build_numeric_history(warmup + ordered, market=Market.EQUITY, timeframe="1d")
    labels = {bar.timestamp for bar in ordered}
    available = {label: equity_daily_available_at(label.date()) for label in labels}
    execution = {label: equity_session_open(label.date()) for label in labels}
    action_available = {action.timestamp: equity_action_effective_open(action.timestamp.date()) for action in actions}
    return (
        MarketReplayInput(
            engine,
            ordered,
            warmup,
            decision_bars,
            actions,
            available,
            action_available,
            final_close_at=max(available.values()),
            execution_schedule=execution,
        ),
        {
            "source_sha256": EXPECTED_SHA[EQUITY_BARS],
            "universe_sha256": version,
            "first_label": min(labels).isoformat(),
            "last_label": max(labels).isoformat(),
        },
    )


def crypto_input(config: Config, replay: SharedAccountReplay) -> tuple[MarketReplayInput, dict[str, object]]:
    checked_source(CRYPTO_HOURLY)
    hourly = load_csv_bars(CRYPTO_HOURLY, default_market=Market.CRYPTO)
    daily = aggregate_historical_bars(hourly, "1d", require_complete=True)
    universe = build_product_universe(config, hourly, market=Market.CRYPTO, end=CRYPTO_END, timeframe="1h")
    strategy = portfolio_strategy_from(
        config.strategy.crypto.name,
        config.strategy.crypto.parameters,
        timeframe=config.strategy.crypto.timeframe,
    )
    _filtered, warmup, (ordered, _decision_bars) = _prepared_window(
        daily, Market.CRYPTO, strategy.timeframe, CRYPTO_START, CRYPTO_END
    )
    version = hash_point_in_time_universe(universe)
    engine = _engine(replay, config, strategy, universe, version, maximum_allocation=0.05)
    engine._numeric_history = build_numeric_history(warmup + ordered, market=Market.CRYPTO, timeframe="1d")
    available = {
        bar.timestamp: bar.timestamp + timedelta(days=1)
        for bar in ordered
        if bar.timestamp + timedelta(days=1) <= CRYPTO_TERMINAL
    }
    selected = [bar for bar in ordered if bar.timestamp in available]
    by_day: dict[tuple[str, datetime], list[Bar]] = defaultdict(list)
    for bar in hourly:
        day = bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if CRYPTO_START < bar.timestamp <= CRYPTO_TERMINAL:
            by_day[(bar.symbol, day)].append(bar)
    execution: list[Bar] = []
    for (_symbol, day), rows in by_day.items():
        if day == CRYPTO_TERMINAL:
            execution.extend(_opening_event(bar, bar.timestamp) for bar in rows if bar.timestamp == CRYPTO_TERMINAL)
        else:
            execution.extend(_opening_event(bar, bar.timestamp) for bar in rows if bar.timestamp > day)
    return (
        MarketReplayInput(engine, execution, warmup, selected, [], available, {}, CRYPTO_TERMINAL),
        {
            "source_sha256": EXPECTED_SHA[CRYPTO_HOURLY],
            "universe_sha256": version,
            "first_label": min(available).isoformat(),
            "last_label": max(available).isoformat(),
        },
    )


def build_replay(cap: int, kind: str) -> tuple[SharedAccountReplay, dict[str, object]]:
    config = frozen_config(cap)
    replay = SharedAccountReplay(starting_nav=100000.0, maximum_positions=cap, allow_test_strategies=True)
    identities: dict[str, object] = {}
    if kind != "isolated_reversal":
        crypto, identity = crypto_input(config, replay)
        replay.add_market(crypto)
        identities["crypto"] = identity
    equity, identity = equity_input(config, replay, kind)
    replay.add_market(equity)
    identities["equity"] = identity
    return replay, identities
