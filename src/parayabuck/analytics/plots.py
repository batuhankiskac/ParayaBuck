from __future__ import annotations

from parayabuck.logger import get_logger

log = get_logger("parayabuck.analytics.plots")


def plot_equity_curve(
    equity_curve: list[float],
    title: str = "Equity Curve",
    save_path: str | None = None,
) -> None:
    """Plot equity curve. Requires matplotlib."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib not installed. Run: pip install matplotlib")
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(equity_curve, color="green", linewidth=1.5)
    ax.set_title(title)
    ax.set_xlabel("Bar #")
    ax.set_ylabel("Equity ($)")
    ax.grid(True, alpha=0.3)
    ax.fill_between(
        range(len(equity_curve)),
        equity_curve,
        equity_curve[0],
        alpha=0.1,
        color="green",
    )

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        log.info(f"Equity curve saved to {save_path}")
    else:
        plt.show()
    plt.close(fig)


def plot_drawdown(
    equity_curve: list[float],
    title: str = "Drawdown",
    save_path: str | None = None,
) -> None:
    """Plot drawdown chart."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib not installed. Run: pip install matplotlib")
        return

    peak = equity_curve[0] if equity_curve else 0
    drawdowns = []
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak if peak > 0 else 0
        drawdowns.append(dd * 100)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(range(len(drawdowns)), drawdowns, 0, color="red", alpha=0.4)
    ax.set_title(title)
    ax.set_xlabel("Bar #")
    ax.set_ylabel("Drawdown (%)")
    ax.grid(True, alpha=0.3)

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        log.info(f"Drawdown chart saved to {save_path}")
    else:
        plt.show()
    plt.close(fig)


def plot_monthly_returns(
    equity_curve: list[float],
    bars_per_day: int = 1,
    title: str = "Monthly Returns Heatmap",
    save_path: str | None = None,
) -> None:
    """Plot monthly returns as a bar chart."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib not installed. Run: pip install matplotlib")
        return

    if len(equity_curve) < 2:
        return

    period_returns = [
        equity_curve[i] / equity_curve[i - 1] - 1
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] != 0
    ]

    chunk_size = max(bars_per_day * 21, 1)
    monthly: list[float] = []
    for i in range(0, len(period_returns), chunk_size):
        chunk = period_returns[i : i + chunk_size]
        if chunk:
            compounded = 1.0
            for r in chunk:
                compounded *= (1 + r)
            monthly.append((compounded - 1) * 100)

    fig, ax = plt.subplots(figsize=(12, 4))
    colors = ["green" if r > 0 else "red" for r in monthly]
    ax.bar(range(len(monthly)), monthly, color=colors, alpha=0.7)
    ax.set_title(title)
    ax.set_xlabel("Month #")
    ax.set_ylabel("Return (%)")
    ax.axhline(0, color="black", linewidth=0.5)
    ax.grid(True, alpha=0.3)

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        log.info(f"Monthly returns saved to {save_path}")
    else:
        plt.show()
    plt.close(fig)


def plot_trade_distribution(
    trades_pnl: list[float],
    title: str = "Trade PnL Distribution",
    save_path: str | None = None,
) -> None:
    """Plot histogram of trade PnLs."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib not installed. Run: pip install matplotlib")
        return

    if not trades_pnl:
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["green" if p > 0 else "red" for p in trades_pnl]
    ax.bar(range(len(trades_pnl)), trades_pnl, color=colors, alpha=0.7)
    ax.set_title(title)
    ax.set_xlabel("Trade #")
    ax.set_ylabel("PnL ($)")
    ax.axhline(0, color="black", linewidth=0.5)
    ax.grid(True, alpha=0.3)

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        log.info(f"Trade distribution saved to {save_path}")
    else:
        plt.show()
    plt.close(fig)


def save_all_plots(
    equity_curve: list[float],
    trades_pnl: list[float],
    output_dir: str = "output",
) -> None:
    """Save all plots to a directory."""
    from pathlib import Path

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    plot_equity_curve(equity_curve, save_path=str(out / "equity_curve.png"))
    plot_drawdown(equity_curve, save_path=str(out / "drawdown.png"))
    plot_monthly_returns(equity_curve, save_path=str(out / "monthly_returns.png"))
    plot_trade_distribution(trades_pnl, save_path=str(out / "trade_distribution.png"))
    log.info(f"All plots saved to {out}/")
