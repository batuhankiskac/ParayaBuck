"""Corporate-action adjustment bridge between SIGNAL and EXECUTION price bases.

The equity residual-momentum strategy scores on SPLIT-ADJUSTED daily bars
(one explicit corporate-action basis across BACKTEST research data, PAPER
warmup and LIVE continuation), while streamed intraday aggregation produces
RAW as-traded prices for execution state. A stock split occurring while the
engine lives would otherwise weld a fake -50%/-75% "return" into the
strategy's signal history at the seam.

Contract implemented here:

* warmup seeds the strategy's signal history on the ``splits`` basis;
* every bar appended to that history from the RAW stream is MULTIPLIED by
  a per-symbol cumulative split factor (1.0 until a split is detected):
  the stored basis predates the split, so its magnitude is the LARGER
  pre-split scale;
* a periodic reconciler compares fresh raw vs splits tails from the
  provider; their ratio IS the cumulative split factor since the warmup
  download (Alpaca rebases its splits series retroactively, so
  ``raw_close / splits_close`` on the newest completed bar equals the
  product of split ratios that happened in between);
* bars appended unscaled between the split date and its detection are
  rescaled once, deterministically, by the same ratio.

All functions here are pure and deterministic so the bridge is testable
without network or engine machinery.
"""

from __future__ import annotations

from datetime import datetime

from parayabuck.core.models import Bar

# A measured raw/splits close ratio within this band of 1.0 is treated as
# "no split" (provider rounding noise only). Real splits move the ratio far
# outside it; reverse splits show up as ratios below 1.
SPLIT_RATIO_TOLERANCE = 0.005


def scale_bar(bar: Bar, factor: float) -> Bar:
    """Multiply one bar's OHLC by ``factor`` (volume untouched)."""
    if factor == 1.0:
        return bar
    return bar.__class__(
        timestamp=bar.timestamp,
        symbol=bar.symbol,
        timeframe=bar.timeframe,
        open=bar.open * factor,
        high=bar.high * factor,
        low=bar.low * factor,
        close=bar.close * factor,
        volume=bar.volume,
        market=bar.market,
    )


def scale_history_since(bars: tuple[Bar, ...], factor: float, since: datetime) -> tuple[Bar, ...]:
    """Multiply OHLC of every bar at/after ``since`` by ``factor``.

    Used when a split is detected AFTER unscaled raw bars already entered the
    signal history: those bars (and only those — earlier bars are already on
    the warmup's adjusted basis) are rewritten once onto the stored basis,
    oldest-first order preserved.
    """
    if factor == 1.0:
        return bars
    return tuple(scale_bar(bar, factor) if bar.timestamp >= since else bar for bar in bars)


def split_ratio(
    raw_bars: list[Bar] | tuple[Bar, ...],
    splits_bars: list[Bar] | tuple[Bar, ...],
    *,
    tolerance: float = SPLIT_RATIO_TOLERANCE,
) -> tuple[datetime, float] | None:
    """Cumulative split factor since the signal basis was downloaded.

    A provider's splits-adjusted series EQUALS the raw series on every bar
    after the most recent split ex-date and diverges only on earlier bars,
    so the informative measurement sits on the OLDEST commonly completed
    bar of the two fresh tails (the reconciler's lookback window must reach
    before the suspected ex-date — hourly checks against a multi-day window
    trivially satisfy this). Returns ``(timestamp, ratio)`` with ``ratio =
    raw_close / splits_close`` (>1 after forward splits) measured there, or
    None when no common completed bar exists or the ratio is within
    ``tolerance`` of 1 (provider rounding noise, not a corporate action).
    """
    adjusted = {bar.timestamp: bar.close for bar in splits_bars}
    candidates = [
        (bar.timestamp, bar.close)
        for bar in raw_bars
        if bar.timestamp in adjusted and bar.close > 0 and adjusted[bar.timestamp] > 0
    ]
    if not candidates:
        return None
    timestamp, raw_close = min(candidates, key=lambda item: item[0])
    ratio = raw_close / adjusted[timestamp]
    if abs(ratio - 1.0) <= tolerance:
        return None
    return timestamp, ratio


__all__ = ["SPLIT_RATIO_TOLERANCE", "scale_bar", "scale_history_since", "split_ratio"]
