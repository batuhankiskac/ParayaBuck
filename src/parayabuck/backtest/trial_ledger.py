"""Global portfolio trial ledger keyed by dataset hash.

Deflation (DSR) must count EVERY hypothesis tried on the same dataset within
the same research program, not just the candidates of the current experiment
run: a new experiment id must never be able to reset the multiple-testing
count. The ledger is keyed by the dataset's data hash, so different datasets
never contaminate each other's counts, and entries are deduplicated by
(dataset hash, trial id, experiment id) so re-runs do not inflate the count.

Writes are serialized with an exclusive OS file lock (like the holdout
registry) so concurrent research processes cannot race the read-modify-write.
"""

from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from parayabuck.backtest.portfolio_experiments import PortfolioTrialResult


class LedgerTrial(NamedTuple):
    """Lightweight trial record for backfilling pre-ledger runs."""

    trial_id: str
    strategy_name: str
    strategy_version: str
    parameters: dict[str, object]


def _trial_fields(trial: PortfolioTrialResult | LedgerTrial) -> LedgerTrial:
    if isinstance(trial, LedgerTrial):
        return trial
    return LedgerTrial(
        trial_id=trial.trial_id,
        strategy_name=trial.strategy_name,
        strategy_version=trial.strategy_version,
        parameters=dict(trial.parameters),
    )


class TrialLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._entries: list[dict[str, object]] = self._load()

    @staticmethod
    def _default_path() -> Path:
        return Path("data/runtime/portfolio_trial_ledger.json")

    def _load(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        # A corrupt ledger must fail loudly, never silently reset: the DSR
        # multiple-testing count is the whole point of this file.
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        entries = raw.get("entries", []) if isinstance(raw, dict) else []
        return [dict(item) for item in entries if isinstance(item, dict)]

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            self._entries = self._load()
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "entries": self._entries,
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def record_trials(
        self,
        *,
        dataset_id: str,
        dataset_hash: str,
        experiment_id: str,
        trials: tuple[PortfolioTrialResult | LedgerTrial, ...],
    ) -> int:
        """Record every candidate of a run; returns the new cumulative count.

        Idempotent per (dataset hash, trial id, experiment id): re-running
        the same experiment never double-counts.
        """
        with self._locked():
            now = datetime.now(UTC).isoformat()
            existing: set[tuple[str, str, str]] = {
                (
                    str(entry["dataset_hash"]),
                    str(entry["trial_id"]),
                    str(entry["experiment_id"]),
                )
                for entry in self._entries
            }
            added = 0
            for trial in trials:
                fields = _trial_fields(trial)
                key = (dataset_hash, fields.trial_id, experiment_id)
                if key in existing:
                    continue
                self._entries.append(
                    {
                        "dataset_id": dataset_id,
                        "dataset_hash": dataset_hash,
                        "trial_id": fields.trial_id,
                        "strategy_name": fields.strategy_name,
                        "strategy_version": fields.strategy_version,
                        "parameters": dict(fields.parameters),
                        "experiment_id": experiment_id,
                        "evaluated_at": now,
                    }
                )
                existing.add(key)
                added += 1
            if added:
                self._persist()
            return self.trial_count(dataset_hash)

    def trial_count(self, dataset_hash: str) -> int:
        """Cumulative distinct hypotheses tried on this dataset hash."""
        return sum(
            1
            for entry in self._entries
            if str(entry.get("dataset_hash", "")) == dataset_hash
        )

    def entries_for(self, dataset_hash: str) -> tuple[dict[str, object], ...]:
        return tuple(
            entry
            for entry in self._entries
            if str(entry.get("dataset_hash", "")) == dataset_hash
        )

    def backfill_from_reports(
        self,
        *,
        dataset_id: str,
        dataset_hash: str,
        experiment_id: str,
        trials: tuple[PortfolioTrialResult | LedgerTrial, ...],
    ) -> int:
        """Backfill previously-run candidates into the ledger.

        Used to make DSR honest about hypotheses tried BEFORE the ledger
        existed (e.g. the original v2 run). Deduplicated like record_trials.
        """
        return self.record_trials(
            dataset_id=dataset_id,
            dataset_hash=dataset_hash,
            experiment_id=experiment_id,
            trials=trials,
        )


__all__ = ["LedgerTrial", "TrialLedger"]
