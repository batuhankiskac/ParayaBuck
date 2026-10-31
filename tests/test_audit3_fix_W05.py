"""Audit wave 3 fixes W05: halts, safety-lock reset, restart and crash windows.

Covers R3-1-1..5, R3-4-1..2, R3-5-1..2, R3-2-4, S3-1-2, C3-5-3, P3-2-1..2 and
D3-5-2/7. Every test works on ``tmp_path`` ledgers and in-memory stubs with
fixed timestamps; nothing touches the network, credentials, data/ or real
runtime state.
"""

from __future__ import annotations

import ast
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import Config, EngineConfig, EquityUniverseConfig, RuntimeConfig, UniverseConfig
from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.alpaca_models import OwnershipDriftError
from parayabuck.execution.alpaca_reconciliation import (
    RECONCILIATION_CATEGORIES,
    RECONCILIATION_DRIFT_CATEGORIES,
)
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.runtime import EngineRuntime
from parayabuck.runtime_health import WorkerHealth

_CLEAN = {category: [] for category in RECONCILIATION_CATEGORIES}
_ACCOUNT = AccountSnapshot(cash=10_000.0, equity=10_000.0, peak_equity=10_000.0, daily_pnl=0.0)
_FIXED = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **fields: events.append((kind, fields)))
    monkeypatch.setattr(
        runtime_module,
        "runtime_metrics_store",
        SimpleNamespace(record_event=lambda *_args, **_kwargs: None),
    )
    return events


