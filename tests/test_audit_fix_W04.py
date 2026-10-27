"""Regression tests for audit worker W04: Alpaca order, update and stream boundary.

Synthetic payloads and in-process fakes only: no network, no credentials, no
production paths. Stream backoff is driven by an injected fake sleep.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.trading.enums import OrderStatus
from alpaca.trading.models import Order
from alpaca.trading.requests import GetOrdersRequest
from pydantic import ValidationError

from parayabuck.core.models import Market
from parayabuck.data import alpaca_stream
from parayabuck.data.alpaca_stream import AlpacaBarStream, guard_sdk_cast, supervise_sdk_stream
from parayabuck.execution import alpaca_updates
from parayabuck.execution.alpaca_client import AlpacaClient, parse_broker_timestamp
from parayabuck.execution.alpaca_models import DefiniteOrderRejection
from parayabuck.execution.alpaca_updates import UNDECODED_TRADE_EVENT, AlpacaTradingUpdateStream
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide
from tests.helpers import make_broker


def _order_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "client_order_id": "cid-1",
        "created_at": "2026-10-27T14:30:00.123456789Z",
        "updated_at": "2026-10-27T14:31:00.123456789Z",
        "submitted_at": "2026-10-27T14:30:00.123456789Z",
        "order_class": "simple",
        "time_in_force": "day",
        "extended_hours": False,
        "status": "new",
        "symbol": "QLD",
        "side": "buy",
        "qty": "10",
        "filled_qty": "0",
    }
    row.update(overrides)
    return row


def _unknown_status_error() -> ValidationError:
    """The ValidationError the pinned SDK model raises for an unknown status."""
    try:
        Order(**_order_row(status="some_new_status"))
    except ValidationError as exc:
        return exc
    raise AssertionError("the SDK model unexpectedly accepted an unknown status")


class _NotFound(Exception):
    status_code = 404


class _RawOrderClient:
    """Trading fake whose order reads raise the SDK's model error for an unknown status."""

    def __init__(self, raw_row: dict[str, Any], *, pages: list[list[dict[str, Any]]] | None = None) -> None:
        self.raw_row = raw_row
        self.pages = pages
        self.get_calls: list[tuple[str, Any]] = []

    def get(self, path: str, params: Any = None) -> Any:
        self.get_calls.append((path, params))
        if self.pages is not None:
            return self.pages[len([c for c in self.get_calls if c[0] == "/orders"]) - 1]
        return self.raw_row

    def get_order_by_client_id(self, _client_id: str) -> Any:
        raise _unknown_status_error()

    def get_order_by_id(self, _order_id: str) -> Any:
        raise _unknown_status_error()


# -- D8-2: unknown broker vocabulary must not fail every read ---------------


def test_open_orders_keep_an_unknown_status_row_as_the_broker_string() -> None:
    unknown = _order_row(id="22222222-2222-2222-2222-222222222222", client_order_id="cid-2", status="mystery")
    client = _RawOrderClient(raw_row={}, pages=[[_order_row(), unknown]])
    adapter = AlpacaClient(client=client)

    orders = adapter.get_open_orders(GetOrdersRequest(status="open"))

    assert len(orders) == 2
    assert orders[0].status == OrderStatus.NEW
    assert orders[1]["status"] == "mystery"


def test_single_order_lookup_falls_back_to_the_raw_row_on_an_unknown_status() -> None:
    client = _RawOrderClient(raw_row=_order_row(status="some_new_status"))
    adapter = AlpacaClient(client=client)

    by_client = adapter.order_by_client_id("cid-1")
    by_id = adapter.order_by_id("11111111-1111-1111-1111-111111111111")

    assert by_client["status"] == "some_new_status"
    assert by_id["status"] == "some_new_status"
    assert client.get_calls[0] == ("/orders:by_client_order_id", {"client_order_id": "cid-1"})
    assert client.get_calls[1][0] == "/orders/11111111-1111-1111-1111-111111111111"


def test_recovery_lookup_with_unknown_status_resolves_without_raising(tmp_path: Path) -> None:
    client = _RawOrderClient(raw_row=_order_row(status="some_new_status"))
    broker = make_broker(tmp_path, client=client)

    assert broker.resolve_portfolio_submission("cid-1", "") == ("submitted", "11111111-1111-1111-1111-111111111111")


