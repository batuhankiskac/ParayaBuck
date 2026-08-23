"""Split-adjustment parity between BACKTEST signal data and PAPER/LIVE.

The equity strategy's signal history must sit on ONE explicit corporate-
action basis everywhere:

* backtest research CSVs: split-adjusted prices (+ explicit dividends);
* runtime warmup: ``adjustment="splits"`` seeds the strategy history while
  RAW seeds the execution-side market state;
* live continuation: RAW stream bars are divided by a cumulative split
  factor and late detections rewrite exactly the unscaled window.

A 4:1 split must never appear as a -75% strategy "return".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data.adjustment import (
    SPLIT_RATIO_TOLERANCE,
    scale_bar,
    scale_history_since,
    split_ratio,
)
from parayabuck.portfolio.equity_residual_momentum import _aligned_returns
from parayabuck.portfolio.runtime_service import PortfolioRuntime


def _bar(symbol: str, timestamp: datetime, close: float) -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1d",
        close,
        close,
        close,
        close,
        1_000_000,
        Market.EQUITY,
    )


def _series(symbol: str, closes: list[float], start: datetime | None = None) -> list[Bar]:
    start = start or datetime(2025, 6, 2, tzinfo=UTC)
    return [
        _bar(symbol, start + timedelta(days=index), close) for index, close in enumerate(closes)
    ]


# -- pure bridge math --------------------------------------------------------


def test_split_ratio_detects_forward_split() -> None:
    """Raw tail reprices 400 -> 100 across a 4:1 split; the provider's
    splits-adjusted tail is continuous at 100: ratio must be exactly 4."""
    timestamps = [datetime(2025, 6, 9, tzinfo=UTC), datetime(2025, 6, 10, tzinfo=UTC)]
    raw = [_bar("AAA", ts, close) for ts, close in zip(timestamps, (400.0, 100.0))]
    adjusted = [_bar("AAA", ts, 100.0) for ts in timestamps]
    detected = split_ratio(raw, adjusted)
    assert detected is not None
    # The measurement lives on the OLDEST common bar (post-split bars have
    # raw == adjusted by construction).
    timestamp, ratio = detected
    assert timestamp == timestamps[0]
    assert ratio == pytest.approx(4.0)


def test_split_ratio_ignores_non_splits_and_missing_overlap() -> None:
    closes = [100.0 * (1 + 0.01 * index) for index in range(5)]
    assert split_ratio(_series("AAA", closes), _series("AAA", closes)) is None
    # Within tolerance of 1.0: provider rounding noise, not a corporate action.
    noisy = [close * (1 + SPLIT_RATIO_TOLERANCE / 2) for close in closes]
    assert split_ratio(_series("AAA", closes), _series("AAA", noisy)) is None
    # No common completed bar -> nothing to measure.
    shifted = [
        _bar("AAA", datetime(2025, 7, 1, tzinfo=UTC) + timedelta(days=index), close)
        for index, close in enumerate(closes)
    ]
    assert split_ratio(_series("AAA", closes), shifted) is None


def test_scale_history_since_rewrites_only_unscaled_window() -> None:
    # The stored basis predates the split (larger scale): unscaled raw bars
    # appended since the last check are raised ONTO that basis (multiplied).
    bars = tuple(_series("AAA", [100.0, 101.0, 101.0, 102.0]))
    since = bars[2].timestamp
    adjusted = scale_history_since(bars, 4.0, since)
    assert [bar.close for bar in adjusted[:2]] == [100.0, 101.0]  # warmup basis kept
    assert [bar.close for bar in adjusted[2:]] == [404.0, 408.0]  # unscaled raw fixed
    # Volume and identity are preserved.
    assert all(after.volume == before.volume for before, after in zip(bars, adjusted))
    assert all(after.symbol == "AAA" for after in adjusted)


def test_scale_bar_multiplies_ohlc_only() -> None:
    bar = Bar(
        datetime(2025, 6, 2, tzinfo=UTC),
        "AAA",
        "1d",
        100.0,
        102.5,
        98.75,
        100.0,
        1_000_000,
        Market.EQUITY,
    )
    scaled = scale_bar(bar, 4.0)
    assert (scaled.open, scaled.high, scaled.low, scaled.close) == (400.0, 410.0, 395.0, 400.0)
    assert scaled.volume == 1_000_000
    assert scale_bar(bar, 1.0) is bar  # no-op fast path


# -- runtime wiring ----------------------------------------------------------


def _bare_equity_runtime(symbols: tuple[str, ...] = ("AAA",)) -> PortfolioRuntime:
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.EQUITY
    runtime.strategy = SimpleNamespace(timeframe="1d")
    runtime.previous_timestamp = None
    runtime.symbols = symbols
    runtime.history = {symbol: [] for symbol in symbols}
    runtime.signal_split_factor = dict.fromkeys(symbols, 1.0)
    runtime.unscaled_since = dict.fromkeys(symbols)
    return runtime


def test_append_signal_bar_records_unscaled_window() -> None:
    runtime = _bare_equity_runtime()
    bars = _series("AAA", [100.0, 101.0])
    for bar in bars:
        runtime._append_signal_bar(bar)
    assert [bar.close for bar in runtime.history["AAA"]] == [100.0, 101.0]
    assert runtime.unscaled_since["AAA"] == bars[0].timestamp
    # Duplicate/late timestamps never append twice.
    runtime._append_signal_bar(bars[-1])
    assert len(runtime.history["AAA"]) == 2


def test_apply_split_factor_rewrites_window_and_raises_factor() -> None:
    runtime = _bare_equity_runtime()
    # Warmup captured the PRE-split provider basis: economically-flat name
    # trading at 400 before its 4:1 split.
    warmup = _series("AAA", [400.0])
    runtime.seed(warmup)
    runtime.unscaled_since["AAA"] = None
    # Post-split the same economics trade at ~101 as-traded (raw stream).
    stream = _series("AAA", [101.0], start=datetime(2025, 6, 3, tzinfo=UTC))
    for bar in stream:
        runtime._append_signal_bar(bar)  # entered unscaled (factor was 1.0)
    assert runtime.history["AAA"][-1].close == pytest.approx(101.0)
    rewritten = runtime.apply_split_factor("AAA", 4.0)
    assert rewritten == 1
    # The unscaled stream bar is raised onto the warmup basis (x4); the
    # warmed-up bar is untouched.
    assert [bar.close for bar in runtime.history["AAA"]] == [400.0, 404.0]
    assert runtime.signal_split_factor["AAA"] == pytest.approx(4.0)
    assert runtime.unscaled_since["AAA"] is None
    # Future appends are scaled by the raised factor immediately.
    newer = _series("AAA", [102.0], start=datetime(2025, 6, 4, tzinfo=UTC))[0]
    runtime._append_signal_bar(newer)
    assert runtime.history["AAA"][-1].close == pytest.approx(408.0)
    # Re-measuring the SAME state is idempotent; a larger cumulative
    # measurement compounds via the increment.
    runtime.apply_split_factor("AAA", 4.0)
    assert runtime.signal_split_factor["AAA"] == pytest.approx(4.0)
    runtime.apply_split_factor("AAA", 8.0)
    assert runtime.signal_split_factor["AAA"] == pytest.approx(8.0)
    # Unknown symbol and crypto-style empty factor map are safe no-ops.
    assert runtime.apply_split_factor("NOPE", 4.0) == 0


def test_crypto_style_runtime_has_no_factor_maps() -> None:
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.CRYPTO
    runtime.symbols = ("BTC/USD",)
    runtime.history = {"BTC/USD": []}
    runtime.signal_split_factor = {}
    runtime.unscaled_since = {}
    bar = _bar("BTC/USD", datetime(2025, 6, 2, tzinfo=UTC), 50_000.0)
    bar = Bar(
        bar.timestamp,
        bar.symbol,
        bar.timeframe,
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.volume,
        Market.CRYPTO,
    )
    runtime._append_signal_bar(bar)
    assert runtime.history["BTC/USD"][-1].close == 50_000.0
    assert runtime.apply_split_factor("BTC/USD", 4.0) == 0


# -- strategy-level parity: no fake split "return" ---------------------------


def test_bridged_signal_series_never_sees_split_as_return() -> None:
    """The deterministic synthetic-split regression: 4:1 split on an
    economically flat name. A consistently bridged (split-adjusted) history
    yields only plausible daily returns across the event; an unbridged one
    fabricates a -75% "return" at the split."""
    start = datetime(2024, 6, 3, tzinfo=UTC)
    economic = [400.0]
    for _ in range(29):
        economic.append(economic[-1] * 1.0002)
    raw = list(economic)
    for index in range(15, 30):
        raw[index] = economic[index] / 4.0  # as-traded after the 4:1 split
    dates = [start + timedelta(days=index) for index in range(30)]
    raw_bars = [_bar("AAA", date, close) for date, close in zip(dates, raw)]
    # Bridged series: post-split bars multiplied by the cumulative ratio 4
    # onto the warmup basis (pre-split warmup bars were already on it).
    bridged_closes = [close * (4.0 if index >= 15 else 1.0) for index, close in enumerate(raw)]
    bridged_bars = [_bar("AAA", date, close) for date, close in zip(dates, bridged_closes)]
    market = {bar.timestamp: 500.0 + index for index, bar in enumerate(raw_bars)}

    unbridged = _aligned_returns(tuple(raw_bars), market, window=273)
    bridged = _aligned_returns(tuple(bridged_bars), market, window=273)
    assert unbridged is not None and bridged is not None
    worst_unbridged = min(unbridged[0])
    worst_bridged = min(bridged[0])
    assert worst_unbridged == pytest.approx(-0.75, abs=5e-3)  # the fabricated crash
    assert worst_bridged > -0.05  # economically plausible daily move only
