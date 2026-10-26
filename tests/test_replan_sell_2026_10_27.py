"""Re-planned SELL residuals, stuck submitting rows and order-batch validation.

A deferred rebalance whose re-plan (after the sell phase) still needs a SELL
submits that residual first under a fresh client id and holds the BUY side
until it settles. A ``submitting`` row whose broker lookup stays unresolved
past the threshold is escalated to ``reconciliation_required`` and the
operator is alerted once. Every planned batch is validated before any of its
orders is recorded as ``submitting``; malformed orders are never sent.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    validate_order_batch,
)
from parayabuck.monitoring import alerts
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.runtime_models import REBALANCE_PHASE_SELL_PENDING
from tests.test_two_phase_rebalance import _bars, _fill, _position, _runtime, _SimBroker


@pytest.fixture(autouse=True)
def _captured_alerts(monkeypatch):
    """Capture operator alerts instead of emitting them."""
    emitted: list[dict[str, object]] = []

    def fake_emit(event: str, **fields: object) -> None:
        emitted.append({"event": event, **fields})

    monkeypatch.setattr(alerts, "emit", fake_emit)
    return emitted


def _order(
    symbol: str,
    side: PortfolioOrderSide,
    quantity: float,
    *,
    price: float = 100.0,
    client_order_id: str | None = None,
    decision_id: str = "decision-under-test",
) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id=client_order_id or f"parayabuck-{decision_id}-{symbol}-{side.value}",
        decision_id=decision_id,
        symbol=symbol,
        market=Market.EQUITY,
        side=side,
        quantity=quantity,
        reference_price=price,
        decision_timestamp=datetime(2026, 10, 27, 14, 0, tzinfo=UTC),
        strategy_name="fixed_target",
        strategy_version="test",
    )


def _status_of(repository, decision_id: str, client_order_id: str) -> str:
    rows = repository.orders_for_decision(decision_id)
    return next(str(row["status"]) for row in rows if str(row["client_order_id"]) == client_order_id)


# (a) partial SELL, then the re-plan submits the residual with a new id and no BUY


def test_partial_sell_replan_submits_residual_sell_then_buys_after_it_settles(tmp_path) -> None:
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert first.status == "sell_phase_submitted"
    original_sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    assert original_sell.quantity == 300

    # 200 shares fill, the rest is canceled; the broker now holds 200 AAPL.
    _fill(repository, original_sell, cumulative=200.0, status="canceled", activity_id="sell-partial")
    broker.open_symbols.discard("AAPL")
    broker.positions = [_position("AAPL", 200)]
    resumed = runtime.advance_rebalance()

    assert resumed is not None and resumed.status == "replan_sell_submitted"
    assert [order.side for order in broker.submitted[1:]] == [PortfolioOrderSide.SELL]
    residual = broker.submitted[-1]
    assert residual.symbol == "AAPL"
    assert residual.quantity == 100
    assert residual.client_order_id == f"{original_sell.client_order_id}-replan1"
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    state = repository.current_rebalance_execution(runtime.account_id, runtime.market)
    assert state is not None and state.phase == REBALANCE_PHASE_SELL_PENDING

    # While the residual is working, the next advance keeps the BUY side held.
    assert runtime.advance_rebalance() is None
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)

    # The residual fills; the BUY is then re-planned against the settled account.
    _fill(repository, residual, activity_id="sell-residual")
    broker.open_symbols.discard("AAPL")
    broker.positions = [_position("AAPL", 100)]
    buy_phase = runtime.advance_rebalance()
    assert buy_phase is not None and buy_phase.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]

    _fill(repository, buys[0], activity_id="buy-msft")
    complete = runtime.advance_rebalance()
    assert complete is not None and complete.status == "rebalance_complete"


def test_residual_replan_id_is_derived_from_persisted_rows() -> None:
    """The id depends only on rows already recorded for the leg, so a restart
    that re-plans the same leg reproduces it, and each replanned row moves the
    next id on by one."""
    base = "parayabuck-d-AAPL-sell"
    assert runtime_service._replan_sell_client_order_id(base, []) == (f"{base}-replan1", 0)
    rows = [{"client_order_id": base}]
    assert runtime_service._replan_sell_client_order_id(base, rows) == (f"{base}-replan1", 0)
    rows.append({"client_order_id": f"{base}-replan1"})
    assert runtime_service._replan_sell_client_order_id(base, rows) == (f"{base}-replan2", 1)
    other = [{"client_order_id": "parayabuck-d-MSFT-sell-replan1"}]
    assert runtime_service._replan_sell_client_order_id(base, other) == (f"{base}-replan1", 0)


# (b) a re-plan without a SELL behaves as before


def test_replan_without_sell_submits_buys_as_before(tmp_path) -> None:
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell, activity_id="sell-full")
    broker.open_symbols.discard("AAPL")
    broker.positions = [_position("AAPL", 100)]

    resumed = runtime.advance_rebalance()

    assert resumed is not None and resumed.status == "buy_phase_submitted"
    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.BUY] == ["MSFT"]
    assert not any("-replan" in order.client_order_id for order in broker.submitted)


# (c) a stuck submitting row escalates after the threshold, once


def test_stuck_submitting_row_escalates_after_threshold_once(tmp_path, monkeypatch, _captured_alerts) -> None:
    broker = _SimBroker((_position("AAPL", 400),))

    def lookup_down(client_order_id: str, broker_order_id: str = "") -> str:
        raise RuntimeError("broker unreachable")

    broker.resolve_portfolio_submission = lookup_down
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    decision_id = first.decision_id
    # The sell row is back at ``submitting``: its broker acknowledgement was lost.
    repository.update_order(sell.client_order_id, "submitting")
    clock = [datetime(2026, 10, 27, 14, 0, tzinfo=UTC)]
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: clock[0])

    def resolve() -> None:
        runtime._resolve_submitting_rows(repository.orders_for_decision(decision_id))

    resolve()
    clock[0] += timedelta(seconds=runtime_service.STUCK_SUBMITTING_LOOKUP_ESCALATION_SECONDS - 1)
    resolve()
    assert _status_of(repository, decision_id, sell.client_order_id) == "submitting"
    assert not [event for event in _captured_alerts if event.get("category") == "stuck_submission_lookup"]

    clock[0] += timedelta(seconds=2)
    resolve()
    assert _status_of(repository, decision_id, sell.client_order_id) == "reconciliation_required"
    alerts_raised = [event for event in _captured_alerts if event.get("category") == "stuck_submission_lookup"]
    assert len(alerts_raised) == 1
    assert alerts_raised[0]["client_order_id"] == sell.client_order_id

    # The escalated row leaves the lookup path, so the alert is not repeated.
    resolve()
    assert len([event for event in _captured_alerts if event.get("category") == "stuck_submission_lookup"]) == 1


def test_lookup_resolved_before_threshold_never_escalates(tmp_path, monkeypatch, _captured_alerts) -> None:
    broker = _SimBroker((_position("AAPL", 400),))
    failing = [True]

    def flaky(client_order_id: str, broker_order_id: str = "") -> str | None:
        if failing[0]:
            raise RuntimeError("broker unreachable")
        return "canceled"

    broker.resolve_portfolio_submission = flaky
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    decision_id = first.decision_id
    repository.update_order(sell.client_order_id, "submitting")
    clock = [datetime(2026, 10, 27, 14, 0, tzinfo=UTC)]
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: clock[0])

    def resolve() -> None:
        runtime._resolve_submitting_rows(repository.orders_for_decision(decision_id))

    resolve()
    clock[0] += timedelta(seconds=runtime_service.STUCK_SUBMITTING_LOOKUP_ESCALATION_SECONDS - 1)
    failing[0] = False
    resolve()

    assert _status_of(repository, decision_id, sell.client_order_id) == "canceled"
    assert not [event for event in _captured_alerts if event.get("category") == "stuck_submission_lookup"]


# (d) validate_order_batch flags each bad case


RULES = {"AAPL": AssetTradingRules(1.0, 1.0, 10.0), "MSFT": AssetTradingRules(1.0, 1.0, 10.0)}


def _reasons(problems: list[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for client_order_id, reason in problems:
        grouped.setdefault(client_order_id, []).append(reason)
    return grouped


def test_validate_order_batch_accepts_a_clean_batch() -> None:
    orders = (_order("AAPL", PortfolioOrderSide.SELL, 10.0), _order("MSFT", PortfolioOrderSide.BUY, 5.0))
    assert validate_order_batch(orders, RULES, minimum_order_notional=10.0) == []


def test_validate_order_batch_accepts_float_quantities_on_the_increment_grid() -> None:
    rules = {"BTC": AssetTradingRules(0.0001, 0.1, 1.0)}
    order = _order("BTC", PortfolioOrderSide.BUY, 0.30000000000000004, price=100.0)
    assert validate_order_batch((order,), rules, minimum_order_notional=1.0) == []


@pytest.mark.parametrize(
    ("order", "rules", "minimum_notional", "fragment"),
    [
        (_order("AAPL", PortfolioOrderSide.BUY, 0.0), RULES, 10.0, "positive and finite"),
        (_order("AAPL", PortfolioOrderSide.BUY, -3.0), RULES, 10.0, "positive and finite"),
        (_order("AAPL", PortfolioOrderSide.BUY, math.nan), RULES, 10.0, "positive and finite"),
        (
            _order("AAPL", PortfolioOrderSide.BUY, 1.25),
            {"AAPL": AssetTradingRules(1.0, 0.5, 10.0)},
            10.0,
            "not a multiple",
        ),
        (
            _order("AAPL", PortfolioOrderSide.BUY, 3.0),
            {"AAPL": AssetTradingRules(5.0, 1.0, 10.0)},
            10.0,
            "below minimum size",
        ),
        (_order("AAPL", PortfolioOrderSide.BUY, 5.0, price=1.0), RULES, 10.0, "notional"),
        (_order("AAPL", PortfolioOrderSide.BUY, 1.0, price=20.0), RULES, 50.0, "notional"),
        (_order("AAPL", PortfolioOrderSide.BUY, 1.0, price=0.0), RULES, 10.0, "reference price"),
        (_order("NVDA", PortfolioOrderSide.BUY, 1.0), RULES, 10.0, "no trading rules"),
    ],
)
def test_validate_order_batch_flags_each_invalid_order(order, rules, minimum_notional, fragment) -> None:
    problems = validate_order_batch((order,), rules, minimum_order_notional=minimum_notional)
    assert [client_order_id for client_order_id, _ in problems] == [order.client_order_id]
    assert fragment in problems[0][1]


def test_validate_order_batch_flags_duplicate_side_for_each_copy() -> None:
    first = _order("AAPL", PortfolioOrderSide.BUY, 5.0, client_order_id="id-1")
    second = _order("AAPL", PortfolioOrderSide.BUY, 6.0, client_order_id="id-2")
    problems = _reasons(validate_order_batch((first, second), RULES, minimum_order_notional=10.0))
    assert set(problems) == {"id-1", "id-2"}
    assert all("duplicate buy" in reason for reasons in problems.values() for reason in reasons)


def test_validate_order_batch_flags_buy_and_sell_for_the_same_symbol() -> None:
    buy = _order("AAPL", PortfolioOrderSide.BUY, 5.0, client_order_id="buy-aapl")
    sell = _order("AAPL", PortfolioOrderSide.SELL, 5.0, client_order_id="sell-aapl")
    problems = _reasons(validate_order_batch((buy, sell), RULES, minimum_order_notional=10.0))
    assert set(problems) == {"buy-aapl", "sell-aapl"}
    assert all("BUY and SELL" in reason for reasons in problems.values() for reason in reasons)


def test_validate_order_batch_reports_only_the_invalid_order_of_a_clean_pair() -> None:
    good = _order("MSFT", PortfolioOrderSide.BUY, 5.0, client_order_id="good")
    bad = _order("AAPL", PortfolioOrderSide.BUY, 0.0, client_order_id="bad")
    problems = validate_order_batch((good, bad), RULES, minimum_order_notional=10.0)
    assert {client_order_id for client_order_id, _ in problems} == {"bad"}


# Runtime boundary: a malformed order is never sent; valid orders of the batch are


def test_submit_orders_drops_malformed_order_and_sends_the_rest(tmp_path, _captured_alerts) -> None:
    broker = _SimBroker()
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    # A pure BUY decision creates a real decision row the batch can attach to.
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    decision_id = first.decision_id
    broker.open_symbols.clear()
    sent_before = len(broker.submitted)
    malformed = _order("MSFT", PortfolioOrderSide.BUY, 0.5, client_order_id="bad-msft", decision_id=decision_id)
    valid = _order("AAPL", PortfolioOrderSide.BUY, 10.0, client_order_id="good-aapl", decision_id=decision_id)
    repository.record_order(malformed, "submitting")

    submitted = runtime._submit_orders((malformed, valid))

    assert len(submitted) == 1
    assert [order.client_order_id for order in broker.submitted[sent_before:]] == ["good-aapl"]
    assert _status_of(repository, decision_id, "bad-msft") == "rejected"
    assert _status_of(repository, decision_id, "good-aapl") == "submitted"
    rejected = [event for event in _captured_alerts if event.get("event") == "order_rejected"]
    assert [event["client_order_id"] for event in rejected] == ["bad-msft"]
    assert rejected[0]["reason"] == "invalid_batch_order"
