"""Audit wave 2, worker F: lock ordering (C2-1), stream supervisor (C2-5), resource leaks (C2-7).

Offline and deterministic: every SDK stream, broker and HTTP client is a fake
or a patched class, and the journal is redirected by the conftest fixtures.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from parayabuck.config import ObservabilityConfig
from parayabuck.core.models import Market
from parayabuck.data.alpaca_stream import AlpacaBarStream, supervise_sdk_stream
from parayabuck.execution import alpaca_updates
from parayabuck.execution.alpaca_updates import UNDECODED_TRADE_EVENT, AlpacaTradingUpdateStream
from parayabuck.execution.portfolio import PortfolioOrderSide
from parayabuck.monitoring import journal as journal_module
from parayabuck.runtime import EngineRuntime, TradingState
from parayabuck.web.services import performance

# -- C2-5-1: reconnect backoff resets only after a stable session -----------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _SdkStream:
    """Fake alpaca-py stream: connects succeed unless ``connect_fails`` is set."""

    def __init__(self) -> None:
        self.connects = 0
        self.connect_fails = 0
        self._should_run = True
        self.closed = 0

    async def _start_ws(self) -> None:
        if self.connect_fails > 0:
            self.connect_fails -= 1
            raise ConnectionError("connect refused")
        self.connects += 1

    async def close(self) -> None:
        self.closed += 1


def _reconnect(stream: _SdkStream) -> None:
    asyncio.run(stream._start_ws())


def test_a_short_session_drop_backs_off_before_the_sdk_reopens_it() -> None:
    sleeps: list[float] = []
    clock = _Clock()

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    stream = _SdkStream()
    supervise_sdk_stream(stream, label="trade updates", sleep=fake_sleep, clock=clock)

    _reconnect(stream)  # first connect: no delay
    assert sleeps == []
    clock.now += 5.0  # the session drops after 5 s
    _reconnect(stream)  # first short-session drop: 1 s
    clock.now += 5.0
    _reconnect(stream)  # second short-session drop: 2 s

    assert stream.connects == 3
    assert sum(sleeps) == pytest.approx(3.0)


def test_a_stable_session_drop_reconnects_without_backoff_and_resets_the_streak() -> None:
    sleeps: list[float] = []
    clock = _Clock()

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    stream = _SdkStream()
    supervise_sdk_stream(stream, label="trade updates", sleep=fake_sleep, clock=clock)
    _reconnect(stream)
    clock.now += 5.0
    _reconnect(stream)  # short: backed off
    clock.now += 600.0
    sleeps.clear()
    _reconnect(stream)  # stable: no delay, and the streak is reset

    assert sleeps == []
    clock.now += 5.0
    sleeps.clear()
    _reconnect(stream)  # the next short drop starts again at 1 s
    assert sum(sleeps) == pytest.approx(1.0)


def test_a_stop_during_reconnect_backoff_does_not_reconnect() -> None:
    clock = _Clock()

    async def fake_sleep(_seconds: float) -> None:
        return None

    stream = _SdkStream()
    supervise_sdk_stream(stream, label="trade updates", sleep=fake_sleep, clock=clock)
    _reconnect(stream)
    clock.now += 2.0
    stream._should_run = False  # stop requested while the session was down

    with pytest.raises(ConnectionError):
        _reconnect(stream)
    assert stream.connects == 1


def test_on_reconnect_runs_after_a_reconnect_but_not_the_first_connect() -> None:
    clock = _Clock()
    calls: list[int] = []

    async def fake_sleep(_seconds: float) -> None:
        return None

    stream = _SdkStream()
    supervise_sdk_stream(
        stream,
        label="trade updates",
        sleep=fake_sleep,
        clock=clock,
        on_reconnect=lambda: calls.append(1),
    )
    _reconnect(stream)
    assert calls == []
    clock.now += 5.0
    _reconnect(stream)
    assert calls == [1]


def test_a_failing_on_reconnect_hook_does_not_fail_the_connect() -> None:
    clock = _Clock()

    async def fake_sleep(_seconds: float) -> None:
        return None

    def broken_hook() -> None:
        raise RuntimeError("hook broke")

    stream = _SdkStream()
    supervise_sdk_stream(stream, label="trade updates", sleep=fake_sleep, clock=clock, on_reconnect=broken_hook)
    _reconnect(stream)
    clock.now += 5.0
    _reconnect(stream)
    assert stream.connects == 2


def test_trade_stream_forwards_on_reconnect_and_uses_the_short_close_timeout(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(alpaca_updates, "supervise_sdk_stream", lambda _stream, **kwargs: captured.update(kwargs))

    async def handler(_update: object) -> None:
        return None

    def hook() -> None:
        return None

    stream = AlpacaTradingUpdateStream("key", "secret", True, handler, on_reconnect=hook)

    assert captured["on_reconnect"] is hook
    assert stream._stream._websocket_params["close_timeout"] == 2


# -- C2-5-4: one failing bar handler drops only its own bar -----------------


class _BarSdk:
    def __init__(self) -> None:
        self.handler: Any = None

    def subscribe_bars(self, handler: Any, *symbols: str) -> None:
        self.handler = handler


def _bar(symbol: str) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=datetime(2026, 10, 27, 14, 30, tzinfo=UTC),
        symbol=symbol,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
    )


def test_a_failing_bar_handler_does_not_drop_the_rest_of_the_frame() -> None:
    sdk = _BarSdk()
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stream.stock = sdk
    seen: list[str] = []

    async def handler(bar: Any) -> None:
        if bar.symbol == "BAD":
            raise RuntimeError("handler failed")
        seen.append(bar.symbol)

    stream.subscribe(["BAD", "QLD"], handler)

    async def frame() -> None:
        # alpaca-py dispatches the frame's bars one after another.
        await sdk.handler(_bar("BAD"))
        await sdk.handler(_bar("QLD"))

    asyncio.run(frame())
    assert seen == ["QLD"]


# -- C2-5-5 / C2-5-6: unparseable updates reconcile; a stop is never lost -----


def test_an_unparseable_update_with_an_identity_is_handed_on_as_undecoded(monkeypatch: Any) -> None:
    alerts: list[dict[str, Any]] = []
    monkeypatch.setattr(alpaca_updates, "emit_execution_alert", lambda kind, **fields: alerts.append(fields))
    handled: list[Any] = []

    async def handler(update: Any) -> None:
        handled.append(update)

    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    stream._handler = handler
    raw = SimpleNamespace(
        event="fill",
        order=SimpleNamespace(symbol="QLD", client_order_id="pb-9", id="b-9", filled_qty="not-a-number"),
    )

    asyncio.run(stream._on_update(raw))

    assert [(update.event, update.client_order_id, update.broker_order_id) for update in handled] == [
        (UNDECODED_TRADE_EVENT, "pb-9", "b-9")
    ]
    assert handled[0].filled_qty == 0.0
    assert alerts and alerts[0]["category"] == "undecoded_trade_update"


def test_an_unparseable_update_without_an_identity_is_not_handed_on(monkeypatch: Any) -> None:
    monkeypatch.setattr(alpaca_updates, "emit_execution_alert", lambda *_args, **_fields: None)
    handled: list[Any] = []

    async def handler(update: Any) -> None:
        handled.append(update)

    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    stream._handler = handler
    raw = SimpleNamespace(event="fill", order=SimpleNamespace(symbol="QLD", filled_qty="not-a-number"))

    asyncio.run(stream._on_update(raw))

    assert handled == []


def test_a_stop_before_run_is_not_lost_for_the_bar_stream() -> None:
    sdk = Mock()
    sdk._loop = None
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stream.stock = sdk

    stream.stop()
    stream.run_stock()

    sdk.run.assert_not_called()


def test_a_stop_before_run_is_not_lost_for_the_trade_stream() -> None:
    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    sdk = Mock()
    sdk._loop = None
    stream._stream = sdk

    stream.stop()
    stream.run()

    sdk.run.assert_not_called()


def test_a_run_without_a_stop_still_runs_the_sdk_loop() -> None:
    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    sdk = Mock()
    stream._stream = sdk

    stream.run()

    sdk.run.assert_called_once_with()


# -- C2-1-1: BUYs wait for the equity signal basis, under the lock and at submit --


def _runtime(*, latch: str | None) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._circuit_breaker_reason = None
    runtime._warming_up = False
    runtime._reconcile_failed = False
    runtime._reconcile_pending_reason = None
    runtime._entry_risk_latch = {}
    runtime._market_entry_halts = {}
    runtime._state_lock = threading.RLock()
    runtime._symbols_by_market = {Market.EQUITY: ["QLD"]}
    runtime._equity_signal_basis_cached_latch = lambda: latch  # type: ignore[method-assign]
    return runtime


def test_a_buy_is_held_while_the_equity_signal_basis_is_unverified() -> None:
    runtime = _runtime(latch="signal_adjustment_unverified")
    buy = SimpleNamespace(side=PortfolioOrderSide.BUY)
    sell = SimpleNamespace(side=PortfolioOrderSide.SELL)

    assert runtime._advance_submit_block_reason(Market.EQUITY, buy) == "buy_not_permitted"
    # The decision path keeps SELLs flowing under the latch. The advance path
    # holds equity SELLs too (P3-3-2): an unverified basis freezes the book.
    assert runtime._advance_submit_block_reason(Market.EQUITY, sell) is None
    assert runtime._advance_submit_block_reason(Market.EQUITY, sell, advance_path=True) == "sell_basis_unverified"
    assert runtime._advance_submit_block_reason(Market.CRYPTO, sell, advance_path=True) is None


def test_a_buy_flows_once_the_equity_signal_basis_verifies() -> None:
    runtime = _runtime(latch=None)
    buy = SimpleNamespace(side=PortfolioOrderSide.BUY)

    assert runtime._advance_submit_block_reason(Market.EQUITY, buy) is None


def test_the_basis_hold_applies_to_equity_only() -> None:
    runtime = _runtime(latch="signal_adjustment_unverified")

    assert runtime._buy_held_by_signal_basis(Market.EQUITY) is True
    assert runtime._buy_held_by_signal_basis(Market.CRYPTO) is False
    assert runtime.trading_state(Market.EQUITY) is TradingState.ACTIVE


# -- C2-1-3: incident-recovery reconcile takes the reconcile lock ------------


def test_incident_recovery_reconcile_runs_under_the_reconcile_lock() -> None:
    runtime = _runtime(latch=None)
    runtime._reconcile_lock = threading.Lock()
    seen: dict[str, bool] = {}

    class _Broker:
        def reconcile(self) -> dict[str, list[str]]:
            seen["locked"] = runtime._reconcile_lock.locked()
            return {"orders": []}

    runtime.broker = _Broker()

    assert runtime._reconcile_for_incident_recovery() == {"orders": []}
    assert seen["locked"] is True
    assert not runtime._reconcile_lock.locked()


def test_incident_recovery_reconcile_without_a_broker_reports_false() -> None:
    runtime = _runtime(latch=None)
    runtime._reconcile_lock = threading.Lock()
    runtime.broker = None

    assert runtime._reconcile_for_incident_recovery() is False


# -- C2-7-1: a replaced default journal is stopped, not orphaned ------------


def test_configure_global_journal_stops_the_replaced_default_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    class _DefaultJournal:
        path = journal_module.DEFAULT_JOURNAL_PATH

        def __init__(self) -> None:
            self.stopped = 0

        def stop(self, *, timeout: float = 2.0) -> None:
            self.stopped += 1

    default = _DefaultJournal()
    monkeypatch.setattr(journal_module, "_global_journal", default)
    monkeypatch.setattr(journal_module, "_global_is_configured", False)
    observability = ObservabilityConfig(journal_path=str(tmp_path / "observability.sqlite3"))

    journal = journal_module.configure_global_journal(observability)

    assert default.stopped == 1
    assert journal is not default
    # A second configuration keeps the installed journal and stops nothing more.
    assert journal_module.configure_global_journal(observability) is journal
    assert default.stopped == 1


# -- C2-7-2: the benchmark client's HTTP session is closed after each fetch --


class _TrackedSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.closed_count = 0

    def close(self) -> None:
        self.closed_count += 1
        super().close()


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, get_stock_bars: Any) -> list[_TrackedSession]:
    from alpaca.data import historical

    sessions: list[_TrackedSession] = []

    class _FakeClient:
        def __init__(self, key: str, secret: str) -> None:
            self._session = _TrackedSession()
            sessions.append(self._session)

        def get_stock_bars(self, request: Any) -> Any:
            return get_stock_bars(request)

    monkeypatch.setattr(historical, "StockHistoricalDataClient", _FakeClient)
    return sessions


def test_the_benchmark_session_is_closed_after_a_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = _install_fake_client(monkeypatch, lambda _request: SimpleNamespace(data={"VOO": []}))
    manager = SimpleNamespace(get_credentials=lambda: ("key", "secret"))
    start = datetime(2026, 10, 27, 13, 30, tzinfo=UTC)

    assert performance.fetch_benchmark_bars(manager, start, start + timedelta(hours=1), 60) == []
    assert [session.closed_count for session in sessions] == [1]


def test_the_benchmark_session_is_closed_even_when_the_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(_request: Any) -> Any:
        raise RuntimeError("provider down")

    sessions = _install_fake_client(monkeypatch, failing)
    manager = SimpleNamespace(get_credentials=lambda: ("key", "secret"))
    start = datetime(2026, 10, 27, 13, 30, tzinfo=UTC)

    with pytest.raises(RuntimeError, match="provider down"):
        performance.fetch_benchmark_bars(manager, start, start + timedelta(hours=1), 60)
    assert [session.closed_count for session in sessions] == [1]
