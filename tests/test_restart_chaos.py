"""Restart and cross-day persistence chaos scenarios (paper-chaos gate).

The paper-chaos gate (``scripts/run_paper_chaos.py``) runs every test marked
``paper_chaos``; this module marks itself so the existing restart coverage in
``test_two_phase_rebalance.py`` / ``test_portfolio_runtime.py`` is exercised
by the gate through these focused, deterministic ports. Everything here is
offline: fake brokers and real SQLite repositories, no network, no live
permissions.

Levels used:
- ``PortfolioRuntime``: the persisted two-phase rebalance / ownership
  lifecycle (``PortfolioStateRepository`` survives the restart).
- ``EngineRuntime`` unit: ``EngineRuntime.__new__`` + minimal fakes, calling
  the exact restart hook (``_restore_persistent_state``, ``_pin_managed_symbols``)
  like the existing hardening tests.
- ``AlpacaBroker`` unit: the ownership-drift exemption predicate.
"""

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Market,
    PositionSnapshot,
)
from parayabuck.execution.alpaca import OwnershipDriftError
from parayabuck.execution.ledger import ExecutionLedger
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
from parayabuck.runtime import EngineRuntime, WorkerHealth
from tests.helpers import make_broker

pytestmark = pytest.mark.paper_chaos


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
    """Controlled paper broker (pattern from test_two_phase_rebalance.py).

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


def _runtime(
    tmp_path: Path,
    broker: _SimBroker,
    targets: dict[str, float],
    *,
    mode: str = "paper",
) -> tuple[PortfolioRuntime, PortfolioStateRepository]:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _FixedTargetStrategy(targets),
        tuple(sorted(targets)),
        broker,
        repository,
        mode=mode,
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    return runtime, repository


def _restarted_runtime(
    tmp_path: Path,
    broker: _SimBroker,
    targets: dict[str, float],
    *,
    mode: str = "paper",
) -> tuple[PortfolioRuntime, PortfolioStateRepository]:
    """Reopen the persisted repository as a fresh process would."""
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _FixedTargetStrategy(targets),
        tuple(sorted(targets)),
        broker,
        repository,
        mode=mode,
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
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
    activity_id: str | None = None,
) -> None:
    # Default id is per order: a broker fill id is unique, and two orders
    # sharing one would collide in portfolio_fills (F2-4-2 refuses that).
    repository.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-" + order.client_order_id,
        status=status,
        cumulative_filled_quantity=(cumulative if cumulative is not None else order.quantity),
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id=activity_id or f"activity-1:{order.client_order_id}",
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


def _bare_runtime(ledger: ExecutionLedger, account_id: str = "acct") -> EngineRuntime:
    """Minimal EngineRuntime shell for restart hooks (hardening pattern)."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id=account_id)
    runtime.ledger = ledger
    runtime._circuit_breaker_reason = None  # as EngineRuntime.__init__ does
    runtime._risk_state_lock = threading.Lock()  # type: ignore[attr-defined]
    return runtime


# ---------------------------------------------------------------------------
# 1. Restart while flat
# ---------------------------------------------------------------------------


def test_restart_while_flat_stays_idle_until_bar_closes(tmp_path: Path) -> None:
    """Level: PortfolioRuntime. A flat restart (no positions, no orders) is a
    clean start: the restart-recovery hook submits nothing and no decision is
    manufactured until a bar actually closes."""
    broker = _SimBroker()
    runtime_a, _ = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    assert broker.submitted == []
    assert runtime_a.advance_rebalance() is None
    runtime_a.close()
    # ---- restart ----
    runtime_b, _ = _restarted_runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    assert broker.submitted == []
    assert runtime_b.advance_rebalance() is None
    # A closed bar arrives: only now does the first decision happen.
    result = runtime_b.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "submitted"
    assert len(broker.submitted) == 2


