"""Regression tests for the 2026-10-06 engine-core audit fixes (EngineRuntime)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.alpaca_reconciliation import ReconciliationResultError
from parayabuck.runtime import EngineRuntime
from parayabuck.runtime_health import WorkerHealth


def _account(equity: float = 100_000.0) -> AccountSnapshot:
    return AccountSnapshot(cash=equity, equity=equity, peak_equity=equity, daily_pnl=0.0)


def test_account_loop_refreshes_readiness_once_startup_committed() -> None:
    """The decision loop only wakes for the daily bar, so the periodic
    readiness refresh (warming_up -> running, warmup retry) rides the account
    poll instead of the deleted 5m pipeline."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    runtime._workers = {"pb-account": WorkerHealth(now)}
    runtime._running = True
    runtime._startup_committed = True
    runtime._warming_up = False
    runtime.config = SimpleNamespace(account_poll_interval_seconds=0.0)
    runtime.broker = SimpleNamespace(account=_account, account_id="acct")
    runtime._portfolio_repository = SimpleNamespace(update_high_water=lambda *_a: None)
    runtime._persist_risk_state = lambda _account: None
    runtime._maybe_run_intent_watchdog = lambda: None
    refreshed: list[bool] = []
    runtime._refresh_readiness = lambda: refreshed.append(True)

    def stop_after_one_poll(_seconds: float) -> bool:
        runtime._running = False
        return True

    runtime._stop_event = SimpleNamespace(wait=stop_after_one_poll)

    runtime._account_loop()

    assert refreshed == [True]


def test_account_loop_skips_readiness_before_startup_commits() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    runtime._workers = {"pb-account": WorkerHealth(now)}
    runtime._running = True
    runtime._startup_committed = False
    runtime._warming_up = False
    runtime.config = SimpleNamespace(account_poll_interval_seconds=0.0)
    runtime.broker = SimpleNamespace(account=_account, account_id="acct")
    runtime._portfolio_repository = SimpleNamespace(update_high_water=lambda *_a: None)
    runtime._persist_risk_state = lambda _account: None
    runtime._maybe_run_intent_watchdog = lambda: None
    refreshed: list[bool] = []
    runtime._refresh_readiness = lambda: refreshed.append(True)
    runtime._stop_event = SimpleNamespace(wait=lambda _s: setattr(runtime, "_running", False) or True)
    runtime._state_lock = threading.RLock()

    runtime._account_loop()

    assert refreshed == []
    assert runtime._workers["pb-account"].alive is False


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    sink: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **fields: sink.append((kind, fields)))
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda _kind: None)
    return sink


def _reconcile_runtime(error: BaseException, incidents: list[str]) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.config = SimpleNamespace(reconcile_failure_threshold=2)
    runtime._state_lock = threading.RLock()
    runtime._reconcile_lock = threading.Lock()
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False

    def _reconcile() -> Any:
        raise error

    runtime.broker = SimpleNamespace(account_id="acct", reconcile=_reconcile)
    runtime.ledger = SimpleNamespace(save_safety_incident=lambda _account, reason: incidents.append(reason))
    return runtime


def test_persistent_network_error_in_fill_backfill_escalates_to_the_breaker(
    emitted: list[tuple[str, dict[str, Any]]],
) -> None:
    """E2: the chained ReconciliationResultError (FILL-activity backfill hit a
    network error) counts toward the threshold and then raises the breaker,
    persists the incident and alerts — like a bare RequestException."""
    try:
        raise ConnectionError("activities endpoint unreachable")
    except ConnectionError as cause:
        try:
            raise ReconciliationResultError("FILL activity backfill incomplete") from cause
        except ReconciliationResultError as chained:
            error = chained
    incidents: list[str] = []
    runtime = _reconcile_runtime(error, incidents)

    runtime._reconcile_safely()
    assert runtime._reconcile_failed is True
    assert runtime._circuit_breaker_reason is None
    assert incidents == [] and emitted == []

    runtime._reconcile_safely()
    assert runtime._circuit_breaker_reason == "reconciliation_failed"
    assert incidents == ["reconciliation_failed"]
    assert [kind for kind, _ in emitted] == ["reconciliation_failure"]


