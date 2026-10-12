"""Regressions for the execution-boundary audit: local pre-submit guards,
trade-update parsing, per-execution fill prices and asset metadata."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.alpaca_client import AlpacaClient
from parayabuck.execution.alpaca_models import DefiniteOrderRejection
from parayabuck.execution.alpaca_orders import OrderService
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream, TradeUpdate
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    RiskOverlayDecision,
)
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from tests.helpers import make_broker

_AT = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def _order(quantity: float = 2.0, reference_price: float = 100.0) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id="pb-guard",
        decision_id="dec-1",
        symbol="QLD",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=quantity,
        reference_price=reference_price,
        decision_timestamp=_AT,
        strategy_name="eq",
        strategy_version="1",
    )


def _guarded_broker(tmp_path: Path, *, open_order: bool = False, tradable: bool = True) -> Any:
    submitted: list[Any] = []
    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            submit_order=lambda request: submitted.append(request),
            get_asset=lambda _symbol: SimpleNamespace(
                tradable=tradable,
                fractionable=True,
                min_order_size="1",
                min_trade_increment="0.5",
                price_increment="0.01",
                min_order_notional="10",
            ),
        ),
    )
    broker._has_open_order = lambda _symbol: open_order
    broker.submitted = submitted
    return broker


@pytest.mark.parametrize(
    ("order", "kwargs", "reason"),
    [
        (_order(quantity=0.9), {}, "below minimum size"),
        (_order(quantity=1.0, reference_price=5.0), {}, "below minimum notional"),
        (_order(), {"open_order": True}, "open order already exists"),
        (_order(), {"tradable": False}, "not tradable"),
    ],
)
def test_local_pre_submit_guards_are_definite_rejections(
    tmp_path: Path, order: PortfolioOrderRequest, kwargs: dict[str, bool], reason: str
) -> None:
    """Nothing reached the broker: the runtime must book ``rejected`` and
    re-plan, not halt on a plain ValueError."""
    broker = _guarded_broker(tmp_path, **kwargs)

    with pytest.raises(DefiniteOrderRejection, match=reason):
        OrderService(broker).submit_portfolio_order(order)

    assert broker.submitted == []


def test_non_fractionable_asset_trades_whole_shares(tmp_path: Path) -> None:
    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            get_asset=lambda _symbol: SimpleNamespace(
                tradable=True, fractionable=False, min_order_size=None, min_trade_increment=None
            )
        ),
    )
    rules = broker.asset_trading_rules("QLD", Market.EQUITY)
    assert (rules.minimum_order_size, rules.quantity_increment) == (1.0, 1.0)


def test_asset_metadata_is_refreshed_once_a_day() -> None:
    assets = iter([SimpleNamespace(tradable=True), SimpleNamespace(tradable=False)])
    calls: list[str] = []

    def get_asset(symbol: str) -> Any:
        calls.append(symbol)
        return next(assets)

    adapter = AlpacaClient(client=SimpleNamespace(get_asset=get_asset))
    now = [1000.0]
    adapter._clock = lambda: now[0]

    assert adapter._asset("QLD").tradable is True
    now[0] += 3600.0
    assert adapter._asset("QLD").tradable is True  # cached within the day
    now[0] += 24 * 3600.0
    assert adapter._asset("QLD").tradable is False  # re-read after a day
    assert calls == ["QLD", "QLD"]


def _stream(handled: list[TradeUpdate]) -> AlpacaTradingUpdateStream:
    async def handler(update: TradeUpdate) -> None:
        handled.append(update)

    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    stream._handler = handler
    return stream


def test_malformed_trade_update_is_dropped_without_raising_into_the_sdk() -> None:
    handled: list[TradeUpdate] = []
    stream = _stream(handled)
    malformed = SimpleNamespace(
        event="fill",
        timestamp=_AT,
        order=SimpleNamespace(symbol="QLD", client_order_id="pb-1", id="b-1", filled_qty="not-a-number"),
    )

    asyncio.run(stream._on_update(malformed))

    assert handled == []


def test_well_formed_trade_update_reaches_the_handler() -> None:
    handled: list[TradeUpdate] = []
    stream = _stream(handled)
    raw = SimpleNamespace(
        event="partial_fill",
        timestamp=_AT,
        execution_id="exec-1",
        order=SimpleNamespace(
            symbol="QLD",
            client_order_id="pb-1",
            id="b-1",
            filled_qty="2",
            filled_avg_price="101.5",
            asset_class="us_equity",
        ),
    )

    asyncio.run(stream._on_update(raw))

    (update,) = handled
    assert (update.event, update.symbol, update.filled_qty, update.average_fill_price, update.activity_id) == (
        "partial_fill",
        "QLD",
        2.0,
        101.5,
        "exec-1",
    )


def test_second_partial_fill_is_recorded_at_its_own_execution_price(tmp_path: Path) -> None:
    """Broker updates carry the cumulative average; the second execution of
    3 @ 100 then 2 @ 105 (cumulative 5 @ 102) paid 105, not 102."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session_id = repo.begin_session("acct", "digest", "paper")
    target = TargetPortfolio(
        weights=(), cash_weight=1.0, decision_timestamp=_AT, strategy_name="eq", strategy_version="1"
    )
    decision_id = repo.record_decision_with_orders(
        session_id, "acct", "digest", target, RiskOverlayDecision(target, False, (), 0.0, 0.0), Market.EQUITY, ()
    )
    repo.record_order(dataclasses.replace(_order(quantity=5.0), decision_id=decision_id), "submitted")
    for cumulative, average, execution in ((3.0, 100.0, "e1"), (5.0, 102.0, "e2")):
        repo.apply_trade_update(
            client_order_id="pb-guard",
            broker_order_id="b-1",
            status="partial_fill" if cumulative < 5 else "fill",
            cumulative_filled_quantity=cumulative,
            average_fill_price=average,
            occurred_at=_AT,
            activity_id=execution,
            fee=None,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )

    fills = repo.store.connection.execute(
        "SELECT activity_id, quantity, price, slippage FROM portfolio_fills ORDER BY activity_id"
    ).fetchall()
    assert [(row[0], row[1]) for row in fills] == [("e1", 3.0), ("e2", 2.0)]
    assert fills[0][2] == pytest.approx(100.0)
    assert fills[1][2] == pytest.approx(105.0)
    assert fills[1][3] == pytest.approx((105.0 - 100.0) * 2.0)  # BUY slippage vs the 100 reference
