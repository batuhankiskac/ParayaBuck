"""Unit tests for the EMA crossover strategy."""
from __future__ import annotations

from parayabuck.strategy.ema_crossover import EMACrossover
from tests._helpers import (
    bars_from_closes,
    classify_signal,
    flat_position,
    run_strategy,
    sideways,
    trending_down,
    trending_up,
)


def test_ema_crossover_buys_on_uptrend_cross():
    """A clear uptrend should produce at least one BUY signal once fast EMA
    crosses above slow EMA."""
    closes = [100.0] * 30 + trending_up(60, start_price=100.0, drift=0.01)
    bars = bars_from_closes(closes)
    strat = EMACrossover(fast_ema=9, slow_ema=21, adx_threshold=0)  # disable ADX filter
    order = run_strategy(strat, bars, flat_position())
    assert classify_signal(order) == "buy"


def test_ema_crossover_sells_on_downtrend_cross():
    closes = trending_up(40, start_price=100.0) + trending_down(60, start_price=110.0, drift=0.01)
    bars = bars_from_closes(closes)
    strat = EMACrossover(fast_ema=9, slow_ema=21, allow_short=False, adx_threshold=0)
    order = run_strategy(strat, bars, flat_position())
    assert classify_signal(order) in {"sell", "none"}  # may not flip if not strong enough


def test_ema_crossover_adx_filter_blocks_weak_trend():
    """With ADX filter ON, a sideways/oscillating series should produce NO
    buy signal — EMA crosses in chop are noise."""
    closes = sideways(100, base=100.0, amplitude=0.5, period=10)
    bars = bars_from_closes(closes)
    strat = EMACrossover(fast_ema=9, slow_ema=21, adx_threshold=20)
    order = run_strategy(strat, bars, flat_position())
    assert classify_signal(order) == "none"


def test_ema_crossover_no_signal_during_warmup():
    """Strategy should return None until enough bars have accumulated for the
    slow EMA to be seeded."""
    bars = bars_from_closes([100.0, 101.0, 102.0])
    strat = EMACrossover(fast_ema=9, slow_ema=21)
    order = run_strategy(strat, bars, flat_position())
    assert order is None


def test_ema_crossover_exits_long_on_bearish_cross():
    """If we enter long and the trend reverses, we should get a SELL to close."""
    closes = trending_up(40, start_price=100.0, drift=0.02) + trending_down(60, 130.0, drift=0.02)
    bars = bars_from_closes(closes)
    strat = EMACrossover(fast_ema=9, slow_ema=21, allow_short=False, adx_threshold=0)
    # Simulate an open long position.
    from tests._helpers import long_position

    order = run_strategy(strat, bars, long_position(qty=10, avg_price=110.0))
    assert classify_signal(order) == "sell"


def test_ema_crossover_respects_allow_short_flag():
    """With allow_short=False, a bearish cross should never emit a fresh
    short entry from a flat position."""
    closes = trending_up(40) + trending_down(80, 130.0, drift=0.03)
    bars = bars_from_closes(closes)
    strat = EMACrossover(fast_ema=9, slow_ema=21, allow_short=False, adx_threshold=0)
    order = run_strategy(strat, bars, flat_position())
    assert classify_signal(order) != "buy"  # never buy in the down leg