# ---------------------------------------------------------------------------
# 2. Restart with a managed position
# ---------------------------------------------------------------------------


def test_restart_with_managed_position_keeps_ownership(tmp_path: Path) -> None:
    """Level: PortfolioRuntime. Ownership derives from the persisted
    repository, not process memory: after a buy fill and a process restart the
    repository still reports the managed quantity and the restarted runtime
    still accounts the position as managed."""
    broker = _SimBroker()
    runtime_a, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    order = next(o for o in result.orders if o.symbol == "AAPL")
    _fill(repository, order)
    broker.positions = [_position("AAPL", order.quantity)]
    broker.open_symbols.discard("AAPL")
    assert repository.owned_quantities("acct").get("AAPL", 0.0) == order.quantity
    runtime_a.close()
    # ---- restart ----
    runtime_b, repository = _restarted_runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    assert repository.owned_symbols("acct") == {"AAPL"}
    assert repository.owned_quantities("acct").get("AAPL", 0.0) == order.quantity
    snapshot = broker.account()
    aapl = next(position for position in snapshot.positions if position.symbol == "AAPL")
    assert aapl.ownership == "managed"
    runtime_b.close()


# ---------------------------------------------------------------------------
# 3. Restart with an open order
# ---------------------------------------------------------------------------


def test_restart_with_open_order_gates_submission(tmp_path: Path) -> None:
    """Level: PortfolioRuntime. A restart while a SELL is still working at the
    broker must not duplicate it and must keep the dependent BUY deferred; the
    BUY is submitted exactly once after the SELL goes terminal."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime_a, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    runtime_a.close()
    # ---- restart: the SELL is still open at the broker ----
    runtime_b, _ = _restarted_runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    assert "AAPL" in broker.all_open_order_symbols()
    assert runtime_b.advance_rebalance() is None
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    # The SELL fills: the BUY phase resumes exactly once.
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    resumed = runtime_b.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]
    assert len(broker.submitted) == 2
    assert runtime_b.advance_rebalance() is None


def test_open_managed_sell_exempts_drift_after_restart(tmp_path: Path) -> None:
    """Level: AlpacaBroker unit. After a restart the repository keeps
    reporting the working managed SELL, and that open order must exempt the
    ownership-drift shortfall (the missing quantity is attributable to the
    in-flight exit). Without the open order the same account fails closed."""
    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                id="acct",
                account_number="acct",
                account_blocked=False,
                trading_blocked=False,
                trade_suspended_by_user=False,
                equity="100000",
                last_equity="100000",
                cash="50000",
                non_marginable_buying_power="50000",
            ),
            get_all_positions=lambda: [
                SimpleNamespace(
                    symbol="QLD",
                    qty="20",
                    current_price="100",
                    unrealized_pl="0",
                    unrealized_intraday_pl="0",
                    asset_class="us_equity",
                    avg_entry_price="100",
                    qty_available="0",
                )
            ],
        ),
    )
    broker.portfolio_owned_quantities = lambda: {"QLD": 50.0}
    broker._peak_equity = 0.0
    broker._save_peak_equity = lambda: None
    working_sell = SimpleNamespace(
        symbol="QLD",
        side="sell",
        market=Market.EQUITY,
        quantity=30.0,
        cumulative_filled_quantity=0.0,
    )
    broker.portfolio_open_orders = lambda: (working_sell,)
    assert broker.account() is not None  # exemption: no OwnershipDriftError
    # Control: the same shortfall without the open order is fail-closed.
    broker.portfolio_open_orders = lambda: ()
    with pytest.raises(OwnershipDriftError, match="Ownership drift"):
        broker.account()


# ---------------------------------------------------------------------------
# 4. Restart during the SELL phase
# ---------------------------------------------------------------------------


def test_restart_during_sell_phase_resumes_exactly_once(tmp_path: Path) -> None:
    """Level: PortfolioRuntime. Port of the two-phase restart semantics into
    the gate: decision -> SELL submitted -> process stops -> SELL fills ->
    restart reconciles the SELL, takes a fresh account and continues the BUY
    exactly once. No duplicate order, no lost BUY, no second SELL."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime_a, _ = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    runtime_a.close()
    # ---- restart ----
    runtime_b, repository = _restarted_runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    # The SELL filled while the process was down (persisted via trade updates).
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 100)]
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


