#!/usr/bin/env python3
"""
Local repair for Phase 4: quarantine polluted cache/observability and fix runtime DB.

Usage:
    python scripts/repair_local_state.py [--config CONFIG] [--confirm]

DRY RUN BY DEFAULT: without ``--confirm`` the script only reports what it
would quarantine and rewrite and exits 0. Pass ``--confirm`` (or ``--yes``)
to actually move files and write the ledger. ``--help`` prints this usage
and exits 0 without touching anything.

Ledger, journal and snapshot paths resolve from ``load_config`` (``--config``
or ``CONFIG_PATH``, same as the CLI) with the ``data/...`` literals below as
fallback, so a custom ``runtime.decision_db_path`` /
``observability.journal_path`` repairs the real files. The warmup cache has
no config knob and keeps its default location.

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
     incident is appended to <ledger-dir>/safety_incident_resolutions.jsonl
     first, so the audit trail survives. The breaker clear is scoped to the
     reconciled account(s): other accounts sharing the session date keep
     their breaker.
7. Restart the engine; it re-downloads the quarantined warmup cache on start.

This script implements quarantine and DB repair steps 1-6; step 7 is left for
the operator.

Idempotent: safe to run twice; second run will find already-quarantined files.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_QUARANTINE_ROOT = Path("data/quarantine")
QUARANTINE_ROOT = DEFAULT_QUARANTINE_ROOT
WARMUP_FILES = [
    Path("data/cache/warmup.sqlite3"),
    Path("data/cache/warmup.sqlite3-wal"),
    Path("data/cache/warmup.sqlite3-shm"),
]
DEFAULT_OBS_FILES = [
    Path("data/runtime/observability.sqlite3"),
    Path("data/runtime/observability.sqlite3-wal"),
    Path("data/runtime/observability.sqlite3-shm"),
    Path("data/runtime/health_snapshot.json"),
]
DEFAULT_LEDGER = Path("data/runtime/parayabuck.sqlite3")
# Back-compat aliases: tests monkeypatch WARMUP_FILES/OBS_FILES/LEDGER.
OBS_FILES = list(DEFAULT_OBS_FILES)
LEDGER = DEFAULT_LEDGER
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


def _load_config(config_path: str | None):
    """Config honoring --config/CONFIG_PATH/cwd, like the CLI."""
    from parayabuck.config import load_config

    return load_config(config_path)


def _heartbeat_config(config_path: str | None = None):
    """Config the heartbeat guard reads the snapshot path/interval from."""
    return _load_config(config_path)


def _resolve_paths(config: Any) -> tuple[Path, list[Path], Path]:
    """(ledger, observability files, snapshot) for a config.

    Missing attributes fall back to the data/... literals so a repair on a
    broken checkout still quarantines the defaults.
    """
    ledger = Path(getattr(config.runtime, "decision_db_path", str(DEFAULT_LEDGER)))
    journal = Path(getattr(config.observability, "journal_path", str(DEFAULT_OBS_FILES[0])))
    snapshot = Path(getattr(config.observability, "snapshot_path", str(DEFAULT_OBS_FILES[-1])))
    return ledger, _obs_files_for(journal, snapshot), snapshot


def _obs_files_for(journal: Path, snapshot: Path) -> list[Path]:
    """Journal (+WAL sidecars) and snapshot resolved from config."""
    files = [journal, Path(str(journal) + "-wal"), Path(str(journal) + "-shm")]
    if snapshot != journal:
        files.append(snapshot)
    return files


def _quarantine_root(ledger: Path) -> Path:
    """Quarantine root: the monkeypatched value in tests, else by the ledger."""
    if Path(str(QUARANTINE_ROOT)) != DEFAULT_QUARANTINE_ROOT:
        return Path(str(QUARANTINE_ROOT))
    return ledger.parent.parent / "quarantine"


def check_engine_not_running(config: Any | None = None, config_path: str | None = None) -> bool:
    """False while the engine heartbeat looks live; fail closed on any doubt.

    A fresh health snapshot whose ``runtime_state`` is not ``stopped`` means
    the engine is running, and this repair must never quarantine files or
    rewrite the ledger underneath it.
    """
    print("[1] Checking engine heartbeat is not live...")
    try:
        if config is not None:
            cfg = config
        else:
            try:
                cfg = _heartbeat_config(config_path)
            except TypeError:  # a zero-arg test stub replaces the loader
                cfg = _heartbeat_config()
        active = _engine_is_active(cfg)
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


def make_quarantine_dir(root: str | Path | None = None) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = Path(str(root)) if root is not None else _quarantine_root(DEFAULT_LEDGER)
    qdir = base / f"quarantine_{ts}"
    qdir.mkdir(parents=True, exist_ok=True)
    # Also create subdirs to preserve relative paths
    (qdir / "cache").mkdir(parents=True, exist_ok=True)
    (qdir / "runtime").mkdir(parents=True, exist_ok=True)
    print(f"[2] Quarantine dir: {qdir}")
    return qdir


def quarantine_files(qdir: Path, warmup_files: list[Path] | None = None, obs_files: list[Path] | None = None):
    print("[3-4] Quarantining polluted files via rename (same filesystem)...")
    moved = []
    # Warmup
    for src in WARMUP_FILES if warmup_files is None else warmup_files:
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
    for src in OBS_FILES if obs_files is None else obs_files:
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


def backup_ledger(qdir: Path, ledger: str | Path | None = None):
    ledger_path = Path(str(ledger)) if ledger is not None else LEDGER
    print("[6] Backing up parayabuck.sqlite3 (ledger) — NOT moving, only copying...")
    if not ledger_path.exists():
        print(f"  Ledger not found at {ledger_path}, skip backup")
        # Never fall through: the sqlite backup API would create an empty
        # ledger at this path, which the repair step then fails on.
        return None
    dst = qdir / "runtime" / f"parayabuck.sqlite3.backup.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    # Use sqlite backup API for safety while WAL open
    try:
        src_con = sqlite3.connect(str(ledger_path))
        dst_con = sqlite3.connect(str(dst))
        src_con.backup(dst_con)
        src_con.close()
        dst_con.close()
        print(f"  Backup via sqlite backup API: {dst} ({dst.stat().st_size} bytes)")
    except Exception as e:
        print(f"  Backup API failed {e}, falling back to copy2")
        shutil.copy2(ledger_path, dst)
        print(f"  Backup via copy2: {dst}")
    # Also copy -wal/-shm if exist as separate files for forensic
    for ext in ["-wal", "-shm"]:
        src = Path(str(ledger_path) + ext)
        if src.exists():
            d2 = qdir / "runtime" / (src.name)
            try:
                shutil.copy2(src, d2)
                print(f"  Copied {src} -> {d2}")
            except Exception as e:
                print(f"  Copy {src} failed: {e}")
    return dst


def _record_resolutions(con: sqlite3.Connection, reason: str, now: str, ledger: str | Path | None = None) -> int:
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
    ledger_path = Path(str(ledger)) if ledger is not None else LEDGER
    log_path = ledger_path.parent / "safety_incident_resolutions.jsonl"
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


def repair_runtime_db(ledger: str | Path | None = None, config: Any | None = None) -> bool:
    """Repair the ledger; return True only when broker reconciliation verified.

    A verified reconcile (it ran AND reported no drift) is the ONLY thing that
    clears the ``decision_worker_crashed`` halt. Without it the incident row
    and its matching ``risk_state`` breaker reason are left untouched and the
    halt stays active, because the script cannot know the crash left no
    unknown broker-side orders or foreign inventory.
    """
    ledger_path = Path(str(ledger)) if ledger is not None else LEDGER
    print("[6] Repairing runtime DB: close open sessions, clear halt if reconciled...")
    if not ledger_path.exists():
        print("  No ledger, skip")
        return False
    con = sqlite3.connect(str(ledger_path), timeout=5)
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
    try:
        reconcile_ok = _run_broker_reconcile(ledger_path, config)
    except TypeError:  # a zero-arg test stub replaces the reconciler
        reconcile_ok = _run_broker_reconcile()
    if reconcile_ok:
        reconciled_accounts = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT account_id FROM safety_incidents WHERE reason='decision_worker_crashed'"
            ).fetchall()
        ]
        _record_resolutions(con, "decision_worker_crashed", now, ledger_path)
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
        # Account-scoped: the key is (account_id, session_date), so an
        # unfiltered UPDATE would lift other accounts' halts sharing the
        # session date. Only each reconciled account's latest row, and only
        # when its breaker is the verified reason.
        for account_id in reconciled_accounts:
            cur = con.execute(
                "SELECT session_date, payload FROM risk_state WHERE account_id=? ORDER BY session_date DESC LIMIT 1",
                (account_id,),
            )
            row = cur.fetchone()
            if row is None:
                continue
            latest_date, raw = row
            payload = json.loads(raw)
            reason = payload.get("circuit_breaker_reason")
            if reason != "decision_worker_crashed":
                print(f"  Latest risk_state {latest_date} breaker left untouched ({reason})")
                continue
            payload["circuit_breaker_reason"] = None
            con.execute(
                "UPDATE risk_state SET payload=?, updated_at=? WHERE account_id=? AND session_date=?",
                (json.dumps(payload), now, account_id, latest_date),
            )
            print(f"  Cleared decision_worker_crashed breaker for {account_id} risk_state {latest_date}")
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


def _run_broker_reconcile(
    ledger: str | Path | None = None,
    config: Any | None = None,
    config_path: str | None = None,
) -> bool:
    """True only when broker.reconcile() ran to completion with no drift.

    Missing credentials, a missing config, any broker error or any non-empty
    drift bucket all return False: unverified reconciliation must never
    authorise clearing the halt. The broker gets the runtime's portfolio
    ownership/open-order providers and the tradable universe; without them
    every bot-owned position reads as BROKER_STALE.
    """
    try:
        cfg = config if config is not None else _load_config(config_path)
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

        ledger_path = Path(str(ledger)) if ledger is not None else LEDGER
        store = SQLiteStore(str(ledger_path))
        broker = None
        try:
            exec_ledger = ExecutionLedger(str(ledger_path), store=store)
            repository = PortfolioStateRepository(str(ledger_path), store=store)
            tradable = set(cfg.universe.equity.symbols) if cfg.universe.equity.enabled else set()

            def _account_id() -> str:
                return (broker.account_id if broker is not None else "") or "unknown"

            broker = AlpacaBroker(
                api_key,
                secret,
                paper=cfg.runtime.mode.value == "paper",
                ledger=exec_ledger,
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quarantine polluted cache/observability files and repair the runtime ledger."
        " Dry run by default; pass --confirm to actually move files and write the ledger.",
    )
    parser.add_argument("--config", default=None, help="settings YAML (default: CONFIG_PATH or config/settings.yaml)")
    parser.add_argument(
        "--confirm",
        "--yes",
        dest="confirm",
        action="store_true",
        help="actually quarantine files and write the ledger (without it: report only)",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="report only (default behaviour; explicit flag for clarity)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    confirm = bool(args.confirm) and not bool(args.dry_run)
    try:
        config = _load_config(args.config)
    except Exception as exc:
        print(f"Cannot load config ({exc}); falling back to default paths.")
        config = None
    if config is not None:
        resolved_ledger, resolved_obs, _snapshot = _resolve_paths(config)
        # Offline tests monkeypatch LEDGER/OBS_FILES/QUARANTINE_ROOT to
        # tmp_path: an explicitly patched global wins over the config value.
        ledger = resolved_ledger if Path(str(LEDGER)) == DEFAULT_LEDGER else Path(str(LEDGER))
        obs_files = (
            resolved_obs if [str(p) for p in OBS_FILES] == [str(p) for p in DEFAULT_OBS_FILES] else list(OBS_FILES)
        )
    else:
        ledger, obs_files = DEFAULT_LEDGER, list(OBS_FILES)
    if not check_engine_not_running(config_path=args.config):
        print("Abort: engine still running. Nothing was quarantined or changed.")
        return 1
    if not confirm:
        print("DRY RUN (no --confirm): reporting what would change; nothing is quarantined or written.")
        _report_plan(ledger, obs_files)
        return 0
    qdir = make_quarantine_dir(_quarantine_root(ledger))
    quarantine_files(qdir, obs_files=obs_files)
    backup_ledger(qdir, ledger)
    reconciled = repair_runtime_db(ledger, config)
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


def _report_plan(ledger: Path, obs_files: list[Path]) -> None:
    """Dry-run report: files that would move and ledger rows that would close."""
    print(f"Ledger: {ledger}")
    for src in [*WARMUP_FILES, *obs_files]:
        print(f"  {'would quarantine' if src.exists() else 'absent (skip)'}: {src}")
    if not ledger.exists():
        print("  Ledger absent: no session/incident rows would change.")
        return
    try:
        con = sqlite3.connect(str(ledger), timeout=5)
        try:
            open_port = con.execute(
                "SELECT COUNT(*) FROM portfolio_runtime_sessions WHERE ended_at IS NULL"
            ).fetchone()[0]
            open_rt = con.execute("SELECT COUNT(*) FROM runtime_sessions WHERE ended_at IS NULL").fetchone()[0]
            halt = con.execute(
                "SELECT COUNT(*) FROM safety_incidents WHERE reason='decision_worker_crashed'"
            ).fetchone()[0]
        finally:
            con.close()
    except Exception as exc:
        print(f"  Ledger unreadable ({exc}); re-run with --confirm after fixing it.")
        return
    print(f"  Would close {open_port} open portfolio session(s) and {open_rt} open runtime session(s) as interrupted.")
    if halt:
        print(f"  Would clear {halt} decision_worker_crashed incident(s) ONLY after a verified broker reconcile.")
    else:
        print("  No decision_worker_crashed incident to clear.")
    print("  Re-run with --confirm to apply. The breaker clear stays scoped to the reconciled account(s).")


if __name__ == "__main__":
    raise SystemExit(main())
