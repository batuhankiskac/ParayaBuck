"""Integration follow-ups of audit wave 2 (orchestrator, after the X1-X4 merges)."""

from __future__ import annotations

import pytest

from parayabuck.cli import _strict_events_query
from parayabuck.portfolio.runtime_service import PortfolioRuntime


class _FailingJournal:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def query(self, **filters: object) -> list[object]:
        self.calls.append(filters)
        if filters.get("strict"):
            raise OSError("database disk image is malformed")
        return []


def test_ops_events_reads_the_journal_strictly() -> None:
    # A failed read must surface, not look like "no events" (D2-6-2).
    journal = _FailingJournal()
    with pytest.raises(OSError, match="malformed"):
        _strict_events_query(journal, limit=5)
    assert journal.calls == [{"limit": 5, "strict": True}]


def test_december_calendar_fallback_raises_one_alert_per_decision() -> None:
    service = object.__new__(PortfolioRuntime)
    raised: list[tuple[tuple[str, str], dict[str, object]]] = []
    service._raise_operator_alert_once = lambda key, **fields: raised.append((key, fields))  # type: ignore[method-assign]

    service._alert_december_calendar_fallback("dec-1", [2026, 2026])

    assert raised[0][0] == ("dec-1", "december_calendar_fallback")
    assert raised[0][1]["years"] == [2026]