# ---------------------------------------------------------------------------
# 5. Restart after the SELL filled, before the BUY phase
# ---------------------------------------------------------------------------


def test_restart_after_sell_filled_before_buy_phase_submits_buy_once(tmp_path: Path) -> None:
    """Level: PortfolioRuntime. The SELL fill is already persisted when the
    process restarts: the restart must skip straight to the BUY phase and
    submit the dependent BUY exactly once."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime_a, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    runtime_a.close()
    # ---- restart: SELL already terminal, BUY phase never started ----
    runtime_b, _ = _restarted_runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    resumed = runtime_b.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]
    assert len([order for order in broker.submitted if order.side == PortfolioOrderSide.SELL]) == 1
    assert len(broker.submitted) == 2
    assert runtime_b.advance_rebalance() is None


# ---------------------------------------------------------------------------
# 6. Restart with a partially filled order
# ---------------------------------------------------------------------------


def test_restart_with_partially_filled_order_stays_non_terminal(tmp_path: Path) -> None:
    """Level: PortfolioRuntime. A partial fill persisted before the restart
    leaves the order non-terminal: the restarted runtime submits nothing (no
    duplicate SELL, no premature BUY) and only resumes the BUY once the order
    goes terminal."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime_a, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell, cumulative=sell.quantity / 2, status="partial_fill")
    runtime_a.close()
    # ---- restart: SELL half filled, still working ----
    runtime_b, _ = _restarted_runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    assert runtime_b.advance_rebalance() is None
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    # The SELL is canceled half filled: the unfilled half goes out once as a
    # residual SELL, and the BUY waits until that residual settles.
    _fill(repository, sell, cumulative=sell.quantity / 2, status="canceled")
    broker.open_symbols.discard("AAPL")
    resumed = runtime_b.advance_rebalance()
    assert resumed is not None and resumed.status == "residual_sell_submitted"
    residual = broker.submitted[-1]
    assert residual.side == PortfolioOrderSide.SELL
    assert residual.client_order_id == f"{sell.client_order_id}-residual1"
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    _fill(repository, residual, activity_id="activity-residual")
    broker.open_symbols.discard("AAPL")
    broker.positions = [_position("AAPL", 100)]
    buy_phase = runtime_b.advance_rebalance()
    assert buy_phase is not None and buy_phase.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]
    assert len(broker.submitted) == 3


# ---------------------------------------------------------------------------
# 7. Restart with a duplicate trade update
# ---------------------------------------------------------------------------


