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
* ``failed`` — evaluation failed or was incomplete; retrying the same
  experiment is allowed but the attempt stays on record.

Writes are atomic (temporary file + rename) so a crash can never leave a
half-written registry.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.core.models import Market

STATE_PENDING = "pending"
STATE_COMPLETE = "complete"
STATE_FAILED = "failed"


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

    @staticmethod
    def _default_path() -> Path:
        return Path("data/runtime/portfolio_holdout_registry.json")

    def _load(self) -> list[HoldoutEvaluationRecord]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        records = raw.get("records", [])
        if not isinstance(records, list):
            return []
        loaded: list[HoldoutEvaluationRecord] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            loaded.append(
                HoldoutEvaluationRecord(
                    record_id=str(item.get("record_id", "")),
                    structural_digest=str(item.get("structural_digest", "")),
                    full_digest=str(item.get("full_digest", "")),
                    experiment_id=str(item.get("experiment_id", "")),
                    state=str(item.get("state", "")),
                    evaluated_at=datetime.fromisoformat(str(item.get("evaluated_at", ""))),
                    data_hash=str(item.get("data_hash", "")),
                    notes=str(item.get("notes", "")),
                )
            )
        return loaded

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
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
            record
            for record in self._records
            if record.structural_digest == structural_digest
        )

    def expire_stale_pending(self, ttl_seconds: float = 24 * 3600) -> int:
        """Recover holdouts whose evaluation crashed mid-flight.

        A crash between ``begin_evaluation`` and ``complete/fail_evaluation``
        leaves a PENDING record that would otherwise burn the structural key
        forever. Records older than ``ttl_seconds`` are marked FAILED so the
        same experiment can retry; the attempt stays on record.
        """
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
                    notes="stale pending evaluation expired (crash recovery)",
                )
                expired += 1
        if expired:
            self._persist()
        return expired

    def preflight(self, fingerprint: HoldoutFingerprint, experiment_id: str) -> None:
        """Fail-closed check before any holdout bar is read."""
        self.expire_stale_pending()
        digest = fingerprint.structural_digest()
        for record in self.records_for(digest):
            if record.state in (STATE_PENDING, STATE_COMPLETE):
                raise HoldoutLockedError(
                    f"Holdout already used by experiment {record.experiment_id!r} "
                    f"(state={record.state}); reopening is forbidden"
                )

    def begin_evaluation(self, fingerprint: HoldoutFingerprint, experiment_id: str) -> None:
        """Mark the holdout as pending; duplicates a pending/complete lock."""
        self.preflight(fingerprint, experiment_id)
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

    def complete_evaluation(
        self,
        fingerprint: HoldoutFingerprint,
        experiment_id: str,
        data_hash: str,
    ) -> None:
        record = self._mutable_record(fingerprint, experiment_id)
        updated = HoldoutEvaluationRecord(
            record_id=record.record_id,
            structural_digest=record.structural_digest,
            full_digest=fingerprint.full_digest(),
            experiment_id=experiment_id,
            state=STATE_COMPLETE,
            evaluated_at=datetime.now(UTC),
            data_hash=data_hash,
            notes="holdout evaluated once; locked forever",
        )
        self._replace_record(updated)

    def fail_evaluation(
        self,
        fingerprint: HoldoutFingerprint,
        experiment_id: str,
        reason: str,
    ) -> None:
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
    "STATE_COMPLETE",
    "STATE_FAILED",
    "STATE_PENDING",
]
