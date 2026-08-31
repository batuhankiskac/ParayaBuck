from __future__ import annotations

import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from research_equity_m3_m4 import (  # noqa: E402
    FROZEN_PARAMETERS,
    ProxyMultifactorResidualMomentum,
    VolumePersistenceResidualMomentum,
)


class _Universe:
    def __init__(self, symbols: set[str]) -> None:
        self.symbols = symbols

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        del timestamp
        return market == Market.EQUITY and symbol in self.symbols


def _series(symbol: str, returns: list[float], volumes: list[float] | None = None) -> tuple[Bar, ...]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    volume_values = volumes or [1_000_000.0] * len(closes)
    return tuple(
        Bar(
            start + timedelta(days=index),
            symbol,
            "1d",
            close,
            close,
            close,
            close,
            volume_values[index],
            Market.EQUITY,
        )
        for index, close in enumerate(closes)
    )


def _context(history: dict[str, tuple[Bar, ...]]) -> PortfolioContext:
    return PortfolioContext(
        decision_timestamp=max(bar.timestamp for series in history.values() for bar in series),
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=_Universe(set(history)),
        universe_version="research-test",
        equity=100_000.0,
        holdings={},
        prices={symbol: series[-1].close for symbol, series in history.items()},
        benchmark_symbol="SPY",
    )


def test_multifactor_candidate_fails_closed_without_iwm() -> None:
    returns = [0.001 + 0.0002 * math.sin(index) for index in range(299)]
    context = _context({symbol: _series(symbol, returns) for symbol in ("SPY", "QQQ", "AAA")})
    strategy = ProxyMultifactorResidualMomentum(FROZEN_PARAMETERS)

    assert strategy.rank_candidates(context) == ()
    assert strategy.reference_outages == 1


def test_multifactor_candidate_uses_all_three_reference_series() -> None:
    spy = [0.0002 + 0.0003 * math.sin(index * 0.7) for index in range(299)]
    size = [0.0002 * math.sin(index * 1.1) for index in range(299)]
    growth = [0.00025 * math.cos(index * 0.9) for index in range(299)]
    iwm = [market + spread for market, spread in zip(spy, size, strict=True)]
    qqq = [market + spread for market, spread in zip(spy, growth, strict=True)]

    def stock(alpha: float, phase: float) -> list[float]:
        return [
            alpha + 0.8 * market + 0.4 * size_factor - 0.2 * growth_factor + 0.00015 * math.sin(index * 1.7 + phase)
            for index, (market, size_factor, growth_factor) in enumerate(zip(spy, size, growth, strict=True))
        ]

    context = _context(
        {
            "SPY": _series("SPY", spy),
            "IWM": _series("IWM", iwm),
            "QQQ": _series("QQQ", qqq),
            "AAA": _series("AAA", stock(0.0010, 0.0)),
            "BBB": _series("BBB", stock(0.0007, 1.0)),
            "CCC": _series("CCC", stock(0.0004, 2.0)),
        }
    )
    strategy = ProxyMultifactorResidualMomentum(FROZEN_PARAMETERS)

    ranked = strategy.rank_candidates(context)

    assert len(ranked) >= 2
    assert set(ranked) <= {"AAA", "BBB", "CCC"}
    assert "IWM" not in ranked and "QQQ" not in ranked and "SPY" not in ranked
    assert strategy.reference_outages == 0
    assert strategy.coefficient_samples


def test_volume_persistence_favors_low_attention_winner() -> None:
    residuals = [0.001 + 0.0002 * math.sin(index * 1.7) for index in range(299)]
    low_attention = [1_000_000.0] * 216 + [500_000.0] * 84
    high_attention = [1_000_000.0] * 216 + [2_000_000.0] * 84
    context = _context(
        {
            "SPY": _series("SPY", [0.0] * 299),
            "AAA": _series("AAA", residuals, low_attention),
            "BBB": _series("BBB", residuals, high_attention),
        }
    )
    strategy = VolumePersistenceResidualMomentum(FROZEN_PARAMETERS)

    ranked = strategy.rank_candidates(context)

    assert ranked[:2] == ("AAA", "BBB")
    assert strategy.volume_valid == 2
    assert strategy.volume_missing_or_zero == 0
