"""Offline regression contracts for the current crypto replay boundary."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import load_csv_bars
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import aggregate_historical_bars
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1
from parayabuck.portfolio.models import RebalancePolicy

ANCHOR = datetime(2024, 1, 1, tzinfo=UTC)


def _bar(symbol: str, day: int, close: float) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ANCHOR + timedelta(days=day),
        timeframe="1d",
        market=Market.CRYPTO,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
    )


def test_actual_volatility_ranking_retains_four_and_fills_vacancy() -> None:
    symbols = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD")
    history = {
        symbol: tuple(_bar(symbol, i, 100 + rank * (i % 2)) for i in range(23))
        for rank, symbol in enumerate(symbols, 1)
    }
    context = PortfolioContext(
        ANCHOR + timedelta(days=22),
        Market.CRYPTO,
        "1d",
        history,
        None,
        "synthetic",
        100000,
        {"XRP/USD": 0.5, "ADA/USD": 0.5},
        {},
        "BTC/USD",
    )
    strategy = CryptoLowVarianceHysteresis1dV1()
    assert strategy.minimum_history_bars == 23
    assert strategy.rank_candidates(context) == symbols
    target = strategy.decide(context)
    assert target.metadata["retained"] == ("XRP/USD",)
    assert {w.symbol: w.weight for w in target.weights} == {"BTC/USD": 0.5, "XRP/USD": 0.5}


@pytest.mark.parametrize(("hours", "due"), [(24, False), (48, False), (72, False), (96, True), (120, True)])
def test_96h_epoch_boundary(hours: int, due: bool) -> None:
    policy = RebalancePolicy(Market.CRYPTO, "hours:96")
    boundary = policy.next_boundary_after(ANCHOR)
    assert policy.is_period_start(boundary, boundary + timedelta(hours=hours)) is due


def test_non_aligned_start_does_not_mean_96_elapsed_hours() -> None:
    policy = RebalancePolicy(Market.CRYPTO, "hours:96")
    boundary = policy.next_boundary_after(ANCHOR)
    assert policy.is_period_start(boundary - timedelta(hours=24), boundary)
    assert policy.is_period_start(None, boundary)
    assert not policy.is_period_start(boundary, boundary)


def test_csv_defaults_and_canonical_dedup_are_explicit(tmp_path: Path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2024-01-01T00:00:00,BTCUSD,100,100,100,100,0\n"
        "2024-01-01T00:00:00+00:00,BTC/USD,100,100,100,100,0\n"
    )
    loaded = load_csv_bars(source, default_market=Market.CRYPTO)
    assert loaded[0].timeframe == "1h"
    assert loaded[0].timestamp.tzinfo == UTC
    normalized = PortfolioBacktestEngine._deduplicate(loaded)
    assert len(normalized) == 1
    assert normalized[0].symbol == "BTC/USD"
    assert normalized[0].volume == 0


def test_complete_daily_aggregation_excludes_missing_hour_but_keeps_zero_volume() -> None:
    bars = [
        Bar(
            symbol="BTC/USD",
            timestamp=ANCHOR + timedelta(hours=i),
            timeframe="1h",
            market=Market.CRYPTO,
            open=100,
            high=100,
            low=100,
            close=100,
            volume=0,
        )
        for i in range(24)
    ]
    assert aggregate_historical_bars(bars[:-1], "1d") == []
    daily = aggregate_historical_bars(bars, "1d")
    assert len(daily) == 1 and daily[0].volume == 0
    assert daily[0].timestamp == ANCHOR
