"""Wave 2a: a session-close decision must not be lost to a transient condition.

Covers the F01 deferral chain (breaker, readiness gate, snapshot lease,
transient account fetch, bounded deadline), the consecutive-reconcile-failure
threshold and the full 16:00 flush -> decision -> deferral -> retry chain.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import EngineConfig, SignalCrossCheckConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.markets.nyse_calendar import session_close_utc
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.runtime import EngineRuntime, _AccountSnapshotLease

# 2026-09-29 is a Tuesday; the session-close daily bar is labelled 09:30 ET.
SESSION_LABEL = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
SESSION_CLOSE = datetime(2026, 9, 29, 16, 0, tzinfo=NEW_YORK).astimezone(UTC)
SYMBOLS = ("SPY", "QQQ", "QLD")


def _bars(close: float = 100.0) -> list[Bar]:
    return [
        Bar(SESSION_LABEL, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY) for symbol in SYMBOLS
    ]


def _result(status: str = "submitted") -> SimpleNamespace:
    return SimpleNamespace(status=status, decision_id="dec-1", target=None, orders=(), submitted_order_ids=())


def _portfolio_runtime(process: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        market=Market.EQUITY,
        symbols=SYMBOLS,
        strategy=SimpleNamespace(timeframe="1d", name="vt35"),
        process_closed_bars=process if process is not None else (lambda bars, **kwargs: _result()),
    )


def _engine(runtimes: tuple[Any, ...], *, config: EngineConfig | None = None) -> EngineRuntime:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.config = config or EngineConfig()
    engine.app_config = SimpleNamespace(signal_cross_check=SignalCrossCheckConfig(enabled=False))
    engine.broker = SimpleNamespace(account_id="acct")
    engine._warming_up = False
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._state = "running"
    engine._state_lock = threading.RLock()
    engine._portfolio_lock = threading.RLock()
    engine._entry_risk_latch = {Market.EQUITY: None}
    engine._market_entry_halts = {Market.EQUITY: None}
    engine._portfolio_runtimes = runtimes
    engine._workers = {}
    engine._last_account_at = SESSION_CLOSE
    engine._account_snapshot_lock = threading.Lock()
    engine._account_snapshot_generation = 0
    engine._last_account_snapshot = None
    engine._last_account_snapshot_at = None
    engine._account_snapshot_active_leases = set()
    engine._account_snapshot_invalidation_pending = False
    # generation=None: the "no cache metadata" lease, valid for every check.
    engine._fresh_account_snapshot_lease = lambda **_kwargs: _AccountSnapshotLease(
        AccountSnapshot(100_000, 100_000, 100_000, 0.0), None
    )
    engine._signal_cross_check_step = lambda *_args, **_kwargs: False
    engine._deferred_portfolio_batches = {}
    engine._entry_readiness_reasons = lambda *_args, **_kwargs: ()
    engine._last_portfolio_status = {}
    return engine


@pytest.fixture(autouse=True)
def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        runtime_module.alerts,
        "emit",
        lambda kind, **fields: events.append((kind, fields)),
    )
    return events


class _Clock:
    """Fake wall clock for ``parayabuck.runtime.datetime.now``."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def freeze(self, moment: datetime) -> None:
        self.now = moment

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = self

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return clock.now.astimezone(tz) if tz is not None else clock.now

        monkeypatch.setattr(runtime_module, "datetime", _Frozen)


# --------------------------------------------------------------------------- #
# F01: a recoverable gate re-queues the batch instead of dropping the session
# --------------------------------------------------------------------------- #


