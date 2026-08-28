"""Sanitized diagnostic bundle — one archive for offline diagnosis."""

from __future__ import annotations

import json
import logging
import platform
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("parayabuck.monitoring.diagnostic")

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "secret",
        "password",
        "webhook_url",
        "approval_key",
        "alpaca_api_key",
        "alpaca_secret_key",
        "live_approval_key",
    }
)

_SENSITIVE_SUBSTRINGS = ("secret", "webhook", "password", "approval_key", "api_key")


def _is_sensitive(key: str) -> bool:
    low = key.lower()
    if low in _SENSITIVE_KEYS:
        return True
    return any(s in low for s in _SENSITIVE_SUBSTRINGS)


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if _is_sensitive(str(k)):
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact(v) for v in obj)
    return obj


def _safe_git_info() -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=2).strip()
        info["head"] = head
    except Exception:
        info["head"] = "unknown"
    try:
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True, timeout=2).strip()
        info["dirty"] = bool(dirty)
        info["dirty_files"] = dirty.splitlines()[:20] if dirty else []
    except Exception:
        info["dirty"] = None
    return info


def _environment_info() -> dict[str, Any]:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "parayabuck_version": _parayabuck_version(),
    }


def _parayabuck_version() -> str:
    try:
        import parayabuck

        return getattr(parayabuck, "__version__", "unknown")
    except Exception:
        return "unknown"


def _collect_status(snapshot_path: str | Path = "data/runtime/health_snapshot.json") -> dict[str, Any]:
    try:
        from parayabuck.monitoring.health import evaluate_health

        return evaluate_health(snapshot_path=snapshot_path)
    except Exception as exc:
        return {"error": str(exc)}


def _collect_doctor() -> dict[str, Any]:
    try:
        from parayabuck.monitoring.diagnostic import _run_doctor_checks

        return _run_doctor_checks()
    except Exception:
        # fallback: import from cli helpers later
        return {}


