#!/usr/bin/env python3
"""
Local repair for Phase 4: quarantine polluted cache/observability and fix runtime DB.

Steps (report Phase 4):
1. Verify the engine heartbeat is not live (fresh health snapshot not marked
   stopped). A live engine is a hard refusal: nothing is quarantined and
   nothing is written.
2. Create timestamped quarantine dir on same filesystem.
3. Move warmup.sqlite3 (+wal,shm) to quarantine via rename (no copy).
4. Move observability.sqlite3*, health_snapshot.json to quarantine.
5. Do NOT delete parayabuck.sqlite3; backup, then:
   - close open portfolio_runtime_sessions and runtime_sessions as interrupted
   - commit that, then run broker reconciliation (if creds available) with the
     runtime's portfolio ownership providers wired in
   - clear the decision_worker_crashed incident AND its risk_state breaker ONLY
     when broker reconciliation ran AND every drift bucket came back empty. When
     it did not, the halt stays active: fix the drift and re-run. A cleared
     incident is appended to data/runtime/safety_incident_resolutions.jsonl
     first, so the audit trail survives.
7. Restart the engine; it re-downloads the quarantined warmup cache on start.

This script implements quarantine and DB repair steps 1-6; step 7 is left for
the operator.

Idempotent: safe to run twice; second run will find already-quarantined files.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

QUARANTINE_ROOT = Path("data/quarantine")
WARMUP_FILES = [
    Path("data/cache/warmup.sqlite3"),
    Path("data/cache/warmup.sqlite3-wal"),
    Path("data/cache/warmup.sqlite3-shm"),
]
OBS_FILES = [
    Path("data/runtime/observability.sqlite3"),
    Path("data/runtime/observability.sqlite3-wal"),
    Path("data/runtime/observability.sqlite3-shm"),
    Path("data/runtime/health_snapshot.json"),
]
LEDGER = Path("data/runtime/parayabuck.sqlite3")
# A live engine rewrites its health snapshot every snapshot interval and marks
# it "stopped" on a clean shutdown.
ENGINE_HEARTBEAT_MIN_TTL_SECONDS = 60.0


def _engine_is_active(config: Any) -> bool:
    """True while the engine looks live; fail closed on an unreadable snapshot."""
    from parayabuck.monitoring.snapshot import read_snapshot

    path = Path(config.observability.snapshot_path)
    if not path.exists():
        return False
    snapshot = read_snapshot(path)
    if not snapshot:
        return True  # present but unreadable: cannot prove the engine is down
    if str(snapshot.get("runtime_state") or "").lower() == "stopped":
        return False
    try:
        written_at = datetime.fromisoformat(str(snapshot["written_at"]))
    except (KeyError, TypeError, ValueError):
        return True
    if written_at.tzinfo is None:
        written_at = written_at.replace(tzinfo=UTC)
    ttl = max(ENGINE_HEARTBEAT_MIN_TTL_SECONDS, config.observability.snapshot_interval_seconds * 3)
    return (datetime.now(UTC) - written_at).total_seconds() <= ttl


def _heartbeat_config():
    """Config the heartbeat guard reads the snapshot path/interval from."""
    from parayabuck.config import load_config

    return load_config()


def check_engine_not_running() -> bool:
    """False while the engine heartbeat looks live; fail closed on any doubt.

    A fresh health snapshot whose ``runtime_state`` is not ``stopped`` means
    the engine is running, and this repair must never quarantine files or
    rewrite the ledger underneath it.
    """
    print("[1] Checking engine heartbeat is not live...")
    try:
        active = _engine_is_active(_heartbeat_config())
    except Exception as exc:
        print(f"  REFUSED: cannot determine engine heartbeat ({exc})")
        print("  Fix config / health snapshot and re-run. Nothing was changed.")
        return False
    if active:
        print("  REFUSED: engine heartbeat is live. Stop the engine first")
        print("  (systemctl stop parayabuck), wait for a 'stopped' health snapshot.")
        return False
    print("  No live engine heartbeat — ok")
    return True


def make_quarantine_dir() -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    qdir = QUARANTINE_ROOT / f"quarantine_{ts}"
    qdir.mkdir(parents=True, exist_ok=True)
    # Also create subdirs to preserve relative paths
    (qdir / "cache").mkdir(parents=True, exist_ok=True)
    (qdir / "runtime").mkdir(parents=True, exist_ok=True)
    print(f"[2] Quarantine dir: {qdir}")
    return qdir


def quarantine_files(qdir: Path):
    print("[3-4] Quarantining polluted files via rename (same filesystem)...")
    moved = []
    # Warmup
    for src in WARMUP_FILES:
        if src.exists():
            dst = qdir / "cache" / src.name
            # Use rename for same-filesystem atomic move
            try:
                src.rename(dst)
                moved.append((src, dst))
                print(f"  Moved {src} -> {dst} ({dst.stat().st_size} bytes)")
            except Exception as e:
                # Cross-device fallback: copy then unlink (should not happen, but handle)
                print(f"  Rename failed for {src}: {e}, trying copy")
                shutil.copy2(src, dst)
                src.unlink()
                moved.append((src, dst))
        else:
            print(f"  Skip {src} (not exists)")
    # Observability + health snapshot
    for src in OBS_FILES:
        if src.exists():
            dst = qdir / "runtime" / src.name
            try:
                src.rename(dst)
                moved.append((src, dst))
                print(f"  Moved {src} -> {dst}")
            except Exception as e:
                print(f"  Rename failed for {src}: {e}")
                shutil.copy2(src, dst)
                src.unlink()
                moved.append((src, dst))
        else:
            print(f"  Skip {src} (not exists)")
    if not moved:
        print("  No files moved — already quarantined or not present")
    return moved


def backup_ledger(qdir: Path):
    print("[6] Backing up parayabuck.sqlite3 (ledger) — NOT moving, only copying...")
    if not LEDGER.exists():
        print(f"  Ledger not found at {LEDGER}, skip backup")
        return None
    dst = qdir / "runtime" / f"parayabuck.sqlite3.backup.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    # Use sqlite backup API for safety while WAL open
    try:
        src_con = sqlite3.connect(str(LEDGER))
        dst_con = sqlite3.connect(str(dst))
        src_con.backup(dst_con)
        src_con.close()
        dst_con.close()
        print(f"  Backup via sqlite backup API: {dst} ({dst.stat().st_size} bytes)")
    except Exception as e:
        print(f"  Backup API failed {e}, falling back to copy2")
        shutil.copy2(LEDGER, dst)
        print(f"  Backup via copy2: {dst}")
    # Also copy -wal/-shm if exist as separate files for forensic
    for ext in ["-wal", "-shm"]:
        src = Path(str(LEDGER) + ext)
        if src.exists():
            d2 = qdir / "runtime" / (src.name)
            try:
                shutil.copy2(src, d2)
                print(f"  Copied {src} -> {d2}")
            except Exception as e:
                print(f"  Copy {src} failed: {e}")
    return dst


def _record_resolutions(con: sqlite3.Connection, reason: str, now: str) -> int:
    """Append the incident rows about to be cleared to the resolution log.

    ``safety_incidents`` holds only ACTIVE halts (the runtime restores every
    row), so a resolved incident cannot stay in the table; this append-only
    log next to the ledger is its audit trail.
    """
    rows = con.execute(
        "SELECT account_id, reason, scope, activated_at, updated_at FROM safety_incidents WHERE reason=?",
        (reason,),
    ).fetchall()
    if not rows:
        return 0
    log_path = LEDGER.parent / "safety_incident_resolutions.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        for account_id, row_reason, scope, activated_at, updated_at in rows:
            entry = {
                "account_id": account_id,
                "reason": row_reason,
                "scope": scope,
                "activated_at": activated_at,
                "updated_at": updated_at,
                "resolved_at": now,
                "resolved_by": "scripts/repair_local_state.py",
                "resolution": "broker reconciliation verified with no drift",
            }
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    print(f"  Recorded {len(rows)} resolution(s) in {log_path}")
    return len(rows)


def repair_runtime_db() -> bool:
    """Repair the ledger; return True only when broker reconciliation verified.

    A verified reconcile (it ran AND reported no drift) is the ONLY thing that
    clears the ``decision_worker_crashed`` halt. Without it the incident row
    and its matching ``risk_state`` breaker reason are left untouched and the
    halt stays active, because the script cannot know the crash left no
    unknown broker-side orders or foreign inventory.
    """
    print("[6] Repairing runtime DB: close open sessions, clear halt if reconciled...")
    if not LEDGER.exists():
        print("  No ledger, skip")
        return False
    con = sqlite3.connect(str(LEDGER), timeout=5)
    con.execute("PRAGMA busy_timeout=5000")
    now = datetime.now(UTC).isoformat()
    # Count before
    open_port = con.execute("SELECT COUNT(*) FROM portfolio_runtime_sessions WHERE ended_at IS NULL").fetchone()[0]
    open_rt = con.execute("SELECT COUNT(*) FROM runtime_sessions WHERE ended_at IS NULL").fetchone()[0]
    print(f"  Open portfolio sessions: {open_port}, open runtime sessions: {open_rt}")
    # Close them as interrupted
    if open_port:
        con.execute(
            "UPDATE portfolio_runtime_sessions SET ended_at=?, status='interrupted' WHERE ended_at IS NULL",
            (now,),
        )
        print(f"  Closed {con.total_changes} portfolio sessions as interrupted")
    if open_rt:
        con.execute(
            "UPDATE runtime_sessions SET ended_at=?, state='interrupted' WHERE ended_at IS NULL",
            (now,),
        )
        print("  Closed runtime sessions as interrupted")
    # Commit before reconciling: the broker opens its own connection to this
    # file and an open write transaction here would make it "database is locked".
    con.commit()
    # Try broker reconciliation to verify holdings==broker (no creds => no verify)
    reconcile_ok = _run_broker_reconcile()

    if reconcile_ok:
        _record_resolutions(con, "decision_worker_crashed", now)
        cleared = con.execute("DELETE FROM safety_incidents WHERE reason='decision_worker_crashed'").rowcount
        if cleared:
            print(f"  Cleared {cleared} decision_worker_crashed incident (reconcile verified)")
        else:
            print("  No decision_worker_crashed incident to clear")
    else:
        remaining = con.execute(
            "SELECT COUNT(*) FROM safety_incidents WHERE reason='decision_worker_crashed'"
        ).fetchone()[0]
        if remaining:
            print(f"  HALT STILL ACTIVE: keeping {remaining} decision_worker_crashed incident(s).")
            print("  Broker reconciliation did NOT verify, so the halt is not lifted.")
            print("  Run broker.reconcile() with working credentials (or re-run this script")
            print("  with ALPACA_API_KEY/ALPACA_SECRET_KEY set) and confirm the incident")
            print("  before considering any manual clearing.")

    if reconcile_ok:
        # Latest risk_state row, and only its decision_worker_crashed breaker.
        cur = con.execute("SELECT session_date, payload FROM risk_state ORDER BY session_date DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            latest_date, raw = row
            payload = json.loads(raw)
            reason = payload.get("circuit_breaker_reason")
            if reason == "decision_worker_crashed":
                payload["circuit_breaker_reason"] = None
                con.execute(
                    "UPDATE risk_state SET payload=?, updated_at=? WHERE session_date=?",
                    (json.dumps(payload), now, latest_date),
                )
                print(f"  Cleared decision_worker_crashed breaker for risk_state {latest_date}")
            else:
                print(f"  Latest risk_state {latest_date} breaker left untouched ({reason})")

    con.commit()
    # Verify
    open_port2 = con.execute("SELECT COUNT(*) FROM portfolio_runtime_sessions WHERE ended_at IS NULL").fetchone()[0]
    open_rt2 = con.execute("SELECT COUNT(*) FROM runtime_sessions WHERE ended_at IS NULL").fetchone()[0]
    remaining_incidents = con.execute(
        "SELECT COUNT(*) FROM safety_incidents WHERE reason='decision_worker_crashed'"
    ).fetchone()[0]
    print(f"  After repair: open portfolio {open_port2}, open rt {open_rt2}, incidents {remaining_incidents}")
    con.close()
    return reconcile_ok


def _run_broker_reconcile() -> bool:
    """True only when broker.reconcile() ran to completion with no drift.

    Missing credentials, a missing config, any broker error or any non-empty
    drift bucket all return False: unverified reconciliation must never
    authorise clearing the halt. The broker gets the runtime's portfolio
    ownership/open-order providers and the tradable universe; without them
    every bot-owned position reads as BROKER_STALE.
    """
    try:
        from parayabuck.config import load_config

        cfg = load_config()
        # Only attempt if keys exist
        api_key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")
        if not (api_key and secret):
            print("  No Alpaca keys, skipping broker reconcile")
            return False
        from parayabuck.execution.alpaca import AlpacaBroker
        from parayabuck.execution.alpaca_reconciliation import RECONCILIATION_DRIFT_CATEGORIES
        from parayabuck.execution.ledger import ExecutionLedger
        from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
        from parayabuck.storage import SQLiteStore

        store = SQLiteStore(str(LEDGER))
        broker = None
        try:
            ledger = ExecutionLedger(str(LEDGER), store=store)
            repository = PortfolioStateRepository(str(LEDGER), store=store)
            tradable = set(cfg.universe.equity.symbols) if cfg.universe.equity.enabled else set()

            def _account_id() -> str:
                return (broker.account_id if broker is not None else "") or "unknown"

            broker = AlpacaBroker(
                api_key,
                secret,
                paper=cfg.runtime.mode.value == "paper",
                ledger=ledger,
                portfolio_owned_quantities=lambda: repository.owned_quantities(_account_id()),
                portfolio_open_orders=lambda: repository.open_orders(_account_id()),
                bot_tradable_symbols=lambda: tradable,
            )
            print("  Running broker.reconcile() for verification...")
            result = broker.reconcile()
        finally:
            if broker is not None:
                try:
                    broker.close()
                except Exception:
                    pass
            try:
                store.release()
            except Exception:
                pass
        drift = {
            bucket: symbols
            for bucket, symbols in result.items()
            if bucket in RECONCILIATION_DRIFT_CATEGORIES and symbols
        }
        if drift:
            print(f"  Reconciliation reported drift, NOT verified: {drift}")
            return False
        print("  Reconciliation succeeded with no drift")
        return True
    except Exception as e:
        print(f"  Reconciliation attempt failed: {e}")
        return False


def main():
    if not check_engine_not_running():
        print("Abort: engine still running. Nothing was quarantined or changed.")
        return 1
    qdir = make_quarantine_dir()
    quarantine_files(qdir)
    backup_ledger(qdir)
    reconciled = repair_runtime_db()
    print("\nDone. Quarantine at", qdir)
    if not reconciled:
        print("\nHALT STILL ACTIVE: broker reconciliation did not verify, so the")
        print("decision_worker_crashed incident was NOT cleared. Resolve the reported")
        print("drift (or supply working credentials) and re-run this script before trading.")
    print("Next steps:")
    print("  - Preflight: parayabuck doctor --strict --paper --offline")
    print("  - Start the engine: sudo systemctl start parayabuck (it re-downloads the warmup cache)")
    print("  - Verify: parayabuck ops status, then parayabuck ops events --since 30m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
