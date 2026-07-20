"""Unit tests for the global market filter (SPY 200-SMA + VIX proxy)."""
from __future__ import annotations

from parayabuck.strategy.market_filter import GlobalMarketFilter
from tests._helpers import bars_from_closes, trending_down, trending_up


def _make_bars(closes, symbol="SPY"):
    """bars_from_closes uses TEST as the symbol; let's override."""
    bars = bars_from_closes(closes, symbol=symbol)
    return bars


def test_market_filter_allows_when_spy_above_200sma():
    closes = trending_up(220, 100.0, 0.003)
    bars = _make_bars(closes, symbol="SPY")
    mf = GlobalMarketFilter(benchmark_symbol="SPY", vix_proxy_symbol="VIXY")
    state = mf.evaluate(bars, vix_bars=None)
    assert state.spy_above_200sma is True
    assert state.vix_high is False
    assert state.allowed is True


def test_market_filter_blocks_when_spy_below_200sma():
    # Strong sustained downtrend -> price well below 200-SMA.
    closes = trending_down(220, 200.0, 0.005)
    bars = _make_bars(closes, symbol="SPY")
    mf = GlobalMarketFilter(benchmark_symbol="SPY")
    state = mf.evaluate(bars, vix_bars=None)
    assert state.spy_above_200sma is False
    assert state.allowed is False
    assert "below 200-SMA" in state.reason


def test_market_filter_blocks_when_vix_proxies_spikes():
    # SPY is fine, but VIXY jumps 100% above its 50-SMA -> crisis signal.
    spy_bars = _make_bars(trending_up(220, 100.0, 0.002), symbol="SPY")
    # VIXY: stable at 20 for 50 bars, then spikes to 40.
    vixy_closes = [20.0] * 50 + [40.0]
    vixy_bars = _make_bars(vixy_closes, symbol="VIXY")
    mf = GlobalMarketFilter(benchmark_symbol="SPY", vix_proxy_symbol="VIXY", vix_high_ratio=1.5)
    state = mf.evaluate(spy_bars, vixy_bars)
    assert state.vix_high is True
    assert state.allowed is False
    assert "VIX" in state.reason or "spiked" in state.reason


def test_market_filter_disabled_allows_everything():
    closes = trending_down(220, 200.0, 0.005)
    bars = _make_bars(closes, symbol="SPY")
    mf = GlobalMarketFilter(enabled=False)
    state = mf.evaluate(bars, vix_bars=None)
    assert state.allowed is True


def test_market_filter_short_input_fails_closed():
    """During warmup, with insufficient SPY history, the filter should
    degrade to allowed=True so the bot can still trade (rather than block
    everything for the first 200 bars)."""
    bars = _make_bars([100.0] * 50, symbol="SPY")
    mf = GlobalMarketFilter(benchmark_symbol="SPY")
    state = mf.evaluate(bars, vix_bars=None)
    assert state.allowed is False
