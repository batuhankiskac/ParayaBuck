"""Two-phase portfolio rebalance lifecycle tests.

A decision with both SELL and BUY legs must submit the risk-reducing sells
first and the dependent buys only after every required sell is terminal AND a
fresh account snapshot proves the remaining exposure is safe. The lifecycle
is persisted so a process restart between phases resumes exactly once.
"""

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
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
)


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
    """Controlled paper broker.

    The position map is owned by the test: after applying a fill through the
    repository the test mutates ``positions``/``open_symbols`` to simulate
    what the real broker would report, exactly like a live fill changes the
    broker-side account.
    """

    def __init__(self, positions: tuple[PositionSnapshot, ...] = ()) -> None:
        self.positions = list(positions)
        self.open_symbols: set[str] = set()
        self.submitted: list[PortfolioOrderRequest] = []
        # Crash-window resolution map: client_order_id -> broker outcome
        # ("canceled" default when absent = the order never reached the
        # broker; "filled" recovers a real fill through the callback).
        self.order_resolution: dict[str, str] = {}

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
        """Fake broker crash-window resolution: an id absent from
        ``order_resolution`` never reached the broker and resolves canceled;
        ``filled`` recovers a real fill through ``portfolio_fill_recovered``."""
        outcome = self.order_resolution.get(client_order_id, "canceled")
        if outcome == "filled":
            if recover := getattr(self, "portfolio_fill_recovered", None):
                recover(
                    client_order_id,
                    broker_order_id or ("broker-" + client_order_id),
                    250.0,
                    100.0,
                    datetime.now(UTC),
                    f"activity-{client_order_id}",
                    0.0,
                )
        return outcome


# The pre-VT35 caps the sell/replan fixtures were sized against (10 % per asset,
# 6 positions). Explicit because the default limits are the shipped RiskLimits.
_PRE_VT35_LIMITS = PortfolioRiskLimits(
    daily_loss_limit=0.005,
    drawdown_halt=0.05,
    maximum_gross=0.60,
    maximum_equity=0.50,
    maximum_equity_asset=0.10,
    minimum_cash=0.40,
    maximum_positions=6,
)


def _runtime(
    tmp_path,
    broker: _SimBroker,
    targets: dict[str, float],
    *,
    mode: str = "paper",
    cap: float = 0.0,
    limits: PortfolioRiskLimits | None = None,
) -> tuple[PortfolioRuntime, PortfolioStateRepository]:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _FixedTargetStrategy(targets),
        tuple(sorted(targets)),
        broker,
        repository,
        mode=mode,
        maximum_allocation=0.50,
        risk_limits=limits or _PRE_VT35_LIMITS,
        live_dollar_cap=cap,
        account_id="acct",
    )
    return runtime, repository


def _bars(symbols: tuple[str, ...], *, day: datetime | None = None) -> list[Bar]:
    timestamp = day or datetime(2025, 1, 1, tzinfo=UTC)
    return [Bar(timestamp, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in symbols]


def _fill(
    repository: PortfolioStateRepository,
    order: PortfolioOrderRequest,
    *,
    cumulative: float | None = None,
    status: str = "fill",
    activity_id: str = "activity-1",
) -> None:
    repository.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-" + order.client_order_id,
        status=status,
        cumulative_filled_quantity=(cumulative if cumulative is not None else order.quantity),
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id=activity_id,
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


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


def _slot_limited_runtime(tmp_path, broker: _SimBroker) -> tuple[PortfolioRuntime, PortfolioStateRepository]:
    # Two account slots, both occupied: the MSFT->NVDA replacement opening
    # needs the AAPL slot to be freed first.
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _FixedTargetStrategy({"MSFT": 0.01, "NVDA": 0.02}),
        ("AAPL", "MSFT", "NVDA"),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.05,
        risk_limits=PortfolioRiskLimits(maximum_positions=2),
        account_id="acct",
    )
    return runtime, repository


