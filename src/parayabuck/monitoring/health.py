"""Health evaluation layer — interprets raw metrics into operator checks.

Separate raw metrics (counters, DB rows) from interpretation (OK/DEGRADED/BLOCKED/CRITICAL/UNKNOWN).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from parayabuck.config import Config, load_config
from parayabuck.markets.timeframes import is_session_open
from parayabuck.monitoring import metrics

log = logging.getLogger("parayabuck.monitoring.health")


class HealthStatus(StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class TradingState(StrEnum):
    ACTIVE = "ACTIVE"
    ENTRY_BLOCKED = "ENTRY_BLOCKED"
    REBALANCE_BLOCKED = "REBALANCE_BLOCKED"
    MARKET_HALTED = "MARKET_HALTED"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class HealthCheck:
    code: str
    status: HealthStatus
    summary: str
    observed: object | None = None
    threshold: object | None = None
    since: datetime | None = None
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": str(self.status),
            "summary": self.summary,
            "observed": self.observed,
            "threshold": self.threshold,
            "since": self.since.isoformat() if isinstance(self.since, datetime) else None,
            "action": self.action,
        }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def _age_seconds(iso: str | None, now: datetime) -> float | None:
    dt = _parse_iso(iso)
    if dt is None:
        return None
    return (now - dt).total_seconds()


# A missing scheduled decision is the 2026-09-29 incident class: the runtime
# stays alive and every liveness check stays green, so it must not be reported
# as a mere degradation.
_INTENT_CRITICAL_CODES = frozenset({"equity_decision_missing"})


def persisted_journal_state(snapshot: object) -> dict[str, Any] | None:
    """The engine's own ``journal_stats()`` copy inside a health snapshot."""
    stats = snapshot.get("observability") if isinstance(snapshot, dict) else None
    return stats if isinstance(stats, dict) and "event_journal_written" in stats else None


# ------------------------------------------------------------------
# Health evaluation
# ------------------------------------------------------------------


def _check_runtime_heartbeat(snapshot_path: Path, now: datetime, heartbeat_tolerance_s: float = 90.0) -> HealthCheck:
    if not snapshot_path.exists():
        return HealthCheck(
            code="runtime_heartbeat",
            status=HealthStatus.UNKNOWN,
            summary="no health snapshot found — runtime may not have started",
            observed=None,
            threshold=heartbeat_tolerance_s,
            action="check if runtime process is running",
        )
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return HealthCheck(
            code="runtime_heartbeat",
            status=HealthStatus.UNKNOWN,
            summary=f"snapshot unreadable: {exc}",
            observed="corrupt",
            threshold=heartbeat_tolerance_s,
            action="inspect data/runtime/health_snapshot.json",
        )
    written_at = _parse_iso(data.get("written_at") or data.get("as_of"))
    if written_at is None:
        return HealthCheck(
            code="runtime_heartbeat",
            status=HealthStatus.UNKNOWN,
            summary="snapshot missing written_at",
            observed=data.get("written_at"),
            threshold=heartbeat_tolerance_s,
        )
    age = (now - written_at).total_seconds()
    state = str(data.get("runtime_state") or data.get("state") or "unknown")
    if age > heartbeat_tolerance_s:
        return HealthCheck(
            code="runtime_heartbeat",
            status=HealthStatus.CRITICAL,
            summary=f"heartbeat stale {age:.0f}s ago (state={state})",
            observed=age,
            threshold=heartbeat_tolerance_s,
            since=written_at,
            action="restart runtime if process dead",
        )
    if state in {"stopped", "stop_failed"}:
        return HealthCheck(
            code="runtime_heartbeat",
            status=HealthStatus.BLOCKED,
            summary=f"runtime stopped (state={state})",
            observed=state,
            threshold=heartbeat_tolerance_s,
            since=written_at,
        )
    return HealthCheck(
        code="runtime_heartbeat",
        status=HealthStatus.OK,
        summary=f"alive {age:.0f}s ago (state={state})",
        observed=age,
        threshold=heartbeat_tolerance_s,
        since=written_at,
    )


