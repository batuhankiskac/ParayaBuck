"""Alert-emission semantics for the observability layer.

Regression guards for the transition-based alert rules: "recovered" events
only follow a prior failure, unexpected-stop alerts only follow a completed
start, risk-latch alerts fire on transitions only, unknown kinds are
rejected (logged, never raised), and the per-kind counters are accurate. All
tests are offline (shell instances).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import parayabuck.monitoring.alerts as alerts
from parayabuck.core.models import Market
from parayabuck.runtime import EngineRuntime


def _delivered(kind: str) -> int:
    stats = alerts.alert_stats()
    delivered = stats["delivered"]
    assert isinstance(delivered, dict)
    return int(delivered.get(kind, 0))


@pytest.fixture(autouse=True)
def _reset_alert_state() -> None:
    alerts.reset_for_tests()
    # Transition semantics are the subject here, not throttling; disable the
    # per-kind window so consecutive transitions are all delivered.
    alerts.configure_alerting(
        enabled=True,
        webhook_url="",
        events=sorted(alerts.ALERT_KINDS),
        throttle_seconds=0.0,
    )


def _shell() -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._universe_retry_at = {}
    runtime._universe_retry_attempt = {}
    runtime._scanner_failed = {}
    runtime._entry_risk_latch = {Market.EQUITY.value: None, Market.CRYPTO.value: None}
    runtime._state_lock = threading.RLock()
    runtime._portfolio_lock = threading.RLock()
    runtime._ingestion_lock = threading.Lock()
    runtime._state = "running"
    return runtime


def test_scanner_recovered_is_transition_only() -> None:
    runtime = _shell()

    # A first healthy success is NOT a recovery.
    runtime._mark_universe_refresh_outcome(Market.CRYPTO, ok=True)
    assert _delivered("scanner_recovered") == 0
    assert _delivered("scanner_refresh_failure") == 0

    # A failure emits scanner_refresh_failure.
    runtime._mark_universe_refresh_outcome(Market.CRYPTO, ok=False)
    assert _delivered("scanner_refresh_failure") == 1

    # The next success IS the recovery.
    runtime._mark_universe_refresh_outcome(Market.CRYPTO, ok=True)
    assert _delivered("scanner_recovered") == 1

    # A second consecutive success is NOT another recovery.
    runtime._mark_universe_refresh_outcome(Market.CRYPTO, ok=True)
    assert _delivered("scanner_recovered") == 1


def test_scanner_failure_flag_is_per_market() -> None:
    runtime = _shell()
    runtime._mark_universe_refresh_outcome(Market.CRYPTO, ok=False)
    # Equity was never failed: its next success is not a recovery.
    runtime._mark_universe_refresh_outcome(Market.EQUITY, ok=True)
    assert _delivered("scanner_recovered") == 0
    assert _delivered("scanner_refresh_failure") == 1


def test_stop_unexpected_requires_completed_start() -> None:
    runtime = _shell()
    # start() never completed: no session row was recorded.
    runtime._runtime_session_id = None
    runtime._circuit_breaker_reason = None
    runtime._running = False
    runtime._storage_closed = True
    runtime._state = "error"
    runtime.stop()
    assert _delivered("runtime_stopped_unexpected") == 0


def test_unknown_alert_kind_rejected_without_raising() -> None:
    # Never raises; unknown kinds are logged and dropped.
    alerts.emit("not_a_real_event", message="x")
    assert _delivered("not_a_real_event") == 0
    # No delivered count for a kind that was never emitted.
    assert "not_a_real_event" not in (alerts.alert_stats()["delivered"] or {})


def test_risk_latch_alerts_on_transition_only() -> None:
    runtime = _shell()
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = SimpleNamespace(
        save_safety_incident=lambda *a, **k: None,
        clear_safety_incident=lambda *a, **k: None,
    )
    runtime.app_config = SimpleNamespace(
        risk=SimpleNamespace(daily_loss_limit_pct=0.005, max_drawdown_pct=0.05)
    )
    account = SimpleNamespace(equity=100_000.0, daily_pnl=-600.0, drawdown_pct=0.01)

    # First breach: alert fires once.
    runtime._evaluate_entry_risk_latch(account)  # type: ignore[arg-type]
    assert _delivered("risk_incident") == 1

    # Persistent breach on the next poll: no repeat alert.
    runtime._evaluate_entry_risk_latch(account)  # type: ignore[arg-type]
    assert _delivered("risk_incident") == 1

    # Recovery clears the latch; the next breach is a new transition.
    healthy = SimpleNamespace(equity=100_000.0, daily_pnl=0.0, drawdown_pct=0.01)
    runtime._evaluate_entry_risk_latch(healthy)  # type: ignore[arg-type]
    runtime._evaluate_entry_risk_latch(account)  # type: ignore[arg-type]
    assert _delivered("risk_incident") == 2


def test_drawdown_halt_alert_transition() -> None:
    runtime = _shell()
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = SimpleNamespace(
        save_safety_incident=lambda *a, **k: None,
        clear_safety_incident=lambda *a, **k: None,
    )
    runtime.app_config = SimpleNamespace(
        risk=SimpleNamespace(daily_loss_limit_pct=0.005, max_drawdown_pct=0.05)
    )
    breaching = SimpleNamespace(equity=100_000.0, daily_pnl=0.0, drawdown_pct=0.06)
    runtime._evaluate_entry_risk_latch(breaching)  # type: ignore[arg-type]
    assert _delivered("drawdown_halt") == 1
    runtime._evaluate_entry_risk_latch(breaching)  # type: ignore[arg-type]
    assert _delivered("drawdown_halt") == 1
