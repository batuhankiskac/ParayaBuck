"""Order-boundary invariants: fill polling state, fee authority, recovery.

Synthetic brokers built with ``__new__`` and a deterministic fake clock — no
network, no credentials, no production paths.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Direction, Market, OrderIntent, OrderState
from parayabuck.execution import alpaca as alpaca_module
from parayabuck.execution.alpaca import AlpacaBroker, AmbiguousOrderState
from parayabuck.execution.alpaca_orders import OrderService


class _NotFound(Exception):
    """A broker 404 as the SDK models it."""

    status_code = 404


def _intent() -> OrderIntent:
    return OrderIntent(
        signal_id="signal-1",
        symbol="AAPL",
        market=Market.EQUITY,
        direction=Direction.LONG,
        qty=2.0,
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        strategy="test",
        client_order_id="intent-1",
    )


def _poll_broker(responses: list[Any], monkeypatch: pytest.MonkeyPatch) -> AlpacaBroker:
    """Broker whose order lookup walks ``responses`` then always fails.

    ``time.monotonic`` advances only after the scripted polls so the loop runs
    a fixed number of iterations regardless of machine speed.
    """
    broker = AlpacaBroker.__new__(AlpacaBroker)
    polls = iter(responses)
    lookups = {"count": 0}

    def get_order_by_id(_order_id: str) -> Any:
        lookups["count"] += 1
        try:
            response = next(polls)
        except StopIteration:
            raise TimeoutError("transient lookup failure")
        if isinstance(response, Exception):
            raise response
        return response

    broker._client = SimpleNamespace(get_order_by_id=get_order_by_id)  # type: ignore[attr-defined]
    ticks = iter([0.0, 0.1, 0.2, 0.3, 0.4, 99.0, 99.0, 99.0])
    monkeypatch.setattr(alpaca_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(alpaca_module.time, "sleep", lambda _seconds: None)
    return broker


def _partial_order() -> SimpleNamespace:
    return SimpleNamespace(
        id="broker-1",
        status="partially_filled",
        filled_qty=1,
        filled_avg_price=101.0,
        fees=2.5,
    )


def test_transient_lookup_failure_keeps_the_observed_partial_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed status lookup is not new information. Downgrading a
    ``PARTIALLY_FILLED`` order to ``ACCEPTED`` skips the cancel/re-protect
    branch for a partially-filled market exit that is still working."""
    broker = _poll_broker([_partial_order(), TimeoutError("boom")], monkeypatch)

    state, fill = broker._wait_for_fill("broker-1", _intent(), timeout=1.0)

    assert state is OrderState.PARTIALLY_FILLED
    assert fill is not None and fill.qty == 1.0


def test_wait_for_fill_does_not_claim_a_fee_from_the_order_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``alpaca.trading.models.Order`` has no ``fees`` field; the fill-activity
    reconciler is the authoritative fee source, so the order lookup must not
    assert one."""
    broker = _poll_broker([_partial_order(), TimeoutError("boom")], monkeypatch)

    _state, fill = broker._wait_for_fill("broker-1", _intent(), timeout=1.0)

    assert fill is not None
    assert fill.commission == 0.0


def test_a_fresh_accepted_status_still_downgrades_the_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine non-terminal, non-partial observation is new information and
    must still be reported as ``ACCEPTED``."""
    accepted = SimpleNamespace(id="broker-1", status="accepted", filled_qty=0, filled_avg_price=0)
    broker = _poll_broker([_partial_order(), accepted], monkeypatch)

    state, _fill = broker._wait_for_fill("broker-1", _intent(), timeout=1.0)

    assert state is OrderState.ACCEPTED


def test_portfolio_submit_404_lookup_reraises_the_submit_failure() -> None:
    """The broker's 404 proves the client id never reached Alpaca, so the
    operator must see the submit rejection, not the lookup's 404."""
    submit_error = ValueError("422 insufficient buying power")

    def submit(_request: Any) -> Any:
        raise submit_error

    def lookup(_client_order_id: str) -> Any:
        raise _NotFound("order not found")

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(  # type: ignore[attr-defined]
        submit_order=submit,
        get_order_by_client_id=lookup,
    )
    service = OrderService(broker)

    with pytest.raises(ValueError) as excinfo:
        service._submit_with_recovery(SimpleNamespace(), "cid-1")

    assert excinfo.value is submit_error



def test_portfolio_submit_ambiguous_lookup_raises_ambiguous_state() -> None:
    """A lookup failure that is not a 404 leaves the broker write outcome
    unknown; the shared recovery decision must report it as ambiguous."""
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(  # type: ignore[attr-defined]
        submit_order=lambda _request: (_ for _ in ()).throw(TimeoutError("network timeout")),
        get_order_by_client_id=lambda _cid: (_ for _ in ()).throw(TimeoutError("lookup timeout")),
    )
    service = OrderService(broker)

    with pytest.raises(AmbiguousOrderState):
        service._submit_with_recovery(SimpleNamespace(), "cid-1")
