"""Fault injection for the Alpaca execution boundary.

Every test drives PRODUCTION code -- ``AlpacaBroker._submit_with_recovery`` --
and asserts the boundary's decision (returned order, raised exception, alert
payload, recovered fill). ``FakeTradingClient`` only supplies scripted broker
responses; it is never the subject of an assertion.

Offline and deterministic: the client is a local fake, and the ledger and
peak sidecar live under ``tmp_path``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.common.exceptions import APIError

from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.alpaca_models import AmbiguousOrderState, DefiniteOrderRejection
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring import alerts
from tests.fake_client import FakeTradingClient

_FILLED_AT = datetime(2026, 1, 2, 14, 5, tzinfo=UTC)


@pytest.fixture
def alerts_emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Capture production ``_emit_alert`` payloads instead of delivering them."""
    emitted: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(alerts, "emit", lambda kind, **fields: emitted.append((kind, fields)))
    return emitted


@pytest.fixture
def broker(tmp_path: Path) -> Iterator[AlpacaBroker]:
    """A production broker wired to a ``tmp_path`` ledger/peak sidecar.

    The services under test are constructed against this broker directly:
    the facade keeps no fallback copies of their logic.
    """
    ledger = ExecutionLedger(str(tmp_path / "ledger.db"))
    instance = AlpacaBroker("k", "s", paper=True, ledger=ledger, peak_state_path=tmp_path / "peak.json")
    try:
        yield instance
    finally:
        ledger.close()


def _broker_with_fake(instance: AlpacaBroker, fake: FakeTradingClient) -> AlpacaBroker:
    instance._client = fake  # type: ignore[assignment]
    fake.set_account(equity="10000")
    return instance


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


def test_a_terminal_partial_fill_found_on_submit_recovery_is_booked_not_rejected(broker: AlpacaBroker) -> None:
    """A 4xx submit whose recovery finds a canceled order that already filled
    part of its quantity is the real terminal order: the fill is booked and
    no DefiniteOrderRejection loses it."""
    submit_error = APIError("forbidden", SimpleNamespace(response=SimpleNamespace(status_code=403)))
    terminal = SimpleNamespace(
        id="broker-5",
        client_order_id="cid-5",
        symbol="QLD",
        status="canceled",
        filled_qty="3",
        filled_avg_price="100.5",
        filled_at=None,
        updated_at=_FILLED_AT,
    )
    recovered_fills: list[tuple[Any, ...]] = []
    fake = FakeTradingClient()

    def failing_submit(_request: Any) -> Any:
        raise submit_error

    fake.script_submit = failing_submit
    fake.script_get_order_by_client_id = lambda _client_id: terminal
    _broker_with_fake(broker, fake)
    broker.portfolio_fill_recovered = lambda *args, **kwargs: recovered_fills.append((*args, kwargs))

    result = broker._submit_with_recovery(SimpleNamespace(client_order_id="cid-5", symbol="QLD"), "cid-5")

    assert result is terminal
    assert len(recovered_fills) == 1
    client_id, broker_id, quantity, price, occurred_at, _activity, fee, kwargs = recovered_fills[0]
    assert (client_id, broker_id, quantity, price, occurred_at, fee) == (
        "cid-5",
        "broker-5",
        3.0,
        100.5,
        _FILLED_AT,
        None,
    )
    assert kwargs == {"status": "canceled"}


def test_an_insufficient_quantity_refusal_never_accepted_is_a_definite_rejection(broker: AlpacaBroker) -> None:
    """Alpaca's insufficient-qty body is a validation refusal: once the client
    id lookup proves the order was never accepted it is a DefiniteOrderRejection."""
    submit_error = RuntimeError(
        '{"code": 40310000, "message": "insufficient qty available for order (requested: 10, available: 4)"}'
    )
    fake = FakeTradingClient()

    def failing_submit(_request: Any) -> Any:
        raise submit_error

    def missing_lookup(_client_id: str) -> Any:
        raise _not_found_error()

    fake.script_submit = failing_submit
    fake.script_get_order_by_client_id = missing_lookup
    _broker_with_fake(broker, fake)

    with pytest.raises(DefiniteOrderRejection) as excinfo:
        broker._submit_with_recovery(SimpleNamespace(client_order_id="cid-6", symbol="QLD"), "cid-6")

    assert excinfo.value.__cause__ is submit_error


def test_an_insufficient_quantity_refusal_recovers_an_earlier_accepted_order(broker: AlpacaBroker) -> None:
    """The shares an insufficient-qty refusal complains about may be held by
    our own earlier, accepted submit: that working order is recovered."""
    accepted = SimpleNamespace(id="broker-7", client_order_id="cid-7", symbol="QLD", status="accepted")
    fake = FakeTradingClient()

    def failing_submit(_request: Any) -> Any:
        raise RuntimeError("insufficient qty available for order (requested: 10, available: 0.5)")

    fake.script_submit = failing_submit
    fake.script_get_order_by_client_id = lambda _client_id: accepted
    _broker_with_fake(broker, fake)

    assert broker._submit_with_recovery(SimpleNamespace(client_order_id="cid-7", symbol="QLD"), "cid-7") is accepted
