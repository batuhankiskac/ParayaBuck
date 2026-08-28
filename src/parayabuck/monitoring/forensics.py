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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class OrderForensic:
    client_order_id: str
    decision_id: str
    symbol: str
    market: str
    side: str
    quantity: float
    status: str
    broker_order_id: str
    created_at: str
    age_seconds: float | None = None


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
            rows = conn.execute("SELECT * FROM portfolio_orders").fetchall()
            terminal = {"filled", "canceled", "cancelled", "rejected", "expired", "done", "complete"}
            out = []
            for r in rows:
                status = str(r["status"] or "")
                if not include_terminal and status.lower() in terminal:
                    continue
                out.append(dict(r))
            return out
        finally:
            conn.close()
    except Exception:
        log.warning("forensics unresolved_orders query failed", exc_info=True)
        return []


def ambiguous_orders(db_path: str | Path | None) -> list[dict[str, Any]]:
    all_orders = unresolved_orders(db_path, include_terminal=False)
    return [
        o
        for o in all_orders
        if str(o.get("status", "")).lower() in {"ambiguous", "manual_intervention_required", "reconciliation_required"}
    ]


def submitting_orders(db_path: str | Path | None) -> list[dict[str, Any]]:
    all_orders = unresolved_orders(db_path, include_terminal=False)
    return [o for o in all_orders if str(o.get("status", "")).lower() == "submitting"]


def find_stuck_rebalances(db_path: str | Path | None, *, threshold_seconds: float = 3600) -> list[dict[str, Any]]:
    """Rebalances remaining in non-terminal phase longer than threshold."""
    if db_path is None or not Path(str(db_path)).exists():
        return []
    terminal_phases = {"COMPLETE", "BLOCKED", "complete", "blocked"}
    stuck: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "portfolio_rebalance_executions"):
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

                    # Sort by started_at
                    def _ts(s: dict[str, Any]) -> str:
                        return str(s.get("started_at") or "")

                    sessions.sort(key=_ts)
                    unclean: list[dict[str, Any]] = []
                    for idx, s in enumerate(sessions):
                        ended = s.get("ended_at")
                        is_last = idx == len(sessions) - 1
                        if not ended and not is_last:
                            unclean.append(s)
                    current = sessions[-1] if sessions else None
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

    # Try to find paper receipt
    try:
        from pathlib import Path as _P

        receipt_dir = _P("data/runtime/paper_receipts")
        if receipt_dir.exists():
            for p in receipt_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    # heuristic: check decision_id fields
                    if decision_id in json.dumps(data):
                        result["receipt_path"] = str(p)
                        result["receipt"] = data
                        break
                except Exception:
                    continue
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


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return cur.fetchone() is not None
    except Exception:
        return False


# ------------------------------------------------------------------ No-trade vocabulary
NO_TRADE_REASONS: tuple[str, ...] = (
    "NO_TARGET_CHANGE",
    "REBALANCE_BAND",
    "MIN_NOTIONAL",
    "NO_QUALIFIED_CANDIDATES",
    "RISK_BLOCKED",
    "ENTRY_LATCH",
    "ACCOUNT_STALE",
    "UNIVERSE_UNAVAILABLE",
    "SELL_PHASE_PENDING",
    "AMBIGUOUS_ORDER",
    "EXTERNAL_OWNERSHIP",
    "MARKET_DISABLED",
)


def map_no_trade_reason(detail: str | None) -> str:
    """Map ad-hoc no-trade strings into canonical vocabulary (preserves original)."""
    if not detail:
        return "NO_TARGET_CHANGE"
    lower = detail.lower()
    mapping = {
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
        "sell phase": "SELL_PHASE_PENDING",
        "ambiguous": "AMBIGUOUS_ORDER",
        "external": "EXTERNAL_OWNERSHIP",
        "market_disabled": "MARKET_DISABLED",
        "no_target": "NO_TARGET_CHANGE",
    }
    for key, canon in mapping.items():
        if key in lower:
            return canon
    return "NO_TARGET_CHANGE"


__all__ = [
    "NO_TRADE_REASONS",
    "ambiguous_orders",
    "decision_reconstruction",
    "find_stuck_rebalances",
    "map_no_trade_reason",
    "runtime_session_forensics",
    "submitting_orders",
    "unresolved_orders",
]
