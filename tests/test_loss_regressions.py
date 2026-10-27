from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from parayabuck.execution.ledger import ExecutionLedger

pytestmark = pytest.mark.paper_chaos


def test_corrupt_risk_state_payload_is_treated_as_absent(tmp_path: Path) -> None:
    """A truncated/corrupt risk_state row must not fail startup: it is
    treated as absent and rebuilt from the account."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    # One session date for the seed and the read: two clock reads straddle midnight.
    today = datetime.now(UTC).date()
    ledger.connection.execute(
        """
        INSERT INTO risk_state(account_id, session_date, payload, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "acct",
            today.isoformat(),
            "{truncated json",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    ledger.connection.commit()
    assert ledger.load_risk_state("acct", today) is None
    ledger.close()
