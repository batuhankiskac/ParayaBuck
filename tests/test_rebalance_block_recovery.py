"""Recovery of blocked two-phase rebalances and stuck deferred BUY alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
)
from parayabuck.monitoring import alerts
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import (
    REBALANCE_PHASE_BLOCKED,
    REBALANCE_PHASE_BUY_PENDING,
    REBALANCE_PHASE_COMPLETE,
)
from parayabuck.portfolio.runtime_service import (
    REBALANCE_STATUS_CLOSED_UNRESOLVED,
    STUCK_DEFERRED_BUY_ALERT_SECONDS,
    PortfolioRuntime,
    PortfolioStateRepository,
)

_DAY = datetime(2025, 1, 1, tzinfo=UTC)
_TARGETS = {"AAPL": 0.15, "MSFT": 0.20}


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


class _Strategy:
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1
    parameters = {"targets": dict(_TARGETS)}

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(self.parameters.items()))

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=tuple(TargetWeight(symbol, weight) for symbol, weight in _TARGETS.items()),
            cash_weight=1.0 - sum(_TARGETS.values()),
            universe_version="test",
        )


class _Broker:
    def __init__(self) -> None:
        self.positions = [_position("AAPL", 400)]
        self.open_symbols: set[str] = set()
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(100_000.0 - value, 100_000.0, 100_000.0, 0.0, tuple(self.positions))

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(1.0, 1.0, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set(self.open_symbols)

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        self.open_symbols.add(order.symbol)
        return "broker-" + order.client_order_id


def _position(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol, qty, 100.0, 100.0, Market.EQUITY, qty_available=qty, ownership="managed", managed_qty=qty
    )


def _bars(day: datetime) -> list[Bar]:
    return [Bar(day, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in _TARGETS]


def _settle(repository: PortfolioStateRepository, order: PortfolioOrderRequest, activity_id: str) -> None:
    repository.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-" + order.client_order_id,
        status="fill",
        cumulative_filled_quantity=order.quantity,
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id=activity_id,
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


@pytest.fixture
def raised(monkeypatch) -> list[dict[str, object]]:
    """Operator alerts raised by the rebalance recovery paths under test."""
    captured: list[dict[str, object]] = []

    def emit(kind: str, *, message: str, **fields: object) -> None:
        if fields.get("category") in {
            "rebalance_unrecoverable",
            "stuck_deferred_buy",
            "rebalance_completed_with_ambiguous_buy",
        }:
            captured.append({"kind": kind, **fields})

    monkeypatch.setattr(alerts, "emit", emit)
    return captured


def _blocked_rebalance(tmp_path):
    """Submit the SELL leg, then lose its outcome so the rebalance blocks."""
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    first = runtime.process_closed_bars(_bars(_DAY))
    assert first.status == "sell_phase_submitted"
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    repository.update_order(sell.client_order_id, "reconciliation_required")
    assert runtime.advance_rebalance().status == "rebalance_blocked"
    return runtime, repository, broker, first.decision_id, sell


def _phase(repository: PortfolioStateRepository, decision_id: str) -> tuple[str, str]:
    row = repository.store.connection.execute(
        "SELECT phase, status FROM portfolio_rebalance_executions WHERE decision_id = ?", (decision_id,)
    ).fetchone()
    return str(row[0]), str(row[1])


def test_blocked_rebalance_stays_blocked_while_a_sell_is_unsettled(tmp_path, raised) -> None:
    runtime, repository, broker, decision_id, _ = _blocked_rebalance(tmp_path)

    result = runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)

    assert result.status == "rebalance_blocked"
    assert _phase(repository, decision_id)[0] == REBALANCE_PHASE_BLOCKED
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert raised == []


def test_settled_blocked_rebalance_closes_without_its_deferred_buys(tmp_path, raised) -> None:
    runtime, repository, broker, decision_id, sell = _blocked_rebalance(tmp_path)
    # Reconciliation proves the SELL filled.
    _settle(repository, sell, "sell-recovered")
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")

    result = runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)

    assert _phase(repository, decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)
    # No order of the blocked decision besides its SELL ever reached the broker.
    assert [order for order in broker.submitted if order.decision_id == decision_id] == [sell]
    # The same cycle plans normally from a fresh target under a new decision.
    assert result.decision_id not in {None, decision_id}
    assert result.orders
    assert {order.decision_id for order in broker.submitted[1:]} == {result.decision_id}
    assert raised == []


def test_unrecoverable_blocked_rebalance_alerts_then_closes_unresolved(tmp_path, raised) -> None:
    """P9-2: an unrecoverable block is alerted once, then closed once its orders settle."""
    runtime, repository, broker, decision_id, sell = _blocked_rebalance(tmp_path)
    _settle(repository, sell, "sell-recovered")
    with repository.store.lock, repository.store.connection:
        repository.store.connection.execute(
            "UPDATE portfolio_decisions SET risk_json = '{}' WHERE decision_id = ?", (decision_id,)
        )

    first = runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)
    second = runtime.process_closed_bars(_bars(_DAY + timedelta(days=2)), force_scheduled=True)

    # The blocked decision's BUYs are discarded; the rebalance no longer blocks entries.
    assert _phase(repository, decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)
    assert first.status != "rebalance_blocked"
    assert second.status != "rebalance_blocked"
    assert all(order.decision_id != decision_id for order in broker.submitted[1:])
    assert [(item["kind"], item["category"], item["decision_id"]) for item in raised] == [
        ("paper_incident", "rebalance_unrecoverable", decision_id)
    ]


def test_unrecoverable_block_with_unsettled_sell_stays_blocked_and_alerts_once(tmp_path, raised) -> None:
    """P9-2: the close waits for the blocked decision's orders to settle."""
    runtime, repository, broker, decision_id, _ = _blocked_rebalance(tmp_path)
    with repository.store.lock, repository.store.connection:
        repository.store.connection.execute(
            "UPDATE portfolio_decisions SET risk_json = '{}' WHERE decision_id = ?", (decision_id,)
        )

    first = runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)
    second = runtime.process_closed_bars(_bars(_DAY + timedelta(days=2)), force_scheduled=True)

    assert (first.status, second.status) == ("rebalance_blocked", "rebalance_blocked")
    assert _phase(repository, decision_id)[0] == REBALANCE_PHASE_BLOCKED
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert [(item["category"], item["decision_id"]) for item in raised] == [("rebalance_unrecoverable", decision_id)]


