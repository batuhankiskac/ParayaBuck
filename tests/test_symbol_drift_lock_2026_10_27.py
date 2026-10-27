"""Per-symbol ownership-drift lock (audit item 21, 2026-10-27).

With ``runtime.per_symbol_drift_lock`` off the runtime halts as before. With it
on, an unexplained broker shortfall locks only the drifting symbol: its orders
are refused locally, other symbols keep trading, the lock clears when the broker
covers the owned quantity again, and drift with no symbol still halts. Stubs and
the in-memory fake client only; no network, no disk state outside tmp_path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import RuntimeConfig
from parayabuck.core.models import Market
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.alpaca_models import DefiniteOrderRejection, OwnershipDriftError
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.runtime import EngineRuntime, _SymbolDriftOrderGate
from tests.fake_client import FakeTradingClient
from tests.helpers import make_broker

ACCOUNT_ID = "fake-acct-1234"


# --- Broker: alpaca_account shortfall handling ---


def _broker(tmp_path: Path, fake: FakeTradingClient, *, flag: bool) -> AlpacaBroker:
    broker = make_broker(
        tmp_path,
        client=fake,
        ledger=ExecutionLedger(str(tmp_path / "exec.sqlite3")),
        peak_state_path=tmp_path / "peak.json",
        portfolio_owned_quantities=lambda: {},
        portfolio_open_orders=lambda: [],
    )
    broker.portfolio_owned_quantities = lambda: {"IWM": 4.0, "QLD": 2.0}
    broker.portfolio_open_orders = lambda: []  # type: ignore[assignment]
    if flag:
        broker.per_symbol_drift_lock = True
    return broker


def test_flag_off_shortfall_still_raises_with_the_symbol(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    fake.set_position("IWM", qty=2)
    fake.set_position("QLD", qty=2)
    broker = _broker(tmp_path, fake, flag=False)
    with pytest.raises(OwnershipDriftError) as info:
        broker.account()
    assert info.value.symbols == ("IWM",)
    assert not hasattr(broker, "drift_locked_symbols")


def test_flag_on_shortfall_is_collected_and_snapshot_still_builds(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    fake.set_position("IWM", qty=2)
    fake.set_position("QLD", qty=2)
    broker = _broker(tmp_path, fake, flag=True)
    snap = broker.account()
    assert broker.drift_locked_symbols == frozenset({"IWM"})
    assert {p.symbol for p in snap.positions} == {"IWM", "QLD"}


def test_flag_on_unlocks_when_the_broker_covers_the_owned_quantity(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    fake.set_position("IWM", qty=2)
    broker = _broker(tmp_path, fake, flag=True)
    broker.account()
    assert broker.drift_locked_symbols == frozenset({"IWM"})
    fake.set_position("IWM", qty=4)
    broker.account()
    assert broker.drift_locked_symbols == frozenset()


# --- Runtime: lock map, alerts, reconcile, gate ---


def _runtime(flag: bool) -> EngineRuntime:
    runtime = object.__new__(EngineRuntime)
    runtime.app_config = SimpleNamespace(runtime=RuntimeConfig(per_symbol_drift_lock=flag))
    runtime.broker = SimpleNamespace(account_id=ACCOUNT_ID, submitted=[])
    runtime.broker.submit_portfolio_order = lambda order: runtime.broker.submitted.append(order.symbol) or "oid"
    runtime._schedule_reconcile = MagicMock()
    runtime._halt_for_ownership_drift = MagicMock()
    return runtime


@pytest.fixture
def alerts_log(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    emitted: list[dict] = []

    def fake_emit(kind: str, *, message: str, **fields: object) -> None:
        emitted.append({"kind": kind, "message": message, **fields})

    monkeypatch.setattr(runtime_module.alerts, "emit", fake_emit)
    return emitted


def _drive_account_loop(runtime: EngineRuntime, steps: list[object]) -> None:
    """Run ``_account_loop`` with one account() call per step.

    A frozenset step is the broker's drift-lock report for that pass; an
    exception step is raised by account(). The loop stops once every step has
    been consumed.
    """
    pending = list(steps)
    runtime._running = True
    runtime.config = SimpleNamespace(account_poll_interval_seconds=0)
    runtime._workers = {"pb-account": SimpleNamespace(alive=False, last_heartbeat=None)}
    runtime._startup_committed = False
    runtime._account_snapshot_cache_state = MagicMock(return_value=(None, None, 0))
    runtime._publish_account_snapshot = MagicMock(return_value=True)
    runtime._persist_risk_state = MagicMock()
    runtime._maybe_retry_rejected_exit_sells = MagicMock()
    runtime._maybe_check_trade_feed_freshness = MagicMock()
    runtime._maybe_run_intent_watchdog = MagicMock()
    runtime._maybe_check_standing_authorization = MagicMock()
    runtime._note_account_fetch_failure = MagicMock()
    runtime._portfolio_repository = MagicMock()

    def account() -> object:
        step = pending.pop(0)
        if isinstance(step, BaseException):
            raise step
        runtime.broker.drift_locked_symbols = step
        return object()

    def stop_when_consumed(*_args: object, **_kwargs: object) -> None:
        if not pending:
            runtime._running = False

    runtime.broker.account = account
    runtime._stop_event = SimpleNamespace(wait=MagicMock(side_effect=stop_when_consumed))
    runtime._account_loop()


def test_flag_off_drift_halts_the_whole_runtime_as_before(alerts_log) -> None:
    runtime = _runtime(flag=False)
    _drive_account_loop(runtime, [OwnershipDriftError("short", symbols=("IWM",))])
    runtime._halt_for_ownership_drift.assert_called_once()
    assert not getattr(runtime, "_symbol_drift_locks", {})
    runtime._schedule_reconcile.assert_not_called()
    assert alerts_log == []


def test_flag_on_locks_only_the_drifting_symbol_and_keeps_trading_others(alerts_log) -> None:
    runtime = _runtime(flag=True)
    _drive_account_loop(runtime, [frozenset({"IWM"}), frozenset({"IWM"})])
    runtime._halt_for_ownership_drift.assert_not_called()
    assert set(runtime._symbol_drift_lock_map()) == {"IWM"}
    assert runtime._symbol_locked_for_order("IWM", Market.EQUITY) is True
    assert runtime._symbol_locked_for_order("QLD", Market.EQUITY) is False
    gate = runtime._portfolio_order_broker()
    assert isinstance(gate, _SymbolDriftOrderGate)
    with pytest.raises(DefiniteOrderRejection):
        gate.submit_portfolio_order(SimpleNamespace(symbol="IWM", market=Market.EQUITY))
    assert gate.submit_portfolio_order(SimpleNamespace(symbol="QLD", market=Market.EQUITY)) == "oid"
    assert runtime.broker.submitted == ["QLD"]


def test_flag_on_alerts_once_per_symbol_and_schedules_reconcile(alerts_log) -> None:
    runtime = _runtime(flag=True)
    _drive_account_loop(runtime, [frozenset({"IWM"}), frozenset({"IWM"}), frozenset({"IWM", "SPY"})])
    locked = [a for a in alerts_log if a["kind"] == "circuit_breaker"]
    assert [a["symbol"] for a in locked] == ["IWM", "SPY"]
    assert all(a["reason"] == "symbol_ownership_drift" for a in locked)
    # One reconcile per transition into a larger locked set; the global
    # reconcile-pending latch (which holds every symbol) is never set.
    assert runtime._schedule_reconcile.call_count == 2
    assert not hasattr(runtime, "_reconcile_pending_reason")


def test_flag_on_unlocks_automatically_when_broker_and_ledger_agree(alerts_log) -> None:
    runtime = _runtime(flag=True)
    _drive_account_loop(runtime, [frozenset({"IWM"}), frozenset()])
    assert runtime._symbol_drift_lock_map() == {}
    assert runtime._symbol_locked_for_order("IWM", Market.EQUITY) is False
    recovered = [a for a in alerts_log if a["kind"] == "reconciliation_recovered"]
    assert [a["symbol"] for a in recovered] == ["IWM"]
    assert recovered[0]["reason"] == "symbol_ownership_drift_cleared"


def test_flag_on_drift_without_symbol_still_halts(alerts_log) -> None:
    runtime = _runtime(flag=True)
    _drive_account_loop(runtime, [OwnershipDriftError("no symbol attached")])
    runtime._halt_for_ownership_drift.assert_called_once()
    assert runtime._symbol_drift_lock_map() == {}


def test_flag_off_order_path_is_the_broker_itself() -> None:
    runtime = _runtime(flag=False)
    assert runtime._portfolio_order_broker() is runtime.broker


def test_flag_default_is_off_and_literal_true_only() -> None:
    assert RuntimeConfig().per_symbol_drift_lock is False
    runtime = _runtime(flag=False)
    runtime.app_config = SimpleNamespace(runtime=SimpleNamespace(per_symbol_drift_lock="yes"))
    assert runtime._per_symbol_drift_lock_enabled() is False
