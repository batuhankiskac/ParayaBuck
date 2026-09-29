"""Reconciliation idempotency against a fake broker."""

from pathlib import Path

from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.fake_client import FakeTradingClient
from parayabuck.execution.ledger import ExecutionLedger


def _broker(tmp_path: Path):
    ledger = ExecutionLedger(tmp_path / "ledger.db")
    fake = FakeTradingClient()
    broker = AlpacaBroker("k", "s", paper=True, ledger=ledger, peak_state_path=tmp_path / "peak.json")
    broker._client = fake  # type: ignore[assignment]
    fake.set_account(equity="10000")
    return broker, fake


def test_reconcile_idempotent(tmp_path: Path):
    broker, fake = _broker(tmp_path)
    fake.set_position("IWM", qty=10)
    # First reconcile
    r1 = broker.reconcile()
    r2 = broker.reconcile()
    assert r1 == r2
    # Idempotent: same broker truth -> same buckets
