"""Fault injection for the Alpaca execution boundary.

Every test drives PRODUCTION code -- ``AlpacaBroker._submit_with_recovery``,
``AlpacaBroker._wait_for_cancel_confirmation`` and the broker's FILL-activity
backfill -- and asserts the broker's decision (returned order, raised
exception, alert payload, ledger rows). ``FakeTradingClient`` only supplies
scripted broker responses; it is never the subject of an assertion.

Offline and deterministic: the client is a local fake, ledger and peak
sidecar live under ``tmp_path``, and the cancel-confirmation loop bounds are
monkeypatched to milliseconds.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.common.exceptions import APIError

from parayabuck.core.models import Direction, Fill, Market, OrderIntent
from parayabuck.execution import alpaca as alpaca_module
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.alpaca_models import AmbiguousOrderState
from parayabuck.execution.fake_client import FakeTradingClient
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring import alerts
from tests.helpers import apply_entry_fill

_FILLED_AT = datetime(2026, 1, 2, 14, 5, tzinfo=UTC)
_CANCEL_TIMEOUT_SECONDS = 0.01
_CANCEL_POLL_INTERVAL_SECONDS = 0.001


@pytest.fixture
def alerts_emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Capture production ``_emit_alert`` payloads instead of delivering them."""
    emitted: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(alerts, "emit", lambda kind, **fields: emitted.append((kind, fields)))
    return emitted


@pytest.fixture
def broker(tmp_path: Path) -> Iterator[AlpacaBroker]:
    """A production broker wired to a ``tmp_path`` ledger/peak sidecar."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.db"))
    instance = AlpacaBroker("k", "s", paper=True, ledger=ledger, peak_state_path=tmp_path / "peak.json")
    # The facade only falls back to its own cancel-confirmation loop when no
    # protection service is attached; that is the branch under test.
    instance.__dict__.pop("_protection_service", None)
    try:
        yield instance
    finally:
        ledger.close()


def _broker_with_fake(instance: AlpacaBroker, fake: FakeTradingClient) -> AlpacaBroker:
    instance._client = fake  # type: ignore[assignment]
    fake.set_account(equity="10000")
    return instance


def _fast_cancel_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse the module-level cancel-confirmation bounds to milliseconds."""
    monkeypatch.setattr(alpaca_module, "STOP_CANCEL_TIMEOUT_SECONDS", _CANCEL_TIMEOUT_SECONDS)
    monkeypatch.setattr(alpaca_module, "STOP_CANCEL_POLL_INTERVAL_SECONDS", _CANCEL_POLL_INTERVAL_SECONDS)


def _not_found_error() -> APIError:
    """The 404 shape Alpaca raises when a client order id was never accepted."""
    return APIError("order not found", SimpleNamespace(response=SimpleNamespace(status_code=404)))


# -- submit recovery -------------------------------------------------------


def test_submit_recovers_the_order_the_broker_actually_accepted(
    broker: AlpacaBroker, alerts_emitted: list[tuple[str, dict[str, Any]]]
) -> None:
    """A submit that timed out after acceptance is recovered, not re-sent."""
    accepted = SimpleNamespace(id="broker-1", client_order_id="cid-1", symbol="IWM", status="accepted")
    submit_error = TimeoutError("network timeout after accept")
    fake = FakeTradingClient()

    def failing_submit(_request: Any) -> Any:
        raise submit_error

    fake.script_submit = failing_submit
    fake.script_get_order_by_client_id = lambda client_id: accepted if client_id == "cid-1" else None
    _broker_with_fake(broker, fake)

    recovered = broker._submit_with_recovery(SimpleNamespace(client_order_id="cid-1", symbol="IWM"), "cid-1")

    assert recovered.id == "broker-1"
    # Recovery is a definite outcome, so no ambiguity is reported.
    assert alerts_emitted == []


def test_submit_is_ambiguous_when_the_recovery_lookup_also_fails(
    broker: AlpacaBroker, alerts_emitted: list[tuple[str, dict[str, Any]]]
) -> None:
    """Both the write and the recovery read failed: the outcome is unknown."""
    fake = FakeTradingClient()

    def failing_submit(_request: Any) -> Any:
        raise TimeoutError("submit timeout")

    def failing_lookup(_client_id: str) -> Any:
        raise TimeoutError("lookup timeout")

    fake.script_submit = failing_submit
    fake.script_get_order_by_client_id = failing_lookup
    _broker_with_fake(broker, fake)

    with pytest.raises(AmbiguousOrderState) as excinfo:
        broker._submit_with_recovery(SimpleNamespace(client_order_id="cid-2", symbol="IWM"), "cid-2")

    assert "cid-2" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, TimeoutError)
    assert alerts_emitted == [
        (
            "ambiguous_order",
            {
                "message": "Broker write outcome unknown for client_order_id=cid-2: submit timeout",
                "client_order_id": "cid-2",
                "reason": "broker_write_outcome_unknown",
            },
        )
    ]


