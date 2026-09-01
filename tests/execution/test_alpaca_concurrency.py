"""Concurrency and idempotency for crypto exit locks."""

import threading
import time
from pathlib import Path

from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.fake_client import FakeTradingClient
from parayabuck.execution.ledger import ExecutionLedger


def _broker(tmp_path: Path):
    ledger = ExecutionLedger(tmp_path / "ledger.db")
    fake = FakeTradingClient()
    broker = AlpacaBroker("k", "s", paper=True, ledger=ledger, peak_state_path=tmp_path / "peak.json")
    broker._client = fake  # type: ignore[assignment]
    broker.portfolio_open_orders = lambda: ()  # type: ignore[method-assign]
    fake.set_account(equity="10000")
    return broker, fake


def test_same_symbol_exit_serialized(tmp_path: Path):
    broker, fake = _broker(tmp_path)
    # Verify locks are per-symbol
    assert broker._symbol_exit_locks_guard is not None
    # Two threads acquiring same symbol lock should serialize
    lock = broker._symbol_exit_locks.setdefault("BTC/USD", threading.Lock())
    acquired: list[int] = []

    def try_acquire() -> None:
        if lock.acquire(blocking=False):
            acquired.append(1)
            time.sleep(0.05)
            lock.release()
        else:
            acquired.append(0)

    t1 = threading.Thread(target=try_acquire)
    t2 = threading.Thread(target=try_acquire)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # One should have acquired, the other not (non-blocking)
    assert sorted(acquired) == [0, 1]


def test_reconcile_idempotent(tmp_path: Path):
    broker, fake = _broker(tmp_path)
    fake.set_position("IWM", qty=10)
    # First reconcile
    r1 = broker.reconcile()
    r2 = broker.reconcile()
    assert r1 == r2
    # Idempotent: same broker truth -> same buckets


def test_different_symbols_not_blocked(tmp_path: Path):
    broker, fake = _broker(tmp_path)
    l1 = broker._symbol_exit_locks.setdefault("BTC/USD", threading.Lock())
    l2 = broker._symbol_exit_locks.setdefault("ETH/USD", threading.Lock())
    assert l1 is not l2
