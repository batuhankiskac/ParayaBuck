"""Sanitized diagnostic bundle — one archive for offline diagnosis."""

from __future__ import annotations

import json
import logging
import os
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

_MAX_SECTION_CHARS = 500_000
_MAX_SECTION_ROWS = 2_000


def _cap_rows(value: Any) -> Any:
    """Cap list rows before serialisation so a big section shrinks first."""
    if isinstance(value, list):
        return value[:_MAX_SECTION_ROWS]
    if isinstance(value, dict):
        return {key: _cap_rows(item) for key, item in value.items()}
    return value


def _dump_section(content: Any) -> str:
    """Serialise one bundle section within the size budget, always as valid JSON.

    Rows are capped first; a section still over budget becomes the parseable
    truncation envelope, and the *serialised* envelope — re-escaping of the
    preview included — is shrunk until it fits the same hard bound.
    """
    text = json.dumps(_cap_rows(content), indent=2, default=str)
    if len(text) <= _MAX_SECTION_CHARS:
        return text
    budget = _MAX_SECTION_CHARS // 2
    while budget > 0:
        envelope = json.dumps({"truncated": True, "size_chars": len(text), "preview": text[:budget]}, indent=2)
        if len(envelope) <= _MAX_SECTION_CHARS:
            return envelope
        budget //= 2
    return json.dumps({"truncated": True, "size_chars": len(text)}, indent=2)


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


def _collect_forensics(db_path: str | Path | None) -> dict[str, Any]:
    """Read the forensic order/rebalance/session sections once (read-only)."""
    from parayabuck.monitoring.forensics import (
        ambiguous_orders,
        find_stuck_rebalances,
        runtime_session_forensics,
    )

    return {
        "ambiguous": ambiguous_orders(db_path),
        "stuck_rebalance": find_stuck_rebalances(db_path),
        "sessions": runtime_session_forensics(db_path),
    }


def _parayabuck_version() -> str:
    try:
        import parayabuck

        return getattr(parayabuck, "__version__", "unknown")
    except Exception:
        return "unknown"


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
        from parayabuck.config import Config, load_config
        from parayabuck.monitoring.health import evaluate_health

        # Judge health against the bundle's own config, never a cwd/env guess
        try:
            status_cfg = load_config(str(config_path)) if Path(str(config_path)).exists() else Config()
        except Exception:
            status_cfg = Config()
        data["status"] = evaluate_health(snapshot_path=snapshot_path, database_path=decision_db_path, config=status_cfg)
    except Exception as exc:
        data["status"] = {"error": str(exc)}

    # doctor — forensic ledger reads are shared with the sections below
    dbp = Path(str(decision_db_path)) if decision_db_path else None
    forensics_db = str(dbp) if dbp is not None and dbp.exists() else None
    forensics_data = _collect_forensics(forensics_db)
    try:
        # lazy import to avoid cycle
        from parayabuck.monitoring.diagnostic import _run_doctor_checks

        data["doctor"] = _run_doctor_checks(
            config_path=config_path,
            snapshot_path=snapshot_path,
            journal_path=journal_path,
            decision_db_path=decision_db_path,
            forensics=forensics_data,
        )
    except Exception as exc:
        data["doctor"] = {"error": str(exc)}

    # recent events
    try:
        from parayabuck.monitoring.journal import EventJournal

        jp = Path(journal_path)
        if jp.exists():
            j = EventJournal.open_readonly(str(jp))
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

    # runtime metrics — runtime_metrics(None) would resolve a cwd-relative
    # config/ledger, so with no supplied path the section is no_db instead
    try:
        from parayabuck.monitoring import metrics

        if dbp is None:
            data["runtime_metrics"] = {"status": "no_db"}
        else:
            data["runtime_metrics"] = metrics.runtime_metrics(database_path=decision_db_path)
    except Exception as exc:
        data["runtime_metrics"] = {"error": str(exc)}

    # incidents / orders / rebalance via forensics
    try:
        from parayabuck.monitoring.forensics import unresolved_orders

        data["active_incidents"] = (
            data["runtime_metrics"].get("incidents") if isinstance(data.get("runtime_metrics"), dict) else None
        )
        data["unresolved_orders"] = unresolved_orders(forensics_db)[:50]
        data["ambiguous_orders"] = (forensics_data.get("ambiguous") or [])[:50]
        data["stuck_rebalance"] = (forensics_data.get("stuck_rebalance") or [])[:20]
        data["sessions"] = forensics_data.get("sessions") or {}
    except Exception as exc:
        data["forensics_error"] = str(exc)

    # alert/journal stats — the copy the ENGINE persisted into its snapshot
    # (same source as `ops status`). This bundle process's own counters are
    # always near-zero and would suggest nothing fired during an incident.
    snapshot = data.get("runtime_snapshot")
    usable = isinstance(snapshot, dict) and not snapshot.get("corrupt")
    for key, snapshot_key in (("alert_stats", "alerts"), ("journal_stats", "observability")):
        persisted = snapshot.get(snapshot_key) if usable and isinstance(snapshot, dict) else None
        if isinstance(persisted, dict):
            data[key] = {**persisted, "source": "engine_snapshot"}
        else:
            data[key] = {"unavailable": True, "reason": "no engine snapshot counters"}

    # sanitized config
    try:
        from parayabuck.config import load_config

        cfg = load_config(str(config_path)) if Path(str(config_path)).exists() else None
        if cfg is not None:
            # Build sanitized dict via asdict then redact
            from dataclasses import asdict

            from parayabuck.monitoring.operational_event import redact_fields

            raw = asdict(cfg)
            data["sanitized_config"] = redact_fields(raw)
        else:
            data["sanitized_config"] = None
    except Exception as exc:
        data["sanitized_config"] = {"error": str(exc)}

    # universe status (use config if available)
    data["universe_status"] = {"checked": True}
    try:
        from parayabuck.config import load_config as _lc

        cfg2 = _lc(str(config_path)) if Path(str(config_path)).exists() else None
        if cfg2 is not None:
            data["universe_status"]["equity"] = "static" if cfg2.universe.equity.enabled else "disabled"
    except Exception:
        pass

    # environment
    data["environment"] = {**_safe_git_info(), **_environment_info()}

    # database checks (integrity) — only the explicitly supplied ledger, never a
    # cwd-relative guess the rest of the bundle did not read
    try:
        checks: dict[str, Any] = {}
        if dbp is not None and dbp.exists():
            conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=2.0)
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

    # redact top-level with the single shared policy
    from parayabuck.monitoring.operational_event import redact_fields

    return redact_fields(data)


