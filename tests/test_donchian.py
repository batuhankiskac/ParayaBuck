"""Unit tests for the Donchian breakout strategy."""
from __future__ import annotations

from parayabuck.strategy.donchian import DonchianBreakout
from tests._helpers import (
    bars_from_closes,
    classify_signal,
    flat_position,
    run_strategy,
    sideways,
    trending_up,
)


def test_donchian_no_signal_during_warmup():
    bars = bars_from_closes([100.0] * 10)
    strat = DonchianBreakout(entry_period=20, exit_period=10)
    assert run_strategy(strat, bars, flat_position()) is None


def test_donchian_breakout_buys_on_new_high():
    """A clear close above the previous 20-bar high should fire a BUY."""
    # 30 bars in a tight range, then a breakout bar.
    closes = [100.0] * 25 + [101.0, 101.5, 102.0, 102.5, 105.0]
    bars = bars_from_closes(closes, high_low_spread=0.005)
    strat = DonchianBreakout(entry_period=20, exit_period=10, atr_filter_mult=0.0)
    order = run_strategy(strat, bars, flat_position())
    assert classify_signal(order) == "buy"


def test_donchian_skips_breakout_without_atr():
    """With the ATR filter ON and a flat series (zero ATR), no entry fires."""
    closes = [100.0] * 30 + [101.0]  # breakout but no volatility
    bars = bars_from_closes(closes, high_low_spread=0.001)
    strat = DonchianBreakout(entry_period=20, exit_period=10, atr_filter_mult=0.5)
    order = run_strategy(strat, bars, flat_position())
    assert order is None  # ATR too low


def test_donchian_exits_on_opposite_channel_break():
    """After a long entry, a close below the exit channel triggers SELL."""
    strat = DonchianBreakout(entry_period=20, exit_period=10, atr_filter_mult=0.0)
    # Simulate holding a long; price falls below the 10-bar low.
    closes = trending_up(50, 100.0, 0.01) + sideways(20, base=140.0, amplitude=2.0, period=5)
    closes = closes + [closes[-1] - 20.0]  # big drop
    bars = bars_from_closes(closes, high_low_spread=0.005)
    from tests._helpers import long_position

    order = run_strategy(strat, bars, long_position(qty=10, avg_price=130.0))
    assert classify_signal(order) == "sell"


def test_donchian_short_only_when_allowed():
    """With allow_short=False, a downside breakout never emits a fresh short."""
    closes = [100.0] * 30 + [90.0, 88.0]
    bars = bars_from_closes(closes, high_low_spread=0.005)
    strat = DonchianBreakout(
        entry_period=20, exit_period=10, allow_short=False, atr_filter_mult=0.0
    )
    order = run_strategy(strat, bars, flat_position())
    assert classify_signal(order) != "sell"


def test_donchian_short_on_downside_breakout_when_allowed():
    closes = [100.0] * 30 + [90.0, 88.0, 85.0]
    bars = bars_from_closes(closes, high_low_spread=0.01)
    strat = DonchianBreakout(entry_period=20, exit_period=10, allow_short=True, atr_filter_mult=0.0)
    order = run_strategy(strat, bars, flat_position())
    assert classify_signal(order) == "sell"
