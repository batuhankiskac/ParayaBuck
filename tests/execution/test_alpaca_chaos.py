"""Chaos scenario: end-to-end with no duplicate economic actions."""

from pathlib import Path

from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.fake_client import FakeTradingClient
from parayabuck.execution.ledger import ExecutionLedger


def test_chaos_no_duplicate_fills_or_stops(tmp_path: Path):
    ledger = ExecutionLedger(tmp_path / "ledger.db")
    fake = FakeTradingClient()
    broker = AlpacaBroker("k", "s", paper=True, ledger=ledger, peak_state_path=tmp_path / "peak.json")
    broker._client = fake  # type: ignore[assignment]
    fake.set_account(equity="10000")
    fake.set_position("IWM", qty=10)
    # Initial reconcile bootstraps
    broker.reconcile()
    # Simulate a fill activity
    fake.activities = [{"id": "act-1", "symbol": "IWM", "qty": "10", "price": "100", "side": "buy"}]
    # Reconcile twice should not duplicate ledger entries or stops
    r2 = broker.reconcile()
    r3 = broker.reconcile()
    assert r2 == r3
    # No duplicate market orders tracked by fake
    assert fake.submitted_market_orders == 0