def _run_doctor_checks(
    *,
    config_path: str | Path = "config/settings.yaml",
    snapshot_path: str | Path = "data/runtime/health_snapshot.json",
    journal_path: str | Path = "data/runtime/observability.sqlite3",
    decision_db_path: str | Path | None = None,
    forensics: dict[str, Any] | None = None,
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

    # decision DB exists — only the explicitly supplied path; none means UNKNOWN
    dbp = Path(str(decision_db_path)) if decision_db_path else None
    if dbp is None:
        _add("decision_db", "UNKNOWN", "no decision DB path configured")
    elif not dbp.exists():
        _add("decision_db", "UNKNOWN", "no decision DB — fresh install")
    else:
        try:
            conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=2.0)
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

    # The engine-reported state (watchdog, cross-check, journal) is read from
    # the snapshot the ENGINE writes. `ops doctor` is a separate process whose
    # own counters are always empty, so reading them here would report a
    # healthy journal the engine never wrote to.
    snapshot_data: dict[str, Any] | None = None
    # snapshot
    sp = Path(snapshot_path)
    if not sp.exists():
        _add("snapshot", "UNKNOWN", "no snapshot — runtime not running")
    else:
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            snapshot_data = data
            written_at = data.get("written_at") or data.get("as_of")
            if written_at:
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

        if forensics is None:
            forensics_db = str(dbp) if dbp is not None and dbp.exists() else None
            forensics = {
                "ambiguous": ambiguous_orders(forensics_db),
                "stuck_rebalance": find_stuck_rebalances(forensics_db),
                "sessions": runtime_session_forensics(forensics_db),
            }

        amb = forensics.get("ambiguous") or []
        if amb:
            _add("ambiguous_orders", "CRITICAL", f"{len(amb)} ambiguous orders")
        else:
            _add("ambiguous_orders", "OK", "no ambiguous orders")

        stuck = forensics.get("stuck_rebalance") or []
        if stuck:
            _add("stuck_rebalance", "DEGRADED", f"{len(stuck)} stuck rebalance")
        else:
            _add("stuck_rebalance", "OK", "no stuck rebalance")

        sess = forensics.get("sessions") or {}
        # Only crashes after the latest clean stop need attention; older ones
        # stay in the history count.
        recent = sess.get("unclean_since_clean") or []
        historical = len(sess.get("unclean") or [])
        if recent:
            _add("sessions", "DEGRADED", f"{len(recent)} unclean sessions since last clean stop")
        elif historical:
            _add("sessions", "OK", f"sessions ok ({historical} historical unclean)")
        else:
            _add("sessions", "OK", "sessions ok")
    except Exception as exc:
        _add("forensics", "UNKNOWN", f"check failed: {exc}")

    # universe
    try:
        from parayabuck.config import load_config as _lc

        if Path(str(config_path)).exists():
            cfg = _lc(str(config_path))
            _add(
                "universe_equity",
                "OK",
                f"static {','.join(cfg.universe.equity.symbols)}" if cfg.universe.equity.enabled else "disabled",
            )
    except Exception:
        pass

    # Intent watchdog / signal cross-check — the runtime may be alive and every
    # liveness check green while the strategy stopped deciding.
    from parayabuck.monitoring.health import (
        _check_intent_watchdog,
        _check_observability,
        _check_signal_cross_check,
        persisted_journal_state,
    )

    merged: dict[str, Any] | None = None
    if snapshot_data is not None:
        inner = snapshot_data.get("runtime_health")
        merged = {**snapshot_data, **inner} if isinstance(inner, dict) else snapshot_data
    # Runtime state: an open circuit breaker has halted decisions and orders.
    # Reading it from the engine's snapshot keeps `ops doctor` from reporting
    # OK (exit 0) for a halted runtime; a snapshot without the field is unknown.
    if merged is None or "circuit_breaker_reason" not in merged:
        _add("circuit_breaker", "UNKNOWN", "runtime state not in the snapshot")
    elif merged["circuit_breaker_reason"]:
        _add("circuit_breaker", "CRITICAL", f"circuit breaker open: {merged['circuit_breaker_reason']}")
    else:
        _add("circuit_breaker", "OK", "not tripped")
    for check in (_check_intent_watchdog(merged), _check_signal_cross_check(merged)):
        _add(check.code, str(check.status), check.summary, action=check.action)
    journal_check = _check_observability(persisted_journal_state(merged))
    _add(journal_check.code, str(journal_check.status), journal_check.summary, action=journal_check.action)
    # The journal file this doctor was pointed at (`--journal`, else the
    # configured path): presence and last-write age, checked without opening
    # it so a missing file is never created.
    jp = Path(str(journal_path))
    try:
        if jp.exists():
            # WAL mode: recent writes land in the -wal sidecar first.
            wal = Path(f"{jp}-wal")
            mtime = max(jp.stat().st_mtime, wal.stat().st_mtime if wal.exists() else 0.0)
            age = max(0.0, datetime.now(UTC).timestamp() - mtime)
            _add("journal_file", "OK", f"{jp} last written {age:.0f}s ago", path=str(jp))
        else:
            _add("journal_file", "UNKNOWN", f"no journal file at {jp}", path=str(jp))
    except OSError as exc:
        _add("journal_file", "UNKNOWN", f"journal file unreadable: {exc}", path=str(jp))

    # receipt writable
    try:
        from parayabuck.monitoring import receipt as receipt_module

        rp = receipt_module.default_receipt_dir()
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

    # Written beside the target and renamed into place: a write that fails
    # part-way leaves no partial archive that looks like a complete bundle.
    staging = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    try:
        _write_bundle_archive(staging, data)
        os.replace(staging, out)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return out


def _write_bundle_archive(path: Path, data: dict[str, Any]) -> None:
    """Write the sanitized, size-bounded bundle members of ``data`` to ``path``."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # manifest
        zf.writestr("manifest.json", _dump_section(data.get("manifest")))
        zf.writestr("status.json", _dump_section(data.get("status")))
        zf.writestr("doctor.json", _dump_section(data.get("doctor")))
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
                zf.writestr(f"{key}.json", _dump_section(content))
            except Exception:
                zf.writestr(f"{key}.json", json.dumps({"error": "serialize failed"}, indent=2))


__all__ = ["collect_bundle_data", "create_bundle_zip"]
