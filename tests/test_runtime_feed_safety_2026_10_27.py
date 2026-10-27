"""Feed-gap and account-poll safety invariants of the runtime.

Each test pins one property of ``EngineRuntime``:

* a pb-trade-updates restart latches new entries and schedules a reconcile;
  only a reconcile started after the latch clears it, and a market stream
  restart does not;
* the latch gates buys through the rebalance advance path;
* open orders with a silent trade feed in the regular session latch and
  reconcile once per silent window;
* five consecutive failed account fetches trip the circuit breaker, and any
  successful fetch resets the count;
* the systemd notification sender writes to NOTIFY_SOCKET and is a no-op
  without it.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.runtime import EngineRuntime
from parayabuck.runtime_health import WorkerHealth
from parayabuck.utils import sd_notify

# A clean broker reconciliation: every category empty, no drift.
_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}

# Tuesday 2026-10-27 11:00 ET: inside the NYSE regular session.
_SESSION_MOMENT = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _quiet_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda *_a, **_k: None)


def _stream_runtime(name: str = "pb-trade-updates") -> tuple[EngineRuntime, WorkerHealth, list[int]]:
    """Bare runtime with one stream worker; ``schedule_calls`` records spawns."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    worker = WorkerHealth(last_heartbeat=datetime.now(UTC), alive=True)
    runtime._workers = {name: worker}
    runtime._running = True
    runtime._state = "running"
    runtime._circuit_breaker_reason = None
    runtime._state_lock = threading.RLock()
    runtime._stop_event = SimpleNamespace(wait=lambda _seconds: False)
    runtime._portfolio_runtimes = (SimpleNamespace(symbols=("AAPL",)),)
    runtime._not_ready_symbols = set()
    runtime._reconcile_pending_reason = None
    runtime._reconcile_pending_seq = 0
    schedule_calls: list[int] = []
    runtime._schedule_reconcile = lambda: schedule_calls.append(1)  # type: ignore[method-assign]
    return runtime, worker, schedule_calls


# --- Task 1: trade-updates restart latches and reconciles ---


def test_trade_update_restart_sets_latch_and_schedules_reconcile() -> None:
    """A failed pb-trade-updates run latches entries and schedules a reconcile
    before the retry; the latch is still set while the retry runs."""
    runtime, _worker, schedule_calls = _stream_runtime()
    seen_latch: list[str | None] = []

    def flaky() -> None:
        seen_latch.append(runtime._reconcile_pending_reason)
        if len(seen_latch) == 1:
            raise RuntimeError("simulated trade-update disconnect")
        runtime._running = False

    runtime._run_stream_worker("pb-trade-updates", flaky)

    assert seen_latch == [None, "trade_updates_restart"]
    assert runtime._reconcile_pending_reason == "trade_updates_restart"
    assert len(schedule_calls) == 1


def test_market_stream_restart_does_not_latch() -> None:
    """Market-only workers keep their own halt path; no trade-feed latch."""
    runtime, _worker, schedule_calls = _stream_runtime("pb-stocks")
    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated market-stream disconnect")
        runtime._running = False

    runtime._run_stream_worker("pb-stocks", flaky)

    assert runtime._reconcile_pending_reason is None
    assert schedule_calls == []


def test_latch_blocks_buys_and_clears_after_a_clean_reconcile() -> None:
    """While latched, the rebalance advance withholds deferred buys; the next
    clean reconcile clears the latch and buys are submitted again."""
    runtime, _worker, _schedule_calls = _stream_runtime()
    runtime._warming_up = False
    runtime._entry_risk_latch = {}
    runtime._market_entry_halts = {}
    runtime._equity_signal_basis_latch = lambda: None  # type: ignore[method-assign]
    runtime._portfolio_lock = threading.RLock()
    submitted: list[bool] = []

    class _Portfolio:
        market = Market.EQUITY
        symbols = ("AAPL",)
        strategy = SimpleNamespace(timeframe="1d")

        def advance_rebalance(self, *, submit_deferred_buys: bool, **_kw: object) -> None:
            submitted.append(submit_deferred_buys)

    runtime._portfolio_runtimes = (_Portfolio(),)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),
        _peak_epoch=0,
    )
    runtime.ledger = SimpleNamespace(clear_safety_incident=lambda *_a: None)
    runtime._reconcile_lock = threading.Lock()
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)

    runtime._set_reconcile_pending("trade_updates_restart")
    runtime._advance_portfolio_rebalance(Market.EQUITY, account=account)
    assert submitted == [False]

    runtime._reconcile_safely()

    assert runtime._reconcile_pending_reason is None
    # The clean reconciliation itself advances the rebalance once the latch
    # is cleared (F2-6-1), so the deferred BUYs go out without a later trigger.
    assert submitted == [False, True]
    runtime._advance_portfolio_rebalance(Market.EQUITY, account=account)
    assert submitted == [False, True, True]


def test_latch_set_during_a_running_scan_survives_that_scan() -> None:
    """A restart that lands while a reconcile is already running is not
    covered by that scan's broker read, so its clean result must not clear
    the latch; only a later scan can."""
    runtime, _worker, schedule_calls = _stream_runtime()
    runtime._reconcile_lock = threading.Lock()
    runtime._set_reconcile_pending("trade_updates_restart")
    schedule_calls.clear()

    def reconcile_with_mid_scan_restart() -> dict[str, list[str]]:
        runtime._set_reconcile_pending("trade_updates_restart")
        return dict(_CLEAN_RECONCILIATION)

    runtime.broker = SimpleNamespace(account_id="acct", reconcile=reconcile_with_mid_scan_restart, _peak_epoch=0)
    runtime.ledger = SimpleNamespace(clear_safety_incident=lambda *_a: None)

    runtime._reconcile_safely()

    assert runtime._reconcile_pending_reason == "trade_updates_restart"
    assert len(schedule_calls) == 1


