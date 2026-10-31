"""Audit wave 3 (W03): risk-off SELL exits, deferred BUY gate, rebalance closure.

Deterministic: tmp_path state, a simulated broker, fixed timestamps and no
network. Each test pins one finding (P3-5-2, P3-3-1, P3-1-1, P3-1-3, P3-3-3,
P3-2-3, P3-4-2, R3-2-1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
)
from parayabuck.monitoring import alerts
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
    _sell_row_precedes,
)
from parayabuck.runtime import _HELD_SESSION_STATUSES


class _FixedTargetStrategy:
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = targets
        self.parameters = {"targets": dict(targets)}

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(self.parameters.items()))

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weights = tuple(TargetWeight(symbol, w) for symbol, w in self._targets.items())
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(self._targets.values()),
            universe_version="test",
        )


class _SimBroker:
    def __init__(self, positions: tuple[PositionSnapshot, ...] = ()) -> None:
        self.positions = list(positions)
        self.open_symbols: set[str] = set()
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        equity = 100_000.0
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(
            cash=equity - value,
            equity=equity,
            peak_equity=equity,
            daily_pnl=0.0,
            positions=tuple(self.positions),
        )

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(1.0, 1.0, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set(self.open_symbols)

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        if order.symbol in self.open_symbols:
            raise ValueError(f"Open order already exists for {order.symbol}")
        self.submitted.append(order)
        self.open_symbols.add(order.symbol)
        return "broker-" + order.client_order_id

    def resolve_portfolio_submission(self, client_order_id: str, broker_order_id: str = "") -> str:
        # An id the broker never saw resolves canceled (the order never reached it).
        return "canceled"


def _position(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol,
        qty,
        100.0,
        100.0,
        Market.EQUITY,
        qty_available=qty,
        ownership="managed",
        managed_qty=qty,
    )


def _runtime(
    tmp_path,
    broker: _SimBroker,
    targets: dict[str, float],
    *,
    maximum_allocation: float = 0.50,
) -> tuple[PortfolioRuntime, PortfolioStateRepository]:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _FixedTargetStrategy(targets),
        tuple(sorted(targets)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=maximum_allocation,
        account_id="acct",
    )
    return runtime, repository


def _bars(symbols: tuple[str, ...], *, day: datetime | None = None) -> list[Bar]:
    timestamp = day or datetime(2025, 1, 1, tzinfo=UTC)
    return [Bar(timestamp, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in symbols]


def _fill(repository: PortfolioStateRepository, order: PortfolioOrderRequest) -> None:
    repository.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-" + order.client_order_id,
        status="fill",
        cumulative_filled_quantity=order.quantity,
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id=f"activity-1:{order.client_order_id}",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def test_gate_held_pure_sell_is_settled_by_residual_path(tmp_path) -> None:
    """P3-5-2 / P3-3-1: a gate-held risk-off SELL is not left stranded.

    The pure-SELL decision carries a sell_pending row, so the advance resolves
    the held (never sent) SELL and re-sends the exit as a residual.
    """
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.10})
    held = runtime.process_closed_bars(_bars(("AAPL",)), submit_gate=lambda order: "halted")
    assert held.status == "submitted"
    assert broker.submitted == []
    current = repository.current_rebalance_execution("acct", Market.EQUITY)
    assert current is not None and current.phase == "sell_pending"
    assert [str(row["status"]) for row in repository.orders_for_decision(current.decision_id)] == ["submitting"]

    runtime.advance_rebalance()

    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert broker.submitted[0].client_order_id.endswith("-residual1")


def test_residual_sell_submitted_status_parks_session() -> None:
    """P3-1-1: a decision pass that submitted a residual SELL is parked, not dropped."""
    assert "residual_sell_submitted" in _HELD_SESSION_STATUSES


def test_deferred_buy_gate_reread_cancels_held_row_and_alerts(tmp_path, monkeypatch) -> None:
    """P3-3-3 / P3-1-3: a BUY held by the gate re-read before its POST is not sent,
    its pre-recorded row is canceled, and the held side is alerted, not dropped."""
    broker = _SimBroker((_position("AAPL", 450),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.10, "MSFT": 0.08, "NVDA": 0.08})
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT", "NVDA")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")

    categories: list[object] = []
    monkeypatch.setattr(alerts, "emit", lambda name, **fields: categories.append(fields.get("category")))

    seen: dict[str, int] = {}

    def gate(order: PortfolioOrderRequest) -> str | None:
        # Each BUY is read twice: before its row exists, then right before its POST.
        # The second read is held once a BUY already went out.
        seen[order.symbol] = seen.get(order.symbol, 0) + 1
        if seen[order.symbol] == 2 and any(item.side == PortfolioOrderSide.BUY for item in broker.submitted):
            return "reread_hold"
        return None

    runtime.advance_rebalance(submit_gate=gate)

    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert len(buys) == 1
    current = repository.current_rebalance_execution("acct", Market.EQUITY)
    assert current is not None and current.phase == "buy_pending"
    buy_statuses = sorted(str(row["status"]) for row in repository.orders_for_decision(current.decision_id, side="buy"))
    assert buy_statuses == ["canceled", "submitted"]
    assert "submit_blocked" in categories


def test_blocked_rebalance_with_aged_out_leg_is_closed(tmp_path, monkeypatch) -> None:
    """P3-2-3: a BLOCKED rebalance whose only open leg is aged out is closed, not frozen."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.10})
    runtime.process_closed_bars(_bars(("AAPL",)), submit_gate=lambda order: "halted")
    current = repository.current_rebalance_execution("acct", Market.EQUITY)
    assert current is not None
    sell = repository.orders_for_decision(current.decision_id, side="sell")[0]
    repository.update_order(str(sell["client_order_id"]), "held")
    repository.update_rebalance_execution(current.decision_id, "blocked", status="reconciliation_required")
    later = datetime.now(UTC) + timedelta(days=10)
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: later)

    assert runtime.advance_rebalance() is None

    closed = repository.current_rebalance_execution("acct", Market.EQUITY)
    assert closed is not None
    assert closed.phase == "complete"
    assert closed.status == runtime_service.REBALANCE_STATUS_STUCK_LEGS_UNRESOLVED