def test_restart_with_duplicate_trade_update_is_idempotent(tmp_path: Path) -> None:
    """Level: PortfolioRuntime. Replayed trade updates (stream replay across a
    restart) must be idempotent: no duplicate fill bookkeeping and no
    duplicate order after the restart."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime_a, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    # The same terminal fill arrives twice before the restart (stream replay).
    _fill(repository, sell, activity_id="a1")
    _fill(repository, sell, activity_id="a2")
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    runtime_a.close()
    # ---- restart ----
    runtime_b, repository = _restarted_runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    resumed = runtime_b.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    assert len([o for o in broker.submitted if o.side == PortfolioOrderSide.BUY]) == 1
    # The BUY fill is also replayed; advancing again must not resubmit.
    for order in resumed.orders:
        _fill(repository, order, activity_id="b1")
        _fill(repository, order, activity_id="b2")
    completed = runtime_b.advance_rebalance()
    assert completed is not None and completed.status == "rebalance_complete"
    assert len(broker.submitted) == 2


# ---------------------------------------------------------------------------
# 8. G1 regression: crash after persist('submitting'), before broker ack
# ---------------------------------------------------------------------------


def test_restart_crash_after_submitting_never_reached_broker_recovers(
    tmp_path: Path,
) -> None:
    """Level: PortfolioRuntime. G1 regression: a crash between repository
    persistence and broker acknowledgement leaves the SELL row at
    ``submitting``. On restart the deterministic client-id lookup proves the
    order never reached the broker, so the row is canceled and the deferred
    BUY proceeds — no operator DB cleanup, no wedged rebalance."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime_a, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    result = runtime_a.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert result.status == "sell_phase_submitted"
    sell = next(order for order in result.orders if order.side == PortfolioOrderSide.SELL)
    # ---- crash between persist("submitting") and broker acknowledgement ----
    repository.update_order(sell.client_order_id, "submitting")
    broker.open_symbols.discard("AAPL")
    runtime_a.close()
    # ---- restart: the broker has no record of the client id ----
    runtime_b, repository = _restarted_runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    recovered = runtime_b.advance_rebalance()
    # The SELL never reached the broker, so its exit is still owed: the row is
    # re-sent as a residual and no BUY goes out while it works.
    assert recovered is not None and recovered.status == "residual_sell_submitted"
    assert not any(order.side == PortfolioOrderSide.BUY for order in broker.submitted)
    residual = broker.submitted[-1]
    assert residual.client_order_id == f"{sell.client_order_id}-residual1"
    rows = repository.orders_for_decision(result.decision_id, side="sell")
    assert [str(row["status"]) for row in rows] == ["canceled", "submitted"]
    # The residual fills; the BUY is then re-planned and the rebalance completes.
    _fill(repository, residual)
    broker.positions = [_position("AAPL", 100)]
    broker.open_symbols.discard("AAPL")
    buy_phase = runtime_b.advance_rebalance()
    assert buy_phase is not None and buy_phase.status == "buy_phase_submitted"
    for order in buy_phase.orders:
        _fill(repository, order)
    broker.positions = [_position("AAPL", 100), _position("MSFT", 100)]
    broker.open_symbols.discard("MSFT")
    completed = runtime_b.advance_rebalance()
    assert completed is not None and completed.status == "rebalance_complete"


def test_restart_crash_after_submitting_broker_filled_recovers_real_fill(
    tmp_path: Path,
) -> None:
    """Level: PortfolioRuntime. G1 regression: a crash between persistence and
    acknowledgement where the broker DID receive and fill the SELL: restart
    resolution recovers the real fill at its actual price, releases ownership,
    and continues the BUY phase."""
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
    broker.positions = [_position("AAPL", 100)]
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
    runtime_b, repository = _restarted_runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
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


# ---------------------------------------------------------------------------
# 9. Restart with a persistent safety incident (G2 contract)
# ---------------------------------------------------------------------------


def test_restart_with_persistent_safety_incident_restores_halt(tmp_path: Path) -> None:
    """Level: EngineRuntime unit. A persisted ``ownership_drift`` safety
    incident (G2 ``save_safety_incident``) survives a ledger reopen and the
    restart hook restores the circuit-breaker halt."""
    path = tmp_path / "ledger.sqlite3"
    ledger = ExecutionLedger(str(path))
    ledger.save_safety_incident("acct", "ownership_drift")
    ledger.close()
    # ---- restart: reopen the ledger and run the restart hook ----
    reopened = ExecutionLedger(str(path))
    incidents = reopened.load_safety_incidents("acct")
    assert len(incidents) == 1
    assert incidents[0].reason == "ownership_drift"
    runtime = _bare_runtime(reopened)
    runtime._restore_persistent_state()
    assert runtime._circuit_breaker_reason == "ownership_drift"
    reopened.close()


