from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, TerminalLifecycle
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight


class _TerminalFixtureStrategy(PortfolioStrategy):
    name = "terminal_fixture"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    rebalance_policy_declared = "weekly"

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weights = (TargetWeight("ESRX", 0.20, rank=1),) if "ESRX" in context.history else ()
        return TargetPortfolio(
            self.name,
            self.version,
            context.decision_timestamp,
            weights,
            1.0 - sum(weight.weight for weight in weights),
            universe_version=context.universe_version,
        )


def _bar(timestamp: datetime, symbol: str, close: float) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000_000.0, Market.EQUITY)


def _engine(terminal: datetime) -> PortfolioBacktestEngine:
    start = datetime(2024, 1, 8, tzinfo=UTC)
    universe = PointInTimeUniverse(
        Market.EQUITY,
        (
            UniverseSnapshot(
                effective_at=start,
                symbols=frozenset({"SPY", "ESRX"}),
                observation_set=("SPY", "ESRX"),
                maximum_stream_symbols=2,
                pinned_symbols=("SPY",),
            ),
        ),
        "terminal fixture",
    )
    return PortfolioBacktestEngine(
        _TerminalFixtureStrategy(),
        RebalancePolicy(Market.EQUITY, "weekly"),
        starting_equity=100_000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        universe=universe,
        universe_version="terminal-fixture",
        allow_static_universe=True,
        terminal_lifecycles={
            "ESRX": TerminalLifecycle(terminal, "POSSIBLE_DELISTING"),
        },
    )


def test_real_terminal_bar_liquidates_and_unwedges_later_decision() -> None:
    start = datetime(2024, 1, 8, tzinfo=UTC)
    terminal = start + timedelta(days=2)
    later = start + timedelta(days=7)
    bars = [
        _bar(start, "SPY", 100.0),
        _bar(start, "ESRX", 50.0),
        _bar(start + timedelta(days=1), "SPY", 101.0),
        _bar(start + timedelta(days=1), "ESRX", 51.0),
        _bar(terminal, "SPY", 102.0),
        _bar(terminal, "ESRX", 52.0),
        _bar(later, "SPY", 103.0),
    ]

    result = _engine(terminal).run(bars, liquidate_at_end=False)

    assert [record.decision_timestamp for record in result.rebalances] == [start, later]
    assert result.state.holdings == {}
    assert len(result.terminal_events) == 1
    assert result.terminal_events[0]["timestamp"] == terminal.isoformat()
    assert result.terminal_events[0]["last_close"] == pytest.approx(52.0)
    assert result.terminal_events[0]["policy"] == "TERMINAL_BAR"
    assert result.terminal_events[0]["terminal_event_class"] == "POSSIBLE_DELISTING"


def test_terminal_position_without_real_final_bar_fails_closed() -> None:
    start = datetime(2024, 1, 8, tzinfo=UTC)
    terminal = start + timedelta(days=2)
    bars = [
        _bar(start, "SPY", 100.0),
        _bar(start, "ESRX", 50.0),
        _bar(start + timedelta(days=1), "SPY", 101.0),
        _bar(start + timedelta(days=1), "ESRX", 51.0),
        _bar(terminal, "SPY", 102.0),
    ]

    with pytest.raises(RuntimeError, match="requires a real final execution bar"):
        _engine(terminal).run(bars, liquidate_at_end=False)


def test_terminal_lifecycle_metadata_requires_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _engine(datetime(2024, 1, 10))
