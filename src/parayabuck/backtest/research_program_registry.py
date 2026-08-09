"""Persistent strategy-family budget for preregistered research programs."""

from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class ResearchFamilyRegistration:
    research_program_id: str
    market: str
    family_name: str
    economic_hypothesis: str
    preregistered_at: datetime


class ResearchFamilyBudgetExceeded(RuntimeError):
    pass


class ResearchProgramRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def entries(self) -> tuple[ResearchFamilyRegistration, ...]:
        if not self.path.exists():
            return ()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        values = raw.get("entries", []) if isinstance(raw, dict) else []
        return tuple(
            ResearchFamilyRegistration(
                research_program_id=str(item["research_program_id"]),
                market=str(item["market"]),
                family_name=str(item["family_name"]),
                economic_hypothesis=str(item["economic_hypothesis"]),
                preregistered_at=datetime.fromisoformat(str(item["preregistered_at"])),
            )
            for item in values
            if isinstance(item, dict)
        )

    def register(
        self,
        registration: ResearchFamilyRegistration,
        *,
        maximum_families: int = 3,
        window_days: int = 365,
    ) -> None:
        if registration.preregistered_at.tzinfo is None:
            raise ValueError("Research preregistration must be timezone-aware")
        if maximum_families <= 0 or window_days <= 0:
            raise ValueError("Research family budget and window must be positive")
        with self._locked():
            entries = list(self.entries())
            key = (
                registration.research_program_id,
                registration.family_name,
            )
            if any((item.research_program_id, item.family_name) == key for item in entries):
                return
            start = registration.preregistered_at - timedelta(days=window_days)
            active = {
                item.family_name
                for item in entries
                if item.research_program_id == registration.research_program_id
                and item.market == registration.market
                and start <= item.preregistered_at <= registration.preregistered_at
            }
            if len(active) >= maximum_families:
                raise ResearchFamilyBudgetExceeded(
                    f"Research program {registration.research_program_id!r} already "
                    f"used {maximum_families} families in {window_days} days"
                )
            entries.append(registration)
            payload = {
                "version": 1,
                "entries": [
                    {
                        **asdict(item),
                        "preregistered_at": item.preregistered_at.astimezone(UTC).isoformat(),
                    }
                    for item in entries
                ],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)


__all__ = [
    "ResearchFamilyBudgetExceeded",
    "ResearchFamilyRegistration",
    "ResearchProgramRegistry",
]
