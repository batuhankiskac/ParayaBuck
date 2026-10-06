from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.config import Config
from parayabuck.core.models import (
    AccountSnapshot,
    Market,
)
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.runtime import EngineRuntime

pytestmark = pytest.mark.paper_chaos


def test_account_poll_does_not_restore_stale_consecutive_loss_count(tmp_path: Path) -> None:
    class RiskStub:
        consecutive_losses = 2

        def record_realized_pnl(self, pnl: float) -> None:
            self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0

    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.ledger = ledger
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.app_config = Config()
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._consecutive_losses = 2
    runtime._circuit_breaker_reason = None
    account = AccountSnapshot(
        cash=10_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(),
    )

    runtime._persist_risk_state(account)
    runtime._consecutive_losses = 3
    runtime._persist_risk_state(account)
    recovered_loss = AccountSnapshot(
        cash=9_950,
        equity=9_950,
        peak_equity=10_000,
        daily_pnl=-50,
        positions=(),
        realized_pnl=-50,
    )
    runtime._persist_risk_state(recovered_loss)

    saved = ledger.load_risk_state("acct", datetime.now(UTC).date())
    assert saved is not None
    assert saved.consecutive_losses == 4
    ledger.close()


def test_corrupt_risk_state_payload_is_treated_as_absent(tmp_path: Path) -> None:
    """A truncated/corrupt risk_state row must not fail startup: it is
    treated as absent and rebuilt from the account."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.connection.execute(
        """
        INSERT INTO risk_state(account_id, session_date, payload, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "acct",
            datetime.now(UTC).date().isoformat(),
            "{truncated json",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    ledger.connection.commit()
    assert ledger.load_risk_state("acct", datetime.now(UTC).date()) is None
    ledger.close()
