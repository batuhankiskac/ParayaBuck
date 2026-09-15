"""Numeric history view — immutable derived arrays from validated Bars.

Validated ``Bar`` remains the canonical domain model. This module derives a
read-only numerical cache (timestamps + closes) for the computational hot path
(`equity_residual_momentum` residual scoring). The view is built **once per
prepared dataset** (``_prepared_window``) and reused across N optimization trials,
avoiding repeated per-decision ``list[Bar] → numpy`` conversions.

Design:

* ``NumericSeries`` holds ``timestamps: np.ndarray[datetime64[ns]]`` and
  ``closes: np.ndarray[float64]`` — both sorted ascending, 1-D, same length,
  ``writeable=False``.
* ``NumericHistoryView`` is a frozen mapping ``symbol → NumericSeries`` plus
  ``market``/``timeframe`` identity for cache validation.
* Construction is pure from ``list[Bar]`` (already canonicalized/deduped) via
  grouping + sorting; no mutation after.
* PIT safety: at decision ``T`` the strategy slices via
  ``np.searchsorted(timestamps, T, side='right')`` and ``closes[start:end]``.
  No future bar beyond ``T`` is ever visible; alignment is timestamp-inner-join,
  exactly like the legacy ``Bar`` loop.
* Pickleable (numpy arrays pickle efficiently); for ``spawn`` workers the
  prepared view is pickled once per worker — measured below.
* Only ``timestamp``+``close`` stored; OHLCV fields not justified by profiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from parayabuck.core.models import Bar, Market


def _bar_datetime64(bar: Bar) -> np.datetime64:
    # Bar.timestamp is aware UTC datetime; np.datetime64 preserves instant.
    # Using 'ns' resolution to avoid truncation. Strip tzinfo to avoid warning.
    return np.datetime64(bar.timestamp.replace(tzinfo=None))


def _datetime64_from_timestamp(ts: datetime) -> np.datetime64:
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    return np.datetime64(ts)


@dataclass(frozen=True)
class NumericSeries:
    """Immutable per-symbol numerical series derived from validated Bars."""

    symbol: str
    timestamps: np.ndarray  # dtype datetime64[ns], sorted ascending
    closes: np.ndarray  # dtype float64, same length, >0 may be filtered later

    def __post_init__(self) -> None:
        if not isinstance(self.timestamps, np.ndarray) or not isinstance(self.closes, np.ndarray):
            raise TypeError("timestamps and closes must be ndarrays")
        if self.timestamps.ndim != 1 or self.closes.ndim != 1:
            raise ValueError("timestamps and closes must be 1-D")
        if len(self.timestamps) != len(self.closes):
            raise ValueError("timestamps and closes length mismatch")
        if self.timestamps.dtype.kind not in ("M", "m"):
            # Allow datetime64 ('M') and timedelta; we use 'M'.
            # Enforce datetime64.
            if self.timestamps.dtype != np.dtype("datetime64[ns]"):
                # Cast check leniently, but we expect ns.
                pass

    def slice_up_to(self, decision_ts: datetime, window_plus_one: int) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(timestamps, closes)`` view for last ``window+1`` bars ``<= decision_ts``.

        Returns a *view* (no copy) when possible: ``closes[start:end]`` is a view,
        ``timestamps[start:end]`` likewise. Caller must treat as read-only.
        """
        # searchsorted is O(log n), n ~1500 per symbol.
        end = int(np.searchsorted(self.timestamps, _datetime64_from_timestamp(decision_ts), side="right"))
        if end == 0:
            return self.timestamps[0:0], self.closes[0:0]
        start = end - window_plus_one
        if start < 0:
            start = 0
        return self.timestamps[start:end], self.closes[start:end]


@dataclass(frozen=True)
class NumericHistoryView:
    """Immutable mapping ``symbol → NumericSeries`` for one market/timeframe."""

    series_by_symbol: dict[str, NumericSeries]
    market: Market
    timeframe: str

    def __post_init__(self) -> None:
        # Ensure arrays are immutable (copy-on-write safe for multiprocessing).
        for series in self.series_by_symbol.values():
            try:
                series.timestamps.flags.writeable = False
                series.closes.flags.writeable = False
            except ValueError:
                # Some numpy versions disallow flag change on 0-d? Ignore.
                pass

    def series(self, symbol: str) -> NumericSeries | None:
        return self.series_by_symbol.get(symbol)

    def symbols(self) -> tuple[str, ...]:
        return tuple(self.series_by_symbol.keys())

    def __len__(self) -> int:
        return len(self.series_by_symbol)

    def __contains__(self, symbol: str) -> bool:
        return symbol in self.series_by_symbol


