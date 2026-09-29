"""One-line operator summary of an equity session-close decision.

Pure formatting: every value comes from the persisted decision target (its
strategy metadata and post-risk account-level weights) and the account
snapshot the decision was made on. Nothing here participates in strategy,
risk or order math. Delivery goes through ``monitoring.alerts.emit`` (log +
journal always; webhook only when ``alerting.webhook_url`` or
``PARAYABUCK_ALERT_WEBHOOK_URL`` is configured).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.markets.symbols import canonical_symbol

if TYPE_CHECKING:
    from parayabuck.portfolio.models import TargetPortfolio


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _qty(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def build_equity_daily_summary(
    target: TargetPortfolio,
    account: AccountSnapshot | None,
    *,
    status: str,
    decision_id: str | None,
    orders: int = 0,
    submitted: int = 0,
) -> tuple[str, dict[str, Any]]:
    """Return ``(one_line_message, structured_fields)`` for one decision.

    Weights are fractions of ACCOUNT equity: ``target`` is the post-risk
    target actually planned (sleeve cap applied); ``held`` is the position
    at decision time, before the decision's orders fill.
    """
    metadata = target.metadata or {}
    signal_symbol = str(metadata.get("signal_symbol") or "signal")
    hold_symbol = str(metadata.get("hold_symbol") or (target.weights[0].symbol if target.weights else ""))
    trend_state = str(metadata.get("trend_state") or "unknown")
    close = _number(metadata.get("signal_close"))
    sma = _number(metadata.get("signal_sma"))
    sma_bars = metadata.get("sma_bars")
    sma_label = f"SMA{sma_bars}" if isinstance(sma_bars, int) and not isinstance(sma_bars, bool) else "SMA"
    distance_pct = (close / sma - 1.0) * 100.0 if close is not None and sma is not None and sma > 0 else None
    target_weight = target.weight_for(hold_symbol, Market.EQUITY) if hold_symbol else 0.0

    equity = float(account.equity) if account is not None else None
    positions: dict[str, float] = {}
    held_weight: float | None = None
    if account is not None:
        held_value = 0.0
        for position in account.positions:
            if position.is_flat:
                continue
            symbol = canonical_symbol(position.symbol, position.market)
            positions[symbol] = float(position.qty)
            if position.market == Market.EQUITY and symbol == hold_symbol:
                held_value += position.market_value
        held_weight = held_value / account.equity if account.equity > 0 else None
    daily_change = float(account.daily_pnl) if account is not None else None
    previous_equity = (equity - daily_change) if equity is not None and daily_change is not None else None
    daily_change_pct = (
        daily_change / previous_equity * 100.0
        if daily_change is not None and previous_equity is not None and previous_equity > 0
        else None
    )

    session = target.decision_timestamp.date().isoformat()
    try:
        from parayabuck.data.adjustment import signal_session_date

        session = signal_session_date(target.decision_timestamp).isoformat()
    except Exception:  # pragma: no cover - formatting must never fail the decision
        pass

    trend_part = f"{signal_symbol} {close:.2f}" if close is not None else f"{signal_symbol} n/a"
    if sma is not None:
        trend_part += f" vs {sma_label} {sma:.2f}"
    if distance_pct is not None:
        trend_part += f" ({distance_pct:+.2f}%)"
    trend_part += f" {trend_state}"
    held_text = f"{held_weight * 100:.1f}%" if held_weight is not None else "n/a"
    weight_part = f"{hold_symbol or 'hold'} target {target_weight * 100:.1f}% held {held_text}"
    positions_part = (
        "pos " + ", ".join(f"{symbol} {_qty(qty)}" for symbol, qty in sorted(positions.items()))
        if positions
        else "pos none"
    )
    if equity is not None:
        equity_part = f"equity ${equity:,.2f}"
        if daily_change is not None:
            equity_part += f" ({daily_change:+,.2f}"
            equity_part += f", {daily_change_pct:+.2f}% d/d)" if daily_change_pct is not None else " d/d)"
    else:
        equity_part = "equity n/a"
    order_part = f"{status} orders {submitted}/{orders}"
    message = (
        f"{target.strategy_name} {session}: {trend_part} | {weight_part} | "
        f"{positions_part} | {equity_part} | {order_part}"
    )
    fields: dict[str, Any] = {
        "market": Market.EQUITY.value,
        "strategy": target.strategy_name,
        "decision_id": decision_id or "",
        "session_date": session,
        "status": status,
        "trend_state": trend_state,
        "signal_symbol": signal_symbol,
        "signal_close": close,
        "signal_sma": sma,
        "signal_sma_distance_pct": distance_pct,
        "hold_symbol": hold_symbol,
        "target_weight": target_weight,
        "held_weight": held_weight,
        "positions": positions,
        "account_equity": equity,
        "daily_change": daily_change,
        "daily_change_pct": daily_change_pct,
        "orders_planned": orders,
        "orders_submitted": submitted,
    }
    return message, fields


__all__ = ["build_equity_daily_summary"]