def _shell(tmp_path: Path) -> tuple[EngineRuntime, ExecutionLedger]:
    """A runtime shell with the attributes the halt, reset and batch paths read."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: dict(_CLEAN), account=lambda: _ACCOUNT)
    runtime.ledger = ledger
    runtime.app_config = SimpleNamespace(
        risk=SimpleNamespace(max_drawdown_pct=0.5, daily_loss_limit_pct=0.5),
        runtime=SimpleNamespace(decision_db_path=str(tmp_path / "decisions.sqlite3")),
    )
    runtime.config = EngineConfig()
    runtime._state_lock = threading.RLock()
    runtime._risk_state_lock = threading.Lock()
    runtime._reconcile_lock = threading.Lock()
    runtime._portfolio_lock = threading.RLock()
    runtime._circuit_breaker_reason = None
    runtime._halt_reasons = []
    runtime._state = "running"
    runtime._running = True
    runtime._warming_up = False
    runtime._workers = {}
    runtime._threads = []
    runtime._threads_lock = threading.Lock()
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._portfolio_runtimes = ()
    runtime._market_entry_halts = {}
    runtime._entry_risk_latch = {}
    runtime._drawdown_halt_restored = False
    runtime._reconcile_failed = False
    runtime._reconcile_pending_reason = None
    runtime._reconcile_pending_seq = 0
    runtime._deferred_portfolio_batches = {}
    runtime._publish_account_snapshot = lambda account: None  # type: ignore[method-assign]
    runtime._resume_startup_rebalances = lambda **_kwargs: None  # type: ignore[method-assign]
    runtime._persist_risk_state = lambda _account: None  # type: ignore[method-assign]
    return runtime, ledger


def _incident_reasons(ledger: ExecutionLedger) -> set[str]:
    return {incident.reason for incident in ledger.load_safety_incidents("acct")}


# --------------------------------------------------------------------------- #
# R3-1-1: a halt raised between the reset's guard and its release is kept
# --------------------------------------------------------------------------- #


def test_reset_keeps_a_rearmed_halt_raised_during_the_unlocked_clear(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._raise_halt("ownership_drift")
    original_clear = runtime._clear_reconciliation_incidents

    def clear_while_another_thread_halts() -> None:
        # Raised after the guard, before the release: the old code released it.
        runtime._raise_halt("final_market_event_queue_overflow")
        original_clear()

    runtime._clear_reconciliation_incidents = clear_while_another_thread_halts  # type: ignore[method-assign]

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason == "final_market_event_queue_overflow"
    assert runtime._state == "halted"
    assert runtime._halt_reasons == ["final_market_event_queue_overflow"]
    assert _incident_reasons(ledger) == {"final_market_event_queue_overflow"}
    ledger.close()


# --------------------------------------------------------------------------- #
# R3-1-5: a reset that re-halts after the release alerts like the catch-up retry
# --------------------------------------------------------------------------- #


def test_reset_failure_after_release_emits_a_circuit_breaker_alert(
    tmp_path: Path, _no_alerts: list[tuple[str, dict[str, Any]]]
) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._raise_halt("ownership_drift")

    def fail_after_release(_account: AccountSnapshot) -> None:
        raise RuntimeError("risk state write failed")

    runtime._persist_risk_state = fail_after_release  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="serbest bırakıldıktan sonra sıfırlama başarısız"):
        runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason == "portfolio_runtime_failure"
    breaker_alerts = [fields for kind, fields in _no_alerts if kind == "circuit_breaker"]
    assert any(fields.get("reason") == "portfolio_runtime_failure" for fields in breaker_alerts)
    ledger.close()


# --------------------------------------------------------------------------- #
# R3-1-4: market halts still in force are listed in the reset alert
# --------------------------------------------------------------------------- #


def test_reset_alert_retained_field_lists_market_halts_in_force(
    tmp_path: Path, _no_alerts: list[tuple[str, dict[str, Any]]]
) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._raise_halt("ownership_drift")
    # No verification path exists for this halt, so it is retained.
    runtime._market_entry_halts = {Market.EQUITY: "custom_unverified_halt"}

    runtime.reset_safety_lock()

    reset_alerts = [fields for kind, fields in _no_alerts if kind == "safety_lock_reset"]
    assert len(reset_alerts) == 1
    assert "equity:custom_unverified_halt" in reset_alerts[0]["retained"]
    ledger.close()


# --------------------------------------------------------------------------- #
# R3-1-3: a live bar after a reset respawn clears a matching restart-limit halt
# --------------------------------------------------------------------------- #


def test_live_bar_clears_restart_limit_halt_whose_respawn_cleared_last_error(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._workers = {"pb-stocks": WorkerHealth(_FIXED, last_error=None, restart_count=0)}
    runtime._market_entry_halts = {Market.EQUITY: "pb-stocks_restart_limit"}

    runtime._mark_market_stream_recovered(Market.EQUITY)

    assert runtime._market_entry_halts[Market.EQUITY] is None
    ledger.close()


def test_live_bar_keeps_an_unrelated_market_halt(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._workers = {"pb-stocks": WorkerHealth(_FIXED, last_error=None)}
    runtime._market_entry_halts = {Market.EQUITY: "rebalance_reconciliation_required"}

    runtime._mark_market_stream_recovered(Market.EQUITY)

    assert runtime._market_entry_halts[Market.EQUITY] == "rebalance_reconciliation_required"
    ledger.close()


# --------------------------------------------------------------------------- #
# R3-1-2: an ownership-drift raised in the decision worker is recorded
# --------------------------------------------------------------------------- #


def test_ownership_drift_in_the_decision_loop_is_recorded_and_persisted(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._workers = {"pb-decisions": WorkerHealth(_FIXED)}

    class _DriftingEvents:
        def get(self, timeout: float) -> None:
            raise OwnershipDriftError("broker holds fewer shares than the repository claims")

        def empty(self) -> bool:
            return True

    runtime._events = _DriftingEvents()  # type: ignore[assignment]

    runtime._decision_loop()

    assert runtime._circuit_breaker_reason == "ownership_drift"
    assert "ownership_drift" in runtime._halt_reasons
    assert runtime._state == "halted"
    assert _incident_reasons(ledger) == {"ownership_drift"}
    ledger.close()


# --------------------------------------------------------------------------- #
# R3-4-1 (owner decision): a decision-worker crash survives a restart
# --------------------------------------------------------------------------- #


def test_decision_worker_crash_halt_survives_a_restart_until_reset(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "decision_worker_crashed")
    restarted = EngineRuntime.__new__(EngineRuntime)
    restarted.broker = SimpleNamespace(account_id="acct")
    restarted.ledger = ledger
    restarted.app_config = Config()
    restarted._risk_state_lock = threading.Lock()
    restarted._state_lock = threading.RLock()
    restarted._circuit_breaker_reason = None
    restarted._entry_risk_latch = {Market.EQUITY: None}

    restarted._restore_persistent_state()

    assert restarted._circuit_breaker_reason == "decision_worker_crashed"
    assert _incident_reasons(ledger) == {"decision_worker_crashed"}
    assert "decision_worker_crashed" not in runtime_module._RESTART_REARMED_REASONS
    ledger.close()


# --------------------------------------------------------------------------- #
# R3-4-2: a long batch refreshes the decision worker heartbeat
# --------------------------------------------------------------------------- #


def test_beat_decision_worker_refreshes_a_stale_heartbeat(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    stale = datetime(2000, 1, 1, tzinfo=UTC)
    worker = WorkerHealth(stale)
    runtime._workers = {"pb-decisions": worker}

    runtime._beat_decision_worker()

    assert worker.last_heartbeat > stale
    ledger.close()


def test_beat_decision_worker_is_a_no_op_without_a_worker(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._workers = {}

    runtime._beat_decision_worker()

    assert runtime._workers == {}
    ledger.close()


# --------------------------------------------------------------------------- #
# P3-2-1: a hard halt raised during the lease fetch defers the session
# --------------------------------------------------------------------------- #


def test_hard_halt_during_lease_fetch_defers_the_batch(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    portfolio_runtime = SimpleNamespace(
        market=Market.CRYPTO,
        strategy=SimpleNamespace(timeframe="1Day", name="stub"),
        symbols={"BTC"},
        startup_catch_up_status=None,
        process_closed_bars=lambda *_args, **_kwargs: pytest.fail("a halted batch must not be decided"),
    )
    runtime._portfolio_runtimes = (portfolio_runtime,)
    bars = [SimpleNamespace(market=Market.CRYPTO, timeframe="1Day", symbol="BTC", timestamp=_FIXED)]
    deferred: list[str] = []

    def lease_fetch_raises_a_hard_halt() -> SimpleNamespace:
        runtime._circuit_breaker_reason = "portfolio_runtime_failure"
        return SimpleNamespace(snapshot=_ACCOUNT)

    runtime._entry_readiness_reasons = lambda market, **_kwargs: ()  # type: ignore[method-assign]
    runtime._fresh_account_snapshot_lease = lease_fetch_raises_a_hard_halt  # type: ignore[method-assign]
    runtime._account_snapshot_lease_is_current = lambda _lease: True  # type: ignore[method-assign]
    runtime._claim_account_snapshot_lease = lambda _lease: True  # type: ignore[method-assign]
    runtime._release_account_snapshot_lease = lambda _lease: None  # type: ignore[method-assign]
    runtime._defer_portfolio_batch = (  # type: ignore[method-assign]
        lambda _bars, _market, reason: deferred.append(reason) or True
    )

    runtime._process_portfolio_timestamp(bars)

    assert deferred == ["halted:portfolio_runtime_failure"]
    ledger.close()


# --------------------------------------------------------------------------- #
# P3-2-2: a clean reconcile re-runs a blocked startup catch-up
# --------------------------------------------------------------------------- #


def test_clean_reconcile_reruns_a_catch_up_blocked_by_the_soft_latch(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._portfolio_runtimes = (
        SimpleNamespace(market=Market.CRYPTO, startup_catch_up_status="blocked:reconcile_pending"),
    )
    calls: list[dict[str, Any]] = []
    runtime._run_startup_catch_up = lambda **kwargs: calls.append(kwargs)  # type: ignore[method-assign]
    runtime._advance_portfolio_rebalance = lambda _market: None  # type: ignore[method-assign]

    runtime._reconcile_safely()

    assert calls == [{"blocked_only": True}]
    assert runtime._circuit_breaker_reason is None
    ledger.close()


def test_failed_catch_up_retry_after_a_clean_reconcile_halts_the_runtime(
    tmp_path: Path, _no_alerts: list[tuple[str, dict[str, Any]]]
) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._portfolio_runtimes = (
        SimpleNamespace(market=Market.CRYPTO, startup_catch_up_status="blocked:reconcile_pending"),
    )

    def retry_raises(**_kwargs: Any) -> None:
        raise RuntimeError("provider unavailable")

    runtime._run_startup_catch_up = retry_raises  # type: ignore[method-assign]
    runtime._advance_portfolio_rebalance = lambda _market: None  # type: ignore[method-assign]

    runtime._reconcile_safely()

    assert runtime._circuit_breaker_reason == "portfolio_runtime_failure"
    assert any(kind == "circuit_breaker" for kind, _fields in _no_alerts)
    ledger.close()


# --------------------------------------------------------------------------- #
# S3-1-2 / R3-2-4: the LIVE approval is consumed before the first order step
# --------------------------------------------------------------------------- #


def test_live_approval_is_consumed_before_the_first_order_submitting_startup_step() -> None:
    # A full LIVE start needs a verified proof, real streams and threads, so the
    # ordering is checked on the start routine's source: the consumption record
    # must precede every step that can submit an order.
    tree = ast.parse(Path(runtime_module.__file__).read_text(encoding="utf-8"))
    start_impl = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_start_impl"
    )
    first_line: dict[str, int] = {}
    for node in ast.walk(start_impl):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in {"record_approval_consumed", "_resume_startup_rebalances", "_run_startup_catch_up"}:
                first_line.setdefault(name, node.lineno)
    assert set(first_line) == {"record_approval_consumed", "_resume_startup_rebalances", "_run_startup_catch_up"}
    assert first_line["record_approval_consumed"] < first_line["_resume_startup_rebalances"]
    assert first_line["_resume_startup_rebalances"] < first_line["_run_startup_catch_up"]


# --------------------------------------------------------------------------- #
# C3-5-3: a failed portfolio repository build releases the ledger's store
# --------------------------------------------------------------------------- #


def test_failed_portfolio_repository_build_releases_the_ledger_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[ExecutionLedger] = []
    real_ledger = runtime_module.ExecutionLedger

    def recording_ledger(*args: Any, **kwargs: Any) -> ExecutionLedger:
        ledger = real_ledger(*args, **kwargs)
        created.append(ledger)
        return ledger

    class _RefusedRepository:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("migration refused")

    class _FakeBroker:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.account_id = "acct"

    monkeypatch.setattr(runtime_module, "ExecutionLedger", recording_ledger)
    monkeypatch.setattr(runtime_module, "PortfolioStateRepository", _RefusedRepository)
    monkeypatch.setattr(runtime_module, "AlpacaBroker", _FakeBroker)
    config = Config(
        universe=UniverseConfig(equity=EquityUniverseConfig(symbols=("SPY",))),
        engine=EngineConfig(),
        runtime=RuntimeConfig(decision_db_path=str(tmp_path / "runtime.sqlite3")),
    )

    with pytest.raises(RuntimeError, match="migration refused"):
        EngineRuntime("key", "secret", config)

    assert len(created) == 1
    assert created[0]._store._closed is True


# --------------------------------------------------------------------------- #
# R3-5-1: a risk-epoch reset that crashed after its sidecar commit is finished
# --------------------------------------------------------------------------- #


def test_interrupted_epoch_reset_is_finished_at_startup(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    repository = runtime_module.PortfolioStateRepository(str(tmp_path / "ledger.sqlite3"), store=ledger._store)
    repository.update_high_water("acct", 10_000.0, epoch=1)
    ledger.save_safety_incident("acct", "portfolio_drawdown_halt")
    peak = SimpleNamespace(peak_epoch=2, peak_equity=9_000.0)
    runtime._portfolio_repository = repository
    runtime.broker = SimpleNamespace(account_id="acct", _get_peak_service=lambda: peak)

    runtime._finish_interrupted_risk_epoch_reset("acct")

    assert repository.high_water("acct", epoch=2) == 9_000.0
    assert "portfolio_drawdown_halt" not in _incident_reasons(ledger)
    ledger.close()


def test_completed_epoch_reset_is_left_alone_at_startup(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    repository = runtime_module.PortfolioStateRepository(str(tmp_path / "ledger.sqlite3"), store=ledger._store)
    repository.update_high_water("acct", 9_000.0, epoch=2)
    ledger.save_safety_incident("acct", "portfolio_drawdown_halt")
    runtime._portfolio_repository = repository
    runtime.broker = SimpleNamespace(
        account_id="acct", _get_peak_service=lambda: SimpleNamespace(peak_epoch=2, peak_equity=9_000.0)
    )

    runtime._finish_interrupted_risk_epoch_reset("acct")

    assert repository.high_water("acct", epoch=2) == 9_000.0
    assert "portfolio_drawdown_halt" in _incident_reasons(ledger)
    ledger.close()


# --------------------------------------------------------------------------- #
# R3-5-2: a pre-reset snapshot does not re-save the cleared drawdown incident
# --------------------------------------------------------------------------- #


def _drawdown_account(peak_epoch: int) -> AccountSnapshot:
    return AccountSnapshot(cash=8_000.0, equity=8_000.0, peak_equity=10_000.0, daily_pnl=0.0, peak_epoch=peak_epoch)


def test_pre_reset_snapshot_does_not_resave_the_drawdown_incident(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime.app_config = SimpleNamespace(
        risk=SimpleNamespace(max_drawdown_pct=0.1, daily_loss_limit_pct=0.5),
        runtime=runtime.app_config.runtime,
    )
    runtime.broker = SimpleNamespace(account_id="acct", _get_peak_service=lambda: SimpleNamespace(peak_epoch=2))

    runtime._evaluate_entry_risk_latch(_drawdown_account(peak_epoch=1))

    assert "portfolio_drawdown_halt" not in _incident_reasons(ledger)
    ledger.close()


def test_current_epoch_snapshot_still_saves_the_drawdown_incident(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime.app_config = SimpleNamespace(
        risk=SimpleNamespace(max_drawdown_pct=0.1, daily_loss_limit_pct=0.5),
        runtime=runtime.app_config.runtime,
    )
    runtime.broker = SimpleNamespace(account_id="acct", _get_peak_service=lambda: SimpleNamespace(peak_epoch=2))

    runtime._evaluate_entry_risk_latch(_drawdown_account(peak_epoch=2))

    assert "portfolio_drawdown_halt" in _incident_reasons(ledger)
    ledger.close()


# --------------------------------------------------------------------------- #
# D3-5-2 / D3-5-7: refusals name the cause and the next step
# --------------------------------------------------------------------------- #


def test_reset_refusal_without_a_breaker_says_there_is_nothing_to_reset(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._state = "degraded"

    with pytest.raises(ValueError, match="devre kesici yok"):
        runtime.reset_safety_lock()
    ledger.close()


def test_reset_refusal_for_a_restart_limit_says_to_stop_then_start(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._raise_halt("pb-decisions_restart_limit")

    with pytest.raises(ValueError, match="Motoru durdurun, sonra yeniden başlatın"):
        runtime.reset_safety_lock()
    ledger.close()


def test_ownership_drift_refusal_points_to_rebase_ownership(tmp_path: Path) -> None:
    runtime, ledger = _shell(tmp_path)
    runtime._raise_halt("ownership_drift")
    drift = dict(_CLEAN)
    drift[sorted(RECONCILIATION_DRIFT_CATEGORIES)[0]] = ["SPY"]
    runtime.broker.reconcile = lambda: dict(drift)  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="rebase-ownership"):
        runtime.reset_safety_lock()
    ledger.close()