@pytest.mark.parametrize(
    ("sell_status", "remaining_aapl"),
    [("rejected", 10.0), ("canceled", 10.0), ("canceled", 5.0), ("filled", 0.0)],
)
def test_deferred_replacement_requires_fresh_free_slot(tmp_path, sell_status: str, remaining_aapl: float) -> None:
    broker = _SimBroker((_position("AAPL", 10), _position("MSFT", 10)))
    runtime, repository = _slot_limited_runtime(tmp_path, broker)
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT", "NVDA")))
    assert result.status == "sell_phase_submitted"
    assert [(order.side, order.symbol) for order in broker.submitted] == [(PortfolioOrderSide.SELL, "AAPL")]
    assert (PortfolioOrderSide.BUY, "NVDA") in [(order.side, order.symbol) for order in result.orders]
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    _fill(
        repository,
        sell,
        status=sell_status,
        cumulative=sell.quantity - remaining_aapl,
    )
    broker.positions = [
        *([_position("AAPL", remaining_aapl)] if remaining_aapl else []),
        _position("MSFT", 10),
    ]
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed is not None
    if remaining_aapl and sell_status == "canceled" and sell.quantity - remaining_aapl > 0:
        # Behaviour change (2026-10-27): a partially filled, then canceled SELL
        # leaves a residual that is sold first under ``-residual1``; no BUY is
        # sent while it works, so the BUY waits on the residual, not on a slot.
        assert resumed.status == "residual_sell_submitted"
        assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL, PortfolioOrderSide.SELL]
        assert broker.submitted[-1].client_order_id == f"{sell.client_order_id}-residual1"
        assert all(order.side == PortfolioOrderSide.SELL for order in broker.submitted)
    elif remaining_aapl:
        assert resumed.status == "rebalance_waiting_for_slot"
        assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
        assert runtime.advance_rebalance().status == "rebalance_waiting_for_slot"
        runtime.close()
        restarted, _ = _slot_limited_runtime(tmp_path, broker)
        assert restarted.advance_rebalance().status == "rebalance_waiting_for_slot"
        assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    else:
        assert resumed.status == "buy_phase_submitted"
        assert [(order.side, order.symbol) for order in broker.submitted][-1] == (PortfolioOrderSide.BUY, "NVDA")


def test_sell_exists_buy_exists_no_buy_submission_before_sell_terminal(tmp_path) -> None:
    """SELL + BUY decision: the BUY must never reach the broker before the
    SELL is terminal, no matter how many bars arrive."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert any(order.side == PortfolioOrderSide.BUY for order in result.orders)
    # More bars arrive while the sell is still working: still no BUY.
    next_day = result.orders[0].decision_timestamp + timedelta(days=1)
    deferred = runtime.process_closed_bars(_bars(("AAPL", "MSFT"), day=next_day))
    assert deferred.status == "rebalance_in_progress"
    assert all(order.side == PortfolioOrderSide.SELL for order in broker.submitted)
    # The SELL fills: the BUY phase resumes with a fresh account snapshot.
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]


def test_partial_sell_keeps_buy_deferred(tmp_path) -> None:
    """A partially filled SELL that is still working keeps the dependent BUY
    deferred."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    # Partial fill, order still working at the broker.
    _fill(repository, sell, cumulative=sell.quantity / 2, status="partial_fill")
    assert runtime.advance_rebalance() is None
    assert all(order.side == PortfolioOrderSide.SELL for order in broker.submitted)
    # Behaviour change (2026-10-27): the sell goes terminal partially filled and
    # canceled. The unsold remainder is no longer left unsold: it is sent as a
    # residual SELL under the deterministic ``-residual1`` id, and the dependent
    # BUY stays held until that residual settles. Previously the BUY was
    # re-planned against the still-heavy account and the residual was dropped.
    _fill(repository, sell, cumulative=sell.quantity / 2, status="canceled")
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "residual_sell_submitted"
    residual = resumed.orders[0]
    assert residual.client_order_id == f"{sell.client_order_id}-residual1"
    assert residual.side == PortfolioOrderSide.SELL and 0 < residual.quantity <= sell.quantity / 2
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    _fill(repository, residual)
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]
    # The BUY must not push gross above the safe level: the AAPL position still
    # held at the re-plan (at most 200 shares, 0.20) + BUY <= 0.60 gross.
    buy = buys[0]
    assert buy.quantity * buy.reference_price <= (0.60 - 0.20) * 100_000 + 1e-6


