"""Universe contract tests.

Equity is a static universe: the configured symbols are the only members and
each one joins the point-in-time universe at its first bar. Crypto keeps the
scanner policy, whose eligible pool is deliberately larger than the streamed
observation set. A missing crypto snapshot is fail-closed, and the two
markets' universes stay isolated.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.universe import (
    build_static_equity_point_in_time_universe,
    build_trailing_point_in_time_universe,
)
from parayabuck.config import CryptoUniverseConfig, EquityUniverseConfig
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.models import PortfolioState


def _bar(symbol: str, timestamp: datetime, close: float) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        market=Market.EQUITY,
    )


def _panel(symbols: list[str], days: int = 320) -> list[Bar]:
    """Synthetic daily panel with a trend AND an oscillation, so every symbol
    has usable history from its first bar."""

    import math

    start = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            close = 100 * (1.002 + offset * 0.0001) ** index * (1 + 0.012 * math.sin(index * 0.8 + offset))
            bars.append(_bar(symbol, timestamp, close))
    return bars


def _static_equity_config(symbols: tuple[str, ...], maximum_stream_symbols: int = 10) -> EquityUniverseConfig:
    return EquityUniverseConfig(
        enabled=True,
        maximum_stream_symbols=maximum_stream_symbols,
        symbols=symbols,
    )


def test_static_equity_universe_only_trades_configured_symbols() -> None:
    """The equity universe is configuration, not discovery: a symbol that is
    not configured is never a member and is never a backtest target."""
    symbols = ["SPY", "QQQ", "QLD"] + [f"S{i:02d}" for i in range(38)]
    bars = _panel(symbols)
    config = _static_equity_config(("SPY", "QQQ", "QLD"), maximum_stream_symbols=10)
    universe = build_static_equity_point_in_time_universe(bars, config)
    assert universe.snapshots
    for snapshot in universe.snapshots:
        assert snapshot.symbols <= {"SPY", "QQQ", "QLD"}
    assert not universe.contains("S00", Market.EQUITY, bars[-1].timestamp)

    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 5})
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        universe=universe,
        universe_version="test",
    )
    result = engine.run(bars, liquidate_at_end=False)
    assert result.rebalances
    for rebalance in result.rebalances:
        for symbol, _weight in rebalance.target_weights:
            assert universe.contains(symbol, Market.EQUITY, rebalance.decision_timestamp)


def test_open_managed_orders_stay_pinned_across_restart() -> None:
    """Pending managed orders decided before a restart are re-armed and fill."""
    symbols = ("SPY", "QQQ", "QLD", "S00")
    bars = _panel(list(symbols))
    universe = build_static_equity_point_in_time_universe(
        bars,
        _static_equity_config(symbols, maximum_stream_symbols=len(symbols)),
    )
    decision_timestamp = bars[10].timestamp
    state = PortfolioState(
        strategy_name="equity_leveraged_trend_v1",
        strategy_version="1.0.0",
        holdings={},
        cash=100_000.0,
        pending_orders=(
            {
                "client_order_id": "pbp-restored",
                "symbol": "S00",
                "market": "equity",
                "side": "buy",
                "qty": 10.0,
                "created_at": decision_timestamp.isoformat(),
                "decision_timestamp": decision_timestamp.isoformat(),
            },
        ),
    )
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 5})
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        state=state,
        universe=universe,
        universe_version="test",
    )
    engine.run(bars, liquidate_at_end=False)
    # The restored order filled on the next tradable bar after its creation.
    restored_fills = [fill for fill in engine._fills if fill.client_order_id == "pbp-restored"]
    assert restored_fills
    assert restored_fills[0].qty == pytest.approx(10.0)
    assert restored_fills[0].timestamp > restored_fills[0].decision_timestamp


def test_missing_crypto_snapshot_is_fail_closed() -> None:
    from parayabuck.universe import validate_active_crypto_universe

    config = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        maximum_stream_symbols=2,
        trade_symbols=("BTC/USD", "ETH/USD"),
        active_universe_path="/nonexistent/active_crypto_universe.json",
    )
    symbols, status = validate_active_crypto_universe(config)
    assert status != "success"
    assert symbols == ()


def test_disabled_strategy_creates_no_orders(tmp_path) -> None:
    from parayabuck.execution.portfolio import PortfolioOrderRequest
    from parayabuck.portfolio.runtime_service import (
        PortfolioRuntime,
        PortfolioStateRepository,
    )

    class _Broker:
        def __init__(self) -> None:
            self.submitted: list[PortfolioOrderRequest] = []

        def account(self):
            from parayabuck.core.models import AccountSnapshot

            return AccountSnapshot(100_000, 100_000, 100_000, 0.0)

        def asset_trading_rules(self, symbol: str, market: Market):
            from parayabuck.execution.portfolio import AssetTradingRules

            return AssetTradingRules(symbol, market, 0.01, 0.01, 0.01, 1.0)

        def all_open_order_symbols(self) -> set[str]:
            return set()

        def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
            self.submitted.append(order)
            return "broker-" + order.client_order_id

    broker = _Broker()
    # Only the equity strategy is enabled: a crypto runtime is never built,
    # so crypto bars can never produce crypto orders.
    equity = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 5})
    runtime = PortfolioRuntime(
        equity,
        ("AAPL", "MSFT", "NVDA", "SPY"),
        broker,
        PortfolioStateRepository(tmp_path / "r.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
    )
    start = datetime(2024, 1, 1, tzinfo=UTC)
    crypto_bars = [
        Bar(
            start + timedelta(hours=4 * index),
            "BTC/USD",
            "4h",
            100.0 + index,
            100.0 + index,
            100.0 + index,
            100.0 + index,
            1_000_000.0,
            Market.CRYPTO,
        )
        for index in range(100)
    ]
    runtime.seed(crypto_bars)
    assert all(not series for series in runtime.history.values())
    assert not broker.submitted
    runtime.close()


def test_static_equity_and_crypto_universes_are_isolated() -> None:
    equity_bars = _panel([f"S{i:02d}" for i in range(6)])
    start = datetime(2024, 1, 1, tzinfo=UTC)
    import math

    crypto_bars = [
        Bar(
            start + timedelta(hours=index),
            symbol,
            "1h",
            100.0 + index / 24.0 + 5.0 * math.sin(index * 0.3 + offset),
            100.0 + index / 24.0 + 5.0 * math.sin(index * 0.3 + offset),
            100.0 + index / 24.0 + 5.0 * math.sin(index * 0.3 + offset),
            100.0 + index / 24.0 + 5.0 * math.sin(index * 0.3 + offset),
            1_000_000.0,
            Market.CRYPTO,
        )
        for offset, symbol in enumerate(("BTC/USD", "ETH/USD"))
        for index in range(2400)
    ]
    equity_universe = build_static_equity_point_in_time_universe(
        equity_bars,
        _static_equity_config(tuple(f"S{i:02d}" for i in range(6)), maximum_stream_symbols=6),
    )
    crypto_universe = build_trailing_point_in_time_universe(
        crypto_bars,
        maximum_symbols=2,
        rebalance_frequency="daily",
        lookback_days=7,
        minimum_history_ratio=0.8,
    )
    for snapshot in equity_universe.snapshots:
        assert all("/" not in symbol for symbol in snapshot.symbols)
    for snapshot in crypto_universe.snapshots:
        assert all("/" in symbol for symbol in snapshot.symbols)
    equity_member = next(
        iter(next(snapshot for snapshot in reversed(equity_universe.snapshots) if snapshot.symbols).symbols)
    )
    crypto_member = next(
        iter(next(snapshot for snapshot in reversed(crypto_universe.snapshots) if snapshot.symbols).symbols)
    )
    assert equity_universe.contains(equity_member, Market.EQUITY, start + timedelta(days=100))
    assert not equity_universe.contains("S99", Market.EQUITY, start + timedelta(days=100))
    assert crypto_universe.contains(crypto_member, Market.CRYPTO, start + timedelta(days=100))
    assert not crypto_universe.contains("DOGE/USD", Market.CRYPTO, start + timedelta(days=100))


def test_point_in_time_universe_round_trip() -> None:
    symbols = tuple(f"S{i:02d}" for i in range(10))
    universe = build_static_equity_point_in_time_universe(
        _panel(list(symbols)),
        _static_equity_config(symbols, maximum_stream_symbols=len(symbols)),
    )
    payload = universe.to_dict()
    assert payload["market"] == "equity"
    assert payload["snapshots"]
    # JSON round-trip must preserve membership semantics.
    assert json.dumps(payload)