def test_submit_reraises_the_original_failure_when_the_lookup_proves_absence(broker: AlpacaBroker) -> None:
    """A 404 lookup proves the client order id was never accepted: retryable."""
    submit_error = RuntimeError("submit rejected")
    fake = FakeTradingClient()

    def failing_submit(_request: Any) -> Any:
        raise submit_error

    def missing_lookup(_client_id: str) -> Any:
        raise _not_found_error()

    fake.script_submit = failing_submit
    fake.script_get_order_by_client_id = missing_lookup
    _broker_with_fake(broker, fake)

    with pytest.raises(RuntimeError) as excinfo:
        broker._submit_with_recovery(SimpleNamespace(client_order_id="cid-3", symbol="IWM"), "cid-3")

    assert excinfo.value is submit_error


def test_submit_reraises_the_original_failure_when_the_recovered_order_is_rejected(
    broker: AlpacaBroker,
) -> None:
    """A terminally rejected recovery hit is a definite rejection, not ambiguity."""
    submit_error = RuntimeError("submit rejected")
    fake = FakeTradingClient()

    def failing_submit(_request: Any) -> Any:
        raise submit_error

    def rejected_lookup(client_id: str) -> Any:
        return SimpleNamespace(id="broker-4", client_order_id=client_id, symbol="IWM", status="rejected")

    fake.script_submit = failing_submit
    fake.script_get_order_by_client_id = rejected_lookup
    _broker_with_fake(broker, fake)

    with pytest.raises(RuntimeError) as excinfo:
        broker._submit_with_recovery(SimpleNamespace(client_order_id="cid-4", symbol="IWM"), "cid-4")

    assert excinfo.value is submit_error


# -- cancel confirmation ---------------------------------------------------


def test_cancel_confirmation_returns_once_the_broker_reports_canceled(
    broker: AlpacaBroker, monkeypatch: pytest.MonkeyPatch, alerts_emitted: list[tuple[str, dict[str, Any]]]
) -> None:
    """The loop keeps polling until the broker confirms the cancellation."""
    _fast_cancel_bounds(monkeypatch)
    statuses = ["pending_cancel", "canceled"]

    def order_by_id(order_id: str) -> Any:
        return SimpleNamespace(id=order_id, symbol="IWM", status=statuses.pop(0))

    fake = FakeTradingClient()
    fake.script_get_order_by_id = order_by_id
    _broker_with_fake(broker, fake)

    broker._wait_for_cancel_confirmation("o1", "IWM")

    # The first poll was not terminal, so production had to ask again.
    assert statuses == []
    assert alerts_emitted == []


def test_cancel_confirmation_is_ambiguous_when_the_broker_never_confirms(
    broker: AlpacaBroker, monkeypatch: pytest.MonkeyPatch, alerts_emitted: list[tuple[str, dict[str, Any]]]
) -> None:
    """A still-pending protective order after the bound is an ambiguity, not a cancel."""
    _fast_cancel_bounds(monkeypatch)
    polls: list[str] = []

    def order_by_id(order_id: str) -> Any:
        polls.append(order_id)
        return SimpleNamespace(id=order_id, symbol="IWM", status="accepted")

    fake = FakeTradingClient()
    fake.script_get_order_by_id = order_by_id
    _broker_with_fake(broker, fake)

    with pytest.raises(AmbiguousOrderState) as excinfo:
        broker._wait_for_cancel_confirmation("o1", "IWM")

    assert "IWM" in str(excinfo.value)
    assert polls  # the broker polled the order before giving up
    assert [kind for kind, _fields in alerts_emitted] == ["ambiguous_order"]
    assert alerts_emitted[0][1]["reason"] == "cancel_unconfirmed"
    assert alerts_emitted[0][1]["symbol"] == "IWM"


