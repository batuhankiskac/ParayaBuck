"""Order-boundary invariants: fee authority, recovery, protection legs.

Real ``AlpacaBroker`` instances built with ``tests.helpers.make_broker`` and
scripted in-process clients — no network, no credentials, no production paths.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.alpaca import AlpacaBroker, AmbiguousOrderState
from parayabuck.execution.alpaca_orders import OrderService
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest, PortfolioOrderSide
from tests.helpers import make_broker


class _NotFound(Exception):
    """A broker 404 as the SDK models it."""

    status_code = 404


def test_portfolio_submit_404_lookup_reraises_the_submit_failure(tmp_path: Path) -> None:
    """The broker's 404 proves the client id never reached Alpaca, so the
    operator must see the submit rejection, not the lookup's 404."""
    submit_error = ValueError("422 insufficient buying power")

    def submit(_request: Any) -> Any:
        raise submit_error

    def lookup(_client_order_id: str) -> Any:
        raise _NotFound("order not found")

    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            submit_order=submit,
            get_order_by_client_id=lookup,
        ),
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


def test_portfolio_submit_done_for_day_lookup_reraises_the_submit_failure(tmp_path: Path) -> None:
    """``done_for_day`` is a broker-confirmed terminal response: the order is
    dead for the session, so a portfolio submit must surface the broker's own
    rejection instead of returning the dead order as a successful submit."""
    submit_error = ValueError("422 insufficient buying power")
    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            submit_order=lambda _request: (_ for _ in ()).throw(submit_error),
            get_order_by_client_id=lambda _cid: SimpleNamespace(id="b-1", status="done_for_day"),
        ),
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


def test_portfolio_submit_ambiguous_lookup_raises_ambiguous_state(tmp_path: Path) -> None:
    """A lookup failure that is not a 404 leaves the broker write outcome
    unknown; the shared recovery decision must report it as ambiguous."""
    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            submit_order=lambda _request: (_ for _ in ()).throw(TimeoutError("network timeout")),
            get_order_by_client_id=lambda _cid: (_ for _ in ()).throw(TimeoutError("lookup timeout")),
        ),
    )
    service = OrderService(broker)

    with pytest.raises(AmbiguousOrderState):
        service._submit_with_recovery(SimpleNamespace(), "cid-1")


def test_recovered_submission_fill_reports_the_fee_as_unobserved(tmp_path: Path) -> None:
    """An order lookup carries no fee: the recovered fill must hand ``None``
    (unobserved) to the booking callback, never an observed ``0.0`` fee."""
    recovered: list[tuple[Any, ...]] = []
    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            get_order_by_client_id=lambda _cid: SimpleNamespace(
                id="b-1",
                status="filled",
                filled_qty="2",
                filled_avg_price="101.5",
                filled_at="2026-01-02T14:30:00+00:00",
            ),
        ),
        portfolio_fill_recovered=lambda *args, **_kwargs: recovered.append(args),
    )

    assert broker.resolve_portfolio_submission("cid-1", "b-1") == ("filled", "b-1")

    assert len(recovered) == 1
    assert recovered[0][2:4] == (2.0, 101.5)
    assert recovered[0][6] is None


def test_terminal_partial_fill_is_booked_from_the_live_recovery_path(tmp_path: Path) -> None:
    """The live owner of fill extraction: an order that ended terminal after
    filling still books its real fill (quantity, price, unobserved fee) and
    falls back to the last update when the payload carries no ``filled_at``."""
    recovered: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            get_order_by_client_id=lambda _cid: SimpleNamespace(
                id="b-1",
                status="expired",
                filled_qty="1",
                filled_avg_price="101.5",
                filled_at=None,
                updated_at="2026-01-02T14:30:00+00:00",
            ),
        ),
        portfolio_fill_recovered=lambda *args, **kwargs: recovered.append((args, kwargs)),
    )

    assert broker.resolve_portfolio_submission("cid-1", "b-1") == ("expired", "b-1")

    assert len(recovered) == 1
    args, kwargs = recovered[0]
    assert args[2:4] == (1.0, 101.5)
    assert args[4] == datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    assert args[6] is None
    assert kwargs["status"] == "expired"


def test_facade_exposes_no_dead_fill_or_position_internals() -> None:
    """Fill polling and single-position lookup have no production owner: the
    strategy decides at the close and fills at the next open, so a live caller
    could only ever reach them on an uninitialised double. The facade must not
    keep them as dead surface."""
    for removed in (
        "_wait_for_fill",
        "_position_qty",
        "_submit_stop",
        "_has_protective_order",
        "_has_nested_bracket_protection",
        "_cancel_protective_orders",
    ):
        assert not hasattr(AlpacaBroker, removed), f"AlpacaBroker still exposes dead {removed}"