def test_restart_without_incident_starts_clean(tmp_path: Path) -> None:
    """Level: EngineRuntime unit. Control: a restart with no persisted
    incident must not restore any circuit-breaker halt."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    assert len(ledger.load_safety_incidents("acct")) == 0
    runtime = _bare_runtime(ledger)
    runtime._restore_persistent_state()
    assert runtime._circuit_breaker_reason is None
    ledger.close()


# ---------------------------------------------------------------------------
# 10. Cross-day restart with a persistent incident (G2 regression)
# ---------------------------------------------------------------------------


def test_cross_day_restart_with_yesterday_incident_keeps_halt(tmp_path: Path) -> None:
    """Level: EngineRuntime unit. G2 regression: an incident activated
    YESTERDAY must still restore the halt on today's restart — the persistent
    safety state is date-independent by contract."""
    yesterday = datetime.now(UTC) - timedelta(days=1)
    path = tmp_path / "ledger.sqlite3"
    ledger = ExecutionLedger(str(path))
    ledger.save_safety_incident("acct", "ownership_drift", activated_at=yesterday)
    ledger.close()
    # ---- restart a day later ----
    reopened = ExecutionLedger(str(path))
    incidents = reopened.load_safety_incidents("acct")
    assert len(incidents) == 1
    assert incidents[0].reason == "ownership_drift"
    assert incidents[0].activated_at.date() == yesterday.date()
    runtime = _bare_runtime(reopened)
    runtime._restore_persistent_state()
    assert runtime._circuit_breaker_reason == "ownership_drift"
    reopened.close()


def test_cleared_incident_does_not_restore_halt(tmp_path: Path) -> None:
    """Level: EngineRuntime unit. After an operator clears the incident
    (G2 ``clear_safety_incident``), a restart starts clean: the halt is not
    restored."""
    path = tmp_path / "ledger.sqlite3"
    ledger = ExecutionLedger(str(path))
    ledger.save_safety_incident("acct", "ownership_drift", activated_at=datetime.now(UTC))
    ledger.clear_safety_incident("acct", "ownership_drift")
    ledger.close()
    reopened = ExecutionLedger(str(path))
    assert len(reopened.load_safety_incidents("acct")) == 0
    runtime = _bare_runtime(reopened)
    runtime._restore_persistent_state()
    assert runtime._circuit_breaker_reason is None
    reopened.close()


def test_stream_worker_restart_limit_alerts_once_not_per_retry(monkeypatch: Any) -> None:
    """The circuit-breaker alert/metric fire once at the restart-limit
    crossing; retries past the limit keep the halt asserted but must not
    re-emit every backoff iteration (alert spam). A verified reconnect resets
    restart_count, which re-arms the edge."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    worker = WorkerHealth(last_heartbeat=now, restart_count=5, alive=True)
    runtime._workers = {"pb-stocks": worker}
    runtime._running = True
    runtime._state = "running"
    runtime._circuit_breaker_reason = None
    runtime._market_entry_halts = {Market.EQUITY: None}

    emitted: list[str] = []
    recorded: list[str] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **_: emitted.append(kind))
    monkeypatch.setattr(
        runtime_module.runtime_metrics_store,
        "record_event",
        lambda event: recorded.append(event),
    )
    # The backoff waits on _stop_event (interruptible by stop()); a fake that
    # never signals skips the real 1+2+4+8+16 s delays.
    runtime._stop_event = SimpleNamespace(wait=lambda _seconds: False)

    def always_failing() -> None:
        raise RuntimeError("simulated stream outage")

    thread = threading.Thread(target=runtime._run_stream_worker, args=("pb-stocks", always_failing))
    thread.start()
    deadline = time.monotonic() + 5.0
    while worker.restart_count < 7 and time.monotonic() < deadline:
        time.sleep(0.001)
    runtime._running = False
    thread.join(timeout=5.0)

    assert not thread.is_alive()
    assert worker.restart_count >= 7
    # Halt stays asserted across retries; exactly one alert and one metric.
    assert runtime._market_entry_halts[Market.EQUITY] == "pb-stocks_restart_limit"
    assert emitted == ["circuit_breaker"]
    assert recorded == ["circuit_breaker"]


