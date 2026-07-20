from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tabulate import tabulate

from parayabuck.logger import get_logger
from parayabuck.models import Bar
from parayabuck.strategy.regime import MarketRegime, RegimeDetector

log = get_logger("parayabuck.analytics.regime_report")


@dataclass
class RegimePerformanceRow:
    """Per-regime performance breakdown."""
    regime: MarketRegime
    bar_count: int = 0
    pct_of_total: float = 0.0
    trade_count: int = 0
    pnl_in_regime: float = 0.0
    avg_pnl_per_trade: float = 0.0
    win_rate: float = 0.0


@dataclass
class RegimePerformanceReport:
    """Breakdown of strategy PnL by the market regime each trade was in.

    Answers the question: "does this strategy actually make money in the
    regime it's designed for?" A mean-reversion strategy that produces all
    its gains in trending regimes and bleeds in ranging regimes is mis-tuned
    or mis-routed — the regime map should be re-examined.
    """
    rows: list[RegimePerformanceRow] = field(default_factory=list)
    total_bars: int = 0
    total_pnl: float = 0.0

    def summary(self) -> str:
        lines = [
            "",
            "=" * 70,
            "  REGIME-CONDITIONAL PERFORMANCE",
            "=" * 70,
            f"  Total bars analyzed: {self.total_bars:,}",
            f"  Total PnL:           ${self.total_pnl:+.2f}",
            "",
            "  Per-Regime Breakdown:",
            "  " + "-" * 64,
        ]
        rows = []
        for r in self.rows:
            rows.append(
                [
                    r.regime.value,
                    r.bar_count,
                    f"{r.pct_of_total:.0%}",
                    r.trade_count,
                    f"${r.pnl_in_regime:+.2f}",
                    f"{r.win_rate:.0%}",
                ]
            )
        lines.append(
            tabulate(
                rows,
                headers=["Regime", "Bars", "% Time", "Trades", "PnL", "Win%"],
                tablefmt="simple",
            )
        )
        lines.append("=" * 70)
        return "\n".join(lines)


def build_regime_report(
    bars: list[Bar],
    trades: list[dict[str, Any]],
    starting_balance: float = 10000.0,
    sma_period: int = 50,
) -> RegimePerformanceReport:
    """Build a regime-conditional performance report.

    For each bar, detect the regime at that point in time. For each trade,
    attribute the trade's PnL to the regime that was active when the trade
    was opened (the closing-trade timestamp is used because that's when PnL
    realises, but the regime attribution uses the entry — which we don't
    always have here, so we use the trade's timestamp).

    Args:
        bars: The bar series the strategy was run on. We use a rolling
            regime detection on these bars.
        trades: The list of trade dicts from BacktestResult.trades. Each
            must have `timestamp`, `pnl`, and (optionally) `closes_position`.
        starting_balance: For initial equity reference.
        sma_period: SMA period for the rolling RegimeDetector.
    """
    detector = RegimeDetector(sma_period=sma_period)
    report = RegimePerformanceReport(total_bars=len(bars))

    # Walk bars in order and tag each with its regime at that point.
    # We need at least `sma_period` bars before detection produces a
    # non-UNKNOWN regime — earlier bars get UNKNOWN.
    window: list[Bar] = []
    regime_at_bar: dict[int, MarketRegime] = {}
    regime_bar_counts: dict[MarketRegime, int] = {r: 0 for r in MarketRegime}

    for i, b in enumerate(bars):
        window.append(b)
        # Keep the window bounded — long enough for detection but not unbounded.
        if len(window) > max(sma_period * 3, 200):
            window = window[-max(sma_period * 2, 150) :]
        if len(window) >= sma_period:
            state = detector.detect(window)
            regime_at_bar[i] = state.regime
            regime_bar_counts[state.regime] = regime_bar_counts.get(state.regime, 0) + 1
        else:
            regime_at_bar[i] = MarketRegime.UNKNOWN
            regime_bar_counts[MarketRegime.UNKNOWN] = regime_bar_counts.get(
                MarketRegime.UNKNOWN, 0
            ) + 1

    # Build a timestamp -> bar index lookup so we can attribute each trade to
    # the regime that was active when the trade was opened/closed.
    ts_to_idx: dict[Any, int] = {}
    for i, b in enumerate(bars):
        ts_to_idx[b.timestamp] = i

    # Attribute each closing trade's PnL to the regime at its timestamp.
    # We have to find the closest bar at or before the trade timestamp.
    sorted_bar_timestamps: list[Any] = [b.timestamp for b in bars]

    def _find_regime_for_ts(ts: Any) -> MarketRegime:
        # Find the latest bar with timestamp <= ts.
        import bisect

        idx = bisect.bisect_right(sorted_bar_timestamps, ts) - 1
        if idx < 0:
            return MarketRegime.UNKNOWN
        return regime_at_bar.get(idx, MarketRegime.UNKNOWN)

    regime_pnls: dict[MarketRegime, list[float]] = {r: [] for r in MarketRegime}
    regime_trades: dict[MarketRegime, int] = {r: 0 for r in MarketRegime}

    for t in trades:
        # Only closing trades carry PnL for the position.
        pnl = t.get("pnl", 0.0)
        if pnl == 0 and not t.get("closes_position", True):
            continue
        regime = _find_regime_for_ts(t["timestamp"])
        regime_pnls[regime].append(pnl)
        regime_trades[regime] = regime_trades.get(regime, 0) + 1
        report.total_pnl += pnl

    # Build the rows.
    total_bars = max(len(bars), 1)
    for regime in MarketRegime:
        bc = regime_bar_counts.get(regime, 0)
        if bc == 0 and regime_trades.get(regime, 0) == 0:
            continue
        pnls = regime_pnls.get(regime, [])
        pnl_sum = sum(pnls)
        wins = [p for p in pnls if p > 0]
        win_rate = len(wins) / len(pnls) if pnls else 0.0
        avg_pnl = pnl_sum / len(pnls) if pnls else 0.0
        report.rows.append(
            RegimePerformanceRow(
                regime=regime,
                bar_count=bc,
                pct_of_total=bc / total_bars,
                trade_count=regime_trades.get(regime, 0),
                pnl_in_regime=pnl_sum,
                avg_pnl_per_trade=avg_pnl,
                win_rate=win_rate,
            )
        )

    # Sort rows: most-active regimes first.
    report.rows.sort(key=lambda r: -r.bar_count)
    return report


__all__ = ["build_regime_report", "RegimePerformanceReport", "RegimePerformanceRow"]
