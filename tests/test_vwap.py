"""Unit tests for the VWAP reversion strategy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from parayabuck.models import Bar
from parayabuck.strategy.vwap import VWAPReversion
from tests._helpers import classify_signal, flat_position


def _intraday_bars(closes, highs=None, lows=None, volumes=None, start_hour=9, start_minute=45):
    """Build intraday bars explicitly within US session hours (9:30 ET)."""
    bars = []
    base = datetime(2025, 6, 2, start_hour, start_minute, tzinfo=UTC).astimezone(
        ZoneInfo("America/New_York")
    )
    # If we cross 16:00, stop — keep all bars within one session.
    for i, c in enumerate(closes):
        ts = base + timedelta(minutes=15 * i)
        h = highs[i] if highs else c * 1.001
        lo = lows[i] if lows else c * 0.999
        v = volumes[i] if volumes else 1000
        bars.append(
            Bar(
                timestamp=ts,
                symbol="TEST",
                timeframe="15m",
                open=c,
                high=h,
                low=lo,
                close=c,
                volume=v,
            )
        )
    return bars


def test_vwap_no_signal_during_warmup():
    bars = _intraday_bars([100.0] * 5)
    strat = VWAPReversion(deviation_threshold=1.0)
    pos = flat_position()
    last = None
    for b in bars:
        o = strat.on_bar(b, pos)
        if o is not None:
            last = o
    assert last is None


def test_vwap_session_vwap_accumulates_intraday():
    """VWAP should be a cumulative session average, not a rolling-window one.
    The first bar sets the VWAP; later bars pull it toward their typical
    price weighted by volume.
    """
    bars = _intraday_bars(
        closes=[100.0, 102.0, 98.0, 101.0, 99.0, 103.0, 97.0, 100.0],
        volumes=[1000] * 8,
    )
    strat = VWAPReversion(deviation_threshold=10.0)  # high threshold -> no entries
    pos = flat_position()
    for b in bars:
        strat.on_bar(b, pos)
    # Expected session VWAP: mean of typical prices (H+L+C)/3.
    # With our high/low ±0.1% spread, tp ≈ close. So VWAP ≈ mean of closes.
    expected = sum(b.close for b in bars) / len(bars)
    assert strat._current_vwap == pytest_approx(expected, rel=0.01)


def pytest_approx(value, rel=1e-3):
    import pytest

    return pytest.approx(value, rel=rel)


def test_vwap_resets_on_new_session():
    """VWAP should reset at the start of a new trading day."""
    bars_day1 = _intraday_bars([100.0, 102.0, 98.0, 101.0], volumes=[1000] * 4)
    # Day 2 starts at 9:45 the next day.
    bars_day2 = _intraday_bars(
        [50.0, 52.0, 48.0, 51.0],
        volumes=[1000] * 4,
        start_hour=9,
        start_minute=45,
    )
    # Shift day 2 timestamps by +1 day so the strategy sees a new session.
    from parayabuck.models import Bar

    bars_day2 = [
        Bar(
            timestamp=b.timestamp + timedelta(days=1),
            symbol=b.symbol,
            timeframe=b.timeframe,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
        for b in bars_day2
    ]
    strat = VWAPReversion(deviation_threshold=10.0)
    pos = flat_position()
    for b in bars_day1:
        strat.on_bar(b, pos)
    vwap_day1 = strat._current_vwap
    # Process day 2 — VWAP should reset and accumulate from scratch.
    for b in bars_day2:
        strat.on_bar(b, pos)
    # Day 2 closes are much lower (50ish), so VWAP should be near 50, not 100.
    assert strat._current_vwap < 60
    assert vwap_day1 > 90  # sanity


def test_vwap_buys_when_price_far_below_vwap():
    """A sharp drop below session VWAP with RSI < 40 should trigger BUY."""
    # Start near 100, then drop hard to 95 — that's > 5% below VWAP.
    closes = [100.0] * 20 + [99.0, 98.0, 96.0, 95.0, 94.5]
    bars = _intraday_bars(closes, volumes=[1000] * len(closes))
    strat = VWAPReversion(deviation_threshold=1.0, rsi_buy_max=45, min_session_bars=3)
    pos = flat_position()
    last = None
    for b in bars:
        o = strat.on_bar(b, pos)
        if o is not None:
            last = o
    assert classify_signal(last) == "buy"
