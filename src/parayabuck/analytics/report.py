from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from parayabuck.analytics.metrics import PerformanceMetrics, calculate_metrics
from parayabuck.logger import get_logger
from parayabuck.models import Account, Trade

log = get_logger("parayabuck.analytics.report")


class DailyReporter:
    """Generates daily performance reports.

    Tracks equity curve and trades, produces a summary at end of day.
    """

    def __init__(self) -> None:
        self._equity_curve: list[float] = []
        self._all_trades: list[Trade] = []
        self._daily_snapshots: list[dict[str, Any]] = []
        self._current_date: str | None = None

    def update(self, account: Account) -> None:
        """Record current account state."""
        self._equity_curve.append(account.equity)
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        if self._current_date != today:
            self._current_date = today
            self._daily_snapshots.append(
                {
                    "date": today,
                    "equity": account.equity,
                    "balance": account.balance,
                    "realized_pnl": account.realized_pnl,
                    "unrealized_pnl": account.unrealized_pnl,
                    "daily_pnl": account.daily_pnl,
                    "positions": len([p for p in account.positions.values() if not p.is_flat]),
                }
            )

    def record_trade(self, trade: Trade) -> None:
        self._all_trades.append(trade)

    def daily_summary(self, account: Account) -> str:
        """Generate today's summary."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        active_positions = [(sym, pos) for sym, pos in account.positions.items() if not pos.is_flat]

        lines = [
            "",
            "=" * 60,
            f"  DAILY REPORT — {today}",
            "=" * 60,
            f"  Equity:            ${account.equity:,.2f}",
            f"  Cash:              ${account.balance:,.2f}",
            f"  Realized PnL:      ${account.realized_pnl:,.2f}",
            f"  Unrealized PnL:    ${account.unrealized_pnl:,.2f}",
            f"  Daily PnL:         ${account.daily_pnl:,.2f}",
            f"  Open positions:    {len(active_positions)}",
        ]

        if active_positions:
            lines.append("")
            lines.append("  Open Positions:")
            for sym, pos in active_positions:
                direction = "LONG" if pos.is_long else "SHORT"
                lines.append(
                    f"    {sym:6s} {direction:5s} {abs(pos.qty):4d} @ ${pos.avg_price:.2f}"
                )

        today_trades = [t for t in self._all_trades if t.timestamp.strftime("%Y-%m-%d") == today]
        if today_trades:
            lines.append("")
            lines.append(f"  Today's trades: {len(today_trades)}")
            for t in today_trades[-5:]:
                lines.append(
                    f"    {t.timestamp.strftime('%H:%M')} {t.side.value:4s} "
                    f"{t.qty:4d} {t.symbol:6s} @ ${t.price:.2f} "
                    f"PnL=${t.realized_pnl:+.2f}"
                )

        lines.append("=" * 60)
        return "\n".join(lines)

    def full_report(self, starting_balance: float, bars_per_year: int = 252) -> PerformanceMetrics:
        """Calculate full performance metrics."""
        return calculate_metrics(
            equity_curve=self._equity_curve or [starting_balance],
            trades=self._all_trades,
            starting_balance=starting_balance,
            bars_per_year=bars_per_year,
        )

    def get_equity_curve(self) -> list[float]:
        return self._equity_curve

    def get_trades(self) -> list[Trade]:
        return self._all_trades
