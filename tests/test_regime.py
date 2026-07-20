"""Unit tests for the regime detector."""
from __future__ import annotations

from parayabuck.strategy.regime import (
    REGIME_TO_STRATEGY,
    MarketRegime,
    RegimeDetector,
    suggest_strategy,
)
from tests._helpers import bars_from_closes, trending_down, trending_up


def test_regime_trending_up_for_strong_uptrend():
    closes = trending_up(220, 100.0, 0.005)
    bars = bars_from_closes(closes, high_low_spread=0.01)
    det = RegimeDetector(sma_period=200)
    state = det.detect(bars)
    assert state.regime == MarketRegime.TRENDING_UP
    assert suggest_strategy(state) == "ema_crossover"


def test_regime_trending_down_for_downtrend():
    closes = trending_down(220, 100.0, 0.005)
    bars = bars_from_closes(closes, high_low_spread=0.01)
    det = RegimeDetector(sma_period=200)
    state = det.detect(bars)
    assert state.regime == MarketRegime.TRENDING_DOWN
    # Long-only bot: downtrend means no trading.
    assert suggest_strategy(state) == "none"


def test_regime_ranging_for_sideways():
    """Sideways price action with rapid oscillation should land in RANGING.

    The RegimeDetector classifies ADX < 20 as ranging. With a fast-enough
    oscillation (period ~10 bars), +DM and -DM cancel out symmetrically and
    ADX stays well below 20. Slower oscillations (period >= 20) let ADX
    accumulate within each half-swing and land in the 20-25 boundary band,
    which the detector falls through to a trending classification.
    """
    import math
    from datetime import UTC, datetime, timedelta

    from parayabuck.models import Bar

    base = datetime(2025, 1, 1, 9, 30, tzinfo=UTC)
    bars: list[Bar] = []
    for i in range(220):
        # Period 10: 5 bars up, 5 bars down — too fast for ADX to build.
        c = 100.0 + 2.0 * math.sin(2 * math.pi * i / 10)
        h = c + 1.0
        lo = c - 1.0
        bars.append(
            Bar(
                timestamp=base + timedelta(minutes=15 * i),
                symbol="TEST",
                timeframe="15m",
                open=c,
                high=h,
                low=lo,
                close=c,
                volume=1000,
            )
        )
    det = RegimeDetector(sma_period=200)
    state = det.detect(bars)
    assert state.regime in {MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY}
    assert suggest_strategy(state) == "mean_reversion"


def test_regime_unknown_for_short_input():
    bars = bars_from_closes([100.0] * 10)
    det = RegimeDetector(sma_period=200)
    state = det.detect(bars)
    assert state.regime == MarketRegime.UNKNOWN
    assert suggest_strategy(state) == "none"


def test_regime_to_strategy_map_is_canonical():
    """The canonical map covers every regime value."""
    expected_keys = {
        "trending_up", "trending_down", "ranging", "low_volatility",
        "high_volatility", "crisis", "unknown",
    }
    assert set(REGIME_TO_STRATEGY.keys()) == expected_keys
    # And every value is either "none" or a known strategy name.
    allowed = {"none", "ema_crossover", "mean_reversion"}
    for v in REGIME_TO_STRATEGY.values():
        assert v in allowed