def test_stuck_deferred_buy_alerts_once_after_threshold_without_phase_change(tmp_path, raised) -> None:
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    first = runtime.process_closed_bars(_bars(_DAY))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _settle(repository, sell, "sell-fill")
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed.status == "buy_phase_submitted"
    buy = next(order for order in resumed.orders if order.side == PortfolioOrderSide.BUY)
    repository.update_order(buy.client_order_id, "reconciliation_required")

    # Fresh ambiguity: within the reconciliation window, no alert.
    assert runtime.advance_rebalance() is None
    assert raised == []

    stale = datetime.now(UTC) - timedelta(seconds=STUCK_DEFERRED_BUY_ALERT_SECONDS + 60)
    with repository.store.lock, repository.store.connection:
        repository.store.connection.execute(
            "UPDATE portfolio_rebalance_executions SET updated_at = ? WHERE decision_id = ?",
            (stale.isoformat(), first.decision_id),
        )
    assert runtime.advance_rebalance() is None
    assert runtime.advance_rebalance() is None

    assert [(item["category"], item["client_order_ids"]) for item in raised] == [
        ("stuck_deferred_buy", buy.client_order_id)
    ]
    assert _phase(repository, first.decision_id)[0] == REBALANCE_PHASE_BUY_PENDING

    _settle(repository, buy, "buy-recovered")
    assert runtime.advance_rebalance().status == "rebalance_complete"
    assert len(raised) == 1


def test_deferred_buy_phase_records_admission_evidence(tmp_path, monkeypatch) -> None:
    """The deferred BUY rows exist before the admission observation that
    references them, so the evidence is kept and no false incident fires."""
    incidents: list[str] = []
    monkeypatch.setattr(alerts, "emit", lambda kind, *, message, **fields: incidents.append(message))
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    first = runtime.process_closed_bars(_bars(_DAY))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _settle(repository, sell, "sell-fill")
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")

    resumed = runtime.advance_rebalance()

    assert resumed.status == "buy_phase_submitted"
    buy_ids = sorted(order.client_order_id for order in resumed.orders)
    observed = repository.store.connection.execute(
        "SELECT client_order_id FROM portfolio_buy_admission_observations WHERE decision_id = ?",
        (first.decision_id,),
    ).fetchall()
    assert sorted(str(row[0]) for row in observed) == buy_ids
    assert "Final BUY admission evidence unavailable" not in incidents
    assert [order.client_order_id for order in broker.submitted[1:]] == [
        order.client_order_id for order in resumed.orders
    ]


