"""Structural authority comparison without running account arms."""

from research.residual_reversal_physical_capacity_replay_v1.incumbent_parity import (
    compare,
    static_identity,
    terminal_count,
)


def test_static_incumbent_inputs_match_tagged_v2_except_global_cap() -> None:
    identity = static_identity()
    assert identity["config_sections_differing_after_cap6_normalization"] == []
    assert identity["timing_byte_identical"]
    assert identity["equity_exclusions"] == ["SPY", "SH", "PSQ", "DOG"]


def test_structural_comparison_flags_event_identity() -> None:
    authority = {
        "event_order_sha256": "expected",
        "fill_count": 1,
        "future_data_access_checks": 1,
        "maximum_observed_position_count": 1,
        "maximum_observed_slot_claim_count": 1,
        "over_cap_timestamp_count": 0,
        "reservation_created": 1,
        "reservation_released": 1,
    }
    assert compare(authority, {**authority, "event_order_sha256": "changed"}) == ["event_order_sha256"]


def test_terminal_count_includes_final_and_inactive_exits() -> None:
    events = ({"event": "terminal_liquidation"}, {"event": "final_liquidation"}, {"event": "fill"})
    assert terminal_count(events) == 2