def test_cancel_confirmation_is_ambiguous_when_the_protective_order_fills(
    broker: AlpacaBroker, monkeypatch: pytest.MonkeyPatch, alerts_emitted: list[tuple[str, dict[str, Any]]]
) -> None:
    """A fill landing mid-cancel means the stop executed: halt, loudly."""
    _fast_cancel_bounds(monkeypatch)
    fake = FakeTradingClient()
    fake.script_get_order_by_id = lambda order_id: SimpleNamespace(
        id=order_id, symbol="IWM", status="filled", filled_qty="10"
    )
    _broker_with_fake(broker, fake)

    with pytest.raises(AmbiguousOrderState) as excinfo:
        broker._wait_for_cancel_confirmation("o1", "IWM")

    assert "IWM" in str(excinfo.value)
    assert alerts_emitted == [
        (
            "ambiguous_order",
            {
                "message": "Protective order filled while cancellation was pending for IWM",
                "symbol": "IWM",
                "reason": "protective_order_filled_during_cancel",
            },
        )
    ]


# -- fill recovery idempotence ---------------------------------------------


def _entry_intent() -> OrderIntent:
    return OrderIntent(
        "signal",
        "XLE",
        Market.EQUITY,
        Direction.LONG,
        10,
        100,
        95,
        110,
        "test",
        created_at=datetime(2026, 1, 2, 14, 0, tzinfo=UTC),
        client_order_id="parayabuck-faults-entry",
    )


def _fill_activity(activity_id: str, *, qty: str, price: str, at: datetime, commission: str) -> dict[str, Any]:
    return {
        "id": activity_id,
        "activity_type": "FILL",
        "order_id": "broker-entry",
        "symbol": "XLE",
        "side": "buy",
        "qty": qty,
        "price": price,
        "commission": commission,
        "transaction_time": at.isoformat(),
    }


def test_a_redelivered_fill_activity_is_booked_once(broker: AlpacaBroker) -> None:
    """The same FILL activity arriving twice must produce exactly one fill."""
    order = _entry_intent()
    ledger = broker.ledger
    assert ledger is not None
    ledger.register_entry_order("acct", order, "broker-entry")

    fake = FakeTradingClient()
    fake.activities = [_fill_activity("activity-1", qty="10", price="100", at=_FILLED_AT, commission="1")]
    _broker_with_fake(broker, fake)
    broker._account_id = "acct"

    assert broker._reconcile_fill_activity_fees() is True
    # The same activity id arrives again on the next pass (a restart replays
    # the 24 h overlap window).
    assert broker._reconcile_fill_activity_fees() is True

    assert ledger.connection.execute(
        "SELECT cumulative_filled_qty, average_fill_price, terminal_state FROM orders WHERE client_order_id=?",
        (order.client_order_id,),
    ).fetchone() == (10.0, 100.0, "filled")
    position = ledger.managed_for_account("acct")["XLE"]
    assert position.owned_qty == 10.0
    assert position.filled_avg_price == pytest.approx(100.0)
    # The commission is recorded once, not once per delivery.
    assert ledger.connection.execute("SELECT COUNT(*) FROM fee_activities").fetchone() == (1,)


def test_recovered_fills_extend_a_known_lot_and_book_their_fee_once(broker: AlpacaBroker) -> None:
    """Two partial activities complete the lot; replaying them changes nothing."""
    order = _entry_intent()
    ledger = broker.ledger
    assert ledger is not None
    ledger.register_entry_order("acct", order, "broker-entry")
    # The stream booked the first partial fill before the bot went offline.
    apply_entry_fill(
        ledger,
        "acct",
        order,
        Fill(order.client_order_id, "XLE", 5, 99.0, Direction.LONG, _FILLED_AT),
        "broker-entry",
    )

    fake = FakeTradingClient()
    fake.activities = [
        _fill_activity(
            f"activity-{index}",
            qty="5",
            price=str(price),
            at=_FILLED_AT + timedelta(seconds=index),
            commission="0.5",
        )
        for index, price in enumerate((101.0, 102.0), 1)
    ]
    _broker_with_fake(broker, fake)
    broker._account_id = "acct"

    assert broker._reconcile_fill_activity_fees() is True
    recovered = ledger.managed_for_account("acct")["XLE"]
    assert recovered.owned_qty == 10.0
    assert recovered.filled_avg_price == pytest.approx(101.5)
    assert ledger.total_fees_for_account("acct") == pytest.approx(1.0)

    assert broker._reconcile_fill_activity_fees() is True

    replayed = ledger.managed_for_account("acct")["XLE"]
    assert replayed.owned_qty == 10.0
    assert replayed.filled_avg_price == pytest.approx(101.5)
    assert ledger.total_fees_for_account("acct") == pytest.approx(1.0)
    assert ledger.connection.execute("SELECT COUNT(*) FROM fee_activities").fetchone() == (2,)
