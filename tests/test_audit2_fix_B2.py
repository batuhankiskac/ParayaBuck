"""Audit wave 2 fix B2: broker idempotency, leg residuals, replan holds, test gaps.

Deterministic, tmp_path-only, no network or credentials. Each test pins one
finding (P2-3-*, P2-4-*, F2-6-*, Q2-8-* in docs/audit/raw-r7.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.trading.enums import OrderSide

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.alpaca_models import AmbiguousOrderState, DefiniteOrderRejection
from parayabuck.execution.alpaca_orders import OrderService
from parayabuck.execution.alpaca_reconciliation import RECONCILIATION_CATEGORIES, ReconciliationService
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    floor_increment,
    validate_order_batch,
)
from parayabuck.portfolio.base import PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_models import REBALANCE_PHASE_BUY_PENDING, OpenPortfolioOrder
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.portfolio.runtime_service import PortfolioRuntime, _sell_leg_owed_quantity
from parayabuck.runtime import EngineRuntime
from tests import test_audit_fix_W05 as w05
from tests.helpers import make_broker

ACCOUNT = "acct"
SYMBOL = "QLD"
T0 = datetime(2026, 9, 1, tzinfo=UTC)


class _NotFound(Exception):
    status_code = 404


class _Refused(Exception):
    status_code = 422


# --------------------------------------------------------------------------- #
# P2-3-2: a 404 lookup proves a non-submission only for a broker refusal
# --------------------------------------------------------------------------- #


def _service(tmp_path: Path, submit_error: Exception) -> OrderService:
    client = SimpleNamespace(
        submit_order=lambda _request: (_ for _ in ()).throw(submit_error),
        get_order_by_client_id=lambda _cid: (_ for _ in ()).throw(_NotFound("order not found")),
    )
    return OrderService(make_broker(tmp_path, client=client))


def test_timeout_followed_by_a_404_lookup_stays_ambiguous(tmp_path: Path) -> None:
    """A transport timeout whose immediate lookup 404s may still have been
    accepted (the lookup can lag), so it must not be booked as a rejection."""
    with pytest.raises(AmbiguousOrderState):
        _service(tmp_path, TimeoutError("read timed out"))._submit_with_recovery(SimpleNamespace(), "cid-t")


def test_broker_refusal_followed_by_a_404_lookup_is_definite(tmp_path: Path) -> None:
    refusal = _Refused("422 insufficient buying power")
    with pytest.raises(DefiniteOrderRejection) as excinfo:
        _service(tmp_path, refusal)._submit_with_recovery(SimpleNamespace(), "cid-r")
    assert excinfo.value.__cause__ is refusal


# --------------------------------------------------------------------------- #
# P2-4-1 (partial): a same-id broker order is adopted only when it is this order
# --------------------------------------------------------------------------- #


def _recovery_broker(tmp_path: Path, recovered: Any, fills: list[Any]) -> Any:
    client = SimpleNamespace(
        submit_order=lambda _request: (_ for _ in ()).throw(TimeoutError("read timed out")),
        get_order_by_client_id=lambda _cid: recovered,
    )
    return make_broker(
        tmp_path,
        client=client,
        portfolio_fill_recovered=lambda *args, **_kwargs: fills.append(args),
    )


def test_recovered_order_with_another_quantity_is_not_adopted(tmp_path: Path) -> None:
    request = SimpleNamespace(symbol=SYMBOL, qty=12.0, side=OrderSide.BUY)
    other = SimpleNamespace(
        id="b-9", client_order_id="cid-q", symbol=SYMBOL, side="buy", qty="10", status="filled", filled_qty="10"
    )
    fills: list[Any] = []
    broker = _recovery_broker(tmp_path, other, fills)

    with pytest.raises(AmbiguousOrderState):
        broker._submit_with_recovery(request, "cid-q")
    assert fills == []


def test_recovered_order_matching_the_request_is_adopted(tmp_path: Path) -> None:
    request = SimpleNamespace(symbol=SYMBOL, qty=12.0, side=OrderSide.BUY)
    same = SimpleNamespace(id="b-8", client_order_id="cid-m", symbol=SYMBOL, side="buy", qty="12", status="accepted")
    broker = _recovery_broker(tmp_path, same, [])

    assert broker._submit_with_recovery(request, "cid-m") is same


# --------------------------------------------------------------------------- #
# P2-3-4: a submit call still in flight is not canceled as an unknown order
# --------------------------------------------------------------------------- #


def _local_order(**overrides: Any) -> OpenPortfolioOrder:
    fields: dict[str, Any] = {
        "client_order_id": "cid-f",
        "symbol": SYMBOL,
        "market": Market.EQUITY,
        "side": "buy",
        "quantity": 5.0,
        "cumulative_filled_quantity": 0.0,
        "status": "submitting",
        "last_updated_at": datetime.now(UTC) - timedelta(seconds=3600),
    }
    fields.update(overrides)
    return OpenPortfolioOrder(**fields)


def test_in_flight_submit_call_is_not_canceled_as_unknown() -> None:
    service = ReconciliationService(SimpleNamespace(_client=SimpleNamespace(get_order_by_client_id=lambda _c: None)))
    canceled: list[Any] = []

    def lookup(_client_id: str, _broker_id: str) -> None:
        return None

    def record(*args: Any, **kwargs: Any) -> None:
        canceled.append(args)

    service._cancel_unknown_portfolio_order(_local_order(submit_call_in_flight=True), "cid-f", lookup, record)
    assert canceled == []

    service._cancel_unknown_portfolio_order(_local_order(submit_call_in_flight=False), "cid-f", lookup, record)
    assert len(canceled) == 1


def test_open_orders_report_a_submit_call_in_flight_until_it_returns(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    decision_id = _decision(repo, T0)
    _record_sell(repo, decision_id, "cid-flight", 5.0, "submitting", T0)
    started = datetime.now(UTC) - timedelta(seconds=30)
    repo.record_submit_call_started("cid-flight", started)

    (order,) = repo.open_orders(ACCOUNT, Market.EQUITY)
    assert order.submit_call_in_flight is True

    repo.record_submit_call_returned("cid-flight", datetime.now(UTC))
    (order,) = repo.open_orders(ACCOUNT, Market.EQUITY)
    assert order.submit_call_in_flight is False


# --------------------------------------------------------------------------- #
# Ledger helpers shared by the runtime tests below
# --------------------------------------------------------------------------- #


def _decision(repo: PortfolioStateRepository, when: datetime) -> str:
    target = TargetPortfolio("vt35", "1", when, (), 1.0)
    session = repo.begin_session(ACCOUNT, "digest", "paper")
    return repo.record_decision_with_orders(
        session,
        ACCOUNT,
        "digest",
        target,
        _risk(target),
        Market.EQUITY,
        (),
    )


def _risk(target: TargetPortfolio) -> Any:
    from parayabuck.execution.portfolio import RiskOverlayDecision

    return RiskOverlayDecision(target, False, (), 0.0, 0.0)


def _record_sell(
    repo: PortfolioStateRepository,
    decision_id: str,
    client_order_id: str,
    quantity: float,
    status: str,
    when: datetime,
    *,
    symbol: str = SYMBOL,
) -> None:
    order = PortfolioOrderRequest(
        client_order_id=client_order_id,
        decision_id=decision_id,
        symbol=symbol,
        market=Market.EQUITY,
        side=PortfolioOrderSide.SELL,
        quantity=quantity,
        reference_price=100.0,
        decision_timestamp=when,
        strategy_name="vt35",
        strategy_version="1",
    )
    repo.record_order(order, status)


def _fill(repo: PortfolioStateRepository, client_order_id: str, cumulative: float, price: float, status: str) -> None:
    repo.apply_trade_update(
        client_order_id=client_order_id,
        broker_order_id=f"b-{client_order_id}",
        status=status,
        cumulative_filled_quantity=cumulative,
        average_fill_price=price,
        occurred_at=T0,
        activity_id=f"act-{client_order_id}-{cumulative}",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


class _Strategy(PortfolioStrategy):
    """Fixed-target strategy; the runtime here only reads its identity and never decides."""

    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: Any) -> TargetPortfolio:
        return TargetPortfolio(self.name, self.version, context.decision_timestamp, (), 1.0, "test")

    def requires_intermediate_risk_rebalance(self, context: Any) -> bool:
        return True


class _Broker:
    def __init__(self) -> None:
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        position = PositionSnapshot(SYMBOL, 10.0, 100.0, 100.0, Market.EQUITY, qty_available=10.0)
        return AccountSnapshot(cash=0.0, equity=100_000.0, peak_equity=100_000.0, daily_pnl=0.0, positions=(position,))

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(1.0, 1.0, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def working_buy_orders(self) -> tuple[()]:
        return ()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


class _Universe:
    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return True


def _runtime(tmp_path: Path, repo: PortfolioStateRepository | None = None) -> PortfolioRuntime:
    return PortfolioRuntime(
        _Strategy(),
        (SYMBOL,),
        _Broker(),  # type: ignore[arg-type]
        repo or PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        account_id=ACCOUNT,
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
        universe=_Universe(),  # type: ignore[arg-type]
        universe_version="test-universe-v1",
    )


# --------------------------------------------------------------------------- #
# P2-4-2 / F2-6-2: the residual is sized from the whole leg, not the tail row
# --------------------------------------------------------------------------- #


def test_filled_retry_shortfall_is_owed_as_a_residual(tmp_path: Path) -> None:
    """A base SELL rejected for qty_available is retried at the available 4 of 10.
    The retry fills in full, so 6 shares are still owed and must be re-sent."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = _runtime(tmp_path, repo)
    decision_id = _decision(repo, T0)
    _record_sell(repo, decision_id, "pbp-root", 10.0, "rejected", T0)
    _record_sell(repo, decision_id, "pbp-root-retry1", 4.0, "submitted", T0)
    _fill(repo, "pbp-root-retry1", 4.0, 100.0, "fill")
    sells = repo.orders_for_decision(decision_id, side="sell")

    result = runtime._residual_sell_result(decision_id, tuple(sells))

    assert result is not None
    assert result.status == "residual_sell_submitted"
    sent = runtime.broker.submitted  # type: ignore[attr-defined]
    assert [order.client_order_id for order in sent] == ["pbp-root-residual1"]
    assert sent[0].quantity == pytest.approx(6.0)