def test_breaker_requeues_the_session_close_batch_instead_of_losing_it(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    """F01: one periodic-reconcile error at 16:00:02 used to drop the batch;
    it is now parked and re-decided once the reconcile recovers."""
    engine = _engine((_portfolio_runtime(),))
    engine._circuit_breaker_reason = "reconciliation_failed"
    clock = _Clock(SESSION_CLOSE + timedelta(seconds=2))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False

    key = (Market.EQUITY, SESSION_LABEL)
    bars, retry_at = engine._deferred_portfolio_batches[key]
    assert bars == _bars()
    assert retry_at == SESSION_CLOSE + timedelta(seconds=62)
    assert [(kind, fields["reason"]) for kind, fields in _capture_alerts] == [
        ("decision_deferred", "reconciliation_failed")
    ]


def test_reconcile_failure_alone_requeues_and_clears_without_an_operator_reset(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    engine = _engine((_portfolio_runtime(),))
    engine._reconcile_failed = True
    clock = _Clock(SESSION_CLOSE + timedelta(seconds=2))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False
    assert (Market.EQUITY, SESSION_LABEL) in engine._deferred_portfolio_batches

    # Reconcile recovers; past the SIP embargo the parked batch decides.
    engine._reconcile_failed = False
    clock.freeze(SESSION_CLOSE + timedelta(minutes=16))
    engine._run_due_deferred_portfolio_batches(now=clock.now)

    assert engine._deferred_portfolio_batches == {}


def test_readiness_gate_requeues_and_the_deferred_key_survives_the_retry(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    """A stale account snapshot at the close must not cost the session."""
    engine = _engine((_portfolio_runtime(),))
    engine._entry_readiness_reasons = lambda *_args, **_kwargs: ("account_snapshot_stale",)
    clock = _Clock(SESSION_CLOSE + timedelta(seconds=2))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False
    assert (Market.EQUITY, SESSION_LABEL) in engine._deferred_portfolio_batches

    # Still blocked on the retry: the key must NOT be popped.
    clock.freeze(SESSION_CLOSE + timedelta(seconds=62))
    engine._run_due_deferred_portfolio_batches(now=clock.now)
    assert (Market.EQUITY, SESSION_LABEL) in engine._deferred_portfolio_batches

    # One alert per deferred session, not one per retry.
    assert [kind for kind, _ in _capture_alerts] == ["decision_deferred"]


def test_invalidated_snapshot_lease_requeues_rather_than_skipping_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine((_portfolio_runtime(),))
    engine._fresh_account_snapshot_lease = lambda **_kwargs: _AccountSnapshotLease(
        AccountSnapshot(100_000, 100_000, 100_000, 0.0), 7
    )
    engine._account_snapshot_lease_is_current = lambda _lease: False
    clock = _Clock(SESSION_CLOSE + timedelta(seconds=2))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False
    bars, _retry_at = engine._deferred_portfolio_batches[(Market.EQUITY, SESSION_LABEL)]
    assert bars == _bars()


def test_transient_account_fetch_at_decision_time_requeues_without_halting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F10: a broker timeout while sizing the decision is provider noise, not
    a portfolio_runtime_failure halt."""
    engine = _engine((_portfolio_runtime(),))

    def _timeout(**_kwargs: Any) -> Any:
        raise ConnectionError("broker read timed out")

    engine._fresh_account_snapshot_lease = _timeout
    clock = _Clock(SESSION_CLOSE + timedelta(seconds=2))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False

    assert engine._circuit_breaker_reason is None
    assert engine._state == "running"
    assert (Market.EQUITY, SESSION_LABEL) in engine._deferred_portfolio_batches


def test_retry_stops_at_the_bounded_deadline_and_alerts_once(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    """The bound sits inside the intent watchdog grace, so an unrecoverable
    gate yields a decision_deferred expiry, not a silent loss."""
    engine = _engine((_portfolio_runtime(),))
    engine._circuit_breaker_reason = "reconciliation_failed"
    clock = _Clock(SESSION_CLOSE + timedelta(minutes=41))
    clock.install(monkeypatch)

    # Expired: the batch is dropped (nothing left to retry) and the operator
    # is told, rather than the session disappearing silently.
    assert engine._process_portfolio_timestamp(_bars()) is True

    assert engine._deferred_portfolio_batches == {}
    assert [kind for kind, _ in _capture_alerts] == ["decision_deferred"]
    # The NYSE calendar hands back the close in UTC; the deadline is the
    # close + the retry window, compared as instants.
    assert _capture_alerts[0][1]["retry_window_ended"] == (SESSION_CLOSE + timedelta(minutes=40)).isoformat()


@pytest.mark.parametrize("reason", ["ownership_drift", "portfolio_runtime_failure", "decision_worker_crashed"])
def test_hard_halt_requeues_the_close_batch_for_the_retry_window(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]], reason: str
) -> None:
    """R9-2 / B-1: a hard halt re-queues the session-close batch inside the retry
    window, so an operator reset before the window closes still decides it.
    The first deferral alerts once, with the halt as its reason."""
    engine = _engine((_portfolio_runtime(),))
    engine._circuit_breaker_reason = reason
    clock = _Clock(SESSION_CLOSE + timedelta(seconds=2))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False

    assert list(engine._deferred_portfolio_batches) == [(Market.EQUITY, SESSION_LABEL)]
    assert [kind for kind, _ in _capture_alerts] == ["decision_deferred"]
    assert _capture_alerts[0][1]["reason"] == reason


def test_warmup_batches_are_requeued_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R8-7: a session-close batch that arrives during warmup is re-queued for
    the retry window instead of being dropped."""
    engine = _engine((_portfolio_runtime(),))
    engine._warming_up = True
    clock = _Clock(SESSION_CLOSE + timedelta(seconds=2))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False
    assert list(engine._deferred_portfolio_batches) == [(Market.EQUITY, SESSION_LABEL)]


def test_early_close_retry_deadline_is_the_actual_close_not_16_et(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    """A 13:00 ET session must stop retrying at 13:40 ET.

    Measured from a fixed 16:00 ET the window would run three hours past the
    close of an early-close session — into the next session, where a stale
    batch competes with the live one.
    """
    # 2026-11-27 (the Friday after Thanksgiving) closes at 13:00 ET.
    early_close_session = date(2026, 11, 27)
    early_close = session_close_utc(early_close_session)
    assert early_close is not None
    assert early_close.astimezone(NEW_YORK).hour == 13

    engine = _engine((_portfolio_runtime(),))
    engine._circuit_breaker_reason = "reconciliation_failed"
    # The daily decision bar is labelled 09:30 ET of the session date.
    bars = [
        Bar(
            datetime(2026, 11, 27, 9, 30, tzinfo=NEW_YORK).astimezone(UTC),
            symbol,
            "1d",
            100.0,
            100.0,
            100.0,
            100.0,
            1_000_000,
            Market.EQUITY,
        )
        for symbol in SYMBOLS
    ]
    clock = _Clock(early_close + timedelta(seconds=2))
    clock.install(monkeypatch)

    # Just after the real close the batch is still retried.
    assert engine._process_portfolio_timestamp(bars) is False
    assert (Market.EQUITY, bars[0].timestamp) in engine._deferred_portfolio_batches
    assert [kind for kind, _ in _capture_alerts] == ["decision_deferred"]

    # Past 13:40 ET the window has expired even though 16:40 ET has not
    # arrived: the session is lost and the operator is told. The retry loop
    # is what re-runs a parked batch and retires the key.
    clock.freeze(early_close + timedelta(minutes=41))
    engine._run_due_deferred_portfolio_batches()
    assert engine._deferred_portfolio_batches == {}
    expiry = [fields for kind, fields in _capture_alerts if fields.get("retry_window_ended")]
    assert len(expiry) == 1
    assert expiry[0]["retry_window_ended"] == (early_close + timedelta(minutes=40)).isoformat()


# --------------------------------------------------------------------------- #
# F01: the full 16:00 -> deferral -> retry chain, with a fake clock
# --------------------------------------------------------------------------- #


def test_full_close_to_deferral_to_retry_chain_decides_the_session(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    """16:16 the SIP cross-check defers; 16:17 a reconcile failure
    defers again; 16:18 everything is clear and the session is decided.

    The decision waits for the SIP embargo (16:15) before anything else runs,
    so the chain starts after it.
    """
    decided: list[list[Bar]] = []

    def _decide(bars: list[Bar], **kwargs: Any) -> SimpleNamespace:
        decided.append(bars)
        return _result()

    engine = _engine((_portfolio_runtime(_decide),))
    clock = _Clock(SESSION_CLOSE + timedelta(minutes=16))
    clock.install(monkeypatch)

    key = (Market.EQUITY, SESSION_LABEL)

    # 1. The second source has not published its daily bar yet.
    def _cross_check_defers(*_args: Any, **_kwargs: Any) -> None:
        # What the real coordinator does: park the batch with its own retry.
        engine._deferred_portfolio_batches[key] = (_bars(), clock.now + timedelta(seconds=60))
        _capture_alerts.append(("decision_deferred", {"reason": "signal_cross_check"}))
        return None

    engine._signal_cross_check_step = _cross_check_defers
    assert engine._process_portfolio_timestamp(_bars()) is False
    assert decided == []
    assert engine._deferred_portfolio_batches[key][1] == SESSION_CLOSE + timedelta(minutes=17)

    # 2. The second source answers, but a reconcile error is still active.
    engine._signal_cross_check_step = lambda *_args, **_kwargs: False
    engine._reconcile_failed = True
    clock.freeze(SESSION_CLOSE + timedelta(minutes=17))
    engine._run_due_deferred_portfolio_batches(now=clock.now)
    assert decided == []
    assert engine._deferred_portfolio_batches[key][1] == SESSION_CLOSE + timedelta(minutes=18)

    # 3. Reconciliation recovers; the retry runs the decision.
    engine._reconcile_failed = False
    clock.freeze(SESSION_CLOSE + timedelta(minutes=18))
    engine._run_due_deferred_portfolio_batches(now=clock.now)

    assert decided == [_bars()]
    assert engine._deferred_portfolio_batches == {}
    # Exactly one alert for the whole deferred session, not one per gate.
    assert [kind for kind, _ in _capture_alerts] == ["decision_deferred"]


# --------------------------------------------------------------------------- #
# F01: N consecutive reconcile failures before the breaker is raised
# --------------------------------------------------------------------------- #


def _flaky_reconcile_runtime(tmp_path: Any, threshold: int) -> EngineRuntime:
    from parayabuck.execution.ledger import ExecutionLedger

    class _Broker:
        account_id = "acct"

        def reconcile(self) -> dict[str, list[str]]:
            raise ConnectionError("broker timeout")

    engine = EngineRuntime.__new__(EngineRuntime)
    engine.broker = _Broker()
    engine.ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    engine.config = EngineConfig(reconcile_failure_threshold=threshold)
    engine._reconcile_lock = threading.Lock()
    engine._state_lock = threading.RLock()
    engine._last_reconciliation_at = None
    engine._last_account_at = None
    engine._generic_reconcile_failures = 0
    engine._transient_reconcile_failures = 0
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    return engine


def test_one_reconcile_timeout_neither_halts_nor_persists_an_incident(tmp_path: Any) -> None:
    engine = _flaky_reconcile_runtime(tmp_path, threshold=3)

    engine._reconcile_safely()

    assert engine._circuit_breaker_reason is None
    assert engine._reconcile_failed is True  # entries fail closed
    assert not engine.ledger.load_safety_incidents("acct")
    engine.ledger.close()


def test_the_nth_consecutive_reconcile_timeout_raises_the_breaker(tmp_path: Any) -> None:
    engine = _flaky_reconcile_runtime(tmp_path, threshold=3)

    for _ in range(2):
        engine._reconcile_safely()
    assert engine._circuit_breaker_reason is None

    engine._reconcile_safely()

    assert engine._circuit_breaker_reason == "reconciliation_failed"
    assert [i.reason for i in engine.ledger.load_safety_incidents("acct")] == ["reconciliation_failed"]
    engine.ledger.close()


def _runtime_with_repository(process: Any, *, decided: bool) -> SimpleNamespace:
    runtime = _portfolio_runtime(process)
    runtime.account_id = "acct"
    runtime.configuration_digest = "digest"
    runtime.repository = SimpleNamespace(has_decision=lambda *_args: decided)
    return runtime


def _broker_read_timeout(bars: list[Bar], **_kwargs: Any) -> Any:
    raise ConnectionError("asset trading rules read timed out")


def test_network_error_before_any_order_is_persisted_defers_instead_of_halting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F10/E8: a pre-submission broker read failing on the network, with no
    decision (hence no order) persisted for the session, re-queues the batch."""
    monkeypatch.setattr(runtime_module, "automate_incident_recovery", lambda *_a, **_k: None)
    engine = _engine((_runtime_with_repository(_broker_read_timeout, decided=False),))
    clock = _Clock(SESSION_CLOSE + timedelta(seconds=2))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False

    assert engine._circuit_breaker_reason is None
    assert engine._state == "running"
    assert (Market.EQUITY, SESSION_LABEL) in engine._deferred_portfolio_batches


def test_network_error_after_the_decision_was_persisted_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "automate_incident_recovery", lambda *_a, **_k: None)
    engine = _engine((_runtime_with_repository(_broker_read_timeout, decided=True),))
    clock = _Clock(SESSION_CLOSE + timedelta(minutes=16))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is True

    assert engine._circuit_breaker_reason == "portfolio_runtime_failure"
    assert engine._state == "halted"
    assert engine._deferred_portfolio_batches == {}


def test_domain_error_before_any_order_is_persisted_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "automate_incident_recovery", lambda *_a, **_k: None)

    def _invalid(bars: list[Bar], **_kwargs: Any) -> Any:
        raise ValueError("Strategy emitted a symbol outside the active universe")

    engine = _engine((_runtime_with_repository(_invalid, decided=False),))
    clock = _Clock(SESSION_CLOSE + timedelta(minutes=16))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is True

    assert engine._circuit_breaker_reason == "portfolio_runtime_failure"
    assert engine._deferred_portfolio_batches == {}


def test_portfolio_failure_never_masks_a_stronger_halt(monkeypatch: pytest.MonkeyPatch) -> None:
    """E16: a later halt writer keeps an existing protection_failure reason."""
    monkeypatch.setattr(runtime_module, "automate_incident_recovery", lambda *_a, **_k: None)
    engine = _engine(())

    engine._circuit_breaker_reason = "protection_failure"
    engine._raise_halt("portfolio_runtime_failure")
    assert engine._circuit_breaker_reason == "protection_failure"
    assert engine._state == "halted"

    engine._circuit_breaker_reason = "reconciliation_failed"
    engine._raise_halt("decision_worker_crashed")
    assert engine._circuit_breaker_reason == "decision_worker_crashed"
