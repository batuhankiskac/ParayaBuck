from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_long_horizon_momentum import (
    EquityLongHorizonMomentum1dV2,
)

V2_NAME = "equity_long_horizon_momentum_1d_v2"
_BASE = datetime(2020, 1, 1, 4, 0, tzinfo=UTC)


def _bar(symbol: str, day: int, close: float) -> Bar:
    return Bar(
        timestamp=_BASE + timedelta(days=day),
        symbol=symbol,
        timeframe="1d",
        open=close * 0.99,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=1_000_000.0,
        market=Market.EQUITY,
    )


class _FakeUniverse:
    def __init__(self, symbols: tuple[str, ...]) -> None:
        self._symbols = symbols

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return symbol in self._symbols


def _series(symbol: str, slope: float, *, days: int = 320) -> tuple[Bar, ...]:
    return tuple(_bar(symbol, day, 100.0 + slope * day) for day in range(1, days + 1))


def _context(series: dict[str, tuple[Bar, ...]], *, spy_slope: float) -> PortfolioContext:
    history: dict[str, tuple[Bar, ...]] = dict(series)
    history.setdefault("SPY", _series("SPY", spy_slope))
    return PortfolioContext(
        decision_timestamp=_BASE + timedelta(days=330),
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=_FakeUniverse(tuple(series)),
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )


def test_v2_registered_in_factory() -> None:
    strategy = portfolio_strategy_from(V2_NAME)
    assert isinstance(strategy, EquityLongHorizonMomentum1dV2)
    assert strategy.name == V2_NAME
    assert strategy.market == Market.EQUITY
    assert strategy.timeframe == "1d"


def test_v2_minimum_history_bars_from_lookback() -> None:
    strategy = portfolio_strategy_from(V2_NAME)
    # max(200, 252 + 21) + 1 = 274
    assert strategy.minimum_history_bars == 274


def test_v2_rebalance_policy_monthly_by_default() -> None:
    strategy = portfolio_strategy_from(V2_NAME)
    policy = rebalance_policy_for(strategy)
    assert policy.frequency == "monthly"


def test_v2_selects_highest_momentum_and_respects_cap() -> None:
    strategy = portfolio_strategy_from(
        V2_NAME,
        {
            "use_stock_trend": True,
            "use_market_regime": False,
            "sizing": "equal",
            "use_vol_target": False,
            "exclude_symbols": ["SPY"],
        },
    )
    series = {f"S{i}": _series(f"S{i}", slope=2.0 + i) for i in range(6)}
    series["WEAK"] = _series("WEAK", slope=-0.25)  # negative momentum
    target = strategy.decide(_context(series, spy_slope=2.0))
    selected = {w.symbol for w in target.weights}
    assert selected == {f"S{i}" for i in range(6)}
    assert "WEAK" not in selected
    assert all(abs(w.weight - 0.10) < 1e-9 for w in target.weights)
    assert target.cash_weight == pytest.approx(0.40)


def test_v2_market_regime_gate_goes_to_cash_when_spy_below_trend() -> None:
    strategy = portfolio_strategy_from(
        V2_NAME,
        {
            "use_stock_trend": True,
            "use_market_regime": True,
            "sizing": "equal",
            "use_vol_target": False,
            "exclude_symbols": ["SPY"],
        },
    )
    series = {f"S{i}": _series(f"S{i}", slope=2.0 + i) for i in range(6)}
    # SPY falls hard, so it sits below its own SMA200 -> regime off.
    target = strategy.decide(_context(series, spy_slope=-0.2))
    assert target.weights == ()
    assert target.cash_weight == pytest.approx(1.0)
    assert target.metadata["market_regime"] is False


def test_v2_ablated_components_change_behavior() -> None:
    # M0: no stock trend gate; a falling name with negative momentum still
    # fails the score>0 gate, so only the rising name qualifies.
    series = {"UP": _series("UP", slope=2.0), "DOWN": _series("DOWN", slope=-0.2)}
    m0 = portfolio_strategy_from(
        V2_NAME,
        {
            "use_stock_trend": False,
            "use_market_regime": False,
            "sizing": "equal",
            "use_vol_target": False,
            "exclude_symbols": ["SPY"],
        },
    )
    target_m0 = m0.decide(_context(series, spy_slope=2.0))
    assert {w.symbol for w in target_m0.weights} == {"UP"}


def test_v2_invalid_parameters_rejected() -> None:
    with pytest.raises(ValueError):
        portfolio_strategy_from(V2_NAME, {"momentum_window": 10})
    with pytest.raises(ValueError):
        portfolio_strategy_from(V2_NAME, {"momentum_skip": 300})
    with pytest.raises(ValueError):
        portfolio_strategy_from(V2_NAME, {"sizing": "magic"})
    with pytest.raises(ValueError):
        portfolio_strategy_from(V2_NAME, {"use_stock_trend": "yes"})