def test_rebalance_completing_over_an_ambiguous_buy_alerts_once(tmp_path, raised) -> None:
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    first = runtime.process_closed_bars(_bars(_DAY))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _settle(repository, sell, "sell-fill")
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    submit = broker.submit_portfolio_order

    def timeout(order: PortfolioOrderRequest) -> str:
        raise TimeoutError("broker timeout")

    # The BUY submit times out: its row turns ambiguous and the phase stays buy_ready.
    broker.submit_portfolio_order = timeout  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        runtime.advance_rebalance()
    buy_rows = repository.orders_for_decision(first.decision_id, side="buy")
    assert [row["status"] for row in buy_rows] == ["reconciliation_required"]
    assert raised == []

    # The re-plan suppresses the ambiguous symbol and completes the rebalance.
    broker.submit_portfolio_order = submit  # type: ignore[method-assign]
    assert runtime.advance_rebalance().status == "rebalance_complete"
    assert _phase(repository, first.decision_id)[0] == REBALANCE_PHASE_COMPLETE
    runtime._alert_completed_with_ambiguous_buys(first.decision_id)

    assert [(item["kind"], item["category"], item["client_order_ids"]) for item in raised] == [
        ("paper_incident", "rebalance_completed_with_ambiguous_buy", buy_rows[0]["client_order_id"])
    ]


def test_failed_deferred_buy_does_not_strand_its_unsubmitted_sibling(tmp_path) -> None:
    """A failed BUY submit leaves no ``submitting`` row for a later BUY of the
    batch that never reached the broker; the next advance submits it."""
    targets = {"AAPL": 0.15, "MSFT": 0.20, "NVDA": 0.10}

    class _ThreeSymbolStrategy(_Strategy):
        parameters = {"targets": dict(targets)}

        def decide(self, context: PortfolioContext) -> TargetPortfolio:
            return TargetPortfolio(
                strategy_name=self.name,
                strategy_version=self.version,
                decision_timestamp=context.decision_timestamp,
                weights=tuple(TargetWeight(symbol, weight) for symbol, weight in targets.items()),
                cash_weight=1.0 - sum(targets.values()),
                universe_version="test",
            )

    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _ThreeSymbolStrategy(),
        tuple(sorted(targets)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    bars = [Bar(_DAY, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in targets]
    first = runtime.process_closed_bars(bars)
    assert first.status == "sell_phase_submitted"
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _settle(repository, sell, "sell-fill")
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    submit = broker.submit_portfolio_order

    def timeout(order: PortfolioOrderRequest) -> str:
        raise TimeoutError("broker timeout")

    broker.submit_portfolio_order = timeout  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        runtime.advance_rebalance()
    rows = repository.orders_for_decision(first.decision_id, side="buy")
    assert [row["status"] for row in rows] == ["reconciliation_required"]
    failed = str(rows[0]["client_order_id"])

    broker.submit_portfolio_order = submit  # type: ignore[method-assign]
    resumed = runtime.advance_rebalance()

    assert resumed.status == "buy_phase_submitted"
    sibling = [order for order in broker.submitted if order.decision_id == first.decision_id][1:]
    assert len(sibling) == 1 and sibling[0].client_order_id != failed
    statuses = {
        str(row["client_order_id"]): str(row["status"])
        for row in repository.orders_for_decision(first.decision_id, side="buy")
    }
    assert statuses == {failed: "reconciliation_required", sibling[0].client_order_id: "submitted"}


def test_buy_ready_resolves_a_crashed_submitting_buy_before_replanning(tmp_path) -> None:
    """A BUY row left at ``submitting`` by a crash in the deferred phase is
    resolved against the broker instead of being suppressed and stranded."""
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    first = runtime.process_closed_bars(_bars(_DAY))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _settle(repository, sell, "sell-fill")
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    submit = broker.submit_portfolio_order

    def crash(order: PortfolioOrderRequest) -> str:
        raise SystemExit("process crashed before the broker call")

    # The process dies after persisting the BUY row, before the broker call.
    broker.submit_portfolio_order = crash  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        runtime.advance_rebalance()
    broker.submit_portfolio_order = submit  # type: ignore[method-assign]
    [row] = repository.orders_for_decision(first.decision_id, side="buy")
    assert row["status"] == "submitting"
    lookups: list[str] = []

    def unreachable(client_order_id: str, broker_order_id: str = "") -> str:
        lookups.append(client_order_id)
        raise ConnectionError("broker unreachable")

    # An unreachable broker leaves the phase waiting, never completing over the row.
    broker.resolve_portfolio_submission = unreachable  # type: ignore[attr-defined]
    assert runtime.advance_rebalance() is None
    assert _phase(repository, first.decision_id)[0] == "buy_ready"

    # The broker proves the order never arrived: the row becomes terminal. The
    # re-plan then sends the BUY again under a fresh ``-replan1`` id (R-4): the
    # canceled row keeps its planned id, so the insert cannot be ignored.
    broker.resolve_portfolio_submission = lambda client_order_id, broker_order_id="": "canceled"  # type: ignore[attr-defined]
    runtime.advance_rebalance()
    runtime.advance_rebalance()

    assert lookups == [row["client_order_id"]]
    buy_rows = repository.orders_for_decision(first.decision_id, side="buy")
    assert [item["status"] for item in buy_rows] == ["canceled", "submitted"]
    assert buy_rows[1]["client_order_id"] == f"{row['client_order_id']}-replan1"
    assert _phase(repository, first.decision_id)[0] == "buy_pending"
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL, PortfolioOrderSide.BUY]


def _stranded_direct_buy(tmp_path):
    """A pure-BUY decision whose process dies at the second submit leaves
    that order's row at ``submitting``."""
    broker = _Broker()
    broker.positions = []
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    submit = broker.submit_portfolio_order

    def crash_at_msft(order: PortfolioOrderRequest) -> str:
        if order.symbol == "MSFT":
            raise SystemExit("process crashed before the broker call")
        return submit(order)

    broker.submit_portfolio_order = crash_at_msft  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        runtime.process_closed_bars(_bars(_DAY))
    broker.submit_portfolio_order = submit  # type: ignore[method-assign]
    broker.submitted.clear()
    statuses = {str(row["symbol"]): str(row["status"]) for row in _all_orders(repository)}
    assert statuses == {"AAPL": "submitted", "MSFT": "submitting"}
    return runtime, repository, broker


def _all_orders(repository: PortfolioStateRepository) -> list[dict[str, object]]:
    rows = repository.store.connection.execute(
        "SELECT client_order_id, symbol, status, decision_id FROM portfolio_orders ORDER BY created_at"
    ).fetchall()
    return [{"client_order_id": r[0], "symbol": r[1], "status": r[2], "decision_id": r[3]} for r in rows]


def test_next_decision_cancels_an_untried_direct_order_unknown_to_the_broker(tmp_path) -> None:
    runtime, repository, broker = _stranded_direct_buy(tmp_path)
    [stranded] = [row for row in _all_orders(repository) if row["status"] == "submitting"]
    lookups: list[str] = []

    def unknown(client_order_id: str, broker_order_id: str = "") -> str:
        lookups.append(client_order_id)
        return "canceled"

    broker.resolve_portfolio_submission = unknown  # type: ignore[attr-defined]
    result = runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)

    assert lookups == [stranded["client_order_id"]]
    rows = {row["client_order_id"]: row for row in _all_orders(repository)}
    assert rows[stranded["client_order_id"]]["status"] == "canceled"
    # The freed MSFT slot is planned again by the new decision; the working
    # AAPL order is not a submitting row and keeps suppressing its symbol.
    assert [(order.symbol, order.decision_id) for order in broker.submitted] == [("MSFT", result.decision_id)]
    assert stranded["decision_id"] != result.decision_id
    open_ids = {order.client_order_id for order in repository.open_orders("acct")}
    assert stranded["client_order_id"] not in open_ids


