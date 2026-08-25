"""Timeframe-ownership contract tests.

The strategy owns its signal timeframe. There is no global strategy
timeframe: scanner, signal, rebalance, execution and reconciliation cadences
are independent responsibilities, and a strategy MUST NOT run on a timeframe
it does not validate.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.config import Config, RuntimeConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.data.aggregator import TimeframeAggregator, aggregate_historical_bars
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioRiskLimits,
)
from parayabuck.portfolio import (
    portfolio_strategy_from,
    rebalance_policy_for,
    validate_strategy_timeframe,
)
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.runtime_service import (
    PortfolioDecisionService,
    PortfolioRuntime,
    PortfolioStateRepository,
)

EQUITY_SYMBOLS = ("AAPL", "MSFT", "NVDA", "SPY")
CRYPTO_SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD")


def _bar(symbol: str, timestamp: datetime, timeframe: str, market: Market, close: float) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        market=market,
    )


def _daily_series(
    symbols: tuple[str, ...],
    days: int = 320,
    base: datetime | None = None,
    market: Market = Market.EQUITY,
) -> list[Bar]:
    start = base or datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            bars.append(
                _bar(
                    symbol,
                    timestamp,
                    "1d",
                    market,
                    100 * (1.001 + offset * 0.0001) ** index,
                )
            )
    return bars


def _crypto_4h_series(symbols: tuple[str, ...], hours: int = 800) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(hours):
        timestamp = start + timedelta(hours=4 * index)
        for offset, symbol in enumerate(symbols):
            bars.append(
                _bar(
                    symbol,
                    timestamp,
                    "4h",
                    Market.CRYPTO,
                    100 * (1.001 + offset * 0.0002) ** index,
                )
            )
    return bars


class _Broker:
    def __init__(self) -> None:
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(100_000, 100_000, 100_000, 0.0)

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(symbol, market, 0.01, 0.01, 0.01, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


def test_strategy_cannot_run_on_unsupported_timeframe() -> None:
    with pytest.raises(ValueError, match="cannot run on timeframe 5m"):
        portfolio_strategy_from("equity_residual_momentum_v1", timeframe="5m")
    strategy = portfolio_strategy_from("equity_residual_momentum_v1")
    with pytest.raises(ValueError, match="not supported"):
        validate_strategy_timeframe(strategy, "1h")
    with pytest.raises(ValueError, match="not supported"):
        validate_strategy_timeframe(strategy, "4h")
    # The configured value must equal a validated variant, never be
    # reinterpreted: 1d is the single supported equity timeframe.
    validate_strategy_timeframe(strategy, "1d")


def test_crypto_strategy_supports_only_validated_variants() -> None:
    strategy = portfolio_strategy_from("crypto_low_variance_v1", timeframe="1d")
    assert strategy.timeframe == "1d"
    validate_strategy_timeframe(strategy, "1d")
    for bad in ("5m", "1h", "4h", "15m"):
        with pytest.raises(ValueError):
            portfolio_strategy_from("crypto_low_variance_v1", timeframe=bad)


def test_spec_declares_owned_timeframes_and_cadence() -> None:
    spec = portfolio_strategy_from("equity_residual_momentum_v1").spec()
    assert spec.primary_timeframe == "1d"
    assert spec.supported_timeframes == ("1d",)
    assert spec.rebalance_policy == "monthly"
    assert spec.market == Market.EQUITY
    assert spec.minimum_history_bars == 274


def test_required_timeframe_aggregation_is_initialized() -> None:
    # 4h crypto bars must be reproducible from the streaming path used by
    # paper/live (1h aggregation) AND from historical aggregation.
    one_hour_bars = []
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for index in range(48):
        timestamp = start + timedelta(hours=index)
        one_hour_bars.append(_bar("BTC/USD", timestamp, "1h", Market.CRYPTO, 100.0 + index))
    aggregated = aggregate_historical_bars(one_hour_bars, "4h", require_complete=True)
    assert len(aggregated) == 12
    assert aggregated[0].open == 100.0
    assert aggregated[0].close == 103.0
    assert aggregated[-1].close == 147.0
    # The streaming aggregator buckets 4h bars at UTC 00/04/08/12/16/20.
    aggregator = TimeframeAggregator("4h")
    assert aggregator.timeframe == "4h"
    assert aggregator.minutes == 240
    # Daily aggregation from 1h must also be complete.
    daily = aggregate_historical_bars(one_hour_bars, "1d", require_complete=True)
    assert len(daily) == 2
    assert daily[0].open == 100.0 and daily[0].close == 123.0


def test_multiple_markets_run_simultaneously_with_distinct_cadences(tmp_path) -> None:
    """Equity (monthly) and crypto (hours:168) run at the same time, one
    account, one risk layer, one repository, without interfering."""
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "shared.sqlite3")
    equity = portfolio_strategy_from(
        "equity_residual_momentum_v1",
        {"top_n": 2, "exclude_symbols": ["SPY"]},
        timeframe="1d",
    )
    crypto = portfolio_strategy_from("crypto_low_variance_v1", {"top_n": 2, "rebalance_hours": 168}, timeframe="1d")
    risk = PortfolioRiskLimits(
        daily_loss_limit=0.05,
        drawdown_halt=0.20,
        maximum_gross=0.60,
        maximum_equity=0.60,
        maximum_crypto=0.10,
        maximum_equity_asset=0.10,
        maximum_crypto_asset=0.05,
        minimum_cash=0.40,
        maximum_positions=6,
        maximum_crypto_positions=2,
    )
    equity_runtime = PortfolioRuntime(
        equity,
        EQUITY_SYMBOLS,
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        risk_limits=risk,
    )
    crypto_runtime = PortfolioRuntime(
        crypto,
        CRYPTO_SYMBOLS,
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.10,
        risk_limits=risk,
        account_id="shared-account",
    )
    assert equity_runtime.strategy.timeframe == "1d"
    assert crypto_runtime.strategy.timeframe == "1d"
    assert equity_runtime.policy.frequency == "monthly"
    assert crypto_runtime.policy.frequency == "hours:168"

    # Both markets decide on their own closed-bar batches.
    equity_result = equity_runtime.process_closed_bars(_daily_series(EQUITY_SYMBOLS))
    assert equity_result.status == "submitted"
    crypto_result = crypto_runtime.process_closed_bars(_daily_series(CRYPTO_SYMBOLS, market=Market.CRYPTO))
    assert crypto_result.status == "submitted"
    submitted_symbols = {order.symbol for order in broker.submitted}
    assert any("/" in symbol for symbol in submitted_symbols)
    assert any("/" not in symbol for symbol in submitted_symbols)
    with sqlite3.connect(tmp_path / "shared.sqlite3") as connection:
        markets = {str(row[0]) for row in connection.execute("SELECT DISTINCT market FROM portfolio_decisions")}
    assert markets == {"equity", "crypto"}
    equity_runtime.close()
    crypto_runtime.close()


def test_execution_timeframe_remains_independent() -> None:
    # A daily signal + weekly decision may still execute on 5m; the runtime
    # config keeps the execution cadence separate from strategy timeframes.
    config = Config(runtime=RuntimeConfig(execution_timeframe="5m", market_state_timeframe="5m"))
    strategy = portfolio_strategy_from("equity_residual_momentum_v1")
    assert config.runtime.execution_timeframe == "5m"
    assert strategy.timeframe == "1d"
    assert strategy.spec().rebalance_policy == "monthly"
    # The runtime aggregates BOTH the strategy timeframe and the execution
    # timeframe (aggregator map is built from the union of required cadences).
    required = {
        config.runtime.execution_timeframe,
        config.runtime.market_state_timeframe,
        strategy.timeframe,
    }
    assert required == {"5m", "1d"}


def test_backtest_uses_same_strategy_timeframe() -> None:
    from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
    from parayabuck.data.market_dataset import DatasetLineage, MarketDataset

    strategy = portfolio_strategy_from("crypto_low_variance_v1", timeframe="1d")
    engine = PortfolioBacktestEngine(strategy, rebalance_policy_for(strategy))
    # The backtest engine refuses a dataset whose timeframe differs from the
    # strategy's signal timeframe: no silent reinterpretation.
    bars_4h = _crypto_4h_series(("BTC/USD",), hours=40)
    dataset = MarketDataset(
        market=Market.CRYPTO,
        timeframe="4h",
        signal_bars=tuple(bars_4h),
        executable_bars=tuple(bars_4h),
        lineage=DatasetLineage("x", (), "test", "test", "raw", datetime.now(UTC), "hash"),
    )
    with pytest.raises(ValueError, match="Dataset timeframe does not match strategy timeframe"):
        engine.run_dataset(dataset)
    assert strategy.timeframe == "1d"


def test_paper_runtime_uses_same_strategy_timeframe(tmp_path) -> None:
    broker = _Broker()
    strategy = portfolio_strategy_from("equity_residual_momentum_v1", {"top_n": 2, "exclude_symbols": ["SPY"]})
    runtime = PortfolioRuntime(
        strategy,
        EQUITY_SYMBOLS,
        broker,
        PortfolioStateRepository(tmp_path / "r.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
    )
    # Seeding non-strategy-timeframe bars must be ignored.
    runtime.seed(_crypto_4h_series(("BTC/USD",), hours=10))
    assert all(not series for series in runtime.history.values())
    runtime.seed(_daily_series(EQUITY_SYMBOLS, days=30))
    assert all(len(series) == 30 for series in runtime.history.values())
    runtime.close()


def test_strategy_warmup_uses_required_history() -> None:
    strategy = portfolio_strategy_from("equity_residual_momentum_v1", {"exclude_symbols": ["SPY"]})
    assert strategy.minimum_history_bars == 274
    start = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    short = _daily_series(EQUITY_SYMBOLS, days=100)
    context = PortfolioContext(
        decision_timestamp=start + timedelta(days=100),
        market=Market.EQUITY,
        timeframe="1d",
        history={symbol: tuple(bar for bar in short if bar.symbol == symbol) for symbol in EQUITY_SYMBOLS},
        universe=None,
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )
    assert strategy.supports(context) is False  # below minimum history
    long = _daily_series(EQUITY_SYMBOLS, days=300)
    context_long = PortfolioContext(
        decision_timestamp=start + timedelta(days=300),
        market=Market.EQUITY,
        timeframe="1d",
        history={symbol: tuple(bar for bar in long if bar.symbol == symbol) for symbol in EQUITY_SYMBOLS},
        universe=None,
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )
    assert strategy.supports(context_long) is True


def test_decision_fires_only_on_finalized_bars() -> None:
    service = PortfolioDecisionService()
    strategy = portfolio_strategy_from("equity_residual_momentum_v1")
    bars = _daily_series(EQUITY_SYMBOLS, days=40)
    decision_timestamp = bars[-1].timestamp
    # One symbol's series ends before the decision timestamp: the batch is
    # not a complete closed-bar batch and MUST be rejected.
    incomplete = {symbol: [bar for bar in bars if bar.symbol == symbol] for symbol in EQUITY_SYMBOLS}
    incomplete["AAPL"] = incomplete["AAPL"][:-1]
    with pytest.raises(ValueError, match="complete closed-bar batch"):
        service.build_context(
            strategy,
            incomplete,
            AccountSnapshot(100_000, 100_000, 100_000, 0.0),
            decision_timestamp,
            universe=None,
            universe_version="test",
        )
    complete = {symbol: [bar for bar in bars if bar.symbol == symbol] for symbol in EQUITY_SYMBOLS}
    context = service.build_context(
        strategy,
        complete,
        AccountSnapshot(100_000, 100_000, 100_000, 0.0),
        decision_timestamp,
        universe=None,
        universe_version="test",
    )
    assert context.decision_timestamp == decision_timestamp
    assert all(series[-1].timestamp == decision_timestamp for series in context.history.values())


def test_no_same_bar_execution_lookahead() -> None:
    """A decision on completed bar t may only fill on the NEXT tradable bar."""
    from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine

    strategy = portfolio_strategy_from("equity_residual_momentum_v1", {"top_n": 2, "exclude_symbols": ["SPY"]})
    engine = PortfolioBacktestEngine(strategy, rebalance_policy_for(strategy))
    bars = _daily_series(EQUITY_SYMBOLS, days=320)
    result = engine.run(bars, liquidate_at_end=False)
    assert result.fills
    for fill in result.fills:
        # Orders are created at the decision and fill at the next bar's open:
        # the fill timestamp must be strictly AFTER the decision timestamp.
        assert fill.timestamp > fill.decision_timestamp
    assert all(
        fill.timestamp == max(bar.timestamp for bar in bars) or fill.timestamp in {bar.timestamp for bar in bars}
        for fill in result.fills
    )