# -- D8-2 / D8-7: stream frames the SDK cannot decode ----------------------


def _trade_frame(**order_overrides: Any) -> dict[str, Any]:
    order = {
        "id": "b-1",
        "client_order_id": "cid-1",
        "symbol": "QLD",
        "asset_class": "us_equity",
        "filled_qty": "2",
        "filled_avg_price": "10.5",
        "status": "new_status",
    }
    order.update(order_overrides)
    return {
        "stream": "trade_updates",
        "data": {
            "event": "fill",
            "execution_id": "e-1",
            "order": order,
            "timestamp": "2026-10-27T14:30:00.123456789Z",
            "price": "10.4",
            "qty": "2",
        },
    }


class _FakeTradingSdk:
    def __init__(self) -> None:
        self.handler: Any = None

    def _cast(self, msg: dict[str, Any]) -> Any:
        if msg["data"]["order"].get("status") == "new_status":
            raise ValueError("'new_status' is not a valid OrderStatus")
        return SimpleNamespace(**msg["data"])


def test_undecodable_trade_frame_is_handed_on_as_an_unknown_event_and_alerted(monkeypatch: Any) -> None:
    alerts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alpaca_updates, "emit_execution_alert", lambda kind, **fields: alerts.append({"kind": kind, **fields})
    )
    received: list[Any] = []

    async def handler(update: Any) -> None:
        received.append(update)

    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    stream._handler = handler
    sdk = _FakeTradingSdk()
    guard_sdk_cast(sdk, AlpacaTradingUpdateStream._undecoded_frame)

    frame = sdk._cast(_trade_frame())
    asyncio.run(stream._on_update(frame))

    assert len(received) == 1
    update = received[0]
    assert update.event == UNDECODED_TRADE_EVENT
    assert (update.client_order_id, update.broker_order_id, update.symbol) == ("cid-1", "b-1", "QLD")
    assert update.timestamp == datetime(2026, 10, 27, 14, 30, 0, 123456, tzinfo=UTC)
    assert [a["category"] for a in alerts] == ["undecoded_trade_update"]
    assert alerts[0]["client_order_id"] == "cid-1"


def test_guard_returns_the_frame_instead_of_raising_so_the_rest_of_the_frame_still_flows() -> None:
    sdk = _FakeTradingSdk()
    guard_sdk_cast(sdk, AlpacaTradingUpdateStream._undecoded_frame)

    good = sdk._cast(_trade_frame(status="filled"))
    bad = sdk._cast(_trade_frame())

    assert isinstance(good, SimpleNamespace)
    assert isinstance(bad, alpaca_updates._UndecodedFrame)
    assert "not a valid OrderStatus" in bad.error


def test_trade_update_carries_execution_price_quantity_and_nanosecond_timestamp() -> None:
    update = AlpacaTradingUpdateStream._parse(_trade_frame(status="partially_filled")["data"])

    assert update.execution_price == pytest.approx(10.4)
    assert update.execution_quantity == pytest.approx(2.0)
    assert update.average_fill_price == pytest.approx(10.5)
    assert update.timestamp == datetime(2026, 10, 27, 14, 30, 0, 123456, tzinfo=UTC)


def test_parse_broker_timestamp_accepts_nanoseconds_and_naive_text() -> None:
    assert parse_broker_timestamp("2026-10-27T14:30:00.123456789Z") == datetime(
        2026, 10, 27, 14, 30, 0, 123456, tzinfo=UTC
    )
    assert parse_broker_timestamp("2026-10-27T14:30:00") == datetime(2026, 10, 27, 14, 30, tzinfo=UTC)


# -- D8-7 / T9-2: one malformed bar drops only itself -----------------------


class _FakeDataSdk:
    def __init__(self, frames: list[Any]) -> None:
        self.frames = frames
        self.subscriptions: list[tuple[Any, tuple[str, ...]]] = []

    def _cast(self, msg: Any) -> Any:
        if msg == "malformed":
            raise KeyError("S")
        return msg

    def subscribe_bars(self, handler: Any, *symbols: str) -> None:
        self.subscriptions.append((handler, symbols))


