"""Observability integration — emit at domain boundaries with isolation.

Every function is never-raising and bounded. Trading correctness never depends
on observability. Failures are counted via journal metrics and reported by the
``observability`` health check.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from parayabuck.monitoring.journal import emit_journal
from parayabuck.monitoring.operational_event import create_event

log = logging.getLogger("parayabuck.monitoring.observe")

# ------------------------------------------------------------------ dedup state
_last_state: dict[str, str] = {}
# A clean reconciliation runs every minute; it is journaled at most once an hour
# per phase, and again right after any reconciliation failure, drift or recovery
# (the soak auditor treats a later success as the proof an incident ended).
_RECONCILIATION_SUCCESS_INTERVAL_SECONDS = 3600.0
_reconciliation_success_emitted_at: dict[str, float] = {}


def _emit_safely(**kwargs: Any) -> None:
    try:
        ev = create_event(**kwargs)
        emit_journal(ev)
    except Exception:
        log.warning("observe emit failed for kind=%s", kwargs.get("kind"), exc_info=True)


def _dedup(key: str, new: str) -> bool:
    """Return True if transition is new (emit), False if duplicate."""
    old = _last_state.get(key)
    if old == new:
        return False
    _last_state[key] = new
    return True


def reset_dedup_for_tests() -> None:
    _last_state.clear()
    _reconciliation_success_emitted_at.clear()


def note_reconciliation_alert() -> None:
    """A reconciliation failure, drift or recovery was raised: the next clean run is journaled."""
    _reconciliation_success_emitted_at.clear()


# ------------------------------------------------------------------ runtime
def on_runtime_started(*, session_id: str | None = None, mode: str | None = None, pid: int | None = None) -> None:
    _emit_safely(
        kind="runtime_started",
        message="runtime started",
        session_id=session_id,
        mode=mode,
        fields={"pid": pid} if pid else {},
    )


def on_runtime_stopping(*, session_id: str | None = None, reason: str | None = None) -> None:
    _emit_safely(
        kind="runtime_stopping",
        message="runtime stopping",
        session_id=session_id,
        fields={"reason": reason} if reason else {},
    )


def on_runtime_health_degraded(*, reason: str, session_id: str | None = None) -> None:
    if not _dedup("runtime_health", "degraded"):
        return
    _emit_safely(
        kind="runtime_health_degraded",
        message=f"health degraded: {reason}",
        session_id=session_id,
        fields={"reason": reason},
    )


def on_runtime_health_recovered(*, session_id: str | None = None) -> None:
    if not _dedup("runtime_health", "ok"):
        return
    _emit_safely(kind="runtime_health_recovered", message="health recovered", session_id=session_id)


# ------------------------------------------------------------------ reconciliation
def on_reconciliation_complete(*, phase: str, session_id: str | None = None) -> None:
    now = time.monotonic()
    last = _reconciliation_success_emitted_at.get(phase)
    if last is not None and now - last < _RECONCILIATION_SUCCESS_INTERVAL_SECONDS:
        return
    _reconciliation_success_emitted_at[phase] = now
    _emit_safely(
        kind="reconciliation_succeeded",
        message=f"{phase} reconciliation completed without drift",
        session_id=session_id,
        fields={"phase": phase},
    )


# ------------------------------------------------------------------ account
def on_account_stale(
    *, age_seconds: float | None = None, session_id: str | None = None, account_id: str | None = None
) -> None:
    if not _dedup("account", "stale"):
        return
    _emit_safely(
        kind="account_became_stale",
        message="account became stale",
        session_id=session_id,
        account_id=account_id,
        fields={"age_seconds": age_seconds} if age_seconds else {},
    )


def on_account_recovered(*, session_id: str | None = None, account_id: str | None = None) -> None:
    if not _dedup("account", "fresh"):
        return
    _emit_safely(kind="account_recovered", message="account recovered", session_id=session_id, account_id=account_id)


# ------------------------------------------------------------------ rebalance
def on_rebalance_phase_changed(
    *,
    decision_id: str,
    market: str | None = None,
    account_id: str | None = None,
    session_id: str | None = None,
    from_phase: str | None = None,
    to_phase: str | None = None,
    reason: str | None = None,
    order_counts: dict[str, int] | None = None,
) -> None:
    _emit_safely(
        kind="rebalance_phase_changed",
        message=f"rebalance {decision_id} {from_phase} -> {to_phase}",
        decision_id=decision_id,
        market=market,
        account_id=account_id,
        session_id=session_id,
        fields={"from_phase": from_phase, "to_phase": to_phase, "reason": reason, "order_counts": order_counts}
        if reason or order_counts
        else {"from_phase": from_phase, "to_phase": to_phase},
    )


# ------------------------------------------------------------------ decision summary
def on_decision_summary(
    *,
    decision_id: str,
    market: str | None = None,
    strategy: str | None = None,
    session_id: str | None = None,
    account_id: str | None = None,
    universe_version: str | None = None,
    qualified_count: int | None = None,
    selected_count: int | None = None,
    planned_sell_count: int | None = None,
    planned_buy_count: int | None = None,
    submitted_count: int | None = None,
    no_trade_reason: str | None = None,
    decision_status: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    fields: dict[str, Any] = {}
    if universe_version is not None:
        fields["universe_version"] = universe_version
    if qualified_count is not None:
        fields["qualified_count"] = qualified_count
    if selected_count is not None:
        fields["selected_count"] = selected_count
    if planned_sell_count is not None:
        fields["planned_sell_count"] = planned_sell_count
    if planned_buy_count is not None:
        fields["planned_buy_count"] = planned_buy_count
    if submitted_count is not None:
        fields["submitted_count"] = submitted_count
    if no_trade_reason is not None:
        fields["no_trade_reason"] = no_trade_reason
    if decision_status is not None:
        fields["decision_status"] = decision_status
    if extra:
        fields.update(extra)
    _emit_safely(
        kind="decision_summary",
        message=f"decision {decision_id} summary: {no_trade_reason or decision_status or 'traded'}",
        decision_id=decision_id,
        market=market,
        strategy=strategy,
        session_id=session_id,
        account_id=account_id,
        fields=fields,
    )