def test_completely_filled_base_leaves_no_residual(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = _runtime(tmp_path, repo)
    decision_id = _decision(repo, T0)
    _record_sell(repo, decision_id, "pbp-full", 10.0, "submitted", T0)
    _fill(repo, "pbp-full", 10.0, 100.0, "fill")

    assert runtime._residual_sell_result(decision_id, tuple(repo.orders_for_decision(decision_id, side="sell"))) is None


def test_leg_owed_quantity_subtracts_every_fill_of_the_leg() -> None:
    rows = [
        {"client_order_id": "x", "quantity": 10.0, "cumulative_filled_quantity": 3.0},
        {"client_order_id": "x-residual1", "quantity": 7.0, "cumulative_filled_quantity": 2.0},
    ]
    assert _sell_leg_owed_quantity(rows, "x", rows[1]) == pytest.approx(5.0)


# --------------------------------------------------------------------------- #
# P2-3-1: a re-plan waits while an earlier re-plan row of the order is unresolved
# --------------------------------------------------------------------------- #


def _planned_sell(client_order_id: str) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id=client_order_id,
        decision_id="",
        symbol=SYMBOL,
        market=Market.EQUITY,
        side=PortfolioOrderSide.SELL,
        quantity=5.0,
        reference_price=100.0,
        decision_timestamp=T0,
        strategy_name="vt35",
        strategy_version="1",
    )


