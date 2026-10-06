"""Timeframe-ownership contract tests.

The strategy owns its signal timeframe. There is no global strategy
timeframe: scanner, signal, rebalance, execution and reconciliation cadences
are independent responsibilities, and a strategy MUST NOT run on a timeframe
it does not validate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.config import Config, RuntimeConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.data.aggregator import TimeframeAggregator
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

EQUITY_SYMBOLS = ("AAPL", "MSFT", "NVDA", "SPY", "QQQ", "QLD")


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
                    Market.EQUITY,
                    100 * (1.001 + offset * 0.0001) ** index,
                )
            )
    return bars


def _equity_4h_series(symbols: tuple[str, ...], count: int) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(count):
        timestamp = start + timedelta(hours=4 * index)
        for offset, symbol in enumerate(symbols):
            bars.append(
                _bar(
                    symbol,
                    timestamp,
                    "4h",
                    Market.EQUITY,
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
        portfolio_strategy_from("equity_leveraged_trend_v1", timeframe="5m")
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1")
    with pytest.raises(ValueError, match="not supported"):
        validate_strategy_timeframe(strategy, "1h")
    with pytest.raises(ValueError, match="not supported"):
        validate_strategy_timeframe(strategy, "4h")
    # The configured value must equal a validated variant, never be
    # reinterpreted: 1d is the single supported equity timeframe.
    validate_strategy_timeframe(strategy, "1d")


def test_spec_declares_owned_timeframes_and_cadence() -> None:
    spec = portfolio_strategy_from("equity_leveraged_trend_v1").spec()
    assert spec.primary_timeframe == "1d"
    assert spec.supported_timeframes == ("1d",)
    assert spec.rebalance_policy == "hours:24"
    assert spec.market == Market.EQUITY
    assert spec.minimum_history_bars == 200


def test_required_timeframe_aggregation_is_initialized() -> None:
    aggregator = TimeframeAggregator("1h")
    assert aggregator.timeframe == "1h"
    assert aggregator.minutes == 60


def test_execution_timeframe_remains_independent() -> None:
    # A daily signal may still execute on 5m; the runtime
    # config keeps the execution cadence separate from strategy timeframes.
    config = Config(runtime=RuntimeConfig(execution_timeframe="5m", market_state_timeframe="5m"))
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1")
    assert config.runtime.execution_timeframe == "5m"
    assert strategy.timeframe == "1d"
    assert strategy.spec().rebalance_policy == "hours:24"
    # The runtime aggregates BOTH the strategy timeframe and the execution
    # timeframe (aggregator map is built from the union of required cadences).
    required = {
        config.runtime.execution_timeframe,
        config.runtime.market_state_timeframe,
        strategy.timeframe,
    }
    assert required == {"5m", "1d"}


def test_paper_runtime_uses_same_strategy_timeframe(tmp_path) -> None:
    broker = _Broker()
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 5})
    runtime = PortfolioRuntime(
        strategy,
        EQUITY_SYMBOLS,
        broker,
        PortfolioStateRepository(tmp_path / "r.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
    )
    # Seeding non-strategy-timeframe bars must be ignored.
    runtime.seed(_equity_4h_series(EQUITY_SYMBOLS, count=10))
    assert all(not series for series in runtime.history.values())
    runtime.seed(_daily_series(EQUITY_SYMBOLS, days=30))
    assert all(len(series) == 30 for series in runtime.history.values())
    runtime.close()


def test_strategy_warmup_uses_required_history() -> None:
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1")
    assert strategy.minimum_history_bars == 200
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
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1")
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

    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 5})
    # The single-symbol target needs the shipped equity risk budget; a 0.93
    # cap leaves the cash headroom the per-asset limit is meant to reserve.
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        risk_limits=PortfolioRiskLimits(
            daily_loss_limit=0.20,
            drawdown_halt=0.75,
            maximum_gross=0.98,
            maximum_equity=0.98,
            maximum_equity_asset=0.93,
            minimum_cash=0.02,
            maximum_positions=8,
        ),
        allow_static_universe=True,
    )
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