# Single source of truth for worker-liveness rules (R4A-2): importing the
# runtime module here would cycle (web/CLI import health without a running
# engine), so the constants and the staleness predicate live here and the
# runtime imports them. The trade-update stream heartbeats only at (re)start,
# the signal-adjustment monitor once per hourly pass, and a market stream
# proves liveness by delivered bars, only while the NYSE calendar says it
# must deliver (never on a holiday, never after an early close).
_EVENT_DRIVEN_WORKERS = frozenset({"pb-trade-updates"})
_SIGNAL_ADJUSTMENT_WORKER = "pb-signal-adjustment"
_SIGNAL_ADJUSTMENT_CHECK_SECONDS = 3600.0
_STREAM_WORKER_MARKETS = {"pb-stocks": "equity"}


def _worker_stale(
    name: str, info: dict[str, Any], now: datetime, ttl: float, markets: dict[str, Any] | None = None
) -> bool:
    if name in _EVENT_DRIVEN_WORKERS:
        return False
    market = _STREAM_WORKER_MARKETS.get(name)
    if market is not None:
        # Like _required_workers: a market that is disabled or has no symbols
        # does not need its stream to deliver.
        market_info = (markets or {}).get(market)
        if isinstance(market_info, dict) and (
            market_info.get("enabled") is False or market_info.get("symbol_count") == 0
        ):
            return False
        if market == "equity" and not is_session_open(now):
            return False
        last_event = _parse_iso(info.get("last_event_at"))
        return last_event is None or (now - last_event).total_seconds() > max(180.0, ttl)
    if name == _SIGNAL_ADJUSTMENT_WORKER:
        ttl = _SIGNAL_ADJUSTMENT_CHECK_SECONDS + max(300.0, ttl)
    last_hb = _parse_iso(info.get("last_heartbeat"))
    return last_hb is not None and (now - last_hb).total_seconds() > ttl


def _check_workers(health_snapshot: dict[str, Any] | None, now: datetime, config: Config) -> HealthCheck:
    if health_snapshot is None:
        return HealthCheck(code="workers", status=HealthStatus.UNKNOWN, summary="no runtime health available")
    workers = health_snapshot.get("workers") or {}
    if not workers:
        return HealthCheck(code="workers", status=HealthStatus.UNKNOWN, summary="no worker data")
    ttl = max(30.0, config.engine.account_poll_interval_seconds * 3)
    # Judge ages as of the snapshot, as the runtime did when it wrote it; the
    # snapshot's own age is the runtime_heartbeat check's job. Measuring from
    # the read time would add up to one snapshot interval to every age.
    written_at = _parse_iso(health_snapshot.get("written_at") or health_snapshot.get("as_of"))
    as_of = min(now, written_at) if written_at is not None else now
    markets = health_snapshot.get("markets")
    markets = markets if isinstance(markets, dict) else None
    stale: list[str] = []
    dead: list[str] = []
    for name, info in workers.items():
        if not isinstance(info, dict):
            continue
        if not bool(info.get("alive")):
            dead.append(name)
        elif _worker_stale(str(name), info, as_of, ttl, markets):
            stale.append(name)
    if dead:
        return HealthCheck(
            code="workers",
            status=HealthStatus.CRITICAL,
            summary=f"dead workers: {', '.join(dead)}",
            observed=dead,
            threshold=ttl,
            action="check worker logs, restart if stuck",
        )
    if stale:
        return HealthCheck(
            code="workers",
            status=HealthStatus.DEGRADED,
            summary=f"stale heartbeat: {', '.join(stale)}",
            observed=stale,
            threshold=ttl,
        )
    return HealthCheck(code="workers", status=HealthStatus.OK, summary=f"{len(workers)} workers alive")