def test_unreachable_broker_keeps_an_untried_direct_order_open(tmp_path) -> None:
    runtime, repository, broker = _stranded_direct_buy(tmp_path)

    def unreachable(client_order_id: str, broker_order_id: str = "") -> str:
        raise ConnectionError("broker unreachable")

    broker.resolve_portfolio_submission = unreachable  # type: ignore[attr-defined]
    runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)

    assert [row["status"] for row in _all_orders(repository) if row["symbol"] == "MSFT"] == ["submitting"]
    # Still an open order: the new decision submits nothing for either symbol.
    assert {order.symbol for order in repository.open_orders("acct")} == {"AAPL", "MSFT"}
    assert broker.submitted == []


def test_failed_direct_submit_cancels_the_untried_rest_of_its_batch(tmp_path) -> None:
    """Orders of a direct batch after a failed submit were never sent: their
    rows are canceled at once, not left at ``submitting`` for reconciliation
    to report as missing at the broker."""
    broker = _Broker()
    broker.positions = []
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    submit = broker.submit_portfolio_order

    def timeout(order: PortfolioOrderRequest) -> str:
        raise TimeoutError("broker timeout")

    broker.submit_portfolio_order = timeout  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        runtime.process_closed_bars(_bars(_DAY))
    broker.submit_portfolio_order = submit  # type: ignore[method-assign]

    statuses = {str(row["symbol"]): str(row["status"]) for row in _all_orders(repository)}
    assert statuses == {"AAPL": "reconciliation_required", "MSFT": "canceled"}
    assert {order.symbol for order in repository.open_orders("acct")} == {"AAPL"}
    # No broker lookup is needed: the next decision plans MSFT again.
    result = runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)
    assert [(order.symbol, order.decision_id) for order in broker.submitted] == [("MSFT", result.decision_id)]
