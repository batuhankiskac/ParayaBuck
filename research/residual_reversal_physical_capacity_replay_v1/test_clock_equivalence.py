"""Archive-backed structural chronology check; no economic fields asserted."""

from research.residual_reversal_physical_capacity_replay_v1.clock_equivalence import audit


def test_archived_reversal_clock_mapping() -> None:
    result = audit()
    assert result["decision_sessions_checked"] == 495
    assert result["archived_fill_groups_checked"] == 3348
    assert result["non_xnys_decisions"] == 0
    assert result["fill_session_mismatches"] == 0
    assert result["archived_buy_target_symbol_mismatches"] == 0
    assert result["classification"] == "REVERSAL_PHYSICAL_CLOCK_REVALIDATION_REQUIRED"