def _check_queue(health_snapshot: dict[str, Any] | None, config: Config) -> HealthCheck:
    if health_snapshot is None:
        return HealthCheck(code="queue", status=HealthStatus.UNKNOWN, summary="no queue metrics")
    qsize = health_snapshot.get("queue_size")
    qage = health_snapshot.get("queue_age_seconds")
    dropped = health_snapshot.get("dropped_events")
    try:
        qsize_n = int(qsize) if qsize is not None else 0
        qage_n = float(qage) if qage is not None else 0.0
        dropped_n = int(dropped) if dropped is not None else 0
    except Exception:
        return HealthCheck(code="queue", status=HealthStatus.UNKNOWN, summary="queue metrics corrupt")
    cap = int(config.engine.event_queue_capacity)
    max_age = float(config.engine.maximum_queue_age_seconds)
    if qsize_n >= cap * 0.9 or qage_n > max_age or dropped_n > 0:
        # dropped is critical if recent, degraded if historic? Use critical for active pressure
        if dropped_n > 0 or qsize_n >= cap:
            return HealthCheck(
                code="queue",
                status=HealthStatus.CRITICAL,
                summary=f"queue pressure size={qsize_n}/{cap} age={qage_n:.1f}s dropped={dropped_n}",
                observed={"size": qsize_n, "age": qage_n, "dropped": dropped_n},
                threshold={"cap": cap, "max_age": max_age},
            )
        return HealthCheck(
            code="queue",
            status=HealthStatus.DEGRADED,
            summary=f"queue elevated size={qsize_n}/{cap} age={qage_n:.1f}s",
            observed={"size": qsize_n, "age": qage_n},
            threshold={"cap": cap, "max_age": max_age},
        )
    return HealthCheck(
        code="queue",
        status=HealthStatus.OK,
        summary=f"queue {qsize_n}/{cap} age {qage_n:.1f}s",
        observed={"size": qsize_n, "age": qage_n},
        threshold={"cap": cap},
    )


# The runtime's decisions loop schedules a reconciliation every 60 s
# (runtime.py, ``next_reconcile``); it is a runtime constant, not a config field.
_RECONCILE_INTERVAL_SECONDS = 60.0


def _heartbeat_tolerance_seconds(config: Config) -> float:
    """Heartbeat age past which the runtime is stale: 90 s, or three snapshot intervals.

    The snapshot is written once per ``snapshot_interval_seconds``, so a fixed
    90 s tolerance reads a healthy runtime as dead whenever the configured
    interval is longer than that.
    """
    return max(90.0, 3.0 * float(config.observability.snapshot_interval_seconds))


def _account_stale_seconds(config: Config) -> float:
    """Age of ``last_reconciliation_at`` past which the account is stale.

    A healthy runtime's stamp can be one reconciliation interval old when the
    snapshot is written, and the snapshot itself up to one snapshot interval
    old when read; one account poll interval is the margin. Never tighter than
    the previous ``maximum_account_age_for_entry_seconds * 2``.
    """
    derived = (
        _RECONCILE_INTERVAL_SECONDS
        + float(config.observability.snapshot_interval_seconds)
        + float(config.engine.account_poll_interval_seconds)
    )
    return max(derived, float(config.engine.maximum_account_age_for_entry_seconds) * 2)


def _check_account(health_snapshot: dict[str, Any] | None, now: datetime, config: Config) -> HealthCheck:
    # account freshness is not persisted directly; infer from reconciliation / health
    # Use last_reconciliation as proxy if no direct account timestamp.
    if health_snapshot is None:
        return HealthCheck(code="account", status=HealthStatus.UNKNOWN, summary="no health snapshot")
    # Check market health entry halt but account itself: use account_poll_interval
    # For offline diagnosis, we treat stale if last_reconciliation is too old? Instead UNKNOWN.
    # Simpler: check if any market entry_halt due to stale account
    # Try to find stale indicator in runtime_state? Offline we fallback to UNKNOWN if no signal.
    # Use last_reconciliation age as account proxy
    last_rec = health_snapshot.get("last_reconciliation_at")
    max_age = _account_stale_seconds(config)
    age = _age_seconds(last_rec, now) if isinstance(last_rec, str) else None
    if age is None:
        return HealthCheck(code="account", status=HealthStatus.UNKNOWN, summary="no reconciliation timestamp")
    if age > max_age:
        return HealthCheck(
            code="account",
            status=HealthStatus.DEGRADED,
            summary=f"account/reconciliation stale {age:.0f}s ago",
            observed=age,
            threshold=max_age,
            action="check broker connectivity",
        )
    return HealthCheck(
        code="account", status=HealthStatus.OK, summary=f"fresh {age:.0f}s ago", observed=age, threshold=max_age
    )


