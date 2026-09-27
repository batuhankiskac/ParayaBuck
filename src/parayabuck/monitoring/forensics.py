"""Offline forensic queries — reusable read models over portfolio repository.

Provides:
* unresolved / ambiguous / stuck orders
* stuck rebalance executions
* runtime session forensics (unclean shutdown)
* decision reconstruction (decision + orders + fills + phases + receipts + events)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("parayabuck.monitoring.forensics")


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


_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"filled", "canceled", "cancelled", "rejected", "expired", "done", "complete"}
)
_AMBIGUOUS_STATUSES: frozenset[str] = frozenset(
    {"ambiguous", "manual_intervention_required", "reconciliation_required"}
)


def _status_variants(statuses: frozenset[str]) -> tuple[str, ...]:
    """Lower/Title/UPPER spellings of ``statuses`` for case-insensitive SQL matching."""
    return tuple(sorted({value for status in statuses for value in (status, status.capitalize(), status.upper())}))


def _orders_with_status(
    db_path: str | Path | None, wanted: frozenset[str], *, strict: bool = False
) -> list[dict[str, Any]]:
    """Orders whose status is one of ``wanted`` (read-only).

    The SQL comparison normalises the column with ``lower()`` so a row written
    with any casing (``Ambiguous``, ``AMBIGUOUS``) is never dropped before the
    Python check below sees it; that check stays authoritative.

    Terminal statuses are excluded defensively so callers asking for a
    non-terminal status never see a settled order.

    ``strict=True`` re-raises a read failure instead of answering ``[]``, so a
    health verdict can tell "no such orders" from "ledger unreadable".
    """
    if db_path is None or not Path(str(db_path)).exists():
        return []
    wanted = wanted - _TERMINAL_STATUSES
    if not wanted:
        return []
    lowered = tuple(sorted(wanted))
    placeholders = ",".join("?" * len(lowered))
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "portfolio_orders", strict=strict):
                return []
            rows = conn.execute(
                f"SELECT * FROM portfolio_orders WHERE lower(status) IN ({placeholders})",
                lowered,
            ).fetchall()
            return [dict(r) for r in rows if str(r["status"] or "").lower() in wanted]
        finally:
            conn.close()
    except Exception:
        if strict:
            raise
        log.warning("forensics status query failed for %s", sorted(wanted), exc_info=True)
        return []


def unresolved_orders(db_path: str | Path | None, *, include_terminal: bool = False) -> list[dict[str, Any]]:
    """Return non-terminal portfolio orders (read-only)."""
    if db_path is None or not Path(str(db_path)).exists():
        return []
    # Raw SQL fallback
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "portfolio_orders"):
                return []
            if include_terminal:
                rows = conn.execute("SELECT * FROM portfolio_orders").fetchall()
            else:
                terminal = _status_variants(_TERMINAL_STATUSES)
                placeholders = ",".join("?" * len(terminal))
                rows = conn.execute(
                    f"SELECT * FROM portfolio_orders WHERE status NOT IN ({placeholders})",
                    terminal,
                ).fetchall()
            out = []
            for r in rows:
                status = str(r["status"] or "")
                if not include_terminal and status.lower() in _TERMINAL_STATUSES:
                    continue
                out.append(dict(r))
            return out
        finally:
            conn.close()
    except Exception:
        log.warning("forensics unresolved_orders query failed", exc_info=True)
        return []


def ambiguous_orders(db_path: str | Path | None, *, strict: bool = False) -> list[dict[str, Any]]:
    return _orders_with_status(db_path, _AMBIGUOUS_STATUSES, strict=strict)


def submitting_orders(db_path: str | Path | None, *, strict: bool = False) -> list[dict[str, Any]]:
    return _orders_with_status(db_path, frozenset({"submitting"}), strict=strict)


def find_stuck_rebalances(
    db_path: str | Path | None, *, threshold_seconds: float = 3600, strict: bool = False
) -> list[dict[str, Any]]:
    """Rebalances remaining in non-terminal phase longer than threshold.

    ``strict=True`` re-raises a read failure instead of answering ``[]``.
    """
    if db_path is None or not Path(str(db_path)).exists():
        return []
    terminal_phases = {"COMPLETE", "BLOCKED", "complete", "blocked"}
    stuck: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "portfolio_rebalance_executions", strict=strict):
                return []
            rows = conn.execute("SELECT * FROM portfolio_rebalance_executions").fetchall()
            now = datetime.now(UTC)
            for r in rows:
                phase = str(r["phase"] or "")
                if phase in terminal_phases:
                    continue
                updated_at = _parse_iso(r["updated_at"])
                age = (now - updated_at).total_seconds() if updated_at else None
                if age is not None and age > threshold_seconds:
                    d = dict(r)
                    d["age_seconds"] = age
                    # try to parse buy_orders_json
                    try:
                        d["buy_orders"] = json.loads(r["buy_orders_json"] or "[]")
                    except Exception:
                        d["buy_orders"] = []
                    stuck.append(d)
            return stuck
        finally:
            conn.close()
    except Exception:
        if strict:
            raise
        log.warning("stuck rebalance query failed", exc_info=True)
        return []


def runtime_session_forensics(db_path: str | Path | None) -> dict[str, Any]:
    """Detect unclean previous sessions: no ended_at with newer session exists."""
    if db_path is None or not Path(str(db_path)).exists():
        return {"sessions": [], "unclean": [], "current": None}
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            for table in ("portfolio_runtime_sessions", "runtime_sessions"):
                if _table_exists(conn, table):
                    rows = conn.execute(f"SELECT * FROM {table} ORDER BY started_at ASC").fetchall()
                    sessions = [dict(r) for r in rows]
                    if not sessions:
                        return {"sessions": [], "unclean": [], "current": None}
                    # Rows arrive ordered by started_at; the last one is the current session
                    unclean: list[dict[str, Any]] = []
                    for idx, s in enumerate(sessions):
                        ended = s.get("ended_at")
                        is_last = idx == len(sessions) - 1
                        if not ended and not is_last:
                            unclean.append(s)
                    current = sessions[-1]
                    # If current is running (no ended_at), it's not unclean by itself
                    return {"sessions": sessions, "unclean": unclean, "current": current}
            return {"sessions": [], "unclean": [], "current": None}
        finally:
            conn.close()
    except Exception:
        log.warning("session forensics failed", exc_info=True)
        return {"sessions": [], "unclean": [], "current": None, "error": "query failed"}


def decision_reconstruction(
    db_path: str | Path | None,
    decision_id: str,
    *,
    journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Reconstruct one decision via portfolio_decisions + orders + fills + phases + receipts + events."""
    if db_path is None or not Path(str(db_path)).exists():
        return {"decision_id": decision_id, "error": "no decision DB"}
    result: dict[str, Any] = {"decision_id": decision_id}
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            if _table_exists(conn, "portfolio_decisions"):
                cur = conn.execute("SELECT * FROM portfolio_decisions WHERE decision_id=?", (decision_id,))
                row = cur.fetchone()
                if row:
                    result["decision"] = dict(row)
                    # Try parse JSON fields
                    for key in ("target_json", "risk_json"):
                        if key in row.keys() and row[key]:
                            try:
                                result[key + "_parsed"] = json.loads(row[key])
                            except Exception:
                                result[key + "_parsed"] = row[key]
                else:
                    result["decision"] = None
            if _table_exists(conn, "portfolio_orders"):
                cur = conn.execute("SELECT * FROM portfolio_orders WHERE decision_id=?", (decision_id,))
                result["orders"] = [dict(r) for r in cur.fetchall()]
            else:
                result["orders"] = []
            if _table_exists(conn, "portfolio_fills"):
                # fills linked via client_order_id
                order_ids = [o["client_order_id"] for o in result.get("orders", []) if o.get("client_order_id")]
                fills: list[dict[str, Any]] = []
                for oid in order_ids:
                    cur = conn.execute("SELECT * FROM portfolio_fills WHERE client_order_id=?", (oid,))
                    fills.extend(dict(r) for r in cur.fetchall())
                result["fills"] = fills
            else:
                result["fills"] = []
            if _table_exists(conn, "portfolio_rebalance_executions"):
                cur = conn.execute("SELECT * FROM portfolio_rebalance_executions WHERE decision_id=?", (decision_id,))
                row = cur.fetchone()
                result["rebalance"] = dict(row) if row else None
            else:
                result["rebalance"] = None
        finally:
            conn.close()
    except Exception as exc:
        result["error"] = str(exc)

    # Try to find paper receipt — match the identity fields, never the blob text
    # The runtime writes receipts via receipt.default_receipt_dir(); read that
    # same directory instead of a cwd-relative guess.
    from parayabuck.monitoring import receipt as receipt_module

    try:
        receipt_dir = receipt_module.default_receipt_dir()
        if receipt_dir.exists():
            for p in receipt_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("decision_id") == decision_id or data.get("receipt_id") == decision_id:
                    result["receipt_path"] = str(p)
                    result["receipt"] = data
                    break
                # V1 receipts carry no id field; fall back to a substring probe
                if "decision_id" not in data and "receipt_id" not in data and decision_id in json.dumps(data):
                    result["receipt_path"] = str(p)
                    result["receipt"] = data
                    break
    except Exception:
        pass

    # Operational events correlated
    if journal_path is not None and Path(str(journal_path)).exists():
        try:
            from parayabuck.monitoring.journal import EventJournal

            j = EventJournal(str(journal_path))
            result["operational_events"] = [e.to_dict() for e in j.query(decision_id=decision_id, limit=100)]
        except Exception:
            result["operational_events"] = []
    else:
        # Try default journal
        try:
            from parayabuck.monitoring.journal import get_journal

            j = get_journal()
            if Path(str(j.path)).exists():
                result["operational_events"] = [e.to_dict() for e in j.query(decision_id=decision_id, limit=100)]
            else:
                result["operational_events"] = []
        except Exception:
            result["operational_events"] = []

    return result


