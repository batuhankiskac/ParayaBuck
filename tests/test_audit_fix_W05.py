"""Audit fixes W05: sell phase, residuals, re-plans and the submit path.

Covers C-1, C-2, C-4, D7-2, F6-1, F6-4, N6-3, P-1/R-2, P6-2 (held symbol),
P7-1, P9-3, R-3, R-4, the two-SELL-leg runtime case (Q7-1) and the optional
``submit_gate`` used by the caller to hold submits.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
)
from parayabuck.monitoring import alerts
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.runtime_models import (
    REBALANCE_PHASE_BUY_READY,
    REBALANCE_PHASE_COMPLETE,
    REBALANCE_PHASE_SELL_PENDING,
)
from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository
from tests.test_two_phase_rebalance import _bars, _fill, _FixedTargetStrategy, _position, _runtime, _SimBroker

# The pre-VT35 limits these fixtures were sized for (0.10 per-asset cap); the
# PortfolioRiskLimits code defaults now follow the shipped RiskLimits (D9-1).
_PRE_VT35_LIMITS = PortfolioRiskLimits(
    daily_loss_limit=0.005,
    drawdown_halt=0.05,
    maximum_gross=0.60,
    maximum_equity=0.50,
    maximum_equity_asset=0.10,
    minimum_cash=0.40,
    maximum_positions=6,
)


@pytest.fixture(autouse=True)
def _captured_alerts(monkeypatch):
    emitted: list[dict[str, object]] = []

    def fake_emit(event: str, **fields: object) -> None:
        emitted.append({"event": event, **fields})

    monkeypatch.setattr(alerts, "emit", fake_emit)
    return emitted


@pytest.fixture
def _captured_journal(monkeypatch):
    rows: list[dict[str, object]] = []

    def fake_no_trade(**fields: object) -> None:
        rows.append(dict(fields))

    monkeypatch.setattr(runtime_service, "_emit_no_trade_safe", fake_no_trade)
    return rows


class _RulesBroker(_SimBroker):
    """Simulated broker that records rule lookups and refuses some symbols."""

    def __init__(self, positions=(), *, untradable=(), notional_floor: float = 10.0) -> None:
        super().__init__(positions)
        self.untradable = set(untradable)
        self.rule_requests: list[str] = []
        self.notional_floor = notional_floor

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        self.rule_requests.append(symbol)
        if symbol in self.untradable:
            raise ValueError(f"Asset is not tradable: {symbol}")
        return AssetTradingRules(0.01, 0.01, self.notional_floor)


def _universe_runtime(tmp_path, broker, targets, universe):
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _FixedTargetStrategy(targets),
        tuple(sorted(universe)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    return runtime, repository


def _order(
    symbol: str,
    side: PortfolioOrderSide,
    quantity: float,
    *,
    decision_id: str,
    client_order_id: str | None = None,
    price: float = 100.0,
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


def _phase(repository, runtime) -> str:
    state = repository.current_rebalance_execution(runtime.account_id, runtime.market)
    assert state is not None
    return state.phase


def _status(repository, decision_id: str, client_order_id: str) -> str:
    return next(
        str(row["status"])
        for row in repository.orders_for_decision(decision_id)
        if str(row["client_order_id"]) == client_order_id
    )


def _sell_decision(tmp_path, broker, targets, universe):
    """Decide a rebalance that sells down the held positions and returns its sell orders."""
    runtime, repository = _universe_runtime(tmp_path, broker, targets, universe)
    first = runtime.process_closed_bars(_bars(tuple(sorted(universe))))
    assert first.status == "sell_phase_submitted"
    return runtime, repository, first


# C-1: rules only for held and targeted symbols; an untradable bystander does not block BUYs


def test_untradable_universe_symbol_does_not_block_deferred_buy(tmp_path) -> None:
    broker = _RulesBroker((_position("AAPL", 400),), untradable=("NVDA",))
    runtime, repository, first = _sell_decision(
        tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, ("AAPL", "MSFT", "NVDA")
    )
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    broker.rule_requests.clear()

    resumed = runtime.advance_rebalance()

    assert resumed is not None and resumed.status == "buy_phase_submitted"
    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.BUY] == ["MSFT"]
    assert "NVDA" not in broker.rule_requests


# C-2: a re-plan that still needs a SELL submits it and keeps the BUY deferred


def test_replan_sell_is_submitted_first_and_buy_waits_for_it(tmp_path) -> None:
    broker = _RulesBroker((_position("AAPL", 400),))
    runtime, repository, first = _sell_decision(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, ("AAPL", "MSFT"))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    # The account still holds 200 above target after the sell settled, and the
    # bot's fill book agrees (the fixture's opening 400 is not booked, so the
    # booked holding is pinned to the snapshot here).
    broker.positions = [_position("AAPL", 300)]
    broker.open_symbols.discard("AAPL")
    repository.owned_quantities = lambda *args, **kwargs: {"AAPL": 300.0}

    replan = runtime.advance_rebalance()

    assert replan is not None and replan.status == "replan_sell_submitted"
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    assert _phase(repository, runtime) == REBALANCE_PHASE_SELL_PENDING
    sent = broker.submitted[-1]
    assert sent.side == PortfolioOrderSide.SELL and sent.symbol == "AAPL"
    assert sent.client_order_id == f"{sell.client_order_id}-replan1"
    assert sent.quantity == 200

    _fill(repository, sent)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    repository.owned_quantities = lambda *args, **kwargs: {"AAPL": 100.0}
    buy_phase = runtime.advance_rebalance()
    assert buy_phase is not None and buy_phase.status == "buy_phase_submitted"
    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.BUY] == ["MSFT"]


# P-1 / R-2: a SELL that ended with nothing filled still owes its exit


def test_zero_fill_expired_sell_is_resent_as_a_full_residual(tmp_path) -> None:
    broker = _RulesBroker((_position("AAPL", 400),))
    runtime, repository, first = _sell_decision(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, ("AAPL", "MSFT"))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell, cumulative=0.0, status="expired")
    broker.open_symbols.discard("AAPL")

    resumed = runtime.advance_rebalance()

    assert resumed is not None and resumed.status == "residual_sell_submitted"
    residual = broker.submitted[-1]
    assert residual.client_order_id == f"{sell.client_order_id}-residual1"
    assert residual.quantity == sell.quantity
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    assert _phase(repository, runtime) == REBALANCE_PHASE_SELL_PENDING


# F6-1: done for day with a remainder is a residual status


def test_done_for_day_with_a_remainder_leaves_a_residual(tmp_path) -> None:
    broker = _RulesBroker((_position("AAPL", 400),))
    runtime, repository, first = _sell_decision(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, ("AAPL", "MSFT"))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell, cumulative=150.0, status="done_for_day")
    broker.open_symbols.discard("AAPL")

    resumed = runtime.advance_rebalance()

    assert resumed is not None and resumed.status == "residual_sell_submitted"
    residual = broker.submitted[-1]
    assert residual.client_order_id == f"{sell.client_order_id}-residual1"
    assert residual.quantity == 150
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)


# Q7-1 and a two-SELL-leg runtime case: one leg still working holds the BUY


def test_two_sell_legs_hold_the_buy_until_the_last_leg_settles(tmp_path) -> None:
    broker = _RulesBroker((_position("AAPL", 400), _position("MSFT", 400)))
    runtime, repository, first = _sell_decision(
        tmp_path, broker, {"AAPL": 0.05, "MSFT": 0.05, "NVDA": 0.10}, ("AAPL", "MSFT", "NVDA")
    )
    sells = {order.symbol: order for order in first.orders if order.side == PortfolioOrderSide.SELL}
    assert set(sells) == {"AAPL", "MSFT"}
    # AAPL sold out to target and filled; MSFT's SELL is still working.
    _fill(repository, sells["AAPL"])
    _fill(repository, sells["MSFT"], cumulative=0.0, status="new")
    broker.positions = [_position("AAPL", 50), _position("MSFT", 400)]
    broker.open_symbols.discard("AAPL")

    assert runtime.advance_rebalance() is None
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)

    _fill(repository, sells["MSFT"], activity_id="msft-fill")
    broker.positions = [_position("AAPL", 50), _position("MSFT", 50)]
    broker.open_symbols.discard("MSFT")
    buy_phase = runtime.advance_rebalance()

    assert buy_phase is not None and buy_phase.status == "buy_phase_submitted"
    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.BUY] == ["NVDA"]


# C-4: one exhausted leg does not close the other leg's residual


def test_exhausted_leg_does_not_close_the_other_legs_residual(tmp_path, _captured_alerts) -> None:
    broker = _RulesBroker((_position("AAPL", 400), _position("MSFT", 400)))
    runtime, repository, first = _sell_decision(
        tmp_path, broker, {"AAPL": 0.05, "MSFT": 0.05, "NVDA": 0.10}, ("AAPL", "MSFT", "NVDA")
    )
    sells = {order.symbol: order for order in first.orders if order.side == PortfolioOrderSide.SELL}
    aapl, msft = sells["AAPL"], sells["MSFT"]
    # AAPL partially filled and canceled: its remainder is owed.
    _fill(repository, aapl, cumulative=100.0, status="canceled")
    # MSFT has used its three residual rounds and is still unfilled.
    _fill(repository, msft, cumulative=0.0, status="canceled")
    for round_index in range(1, runtime_service.RESIDUAL_SELL_MAX_ROUNDS + 1):
        repository.record_order(
            _order(
                "MSFT",
                PortfolioOrderSide.SELL,
                msft.quantity,
                decision_id=first.decision_id,
                client_order_id=f"{msft.client_order_id}-residual{round_index}",
            ),
            "canceled",
        )
    broker.open_symbols.discard("AAPL")

    resumed = runtime.advance_rebalance()

    assert resumed is not None and resumed.status == "residual_sell_submitted"
    assert broker.submitted[-1].client_order_id == f"{aapl.client_order_id}-residual1"
    assert not any(
        "MSFT" in order.client_order_id and "residual" in order.client_order_id for order in broker.submitted
    )
    assert any(event.get("category") == "residual_sell_exhausted" for event in _captured_alerts)
    assert _phase(repository, runtime) == REBALANCE_PHASE_SELL_PENDING
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)

    # The AAPL residual fills; with MSFT exhausted, the rebalance closes without its BUYs.
    _fill(repository, broker.submitted[-1])
    broker.open_symbols.discard("AAPL")
    closed = runtime.advance_rebalance()
    assert closed is not None and closed.status == "rebalance_complete"
    assert _phase(repository, runtime) == REBALANCE_PHASE_COMPLETE
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)


# D7-2: a residual that cannot be sent is alerted, journaled and holds the BUYs


def test_dropped_residual_alerts_journals_and_holds_the_buys(tmp_path, _captured_alerts, _captured_journal) -> None:
    broker = _RulesBroker((_position("AAPL", 400),))
    runtime, repository, first = _sell_decision(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, ("AAPL", "MSFT"))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell, cumulative=150.0, status="canceled")
    broker.open_symbols.discard("AAPL")
    broker.untradable.add("AAPL")

    resumed = runtime.advance_rebalance()

    assert resumed is not None and resumed.status == "rebalance_complete"
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    assert _phase(repository, runtime) == REBALANCE_PHASE_COMPLETE
    assert any(event.get("category") == "residual_sell_dropped" for event in _captured_alerts)
    assert [row["reason"] for row in _captured_journal] == ["residual_sell_dropped"]


# P6-2 (held symbol): an untradable held symbol closes the rebalance without halting


def test_untradable_held_symbol_closes_the_rebalance_without_a_halt(tmp_path, _captured_alerts) -> None:
    broker = _RulesBroker((_position("AAPL", 400),))
    runtime, repository, first = _sell_decision(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, ("AAPL", "MSFT"))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    broker.untradable.add("AAPL")

    result = runtime.advance_rebalance()

    assert result is not None and result.status == "rebalance_complete"
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    assert any(event.get("category") == "held_symbol_untradable" for event in _captured_alerts)


# N6-3: BUY sizing uses the stricter of the policy and the symbol's notional floor


def test_buy_sizing_drops_a_buy_below_the_policy_notional_floor(tmp_path) -> None:
    broker = _RulesBroker(notional_floor=1.0)
    runtime, _repository = _universe_runtime(tmp_path, broker, {"AAPL": 0.15}, ("AAPL",))
    policy_floor = runtime.planner.policy.minimum_order_notional
    assert policy_floor > 7.0  # the $7 BUY below must be under the policy floor
    small = _order("AAPL", PortfolioOrderSide.BUY, 0.35, decision_id="d", price=20.0)

    admission = runtime._size_buys_to_safe_capacity((small,))

    assert admission.orders == ()


# P7-1 and F6-4: an existing row is not re-sent unless it is the same order


def test_submit_does_not_resend_a_submitting_row_with_a_different_quantity(tmp_path) -> None:
    broker = _SimBroker()
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    decision_id = first.decision_id
    broker.open_symbols.clear()
    broker.submitted.clear()
    recorded = _order("NVDA", PortfolioOrderSide.BUY, 10.0, decision_id=decision_id, client_order_id="nvda-buy")
    repository.record_order(recorded, "submitting")
    changed = _order("NVDA", PortfolioOrderSide.BUY, 11.0, decision_id=decision_id, client_order_id="nvda-buy")

    runtime._submit_orders((changed,))

    assert broker.submitted == []


@pytest.mark.parametrize("status", ["replaced", "stopped", "held", "pending_review"])
def test_submit_never_resends_an_existing_row_whatever_its_status(tmp_path, status: str) -> None:
    broker = _SimBroker()
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    broker.open_symbols.clear()
    broker.submitted.clear()
    order = _order("NVDA", PortfolioOrderSide.BUY, 10.0, decision_id=first.decision_id, client_order_id="nvda-buy")
    repository.record_order(order, status)

    runtime._submit_orders((order,))

    assert broker.submitted == []


# R-4: a re-planned order does not reuse the terminal row of its planned id


def test_replanned_order_takes_a_fresh_id_when_its_planned_id_is_terminal(tmp_path) -> None:
    broker = _SimBroker()
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    decision_id = first.decision_id
    planned = _order("NVDA", PortfolioOrderSide.BUY, 10.0, decision_id=decision_id, client_order_id="nvda-buy")
    repository.record_order(planned, "canceled")

    fresh = runtime._replan_orders_with_fresh_ids(decision_id, (planned,))

    assert [order.client_order_id for order in fresh] == ["nvda-buy-replan1"]


def test_replan_leaves_a_rejected_sell_to_its_retry_path(tmp_path) -> None:
    broker = _SimBroker()
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    decision_id = first.decision_id
    rejected = _order("AAPL", PortfolioOrderSide.SELL, 10.0, decision_id=decision_id, client_order_id="aapl-sell")
    repository.record_order(rejected, "rejected")

    fresh = runtime._replan_orders_with_fresh_ids(decision_id, (rejected,))

    assert fresh == ()


# R-3: a submitting row the broker never received is resolved canceled and alerted, not dropped silently


def test_resolved_canceled_submission_is_alerted_and_not_resent(tmp_path, _captured_alerts) -> None:
    broker = _SimBroker()
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    decision_id = first.decision_id
    order = _order("NVDA", PortfolioOrderSide.BUY, 10.0, decision_id=decision_id, client_order_id="nvda-stuck")
    repository.record_order(order, "submitting")
    broker.open_symbols.clear()
    sent_before = len(broker.submitted)

    runtime._resolve_submitting_rows(repository.orders_for_decision(decision_id))

    assert _status(repository, decision_id, "nvda-stuck") == "canceled"
    assert len(broker.submitted) == sent_before
    assert [e["category"] for e in _captured_alerts if e.get("event") == "paper_incident"] == [
        "submission_resolved_canceled"
    ]


# submit_gate: the caller can hold submits; the default changes nothing


def test_submit_gate_holds_a_residual_without_booking_it(tmp_path) -> None:
    broker = _RulesBroker((_position("AAPL", 400),))
    runtime, repository, first = _sell_decision(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, ("AAPL", "MSFT"))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell, cumulative=150.0, status="canceled")
    broker.open_symbols.discard("AAPL")
    sent_before = len(broker.submitted)

    held = runtime.advance_rebalance(submit_gate=lambda order: "halted by operator")

    assert held is not None and held.submitted_order_ids == ()
    assert len(broker.submitted) == sent_before
    assert _phase(repository, runtime) == REBALANCE_PHASE_SELL_PENDING
    assert [row["status"] for row in repository.orders_for_decision(first.decision_id, side="sell")] == ["canceled"]

    released = runtime.advance_rebalance()
    assert released is not None and released.status == "residual_sell_submitted"
    assert broker.submitted[-1].client_order_id == f"{sell.client_order_id}-residual1"


def test_submit_gate_holds_a_deferred_buy_and_keeps_the_phase_ready(tmp_path) -> None:
    broker = _RulesBroker((_position("AAPL", 400),))
    runtime, repository, first = _sell_decision(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, ("AAPL", "MSFT"))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    sent_before = len(broker.submitted)

    held = runtime.advance_rebalance(submit_gate=lambda order: "halted by operator")

    assert held is not None and held.status == "submit_blocked"
    assert len(broker.submitted) == sent_before
    assert _phase(repository, runtime) == REBALANCE_PHASE_BUY_READY
    assert repository.orders_for_decision(first.decision_id, side="buy") == ()

    released = runtime.advance_rebalance()
    assert released is not None and released.status == "buy_phase_submitted"
    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.BUY] == ["MSFT"]


# Stale decision: a BUY side whose decision is no longer the newest is never sent from an advance


def test_stale_decision_buy_is_not_sent_from_an_advance(tmp_path, monkeypatch) -> None:
    broker = _RulesBroker((_position("AAPL", 400),))
    runtime, repository, first = _sell_decision(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, ("AAPL", "MSFT"))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    latest = ["a-newer-session-decision"]
    monkeypatch.setattr(repository, "latest_decision_id", lambda account_id, market: latest[0])

    held = runtime.advance_rebalance()

    assert held is not None and held.status == "stale_decision_buy_held"
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    assert _phase(repository, runtime) == REBALANCE_PHASE_BUY_READY
    assert repository.orders_for_decision(first.decision_id, side="buy") == ()

    latest[0] = first.decision_id
    released = runtime.advance_rebalance()
    assert released is not None and released.status == "buy_phase_submitted"
    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.BUY] == ["MSFT"]
