"""Holdout evaluation registry with structural fingerprint locking.

The outer holdout may be evaluated exactly once per holdout — not per
experiment id. A structural fingerprint (market, timeframe, universe
snapshot hash, holdout window, adjustment policy, source provider) is the
primary key; the holdout bar hash is added after the bars are read and
recorded for confirmation.

The registry must be consulted BEFORE the backtest engine ever touches the
holdout bars: a previously used holdout may not be read again, regardless of
the experiment id or manifest name used by the new run.

State model:

* ``pending`` — evaluation started; locks the structural key.
* ``complete`` — evaluation finished successfully; locked forever.
* ``failed`` — evaluation failed or was incomplete; the key stays locked.
  A crash before the outcome was persisted does NOT auto-unlock: the
  process may already have computed the holdout, so reopening requires a
  manual, audited operator decision.
* ``audited`` — an operator reviewed the failed/pending attempt and
  explicitly authorized a new evaluation; only then may a new record be
  started for the same structural key.

Concurrency: all read-modify-write operations hold an exclusive OS file
lock on the registry, so two processes can never both pass ``preflight``
for the same holdout.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.core.models import Market

STATE_PENDING = "pending"
STATE_PASSED = "passed"
# Backward-compatible public name; new records use the explicit terminal
# vocabulary passed/failed/error.
STATE_COMPLETE = STATE_PASSED
STATE_FAILED = "failed"
STATE_ERROR = "error"
STATE_AUDITED = "audited"
_LEGACY_COMPLETE = "complete"


@dataclass(frozen=True)
class HoldoutFingerprint:
    market: Market
    timeframe: str
    universe_snapshot_hash: str
    holdout_start: datetime
    holdout_end: datetime
    adjustment: str
    source_provider: str
    bar_hash: str = ""

    def with_bar_hash(self, bar_hash: str) -> HoldoutFingerprint:
        return HoldoutFingerprint(
            self.market,
            self.timeframe,
            self.universe_snapshot_hash,
            self.holdout_start,
            self.holdout_end,
            self.adjustment,
            self.source_provider,
            bar_hash,
        )

    def structural_digest(self) -> str:
        """Key that identifies the holdout itself (bar contents excluded)."""
        payload = "|".join(
            (
                self.market.value,
                self.timeframe,
                self.universe_snapshot_hash,
                self.holdout_start.isoformat(),
                self.holdout_end.isoformat(),
                self.adjustment,
                self.source_provider,
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def full_digest(self) -> str:
        if not self.bar_hash:
            return self.structural_digest()
        payload = self.structural_digest() + "|" + self.bar_hash
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class HoldoutEvaluationRecord:
    record_id: str
    structural_digest: str
    full_digest: str
    experiment_id: str
    state: str
    evaluated_at: datetime
    data_hash: str = ""
    notes: str = ""


class HoldoutRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._records = self._load()

    def _load(self) -> list[HoldoutEvaluationRecord]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(
                "Holdout registry is corrupt (unreadable JSON); manual audit required"
            ) from exc
        if not isinstance(raw, dict):
            raise ValueError(
                "Holdout registry is corrupt (root is not an object); manual audit required"
            )
        records = raw.get("records", [])
        if not isinstance(records, list):
            raise ValueError(
                "Holdout registry is corrupt ('records' is not a list); manual audit required"
            )
        loaded: list[HoldoutEvaluationRecord] = []
        for item in records:
            if not isinstance(item, dict):
                raise ValueError(
                    "Holdout registry is corrupt (a record is not an object); manual audit required"
                )
            try:
                evaluated_at = datetime.fromisoformat(str(item.get("evaluated_at", "")))
            except ValueError as exc:
                raise ValueError(
                    f"Holdout registry contains a record with an invalid evaluated_at: {item!r}"
                ) from exc
            loaded.append(
                HoldoutEvaluationRecord(
                    record_id=str(item.get("record_id", "")),
                    structural_digest=str(item.get("structural_digest", "")),
                    full_digest=str(item.get("full_digest", "")),
                    experiment_id=str(item.get("experiment_id", "")),
                    state=str(item.get("state", "")),
                    evaluated_at=evaluated_at,
                    data_hash=str(item.get("data_hash", "")),
                    notes=str(item.get("notes", "")),
                )
            )
        return loaded

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 3,
            "records": [asdict(record) for record in self._records],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def records_for(self, structural_digest: str) -> tuple[HoldoutEvaluationRecord, ...]:
        return tuple(
            record for record in self._records if record.structural_digest == structural_digest
        )

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Exclusive OS lock over read-modify-write registry operations."""
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            self._records = self._load()
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def expire_stale_pending(self, ttl_seconds: float = 24 * 3600) -> int:
        """Mark crashed evaluations as FAILED for the audit trail.

        A crash between ``begin_evaluation`` and ``complete/fail_evaluation``
        leaves a PENDING record. It is marked FAILED (so the audit trail is
        honest) but NEVER auto-unlocked: the process may have computed the
        holdout before crashing, so reopening requires ``recover_holdout``.
        """
        with self._locked():
            cutoff = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
            expired = 0
            for index, record in enumerate(self._records):
                if record.state == STATE_PENDING and record.evaluated_at < cutoff:
                    self._records[index] = HoldoutEvaluationRecord(
                        record_id=record.record_id,
                        structural_digest=record.structural_digest,
                        full_digest=record.full_digest,
                        experiment_id=record.experiment_id,
                        state=STATE_FAILED,
                        evaluated_at=datetime.now(UTC),
                        data_hash=record.data_hash,
                        notes=(
                            "stale pending evaluation expired (crash recovery); "
                            "manual audit required before reopening"
                        ),
                    )
                    expired += 1
            if expired:
                self._persist()
            return expired

    def _preflight_unlocked(self, fingerprint: HoldoutFingerprint, experiment_id: str) -> None:
        digest = fingerprint.structural_digest()
        for record in self.records_for(digest):
            if record.state in (
                STATE_PENDING,
                STATE_PASSED,
                STATE_FAILED,
                STATE_ERROR,
                _LEGACY_COMPLETE,
            ):
                raise HoldoutLockedError(
                    f"Holdout already used by experiment {record.experiment_id!r} "
                    f"(state={record.state}); reopening requires an explicit "
                    "audited recovery"
                )

    def preflight(self, fingerprint: HoldoutFingerprint, experiment_id: str) -> None:
        """Fail-closed check before any holdout bar is read."""
        with self._locked():
            self._preflight_unlocked(fingerprint, experiment_id)

    def begin_evaluation(self, fingerprint: HoldoutFingerprint, experiment_id: str) -> str:
        """Mark the holdout as pending; duplicates a pending/complete lock."""
        with self._locked():
            self._preflight_unlocked(fingerprint, experiment_id)
            digest = fingerprint.structural_digest()
            record = HoldoutEvaluationRecord(
                record_id=uuid.uuid4().hex[:16],
                structural_digest=digest,
                full_digest=fingerprint.full_digest(),
                experiment_id=experiment_id,
                state=STATE_PENDING,
                evaluated_at=datetime.now(UTC),
            )
            self._records.append(record)
            self._persist()
            return record.record_id

    def recover_holdout(
        self,
        fingerprint: HoldoutFingerprint,
        experiment_id: str,
        *,
        audit_note: str,
    ) -> None:
        """Manually authorize reopening a crashed/incomplete holdout.

        This is the ONLY path that unlocks a ``failed``/``pending`` record
        and it must be an explicit operator decision: the audit note is
        persisted with the record so the reopening is fully attributable.
        After recovery a NEW evaluation may start (with a fresh experiment
        id).
        """
        with self._locked():
            digest = fingerprint.structural_digest()
            matches = [
                record
                for record in self._records
                if record.structural_digest == digest
                and record.experiment_id == experiment_id
                and record.state in (STATE_PENDING, STATE_FAILED, STATE_ERROR)
            ]
            if not matches:
                raise HoldoutLockedError(
                    f"No recoverable (pending/failed) evaluation for "
                    f"{experiment_id!r}; nothing to audit"
                )
            updated = HoldoutEvaluationRecord(
                record_id=matches[-1].record_id,
                structural_digest=digest,
                full_digest=matches[-1].full_digest,
                experiment_id=experiment_id,
                state=STATE_AUDITED,
                evaluated_at=datetime.now(UTC),
                data_hash=matches[-1].data_hash,
                notes=f"audited reopen: {audit_note}",
            )
            self._replace_record(updated)

    def complete_evaluation(
        self,
        fingerprint: HoldoutFingerprint,
        experiment_id: str,
        data_hash: str,
    ) -> None:
        with self._locked():
            record = self._mutable_record(fingerprint, experiment_id)
            updated = HoldoutEvaluationRecord(
                record_id=record.record_id,
                structural_digest=record.structural_digest,
                full_digest=fingerprint.full_digest(),
                experiment_id=experiment_id,
                state=STATE_PASSED,
                evaluated_at=datetime.now(UTC),
                data_hash=data_hash,
                notes="holdout evaluated once; locked forever",
            )
            self._replace_record(updated)

    def error_evaluation(
        self,
        fingerprint: HoldoutFingerprint,
        experiment_id: str,
        reason: str,
    ) -> None:
        """Finish an evaluation that could not produce a valid gate result."""
        with self._locked():
            record = self._mutable_record(fingerprint, experiment_id)
            updated = HoldoutEvaluationRecord(
                record_id=record.record_id,
                structural_digest=record.structural_digest,
                full_digest=fingerprint.full_digest(),
                experiment_id=experiment_id,
                state=STATE_ERROR,
                evaluated_at=datetime.now(UTC),
                data_hash=record.data_hash,
                notes=reason,
            )
            self._replace_record(updated)

    def fail_evaluation(
        self,
        fingerprint: HoldoutFingerprint,
        experiment_id: str,
        reason: str,
    ) -> None:
        with self._locked():
            record = self._mutable_record(fingerprint, experiment_id)
            updated = HoldoutEvaluationRecord(
                record_id=record.record_id,
                structural_digest=record.structural_digest,
                full_digest=fingerprint.full_digest(),
                experiment_id=experiment_id,
                state=STATE_FAILED,
                evaluated_at=datetime.now(UTC),
                data_hash=record.data_hash,
                notes=reason,
            )
            self._replace_record(updated)

    def _mutable_record(
        self, fingerprint: HoldoutFingerprint, experiment_id: str
    ) -> HoldoutEvaluationRecord:
        digest = fingerprint.structural_digest()
        matches = [
            record
            for record in self._records
            if record.structural_digest == digest and record.experiment_id == experiment_id
        ]
        if not matches:
            raise HoldoutLockedError(
                f"No pending evaluation for {experiment_id!r}; call begin_evaluation first"
            )
        return matches[-1]

    def _replace_record(self, record: HoldoutEvaluationRecord) -> None:
        self._records = [
            existing if existing.record_id != record.record_id else record
            for existing in self._records
        ]
        self._persist()


class HoldoutLockedError(RuntimeError):
    """Raised when a holdout was already evaluated or evaluation is pending."""


__all__ = [
    "HoldoutEvaluationRecord",
    "HoldoutFingerprint",
    "HoldoutLockedError",
    "HoldoutRegistry",
    "STATE_AUDITED",
    "STATE_COMPLETE",
    "STATE_ERROR",
    "STATE_FAILED",
    "STATE_PENDING",
    "STATE_PASSED",
]
