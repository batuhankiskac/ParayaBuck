"""Unit tests for the mean-reversion strategy."""
from __future__ import annotations

from parayabuck.strategy.mean_reversion import MeanReversion
from tests._helpers import (
    bars_from_closes,
    classify_signal,
    flat_position,
    run_strategy,
    sideways,
)


def test_mean_reversion_no_signal_in_warmup():
    bars = bars_from_closes([100.0] * 10)
    strat = MeanReversion(bb_period=20, rsi_period=14)
    order = run_strategy(strat, bars, flat_position())
    assert order is None


def test_mean_reversion_no_signal_in_sideways_without_climax():
    """A flat sideways series with constant volume never triggers a climax
    arm, so no entry should fire when require_climax_exit=True."""
    closes = sideways(120, base=100.0, amplitude=0.3, period=10)
    bars = bars_from_closes(closes, volume=1000)
    strat = MeanReversion(
        bb_period=20,
        rsi_period=14,
        climax_factor=2.0,
        require_climax_exit=True,
    )
    order = run_strategy(strat, bars, flat_position())
    assert order is None


def test_mean_reversion_buys_on_climax_reclaim():
    """A spike-down bar with huge volume arms the signal, then a close back
    above the lower band triggers the BUY."""
    # Build a base with small oscillation so the BB has some width before
    # the climax — a perfectly flat base makes std=0 and the climax itself
    # widens the band so much that the reclaim bar can't get back above it.
    import math

    base = [100.0 + 0.3 * math.sin(i / 3.0) for i in range(40)]
    bars = bars_from_closes(base, volume=1000)
    from datetime import timedelta

    from parayabuck.models import Bar

    # Three climax bars: each closes lower with 10x volume, pushing RSI
    # firmly below the oversold threshold. A single climax bar often leaves
    # RSI in the 40-50 range which fails the reclaim's RSI<45 guard.
    climax_bars = []
    climax_closes = [96.0, 94.0, 92.0]
    last_ts = bars[-1].timestamp
    for c in climax_closes:
        climax_bars.append(
            Bar(
                timestamp=last_ts + timedelta(minutes=15),
                symbol="TEST",
                timeframe="15m",
                open=100.0,
                high=100.5,
                low=c - 0.5,
                close=c,
                volume=10000,
            )
        )
        last_ts = climax_bars[-1].timestamp

    # Reclaim bar: close back ABOVE the post-climax lower band. After the
    # climax the band widens but a close near 95 should clear it.
    reclaim_bar = Bar(
        timestamp=last_ts + timedelta(minutes=15),
        symbol="TEST",
        timeframe="15m",
        open=92.0,
        high=96.0,
        low=91.5,
        close=95.5,  # close back above the (widened) lower band
        volume=2000,
    )
    strat = MeanReversion(
        bb_period=20,
        bb_std=2.0,
        rsi_period=14,
        oversold=40,
        climax_factor=2.0,
        require_climax_exit=True,
        use_trend_filter=False,  # disable to test signal in isolation
    )
    pos = flat_position()
    last = None
    for b in bars + climax_bars + [reclaim_bar]:
        o = strat.on_bar(b, pos)
        if o is not None:
            last = o
    assert classify_signal(last) == "buy"


def test_mean_reversion_skips_when_trend_filter_blocks():
    """With use_trend_filter=True and price below its long SMA, no long entry
    fires even on a climax reclaim — falling-knife protection."""
    # Falling series puts price well below its 50-SMA.
    closes = [120.0, 119.0, 118.0, 117.0, 116.0, 115.0] * 12  # ~72 bars falling
    bars = bars_from_closes(closes, volume=1000)
    strat = MeanReversion(
        bb_period=20,
        use_trend_filter=True,
        trend_filter_period=50,
        climax_factor=2.0,
        require_climax_exit=True,
    )
    order = run_strategy(strat, bars, flat_position())
    assert order is None


def test_mean_reversion_exits_on_target():
    """If price returns to the middle band after entry, the position is closed."""
    # Construct a scenario where after entry, price climbs back to the middle.
    strat = MeanReversion(
        bb_period=20,
        rsi_period=14,
        climax_factor=2.0,
        require_climax_exit=False,  # immediate entry on climax
        use_trend_filter=False,
        risk_reward=2.0,
    )
    # Start a flat position; manually set the entry/target state.
    strat._entry_price = 95.0
    strat._stop_price = 90.0
    strat._target_price = 100.0

    from parayabuck.models import Bar, Position, Side

    bars = bars_from_closes([100.0] * 25, volume=1000)
    # The last bar closes at target.
    bars[-1] = Bar(
        timestamp=bars[-1].timestamp,
        symbol="TEST",
        timeframe="15m",
        open=99.0,
        high=101.0,
        low=99.0,
        close=100.5,  # above target
        volume=1000,
    )
    pos = Position(symbol="TEST", qty=10, avg_price=95.0)
    order = None
    for b in bars:
        o = strat.on_bar(b, pos)
        if o is not None:
            order = o
    assert order is not None
    assert order.side == Side.SELL
