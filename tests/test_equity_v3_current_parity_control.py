from __future__ import annotations

from datetime import UTC, datetime

from scripts.research_v3_current_parity_control import canonical_digest


def test_current_parity_receipt_digest_is_order_stable_and_datetime_safe() -> None:
    first = {"b": (datetime(2026, 1, 2, tzinfo=UTC),), "a": -0.0}
    second = {"a": 0.0, "b": [datetime(2026, 1, 2, tzinfo=UTC)]}

    assert canonical_digest(first) == canonical_digest(second)
