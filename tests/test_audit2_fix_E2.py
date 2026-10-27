"""Wave-2 fixes E2: crash-window recovery, web engine state and settings windows.

Covers R2-5-1, R2-5-2, R2-5-4, C2-6-1..4, C2-2-2, C2-2-7. Each test drives one
invariant with a small fake; nothing touches the network, credentials or the
production state paths.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.execution import alpaca_reconciliation as reconciliation
from parayabuck.portfolio import runtime_service
from parayabuck.web.managers import engine as engine_module
from parayabuck.web.managers.engine import EngineManager


def _manager(monkeypatch: pytest.MonkeyPatch) -> EngineManager:
    monkeypatch.setattr(engine_module, "_scan_tracked_secrets", lambda root: None)
    manager = EngineManager()
    manager.config_path = Path(__file__).resolve().parents[1] / "config/settings.yaml"
    monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))
    return manager


# ---------------------------------------------------------------------------
# R2-5-1: the startup reconcile may close a young unknown submitting row
# ---------------------------------------------------------------------------


def test_young_unknown_order_is_only_closed_inside_startup_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reconciliation, "emit_execution_alert", lambda *args, **kwargs: None)
    broker = SimpleNamespace(_client=SimpleNamespace(get_order_by_client_id=lambda *a, **k: None))
    service = reconciliation.ReconciliationService(broker)
    local = SimpleNamespace(
        status="submitting",
        last_updated_at=datetime.now(UTC) - timedelta(seconds=30),
        broker_order_id="",
    )
    calls: list[tuple[Any, ...]] = []

    def callback(*args: Any, **kwargs: Any) -> None:
        calls.append(args)

    def lookup(client_id: str, broker_id: str) -> None:
        return None

    service._cancel_unknown_portfolio_order(local, "cid-1", lookup, callback)
    assert calls == []  # periodic reconcile keeps the age gate

    with reconciliation.startup_reconcile_scope():
        service._cancel_unknown_portfolio_order(local, "cid-1", lookup, callback)
    assert len(calls) == 1
    assert calls[0][0] == "cid-1"

    # The scope ends with the startup reconcile.
    calls.clear()
    service._cancel_unknown_portfolio_order(local, "cid-2", lookup, callback)
    assert calls == []


# ---------------------------------------------------------------------------
# R2-5-2: a re-plan SELL is recorded as sell_pending before it is sent
# ---------------------------------------------------------------------------


def test_replan_sell_flips_phase_before_the_post(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    sell = SimpleNamespace(symbol="QLD", client_order_id="cid-sell", side="sell", quantity=1.0)
    runtime = object.__new__(runtime_service.PortfolioRuntime)
    runtime.account_id = "acct"
    runtime.session_id = "sess"
    runtime.repository = SimpleNamespace(
        update_rebalance_execution=lambda decision_id, phase, **kwargs: events.append(f"phase:{phase}")
    )
    runtime._market_name = lambda: "equity"  # type: ignore[method-assign]
    runtime._validated_batch = lambda orders, rules: tuple(orders)  # type: ignore[method-assign]

    def submit(orders: Any, rules: Any, submit_gate: Any = None) -> tuple[Any, ...]:
        events.append("submit")
        return tuple(orders)

    runtime._submit_orders = submit  # type: ignore[method-assign]
    monkeypatch.setattr(runtime_service, "_emit_rebalance_phase_safe", lambda *a, **k: None)

    risk = SimpleNamespace(target=None)
    result = runtime._submit_replan_sells("dec-1", risk, (sell,), {})  # type: ignore[arg-type]

    assert events == [f"phase:{runtime_service.REBALANCE_PHASE_SELL_PENDING}", "submit"]
    assert result.status == "replan_sell_submitted"


# ---------------------------------------------------------------------------
# R2-5-4: a stale persisted high-water is lowered to the active epoch's peak
# ---------------------------------------------------------------------------


class _HighWaterRepo:
    def __init__(self, equity: float) -> None:
        self.equity = equity
        self.resets: list[tuple[str, float]] = []

    def high_water(self, account_id: str, *, epoch: int | None = None) -> float:
        return self.equity

    def reset_high_water(self, account_id: str, equity: float, *, epoch: int | None = None) -> None:
        assert epoch == 3
        self.resets.append((account_id, equity))
        self.equity = equity


def test_stale_high_water_above_active_peak_is_healed() -> None:
    repo = _HighWaterRepo(120_000.0)
    runtime_module._heal_persisted_high_water(repo, "acct", 100_000.0, 3)
    assert repo.resets == [("acct", 100_000.0)]


def test_high_water_at_or_below_active_peak_is_left_alone() -> None:
    repo = _HighWaterRepo(95_000.0)
    runtime_module._heal_persisted_high_water(repo, "acct", 100_000.0, 3)
    assert repo.resets == []


# ---------------------------------------------------------------------------
# C2-6-1: a refused auto-start retry settles the state it left behind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left_state", "expected_state"),
    [("starting", "error"), ("stopping", "stopped")],
)
def test_refused_auto_retry_settles_left_state(
    monkeypatch: pytest.MonkeyPatch, left_state: str, expected_state: str
) -> None:
    manager = _manager(monkeypatch)
    manager.engine_state = left_state

    def refused(*, auto_start: bool = False) -> None:
        raise ValueError("Operator halt is set")

    monkeypatch.setattr(manager, "start_engine", refused)
    with pytest.raises(ValueError, match="Operator halt"):
        asyncio.run(manager.auto_start_engine())

    assert manager.engine_state == expected_state
    assert manager.runtime is None
    assert manager._retry_task is None


# ---------------------------------------------------------------------------
# C2-6-2: a generic startup failure during a stop reads the live 'stopping'
# ---------------------------------------------------------------------------


class _StopDuringWarmupRuntime:
    def __init__(self, manager: EngineManager) -> None:
        self._manager = manager
        self.broker = SimpleNamespace(account_id="acct")

    def start(self) -> None:
        # A stop lands after the runtime exists but before the start publishes it.
        self._manager.engine_state = "stopping"
        raise RuntimeError("warmup exploded")

    def stop(self) -> bool:
        return True

    def health(self) -> Any:
        return SimpleNamespace(state="stopped")


def test_generic_start_failure_during_stop_ends_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(
        runtime_module,
        "EngineRuntime",
        lambda **kwargs: _StopDuringWarmupRuntime(manager),
    )
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    monkeypatch.setattr(manager, "_operator_halt_refusal", lambda: None)

    with pytest.raises(RuntimeError, match="warmup exploded"):
        manager.start_engine()

    assert manager.engine_state == "stopped"
    assert manager.last_engine_error is None


# ---------------------------------------------------------------------------
# C2-6-3: an operator Stop during a settings save survives the settings restart
# ---------------------------------------------------------------------------


def test_operator_stop_during_settings_window_refuses_the_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    monkeypatch.setattr(manager, "_operator_halt_refusal", lambda: None)
    monkeypatch.setattr(manager, "_write_operator_stop_marker", lambda: None)

    manager.begin_settings_write()
    manager.stop_engine(operator_request=True)
    manager.end_settings_write()

    with pytest.raises(ValueError, match="durdurma istendi"):
        manager.start_engine(settings_restart=True)
    assert manager._settings_stop_requested is False
    assert manager.runtime is None


def test_operator_stop_during_settings_window_never_refuses_a_manual_start(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    monkeypatch.setattr(manager, "_operator_halt_refusal", lambda: None)
    monkeypatch.setattr(manager, "_write_operator_stop_marker", lambda: None)

    manager.begin_settings_write()
    manager.stop_engine(operator_request=True)
    manager.end_settings_write()

    try:
        manager.start_engine()
    except Exception as exc:  # the fixture has no broker; only the refusal matters
        assert "durdurma istendi" not in str(exc)
    assert manager._settings_stop_requested is False


def test_settings_restart_stop_does_not_block_its_own_start(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    manager.begin_settings_write()
    manager.stop_engine()  # the settings save's own stop, not an operator request
    manager.end_settings_write()
    assert manager._settings_stop_requested is False


# ---------------------------------------------------------------------------
# C2-6-4: a status probe does not overwrite the manager's stop states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("held_state", ["stopping", "stop_failed"])
def test_status_probe_keeps_manager_stop_states(monkeypatch: pytest.MonkeyPatch, held_state: str) -> None:
    manager = _manager(monkeypatch)
    manager.runtime = SimpleNamespace(health=lambda: SimpleNamespace(state="running"))
    manager.engine_state = held_state
    assert manager.current_engine_state() == held_state


def test_status_probe_still_reports_runtime_health(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    manager.runtime = SimpleNamespace(health=lambda: SimpleNamespace(state="running"))
    manager.engine_state = "starting"
    assert manager.current_engine_state() == "running"


# ---------------------------------------------------------------------------
# C2-2-2: a poll that finished on a dropped broker does not publish its account
# ---------------------------------------------------------------------------


def test_refresh_does_not_publish_a_dropped_brokers_account(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_read_paper_mode", lambda: True)

    class _OldBroker:
        account_suffix = ""

        def account(self) -> SimpleNamespace:
            # reset_broker or start swaps the broker while this poll is in flight.
            manager.broker = SimpleNamespace(name="replacement")
            return SimpleNamespace(
                equity=1.0,
                cash=1.0,
                daily_pnl=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                fees=0.0,
                peak_equity=1.0,
                positions=[],
            )

    manager.broker = _OldBroker()
    snapshot = manager._refresh_account_snapshot()

    assert manager.last_account is None
    assert snapshot["source"] == "unavailable"


def test_refresh_publishes_when_the_broker_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    monkeypatch.setattr(manager, "_read_paper_mode", lambda: True)

    class _Broker:
        account_suffix = ""

        def account(self) -> SimpleNamespace:
            return SimpleNamespace(
                equity=2.0,
                cash=2.0,
                daily_pnl=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                fees=0.0,
                peak_equity=2.0,
                positions=[],
            )

    manager.broker = _Broker()
    snapshot = manager._refresh_account_snapshot()

    assert manager.last_account is not None
    assert snapshot["source"] == "broker"


# ---------------------------------------------------------------------------
# C2-2-7: live arming is invalidated under the state lock
# ---------------------------------------------------------------------------


def test_invalidate_live_arming_waits_for_the_state_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    done = threading.Event()

    def invalidate() -> None:
        manager.invalidate_live_arming()
        done.set()

    worker = threading.Thread(target=invalidate)
    with manager._state_lock:
        worker.start()
        # While the lock is held, the invalidation must not complete.
        assert not done.wait(0.2)
    assert done.wait(5)
    worker.join(5)
    assert manager._live_armed is False


# ---------------------------------------------------------------------------
# C2-2-3: a snapshot reused across a state-changing fill is not persisted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "expect_persisted"),
    [("fill", False), ("accepted", True)],
)
def test_reused_snapshot_is_persisted_only_across_non_fill_events(
    monkeypatch: pytest.MonkeyPatch, event: str, expect_persisted: bool
) -> None:
    import asyncio

    from parayabuck.core.models import AccountSnapshot, Market
    from parayabuck.execution.alpaca_updates import TradeUpdate
    from parayabuck.runtime import EngineRuntime

    stale = AccountSnapshot(100_000, 100_000, 100_000, 0.0)

    class _Repository:
        def has_order(self, client_order_id: str) -> bool:
            return True

        def apply_trade_update(self, **_kwargs: object) -> None:
            return None

    class _Broker:
        account_id = "acct"
        account_calls = 0

        def snapshot_if_fresh(self, _max_age: float) -> AccountSnapshot:
            return stale

        def account(self) -> AccountSnapshot:
            self.account_calls += 1
            return stale

    persisted: list[object] = []
    broker = _Broker()
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._portfolio_repository = _Repository()
    runtime.broker = broker
    now = datetime.now(UTC)
    runtime._last_account_at = now
    runtime._last_account_snapshot = stale
    runtime._last_account_snapshot_at = now
    runtime.config = SimpleNamespace(account_poll_interval_seconds=15.0, maximum_account_age_for_entry_seconds=30.0)
    runtime._persist_risk_state = lambda account, *a, **k: persisted.append(account)
    runtime._advance_portfolio_rebalance = lambda _market, **_kwargs: None
    update = TradeUpdate(
        event=event,
        client_order_id="buy-1",
        broker_order_id="broker-buy-1",
        symbol="AAPL",
        market=Market.EQUITY,
        filled_qty=1.0,
        average_fill_price=100.0,
        timestamp=now,
        fee=0.0,
        activity_id=f"activity-{event}",
    )
    asyncio.run(runtime._handle_trade_update(update))

    assert bool(persisted) is expect_persisted