# --- Task 2: stale trade feed with open orders ---


@pytest.mark.parametrize(
    ("silent_seconds", "has_open_orders", "moment", "expect_latch"),
    [
        (301, True, _SESSION_MOMENT, True),
        (120, True, _SESSION_MOMENT, False),
        (301, False, _SESSION_MOMENT, False),
        (301, True, datetime(2026, 10, 25, 15, 0, tzinfo=UTC), False),  # Sunday
    ],
    ids=["stale_with_orders", "fresh_feed", "stale_without_orders", "stale_outside_session"],
)
def test_stale_trade_feed_with_open_orders_latches_and_reconciles(
    silent_seconds: int, has_open_orders: bool, moment: datetime, expect_latch: bool
) -> None:
    runtime, _worker, schedule_calls = _stream_runtime()
    runtime.broker = SimpleNamespace(account_id="acct")
    orders = (SimpleNamespace(client_order_id="c-1"),) if has_open_orders else ()
    runtime._portfolio_repository = SimpleNamespace(open_orders=lambda _account_id: orders)
    # The silence clock is per open order (C2-5-2): the order's own last update.
    runtime._order_update_at = {"c-1": moment - timedelta(seconds=silent_seconds)}
    runtime._trade_feed_probe_at = None

    runtime._maybe_check_trade_feed_freshness(now=moment)

    if expect_latch:
        assert runtime._reconcile_pending_reason == "trade_feed_stale"
        assert len(schedule_calls) == 1
    else:
        assert runtime._reconcile_pending_reason is None
        assert schedule_calls == []


# --- Task 3: consecutive account fetch failures ---


def _account_loop_runtime(fetch: Callable[[], AccountSnapshot], iterations: int) -> EngineRuntime:
    """A runtime whose ``_account_loop`` makes exactly ``iterations`` passes."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._workers = {"pb-account": WorkerHealth(datetime.now(UTC))}
    runtime._running = True
    runtime._state = "running"
    runtime._state_lock = threading.RLock()
    runtime._circuit_breaker_reason = None
    runtime.config = SimpleNamespace(account_poll_interval_seconds=0.0)
    runtime._account_snapshot_cache_state = lambda: (None, None, 0)  # type: ignore[method-assign]
    runtime._publish_account_snapshot = lambda *_a, **_k: True  # type: ignore[method-assign]
    runtime._persist_risk_state = lambda _account: None  # type: ignore[method-assign]
    runtime._maybe_retry_rejected_exit_sells = lambda _account: None  # type: ignore[method-assign]
    runtime._portfolio_repository = SimpleNamespace(update_high_water=lambda *_a: None)
    runtime.broker = SimpleNamespace(account_id="acct", account=fetch)
    waits: list[float] = []

    def wait(timeout: float) -> bool:
        waits.append(timeout)
        if len(waits) >= iterations:
            runtime._running = False
        return False

    runtime._stop_event = SimpleNamespace(wait=wait)  # type: ignore[assignment]
    return runtime


def _failing_fetch() -> AccountSnapshot:
    raise RuntimeError("broker unreachable")


def test_four_failed_account_fetches_do_not_trip_the_breaker() -> None:
    runtime = _account_loop_runtime(_failing_fetch, iterations=4)

    runtime._account_loop()

    assert runtime._circuit_breaker_reason is None
    assert runtime._consecutive_account_failures == 4


def test_five_consecutive_failed_account_fetches_trip_the_breaker() -> None:
    runtime = _account_loop_runtime(_failing_fetch, iterations=5)

    runtime._account_loop()

    assert runtime._circuit_breaker_reason == "account_fetch_failures"
    assert runtime._state == "halted"


def test_successful_fetch_resets_the_failure_count() -> None:
    outcomes: list[bool] = [False] * 4 + [True] + [False] * 4  # True = fetch succeeds
    snapshot = AccountSnapshot(10_000, 10_000, 10_000, 0)

    def fetch() -> AccountSnapshot:
        if outcomes.pop(0):
            return snapshot
        raise RuntimeError("down")

    runtime = _account_loop_runtime(fetch, iterations=9)

    runtime._account_loop()

    assert runtime._circuit_breaker_reason is None
    assert runtime._consecutive_account_failures == 4


# --- Task 4: sd_notify ---


def test_notify_sends_datagrams_to_notify_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        path = os.path.join(directory, "n.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            server.bind(path)
            # A regression must fail the test, not block the whole suite on recv().
            server.settimeout(2.0)
            monkeypatch.setenv("NOTIFY_SOCKET", path)

            assert sd_notify.ready() is True
            assert server.recv(64) == b"READY=1"
            assert sd_notify.watchdog() is True
            assert server.recv(64) == b"WATCHDOG=1"
        finally:
            server.close()


def test_notify_is_a_no_op_without_notify_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)

    assert sd_notify.notify("READY=1") is False
    assert sd_notify.ready() is False
    assert sd_notify.watchdog() is False


def test_notify_reports_an_unreachable_socket_without_raising(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "missing.sock"))

    assert sd_notify.notify("WATCHDOG=1") is False


@pytest.mark.skipif(sys.platform != "linux", reason="abstract AF_UNIX names are Linux-only")
def test_notify_supports_abstract_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    name = f"pb-test-{os.getpid()}"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        server.bind("\0" + name)
        server.settimeout(2.0)
        monkeypatch.setenv("NOTIFY_SOCKET", "@" + name)

        assert sd_notify.ready() is True
        assert server.recv(64) == b"READY=1"
    finally:
        server.close()