def test_digest_superseded_rebalance_does_not_block_the_session(tmp_path) -> None:
    """R3-2-1: a rebalance closed by a configuration-digest change does not hold its session."""
    broker = _SimBroker((_position("AAPL", 450),))
    old, repository = _runtime(tmp_path, broker, {"AAPL": 0.10, "MSFT": 0.08}, maximum_allocation=0.50)
    result = old.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")

    new = PortfolioRuntime(
        _FixedTargetStrategy({"AAPL": 0.10, "MSFT": 0.08}),
        ("AAPL", "MSFT"),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.40,
        account_id="acct",
    )
    assert new.configuration_digest != old.configuration_digest
    timestamp = result.orders[0].decision_timestamp
    new.advance_rebalance(submit_deferred_buys=False)
    assert new._session_decided(timestamp) is True

    new._close_superseded_rebalance(result.decision_id)

    assert new._session_decided(timestamp) is False


def test_sell_row_precedes_only_earlier_attempts_of_the_same_exit() -> None:
    """P3-4-2: a leg's own predecessor does not block its retry; a later attempt or another exit does."""
    base = "parayabuck-d1-acct-AAPL-sell"
    assert _sell_row_precedes(base, f"{base}-residual1") is True
    assert _sell_row_precedes(f"{base}-residual1", base) is False
    assert _sell_row_precedes(base, f"{base}-replan1") is True
    assert _sell_row_precedes(f"{base}-residual1", f"{base}-residual2") is True
    assert _sell_row_precedes(base, base) is False
    assert _sell_row_precedes("parayabuck-d1-acct-MSFT-sell", base) is False
