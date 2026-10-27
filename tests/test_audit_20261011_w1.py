"""W1 audit regression tests (2026-10-11 findings).

Deterministic, single-invariant, tmp_path-only. Each test fails before its
fix and passes after.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import EngineConfig, SignalCrossCheckConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.runtime import EngineRuntime, _SessionCloseFallback
from parayabuck.runtime_events import _Event, _EventQueue

SESSION_LABEL = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
SESSION_CLOSE = datetime(2026, 9, 29, 16, 0, tzinfo=NEW_YORK).astimezone(UTC)
SYMBOLS = ("SPY", "QQQ", "QLD")


def _bars(symbols: tuple[str, ...] = SYMBOLS, close: float = 100.0) -> list[Bar]:
    return [
        Bar(SESSION_LABEL, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY) for symbol in symbols
    ]


def _engine() -> EngineRuntime:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.config = EngineConfig()
    engine.app_config = SimpleNamespace(signal_cross_check=SignalCrossCheckConfig(enabled=False))
    engine.broker = SimpleNamespace(account_id="acct", account=lambda: AccountSnapshot(100_000, 100_000, 100_000, 0.0))
    engine._warming_up = False
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._state = "running"
    engine._running = True
    engine._state_lock = threading.RLock()
    engine._stop_event = threading.Event()
    engine._portfolio_lock = threading.RLock()
    engine._threads_lock = threading.Lock()
    engine._entry_risk_latch = {Market.EQUITY: None}
    engine._market_entry_halts = {Market.EQUITY: None}
    engine._portfolio_runtimes = ()
    engine._workers = {}
    engine._deferred_portfolio_batches = {}
    engine._session_close_fallback = None
    engine._entry_readiness_reasons = lambda *_args, **_kwargs: ()
    engine._last_portfolio_status = {}
    engine._not_ready_symbols = set()
    return engine


@pytest.fixture(autouse=True)
def _silence_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda _kind, **_kw: None)
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda _event: None)


# A2: partial SIP fallback keeps the missing symbols armed.
def test_partial_sip_fallback_keeps_missing_symbols_armed() -> None:
    engine = _engine()
    engine._session_close_fallback = _SessionCloseFallback(
        session=date(2026, 9, 29),
        symbols=("QQQ", "SPY"),
        retry_at=SESSION_CLOSE,
    )
    emitted: list[Bar] = []
    engine._emit_finalized_bar = lambda _timeframe, bar: emitted.append(bar)  # type: ignore[method-assign]

    class _Historical:
        def get_session_daily_bars(self, symbols: list[str], start: Any, end: Any, **_kw: Any) -> dict[str, Bar]:
            assert symbols == ["QQQ", "SPY"]
            return {"QLD": _bars(("QLD",))[0], "QQQ": _bars(("QQQ",))[0]}

    engine._historical = _Historical()
    engine._run_session_close_fallback(SESSION_CLOSE + timedelta(minutes=20))

    assert [bar.symbol for bar in emitted] == ["QQQ"]
    pending = engine._session_close_fallback
    assert pending is not None and pending.symbols == ("SPY",)


# R3C-02: deferred re-mark uses signed quantities (a foreign short is a liability).
def test_account_at_decision_close_uses_signed_quantities() -> None:
    engine = _engine()
    account = AccountSnapshot(
        100_000,
        100_500,
        100_500,
        0.0,
        positions=(
            PositionSnapshot("QLD", 10.0, 100.0, 100.0, Market.EQUITY, managed_qty=10.0),
            PositionSnapshot("XYZ", -10.0, 50.0, 50.0, Market.EQUITY),
        ),
    )
    remarked = engine._account_at_decision_close(
        account, [Bar(SESSION_LABEL, "QLD", "1d", 100, 100, 100, 100, 1.0, Market.EQUITY)]
    )
    assert remarked.equity == pytest.approx(100_500.0)


# R2A1: stop() on a full queue never evicts a decision bar.
def test_stop_never_evicts_a_decision_bar_on_a_full_queue() -> None:
    engine = _engine()
    engine._events = _EventQueue(2)
    stamp = datetime.now(UTC)
    engine._events.put_market_event(_Event(_bars(("SPY",))[0], stamp))
    engine._events.put_market_event(_Event(_bars(("QQQ",))[0], stamp))
    engine._running = True
    engine._snapshot_writer_started = False
    engine._stream_workers_launched = False
    engine._threads = []
    engine._storage_closed = True

    assert engine.stop() is True
    assert engine._events.qsize() == 2  # both bars kept; the sentinel was dropped


# R2A3: coalescing is per-market.
def test_coalescing_key_includes_market() -> None:
    stamp = datetime.now(UTC)
    q = _EventQueue(1)
    equity = Bar(stamp, "X", "1d", 1, 1, 1, 1, 1.0, Market.EQUITY)
    assert q.put_market_event(_Event(equity, stamp)) == "added"
    crypto = Bar(stamp, "X", "1d", 2, 2, 2, 2, 1.0, Market.CRYPTO)
    assert q.put_market_event(_Event(crypto, stamp)) == "overflow"
    assert tuple(q._items)[0] is not None and tuple(q._items)[0].bar.close == 1.0  # type: ignore[union-attr]


# R2A2: a batch parked past its retry window expires instead of re-deciding.
def test_due_runner_expires_a_batch_parked_past_its_window() -> None:
    engine = _engine()
    engine._deferred_portfolio_batches[(Market.EQUITY, SESSION_LABEL)] = (_bars(), SESSION_CLOSE)
    handled: list[list[Bar]] = []
    engine._process_portfolio_timestamp = lambda batch, **_kw: handled.append(batch) or True  # type: ignore[method-assign]
    engine._run_due_deferred_portfolio_batches(now=SESSION_CLOSE + timedelta(minutes=60))
    assert handled == []
    assert engine._deferred_portfolio_batches == {}


# R5C-2: risk-state rows are keyed by the New York session date.
def test_persist_risk_state_keys_by_new_york_date(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from parayabuck.execution.ledger import ExecutionLedger

    engine = _engine()
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    engine.ledger = ledger
    engine._risk_state_lock = threading.Lock()
    engine._entry_risk_latch = {}
    engine._evaluate_entry_risk_latch = lambda _account: None  # type: ignore[method-assign]

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            moment = datetime(2026, 12, 25, 1, 30, tzinfo=UTC)  # 20:30 ET Dec 24
            return moment.astimezone(tz) if tz is not None else moment

    monkeypatch.setattr(runtime_module, "datetime", _Frozen)
    engine._persist_risk_state(AccountSnapshot(100_000, 100_000, 100_000, 0.0))
    saved = ledger.load_risk_state("acct", date(2026, 12, 24))
    assert saved is not None
    assert ledger.load_risk_state("acct", date(2026, 12, 25)) is None
    ledger.close()


# R5E-2: stop() stops the writer before publishing the stopped snapshot.
def test_stop_orders_writer_stop_before_stopped_write(tmp_path: Any) -> None:
    from parayabuck.monitoring.snapshot import SnapshotWriter

    engine = _engine()
    writer = SnapshotWriter(path=tmp_path / "snap.json", interval_seconds=30.0, provider=lambda: None)
    writer.start()
    calls: list[str] = []
    orig_stop, orig_stopped = writer.stop, writer.write_stopped
    writer.stop = lambda **_kw: (calls.append("stop"), orig_stop(**_kw))[1]  # type: ignore[method-assign]
    writer.write_stopped = lambda: (calls.append("stopped"), orig_stopped())[1]  # type: ignore[method-assign]
    engine._snapshot_writer = writer
    engine._snapshot_writer_started = True
    engine._stream_workers_launched = False
    engine._threads = []
    engine._storage_closed = True
    try:
        assert engine.stop() is True
    finally:
        orig_stop()
    assert calls[:2] == ["stop", "stopped"]


# R3B2: transient catch-up statuses are retried by the operator reset.
def test_reset_retries_transient_catch_up_statuses() -> None:
    from parayabuck.runtime import _catch_up_blocked

    assert _catch_up_blocked(SimpleNamespace(startup_catch_up_status="warming_up")) is True
    assert _catch_up_blocked(SimpleNamespace(startup_catch_up_status="incomplete_strategy_batch")) is True
    assert _catch_up_blocked(SimpleNamespace(startup_catch_up_status="blocked:halted")) is True
    assert _catch_up_blocked(SimpleNamespace(startup_catch_up_status="submitted")) is False


# R3B3 / audit-2 Q1A-2: a transient catch-up status is final for the restart.
# The catch-up and the intent watchdog own warming_up/incomplete outcomes; the
# removed recovery helper only re-ran the same tails and duplicated the
# receipt, daily summary and no-trade event for the undecided session.
def test_transient_startup_catch_up_records_one_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    decided: list[list[Bar]] = []
    receipts: list[str] = []
    summaries: list[str] = []

    class _Portfolio:
        market = Market.EQUITY
        history = {symbol: _bars((symbol,)) for symbol in SYMBOLS}

        def _required_symbols(self) -> tuple[str, ...]:
            return SYMBOLS

        def _session_decided(self, _timestamp: datetime) -> bool:
            return False

        def process_closed_bars(self, bars: list[Bar], **_kw: Any) -> SimpleNamespace:
            decided.append(list(bars))
            return SimpleNamespace(
                status="warming_up", decision_id=None, target=None, orders=(), submitted_order_ids=()
            )

        def startup_catch_up(self, **_kw: Any) -> SimpleNamespace:
            self.startup_catch_up_status = "warming_up"
            return SimpleNamespace(
                status="warming_up", decision_id=None, target=None, orders=(), submitted_order_ids=()
            )

    engine._portfolio_runtimes = (_Portfolio(),)
    engine._record_paper_decision_receipt = lambda _rt, result, _acct: receipts.append(result.status)  # type: ignore[method-assign]
    engine._emit_equity_daily_summary = lambda _rt, result, _acct: summaries.append(result.status)  # type: ignore[method-assign]

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            moment = SESSION_CLOSE + timedelta(minutes=5)
            return moment.astimezone(tz) if tz is not None else moment

    monkeypatch.setattr(runtime_module, "datetime", _Clock)
    engine._run_startup_catch_up()
    assert decided == []
    assert receipts == ["warming_up"]
    assert summaries == ["warming_up"]


# R2A5: concurrent refresh-vs-read of the not-ready set never raises.
def test_concurrent_readiness_access_never_raises() -> None:
    engine = _engine()
    engine._not_ready_symbols = {"A", "B", "C"}
    engine._portfolio_runtimes = (
        SimpleNamespace(
            symbols=("A", "B", "C"),
            history={"A": [], "B": [], "C": []},
            market=Market.EQUITY,
            strategy=SimpleNamespace(timeframe="1d", minimum_history_bars=1),
        ),
    )
    errors: list[BaseException] = []
    stop = threading.Event()
    started = threading.Barrier(3)

    def refresh() -> None:
        try:
            started.wait(timeout=5.0)
            while not stop.is_set():
                engine._refresh_readiness()
        except BaseException as exc:  # pragma: no cover - the invariant is no raise
            errors.append(exc)

    def read() -> None:
        try:
            started.wait(timeout=5.0)
            for _ in range(200):
                engine._has_ready_symbols()
                engine._not_ready_snapshot()
                engine._build_market_health({Market.EQUITY: True}, {Market.EQUITY: {"A"}}, {}, datetime.now(UTC), 45.0)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            stop.set()

    threads = [threading.Thread(target=refresh), threading.Thread(target=read)]
    for thread in threads:
        thread.start()
    started.wait(timeout=5.0)
    for thread in threads:
        thread.join(timeout=10.0)
    stop.set()
    assert errors == []
    assert not any(thread.is_alive() for thread in threads)


# R5A-1: scheduling refuses to spawn once the engine is stopping.
def test_schedule_refuses_to_spawn_while_stopping() -> None:
    engine = _engine()
    engine._running = False
    engine._reconcile_thread = None
    engine._warmup_retry_thread = None
    engine._schedule_reconcile()
    engine._schedule_warmup_retry()
    assert engine._reconcile_thread is None
    assert engine._warmup_retry_thread is None


# R5A-4: the readiness gate survives a concurrent worker-key insert.
def test_gate_survives_a_concurrent_worker_key_insert() -> None:
    from parayabuck.runtime_health import WorkerHealth

    engine = _engine()
    engine._workers = {"pb-decisions": WorkerHealth(datetime.now(UTC), alive=True)}
    engine.config = EngineConfig()
    engine._last_account_at = datetime.now(UTC)
    engine.health = lambda: SimpleNamespace(queue_age_seconds=0.0)  # type: ignore[method-assign]
    errors: list[BaseException] = []

    def gate() -> None:
        try:
            for _ in range(2000):
                engine._entry_readiness_reasons(Market.EQUITY)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def register() -> None:
        for _ in range(200):
            engine._workers["pb-signal-adjustment"] = WorkerHealth(datetime.now(UTC), alive=True)
            engine._workers.pop("pb-signal-adjustment", None)

    threads = [threading.Thread(target=gate), threading.Thread(target=register)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15.0)
    assert errors == []


# R3A3: fill-driven rebalance advances while warming up must not submit deferred BUYs.
def test_warmup_advance_withholds_deferred_buys() -> None:
    engine = _engine()
    engine._warming_up = True
    seen: dict[str, Any] = {}

    class _Portfolio:
        market = Market.EQUITY

        def advance_rebalance(self, **kwargs: Any) -> SimpleNamespace:
            seen.update(kwargs)
            return SimpleNamespace(status="buy_ready")

    engine._portfolio_runtimes = (_Portfolio(),)
    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert seen.get("submit_deferred_buys") is False


# R3D4: operator-actionable epoch refusals are ValueError (web maps to 400).
def test_risk_epoch_refusals_are_value_errors() -> None:
    engine = _engine()
    engine._running = False
    with pytest.raises(ValueError, match="not running"):
        engine.start_new_risk_epoch()
    engine._running = True
    engine._circuit_breaker_reason = "ownership_drift"
    with pytest.raises(ValueError, match="circuit breaker"):
        engine.start_new_risk_epoch()


# R2A5/lease compat: a double overriding _fresh_account_snapshot gets an unvalidated lease.
def test_lease_compat_for_doubles_without_cache_state() -> None:
    engine = _engine()
    account = AccountSnapshot(10_000, 10_000, 10_000, 0.0)
    engine._fresh_account_snapshot = lambda: account  # type: ignore[method-assign]
    lease = engine._fresh_account_snapshot_lease()
    assert lease.snapshot is account and lease.generation is None
    assert engine._account_snapshot_lease_is_current(lease) is True


# R4D6: production fetch paths call the real methods directly.
def test_production_fetch_paths_use_real_methods() -> None:
    engine = _engine()
    engine._historical = SimpleNamespace(get_session_daily_bars=lambda *a, **k: {})
    engine._session_close_fallback = _SessionCloseFallback(
        session=date(2026, 9, 29), symbols=("QQQ",), retry_at=SESSION_CLOSE
    )
    engine._emit_finalized_bar = lambda *_a, **_k: None  # type: ignore[method-assign]
    engine._run_session_close_fallback(SESSION_CLOSE + timedelta(minutes=20))
    # Empty provider response: still armed (attempts=1 of 4), never cleared.
    assert engine._session_close_fallback is not None and engine._session_close_fallback.attempts == 1
