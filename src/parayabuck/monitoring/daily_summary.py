"""One-line operator summary of an equity session-close decision.

Pure formatting: every value comes from the persisted decision target (its
strategy metadata and post-risk account-level weights) and the account
snapshot the decision was made on. Nothing here participates in strategy,
risk or order math. Delivery goes through ``monitoring.alerts.emit`` (log +
event journal).
"""

from __future__ import annotations

from collections.abc import Mapping
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


def _trend_votes(signals: object) -> str | None:
    """``"2/3 (SMA+ TSM- DON+)"`` from E2 vote metadata; ``None`` without it."""
    if not isinstance(signals, dict):
        return None
    marks = []
    for key in ("sma", "tsm", "don"):
        entry = signals.get(key)
        vote = entry.get("up") if isinstance(entry, dict) else None
        marks.append(f"{key.upper()}{'+' if vote is True else '-' if vote is False else '?'}")
    count = signals.get("votes_up")
    total = f"{count}/3" if isinstance(count, int) and not isinstance(count, bool) else "?/3"
    return f"{total} ({' '.join(marks)})"


def _early_reentry(entry: object) -> str | None:
    """``"early re-entry 0.5x"`` while R2's early path is active; ``None`` otherwise."""
    if not isinstance(entry, dict) or entry.get("active") is not True:
        return None
    weight = _number(entry.get("weight"))
    return f"early re-entry {weight:g}x" if weight is not None else "early re-entry"


def _gain_band(entry: object) -> str | None:
    """``"gain band held (g 7.2%, band 8pp)"`` for every downsize A1u suppressed.

    ``entry`` is the ``gain_band`` metadata tuple written by the planner: one
    mapping per suppressed symbol. ``None`` when the key is absent, so every
    decision the band did not touch formats exactly as before.
    """
    entries = (entry,) if isinstance(entry, dict) else entry
    if not isinstance(entries, (tuple, list)):
        return None
    described: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        g_frac, band = _number(item.get("g_frac")), _number(item.get("band"))
        if g_frac is None or band is None:
            continue
        symbol = f" {item['symbol']}" if len(entries) > 1 and item.get("symbol") else ""
        described.append(f"gain band held{symbol} (g {g_frac * 100:.1f}%, band {band * 100:.0f}pp)")
    return "; ".join(described) if described else None


def _december_deferral(entry: object) -> str | None:
    """``"december deferral held (0.88 -> 0.90, 10 sessions)"`` per postponed downsize.

    ``entry`` is the ``december_deferral`` metadata tuple written by the
    planner: one mapping per symbol whose December downsize waited for the
    first January decision (H2). ``None`` when the key is absent, so every
    decision the window did not touch formats exactly as before.
    """
    entries = (entry,) if isinstance(entry, dict) else entry
    if not isinstance(entries, (tuple, list)):
        return None
    described: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        target, current = _number(item.get("target_weight")), _number(item.get("current_weight"))
        sessions = item.get("sessions")
        if target is None or current is None or not isinstance(sessions, int) or isinstance(sessions, bool):
            continue
        symbol = f" {item['symbol']}" if len(entries) > 1 and item.get("symbol") else ""
        described.append(
            f"december deferral held{symbol} ({target * 100:.1f}% -> {current * 100:.1f}%, {sessions} sessions)"
        )
    return "; ".join(described) if described else None


def format_tax_ytd(status: Mapping[str, Any]) -> str:
    """One YTD tax line from ``parayabuck.tax.export.ytd_status`` (pure formatting).

    ``tax: rates missing`` when the USD/TRY or Yİ-ÜFE inputs cannot be used, so
    the operator sees the gap instead of a silent omission.
    """
    if status.get("status") != "ok":
        return "tax: rates missing" if status.get("status") == "rates_missing" else f"tax: {status.get('status')}"
    net = _number(status.get("net_gain_tl"))
    tax = _number(status.get("estimated_tax_tl"))
    if net is None or tax is None:
        return "tax: rates missing"
    return f"tax YTD {status.get('year')}: net gain {net:+,.2f} TL, est. tax {tax:,.2f} TL"


def build_equity_daily_summary(
    target: TargetPortfolio,
    account: AccountSnapshot | None,
    *,
    status: str,
    decision_id: str | None,
    orders: int = 0,
    submitted: int = 0,
    tax_ytd: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return ``(one_line_message, structured_fields)`` for one decision.

    Weights are fractions of ACCOUNT equity: ``target`` is the post-risk
    target actually planned (sleeve cap applied); ``held`` is the position
    at decision time, before the decision's orders fill. ``tax_ytd`` (from
    :func:`format_tax_ytd`) is appended as the last segment when given.
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
    votes = _trend_votes(metadata.get("trend_signals"))
    if votes is not None:
        # E2 (trend_rule majority_sma_tsm_don): the state is the 2-of-3 vote,
        # so the SMA distance alone does not explain it.
        trend_part += f" votes {votes}"
    early = _early_reentry(metadata.get("early_reentry"))
    if early is not None:
        # R2 (early_reentry_weight): the state name is the phrase plus the
        # weight factor, so it replaces the raw state token.
        trend_part += f" {early}"
    else:
        trend_part += f" {trend_state}"
    held_by_band = _gain_band(metadata.get("gain_band"))
    if held_by_band is not None:
        # A1u: the target wanted a smaller position and the gain-scaled band
        # kept it; say so instead of letting the held weight look unexplained.
        trend_part += f" {held_by_band}"
    deferred = _december_deferral(metadata.get("december_deferral"))
    if deferred is not None:
        # H2: the year-end window kept the position until the first January
        # decision; the held weight is intended, not a failed order.
        trend_part += f" {deferred}"
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
    if tax_ytd:
        message = f"{message} | {tax_ytd}"
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
        "trend_votes": votes,
        "early_reentry": early,
        "gain_band": held_by_band,
        "december_deferral": deferred,
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


__all__ = ["build_equity_daily_summary", "format_tax_ytd"]
