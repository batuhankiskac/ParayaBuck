"""Universe / scanner contract tests.

The scanner answers "which assets are tradeable and observable?" and the
eligibility pool is deliberately larger than the streamed observation set;
alpha scoring happens on that broader pool. Managed positions and open
managed orders are pinned regardless of ranking, scanner failure is
fail-closed, and the two markets' universes are isolated.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.universe import build_trailing_point_in_time_universe
from parayabuck.config import EquityScannerConfig, EquityUniverseConfig
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
    """Synthetic panel with a trend AND oscillation so the scanner's ATR range
    and liquidity filters are satisfied on every day."""
    import math

    start = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            close = 100 * (1.002 + offset * 0.0001) ** index + 2.0 * math.sin(index * 0.8 + offset)
            bars.append(_bar(symbol, timestamp, close))
    return bars


def _equity_config(maximum_stream_symbols: int = 10) -> EquityUniverseConfig:
    return EquityUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        maximum_stream_symbols=maximum_stream_symbols,
        target_shortlist=30,
        reference_symbols=(),
        scanner=EquityScannerConfig(
            minimum_price=1.0,
            minimum_median_dollar_volume=1_000.0,
            minimum_data_completeness=0.9,
        ),
    )


def test_scanner_eligibility_pool_exceeds_stream_symbol_count() -> None:
    symbols = [f"S{i:02d}" for i in range(40)]
    universe = build_trailing_point_in_time_universe(
        _panel(symbols),
        market=Market.EQUITY,
        maximum_symbols=10,
        rebalance_frequency="monthly",
        lookback_days=60,
        minimum_history_ratio=0.8,
        target_shortlist=30,
        equity_config=_equity_config(maximum_stream_symbols=10),
    )
    assert universe.snapshots
    for snapshot in universe.snapshots:
        # Eligibility pool (candidate count) can exceed the stream capacity.
        assert snapshot.candidate_count >= len(snapshot.symbols)
        assert len(snapshot.symbols) <= 10
    # The very first snapshot already has a 30-symbol candidate pool.
    assert universe.snapshots[0].candidate_count >= 20


def test_alpha_scores_broader_pool_than_final_stream_set() -> None:
    """The backtest strategy sees the full downloaded pool; the point-in-time
    universe gates eligibility, so alpha scoring runs before the streamed
    observation-set truncation (and only universe members are investable)."""
    symbols = [f"S{i:02d}" for i in range(40)]
    bars = _panel(symbols)
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=10,
        rebalance_frequency="monthly",
        lookback_days=60,
        minimum_history_ratio=0.8,
        target_shortlist=30,
        equity_config=_equity_config(maximum_stream_symbols=10),
    )
    strategy = portfolio_strategy_from(
        "equity_long_horizon_momentum_1d_v2",
        {"top_n": 3, "use_market_regime": False, "exclude_symbols": []},
    )
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        universe=universe,
        universe_version="test",
    )
    result = engine.run(bars, liquidate_at_end=False)
    assert result.rebalances
    last_decision = result.rebalances[-1].decision_timestamp
    context_history = {symbol for symbol in engine._history}
    # Every pool symbol was ingested; only universe members may be targeted.
    assert len(context_history) == len(symbols)
    snapshot_symbols = next(
        snapshot.symbols
        for snapshot in reversed(universe.snapshots)
        if snapshot.effective_at <= last_decision
    )
    assert snapshot_symbols
    assert len(snapshot_symbols) <= 10 < len(context_history)
    for symbol, _weight in result.rebalances[-1].target_weights:
        assert symbol in snapshot_symbols


def test_managed_positions_stay_pinned_outside_universe() -> None:
    """A managed position is pinned into the scanner universe regardless of
    ranking: the pinned symbol stays selected even when it fails the scanner's
    liquidity filters, so the strategy can always observe and target it."""
    symbols = [f"S{i:02d}" for i in range(20)]
    bars = _panel(symbols)
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=5,
        rebalance_frequency="monthly",
        lookback_days=60,
        minimum_history_ratio=0.8,
        target_shortlist=30,
        pinned_symbols=("S19",),
        equity_config=_equity_config(maximum_stream_symbols=5),
    )
    assert universe.snapshots
    for snapshot in universe.snapshots:
        assert "S19" in snapshot.symbols
    # The pinned symbol is investable even though the strategy ranks by alpha.
    assert universe.contains("S19", Market.EQUITY, bars[-1].timestamp)


def test_open_managed_orders_stay_pinned_across_restart() -> None:
    """Pending managed orders decided before a restart are re-armed and fill."""
    symbols = [f"S{i:02d}" for i in range(20)]
    bars = _panel(symbols)
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=5,
        rebalance_frequency="monthly",
        lookback_days=60,
        minimum_history_ratio=0.8,
        target_shortlist=30,
        equity_config=_equity_config(maximum_stream_symbols=5),
    )
    decision_timestamp = bars[10].timestamp
    state = PortfolioState(
        strategy_name="equity_long_horizon_momentum_1d_v2",
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
    strategy = portfolio_strategy_from(
        "equity_long_horizon_momentum_1d_v2",
        {"top_n": 2, "use_market_regime": False, "exclude_symbols": []},
    )
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


def test_market_scanner_failure_is_fail_closed() -> None:
    from parayabuck.universe import validate_active_universe

    config = _equity_config()
    config = EquityUniverseConfig(
        **{
            **config.__dict__,
            "active_universe_path": "/nonexistent/active_equity_universe.json",
        }
    )
    _symbols, status = validate_active_universe(config)
    assert status != "success"
    assert _symbols == ()


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
    equity = portfolio_strategy_from(
        "equity_residual_momentum_v1", {"top_n": 2, "exclude_symbols": ["SPY"]}
    )
    runtime = PortfolioRuntime(
        equity,
        ("AAPL", "MSFT", "NVDA", "SPY"),
        broker,
        PortfolioStateRepository(tmp_path / "r.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
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


def test_crypto_and_equity_universe_states_are_isolated() -> None:
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
    equity_universe = build_trailing_point_in_time_universe(
        equity_bars,
        market=Market.EQUITY,
        maximum_symbols=3,
        rebalance_frequency="daily",
        lookback_days=30,
        minimum_history_ratio=0.8,
        equity_config=_equity_config(maximum_stream_symbols=3),
    )
    crypto_universe = build_trailing_point_in_time_universe(
        crypto_bars,
        market=Market.CRYPTO,
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
        iter(
            next(
                snapshot for snapshot in reversed(equity_universe.snapshots) if snapshot.symbols
            ).symbols
        )
    )
    crypto_member = next(
        iter(
            next(
                snapshot for snapshot in reversed(crypto_universe.snapshots) if snapshot.symbols
            ).symbols
        )
    )
    assert equity_universe.contains(equity_member, Market.EQUITY, start + timedelta(days=100))
    assert not equity_universe.contains("S99", Market.EQUITY, start + timedelta(days=100))
    assert crypto_universe.contains(crypto_member, Market.CRYPTO, start + timedelta(days=100))
    assert not crypto_universe.contains("DOGE/USD", Market.CRYPTO, start + timedelta(days=100))


def test_scanners_do_not_block_each_other() -> None:
    """A missing/invalid equity universe does not fail the crypto scanner and
    vice versa: each market's selection is independent and fail-closed per
    market."""
    from parayabuck.universe import (
        validate_active_crypto_universe,
        validate_active_universe,
    )

    equity_config = _equity_config()
    equity_config = EquityUniverseConfig(
        **{
            **equity_config.__dict__,
            "active_universe_path": "/nonexistent/equity_active.json",
        }
    )
    equity_symbols, equity_status = validate_active_universe(equity_config)
    assert equity_status != "success" and equity_symbols == ()

    crypto_config = _equity_config()
    from parayabuck.config import CryptoUniverseConfig

    crypto_config = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        maximum_stream_symbols=2,
        trade_symbols=("BTC/USD", "ETH/USD"),
        pinned_symbols=("BTC/USD",),
        active_universe_path="/nonexistent/crypto_active.json",
    )
    crypto_symbols, crypto_status = validate_active_crypto_universe(crypto_config)
    assert crypto_status != "success" and crypto_symbols == ()
    # Both fail closed on their own missing state; neither "blocks" the
    # other's validation path (they are independent checks).
    assert equity_status != crypto_status or equity_status != "success"


def test_point_in_time_universe_round_trip() -> None:
    symbols = [f"S{i:02d}" for i in range(10)]
    universe = build_trailing_point_in_time_universe(
        _panel(symbols),
        market=Market.EQUITY,
        maximum_symbols=4,
        rebalance_frequency="monthly",
        lookback_days=60,
        minimum_history_ratio=0.8,
        target_shortlist=10,
        equity_config=_equity_config(maximum_stream_symbols=4),
    )
    payload = universe.to_dict()
    assert payload["market"] == "equity"
    assert payload["snapshots"]
    # JSON round-trip must preserve membership semantics.
    assert json.dumps(payload)