def _warmup_runtime(seeded: list[list[Any]]) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._strategy_timeframes = {"1d"}
    runtime.aggregators = {}
    runtime._portfolio_lock = threading.RLock()
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(timeframe="1d", minimum_history_bars=150),
            seed=seeded.append,
        ),
    )
    return runtime


class _DownProvider:
    def get_bars_batch(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("data API unreachable")


def test_first_warmup_reraises_a_network_failure_for_transient_startup() -> None:
    """E5: start() can only discard-and-retry a provider outage if the first
    warmup lets the network-class failure out."""
    runtime = _warmup_runtime([])

    with pytest.raises(ConnectionError):
        runtime._warmup_symbols(_DownProvider(), ["QQQ", "QLD"], retry=False)


def test_warmup_retry_still_skips_a_network_failure() -> None:
    seeded: list[list[Any]] = []
    runtime = _warmup_runtime(seeded)

    runtime._warmup_symbols(_DownProvider(), ["QQQ", "QLD"], retry=True)

    assert seeded == []


def test_signal_basis_latch_fails_closed_when_the_verifier_raises() -> None:
    """E18: an unexpected verifier exception never escapes onto the decision
    thread (where it would halt the runtime); entries fail closed."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._symbols_by_market = {Market.EQUITY: {"QQQ", "QLD"}}
    runtime._equity_signal_basis_verified = lambda _timestamp=None: False

    def _boom(required_through: datetime | None = None) -> bool:
        raise RuntimeError("history client closed")

    runtime.verify_equity_signal_basis = _boom

    assert runtime._equity_signal_basis_latch(datetime.now(UTC)) == "signal_adjustment_unverified"


_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}


def _reset_runtime(cleared: list[tuple[str, bool]]) -> EngineRuntime:
    """A halted runtime whose reset_safety_lock verification passes."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "ambiguous_order_state"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._risk_state_lock = threading.Lock()
    runtime._state_lock = threading.RLock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._portfolio_runtimes = ()
    account = _account(10_000)
    runtime.broker = SimpleNamespace(
        account_id="acct", reconcile=lambda: dict(_CLEAN_RECONCILIATION), account=lambda: account
    )
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (),
        clear_safety_incident=lambda _account, reason: cleared.append((reason, runtime._risk_state_lock.locked())),
    )
    runtime._persist_risk_state = lambda _account: None
    return runtime


def test_reset_clears_breaker_and_incident_under_the_risk_state_lock() -> None:
    """E15: the account loop's _persist_risk_state re-saves the breaker reason
    it read under the risk-state lock; clearing outside that lock could
    resurrect the incident row the operator just cleared."""
    cleared: list[tuple[str, bool]] = []
    runtime = _reset_runtime(cleared)

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    reset_rows = {"ambiguous_order_state", *runtime_module._RESTART_REARMED_REASONS}
    assert ("ambiguous_order_state", True) in cleared
    assert all(locked for reason, locked in cleared if reason in reset_rows)


def test_reset_retries_a_startup_catch_up_the_halt_blocked() -> None:
    """E9: a catch-up blocked at startup by the breaker re-runs after reset."""
    runtime = _reset_runtime([])
    calls: list[dict[str, Any]] = []
    blocked = SimpleNamespace(market=Market.EQUITY, startup_catch_up_status="blocked:ambiguous_order_state")
    runtime._portfolio_runtimes = (blocked,)
    runtime._resume_startup_rebalances = lambda **_kwargs: None
    runtime._run_startup_catch_up = lambda **kwargs: calls.append(kwargs)

    runtime.reset_safety_lock()

    assert len(calls) == 1
    assert calls[0]["blocked_only"] is True


def test_reset_does_not_rerun_a_completed_startup_catch_up() -> None:
    runtime = _reset_runtime([])
    calls: list[dict[str, Any]] = []
    runtime._portfolio_runtimes = (SimpleNamespace(market=Market.EQUITY, startup_catch_up_status="submitted"),)
    runtime._resume_startup_rebalances = lambda **_kwargs: None
    runtime._run_startup_catch_up = lambda **kwargs: calls.append(kwargs)

    runtime.reset_safety_lock()

    assert calls == []


