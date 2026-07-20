"""Unit tests for the indicator primitives.

These guard the lowest-level maths the strategies depend on. If RSI or ATR
regresses, every strategy built on top of them silently breaks — so we pin
the canonical behaviours here.
"""
from __future__ import annotations

import pytest

from parayabuck.strategy.indicators import (
    atr,
    bollinger,
    ema,
    rsi,
    session_vwap_step,
    sma,
    vwap,
    zscore,
)


def test_sma_basic():
    assert sma([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)
    assert sma([1, 2, 3], 5) is None  # not enough data


def test_ema_seeds_with_sma():
    # EMA(3) over [1, 2, 3, 4] -> seed = mean(1,2,3)=2, then (4-2)*0.5+2=3
    assert ema([1, 2, 3, 4], 3) == pytest.approx(3.0)


def test_ema_returns_none_for_short_input():
    assert ema([1, 2], 5) is None


def test_rsi_all_up_is_100():
    # Monotone ascending series -> every change is a gain -> RSI=100.
    vals = [float(i) for i in range(50)]
    assert rsi(vals, 14) == pytest.approx(100.0)


def test_rsi_all_down_is_0():
    vals = [float(50 - i) for i in range(50)]
    assert rsi(vals, 14) == pytest.approx(0.0)


def test_rsi_neutral_series_near_50():
    # Perfectly alternating series should average near 50.
    vals = [100.0, 100.5, 100.0, 100.5] * 20
    r = rsi(vals, 14)
    assert r is not None and 30 < r < 70


def test_bollinger_contains_mean():
    vals = [100.0] * 20
    bb = bollinger(vals, 20, 2.0)
    assert bb is not None
    assert bb.middle == pytest.approx(100.0)
    assert bb.upper == pytest.approx(100.0)
    assert bb.lower == pytest.approx(100.0)
    assert bb.bandwidth == pytest.approx(0.0)


def test_bollinger_bands_around_std():
    # 20 values from N(0,1)-ish; bands should sit off the mean.
    vals = [
        -1.0, 0.5, -0.5, 1.0, 0.0, -1.5, 1.5, 0.3, -0.3, 0.7,
        -0.7, 0.9, -0.9, 1.2, -1.2, 0.2, -0.2, 0.4, -0.4, 0.6,
    ]
    bb = bollinger(vals, 20, 2.0)
    assert bb is not None
    assert bb.upper > bb.middle > bb.lower


def test_atr_positive_for_volatile_series():
    highs = [11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0,
             21.0, 22.0, 23.0, 24.0, 25.0, 26.0]
    lows = [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0,
            19.0, 20.0, 21.0, 22.0, 23.0, 24.0]
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0,
              20.0, 21.0, 22.0, 23.0, 24.0, 25.0]
    a = atr(highs, lows, closes, 14)
    assert a is not None and a > 0


def test_atr_zero_for_flat_series():
    n = 30
    closes = [100.0] * n
    highs = [100.0] * n
    lows = [100.0] * n
    assert atr(highs, lows, closes, 14) == pytest.approx(0.0)


def test_vwap_standard_typical_price():
    # With closes provided, VWAP uses (H+L+C)/3.
    highs = [10.0, 12.0]
    lows = [8.0, 10.0]
    closes = [9.0, 11.0]
    vols = [100, 200]
    # Bar 0 tp = (10+8+9)/3 = 9; bar 1 tp = (12+10+11)/3 = 11
    # VWAP = (9*100 + 11*200) / 300 = (900 + 2200)/300 = 3100/300 ≈ 10.333
    assert vwap(highs, lows, closes, vols) == pytest.approx(3100.0 / 300.0)


def test_vwap_legacy_fallback_uses_hl_only():
    # Without closes, falls back to (H+L)/2.
    highs = [10.0, 12.0]
    lows = [8.0, 10.0]
    vols = [100, 200]
    # Bar 0 tp = (10+8)/2 = 9; bar 1 tp = (12+10)/2 = 11
    assert vwap(highs, lows, None, vols) == pytest.approx(3100.0 / 300.0)


def test_session_vwap_step_resets_and_accumulates():
    # First bar: tp = (100+90+95)/3 = 95, vol = 1000
    cum_pv, cum_v, v = session_vwap_step(0.0, 0.0, 100.0, 90.0, 95.0, 1000)
    assert cum_pv == pytest.approx(95.0 * 1000)
    assert cum_v == 1000
    assert v == pytest.approx(95.0)
    # Second bar: tp = (102+92+98)/3 = 97.333, vol = 2000
    cum_pv, cum_v, v = session_vwap_step(cum_pv, cum_v, 102.0, 92.0, 98.0, 2000)
    expected = (95.0 * 1000 + (102 + 92 + 98) / 3 * 2000) / 3000
    assert v == pytest.approx(expected)


def test_zscore_zero_for_flat():
    vals = [100.0] * 30
    assert zscore(vals, 20) == pytest.approx(0.0)


def test_zscore_positive_for_above_mean():
    # A spike at the end -> positive z.
    vals = [100.0] * 30 + [120.0]
    z = zscore(vals, 20)
    assert z is not None and z > 0


def test_zscore_handles_short_input():
    assert zscore([1, 2], 20) is None