def _check_reconciliation(health_snapshot: dict[str, Any] | None, now: datetime, config: Config) -> HealthCheck:
    if health_snapshot is None:
        return HealthCheck(code="reconciliation", status=HealthStatus.UNKNOWN, summary="no runtime health")
    # Freshness is judged on the last CLEAN reconcile: a drift result refreshes
    # last_reconciliation_at while the drift halt persists (R9-4). Snapshots
    # written before the clean stamp existed fall back to the old field.
    if "last_clean_reconciliation_at" in health_snapshot:
        last = health_snapshot.get("last_clean_reconciliation_at")
    else:
        last = health_snapshot.get("last_reconciliation_at")
    if not isinstance(last, str) or not last:
        return HealthCheck(code="reconciliation", status=HealthStatus.UNKNOWN, summary="no reconciliation timestamp")
    age = _age_seconds(last, now)
    if age is None:
        return HealthCheck(
            code="reconciliation", status=HealthStatus.UNKNOWN, summary="reconciliation timestamp corrupt"
        )
    # Derive threshold from account poll + event grace: poll 15s, allow 5* poll
    threshold = max(300.0, config.engine.account_poll_interval_seconds * 20)
    # Also check runtime metrics persisted risk? For now simple age
    if age > threshold:
        return HealthCheck(
            code="reconciliation",
            status=HealthStatus.CRITICAL,
            summary=f"reconciliation stale {age:.0f}s ago",
            observed=age,
            threshold=threshold,
            action="check broker / ledger locks",
        )
    # check if last failure exists via journal? Offline we can't; just OK if recent success
    return HealthCheck(
        code="reconciliation", status=HealthStatus.OK, summary=f"{age:.0f}s ago", observed=age, threshold=threshold
    )


def _check_signal_adjustment(health_snapshot: dict[str, Any] | None) -> HealthCheck:
    if health_snapshot is None:
        return HealthCheck(code="signal_adjustment", status=HealthStatus.UNKNOWN, summary="no signal data")
    sa = health_snapshot.get("signal_adjustment") or {}
    if not isinstance(sa, dict) or not sa:
        return HealthCheck(code="signal_adjustment", status=HealthStatus.UNKNOWN, summary="no signal_adjustment data")
    healthy = sa.get("healthy")
    stale = sa.get("stale")
    if stale:
        return HealthCheck(
            code="signal_adjustment",
            status=HealthStatus.DEGRADED,
            summary="signal adjustment stale — entries may be blocked",
            observed=sa,
            action="check adjustment verifier logs",
        )
    if healthy is False:
        return HealthCheck(
            code="signal_adjustment",
            status=HealthStatus.DEGRADED,
            summary=f"signal adjustment unhealthy: {sa.get('last_error')}",
            observed=sa,
        )
    return HealthCheck(code="signal_adjustment", status=HealthStatus.OK, summary="verified")


def _check_market_halts(health_snapshot: dict[str, Any] | None) -> HealthCheck:
    # A market entry halt (protection failure, ambiguous exit, unreconciled
    # rebalance, invalid universe, ...) stops that market's entries: never OK.
    if health_snapshot is None:
        return HealthCheck(code="market_halts", status=HealthStatus.UNKNOWN, summary="no market data")
    markets = health_snapshot.get("markets")
    halts = (
        {
            m: info["entry_halt_reason"]
            for m, info in markets.items()
            if isinstance(info, dict) and info.get("entry_halt_reason")
        }
        if isinstance(markets, dict)
        else {}
    )
    if halts:
        return HealthCheck(
            code="market_halts",
            status=HealthStatus.DEGRADED,
            summary="entries halted: " + ", ".join(f"{m}={reason}" for m, reason in sorted(halts.items())),
            observed=halts,
            action="resolve the halt reason; reset_safety_lock verifies and clears it",
        )
    return HealthCheck(code="market_halts", status=HealthStatus.OK, summary="no market entry halts")


def _ledger_tables(db_path: str | Path) -> frozenset[str] | None:
    """Tables of the ledger opened read-only; None when its schema is unreadable.

    A zero-byte or foreign SQLite file reads fine but lacks the ledger tables,
    so callers check for the table they need instead of trusting "readable".
    """
    try:
        conn = sqlite3.connect(f"file:{Path(str(db_path)).resolve()}?mode=ro", uri=True, timeout=2.0)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        finally:
            conn.close()
        return frozenset(str(row[0]) for row in rows)
    except sqlite3.Error:
        log.warning("decision DB unreadable at %s", db_path, exc_info=True)
        return None