def test_stream_worker_backoff_is_interrupted_by_stop_event() -> None:
    """stop() joins stream workers with a 5 s timeout; a worker parked in its
    (up to 16 s) restart backoff must wake on _stop_event instead of
    outliving the join and forcing stop_failed."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    # restart_count=3 -> next failure backs off 2 ** 3 = 8 s.
    worker = WorkerHealth(last_heartbeat=now, restart_count=3, alive=True)
    runtime._workers = {"pb-stocks": worker}
    runtime._running = True
    runtime._state = "running"
    runtime._circuit_breaker_reason = None
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime._stop_event = threading.Event()
    failed = threading.Event()

    def failing() -> None:
        failed.set()
        raise RuntimeError("simulated stream outage")

    thread = threading.Thread(target=runtime._run_stream_worker, args=("pb-stocks", failing))
    thread.start()
    assert failed.wait(timeout=5.0)
    runtime._stop_event.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert worker.alive is False


def _trade_update_worker_runtime() -> tuple[EngineRuntime, WorkerHealth]:
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    worker = WorkerHealth(last_heartbeat=now, alive=True)
    runtime._workers = {"pb-trade-updates": worker}
    runtime._running = True
    runtime._state = "running"
    runtime._circuit_breaker_reason = None
    runtime._state_lock = threading.RLock()
    runtime._stop_event = SimpleNamespace(wait=lambda _seconds: False)
    runtime._portfolio_runtimes = (SimpleNamespace(symbols=("AAPL",)),)
    runtime._not_ready_symbols = set()
    return runtime, worker


def test_trade_update_blip_degrades_then_recovers_on_retry() -> None:
    """A single transient pb-trade-updates failure marks the runtime degraded
    but must not pin it there: the retry restores running before reconnect."""
    runtime, _worker = _trade_update_worker_runtime()
    states: list[str] = []

    def flaky() -> None:
        states.append(runtime._state)
        if len(states) == 1:
            raise RuntimeError("simulated trade-update disconnect")
        runtime._running = False

    runtime._run_stream_worker("pb-trade-updates", flaky)

    assert states == ["running", "running"]
    assert runtime._state == "running"


def test_trade_update_blip_never_clears_a_circuit_breaker_halt() -> None:
    """The degraded -> running restore applies only without a breaker."""
    runtime, _worker = _trade_update_worker_runtime()
    runtime._circuit_breaker_reason = "reconciliation_failed"
    states: list[str] = []

    def flaky() -> None:
        states.append(runtime._state)
        if len(states) == 1:
            raise RuntimeError("simulated trade-update disconnect")
        runtime._running = False

    runtime._run_stream_worker("pb-trade-updates", flaky)

    assert states == ["running", "degraded"]


def test_clean_reconcile_restores_a_blip_degraded_runtime() -> None:
    """A blip while a reconciliation reason is set stays degraded (the blip
    restore never passes a breaker); the clean reconcile that clears the
    reason must un-pin it while the trade-update worker is alive."""
    runtime, worker = _trade_update_worker_runtime()
    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = SimpleNamespace(clear_safety_incident=lambda *_a: None)
    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated trade-update disconnect")
        runtime._running = False

    runtime._run_stream_worker("pb-trade-updates", flaky)
    assert runtime._state == "degraded"

    worker.alive = False  # a dead trade-update worker is never "restored"
    runtime._clear_reconciliation_incidents()
    assert runtime._state == "degraded"

    runtime._circuit_breaker_reason = "reconciliation_failed"
    worker.alive = True
    runtime._running = True
    runtime._clear_reconciliation_incidents()
    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"


@pytest.mark.parametrize("blocking_state", ["halted", "error", "stop_failed", "stopping", "stopped"])
def test_trade_update_blip_never_overwrites_a_blocking_state(blocking_state: str) -> None:
    """A pb-trade-updates failure must not downgrade a halted (or stopping)
    runtime to "degraded"."""
    runtime, _worker = _trade_update_worker_runtime()
    runtime._state = blocking_state
    runtime._circuit_breaker_reason = "reconciliation_failed" if blocking_state == "halted" else None
    states: list[str] = []

    def flaky() -> None:
        states.append(runtime._state)
        if len(states) == 1:
            raise RuntimeError("simulated trade-update disconnect")
        runtime._running = False

    runtime._run_stream_worker("pb-trade-updates", flaky)

    assert states == [blocking_state, blocking_state]
    assert runtime._state == blocking_state


def test_stop_attempts_independent_storage_cleanup_after_portfolio_close_failure(
    monkeypatch: Any,
) -> None:
    """A failed session-finalization write must not leak the ledger/store.

    This is the shutdown analogue of persist-before-submit: independent
    session writes all get an attempt, their shared store remains available
    for retry, and the runtime must not report a clean stop after a failure.
    """

    class CloseSpy:
        def __init__(self, *, fails: bool = False) -> None:
            self.calls = 0
            self.fails = fails

        def close(self, *_args: object) -> None:
            self.calls += 1
            if self.fails:
                raise RuntimeError("injected close failure")

    failed_portfolio = CloseSpy(fails=True)
    later_portfolio = CloseSpy()
    ledger = CloseSpy()
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = False
    runtime._state = "stopped"
    runtime._startup_committed = False
    runtime._startup_cleanup_attempted = False
    runtime._storage_closed = False
    runtime._runtime_session_id = None
    runtime._threads = []
    runtime._portfolio_runtimes = (failed_portfolio, later_portfolio)
    runtime.ledger = ledger
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)

    assert runtime.stop() is False

    assert failed_portfolio.calls == 1
    assert later_portfolio.calls == 1
    # The ledger owns the final shared-store release.  It must be deferred
    # until store-backed session writes succeed or a retry would target a
    # closed SQLite connection.
    assert ledger.calls == 0
    assert runtime._storage_closed is False
    assert runtime._state == "stop_failed"

    failed_portfolio.fails = False
    assert runtime.stop() is True
    assert failed_portfolio.calls == 2
    assert later_portfolio.calls == 2
    assert ledger.calls == 1
    assert runtime._storage_closed is True
    assert runtime._state == "stopped"


def test_stop_retries_runtime_session_finalization_before_ledger_release(
    monkeypatch: Any,
) -> None:
    """A transient session-row write failure retains its retry identity."""

    class Ledger:
        def __init__(self) -> None:
            self.end_calls = 0
            self.close_calls = 0
            self.fail_end = True

        def end_runtime_session(self, session_id: str, state: str) -> None:
            assert session_id == "session-1"
            assert state in {"stopped", "unexpected"}
            self.end_calls += 1
            if self.fail_end:
                raise RuntimeError("injected session write failure")

        def close(self) -> None:
            self.close_calls += 1

    ledger = Ledger()
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = False
    runtime._state = "stopped"
    runtime._startup_committed = True
    runtime._startup_cleanup_attempted = False
    runtime._storage_closed = False
    runtime._runtime_session_id = "session-1"
    runtime._threads = []
    runtime._portfolio_runtimes = ()
    runtime.ledger = ledger
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)

    assert runtime.stop() is False
    assert runtime._runtime_session_id == "session-1"
    assert ledger.end_calls == 1
    assert ledger.close_calls == 0

    ledger.fail_end = False
    assert runtime.stop() is True
    assert runtime._runtime_session_id is None
    assert ledger.end_calls == 2
    assert ledger.close_calls == 1


def test_stop_reports_stream_and_session_cleanup_failures_but_continues(
    monkeypatch: Any,
) -> None:
    """Stream/session failures stay visible without short-circuiting peers."""

    calls: list[str] = []

    class Resource:
        def __init__(self, name: str, *, fails: bool = False) -> None:
            self.name = name
            self.fails = fails

        def stop(self) -> None:
            calls.append(f"stop:{self.name}")
            if self.fails:
                raise RuntimeError("injected stream failure")

        def close(self) -> None:
            calls.append(f"close:{self.name}")
            if self.fails:
                raise RuntimeError("injected session failure")

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = False
    runtime._state = "stopped"
    runtime._startup_committed = False
    runtime._startup_cleanup_attempted = False
    runtime._storage_closed = True
    runtime._runtime_session_id = None
    runtime._threads = []
    runtime._portfolio_runtimes = ()
    runtime.stream = Resource("bars", fails=True)
    runtime.trading_updates = Resource("updates")
    runtime.broker = Resource("broker", fails=True)
    runtime._historical = Resource("history")
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)

    assert runtime.stop() is False

    assert calls == ["stop:bars", "stop:updates", "close:broker", "close:history"]
    assert runtime._state == "stop_failed"


class _FakeMonotonic:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _patch_worker_clock_and_alerts(monkeypatch: Any) -> tuple[_FakeMonotonic, list[str]]:
    clock = _FakeMonotonic()
    emitted: list[str] = []
    monkeypatch.setattr(runtime_module.time, "monotonic", clock)
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **_: emitted.append(kind))
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda _kind: None)
    return clock, emitted


def test_sparse_trade_update_blips_never_accumulate_into_the_restart_limit(monkeypatch: Any) -> None:
    """Each failure follows a long healthy run: it starts a fresh restart
    budget, so blips spread over weeks never reach the (operator-unresettable)
    pb-trade-updates_restart_limit halt, and the backoff stays at its floor."""
    clock, emitted = _patch_worker_clock_and_alerts(monkeypatch)
    runtime, worker = _trade_update_worker_runtime()
    delays: list[float] = []

    def wait(delay: float) -> bool:
        delays.append(delay)
        return False

    runtime._stop_event = SimpleNamespace(wait=wait)
    calls: list[int] = []

    def healthy_then_blip() -> None:
        calls.append(1)
        if len(calls) > 10:
            runtime._running = False
            return
        clock.now += 3_600.0  # an hour of healthy streaming, then a blip
        raise RuntimeError("simulated trade-update disconnect")

    runtime._run_stream_worker("pb-trade-updates", healthy_then_blip)

    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    assert worker.restart_count == 1
    assert delays == [1.0] * 10
    # The first blip latches the reconcile-pending gate and alerts once (D7-5);
    # later blips under the same latch stay silent.
    assert emitted == ["reconciliation_failure"]


@pytest.mark.parametrize("run_seconds", [0.0, 599.0])
def test_trade_update_crash_loop_still_hits_the_restart_limit(monkeypatch: Any, run_seconds: float) -> None:
    """A worker that never stays up for the healthy period keeps its restart
    count: the sixth consecutive failure halts exactly as before."""
    clock, emitted = _patch_worker_clock_and_alerts(monkeypatch)
    runtime, worker = _trade_update_worker_runtime()
    calls: list[int] = []

    def crash() -> None:
        calls.append(1)
        clock.now += run_seconds
        raise RuntimeError("simulated trade-update crash")

    runtime._run_stream_worker("pb-trade-updates", crash)

    assert len(calls) == 6
    assert worker.restart_count == 6
    assert runtime._circuit_breaker_reason == "pb-trade-updates_restart_limit"
    assert runtime._state == "halted"
    # The reconcile-pending latch of the first failure alerts (D7-5), then the halt.
    assert emitted == ["reconciliation_failure", "circuit_breaker"]
