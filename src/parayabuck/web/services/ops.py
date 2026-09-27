"""Ops read model — reuses Mission 5 monitoring (extracted for Mission 6)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.ops")

_OPS_LIMIT_MAX = 1000


def _current_config() -> Any | None:
    """Engine config from WebState, else from the legacy server singleton."""
    from parayabuck.config import load_config

    try:
        from parayabuck.web.state import get_web_state

        ws = get_web_state()
        if ws and getattr(ws.engine_manager, "config_path", None):
            return load_config(str(ws.engine_manager.config_path))
    except Exception:
        pass
    try:
        import parayabuck.web.server as srv

        return load_config(str(srv.manager.config_path))
    except Exception:
        return None


def _resolve_db_path(cfg: Any | None) -> str:
    if cfg is not None:
        try:
            return str(getattr(cfg.runtime, "decision_db_path", "data/runtime/parayabuck.sqlite3"))
        except Exception:
            pass
    # fallback via manager config path
    cfg = _current_config()
    if cfg is not None:
        try:
            return str(getattr(cfg.runtime, "decision_db_path", "data/runtime/parayabuck.sqlite3"))
        except Exception:
            pass
    return "data/runtime/parayabuck.sqlite3"


def get_ops_status(
    snapshot_path: str | Path | None = None, db_path: str | Path | None = None, config: Any | None = None
) -> dict[str, Any]:
    try:
        from parayabuck.monitoring.health import evaluate_health

        cfg = config if config is not None else _current_config()
        snap = str(snapshot_path) if snapshot_path else "data/runtime/health_snapshot.json"
        db = str(db_path) if db_path else _resolve_db_path(cfg)
        if cfg is not None:
            try:
                snap = getattr(cfg.observability, "snapshot_path", snap)
                db = getattr(cfg.runtime, "decision_db_path", db)
            except Exception:
                pass
        return evaluate_health(snapshot_path=snap, database_path=db, config=cfg)
    except Exception as exc:  # pragma: no cover
        log.warning("ops status probe failed: %s", exc)
        return {
            "error": str(exc),
            "overall_health": "UNKNOWN",
            "trading_state": "UNKNOWN",
            "checks": [],
            "as_of": datetime.now(UTC).isoformat(),
        }


def query_events(limit: int = 100, **filters: Any) -> list[dict[str, Any]]:
    # Bounded limit <=1000
    try:
        limit = int(limit)
    except Exception:
        limit = 100
    limit = max(1, min(limit, _OPS_LIMIT_MAX))
    # Normalize severity filter if present
    sev = filters.get("severity")
    if isinstance(sev, str) and sev.strip():
        # The journal stores the uppercase Severity vocabulary, so normalize to
        # it.  An unrecognised severity stays in the filter and matches
        # nothing; dropping it would silently widen the query to every event.
        filters["severity"] = sev.strip().upper()
    try:
        from parayabuck.monitoring.journal import get_journal

        j = get_journal()
        events = j.query(limit=limit, **filters)
        # Convert to dict for JSON
        out: list[dict[str, Any]] = []
        for e in events:
            try:
                out.append(e.to_dict())
            except Exception:
                out.append({"kind": getattr(e, "kind", ""), "message": getattr(e, "message", "")})
        return out
    except Exception as exc:  # pragma: no cover
        log.warning("event query failed: %s", exc)
        return []


def get_incidents(limit: int = 100) -> list[dict[str, Any]]:
    try:
        limit = max(1, min(int(limit), _OPS_LIMIT_MAX))
    except Exception:
        limit = 100
    # Prefer querying safety_incidents table directly (forensics reuses same table)
    db_path = _resolve_db_path(_current_config())
    if not Path(str(db_path)).exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='safety_incidents'")
            if cur.fetchone() is None:
                return []
            rows = conn.execute(
                "SELECT id, account_id, reason, scope, activated_at, updated_at FROM safety_incidents ORDER BY activated_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        log.warning("incidents query failed: %s", exc)
        return []


def get_timeline(decision_id: str) -> dict[str, Any]:
    if not decision_id or not decision_id.strip():
        return {"decision_id": decision_id, "error": "decision_id required", "unavailable": True}
    decision_id = decision_id.strip()
    # Reuse forensics without duplication
    try:
        from parayabuck.monitoring.forensics import decision_reconstruction

        # decision_reconstruction expects (db_path, decision_id)
        db_path = _resolve_db_path(_current_config())
        return decision_reconstruction(db_path, decision_id)
    except Exception as exc:  # pragma: no cover
        log.warning("timeline reconstruction failed: %s", exc)
        return {"error": str(exc), "decision_id": decision_id, "unavailable": True}


def get_latest_decision() -> dict[str, Any]:
    try:
        db_path = Path(_resolve_db_path(_current_config()))
        if not db_path.exists():
            return {"decision": None, "unavailable": True, "reason": "no decision DB", "overall_health": "UNKNOWN"}
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            # need to check table exists
            cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='portfolio_decisions'")
            if cur.fetchone() is None:
                return {"decision": None, "unavailable": True, "reason": "no decisions table"}
            row = conn.execute("SELECT * FROM portfolio_decisions ORDER BY decision_timestamp DESC LIMIT 1").fetchone()
            if not row:
                return {"decision": None, "unavailable": True, "reason": "no decisions yet"}
            data = dict(row)
            # parse json convenience
            for key in ("target_json", "risk_json"):
                if data.get(key):
                    try:
                        import json

                        data[key + "_parsed"] = json.loads(data[key])
                    except Exception:
                        pass
            return {"decision": data}
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover
        log.warning("latest decision query failed: %s", exc)
        return {"decision": None, "unavailable": True, "error": str(exc)}