# The runtime persists every breaker reason in effect, but never restores or
# clears the shared-worker f"{name}_restart_limit" halts (runtime.py
# _restore_persistent_state): the row stays as an audit trail after a restart
# re-arms the worker. A live one shows through the snapshot (dead worker,
# circuit_breaker_reason), so the row itself is not an active block.
_UNRESTORED_INCIDENT_SUFFIX = "_restart_limit"


def _unrestored_incident(incident: object) -> bool:
    return isinstance(incident, dict) and str(incident.get("reason") or "").endswith(_UNRESTORED_INCIDENT_SUFFIX)


def _check_risk(db_path: str | Path | None, health_snapshot: dict[str, Any] | None) -> HealthCheck:
    # Check entry latch and active incidents. The runtime always reports every
    # market ({"equity": None, ...}); only a market with a reason is latched.
    latch = {}
    if health_snapshot and isinstance(health_snapshot.get("entry_risk_latch"), dict):
        latch = {market: reason for market, reason in health_snapshot["entry_risk_latch"].items() if reason}
    if latch:
        return HealthCheck(
            code="risk",
            status=HealthStatus.BLOCKED,
            summary=f"entry latch active: {latch}",
            observed=latch,
            action="risk latch blocks new entries; check drawdown/daily loss",
        )
    # Fail closed: with no explicitly configured, readable ledger the persisted
    # incidents are unknown — never "no risk block". runtime_metrics(None) would
    # otherwise fall back to a cwd-relative ledger, and it answers an unreadable
    # one with the empty shape.
    if db_path is None or not Path(str(db_path)).exists():
        return HealthCheck(code="risk", status=HealthStatus.UNKNOWN, summary="no decision DB")
    tables = _ledger_tables(db_path)
    if tables is None:
        return HealthCheck(code="risk", status=HealthStatus.UNKNOWN, summary="decision DB unreadable")
    if "safety_incidents" not in tables:
        return HealthCheck(
            code="risk", status=HealthStatus.UNKNOWN, summary="decision DB has no safety_incidents table"
        )
    # One strict ledger read of the persisted incidents.
    # strict: a failed incidents read is UNKNOWN, never an empty "no incidents"
    try:
        snapshot: dict[str, Any] = metrics.runtime_metrics(database_path=db_path, strict=True)
    except Exception as exc:
        return HealthCheck(code="risk", status=HealthStatus.UNKNOWN, summary=f"risk check failed: {exc}")
    # Check persisted incidents
    try:
        incidents = snapshot.get("incidents") or {}
        active = incidents.get("active") if isinstance(incidents, dict) else None
        if isinstance(active, list):
            active = [item for item in active if not _unrestored_incident(item)]
        if isinstance(active, list) and active:
            # If any incident is BLOCKED-like, treat as BLOCKED else CRITICAL
            return HealthCheck(
                code="risk",
                status=HealthStatus.BLOCKED,
                summary=f"{len(active)} active safety incidents",
                observed=active[:3],
                action="review safety_incidents table",
            )
    except Exception:
        pass
    # A drawdown halt (entry_block_duration_seconds) is its own active
    # portfolio_drawdown_halt incident, already reported above.
    return HealthCheck(code="risk", status=HealthStatus.OK, summary="no risk block")


