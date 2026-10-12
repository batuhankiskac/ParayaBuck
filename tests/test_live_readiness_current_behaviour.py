"""Pin the order-path behaviour that docs/live_readiness_v1.md relies on.

These tests document CURRENT behaviour (not desired LIVE behaviour) so a fix
wave that changes it has to update the readiness doc deliberately:

- orders are DAY market orders by share quantity (no OPG, no notional);
- a fractionable asset gets a fractional quantity, a non-fractionable one
  is floored to whole shares;
- a broker 403 (for example Alpaca's pattern-day-trading protection) whose
  client id was never accepted is a definite rejection, never a retry;
- on a small account the $10 minimum order notional, not the 0.5 pp band,
  decides whether a resize trades.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from requests import Response
from requests.exceptions import HTTPError

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.alpaca_models import DefiniteOrderRejection
from parayabuck.execution.alpaca_orders import OrderService
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    TargetToOrderPlanner,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from tests.helpers import make_broker

DECISION_AT = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)


class _RecordingBroker:
    """Minimal broker facade for OrderService: records the request object."""

    def __init__(self, rules: AssetTradingRules) -> None:
        self.rules = rules
        self.requests: list[Any] = []

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        return getattr(value, name, default)

    def _has_open_order(self, symbol: str) -> bool:
        return False

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return self.rules

    def _submit_with_recovery(self, request: Any, client_order_id: str) -> Any:
        self.requests.append(request)
        return SimpleNamespace(id="broker-1")


def _order(quantity: float, side: PortfolioOrderSide = PortfolioOrderSide.BUY) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id="parayabuck-d1-QLD-buy",
        decision_id="d1",
        symbol="QLD",
        market=Market.EQUITY,
        side=side,
        quantity=quantity,
        reference_price=100.0,
        decision_timestamp=DECISION_AT,
        strategy_name="equity_leveraged_trend_v1",
        strategy_version="1.1.0",
    )


def test_orders_are_day_market_orders_by_fractional_quantity() -> None:
    broker = _RecordingBroker(AssetTradingRules(0.000000001, 0.000000001, 1.0))

    OrderService(broker).submit_portfolio_order(_order(1.234567891))

    (request,) = broker.requests
    assert request.type.value == "market"
    assert request.time_in_force.value == "day"  # not opg: no opening-auction order
    assert request.notional is None
    assert request.qty == pytest.approx(1.234567891)
    assert request.extended_hours in (None, False)
    assert request.client_order_id == "parayabuck-d1-QLD-buy"


def test_non_fractionable_rules_floor_to_whole_shares() -> None:
    broker = _RecordingBroker(AssetTradingRules(1.0, 1.0, 1.0))

    OrderService(broker).submit_portfolio_order(_order(3.9))

    assert broker.requests[0].qty == 3.0


def test_whole_share_floor_below_one_share_is_a_local_rejection() -> None:
    broker = _RecordingBroker(AssetTradingRules(1.0, 1.0, 1.0))

    with pytest.raises(DefiniteOrderRejection, match="below minimum size"):
        OrderService(broker).submit_portfolio_order(_order(0.9))
    assert broker.requests == []


def _http_403_pdt() -> HTTPError:
    error = HTTPError(
        '403 {"code": 40310100, "message": "trade denied due to pattern day trading protection"}',
        response=Response(),
    )
    error.response.status_code = 403
    error.status_code = 403  # type: ignore[attr-defined]
    return error


def _not_found() -> HTTPError:
    error = HTTPError("404 order not found", response=Response())
    error.response.status_code = 404
    error.status_code = 404  # type: ignore[attr-defined]
    return error


def test_pdt_style_403_is_a_definite_rejection_without_retry(tmp_path: Any) -> None:
    submits: list[Any] = []

    def submit(request: Any) -> Any:
        submits.append(request)
        raise _http_403_pdt()

    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            submit_order=submit,
            get_order_by_client_id=lambda _cid: (_ for _ in ()).throw(_not_found()),
        ),
    )

    with pytest.raises(DefiniteOrderRejection, match="pattern day trading"):
        broker._submit_with_recovery(SimpleNamespace(), "cid-pdt")
    assert len(submits) == 1


def _qld_account(*, equity: float, weight: float, price: float = 100.0) -> AccountSnapshot:
    qty = equity * weight / price
    position = PositionSnapshot(
        "QLD",
        qty,
        price,
        price,
        Market.EQUITY,
        qty_available=qty,
        ownership="managed",
        managed_qty=qty,
    )
    return AccountSnapshot(
        cash=equity - qty * price,
        equity=equity,
        peak_equity=equity,
        daily_pnl=0.0,
        positions=(position,),
    )


def _plan(*, equity: float, held: float, target: float) -> tuple[PortfolioOrderRequest, ...]:
    return TargetToOrderPlanner(PortfolioExecutionPolicy(rebalance_band_pct=0.005, minimum_order_notional=10.0)).plan(
        TargetPortfolio(
            strategy_name="equity_leveraged_trend_v1",
            strategy_version="1.1.0",
            decision_timestamp=DECISION_AT,
            weights=(TargetWeight("QLD", target),),
            cash_weight=1.0 - target,
            universe_version="test",
        ),
        _qld_account(equity=equity, weight=held),
        market=Market.EQUITY,
        prices={"QLD": 100.0},
        rules={"QLD": AssetTradingRules(0.000000001, 0.000000001, 1.0)},
        approved_symbols=("QLD",),
        decision_id="d1",
    )


def test_small_account_resize_above_band_but_below_ten_dollars_is_skipped() -> None:
    # $1,000 account: a 0.8 pp drift clears the 0.5 pp band but is only $8.
    assert _plan(equity=1_000.0, held=0.80, target=0.808) == ()
    # A 1.2 pp drift ($12) trades.
    (order,) = _plan(equity=1_000.0, held=0.80, target=0.812)
    assert order.side == PortfolioOrderSide.BUY
    assert order.quantity * order.reference_price == pytest.approx(12.0)


def test_mid_account_resize_just_above_band_trades() -> None:
    # $25,000 account: a 0.6 pp drift is $150, so the band alone decides.
    (order,) = _plan(equity=25_000.0, held=0.80, target=0.794)
    assert order.side == PortfolioOrderSide.SELL
    assert order.quantity * order.reference_price == pytest.approx(150.0)