def collect_bundle_data(
    *,
    config_path: str | Path = "config/settings.yaml",
    snapshot_path: str | Path = "data/runtime/health_snapshot.json",
    journal_path: str | Path = "data/runtime/observability.sqlite3",
    decision_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Collect all bundle sections as dict (redacted)."""
    data: dict[str, Any] = {}
    data["manifest"] = {
        "collected_at": datetime.now(UTC).isoformat(),
        "schema_version": 1,
    }
    # status
    try:
        from parayabuck.monitoring.health import evaluate_health

        data["status"] = evaluate_health(snapshot_path=snapshot_path, database_path=decision_db_path)
    except Exception as exc:
        data["status"] = {"error": str(exc)}

    # doctor
    try:
        # lazy import to avoid cycle
        from parayabuck.monitoring.diagnostic import _run_doctor_checks

        data["doctor"] = _run_doctor_checks(
            config_path=config_path,
            snapshot_path=snapshot_path,
            journal_path=journal_path,
            decision_db_path=decision_db_path,
        )
    except Exception:
        try:
            # alternative: call via cli module

            # use internal if exists
            data["doctor"] = {}
        except Exception as exc:
            data["doctor"] = {"error": str(exc)}

    # recent events
    try:
        from parayabuck.monitoring.journal import EventJournal

        jp = Path(journal_path)
        if jp.exists():
            j = EventJournal(str(jp))
            events = j.query(limit=200)
            data["recent_events"] = [e.to_dict() for e in events]
        else:
            data["recent_events"] = []
    except Exception as exc:
        data["recent_events"] = {"error": str(exc)}

    # runtime snapshot
    try:
        p = Path(snapshot_path)
        if p.exists():
            data["runtime_snapshot"] = json.loads(p.read_text(encoding="utf-8"))
        else:
            data["runtime_snapshot"] = None
    except Exception as exc:
        data["runtime_snapshot"] = {"error": str(exc), "corrupt": True}

    # runtime metrics
    try:
        from parayabuck.monitoring import metrics

        data["runtime_metrics"] = metrics.runtime_metrics(database_path=decision_db_path)
    except Exception as exc:
        data["runtime_metrics"] = {"error": str(exc)}

    # incidents / orders / rebalance via forensics
    try:
        from parayabuck.monitoring.forensics import (
            ambiguous_orders,
            find_stuck_rebalances,
            runtime_session_forensics,
            unresolved_orders,
        )

        data["active_incidents"] = (
            data["runtime_metrics"].get("incidents") if isinstance(data.get("runtime_metrics"), dict) else None
        )
        data["unresolved_orders"] = unresolved_orders(decision_db_path)[:50]
        data["ambiguous_orders"] = ambiguous_orders(decision_db_path)[:50]
        data["stuck_rebalance"] = find_stuck_rebalances(decision_db_path)[:20]
        data["sessions"] = runtime_session_forensics(decision_db_path)
    except Exception as exc:
        data["forensics_error"] = str(exc)

    # alert stats
    try:
        from parayabuck.monitoring import alerts
        from parayabuck.monitoring.journal import journal_stats

        data["alert_stats"] = alerts.alert_stats()
        data["journal_stats"] = journal_stats()
    except Exception as exc:
        data["alert_stats"] = {"error": str(exc)}

    # sanitized config
    try:
        from parayabuck.config import load_config

        cfg = load_config(str(config_path)) if Path(str(config_path)).exists() else None
        if cfg is not None:
            # Build sanitized dict via asdict then redact
            from dataclasses import asdict

            raw = asdict(cfg)
            data["sanitized_config"] = _redact(raw)
        else:
            data["sanitized_config"] = None
    except Exception as exc:
        data["sanitized_config"] = {"error": str(exc)}

    # universe status
    try:
        from parayabuck.universe import validate_active_crypto_universe, validate_active_universe

        # Use config if available
        data["universe_status"] = {"checked": True}
        try:
            from parayabuck.config import load_config as _lc

            cfg2 = _lc(str(config_path)) if Path(str(config_path)).exists() else None
            if cfg2 is not None:
                data["universe_status"]["equity"] = str(validate_active_universe(cfg2.universe.equity))
                if cfg2.universe.crypto.enabled:
                    data["universe_status"]["crypto"] = str(validate_active_crypto_universe(cfg2.universe.crypto))
        except Exception:
            pass
    except Exception as exc:
        data["universe_status"] = {"error": str(exc)}

    # environment
    data["environment"] = {**_safe_git_info(), **_environment_info()}

    # database checks (integrity)
    try:
        dbp = Path(str(decision_db_path)) if decision_db_path else Path("data/runtime/parayabuck.sqlite3")
        checks: dict[str, Any] = {}
        if dbp.exists():
            conn = sqlite3.connect(str(dbp), timeout=2.0)
            try:
                cur = conn.execute("PRAGMA integrity_check")
                row = cur.fetchone()
                checks["integrity_check"] = str(row[0]) if row else "unknown"
                cur = conn.execute("PRAGMA foreign_key_check")
                fks = cur.fetchall()
                checks["foreign_key_violations"] = len(fks)
            finally:
                conn.close()
        else:
            checks["integrity_check"] = "no_db"
        data["database_checks"] = checks
    except Exception as exc:
        data["database_checks"] = {"error": str(exc)}

    # redact top-level
    return _redact(data)  # type: ignore[no-any-return]


def _run_doctor_checks(
    *,
    config_path: str | Path = "config/settings.yaml",
    snapshot_path: str | Path = "data/runtime/health_snapshot.json",
    journal_path: str | Path = "data/runtime/observability.sqlite3",
    decision_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Lightweight doctor checks (read-only)."""
    checks: list[dict[str, Any]] = []

    def _add(code: str, status: str, summary: str, **extra: Any) -> None:
        checks.append({"code": code, "status": status, "summary": summary, **extra})

    # config parses
    try:
        from parayabuck.config import load_config

        load_config(str(config_path))
        _add("config", "OK", "config parses")
    except FileNotFoundError:
        _add("config", "CRITICAL", "config missing", action="create config/settings.yaml")
    except Exception as exc:
        _add("config", "CRITICAL", f"config invalid: {exc}")

    # decision DB exists
    dbp = Path(str(decision_db_path)) if decision_db_path else Path("data/runtime/parayabuck.sqlite3")
    if not dbp.exists():
        _add("decision_db", "UNKNOWN", "no decision DB — fresh install")
    else:
        try:
            conn = sqlite3.connect(str(dbp), timeout=2.0)
            try:
                cur = conn.execute("PRAGMA integrity_check")
                val = str(cur.fetchone()[0])
                if val == "ok":
                    _add("decision_db", "OK", "sqlite integrity ok")
                else:
                    _add("decision_db", "CRITICAL", f"integrity: {val}")
                cur = conn.execute("PRAGMA foreign_key_check")
                fks = cur.fetchall()
                if fks:
                    _add("foreign_keys", "CRITICAL", f"{len(fks)} violations")
                else:
                    _add("foreign_keys", "OK", "no violations")
            finally:
                conn.close()
        except Exception as exc:
            _add("decision_db", "CRITICAL", f"db check failed: {exc}")

    # snapshot
    sp = Path(snapshot_path)
    if not sp.exists():
        _add("snapshot", "UNKNOWN", "no snapshot — runtime not running")
    else:
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            written_at = data.get("written_at") or data.get("as_of")
            if written_at:
                from datetime import datetime

                dt = datetime.fromisoformat(str(written_at))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                age = (datetime.now(UTC) - dt).total_seconds()
                if age > 90:
                    _add("snapshot", "CRITICAL", f"stale {age:.0f}s ago")
                else:
                    _add("snapshot", "OK", f"fresh {age:.0f}s ago")
            else:
                _add("snapshot", "UNKNOWN", "missing written_at")
        except Exception as exc:
            _add("snapshot", "CRITICAL", f"unreadable: {exc}")

    # active incidents / ambiguous / stuck
    try:
        from parayabuck.monitoring.forensics import ambiguous_orders, find_stuck_rebalances, runtime_session_forensics

        amb = ambiguous_orders(str(dbp) if dbp.exists() else None)
        if amb:
            _add("ambiguous_orders", "CRITICAL", f"{len(amb)} ambiguous orders")
        else:
            _add("ambiguous_orders", "OK", "no ambiguous orders")

        stuck = find_stuck_rebalances(str(dbp) if dbp.exists() else None)
        if stuck:
            _add("stuck_rebalance", "DEGRADED", f"{len(stuck)} stuck rebalance")
        else:
            _add("stuck_rebalance", "OK", "no stuck rebalance")

        sess = runtime_session_forensics(str(dbp) if dbp.exists() else None)
        if sess.get("unclean"):
            _add("sessions", "DEGRADED", f"{len(sess['unclean'])} unclean sessions")
        else:
            _add("sessions", "OK", "sessions ok")
    except Exception as exc:
        _add("forensics", "UNKNOWN", f"check failed: {exc}")

    # universe artifact
    try:
        from parayabuck.config import load_config as _lc

        if Path(str(config_path)).exists():
            cfg = _lc(str(config_path))
            for market, path in [
                ("equity", cfg.universe.equity.active_universe_path),
                ("crypto", cfg.universe.crypto.active_universe_path),
            ]:
                p = Path(path)
                if not p.exists():
                    _add(f"universe_{market}", "UNKNOWN", f"no artifact at {path}")
                else:
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        # check age if possible
                        _add(f"universe_{market}", "OK", f"exists {p}")
                    except Exception as exc:
                        _add(f"universe_{market}", "CRITICAL", f"corrupt: {exc}")
    except Exception:
        pass

    # alert config
    try:
        from parayabuck.monitoring import alerts

        stats = alerts.alert_stats()
        failed = int(stats.get("webhook_failed", 0) or 0)  # type: ignore[call-overload]
        if failed > 0:
            _add("alerts", "DEGRADED", f"webhook failed {failed}")
        else:
            _add("alerts", "OK", "alerts ok")
    except Exception:
        _add("alerts", "UNKNOWN", "cannot check")

    # receipt writable
    try:
        rp = Path("data/runtime/paper_receipts")
        rp.mkdir(parents=True, exist_ok=True)
        test = rp / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        _add("receipt_dir", "OK", "writable")
    except Exception as exc:
        _add("receipt_dir", "CRITICAL", f"not writable: {exc}")

    # disk space
    try:
        usage = shutil.disk_usage(".")
        free_gb = usage.free / (1024**3)
        if free_gb < 1.0:
            _add("disk", "CRITICAL", f"low disk {free_gb:.1f}GB free")
        elif free_gb < 5.0:
            _add("disk", "DEGRADED", f"disk {free_gb:.1f}GB free")
        else:
            _add("disk", "OK", f"{free_gb:.1f}GB free")
    except Exception as exc:
        _add("disk", "UNKNOWN", f"check failed: {exc}")

    # overall doctor status
    statuses = [c["status"] for c in checks]
    if any(s == "CRITICAL" for s in statuses):
        overall = "CRITICAL"
    elif any(s == "DEGRADED" for s in statuses):
        overall = "DEGRADED"
    elif any(s == "UNKNOWN" for s in statuses):
        overall = "UNKNOWN"
    else:
        overall = "OK"

    return {"overall": overall, "checks": checks}


def create_bundle_zip(
    output_path: str | Path | None = None,
    *,
    config_path: str | Path = "config/settings.yaml",
    snapshot_path: str | Path = "data/runtime/health_snapshot.json",
    journal_path: str | Path = "data/runtime/observability.sqlite3",
    decision_db_path: str | Path | None = None,
) -> Path:
    data = collect_bundle_data(
        config_path=config_path,
        snapshot_path=snapshot_path,
        journal_path=journal_path,
        decision_db_path=decision_db_path,
    )
    if output_path is None:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_path = Path(f"data/runtime/diagnostics/parayabuck_bundle_{ts}.zip")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Write zip with sanitized files, size bounded
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # manifest
        zf.writestr("manifest.json", json.dumps(data.get("manifest"), indent=2))
        zf.writestr("status.json", json.dumps(data.get("status"), indent=2))
        zf.writestr("doctor.json", json.dumps(data.get("doctor"), indent=2))
        # recent events jsonl
        events = data.get("recent_events") or []
        if isinstance(events, list):
            lines = "\n".join(json.dumps(e, separators=(",", ":")) for e in events[:200])
            zf.writestr("recent_events.jsonl", lines)
        else:
            zf.writestr("recent_events.jsonl", json.dumps(events, indent=2))
        for key in (
            "runtime_snapshot",
            "runtime_metrics",
            "active_incidents",
            "unresolved_orders",
            "stuck_rebalance",
            "sessions",
            "database_checks",
            "environment",
            "sanitized_config",
            "universe_status",
            "alert_stats",
            "journal_stats",
        ):
            content = data.get(key)
            if content is None:
                continue
            try:
                zf.writestr(f"{key}.json", json.dumps(content, indent=2, default=str)[:500_000])
            except Exception:
                zf.writestr(f"{key}.json", json.dumps({"error": "serialize failed"}, indent=2))
        # bundle size check: ensure not huge
    return out


__all__ = ["collect_bundle_data", "create_bundle_zip"]
