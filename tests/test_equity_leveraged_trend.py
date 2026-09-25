"""Contract tests for the trend-filtered leveraged index ETF equity identity.

Each test pins one invariant: the SMA trend rule on completed closes, cash when
history is short or the hold ETF is unavailable, the unconditional-hold control
(``sma_bars: 0``), parameter validation, the daily policy, and registration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import (
    PRODUCT_STRATEGY_NAMES,
    RebalancePolicy,
    portfolio_strategy_from,
    rebalance_policy_for,
)
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1

START = datetime(2024, 1, 1, 21, 0, tzinfo=UTC)


def _bars(symbol: str, closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(START + timedelta(days=index), symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)
        for index, close in enumerate(closes)
    )


class _Universe:
    def __init__(self, excluded: set[str] | None = None) -> None:
        self.excluded = excluded or set()

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return symbol not in self.excluded


def _context(paths: dict[str, list[float]], universe: _Universe | None = None) -> PortfolioContext:
    history = {symbol: _bars(symbol, closes) for symbol, closes in paths.items()}
    end = max(len(closes) for closes in paths.values()) - 1
    return PortfolioContext(
        decision_timestamp=START + timedelta(days=end),
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=universe or _Universe(),
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )


def _strategy(**parameters: object) -> EquityLeveragedTrendV1:
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 5, **parameters})
    assert isinstance(strategy, EquityLeveragedTrendV1)
    return strategy


def test_close_above_sma_targets_the_hold_etf_with_the_full_allocation() -> None:
    target = _strategy().decide(_context({"QQQ": [100, 100, 100, 100, 100, 101], "QLD": [50] * 6}))
    assert [(weight.symbol, weight.weight) for weight in target.weights] == [("QLD", 1.0)]
    assert target.cash_weight == 0.0
    assert target.metadata["trend_state"] == "uptrend"


def test_close_at_or_below_sma_targets_cash() -> None:
    target = _strategy().decide(_context({"QQQ": [100, 100, 100, 100, 100, 100], "QLD": [50] * 6}))
    assert target.weights == ()
    assert target.cash_weight == 1.0
    assert target.metadata["trend_state"] == "downtrend"


def test_sma_uses_only_the_last_sma_bars_completed_closes() -> None:
    # An old spike outside the 5-bar window must not lift the average.
    target = _strategy().decide(_context({"QQQ": [1000, 100, 100, 100, 100, 101], "QLD": [50] * 6}))
    assert target.metadata["signal_sma"] == pytest.approx(100.2)
    assert target.metadata["trend_state"] == "uptrend"


def test_short_signal_history_targets_cash() -> None:
    target = _strategy().decide(_context({"QQQ": [100, 101, 102, 103], "QLD": [50] * 4}))
    assert target.weights == ()
    assert target.metadata["trend_state"] == "insufficient_history"


@pytest.mark.parametrize("universe", [_Universe({"QLD"}), None])
def test_unavailable_hold_etf_targets_cash(universe: _Universe | None) -> None:
    paths = {"QQQ": [100, 100, 100, 100, 100, 101]}
    if universe is not None:
        paths["QLD"] = [50] * 6
    target = _strategy().decide(_context(paths, universe))
    assert target.weights == ()
    assert target.metadata["trend_state"] == "hold_symbol_unavailable"


def test_zero_sma_holds_unconditionally() -> None:
    target = _strategy(sma_bars=0, hold_symbol="VOO").decide(_context({"QQQ": [100, 90, 80], "VOO": [400] * 3}))
    assert [(weight.symbol, weight.weight) for weight in target.weights] == [("VOO", 1.0)]


@pytest.mark.parametrize(
    "parameters",
    [{"sma_bars": -1}, {"sma_bars": 2.5}, {"sma_bars": True}, {"hold_symbol": ""}, {"rebalance_frequency": "weekly"}],
)
def test_invalid_parameters_are_rejected(parameters: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _strategy(**parameters)


def test_defaults_and_daily_policy() -> None:
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1")
    assert (strategy.signal_symbol, strategy.hold_symbol, strategy.sma_bars) == ("QQQ", "QLD", 200)
    assert strategy.minimum_history_bars == 200
    assert rebalance_policy_for(strategy) == RebalancePolicy(Market.EQUITY, "hours:24")
    assert "equity_leveraged_trend_v1" in PRODUCT_STRATEGY_NAMES


def _alternating(n: int, low: float, high: float) -> list[float]:
    return [low if index % 2 == 0 else high for index in range(n)]


def test_vol_target_scales_the_weight_down_when_the_etf_is_turbulent() -> None:
    qld = _alternating(8, 50.0, 55.0)
    strategy = _strategy(vol_target=0.35, vol_window=5)
    context = _context({"QQQ": [100, 100, 100, 100, 100, 100, 100, 101], "QLD": qld})
    target = strategy.decide(context)
    volatility = strategy.realized_volatility(context)
    assert volatility is not None and volatility > 0.35
    assert [(weight.symbol, weight.weight) for weight in target.weights] == [("QLD", pytest.approx(0.35 / volatility))]
    assert target.cash_weight == pytest.approx(1.0 - 0.35 / volatility)


def test_vol_target_caps_the_weight_at_one_when_calm() -> None:
    target = _strategy(vol_target=0.35, vol_window=5).decide(
        _context({"QQQ": [100, 100, 100, 100, 100, 100, 100, 101], "QLD": [50.0 + 0.01 * i for i in range(8)]})
    )
    assert [(weight.symbol, weight.weight) for weight in target.weights] == [("QLD", 1.0)]


def test_vol_target_needs_vol_window_plus_one_hold_closes() -> None:
    strategy = _strategy(vol_target=0.35, vol_window=10)
    assert strategy.minimum_history_bars == 11
    target = strategy.decide(_context({"QQQ": [100, 100, 100, 100, 100, 101], "QLD": [50] * 6}))
    assert target.weights == ()
    assert target.metadata["trend_state"] == "insufficient_history"


@pytest.mark.parametrize("parameters", [{"vol_target": -0.1}, {"vol_target": "x"}, {"vol_window": 1}])
def test_invalid_vol_parameters_are_rejected(parameters: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _strategy(**parameters)