def test_rejected_sell_replans_smaller_buy(tmp_path) -> None:
    """A rejected SELL leaves the account over-exposed: the re-planned BUY is
    capped by the gross headroom, not replayed at the original size."""
    broker = _SimBroker((_position("AAPL", 450),))
    runtime, repository = _runtime(
        tmp_path,
        broker,
        {"AAPL": 0.25, "MSFT": 0.30},
        limits=PortfolioRiskLimits(maximum_gross=0.60, maximum_equity=0.50, minimum_cash=0.40),
    )
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    assert sell.symbol == "AAPL"
    # The broker rejects the sell; the position never left the account.
    _fill(repository, sell, cumulative=0.0, status="rejected")
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]
    gross_headroom = (0.60 - 0.45) * 100_000
    assert buys[0].quantity * buys[0].reference_price <= gross_headroom + 1e-6
    assert buys[0].quantity < 250  # smaller than the original plan (272)


def test_restart_during_sell_phase_recovers_exactly_once(tmp_path) -> None:
    """decision -> SELL submitted -> process stops -> SELL fills -> restart:
    the new process reconciles the SELL, takes a fresh account and continues
    the BUY exactly once. No duplicate order, no lost BUY, no second SELL."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime_a, _ = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    runtime_a.close()
    # ---- process restarts ----
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime_b = PortfolioRuntime(
        _FixedTargetStrategy({"AAPL": 0.15, "MSFT": 0.20}),
        ("AAPL", "MSFT"),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
    )
    # The SELL filled while the process was down (persisted via trade updates
    # or broker reconciliation).
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")
    recovered = runtime_b.advance_rebalance()
    assert recovered is not None and recovered.status == "buy_phase_submitted"
    assert [order.side for order in broker.submitted] == [
        PortfolioOrderSide.SELL,
        PortfolioOrderSide.BUY,
    ]
    assert len(broker.submitted) == 2
    # Re-advancing (restart storms, duplicate trade updates) submits nothing.
    assert runtime_b.advance_rebalance() is None
    assert len(broker.submitted) == 2


def test_crash_before_broker_ack_never_submitted_recovers_autonomously(tmp_path) -> None:
    """A crash between repository persistence and broker acknowledgement
    leaves the SELL row at ``submitting``. On restart the deterministic
    client id lookup proves the order never reached Alpaca, so the row is
    canceled and the deferred BUY proceeds — no operator DB cleanup, no
    wedged rebalance."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime_a, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    # ---- crash between persist("submitting") and broker acknowledgement ----
    repository.update_order(sell.client_order_id, "submitting")
    broker.open_symbols.discard("AAPL")
    runtime_a.close()
    # ---- restart: broker has no record of the client id ----
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime_b = PortfolioRuntime(
        _FixedTargetStrategy({"AAPL": 0.15, "MSFT": 0.20}),
        ("AAPL", "MSFT"),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
    )
    recovered = runtime_b.advance_rebalance()
    assert recovered is not None and recovered.status == "buy_phase_submitted"
    assert [order.side for order in broker.submitted] == [
        PortfolioOrderSide.SELL,
        PortfolioOrderSide.BUY,
    ]
    rows = repository.orders_for_decision(result.decision_id, side="sell")
    assert [str(row["status"]) for row in rows] == ["canceled"]
    # The rebalance completes normally afterwards.
    for order in recovered.orders:
        _fill(repository, order)
    broker.positions = [_position("AAPL", 400), _position("MSFT", 200)]
    broker.open_symbols.discard(order.symbol)
    completed = runtime_b.advance_rebalance()
    assert completed is not None and completed.status == "rebalance_complete"