def test_one_malformed_bar_is_dropped_alone_and_counted() -> None:
    good_one = SimpleNamespace(
        timestamp=datetime(2026, 10, 27, 14, 30, tzinfo=UTC),
        symbol="QLD",
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100,
    )
    good_two = SimpleNamespace(**{**vars(good_one), "timestamp": datetime(2026, 10, 27, 14, 31, tzinfo=UTC)})
    frames = [good_one, "malformed", SimpleNamespace(symbol="QLD"), good_two]
    sdk = _FakeDataSdk(frames)
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stream.stock = sdk
    stream._install_guards()
    received: list[Any] = []

    async def handler(bar: Any) -> None:
        received.append(bar)

    stream.subscribe(["QLD"], handler, timeframe="1m")
    stock_handler, _symbols = sdk.subscriptions[0]

    async def dispatch_frame() -> None:
        for frame in frames:
            await stock_handler(sdk._cast(frame))

    asyncio.run(dispatch_frame())

    assert [bar.timestamp.minute for bar in received] == [30, 31]
    assert stream.malformed_bar_count == 2


# -- D8-4: supervised reconnects ---------------------------------------------


class _FakeSdkStream:
    def __init__(self, auth_failures: int, connect_failures: int = 0) -> None:
        self.auth_failures_left = auth_failures
        self.connect_failures_left = connect_failures
        self.closed = 0
        self._should_run = True

    async def _auth(self) -> None:
        if self.auth_failures_left > 0:
            self.auth_failures_left -= 1
            raise ValueError("failed to authenticate")

    async def _start_ws(self) -> None:
        if self.connect_failures_left > 0:
            self.connect_failures_left -= 1
            raise ConnectionError("connect refused")
        await self._auth()

    async def close(self) -> None:
        self.closed += 1


def _attempt(stream: _FakeSdkStream) -> None:
    asyncio.run(stream._start_ws())


def test_repeated_auth_failures_back_off_close_the_socket_and_alert_once(monkeypatch: Any) -> None:
    alerts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alpaca_stream, "emit_execution_alert", lambda kind, **fields: alerts.append({"kind": kind, **fields})
    )
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    stream = _FakeSdkStream(auth_failures=3)
    supervise_sdk_stream(stream, label="trade updates", sleep=fake_sleep)

    for _ in range(3):
        with pytest.raises(ValueError):
            _attempt(stream)
    _attempt(stream)  # the fourth attempt authenticates

    assert stream.closed == 3
    # Delays 1s, 2s and 4s, sliced into one-second sleeps so a stop is noticed.
    assert sum(sleeps) == pytest.approx(7.0)
    assert [a["category"] for a in alerts] == ["stream_auth_failed"]
    assert alerts[0]["kind"] == "paper_incident"


def test_connect_failures_back_off_without_an_auth_alert(monkeypatch: Any) -> None:
    alerts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alpaca_stream, "emit_execution_alert", lambda kind, **fields: alerts.append({"kind": kind, **fields})
    )

    async def fake_sleep(_seconds: float) -> None:
        return None

    stream = _FakeSdkStream(auth_failures=0, connect_failures=4)
    supervise_sdk_stream(stream, label="stock data", sleep=fake_sleep)

    for _ in range(4):
        with pytest.raises(ConnectionError):
            _attempt(stream)

    assert stream.closed == 4
    assert alerts == []


def test_a_successful_connect_resets_the_backoff_and_the_auth_streak(monkeypatch: Any) -> None:
    monkeypatch.setattr(alpaca_stream, "emit_execution_alert", lambda *_args, **_fields: None)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    stream = _FakeSdkStream(auth_failures=2)
    supervise_sdk_stream(stream, label="trade updates", sleep=fake_sleep)
    for _ in range(2):
        with pytest.raises(ValueError):
            _attempt(stream)
    _attempt(stream)
    stream.auth_failures_left = 1
    with pytest.raises(ValueError):
        _attempt(stream)

    # After the success the next failure is again the first: a one-second delay.
    assert sum(sleeps[-1:]) == pytest.approx(1.0)
    assert sum(sleeps) == pytest.approx(1.0 + 2.0 + 1.0)


