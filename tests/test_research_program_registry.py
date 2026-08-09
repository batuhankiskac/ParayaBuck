from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.research_program_registry import (
    ResearchFamilyBudgetExceeded,
    ResearchFamilyRegistration,
    ResearchProgramRegistry,
)


def test_research_program_allows_only_three_new_families_per_year(tmp_path) -> None:
    registry = ResearchProgramRegistry(tmp_path / "families.json")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(3):
        registry.register(
            ResearchFamilyRegistration(
                "crypto-vnext",
                "crypto",
                f"family-{index}",
                f"hypothesis-{index}",
                now + timedelta(days=index),
            )
        )
    # Re-registering the same family is idempotent and does not consume budget.
    registry.register(
        ResearchFamilyRegistration("crypto-vnext", "crypto", "family-0", "hypothesis-0", now)
    )
    with pytest.raises(ResearchFamilyBudgetExceeded):
        registry.register(
            ResearchFamilyRegistration(
                "crypto-vnext",
                "crypto",
                "family-3",
                "different-mechanism",
                now + timedelta(days=10),
            )
        )
    assert len(registry.entries()) == 3


def test_family_budget_rolls_forward_only_after_full_window(tmp_path) -> None:
    registry = ResearchProgramRegistry(tmp_path / "families.json")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for index in range(3):
        registry.register(
            ResearchFamilyRegistration("equity-vnext", "equity", f"old-{index}", "h", start)
        )
    registry.register(
        ResearchFamilyRegistration(
            "equity-vnext",
            "equity",
            "new-family",
            "new-hypothesis",
            start + timedelta(days=366),
        )
    )
    assert len(registry.entries()) == 4