def _check_execution(db_path: str | Path | None) -> HealthCheck:
    try:
        if db_path is None or not Path(str(db_path)).exists():
            return HealthCheck(code="execution", status=HealthStatus.UNKNOWN, summary="no decision DB")
        tables = _ledger_tables(db_path)
        if tables is None:
            return HealthCheck(code="execution", status=HealthStatus.UNKNOWN, summary="decision DB unreadable")
        if "portfolio_orders" not in tables:
            return HealthCheck(
                code="execution", status=HealthStatus.UNKNOWN, summary="decision DB has no portfolio_orders table"
            )
        from parayabuck.monitoring import forensics

        # strict reads: an unreadable ledger must surface as UNKNOWN below, not as
        # an empty result that reads as "no stuck/ambiguous orders"
        # ambiguous: orders the ledger could not resolve — operator action required
        ambiguous = forensics.ambiguous_orders(db_path, strict=True)
        if ambiguous:
            return HealthCheck(
                code="execution",
                status=HealthStatus.CRITICAL,
                summary=f"{len(ambiguous)} ambiguous orders require manual intervention",
                observed=len(ambiguous),
                action="resolve ambiguous orders before trading resumes",
            )
        # submitting: an order that never got an ack and is older than 10 minutes
        now = datetime.now(UTC)
        stuck: list[Any] = []
        for order in forensics.submitting_orders(db_path, strict=True):
            created = str(order.get("created_at") or "")
            if not created:
                continue
            age = _age_seconds(created, now)
            if age is None or age > 600:
                stuck.append(order)
        if stuck:
            return HealthCheck(
                code="execution",
                status=HealthStatus.DEGRADED,
                summary=f"{len(stuck)} stuck submitting orders >10m",
                observed=len(stuck),
            )
        stuck_rb = forensics.find_stuck_rebalances(db_path, threshold_seconds=3600, strict=True)
        if stuck_rb:
            return HealthCheck(
                code="execution",
                status=HealthStatus.DEGRADED,
                summary=f"{len(stuck_rb)} stuck rebalance executions >1h",
                observed=len(stuck_rb),
            )
        return HealthCheck(code="execution", status=HealthStatus.OK, summary="no stuck/ambiguous orders")
    except Exception as exc:
        return HealthCheck(code="execution", status=HealthStatus.UNKNOWN, summary=f"execution check failed: {exc}")


def _check_observability(journal_state: dict[str, Any] | None) -> HealthCheck:
    """The event journal as reported by the engine process."""
    if journal_state is None:
        return HealthCheck(
            code="observability",
            status=HealthStatus.UNKNOWN,
            summary="no journal state persisted by the engine",
        )
    failed = int(journal_state.get("event_journal_failed", 0) or 0)
    dropped = int(journal_state.get("event_journal_dropped", 0) or 0)
    # The counters are process-lifetime totals: a burst that the journal has
    # since recovered from must not pin DEGRADED until the next restart. Only
    # a failure newer than the last successful write is a current failure.
    last_failure = _parse_iso(journal_state.get("event_journal_last_failure_at"))
    last_success = _parse_iso(journal_state.get("event_journal_last_success_at"))
    if last_failure is not None and (last_success is None or last_failure >= last_success):
        return HealthCheck(
            code="observability",
            status=HealthStatus.DEGRADED,
            summary=f"journal failing (failed={failed} dropped={dropped}; last failure {last_failure.isoformat()})",
            observed=journal_state,
            action="check data/runtime disk space and permissions; `parayabuck ops doctor`",
        )
    summary = (
        "journal healthy"
        if not (failed or dropped)
        else f"journal healthy (recovered; failed={failed} dropped={dropped})"
    )
    return HealthCheck(code="observability", status=HealthStatus.OK, summary=summary)


def _check_intent_watchdog(health_snapshot: dict[str, Any] | None) -> HealthCheck:
    """The intent-vs-reality watchdog state the runtime published.

    A missed scheduled decision keeps the runtime alive and every liveness
    check green (2026-09-25..29), so this is the only surface that says the
    strategy stopped acting while the process looked healthy.
    """
    watchdog = health_snapshot.get("intent_watchdog") if isinstance(health_snapshot, dict) else None
    if not isinstance(watchdog, dict) or not watchdog:
        return HealthCheck(
            code="intent_watchdog",
            status=HealthStatus.UNKNOWN,
            summary="no intent watchdog state in the health snapshot",
        )
    if watchdog.get("enabled") is False:
        return HealthCheck(code="intent_watchdog", status=HealthStatus.OK, summary="watchdog disabled")
    if not watchdog.get("evaluated_at"):
        return HealthCheck(
            code="intent_watchdog",
            status=HealthStatus.UNKNOWN,
            summary="watchdog has not evaluated yet",
        )
    codes = [str(code) for code in (watchdog.get("degraded_reasons") or [])]
    if not codes:
        return HealthCheck(
            code="intent_watchdog",
            status=HealthStatus.OK,
            summary=f"no alarms (evaluated {watchdog.get('evaluated_at')})",
        )
    critical = sorted(code for code in codes if code in _INTENT_CRITICAL_CODES)
    summary = "active alarms: " + ", ".join(codes)
    if critical:
        return HealthCheck(
            code="intent_watchdog",
            status=HealthStatus.CRITICAL,
            summary=summary,
            observed=codes,
            action="check the decision schedule and the engine journal for the named session",
        )
    return HealthCheck(code="intent_watchdog", status=HealthStatus.DEGRADED, summary=summary, observed=codes)


