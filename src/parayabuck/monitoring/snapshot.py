"""Persistent runtime heartbeat / snapshot — atomic JSON writer.

Writes ``data/runtime/health_snapshot.json`` periodically via atomic_write.
Snapshot contains enough state for offline ``ops status`` without broker access.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.monitoring.alerts import alert_stats
from parayabuck.monitoring.journal import journal_stats
from parayabuck.utils.io import atomic_write

log = logging.getLogger("parayabuck.monitoring.snapshot")

DEFAULT_SNAPSHOT_PATH = Path("data/runtime/health_snapshot.json")
DEFAULT_INTERVAL_SECONDS = 30.0
SCHEMA_VERSION = 1


def build_snapshot(
    *,
    pid: int | None = None,
    session_id: str | None = None,
    mode: str | None = None,
    runtime_state: str | None = None,
    runtime_health: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build snapshot dict (JSON serializable) from provided health."""
    import os

    now = datetime.now(UTC)
    # Evaluate system health if we have enough context? For snapshot writer we just embed runtime_health
    # and compute summary via evaluate_health if we are offline path? But build_snapshot is called
    # from runtime where we already have health — we embed it and also compute overall
    snap: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "written_at": now.isoformat(),
        "pid": pid if pid is not None else os.getpid(),
        "session_id": session_id,
        "mode": mode,
        "runtime_state": runtime_state or (runtime_health.get("state") if isinstance(runtime_health, dict) else None),
        "runtime_health": runtime_health or {},
        # Alert/journal counters of THIS (engine) process. `ops status`,
        # `ops doctor` and the web API run in other processes, so the snapshot
        # is the only place they can read the engine's real counters from.
        "observability": journal_stats(),
        "alerts": alert_stats(),
        "extra": extra or {},
    }
    # Also include derived system health if possible via quick evaluate? Keep minimal to avoid DB reads in writer.
    return snap


def _snapshot_json_default(obj: Any) -> str:
    """JSON fallback for non-serializable types in snapshot (datetime -> ISO 8601)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    # Fallback for other non-serializables (e.g. Path, Enum) — use str
    return str(obj)


def _make_json_serializable(value: Any) -> Any:
    """Recursively convert datetime etc. to JSON-safe primitives for snapshot."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _make_json_serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_make_json_serializable(v) for v in value]
    return value


def write_snapshot_atomic(path: str | Path, snapshot: dict[str, Any]) -> bool:
    """Atomic write via temp+replace. Never raises."""
    try:
        p = Path(path)
        # Ensure snapshot is JSON serializable (handles WorkerHealth datetime fields)
        safe_snapshot = _make_json_serializable(snapshot)
        content = json.dumps(safe_snapshot, indent=2, sort_keys=True, default=_snapshot_json_default)
        atomic_write(p, content)
        return True
    except Exception:
        log.warning("snapshot write failed for %s", path, exc_info=True)
        return False


def read_snapshot(path: str | Path = DEFAULT_SNAPSHOT_PATH) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except Exception:
        log.warning("snapshot corrupt at %s", path, exc_info=True)
        return None


class SnapshotWriter:
    """Periodic atomic snapshot writer (bounded, never blocks trading correctness)."""

    def __init__(
        self,
        path: str | Path = DEFAULT_SNAPSHOT_PATH,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        provider: Any | None = None,
    ) -> None:
        self.path = Path(path)
        self.interval = max(5.0, float(interval_seconds))
        self.provider = provider  # callable returning dict or RuntimeHealth
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False
        self._lock = threading.Lock()
        # Serializes periodic writes with the final write, and _final turns
        # every later periodic write off (C2-9-4).
        self._write_lock = threading.Lock()
        self._final = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            self._final = False
            self._thread = threading.Thread(target=self._loop, name="pb-snapshot-writer", daemon=True)
            self._thread.start()
            self._started = True

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            if not self._started:
                return
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        with self._lock:
            self._started = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.write_once()
            except Exception:
                log.warning("snapshot periodic write failed", exc_info=True)
            if self._stop.wait(self.interval):
                break

    def write_once(self) -> bool:
        with self._write_lock:
            if self._final:
                return False
            return self._write_snapshot()

    def _write_snapshot(self) -> bool:
        snap: dict[str, Any] | None = None
        try:
            if self.provider is not None:
                try:
                    provided = self.provider()
                except Exception:
                    log.warning("snapshot provider failed", exc_info=True)
                    provided = None
                if isinstance(provided, dict):
                    # assume already snapshot-like
                    if "written_at" not in provided:
                        provided = build_snapshot(runtime_health=provided, runtime_state=provided.get("state"))
                    snap = provided
                elif provided is not None and hasattr(provided, "__dataclass_fields__"):
                    # RuntimeHealth dataclass
                    from dataclasses import asdict

                    rh = asdict(provided)
                    snap = build_snapshot(runtime_health=rh, runtime_state=rh.get("state"))
                else:
                    snap = None
            if snap is None:
                snap = build_snapshot()
        except Exception:
            log.warning("snapshot build failed", exc_info=True)
            snap = build_snapshot()

        try:
            return write_snapshot_atomic(self.path, snap)
        except Exception:
            log.warning("snapshot write failed (isolated)", exc_info=True)
            return False

    def write_stopped(self, state: str = "stopped") -> None:
        """Write the final snapshot with the stop outcome (best effort).

        Taken under the write lock, so a periodic write already in progress
        finishes first, and ``_final`` keeps every later one from replacing this
        snapshot. The timed acquire bounds the wait if the provider is wedged;
        the write then goes ahead without the lock.
        """
        acquired = self._write_lock.acquire(timeout=5.0)
        try:
            self._final = True
            snap = build_snapshot(runtime_state=state)
            write_snapshot_atomic(self.path, snap)
        except Exception:
            log.warning("snapshot stopped write failed", exc_info=True)
        finally:
            if acquired:
                self._write_lock.release()


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_SNAPSHOT_PATH",
    "SCHEMA_VERSION",
    "SnapshotWriter",
    "build_snapshot",
    "read_snapshot",
    "write_snapshot_atomic",
]
