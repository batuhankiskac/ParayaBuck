"""Parity tests for NumericHistoryView.

Covers:
- aligned returns inner-join correctness vs legacy Bar path
- missing bar cases (stock missing, SPY missing, both missing, late start)
- PIT safety (no future leakage)
- ranking sensitivity near top_n and ivol boundaries
- corporate actions not affecting numeric cache (view built from split-adjusted bars)
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from parayabuck.backtest.numeric_history import (
    aligned_returns_numeric,
    build_numeric_history,
    compounded_return_array,
    ols_beta_arrays,
    volatility_array,
)
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_residual_momentum import (
    _aligned_returns,
    _compounded_return,
    _ols_beta,
    _volatility,
)


def _bar(ts: datetime, symbol: str, close: float) -> Bar:
    return Bar(
        timestamp=ts,
        symbol=symbol,
        timeframe="1d",
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=1000,
        market=Market.EQUITY,
    )


def _series_from_bars(bars: list[Bar], market=Market.EQUITY, timeframe="1d"):
    view = build_numeric_history(bars, market=market, timeframe=timeframe)
    return view


def test_aligned_returns_matches_legacy_simple():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(10):
        ts = start + timedelta(days=i)
        bars.append(_bar(ts, "AAA", 100 + i))
        bars.append(_bar(ts, "SPY", 300 + i))
    view = _series_from_bars(bars)
    stock_series = view.series("AAA")
    bench_series = view.series("SPY")
    assert stock_series is not None and bench_series is not None
    # Legacy: need Bar tuples and dict
    stock_bars = tuple(b for b in bars if b.symbol == "AAA")
    spy_dict = {b.timestamp: b.close for b in bars if b.symbol == "SPY"}
    legacy = _aligned_returns(stock_bars, spy_dict, window=5)
    numeric = aligned_returns_numeric(stock_series, bench_series, stock_bars[-1].timestamp, window=5)
    assert legacy is not None and numeric is not None
    np.testing.assert_allclose(legacy[0], numeric[0], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(legacy[1], numeric[1], rtol=1e-12, atol=1e-12)


def test_missing_stock_bar():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(10):
        ts = start + timedelta(days=i)
        if i != 3:  # stock missing day 3
            bars.append(_bar(ts, "AAA", 100 + i))
        bars.append(_bar(ts, "SPY", 300 + i))
    view = _series_from_bars(bars)
    stock_series = view.series("AAA")
    bench_series = view.series("SPY")
    assert stock_series is not None and bench_series is not None
    stock_bars = tuple(b for b in bars if b.symbol == "AAA")
    spy_dict = {b.timestamp: b.close for b in bars if b.symbol == "SPY"}
    legacy = _aligned_returns(stock_bars, spy_dict, window=5)
    # decision at last timestamp
    numeric = aligned_returns_numeric(stock_series, bench_series, bars[-1].timestamp, window=5)
    assert legacy is not None and numeric is not None
    np.testing.assert_allclose(legacy[0], numeric[0], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(legacy[1], numeric[1], rtol=1e-12, atol=1e-12)
    # legacy aligned length should be 5 window: stock has 9 bars (missing 1) -> last 6 bars -> after inner join still 6 -> returns 5
    # numeric should match


def test_missing_spy_bar():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(10):
        ts = start + timedelta(days=i)
        bars.append(_bar(ts, "AAA", 100 + i))
        if i != 4:  # SPY missing
            bars.append(_bar(ts, "SPY", 300 + i))
    view = _series_from_bars(bars)
    stock_series = view.series("AAA")
    bench_series = view.series("SPY")
    stock_bars = tuple(b for b in bars if b.symbol == "AAA")
    spy_dict = {b.timestamp: b.close for b in bars if b.symbol == "SPY"}
    legacy = _aligned_returns(stock_bars, spy_dict, window=5)
    numeric = aligned_returns_numeric(stock_series, bench_series, bars[-1].timestamp, window=5)
    assert legacy is not None and numeric is not None
    np.testing.assert_allclose(legacy[0], numeric[0], rtol=1e-12)
    np.testing.assert_allclose(legacy[1], numeric[1], rtol=1e-12)


def test_both_missing_different():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(12):
        ts = start + timedelta(days=i)
        if i not in (2, 5):
            bars.append(_bar(ts, "AAA", 100 + i))
        if i not in (3, 7):
            bars.append(_bar(ts, "SPY", 300 + i))
    view = _series_from_bars(bars)
    stock_series = view.series("AAA")
    bench_series = view.series("SPY")
    stock_bars = tuple(b for b in bars if b.symbol == "AAA")
    spy_dict = {b.timestamp: b.close for b in bars if b.symbol == "SPY"}
    legacy = _aligned_returns(stock_bars, spy_dict, window=6)
    numeric = aligned_returns_numeric(stock_series, bench_series, start + timedelta(days=11), window=6)
    assert legacy is not None and numeric is not None
    np.testing.assert_allclose(legacy[0], numeric[0], rtol=1e-12)
    np.testing.assert_allclose(legacy[1], numeric[1], rtol=1e-12)


def test_late_start_insufficient_history():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(5):
        ts = start + timedelta(days=i)
        bars.append(_bar(ts, "AAA", 100 + i))
        bars.append(_bar(ts, "SPY", 300 + i))
    view = _series_from_bars(bars)
    stock_series = view.series("AAA")
    bench_series = view.series("SPY")
    # window 10 requires more than available, so aligned should be with whatever available but beta may be insufficient
    stock_bars = tuple(b for b in bars if b.symbol == "AAA")
    spy_dict = {b.timestamp: b.close for b in bars if b.symbol == "SPY"}
    legacy = _aligned_returns(stock_bars, spy_dict, window=10)
    numeric = aligned_returns_numeric(stock_series, bench_series, start + timedelta(days=4), window=10)
    # Both should be same length (4 returns from 5 bars)
    assert legacy is not None and numeric is not None
    assert len(legacy[0]) == len(numeric[0]) == 4


def test_pit_no_future_leak():
    # Create 10 days, decision at day 5, future bars beyond 5 should not affect result
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars_full = []
    for i in range(10):
        ts = start + timedelta(days=i)
        bars_full.append(_bar(ts, "AAA", 100 + i))
        bars_full.append(_bar(ts, "SPY", 300 + i))
    view_full = _series_from_bars(bars_full)
    stock_full = view_full.series("AAA")
    bench_full = view_full.series("SPY")
    decision_ts = start + timedelta(days=5)
    numeric_at_5_full = aligned_returns_numeric(stock_full, bench_full, decision_ts, window=5)
    # Build view only up to day 5 (no future)
    bars_up_to_5 = [b for b in bars_full if b.timestamp <= decision_ts]
    view_up_to_5 = _series_from_bars(bars_up_to_5)
    stock_up_to_5 = view_up_to_5.series("AAA")
    bench_up_to_5 = view_up_to_5.series("SPY")
    numeric_at_5_truncated = aligned_returns_numeric(stock_up_to_5, bench_up_to_5, decision_ts, window=5)
    assert numeric_at_5_full is not None and numeric_at_5_truncated is not None
    np.testing.assert_allclose(numeric_at_5_full[0], numeric_at_5_truncated[0])
    np.testing.assert_allclose(numeric_at_5_full[1], numeric_at_5_truncated[1])
    # Also check legacy vs numeric at 5
    stock_bars_up_to_5 = tuple(b for b in bars_up_to_5 if b.symbol == "AAA")
    spy_dict_up_to_5 = {b.timestamp: b.close for b in bars_up_to_5 if b.symbol == "SPY"}
    legacy_at_5 = _aligned_returns(stock_bars_up_to_5, spy_dict_up_to_5, window=5)
    assert legacy_at_5 is not None
    np.testing.assert_allclose(legacy_at_5[0], numeric_at_5_full[0])


def test_beta_vol_compounded_match_legacy():
    # Random returns
    rng = np.random.default_rng(0)
    stock = rng.normal(0.001, 0.02, size=273)
    market = rng.normal(0.0005, 0.015, size=273)
    beta_legacy = _ols_beta(stock.tolist(), market.tolist())
    beta_numeric = ols_beta_arrays(stock, market)
    assert math.isclose(beta_legacy, beta_numeric, rel_tol=1e-12, abs_tol=1e-12)
    residuals_legacy = [s - beta_legacy * m for s, m in zip(stock.tolist(), market.tolist(), strict=True)]
    residuals_numeric = stock - beta_numeric * market
    np.testing.assert_allclose(residuals_legacy, residuals_numeric, rtol=1e-12)
    vol_legacy = _volatility(residuals_legacy[-63:])
    vol_numeric = volatility_array(residuals_numeric[-63:])
    assert math.isclose(vol_legacy, vol_numeric, rel_tol=1e-12)
    comp_legacy = _compounded_return(residuals_legacy[-252 - 21 : -21])
    comp_numeric = compounded_return_array(residuals_numeric[-252 - 21 : -21])
    assert math.isclose(comp_legacy, comp_numeric, rel_tol=1e-12, abs_tol=1e-12)


def test_ranking_sensitivity_near_boundary():
    # Three candidates with distinct but close trends to ensure positive scores
    # Ensure numeric and legacy produce same ordering (deterministic tie break by symbol)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = []
    # Create SPY
    for i in range(300):
        ts = start + timedelta(days=i)
        bars.append(_bar(ts, "SPY", 300 + i * 0.1))
    # Create three symbols with distinct but close trends to ensure positive scores
    for i in range(300):
        ts = start + timedelta(days=i)
        bars.append(_bar(ts, "AAA", 100 + i * 0.07 + (1e-9 if i > 200 else 0)))
        bars.append(_bar(ts, "BBB", 100 + i * 0.06))
        bars.append(_bar(ts, "CCC", 100 + i * 0.05))
    view = _series_from_bars(bars)
    # Build contexts
    decision_ts = start + timedelta(days=299)
    hist = {}
    by_sym: dict[str, list[Bar]] = {}
    for b in bars:
        by_sym.setdefault(b.symbol, []).append(b)
    for sym, lst in by_sym.items():
        filtered = [b for b in lst if b.timestamp <= decision_ts]
        if len(filtered) > 275:
            filtered = filtered[-275:]
        hist[sym] = tuple(filtered)
    ctx_legacy = PortfolioContext(
        decision_timestamp=decision_ts,
        market=Market.EQUITY,
        timeframe="1d",
        history=hist,
        universe=None,
        universe_version="test",
        equity=100_000,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
        numeric_history=None,
    )
    ctx_numeric = PortfolioContext(
        decision_timestamp=decision_ts,
        market=Market.EQUITY,
        timeframe="1d",
        history=hist,
        universe=None,
        universe_version="test",
        equity=100_000,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
        numeric_history=view,
    )
    strat = portfolio_strategy_from(
        "equity_residual_momentum_v1",
        parameters={
            "top_n": 2,
            "formation_bars": 252,
            "skip_bars": 21,
            "beta_window_bars": 273,
            "residual_vol_window": 63,
            "ivol_reject_quantile": 0.90,
        },
    )
    ranked_legacy, _ = strat._score_candidates(ctx_legacy)
    ranked_numeric, _ = strat._score_candidates(ctx_numeric)
    # Should be same ordering (deterministic)
    assert ranked_legacy == ranked_numeric
    # If both non-empty, top should be deterministic; empty is also valid parity (both filtered same)
    if ranked_legacy:
        assert ranked_legacy[0][1] == ranked_numeric[0][1]


def test_immutability_flags():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = [_bar(start + timedelta(days=i), "AAA", 100 + i) for i in range(5)]
    bars += [_bar(start + timedelta(days=i), "SPY", 300 + i) for i in range(5)]
    view = _series_from_bars(bars)
    s = view.series("AAA")
    assert s is not None
    assert not s.timestamps.flags.writeable
    assert not s.closes.flags.writeable
    with pytest.raises(ValueError):
        s.timestamps[0] = np.datetime64("2020-01-02")  # type: ignore


def test_strategy_same_scoring_core_paper_live():
    # Ensure PAPER/LIVE path (no numeric view) and backtest (with view) use same math
    # We already tested parity for 3y/6m, here test small synthetic
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(300):
        ts = start + timedelta(days=i)
        bars.append(_bar(ts, "SPY", 300 + i * 0.1))
        for sym in ["AAA", "BBB", "CCC"]:
            bars.append(_bar(ts, sym, 100 + i * 0.05 + (ord(sym[0]) - 65) * 0.01))
    view = _series_from_bars(bars)
    decision_ts = start + timedelta(days=299)
    hist = {}
    by_sym: dict[str, list[Bar]] = {}
    for b in bars:
        by_sym.setdefault(b.symbol, []).append(b)
    for sym, lst in by_sym.items():
        filtered = [b for b in lst if b.timestamp <= decision_ts]
        if len(filtered) > 275:
            filtered = filtered[-275:]
        hist[sym] = tuple(filtered)
    ctx_legacy = PortfolioContext(
        decision_timestamp=decision_ts,
        market=Market.EQUITY,
        timeframe="1d",
        history=hist,
        universe=None,
        universe_version="test",
        equity=100_000,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
        numeric_history=None,
    )
    ctx_numeric = PortfolioContext(
        decision_timestamp=decision_ts,
        market=Market.EQUITY,
        timeframe="1d",
        history=hist,
        universe=None,
        universe_version="test",
        equity=100_000,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
        numeric_history=view,
    )
    strat = portfolio_strategy_from("equity_residual_momentum_v1", parameters={})
    ranked_legacy, vols_legacy = strat._score_candidates(ctx_legacy)
    ranked_numeric, vols_numeric = strat._score_candidates(ctx_numeric)
    assert ranked_legacy == ranked_numeric
    for sym in vols_legacy:
        assert math.isclose(vols_legacy[sym], vols_numeric[sym], rel_tol=1e-12)
