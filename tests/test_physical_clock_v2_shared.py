"""Small physical-ordering checks against the shared account engine."""

from __future__ import annotations

from datetime import UTC, datetime

from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason, TargetPortfolio, TargetWeight
from research.physical_availability_clock_v2.shared_replay import MarketReplayInput, SharedAccountReplay, _SharedEngine


class Fixed(PortfolioStrategy):
    name = "physical_test"
    version = "1.0.0"
    timeframe = "1d"

    def __init__(self, market: Market, symbol: str):
        self.market = market
        self.symbol = symbol
        super().__init__()

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=(TargetWeight(self.symbol, 0.2),),
            cash_weight=0.8,
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
        )


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def bar(market: Market, symbol: str, when: str, price: float, volume: float = 1000.0) -> Bar:
    time = dt(when)
    return Bar(time, symbol, "1d", price, price, price, price, volume, market)


def engine(replay: SharedAccountReplay, market: Market, symbol: str) -> _SharedEngine:
    limits = PortfolioRiskLimits(
        daily_loss_limit=0.99,
        drawdown_halt=0.99,
        maximum_gross=0.8,
        maximum_equity=0.8,
        maximum_crypto=0.8,
        maximum_equity_asset=0.8,
        maximum_crypto_asset=0.8,
        minimum_cash=0.2,
        maximum_positions=6,
        maximum_crypto_positions=6,
    )
    return _SharedEngine(
        replay.account,
        Fixed(market, symbol),
        RebalancePolicy(market, "monthly" if market == Market.EQUITY else "hours:24"),
        starting_equity=1000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        commission=0.0,
        crypto_taker_fee_bps=0.0,
        maximum_allocation=0.8,
        risk_limits=limits,
        allow_static_universe=True,
        max_participation_pct=0.1,
        liquidity_lookback_bars=1,
        turnover_band=0.0,
        minimum_order_notional=0.0,
    )


def test_equity_complete_session_decides_after_close_and_fills_next_open() -> None:
    replay = SharedAccountReplay(starting_nav=1000.0, maximum_positions=6, allow_test_strategies=True)
    market, symbol = Market.EQUITY, "AAPL"
    first = bar(market, symbol, "2025-01-02T00:00:00Z", 100.0)
    second = bar(market, symbol, "2025-01-03T00:00:00Z", 101.0)
    replay.add_market(
        MarketReplayInput(
            engine(replay, market, symbol),
            [first, second],
            signal_bars=[first, second],
            signal_availability={
                first.timestamp: dt("2025-01-02T21:00:00Z"),
                second.timestamp: dt("2025-01-03T21:00:00Z"),
            },
            execution_schedule={
                first.timestamp: dt("2025-01-02T14:30:00Z"),
                second.timestamp: dt("2025-01-03T14:30:00Z"),
            },
        )
    )
    result = replay.run(liquidate_at_end=False)
    decisions = [event for event in result.events if event["event"] == "decision"]
    fills = [event for event in result.events if event["event"] == "fill"]
    assert decisions[0]["timestamp"] == "2025-01-02T21:00:00+00:00"
    assert fills[0]["timestamp"] == "2025-01-03T14:30:00+00:00"
    assert fills[0]["order_created_at"] == decisions[0]["timestamp"]


def test_crypto_same_midnight_open_excluded_then_first_later_source_open_used() -> None:
    replay = SharedAccountReplay(starting_nav=1000.0, maximum_positions=6, allow_test_strategies=True)
    market, symbol = Market.CRYPTO, "BTC/USD"
    daily = bar(market, symbol, "2021-04-01T00:00:00Z", 100.0)
    zero = bar(market, symbol, "2021-04-02T00:00:00Z", 101.0)
    one = bar(market, symbol, "2021-04-02T01:00:00Z", 102.0)
    two = bar(market, symbol, "2021-04-02T02:00:00Z", 103.0)
    replay.add_market(
        MarketReplayInput(
            engine(replay, market, symbol),
            [zero, one, two],
            signal_bars=[daily],
            signal_availability={daily.timestamp: zero.timestamp},
        )
    )
    result = replay.run(liquidate_at_end=False)
    fills = [event for event in result.events if event["event"] == "fill"]
    assert len(fills) == 1
    assert fills[0]["timestamp"] == "2021-04-02T01:00:00+00:00"
    assert fills[0]["reference_price"] == 102.0
    assert fills[0]["order_created_at"] == "2021-04-02T00:00:00+00:00"
