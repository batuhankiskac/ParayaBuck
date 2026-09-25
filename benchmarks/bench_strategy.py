"""Strategy computation benchmarks: equity leveraged trend v1, crypto low variance v2.

Deterministic synthetic histories; measures candidate scoring, ranking and
target construction in isolation (the per-decision hot path).
"""

from __future__ import annotations

import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.harness import Report, timeit
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_low_variance import CryptoLowVariance1dV2
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1

_EQUITY_BARS = 300  # >= sma_bars(200)
_CRYPTO_SYMBOLS = 18
_CRYPTO_BARS = 120  # >= lookback(22) + 1


def _synthetic_history(
    symbols: list[str],
    bars: int,
    market: Market,
    start: datetime,
    seed: int,
) -> dict[str, tuple[Bar, ...]]:
    """Deterministic synthetic daily bars; last bar timestamp = start + (bars-1) days."""
    rng = __import__("random").Random(seed)
    history: dict[str, tuple[Bar, ...]] = {}
    for index, symbol in enumerate(symbols):
        price = 50.0 + index
        drift = (rng.random() - 0.5) * 0.0008
        vol = 0.01 + rng.random() * 0.02
        series: list[Bar] = []
        for day in range(bars):
            price *= math.exp(drift + rng.gauss(0, vol))
            close = round(price, 2)
            timestamp = start + timedelta(days=day)
            series.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    round(close * 0.997, 2),
                    round(close * 1.004, 2),
                    round(close * 0.995, 2),
                    close,
                    2_000_000.0,
                    market,
                )
            )
        history[symbol] = tuple(series)
    return history


def _equity_context() -> tuple[PortfolioContext, EquityLeveragedTrendV1]:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    symbols = ["SPY", "QQQ", "QLD"]
    history = _synthetic_history(symbols, _EQUITY_BARS, Market.EQUITY, start, seed=7)
    universe = SimpleNamespace(contains=lambda _s, _m, _t: True)
    context = PortfolioContext(
        decision_timestamp=history["SPY"][-1].timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=universe,
        universe_version="bench",
        equity=100_000.0,
        holdings={"QLD": 100.0},
        prices={symbol: history[symbol][-1].close for symbol in symbols},
        benchmark_symbol="SPY",
    )
    strategy = EquityLeveragedTrendV1({"vol_target": 0.35})
    return context, strategy


def _crypto_context() -> tuple[PortfolioContext, CryptoLowVariance1dV2]:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    symbols = [
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
        "XRP/USD",
        "ADA/USD",
        "DOGE/USD",
        "AVAX/USD",
        "LINK/USD",
        "LTC/USD",
        "BCH/USD",
        "DOT/USD",
        "UNI/USD",
        "AAVE/USD",
        "ARB/USD",
        "FIL/USD",
        "RENDER/USD",
        "ONDO/USD",
        "HYPE/USD",
    ]
    history = _synthetic_history(symbols, _CRYPTO_BARS, Market.CRYPTO, start, seed=11)
    universe = SimpleNamespace(contains=lambda _s, _m, _t: True)
    context = PortfolioContext(
        decision_timestamp=history["BTC/USD"][-1].timestamp,
        market=Market.CRYPTO,
        timeframe="1d",
        history=history,
        universe=universe,
        universe_version="bench",
        equity=100_000.0,
        holdings={"BTC/USD": 0.01},
        prices={symbol: history[symbol][-1].close for symbol in symbols},
        benchmark_symbol="BTC/USD",
    )
    strategy = CryptoLowVariance1dV2()
    return context, strategy


def run(report: Report) -> None:
    equity_context, equity_strategy = _equity_context()
    crypto_context, crypto_strategy = _crypto_context()

    timing, _ = timeit(
        lambda: equity_strategy.decide(equity_context),
        label="equity decide (QQQ trend, QLD vol target)",
        warmup=3,
        runs=10,
    )
    report.add(timing)

    timing, _ = timeit(
        lambda: crypto_strategy.rank_candidates(crypto_context),
        label="crypto rank_candidates (18 sym)",
        warmup=3,
        runs=10,
    )
    report.add(timing)

    timing, _ = timeit(
        lambda: crypto_strategy.decide(crypto_context),
        label="crypto decide (18 sym)",
        warmup=3,
        runs=10,
    )
    report.add(timing)

    timing, _ = timeit(
        lambda: (
            crypto_strategy.rank_candidates(crypto_context),
            crypto_strategy.decide(crypto_context),
        ),
        label="crypto rank+decide combined",
        warmup=3,
        runs=10,
    )
    report.add(timing)


if __name__ == "__main__":
    report = Report()
    run(report)
    report.print_table()
