"""Order-boundary invariants: fill polling state, fee authority, recovery.

Synthetic brokers built with ``__new__`` and a deterministic fake clock — no
network, no credentials, no production paths.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Direction, Market, OrderIntent, OrderState
from parayabuck.execution import alpaca as alpaca_module
from parayabuck.execution.alpaca import AlpacaBroker, AmbiguousOrderState
from parayabuck.execution.alpaca_orders import OrderService
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest, PortfolioOrderSide


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


class _FakeClock:
    """Broker-local clock yielding exactly ``polls`` loop iterations.

    ``monotonic`` reports "before the deadline" until the scripted polls are
    consumed, so the loop cannot be prolonged or cut short by any other
    in-process clock consumer and cannot raise ``StopIteration``.
    """

    def __init__(self, polls: int) -> None:
        self._remaining = polls
        self.sleeps = 0

    def monotonic(self) -> float:
        return 0.0 if self._remaining > 0 else 1.0

    def sleep(self, _seconds: float) -> None:
        self.sleeps += 1
        self._remaining -= 1


def _poll_broker(responses: list[Any], monkeypatch: pytest.MonkeyPatch) -> tuple[AlpacaBroker, _FakeClock]:
    """Broker whose order lookup walks ``responses`` then always fails."""
    broker = AlpacaBroker.__new__(AlpacaBroker)
    polls = iter(responses)

    def get_order_by_id(_order_id: str) -> Any:
        try:
            response = next(polls)
        except StopIteration:
            raise TimeoutError("transient lookup failure")
        if isinstance(response, Exception):
            raise response
        return response

    broker._client = SimpleNamespace(get_order_by_id=get_order_by_id)  # type: ignore[attr-defined]
    # Patch this module's ``time`` binding only: the stdlib module object stays
    # intact for every other consumer in the process.
    clock = _FakeClock(polls=len(responses))
    monkeypatch.setattr(alpaca_module, "time", clock)
    return broker, clock


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
    broker, clock = _poll_broker([_partial_order(), TimeoutError("boom")], monkeypatch)

    state, fill = broker._wait_for_fill("broker-1", _intent(), timeout=1.0)

    assert state is OrderState.PARTIALLY_FILLED
    assert fill is not None and fill.qty == 1.0
    assert clock.sleeps == 2


def test_wait_for_fill_does_not_claim_a_fee_from_the_order_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``alpaca.trading.models.Order`` has no ``fees`` field; the fill-activity
    reconciler is the authoritative fee source, so the order lookup must not
    assert one."""
    broker, _clock = _poll_broker([_partial_order(), TimeoutError("boom")], monkeypatch)

    _state, fill = broker._wait_for_fill("broker-1", _intent(), timeout=1.0)

    assert fill is not None
    assert fill.commission == 0.0


def test_a_fresh_accepted_status_still_downgrades_the_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine non-terminal, non-partial observation is new information and
    must still be reported as ``ACCEPTED``."""
    accepted = SimpleNamespace(id="broker-1", status="accepted", filled_qty=0, filled_avg_price=0)
    broker, clock = _poll_broker([_partial_order(), accepted], monkeypatch)

    state, _fill = broker._wait_for_fill("broker-1", _intent(), timeout=1.0)

    assert state is OrderState.ACCEPTED
    assert clock.sleeps == 2


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


def _portfolio_request() -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id="cid-dfd",
        decision_id="dec-1",
        symbol="AAPL",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=1.0,
        reference_price=100.0,
        decision_timestamp=datetime(2026, 1, 2, 14, 0, tzinfo=UTC),
        strategy_name="test",
        strategy_version="v1",
        risk_reducing=False,
    )


def test_portfolio_submit_done_for_day_lookup_reraises_the_submit_failure() -> None:
    """``done_for_day`` is a broker-confirmed terminal response: the order is
    dead for the session, so a portfolio submit must surface the broker's own
    rejection instead of returning the dead order as a successful submit."""
    submit_error = ValueError("422 insufficient buying power")
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(  # type: ignore[attr-defined]
        submit_order=lambda _request: (_ for _ in ()).throw(submit_error),
        get_order_by_client_id=lambda _cid: SimpleNamespace(id="b-1", status="done_for_day"),
    )
    broker.asset_trading_rules = lambda symbol, market: AssetTradingRules(  # type: ignore[method-assign]
        symbol=symbol,
        market=market,
        minimum_order_size=1.0,
        quantity_increment=1.0,
        price_increment=0.01,
        minimum_order_notional=1.0,
    )
    broker._has_open_order = lambda _symbol: False  # type: ignore[method-assign]
    service = OrderService(broker)

    with pytest.raises(ValueError) as excinfo:
        service.submit_portfolio_order(_portfolio_request())

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


def test_recovered_submission_fill_reports_the_fee_as_unobserved() -> None:
    """An order lookup carries no fee: the recovered fill must hand ``None``
    (unobserved) to the booking callback, never an observed ``0.0`` fee."""
    recovered: list[tuple[Any, ...]] = []
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(  # type: ignore[attr-defined]
        get_order_by_client_id=lambda _cid: SimpleNamespace(
            id="b-1",
            status="filled",
            filled_qty="2",
            filled_avg_price="101.5",
            filled_at="2026-01-02T14:30:00+00:00",
        ),
    )
    broker.portfolio_fill_recovered = lambda *args, **_kwargs: recovered.append(args)

    assert broker.resolve_portfolio_submission("cid-1", "b-1") == ("filled", "b-1")

    assert len(recovered) == 1
    assert recovered[0][2:4] == (2.0, 101.5)
    assert recovered[0][6] is None


@pytest.mark.parametrize("dead_status", ["done_for_day", "replaced", "stopped", "cancelled"])
@pytest.mark.parametrize("via_service", [True, False])
def test_nested_bracket_dead_stop_leg_is_not_protection(dead_status: str, via_service: bool) -> None:
    """A bracket stop leg that can no longer execute must not count as live
    protection, in the service and in the facade fallback alike."""
    from parayabuck.execution.alpaca_protection import ProtectionService

    leg = SimpleNamespace(side="sell", type="stop", stop_price=95.0, status=dead_status, qty="2", filled_qty="0")
    parent = SimpleNamespace(symbol="AAPL", side="buy", type="market", stop_price=None, status="filled", legs=[leg])
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(get_order_by_id=lambda _order_id, _request=None: parent)  # type: ignore[attr-defined]
    if via_service:
        broker.__dict__["_protection_service"] = ProtectionService(broker)

    assert broker._has_nested_bracket_protection("parent-1", "AAPL", True, 2.0) is False
    assert broker._last_protection_mismatch == "missing"