# Signal cross-check outcomes (portfolio.signal_cross_check STATUS_* values).
_CROSS_CHECK_AGREE = "agree"


def _check_signal_cross_check(health_snapshot: dict[str, Any] | None) -> HealthCheck:
    """IEX-stream vs SIP-daily cross-check of the last equity decision."""
    cross_check = health_snapshot.get("signal_cross_check") if isinstance(health_snapshot, dict) else None
    if not isinstance(cross_check, dict) or not cross_check:
        return HealthCheck(
            code="signal_cross_check",
            status=HealthStatus.UNKNOWN,
            summary="no signal cross-check state in the health snapshot",
        )
    if cross_check.get("enabled") is False:
        return HealthCheck(code="signal_cross_check", status=HealthStatus.OK, summary="cross-check disabled")
    # A session still waiting for its SIP daily bar must not erase the last
    # completed comparison — that would make every post-close embargo window
    # read UNKNOWN. Only a runtime that has never finished one is UNKNOWN.
    pending = cross_check.get("pending")
    pending_note = (
        "; waiting for the SIP daily bar: " + ", ".join(sorted(str(s) for s in pending))
        if isinstance(pending, dict) and pending
        else ""
    )
    last = cross_check.get("last")
    if not isinstance(last, dict) or not last:
        return HealthCheck(
            code="signal_cross_check",
            status=HealthStatus.UNKNOWN,
            summary="no session cross-checked since start",
        )
    status = str(last.get("status") or "unknown")
    session = last.get("session_date")
    if status == _CROSS_CHECK_AGREE and not last.get("hold"):
        return HealthCheck(
            code="signal_cross_check",
            status=HealthStatus.OK,
            summary=f"sources agree on the trend (session {session}){pending_note}",
        )
    detail = f"session {session}, status {status}"
    if last.get("hold"):
        detail += ", position held"
    if last.get("reason"):
        detail += f" ({last.get('reason')})"
    return HealthCheck(
        code="signal_cross_check",
        status=HealthStatus.DEGRADED,
        summary=detail,
        observed={"session_date": session, "status": status},
        action="confirm the IEX and SIP daily bars agree before relying on the position",
    )