def test_crash_before_ack_but_broker_filled_recovers_real_fill(tmp_path) -> None:
    """A crash between persistence and acknowledgement where the broker DID
    receive and fill the SELL: restart resolution recovers the real fill at
    its actual price, releases ownership, and continues the BUY phase."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime_a, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    # The 400 AAPL were bought by the portfolio in an earlier session; seed
    # the repo ownership so the recovered SELL fill can release it.
    entry = PortfolioOrderRequest(
        client_order_id="pbp-entry-aapl",
        decision_id=result.decision_id,
        symbol="AAPL",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=400.0,
        reference_price=100.0,
        decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        strategy_name="fixed_target",
        strategy_version="1.0.0",
    )
    repository.record_order(entry, "submitted")
    repository.apply_trade_update(
        client_order_id="pbp-entry-aapl",
        broker_order_id="broker-pbp-entry-aapl",
        status="filled",
        cumulative_filled_quantity=400.0,
        average_fill_price=100.0,
        occurred_at=datetime(2025, 1, 1, tzinfo=UTC),
        activity_id="activity-entry-aapl",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    # ---- crash; the broker actually filled the SELL while the process was
    # down (no stream event was persisted) ----
    repository.update_order(sell.client_order_id, "submitting")
    broker.open_symbols.discard("AAPL")
    broker.positions = [_position("AAPL", 150)]
    broker.order_resolution[sell.client_order_id] = "filled"

    def _recover_fill(
        client_order_id: str,
        broker_order_id: str,
        filled_qty: float,
        avg_price: float,
        occurred_at: datetime,
        activity_id: str,
        fee: float,
    ) -> None:
        repository.apply_trade_update(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            status="filled",
            cumulative_filled_quantity=filled_qty,
            average_fill_price=avg_price,
            occurred_at=occurred_at,
            activity_id=activity_id,
            fee=fee,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )

    broker.portfolio_fill_recovered = _recover_fill
    runtime_a.close()
    # ---- restart ----
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime_b = PortfolioRuntime(
        _FixedTargetStrategy({"AAPL": 0.15, "MSFT": 0.20}),
        ("AAPL", "MSFT"),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
    )
    recovered = runtime_b.advance_rebalance()
    assert recovered is not None and recovered.status == "buy_phase_submitted"
    # Ownership was released by the recovered real fill.
    owned = repository.owned_quantities("acct")
    assert owned.get("AAPL", 0.0) == 150.0
    rows = repository.orders_for_decision(result.decision_id, side="sell")
    assert [str(row["status"]) for row in rows] == ["filled"]
    assert [order.side for order in broker.submitted] == [
        PortfolioOrderSide.SELL,
        PortfolioOrderSide.BUY,
    ]


def test_duplicate_trade_update_no_duplicate_buy(tmp_path) -> None:
    """Replayed/duplicate trade updates must not submit the BUY twice."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    # The same terminal fill arrives twice (stream replay).
    _fill(repository, sell, activity_id="a1")
    _fill(repository, sell, activity_id="a2")
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    assert len([o for o in broker.submitted if o.side == PortfolioOrderSide.BUY]) == 1
    # The BUY fill is also replayed; advancing again must not resubmit.
    for order in resumed.orders:
        _fill(repository, order, activity_id="b1")
        _fill(repository, order, activity_id="b2")
    completed = runtime.advance_rebalance()
    assert completed is not None and completed.status == "rebalance_complete"
    assert len(broker.submitted) == 2


def test_live_hard_dollar_cap_transient_exposure_never_exceeds_cap(tmp_path) -> None:
    """LIVE: with a rejected SELL the account is already over the hard dollar
    cap, so the dependent BUY must NOT be submitted at all."""
    broker = _SimBroker((_position("AAPL", 400),))  # $40k managed exposure
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, mode="live", cap=30_000.0)
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    assert all(order.side == PortfolioOrderSide.SELL for order in broker.submitted)
    # SELL rejected: $40k managed exposure is already above the $30k cap.
    _fill(repository, sell, cumulative=0.0, status="rejected")
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "rebalance_complete"
    assert all(order.side == PortfolioOrderSide.SELL for order in broker.submitted)