@pytest.mark.parametrize(
    ("replan_status", "held"),
    [
        ("reconciliation_required", True),
        ("submitting", True),
        ("canceled", False),
    ],
)
def test_replan_is_held_while_an_earlier_replan_row_is_unresolved(
    tmp_path: Path, replan_status: str, held: bool
) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = _runtime(tmp_path, repo)
    decision_id = _decision(repo, T0)
    _record_sell(repo, decision_id, "pbp-leg", 5.0, "canceled", T0)
    _record_sell(repo, decision_id, "pbp-leg-replan1", 5.0, replan_status, T0)

    in_flight = runtime._replan_ids_in_flight(decision_id, [_planned_sell("pbp-leg")])

    assert (in_flight == ("pbp-leg",)) is held


# --------------------------------------------------------------------------- #
# P2-4-3: the lock-free retry candidate mirrors the ledger-only skip rules
# --------------------------------------------------------------------------- #


def _candidate(runtime: PortfolioRuntime) -> bool:
    return EngineRuntime._exit_sell_retry_candidate(SimpleNamespace(), runtime)


def test_rejected_sell_sharing_its_symbol_with_another_sell_is_not_a_candidate(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = _runtime(tmp_path, repo)
    decision_id = _decision(repo, T0)
    _record_sell(repo, decision_id, "pbp-rejected", 5.0, "rejected", T0)
    _record_sell(repo, decision_id, "pbp-other", 2.0, "submitted", T0)
    # The retry skips a rejected SELL whose symbol another SELL of the decision
    # already carries; the lock-free candidate check must agree.
    assert _candidate(runtime) is False


def test_rejected_sell_with_no_competing_order_is_a_candidate(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = _runtime(tmp_path, repo)
    decision_id = _decision(repo, T0)
    _record_sell(repo, decision_id, "pbp-alone", 5.0, "rejected", T0)

    assert _candidate(runtime) is True


# --------------------------------------------------------------------------- #
# Q2-8-5: floor_increment never rounds a negative value toward zero
# --------------------------------------------------------------------------- #


def test_floor_increment_refuses_a_negative_value() -> None:
    with pytest.raises(ValueError):
        floor_increment(-0.5, 1.0)


@pytest.mark.parametrize("increment", [1.0, 0.01, 0.0001])
def test_floored_grid_values_pass_the_order_validator(increment: float) -> None:
    rules = {
        SYMBOL: AssetTradingRules(
            minimum_order_size=increment, quantity_increment=increment, minimum_order_notional=0.0
        )
    }
    for step in range(1, 200):
        value = step * 0.37
        quantity = floor_increment(value, increment)
        if quantity < increment:
            continue
        order = PortfolioOrderRequest(
            client_order_id=f"g-{step}",
            decision_id="d",
            symbol=SYMBOL,
            market=Market.EQUITY,
            side=PortfolioOrderSide.BUY,
            quantity=quantity,
            reference_price=100.0,
            decision_timestamp=T0,
            strategy_name="vt35",
            strategy_version="1",
        )
        assert validate_order_batch((order,), rules, minimum_order_notional=0.0) == []


# --------------------------------------------------------------------------- #
# Q2-8-6: the owned quantity equals the sum of the open FIFO lots
# --------------------------------------------------------------------------- #


def test_owned_quantity_equals_the_sum_of_open_fifo_lots(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    decision_id = _decision(repo, T0)
    _record_buy(repo, decision_id, "pbp-b1", 10.0, T0)
    _fill(repo, "pbp-b1", 10.0, 100.0, "fill")
    _record_sell(repo, decision_id, "pbp-s1", 4.0, "submitted", T0)
    _fill(repo, "pbp-s1", 4.0, 105.0, "fill")
    _record_buy(repo, decision_id, "pbp-b2", 3.0, T0 + timedelta(minutes=1))
    _fill(repo, "pbp-b2", 3.0, 101.0, "fill")

    owned = repo.owned_quantities(ACCOUNT, Market.EQUITY)[SYMBOL]
    lots = repo.owned_fifo_lots(ACCOUNT, SYMBOL, Market.EQUITY)

    assert owned == pytest.approx(9.0)
    assert sum(quantity for quantity, _cost in lots) == pytest.approx(owned)


def _record_buy(
    repo: PortfolioStateRepository, decision_id: str, client_order_id: str, quantity: float, when: datetime
) -> None:
    order = PortfolioOrderRequest(
        client_order_id=client_order_id,
        decision_id=decision_id,
        symbol=SYMBOL,
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=quantity,
        reference_price=100.0,
        decision_timestamp=when,
        strategy_name="vt35",
        strategy_version="1",
    )
    repo.record_order(order, "submitted")


# --------------------------------------------------------------------------- #
# Q2-8-1: two arrival orders of the same updates book the same quantity and cost
# --------------------------------------------------------------------------- #


def _book(tmp_path: Path, name: str, updates: list[tuple[float, float]]) -> tuple[float, float]:
    repo = PortfolioStateRepository(tmp_path / f"{name}.sqlite3")
    decision_id = _decision(repo, T0)
    _record_buy(repo, decision_id, "pbp-perm", 12.0, T0)
    for cumulative, average in updates:
        _fill(repo, "pbp-perm", cumulative, average, "partial_fill")
    lots = repo.owned_fifo_lots(ACCOUNT, SYMBOL, Market.EQUITY)
    quantity = sum(lot_quantity for lot_quantity, _cost in lots)
    cost = sum(lot_quantity * lot_cost for lot_quantity, lot_cost in lots)
    return quantity, cost


def test_arrival_order_keeps_booked_quantity_and_cost_of_the_same_updates(tmp_path: Path) -> None:
    in_order = _book(tmp_path, "in_order", [(10.0, 102.0), (12.0, 101.0)])
    stale_last = _book(tmp_path, "stale_last", [(12.0, 101.0), (10.0, 102.0)])

    # Total quantity and total cost agree: 12 shares at an average of 101.
    assert in_order[0] == pytest.approx(12.0)
    assert stale_last[0] == pytest.approx(12.0)
    assert in_order[1] == pytest.approx(12.0 * 101.0)
    assert stale_last[1] == pytest.approx(12.0 * 101.0)


# --------------------------------------------------------------------------- #
# P2-3-3: a gate hold after a BUY of the same batch went out does not re-plan it
# --------------------------------------------------------------------------- #


def test_gate_hold_after_a_buy_went_out_leaves_the_batch_tracked_not_re_planned(tmp_path: Path) -> None:
    broker = w05._RulesBroker((w05._position("AAPL", 400),))
    runtime, repository, first = w05._sell_decision(
        tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20, "NVDA": 0.20}, ("AAPL", "MSFT", "NVDA")
    )
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    w05._fill(repository, sell)
    broker.positions = [w05._position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")

    held = runtime.advance_rebalance(submit_gate=lambda order: "halted" if order.symbol == "NVDA" else None)

    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.BUY] == ["MSFT"]
    assert held is not None and held.status == "buy_phase_submitted"
    assert w05._phase(repository, runtime) == REBALANCE_PHASE_BUY_PENDING


# --------------------------------------------------------------------------- #
# F2-6-1: a clean reconciliation advances every market's rebalance
# --------------------------------------------------------------------------- #


def _reconcile_stub(advance: Any) -> Any:
    return SimpleNamespace(
        broker=SimpleNamespace(
            reconcile=lambda: {category: [] for category in RECONCILIATION_CATEGORIES}, account_id=ACCOUNT
        ),
        _portfolio_runtimes=[SimpleNamespace(market=Market.EQUITY), SimpleNamespace(market=Market.EQUITY)],
        _clear_reconciliation_incidents=lambda: None,
        _advance_portfolio_rebalance=advance,
        _reconcile_failed=False,
    )


def test_clean_reconciliation_advances_each_market_once() -> None:
    advanced: list[Market] = []
    engine = _reconcile_stub(lambda market, **_kwargs: advanced.append(market))

    EngineRuntime._reconcile_safely(engine)

    assert advanced == [Market.EQUITY]


def test_a_failing_rebalance_advance_does_not_fail_the_reconciliation() -> None:
    def failing(market: Market, **_kwargs: Any) -> None:
        raise RuntimeError("advance failed")

    engine = _reconcile_stub(failing)

    EngineRuntime._reconcile_safely(engine)

    assert engine._last_reconciliation_at is not None