def test_startup_catch_up_records_a_breaker_block_for_the_reset_retry() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._warming_up = False
    runtime._circuit_breaker_reason = "ownership_drift"
    portfolio_runtime = SimpleNamespace(market=Market.EQUITY, startup_catch_up_status=None)
    runtime._portfolio_runtimes = (portfolio_runtime,)

    runtime._run_startup_catch_up(account=_account())

    assert portfolio_runtime.startup_catch_up_status == "blocked:ownership_drift"


def test_bot_tradable_symbols_are_the_strategy_hold_symbol_plus_pins() -> None:
    """E7: VT35 observes SPY/QQQ/QLD but only holds QLD, so an operator's SPY
    or QQQ position is foreign inventory, not ownership drift."""
    from parayabuck.portfolio import product_strategy_from

    runtime = EngineRuntime.__new__(EngineRuntime)
    strategy = product_strategy_from("equity_leveraged_trend_v1", {}, timeframe="1d")
    runtime._strategy_specs = ((strategy, ("SPY", "QQQ", "QLD"), 0.5),)
    runtime._managed_pin_symbols = {"TQQQ"}

    assert runtime._bot_tradable_symbols() == {"QLD", "TQQQ"}


def test_bot_tradable_symbols_fall_back_to_the_universe_without_a_hold_symbol() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._strategy_specs = ((SimpleNamespace(), ("SPY", "QQQ"), 0.5),)
    runtime._managed_pin_symbols = set()

    assert runtime._bot_tradable_symbols() == {"SPY", "QQQ"}


def _latch_runtime(saved: list[str], cleared: list[str]) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = SimpleNamespace(
        save_safety_incident=lambda _account, reason: saved.append(reason),
        clear_safety_incident=lambda _account, reason: cleared.append(reason),
    )
    runtime.app_config = SimpleNamespace(risk=SimpleNamespace(daily_loss_limit_pct=0.20, max_drawdown_pct=0.30))
    runtime._entry_risk_latch = {Market.EQUITY: None}
    return runtime


def test_daily_loss_latch_is_measured_against_day_start_equity(emitted: list[tuple[str, dict[str, Any]]]) -> None:
    """E13: an 18% loss of day-start equity (21.95% of current equity) does
    not trip a 20% limit; a 20% loss does."""
    runtime = _latch_runtime([], [])

    runtime._evaluate_entry_risk_latch(AccountSnapshot(cash=82.0, equity=82.0, peak_equity=100.0, daily_pnl=-18.0))
    assert runtime._entry_risk_latch[Market.EQUITY] is None

    runtime._evaluate_entry_risk_latch(AccountSnapshot(cash=80.0, equity=80.0, peak_equity=100.0, daily_pnl=-20.0))
    assert runtime._entry_risk_latch[Market.EQUITY] == "daily_loss_limit"


def test_drawdown_incident_is_never_cleared_while_daily_loss_also_breaches(
    emitted: list[tuple[str, dict[str, Any]]],
) -> None:
    """E12: with both limits breached, drawdown wins and its persisted
    incident is saved, never cleared-then-resaved."""
    saved: list[str] = []
    cleared: list[str] = []
    runtime = _latch_runtime(saved, cleared)

    runtime._evaluate_entry_risk_latch(AccountSnapshot(cash=60.0, equity=60.0, peak_equity=100.0, daily_pnl=-40.0))

    assert runtime._entry_risk_latch[Market.EQUITY] == "portfolio_drawdown_halt"
    assert saved == ["portfolio_drawdown_halt"]
    assert cleared == []


def test_deferred_buys_respect_the_entry_risk_latch_and_market_halts() -> None:
    """E14: a deferred BUY of a two-phase rebalance is an entry, so a latched
    or halted market keeps it deferred."""
    flags: list[bool] = []

    def advance(**kwargs: Any) -> None:
        flags.append(kwargs["submit_deferred_buys"])

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._portfolio_lock = threading.RLock()
    runtime._portfolio_runtimes = (SimpleNamespace(market=Market.EQUITY, advance_rebalance=advance),)

    runtime._entry_risk_latch = {Market.EQUITY: "daily_loss_limit"}
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime._advance_portfolio_rebalance(Market.EQUITY)
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._market_entry_halts = {Market.EQUITY: "rebalance_reconciliation_required"}
    runtime._advance_portfolio_rebalance(Market.EQUITY)
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime._advance_portfolio_rebalance(Market.EQUITY)

    assert flags == [False, False, True]