def _table_exists(conn: sqlite3.Connection, table: str, *, strict: bool = False) -> bool:
    try:
        cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return cur.fetchone() is not None
    except Exception:
        # An unreadable file is not "table absent"; strict callers must see it
        if strict:
            raise
        return False


# ------------------------------------------------------------------ No-trade vocabulary


_NO_TRADE_PATTERNS: dict[str, str] = {
    "rebalance_band": "REBALANCE_BAND",
    "band": "REBALANCE_BAND",
    "min_notional": "MIN_NOTIONAL",
    "minimum_order_notional": "MIN_NOTIONAL",
    "no_qualified": "NO_QUALIFIED_CANDIDATES",
    "risk": "RISK_BLOCKED",
    "latch": "ENTRY_LATCH",
    "account_stale": "ACCOUNT_STALE",
    "stale_account": "ACCOUNT_STALE",
    "universe": "UNIVERSE_UNAVAILABLE",
    "sell_pending": "SELL_PHASE_PENDING",
    "sell_phase_pending": "SELL_PHASE_PENDING",
    "sell phase": "SELL_PHASE_PENDING",
    "ambiguous": "AMBIGUOUS_ORDER",
    "external": "EXTERNAL_OWNERSHIP",
    "market_disabled": "MARKET_DISABLED",
    "no_target": "NO_TARGET_CHANGE",
}


def map_no_trade_reason(detail: str | None) -> str:
    """Map ad-hoc no-trade strings into canonical vocabulary (preserves original)."""
    if not detail:
        return "NO_TARGET_CHANGE"
    lower = detail.lower()
    for key, canon in _NO_TRADE_PATTERNS.items():
        if key in lower:
            return canon
    return "NO_TARGET_CHANGE"


__all__ = [
    "ambiguous_orders",
    "decision_reconstruction",
    "find_stuck_rebalances",
    "map_no_trade_reason",
    "runtime_session_forensics",
    "submitting_orders",
    "unresolved_orders",
]