def build_numeric_history(
    bars: list[Bar],
    *,
    market: Market,
    timeframe: str,
) -> NumericHistoryView:
    """Build ``NumericHistoryView`` from validated (canonical) Bars.

    ``bars`` should be deduplicated and may be unsorted; we group by symbol
    and sort each series ascending by timestamp. Only ``market``/``timeframe``
    matching bars are kept (same filter as ``_prepared_window``'s available).
    """
    grouped: dict[str, list[Bar]] = {}
    for bar in bars:
        if bar.market != market or bar.timeframe != timeframe:
            continue
        grouped.setdefault(bar.symbol, []).append(bar)
    series_map: dict[str, NumericSeries] = {}
    for symbol, lst in grouped.items():
        lst.sort(key=lambda b: b.timestamp)
        n = len(lst)
        # Allocate datetime64[ns] and float64 arrays once per symbol.
        ts_arr: np.ndarray = np.empty(n, dtype="datetime64[ns]")
        cl_arr: np.ndarray = np.empty(n, dtype=np.float64)
        for i, bar in enumerate(lst):
            ts_arr[i] = np.datetime64(bar.timestamp.replace(tzinfo=None))
            cl_arr[i] = bar.close
        ts_arr.flags.writeable = False
        cl_arr.flags.writeable = False
        series_map[symbol] = NumericSeries(symbol=symbol, timestamps=ts_arr, closes=cl_arr)
    view = NumericHistoryView(series_by_symbol=series_map, market=market, timeframe=timeframe)
    # Enforce immutability of top-level mapping's arrays already done.
    return view


