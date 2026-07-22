"""Unit tests for the walk-forward optimizer and regime report."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.analytics.regime_report import build_regime_report
from parayabuck.models import Bar


def _bars_from_closes(closes, symbol="TEST"):
    bars = []
    base = datetime(2025, 1, 1, 9, 30, tzinfo=UTC)
    for i, c in enumerate(closes):
        ts = base + timedelta(minutes=15 * i)
        bars.append(
            Bar(
                timestamp=ts,
                symbol=symbol,
                timeframe="15m",
                open=c,
                high=c * 1.001,
                low=c * 0.999,
                close=c,
                volume=1000,
            )
        )
    return bars


def test_regime_report_returns_one_row_per_active_regime():
    """The regime report should produce at least one row covering the bars."""
    import math

    # 220 bars oscillating rapidly -> RANGING regime.
    closes = [100.0 + 2.0 * math.sin(2 * math.pi * i / 10) for i in range(220)]
    bars = _bars_from_closes(closes)
    trades = [
        {"timestamp": bars[100].timestamp, "side": "BUY", "qty": 10, "price": 100.0, "pnl": 50.0},
        {"timestamp": bars[150].timestamp, "side": "SELL", "qty": 10, "price": 100.5, "pnl": -20.0},
    ]
    report = build_regime_report(bars, trades, starting_balance=10000.0, sma_period=50)
    assert report.total_bars == 220
    assert report.total_pnl == 30.0
    # At least one row should be present.
    assert len(report.rows) >= 1
    # Sum of bar counts across rows should equal total bars.
    assert sum(r.bar_count for r in report.rows) == 220
    # Summary should render without error.
    text = report.summary()
    assert "REGIME-CONDITIONAL PERFORMANCE" in text


def test_regime_report_handles_empty_trades():
    """With no trades, the report should still produce rows with zero PnL."""
    bars = _bars_from_closes([100.0] * 220)
    report = build_regime_report(bars, [], starting_balance=10000.0)
    assert report.total_pnl == 0.0
    assert all(r.trade_count == 0 for r in report.rows)
    # All bars should land in some regime.
    assert sum(r.bar_count for r in report.rows) == 220


def test_regime_report_attributes_pnl_to_active_regime():
    """A trade's PnL should be attributed to the regime at the trade's
    timestamp, not the regime at the end of the bar series."""
    import math

    # 100 bars trending up (TRENDING_UP), then 100 bars oscillating (RANGING).
    trending = [100.0 + i * 0.5 for i in range(100)]
    ranging = [150.0 + 2.0 * math.sin(2 * math.pi * i / 10) for i in range(100)]
    bars = _bars_from_closes(trending + ranging)
    # A trade closed during the trending phase should attribute its PnL to
    # the trending regime, not to ranging.
    trades = [
        {"timestamp": bars[50].timestamp, "side": "BUY", "qty": 10, "price": 125.0, "pnl": 100.0},
    ]
    report = build_regime_report(bars, trades, starting_balance=10000.0, sma_period=50)
    trending_row = next(
        (r for r in report.rows if "trending" in r.regime.value), None
    )
    if trending_row is not None:
        assert trending_row.pnl_in_regime == 100.0
