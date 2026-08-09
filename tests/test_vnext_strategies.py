from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_volatility_trend import (
    CRYPTO_CORE_UNIVERSE,
    CryptoVolatilityTrend1dV1,
)
from parayabuck.portfolio.equity_covariance_trend import (
    EQUITY_ALL_WEATHER_UNIVERSE,
    EquityCovarianceTrend1dV1,
)


def _context(market: Market, trends: dict[str, float], days: int = 260) -> PortfolioContext:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    history: dict[str, tuple[Bar, ...]] = {}
    for symbol, drift in trends.items():
        bars = []
        for index in range(days):
            close = 100.0 * (1 + drift) ** index
            bars.append(
                Bar(
                    start + timedelta(days=index),
                    symbol,
                    "1d",
                    close,
                    close,
                    close,
                    close,
                    1_000_000,
                    market,
                )
            )
        history[symbol] = tuple(bars)
    return PortfolioContext(
        decision_timestamp=start + timedelta(days=days - 1),
        market=market,
        timeframe="1d",
        history=history,
        universe=None,
        universe_version="fixture",
        equity=100_000.0,
        holdings={},
        prices={symbol: bars[-1].close for symbol, bars in history.items()},
        benchmark_symbol="BTC/USD" if market == Market.CRYPTO else "SPY",
    )


def test_equity_vnext_is_long_cash_capped_and_includes_defensive_assets() -> None:
    trends = {
        symbol: (0.0005 if symbol in {"SPY", "IEF", "GLD"} else -0.0003)
        for symbol in EQUITY_ALL_WEATHER_UNIVERSE
    }
    target = EquityCovarianceTrend1dV1().decide(_context(Market.EQUITY, trends))
    assert {weight.symbol for weight in target.weights} == {"SPY", "IEF", "GLD"}
    assert all(0 < weight.weight <= 0.25 for weight in target.weights)
    assert target.cash_weight >= 0.25
    assert sum(weight.weight for weight in target.weights) + target.cash_weight == pytest.approx(1)


def test_equity_vnext_goes_fully_to_cash_when_all_trends_are_negative() -> None:
    target = EquityCovarianceTrend1dV1().decide(
        _context(
            Market.EQUITY,
            {symbol: -0.0003 for symbol in EQUITY_ALL_WEATHER_UNIVERSE},
        )
    )
    assert target.weights == ()
    assert target.cash_weight == 1.0


def test_equity_vnext_requests_intermediate_reduction_on_held_trend_break() -> None:
    context = _context(
        Market.EQUITY,
        {symbol: -0.0003 for symbol in EQUITY_ALL_WEATHER_UNIVERSE},
    )
    context = replace(context, holdings={"SPY": 10.0})
    assert EquityCovarianceTrend1dV1().requires_intermediate_risk_rebalance(context)


def test_crypto_vnext_is_btc_eth_only_weekly_and_account_capped() -> None:
    strategy = CryptoVolatilityTrend1dV1()
    target = strategy.decide(
        _context(
            Market.CRYPTO,
            {"BTC/USD": 0.001, "ETH/USD": 0.0012, "SOL/USD": 0.002},
        )
    )
    assert {weight.symbol for weight in target.weights}.issubset(CRYPTO_CORE_UNIVERSE)
    assert all(weight.weight <= 0.05 for weight in target.weights)
    assert sum(weight.weight for weight in target.weights) <= 0.10
    assert rebalance_policy_for(strategy).hours == 168


def test_vnext_strategies_are_registered_under_new_family_names() -> None:
    assert isinstance(
        portfolio_strategy_from("equity_covariance_trend_1d_v1"),
        EquityCovarianceTrend1dV1,
    )
    assert isinstance(
        portfolio_strategy_from("crypto_volatility_trend_1d_v1"),
        CryptoVolatilityTrend1dV1,
    )
