"""The only configured treatment-arm difference is the global position cap."""

from dataclasses import replace

import pytest

from research.residual_reversal_physical_capacity_replay_v1.build_inputs import frozen_config
from research.residual_reversal_physical_capacity_replay_v1.run_capacity import _frozen_event_window


def test_frozen_arm_configuration_differs_only_by_global_cap() -> None:
    six = frozen_config(6)
    eight = frozen_config(8)
    assert six.risk.max_positions == 6
    assert eight.risk.max_positions == 8
    assert replace(six, risk=replace(six.risk, max_positions=8)) == eight
    assert six.strategy == eight.strategy
    assert six.universe == eight.universe
    assert six.execution == eight.execution


def test_frozen_window_excludes_only_flat_post_terminal_feed_events() -> None:
    cutoff = "2026-07-31T00:00:00+00:00"
    events = [
        {"event": "account_mark", "timestamp": cutoff, "position_count": 0, "slot_claim_count": 0},
        {
            "event": "corporate_action",
            "timestamp": "2026-08-03T13:30:00+00:00",
            "position_qty_before": 0.0,
            "position_qty_after": 0.0,
        },
        {
            "event": "account_mark",
            "timestamp": "2026-08-03T13:30:00+00:00",
            "position_count": 0,
            "slot_claim_count": 0,
        },
        {"event": "final_state", "pending": []},
    ]
    kept, receipt = _frozen_event_window(events)
    assert kept == [events[0], events[-1]]
    assert receipt["excluded_event_count"] == 2
    with pytest.raises(RuntimeError, match="touched inventory"):
        _frozen_event_window(
            [
                {**event, "position_qty_after": 1.0} if event["event"] == "corporate_action" else event
                for event in events
            ]
        )