def test_live_hard_dollar_cap_buy_fits_after_sell(tmp_path) -> None:
    """LIVE: after the SELL fills there is room under the cap; the BUY is
    submitted but the post-buy managed exposure never exceeds the cap."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20}, mode="live", cap=30_000.0)
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    sold = sell.quantity
    broker.positions = [_position("AAPL", 400 - sold)]
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]
    managed_after = (400 - sold) * 100 + buys[0].quantity * buys[0].reference_price
    assert managed_after <= 30_000 + 1e-6


def test_max_gross_exposure_transient_never_exceeds_limit(tmp_path) -> None:
    """While a SELL is open the broker exposure must not grow; after a
    rejected SELL the re-planned BUY is capped by the gross headroom."""
    broker = _SimBroker((_position("AAPL", 350),))  # 0.35 gross
    runtime, repository = _runtime(
        tmp_path,
        broker,
        {"AAPL": 0.10, "MSFT": 0.25},
        limits=PortfolioRiskLimits(maximum_gross=0.40, maximum_equity=0.40, minimum_cash=0.40),
    )
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    # No BUY at the broker while the SELL is open: gross stays at 0.35.
    assert all(order.side == PortfolioOrderSide.SELL for order in broker.submitted)
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell, cumulative=0.0, status="expired")
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]
    # 0.35 + buy <= 0.40 maximum gross.
    assert buys[0].quantity * buys[0].reference_price <= 0.05 * 100_000 + 1e-6


def test_pure_buy_decision_has_no_unnecessary_delay(tmp_path) -> None:
    """A decision with no SELL leg begins the BUY phase immediately."""
    broker = _SimBroker()
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.30, "MSFT": 0.20})
    result = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "submitted"
    assert len(broker.submitted) == 2
    assert all(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    # No rebalance lifecycle was created: there was nothing to defer.
    assert repository.current_rebalance_execution("acct", Market.EQUITY) is None


def test_pending_buy_reserves_position_slot_across_new_decision_cycle(tmp_path) -> None:
    """A working BUY must consume its account slot after the cycle ends."""
    first_targets = {
        "AAPL": 0.10,
        "MSFT": 0.10,
        "NVDA": 0.10,
        "SPY": 0.10,
        "QQQ": 0.10,
        "IWM": 0.10,
    }
    second_targets = {"AMD": 0.10}
    broker = _SimBroker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    strategy = _FixedTargetStrategy(first_targets)
    runtime = PortfolioRuntime(
        strategy,
        tuple(sorted(first_targets | second_targets)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.60,
        risk_limits=PortfolioRiskLimits(maximum_positions=6),
        account_id="acct",
    )

    first = runtime.process_closed_bars(_bars(tuple(sorted(first_targets | second_targets))))
    assert first.status == "submitted"
    assert len([order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]) == 6
    assert len(repository.open_orders("acct", Market.EQUITY)) == 6

    strategy._targets = second_targets
    next_month = first.orders[0].decision_timestamp + timedelta(days=31)
    second = runtime.process_closed_bars(_bars(tuple(sorted(first_targets | second_targets)), day=next_month))

    assert second.status == "submitted"
    assert second.orders == ()
    assert len(broker.submitted) == 6


def test_sells_only_decision_submits_immediately(tmp_path) -> None:
    """A pure risk-reducing decision (no BUY leg) is not deferred either."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.10})
    result = runtime.process_closed_bars(_bars(("AAPL",)))
    assert result.status == "submitted"
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert repository.current_rebalance_execution("acct", Market.EQUITY) is None


def test_pending_sell_reserves_position_slot_across_new_decision_cycle(tmp_path) -> None:
    """Working SELLs remain fixed slots until they become terminal."""
    held_symbols = tuple(f"S{index:02d}" for index in range(6))
    initial_targets = {symbol: 0.0 for symbol in held_symbols}
    initial_targets["NEW"] = 0.0
    broker = _SimBroker(tuple(_position(symbol, 100) for symbol in held_symbols))
    runtime, _repository = _runtime(tmp_path, broker, initial_targets)

    first = runtime.process_closed_bars(_bars(tuple(sorted(initial_targets))))
    assert first.status == "submitted"
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL] * 6

    runtime.strategy._targets = {"NEW": 0.10}
    next_month = first.orders[0].decision_timestamp + timedelta(days=31)
    second = runtime.process_closed_bars(_bars(tuple(sorted(initial_targets)), day=next_month))

    assert second.orders == ()
    assert len(broker.submitted) == 6