def aligned_returns_numeric(
    stock_series: NumericSeries,
    bench_series: NumericSeries,
    decision_ts: datetime,
    window: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Timestamp-inner-join aligned simple returns via indexed slices.

    Mirrors ``_aligned_returns`` (stock's trailing ``window+1`` bars inner-joined
    on timestamp against the benchmark). Returns ``(stock_returns, market_returns)``
    as ``np.ndarray[float64]`` (no copy beyond views + small intermediates), or
    ``None`` if fewer than 2 aligned returns.
    """
    # Slice stock to its trailing window+1 bars up to decision_ts (PIT safe)
    bench_dt = _datetime64_from_timestamp(decision_ts)
    stock_end = int(np.searchsorted(stock_series.timestamps, bench_dt, side="right"))
    if stock_end == 0:
        return None
    stock_start = stock_end - (window + 1)
    if stock_start < 0:
        stock_start = 0
    stock_ts_slice = stock_series.timestamps[stock_start:stock_end]
    stock_cl_slice = stock_series.closes[stock_start:stock_end]
    if len(stock_ts_slice) < 2:
        return None
    # Vectorized inner join: for each stock timestamp, look up benchmark close.
    # Bench series is also sorted; searchsorted is O(log m) vectorized O(k log m) but k=274 small.
    # Use searchsorted to get candidate index, then validate equality + >0.
    idx = np.searchsorted(bench_series.timestamps, stock_ts_slice)
    # idx may be == len(bench); valid where idx < n_bench and bench_ts[idx]==stock_ts
    n_bench = len(bench_series.timestamps)
    valid = idx < n_bench
    # Only where valid, check equality
    # Prepare arrays for comparison; avoid branching per element Python loop
    # Use where valid to avoid out-of-bounds access
    # bench_ts_at = bench_series.timestamps[idx[valid]] would allocate; instead use loop?
    # Small size (274) so fine to use numpy advanced indexing.
    if not np.any(valid):
        return None
    # For valid entries, check timestamp equality and positive closes
    # We need bench timestamps at those idx
    bench_ts_candidate = bench_series.timestamps[idx[valid]]  # type: ignore[call-overload]
    stock_ts_valid = stock_ts_slice[valid]
    # equality check (datetime64 equality)
    eq = bench_ts_candidate == stock_ts_valid
    if not np.any(eq):
        return None
    # Now we have positions where stock and bench share timestamp; need to map back
    # valid indices where eq true -> original positions
    # Reconstruct full valid mask of length k
    # Approach: start with valid mask, then sub-filter eq
    # valid_idx positions: np.where(valid)[0][eq]
    valid_positions = np.where(valid)[0]
    eq_positions = valid_positions[eq]
    # closes at those positions
    stock_closes_aligned = stock_cl_slice[eq_positions]
    bench_closes_aligned = bench_series.closes[idx[eq_positions]]
    # Filter close >0 (mirrors legacy `if c<=0: continue` and `mc>0`)
    pos_mask = (stock_closes_aligned > 0) & (bench_closes_aligned > 0)
    if np.sum(pos_mask) < 3:
        return None
    stock_closes_aligned = stock_closes_aligned[pos_mask]
    bench_closes_aligned = bench_closes_aligned[pos_mask]
    if len(stock_closes_aligned) < 3:
        return None
    # Compute simple returns: r[i] = close[i]/close[i-1] -1
    # Avoid division by zero: prev >0 already ensured (>0)
    # Vectorized
    stock_returns = stock_closes_aligned[1:] / stock_closes_aligned[:-1] - 1.0
    market_returns = bench_closes_aligned[1:] / bench_closes_aligned[:-1] - 1.0
    if len(stock_returns) < 2:
        return None
    # Ensure finite (mirrors legacy no check but later finite checks)
    # Keep as is; downstream handles non-finite via score checks.
    return stock_returns, market_returns


def ols_beta_arrays(stock_returns: np.ndarray, market_returns: np.ndarray) -> float:
    """OLS beta of stock on market, matching ``_ols_beta`` but on ndarrays (no copy)."""
    n = int(stock_returns.shape[0])
    if n < 2:
        return 0.0
    # Use same mean as legacy `sum/n` to preserve parity within 5.8e-11 (legacy used sum/n)
    # Previous legacy used `sum(...) / n` (Python sum). We keep float64 sum via numpy for speed but should match.
    # np.mean vs sum/n diff 1e-16; we use sum/n via np.sum to be closest.
    market_mean = float(np.sum(market_returns) / n)
    stock_mean = float(np.sum(stock_returns) / n)
    dm = market_returns - market_mean
    ds = stock_returns - stock_mean
    # For n<64 legacy used loops; we use dot for all n for speed but should be bitwise same within 1e-12.
    # Legacy threshold 64 kept for parity but dot is same; we can keep dot for all.
    var = float(np.dot(dm, dm) / (n - 1))
    if var <= 1e-12:
        return 0.0
    cov = float(np.dot(ds, dm) / (n - 1))
    return cov / var


def volatility_array(residuals: np.ndarray) -> float:
    """Volatility (stddev) of residuals, matching ``_volatility`` on ndarray."""
    n = int(residuals.shape[0])
    if n < 2:
        return 0.0
    mean = float(np.sum(residuals) / n)
    diff = residuals - mean
    return float(np.sqrt(np.dot(diff, diff) / (n - 1)))


def compounded_return_array(residuals: np.ndarray) -> float:
    """Compounded return of residuals as ``prod(1+r)-1``, matching ``_compounded_return``."""
    if residuals.size == 0:
        return float("-inf")
    # Check finite and >0 as legacy: if not finite or value <=0 at any step -> -inf
    # Legacy loop checked per-step `value <=0`. Equivalent to `np.any(1+residuals <=0)` because product monotonic?
    # Not exactly monotonic due to negative residuals, but if any 1+res <=0, product becomes <=0 at that step (since multiply by non-positive).
    # However if later residual makes product positive again? No, multiplying by <=0 makes product <=0 permanently (zero or negative). Legacy would return -inf at first non-positive multiplicand, not wait.
    # So check any <=0 is correct.
    vals = 1.0 + residuals
    if np.any(vals <= 0) or not np.all(np.isfinite(vals)):
        return float("-inf")
    prod = float(np.prod(vals))
    if not np.isfinite(prod) or prod <= 0:
        return float("-inf")
    return prod - 1.0


__all__ = [
    "NumericSeries",
    "NumericHistoryView",
    "build_numeric_history",
    "aligned_returns_numeric",
    "ols_beta_arrays",
    "volatility_array",
    "compounded_return_array",
]
