from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market, Regime
from parayabuck.markets.regime import RegimeAssessment
from parayabuck.strategies.base import StrategyContext
from parayabuck.strategies.crypto_range import CryptoRangeReversion
from parayabuck.strategies.crypto_trend import CryptoTrendBreakout


def _crypto_bar(index: int, close: float, volume: float, **overrides: float) -> Bar:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * index)
    return Bar(
        timestamp,
        "BTC/USD",
        "5m",
        close,
        overrides.get("high", close + 0.3),
        overrides.get("low", close - 0.3),
        close,
        volume,
        Market.CRYPTO,
    )


def _trend_bars(baseline_volumes: list[float], current_volume: float = 1000.0) -> list[Bar]:
    """Slow-rise series with a 20-bar breakout; baseline_volumes covers bars[-21:-1]."""
    assert len(baseline_volumes) == 20
    bars = [_crypto_bar(i, 100 + i * 0.01, 1000.0) for i in range(199)]
    bars.append(_crypto_bar(199, 103.0, current_volume, high=103.2, low=102.0))
    for offset, volume in enumerate(baseline_volumes):
        bars[179 + offset] = replace(bars[179 + offset], volume=volume)
    return bars


def _range_bars(window_volumes: list[float]) -> list[Bar]:
    """Flat-ish range series; window_volumes covers bars[-20:]."""
    assert len(window_volumes) == 20
    bars = [_crypto_bar(i, 100.0 + (i % 5) * 0.01, 1000.0) for i in range(50)]
    for offset, volume in enumerate(window_volumes):
        bars[30 + offset] = replace(bars[30 + offset], volume=volume)
    return bars


def _context(regime: Regime) -> StrategyContext:
    return StrategyContext(
        regime=RegimeAssessment(regime, 0.9, 30.0, 0.01, ("test_fixture",)),
        higher_timeframe_regime=Regime.TREND_UP,
    )


def _condition(evaluation, code: str):
    return next(item for item in evaluation.conditions if item.code == code)


def test_trend_breakout_fires_with_half_of_window_volumed() -> None:
    # 11 of the last 21 bars have volume>0 (10 baseline + current): gate must open.
    baseline = [1000.0, 0.0] * 10
    evaluation = CryptoTrendBreakout().evaluate_with_trace(
        _trend_bars(baseline), _context(Regime.TREND_UP)
    )
    assert _condition(evaluation, "indicators_available").passed
    assert evaluation.fired
    assert evaluation.signal is not None
    assert evaluation.signal.strategy == "crypto_trend_breakout"


def test_trend_breakout_rejects_nearly_dead_volume_series() -> None:
    # Only 5 of the last 21 bars have volume>0: series is still filtered out.
    baseline = [1000.0, 0.0, 0.0, 0.0] * 5
    evaluation = CryptoTrendBreakout().evaluate_with_trace(
        _trend_bars(baseline), _context(Regime.TREND_UP)
    )
    assert not evaluation.fired
    assert evaluation.signal is None
    assert evaluation.summary == "Required indicators are unavailable."
    assert not _condition(evaluation, "indicators_available").passed


def test_relative_volume_stays_neutral_when_baseline_is_mostly_zero() -> None:
    # 9 nonzero baseline bars plus a volumed current bar: gate opens at the 0.5 ratio.
    # Median-of-nonzero baseline keeps relative_volume at 1.0 instead of inflating
    # the score against a zero-diluted mean.
    baseline = [1000.0] * 9 + [0.0] * 11
    evaluation = CryptoTrendBreakout().evaluate_with_trace(
        _trend_bars(baseline), _context(Regime.TREND_UP)
    )
    assert evaluation.fired
    assert evaluation.metrics is not None
    assert evaluation.metrics["relative_volume"] == pytest.approx(1.0)
    assert evaluation.metrics["volume_points"] == pytest.approx(4.0)
    assert evaluation.signal is not None
    assert evaluation.signal.score <= 100


def test_relative_volume_defaults_to_one_without_any_volumed_baseline() -> None:
    # A tolerant ratio lets the gate open with only the current bar volumed; with no
    # nonzero baseline bars at all the neutral relative_volume fallback applies.
    strategy = CryptoTrendBreakout(min_volume_bar_ratio=0.05)
    evaluation = strategy.evaluate_with_trace(
        _trend_bars([0.0] * 20), _context(Regime.TREND_UP)
    )
    assert evaluation.fired
    assert evaluation.metrics is not None
    assert evaluation.metrics["relative_volume"] == pytest.approx(1.0)
    assert evaluation.metrics["volume_points"] == pytest.approx(4.0)
    assert evaluation.signal is not None
    assert evaluation.signal.score <= 100


def test_range_reversion_volume_gate_is_ratio_based() -> None:
    context = _context(Regime.RANGE)
    sparse = CryptoRangeReversion().evaluate_with_trace(
        _range_bars([1000.0, 0.0] * 10), context
    )
    assert _condition(sparse, "indicators_available").passed
    dead = CryptoRangeReversion().evaluate_with_trace(
        _range_bars([1000.0, 0.0, 0.0, 0.0] * 5), context
    )
    assert not _condition(dead, "indicators_available").passed
    assert dead.summary == "Required indicators are unavailable."
