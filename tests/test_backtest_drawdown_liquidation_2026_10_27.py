"""The backtester carries the drawdown liquidation brake like the runtime does.

Disabled (the shipped default) the engine runs exactly as before; enabled and
enforced, a drawdown past the threshold sells the whole position and the
latched state keeps it in cash while the cooldown runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.config import DrawdownLiquidationConfig
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)  # Monday 09:30 New York


def _weekdays(count: int) -> list[datetime]:
    days: list[datetime] = []
    moment = START
    while len(days) < count:
        if moment.weekday() < 5:
            days.append(moment)
        moment += timedelta(days=1)
    return days


def _bars(closes: list[float]) -> list[Bar]:
    bars: list[Bar] = []
    for moment, close in zip(_weekdays(len(closes)), closes, strict=True):
        bars.append(Bar(moment, "QLD", "1d", close, close * 1.001, close * 0.999, close, 5_000_000.0, Market.EQUITY))
        bars.append(Bar(moment, "SPY", "1d", 400.0, 400.4, 399.6, 400.0, 5_000_000.0, Market.EQUITY))
    return bars


class _Hold(PortfolioStrategy):
    name = "hold_test"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(TargetWeight("QLD", 0.9),),
            cash_weight=0.1,
            universe_version="test",
        )


LIMITS = PortfolioRiskLimits(
    daily_loss_limit=0.90,
    drawdown_halt=0.95,
    maximum_gross=0.98,
    maximum_equity=0.98,
    maximum_equity_asset=0.98,
    minimum_cash=0.02,
    maximum_positions=8,
)

# QLD falls ~12% a session for six sessions, then recovers slowly.
CLOSES = [100.0, 88.0, 77.4, 68.1, 60.0, 52.8, 46.4, 47.0, 47.5, 48.0, 48.5, 49.0]


def _run(brake: DrawdownLiquidationConfig | None):  # type: ignore[no-untyped-def]
    engine = PortfolioBacktestEngine(
        _Hold(),
        RebalancePolicy(Market.EQUITY, "hours:24"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=2.0,
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        turnover_band=0.005,
        minimum_order_notional=10.0,
        risk_limits=LIMITS,
        enforce_drawdown_halt=True,
        drawdown_liquidation=brake,
    )
    return engine, engine.run(_bars(CLOSES), liquidate_at_end=False)


def _held(result) -> float:  # type: ignore[no-untyped-def]
    signed = {"buy": 1.0, "sell": -1.0}
    return round(sum(signed[fill.side.value.lower()] * fill.qty for fill in result.fills), 9)


def test_disabled_brake_reproduces_the_engine_without_it() -> None:
    _, baseline = _run(None)
    _, disabled = _run(DrawdownLiquidationConfig())
    assert list(disabled.equity_curve) == list(baseline.equity_curve)
    assert len(disabled.fills) == len(baseline.fills)


def test_enabled_brake_sells_out_and_latches_its_state() -> None:
    brake = DrawdownLiquidationConfig(enabled=True, threshold_pct=0.30, rearm_recovery_pct=0.20, cooldown_sessions=5)
    engine, result = _run(brake)
    _, baseline = _run(None)
    assert _held(result) == 0.0, "the brake sells the whole managed position"
    assert _held(baseline) > 0.0, "without the brake the position is kept"
    state = engine._drawdown_liquidation_state  # noqa: SLF001 - the carried latch
    assert state is not None and state.liquidated_at is not None
