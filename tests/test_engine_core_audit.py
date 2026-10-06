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
    runtime._workers = {"pb-account": WorkerHealth(now, now)}
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
    runtime._workers = {"pb-account": WorkerHealth(now, now)}
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