def evaluate_health(
    *,
    snapshot_path: str | Path = "data/runtime/health_snapshot.json",
    database_path: str | Path | None = None,
    config: Config | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate all health checks and derive overall status.

    Returns dict with overall_health, trading_state, checks, etc.
    """
    now = now or datetime.now(UTC)
    snapshot_path = Path(snapshot_path)
    if config is None:
        try:
            config = load_config()
        except Exception:
            config = Config()

    health_snapshot: dict[str, Any] | None = None
    if snapshot_path.exists():
        try:
            health_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            # snapshot may wrap RuntimeHealth under 'runtime_health' or be flat
            # Normalize: if it has 'runtime_health' key, use that for raw health, but keep outer for system fields
            inner = (
                health_snapshot.get("runtime_health")
                if isinstance(health_snapshot.get("runtime_health"), dict)
                else None
            )
            if inner is not None:
                # merge for checks that expect flat keys
                merged = {**health_snapshot, **inner}
                # preserve workers/markets from inner if outer missing
                health_snapshot = merged
        except Exception:
            health_snapshot = None

    checks: list[HealthCheck] = []
    checks.append(_check_runtime_heartbeat(snapshot_path, now, _heartbeat_tolerance_seconds(config)))
    checks.append(_check_workers(health_snapshot, now, config))
    checks.append(_check_queue(health_snapshot, config))
    checks.append(_check_account(health_snapshot, now, config))
    checks.append(_check_reconciliation(health_snapshot, now, config))
    checks.append(_check_signal_adjustment(health_snapshot))
    checks.append(_check_market_halts(health_snapshot))
    checks.append(_check_risk(database_path, health_snapshot))
    checks.append(_check_execution(database_path))
    checks.append(_check_intent_watchdog(health_snapshot))
    checks.append(_check_signal_cross_check(health_snapshot))
    checks.append(_check_observability(persisted_journal_state(health_snapshot)))

    # Derive overall
    statuses = [c.status for c in checks]
    if any(s == HealthStatus.CRITICAL for s in statuses):
        overall = HealthStatus.CRITICAL
    elif any(s == HealthStatus.BLOCKED for s in statuses):
        # BLOCKED is trading intentionally blocked, but system may still be healthy infra
        # Precedence: CRITICAL > BLOCKED > DEGRADED > UNKNOWN > OK per spec
        overall = HealthStatus.BLOCKED
    elif any(s == HealthStatus.DEGRADED for s in statuses):
        overall = HealthStatus.DEGRADED
    elif any(s == HealthStatus.UNKNOWN for s in statuses):
        overall = HealthStatus.UNKNOWN
    else:
        overall = HealthStatus.OK

    # Trading state
    trading_state = _derive_trading_state(health_snapshot, checks)

    # Effective snapshot age
    snapshot_age: float | None = None
    if snapshot_path.exists() and health_snapshot is not None:
        try:
            written_at = _parse_iso(health_snapshot.get("written_at") or health_snapshot.get("as_of"))
            if written_at:
                snapshot_age = (now - written_at).total_seconds()
        except Exception:
            snapshot_age = None

    return {
        "schema_version": 1,
        "as_of": now.isoformat(),
        "snapshot_path": str(snapshot_path),
        "snapshot_age_seconds": snapshot_age,
        "overall_health": str(overall),
        "trading_state": str(trading_state),
        "checks": [c.to_dict() for c in checks],
    }


def _derive_trading_state(health_snapshot: dict[str, Any] | None, checks: list[HealthCheck]) -> TradingState:
    if health_snapshot is None:
        return TradingState.UNKNOWN
    # Check circuit breaker
    cb = health_snapshot.get("circuit_breaker_reason") if isinstance(health_snapshot, dict) else None
    if cb:
        return TradingState.CIRCUIT_BREAKER
    state = str(health_snapshot.get("runtime_state") or health_snapshot.get("state") or "")
    if state in {"stopped", "stop_failed", "halted"}:
        if state == "halted":
            return TradingState.CIRCUIT_BREAKER
        return TradingState.STOPPED
    # Check risk latch => ENTRY_BLOCKED
    for c in checks:
        if c.code == "risk" and c.status == HealthStatus.BLOCKED:
            return TradingState.ENTRY_BLOCKED
        if c.code == "execution" and c.status == HealthStatus.CRITICAL:
            return TradingState.REBALANCE_BLOCKED
    # Market halt: check markets entry_halt
    markets = health_snapshot.get("markets") if isinstance(health_snapshot, dict) else None
    if isinstance(markets, dict):
        for _m, info in markets.items():
            if isinstance(info, dict) and info.get("entry_halt_reason"):
                return TradingState.MARKET_HALTED
    # Healthy running => ACTIVE
    healthy = health_snapshot.get("healthy")
    if healthy is True or state == "running":
        # check if any worker critical would still be active? But if running and no block => ACTIVE
        for c in checks:
            if c.code in {"runtime_heartbeat", "workers"} and c.status in {HealthStatus.CRITICAL, HealthStatus.UNKNOWN}:
                return TradingState.UNKNOWN
            # A missed scheduled decision: the process is alive but not trading, so never ACTIVE
            if c.code == "intent_watchdog" and c.status == HealthStatus.CRITICAL:
                return TradingState.UNKNOWN
        return TradingState.ACTIVE
    return TradingState.UNKNOWN


__all__ = [
    "HealthCheck",
    "HealthStatus",
    "TradingState",
    "evaluate_health",
    "persisted_journal_state",
]