# -- D8-8 / P6-4 / BF-3 / D8-9: order submit and fill recovery --------------


def test_filled_order_recovered_after_a_submit_error_is_booked(tmp_path: Path) -> None:
    filled_at = datetime(2026, 10, 27, 14, 30, tzinfo=UTC)
    recovered_order = SimpleNamespace(
        id="b-9",
        client_order_id="cid-9",
        status="filled",
        filled_qty="4",
        filled_avg_price="50.0",
        filled_at=filled_at,
        submitted_at=filled_at,
        updated_at=datetime(2027, 1, 3, tzinfo=UTC),
    )
    booked: list[tuple[Any, ...]] = []
    client = SimpleNamespace(
        submit_order=lambda _request: (_ for _ in ()).throw(RuntimeError("timed out")),
        get_order_by_client_id=lambda _client_id: recovered_order,
    )
    broker = make_broker(tmp_path, client=client, portfolio_fill_recovered=lambda *a, **k: booked.append(a))

    result = broker._submit_with_recovery(SimpleNamespace(), "cid-9")

    assert result is recovered_order
    assert booked == [("cid-9", "b-9", 4.0, 50.0, filled_at, "cid-9:recovered:4.0", None)]


def test_lookup_404_after_a_server_error_is_still_a_definite_non_submission(tmp_path: Path) -> None:
    server_error = RuntimeError("500 internal server error")
    client = SimpleNamespace(
        submit_order=lambda _request: (_ for _ in ()).throw(server_error),
        get_order_by_client_id=lambda _client_id: (_ for _ in ()).throw(_NotFound("order not found")),
    )
    broker = make_broker(tmp_path, client=client)

    with pytest.raises(DefiniteOrderRejection) as excinfo:
        broker._submit_with_recovery(SimpleNamespace(), "cid-10")

    assert excinfo.value.__cause__ is server_error


def test_fill_is_dated_from_submission_when_the_order_has_no_filled_at(tmp_path: Path) -> None:
    broker = make_broker(tmp_path)
    order = SimpleNamespace(
        id="b-11",
        status="canceled",
        filled_qty="1",
        filled_avg_price="20.0",
        filled_at=None,
        submitted_at="2026-12-31T15:00:00.123456789Z",
        updated_at="2027-01-02T10:00:00+00:00",
    )

    fill = broker._fill_from_broker_order(order, "cid-11", "")

    assert fill is not None
    assert fill[4] == datetime(2026, 12, 31, 15, 0, 0, 123456, tzinfo=UTC)


def test_asset_rules_use_the_alpaca_order_minimum_not_asset_metadata(tmp_path: Path) -> None:
    client = SimpleNamespace(
        get_asset=lambda _symbol: SimpleNamespace(
            tradable=True,
            fractionable=True,
            min_order_size="1",
            min_trade_increment="1",
            min_order_notional="10",
        ),
    )
    broker = make_broker(tmp_path, client=client)

    rules = broker.asset_trading_rules("QLD", Market.EQUITY)

    assert rules.minimum_order_notional == pytest.approx(1.0)


def test_submit_guard_still_rejects_a_sub_dollar_order(tmp_path: Path) -> None:
    client = SimpleNamespace(
        get_asset=lambda _symbol: SimpleNamespace(
            tradable=True,
            fractionable=True,
            min_order_size="1",
            min_trade_increment="1",
        ),
        submit_order=lambda _request: pytest.fail("a sub-dollar order must not reach the broker"),
    )
    broker = make_broker(tmp_path, client=client)
    broker._has_open_order = lambda _symbol: False

    request = PortfolioOrderRequest(
        client_order_id="parayabuck-tiny",
        decision_id="dec-1",
        symbol="QLD",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=1.0,
        reference_price=0.5,
        decision_timestamp=datetime(2026, 1, 2, 14, 0, tzinfo=UTC),
        strategy_name="test",
        strategy_version="v1",
    )
    with pytest.raises(DefiniteOrderRejection, match="below minimum notional"):
        broker.submit_portfolio_order(request)