def test_local_nonterminal_order_suppresses_normal_planner_when_broker_is_empty(tmp_path) -> None:
    """A local managed order still suppresses a duplicate after a broker
    open-order API omission or process crash window."""
    broker = _SimBroker()
    broker.all_open_order_symbols = lambda: set()  # type: ignore[method-assign]
    runtime, _repository = _runtime(tmp_path, broker, {"AAPL": 0.20})

    first = runtime.process_closed_bars(_bars(("AAPL",)))
    assert first.status == "submitted"
    assert [order.side for order in first.orders] == [PortfolioOrderSide.BUY]
    # The broker no longer reports the still-working order, but the local
    # account-scoped ledger row remains nonterminal.
    broker.open_symbols.clear()

    next_month = first.orders[0].decision_timestamp + timedelta(days=31)
    second = runtime.process_closed_bars(_bars(("AAPL",), day=next_month))

    assert second.status == "submitted"
    assert second.orders == ()
    assert len(broker.submitted) == 1


def test_local_nonterminal_order_suppresses_deferred_planner_when_broker_is_empty(tmp_path) -> None:
    """The deferred BUY phase also honors a local order absent from the
    broker's open-order response."""
    broker = _SimBroker((_position("AAPL", 400),))
    broker.all_open_order_symbols = lambda: set()  # type: ignore[method-assign]
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})

    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert first.status == "sell_phase_submitted"
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)

    # A separate local submission for the deferred target is still open, but
    # it is invisible to the broker adapter in this crash/recovery scenario.
    assert first.decision_id is not None
    repository.record_order(
        PortfolioOrderRequest(
            client_order_id="local-msft-blocker",
            decision_id=first.decision_id,
            symbol="MSFT",
            market=Market.EQUITY,
            side=PortfolioOrderSide.BUY,
            quantity=1.0,
            reference_price=100.0,
            decision_timestamp=sell.decision_timestamp,
            strategy_name="fixed_target",
            strategy_version="1.0.0",
        ),
        "submitted",
    )
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")

    resumed = runtime.advance_rebalance()

    # The working local BUY is suppressed from the re-plan (no duplicate
    # submit) and keeps the rebalance tracking it in buy_pending instead of
    # completing it (audit-2 Q3A-1).
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    assert resumed.submitted_order_ids == ()
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    state = repository.current_rebalance_execution(runtime.account_id, Market.EQUITY)
    assert state is not None and state.phase == "buy_pending"


def test_process_closed_bars_exposes_deferred_buys_advanced_in_same_cycle(tmp_path) -> None:
    """When the bar path advances a ready rebalance, its BUYs remain visible
    to the engine's same-cycle reservation accounting."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})

    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert first.status == "sell_phase_submitted"
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")

    next_day = sell.decision_timestamp + timedelta(days=1)
    resumed = runtime.process_closed_bars(_bars(("AAPL", "MSFT"), day=next_day))

    assert resumed.status == "rebalance_in_progress"
    assert any(order.side == PortfolioOrderSide.BUY for order in resumed.orders)


def test_process_closed_bars_returns_after_rebalance_completion_without_new_decision(tmp_path) -> None:
    """A terminal deferred BUY completion must end this cycle.

    The caller may still hold the account snapshot captured before the BUY
    fill. Re-entering the decision path with that stale snapshot could submit
    the same BUY a second time before the next fresh account snapshot.
    """
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})

    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert first.status == "sell_phase_submitted"
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell, activity_id="sell-terminal")
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")

    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buy = next(order for order in resumed.orders if order.side == PortfolioOrderSide.BUY)

    # This is the stale account that was captured while MSFT was still only a
    # working order. The broker has the position only after the terminal fill.
    pre_fill_account = broker.account()
    _fill(repository, buy, activity_id="buy-terminal")
    broker.positions.append(_position("MSFT", buy.quantity))
    broker.open_symbols.discard("MSFT")

    next_day = buy.decision_timestamp + timedelta(days=1)
    result = runtime.process_closed_bars(
        _bars(("AAPL", "MSFT"), day=next_day),
        account=pre_fill_account,
        force_scheduled=True,
    )

    assert result.status == "rebalance_complete"
    assert result.decision_id == first.decision_id
    assert [order.side for order in broker.submitted] == [
        PortfolioOrderSide.SELL,
        PortfolioOrderSide.BUY,
    ]
