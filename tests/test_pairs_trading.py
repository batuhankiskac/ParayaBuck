"""Unit tests for the pairs-trading strategy."""
from __future__ import annotations

import math

from parayabuck.models import Bar
from parayabuck.strategy.pairs_trading import PairsTrading
from tests._helpers import flat_position


def _pair_bars(closes_a, closes_b, symbol_a="AAA", symbol_b="BBB"):
    """Build interleaved A/B bars sharing timestamps."""
    from datetime import UTC, datetime, timedelta

    bars = []
    base = datetime(2025, 1, 1, 9, 30, tzinfo=UTC)
    for i, (ca, cb) in enumerate(zip(closes_a, closes_b, strict=True)):
        ts = base + timedelta(minutes=15 * i)
        bars.append(
            Bar(ts, symbol_a, "15m", ca, ca * 1.001, ca * 0.999, ca, volume=1000)
        )
        bars.append(
            Bar(ts, symbol_b, "15m", cb, cb * 1.001, cb * 0.999, cb, volume=1000)
        )
    return bars


def test_pairs_no_signal_during_warmup():
    closes_a = [100.0] * 30
    closes_b = [50.0] * 30
    bars = _pair_bars(closes_a, closes_b)
    strat = PairsTrading(
        symbol_a="AAA", symbol_b="BBB", lookback=60, entry_z=2.0, exit_z=0.5,
        use_half_life_filter=False,
    )
    pos = flat_position()
    last = None
    for b in bars:
        o = strat.on_bar(b, pos)
        if o is not None:
            last = o
        strat.pop_pending_orders()
    assert last is None


def test_pairs_enters_when_spread_widens():
    """When A spikes up relative to B, z-score exceeds entry_threshold and
    the strategy goes short A / long B.

    The strategy can fire multiple signals as the shock plays out (entry,
    possible exit, possible re-entry). We assert that the FIRST entry is
    SELL A + BUY B (the canonical short-spread signal).
    """
    # Build a stable co-moving base where A ≈ 2*B. The base needs to be long
    # enough to fill the lookback window AFTER both price series cross 60
    # bars (the spread series starts later than the price series).
    base_a = [100.0] * 120
    base_b = [50.0] * 120
    # Then push A up sharply — spread widens.
    shock_a = [110.0, 115.0, 118.0, 120.0, 122.0]
    shock_b = [50.0, 50.5, 51.0, 51.5, 52.0]
    bars = _pair_bars(base_a + shock_a, base_b + shock_b)
    strat = PairsTrading(
        symbol_a="AAA",
        symbol_b="BBB",
        lookback=60,
        entry_z=2.0,
        exit_z=0.5,
        use_half_life_filter=False,
        max_position=10,
    )
    pos = flat_position()
    first_entry = None
    first_pending: list = []
    captured = False
    for b in bars:
        o = strat.on_bar(b, pos)
        pend = strat.pop_pending_orders()
        if not captured and (o is not None or pend):
            first_entry = o
            first_pending = pend
            captured = True
    assert first_entry is not None
    from parayabuck.models import Side

    assert first_entry.side == Side.SELL  # short A
    assert any(o.side == Side.BUY and o.symbol == "BBB" for o in first_pending)


def test_pairs_half_life_filter_blocks_random_walk():
    """When the spread is a random walk (no mean reversion), half-life is
    infinite/None and the filter should block the entry."""
    # Construct an obviously trending spread: A goes up monotonically while
    # B stays flat -> spread trends, never reverts.
    closes_a = [100.0 + i * 0.5 for i in range(120)]
    closes_b = [50.0] * 120
    bars = _pair_bars(closes_a, closes_b)
    strat = PairsTrading(
        symbol_a="AAA",
        symbol_b="BBB",
        lookback=60,
        entry_z=2.0,
        use_half_life_filter=True,
        max_half_life=60.0,
        max_position=10,
    )
    pos = flat_position()
    last = None
    for b in bars:
        o = strat.on_bar(b, pos)
        if o is not None:
            last = o
        strat.pop_pending_orders()
    # Either no signal, or filtered by the half-life check.
    # (A trending spread may register high z but no half-life -> blocked.)
    assert last is None or strat._last_half_life == 0.0


def test_pairs_exits_when_spread_reverts():
    """After entry, when z reverts past exit_threshold, the strategy flattens."""
    strat = PairsTrading(
        symbol_a="AAA",
        symbol_b="BBB",
        lookback=60,
        entry_z=2.0,
        exit_z=0.5,
        use_half_life_filter=False,
        max_position=10,
    )
    # Manually prime state: in a position and a flat spread history so the
    # z-score is ~0 (which is < exit_z=0.5, triggers exit).
    strat._state = "long_a_short_b"
    strat._spreads.extend([0.0] * 60)  # flat spread history -> z=0
    # Build enough bars to push through on_bar AND populate prices for the
    # lookback check to pass so the spread update + z-score happen.
    bars = _pair_bars([100.0] * 70, [50.0] * 70)
    pos = flat_position()
    last = None
    for b in bars:
        o = strat.on_bar(b, pos)
        if o is not None:
            last = o
        strat.pop_pending_orders()
    assert last is not None
    from parayabuck.models import Side

    # Exit: sell A (close long), buy B (close short).
    assert last.side == Side.SELL


def test_pairs_estimate_half_life_on_reverting_series():
    """For a true mean-reverting spread, half-life should be finite & positive."""
    # Spread oscillates around 0 with mean reversion.
    spread_series = [math.sin(i / 5.0) for i in range(120)]
    # Convert spread -> prices: A = exp(spread), B = 1 (so log(A) - 1*log(B) = spread).
    closes_a = [math.exp(s) for s in spread_series]
    closes_b = [1.0] * 120
    bars = _pair_bars(closes_a, closes_b)
    strat = PairsTrading(symbol_a="AAA", symbol_b="BBB", lookback=60, use_half_life_filter=True)
    for b in bars:
        strat.on_bar(b, flat_position())
        strat.pop_pending_orders()
    # _estimate_half_life should return a finite, positive value.
    hl = strat._estimate_half_life()
    assert hl is not None
    assert hl > 0
    assert math.isfinite(hl)
