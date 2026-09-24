import pytest

from research.residual_reversal_physical_capacity_replay_v1.no_economic_leakage import assert_no_economics


def test_structural_schema_rejects_outcome_field_at_any_depth() -> None:
    assert_no_economics({"events": [{"slot_claim_count": 8, "admitted_opening": True}]})
    with pytest.raises(AssertionError, match="Prohibited economic field"):
        assert_no_economics({"events": [{"candidate": {"ending_nav": 123456}}]})
