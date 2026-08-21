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
- universe module: scanner persist -> validate/load round trip.
"""

import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.config import Config, EquityUniverseConfig
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    Fill,
    Market,
    OrderIntent,
    PositionSnapshot,
)
from parayabuck.execution.alpaca import AlpacaBroker, OwnershipDriftError
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
)
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
)
from parayabuck.runtime import EngineRuntime, WorkerHealth
from parayabuck.universe.scanner import (
    EquityCandidate,
    EquityUniverseScanner,
    load_active_symbols,
    validate_active_universe,
)
from tests.helpers import apply_entry_fill

pytestmark = pytest.mark.paper_chaos


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
        return AssetTradingRules(symbol, market, 1.0, 1.0, 0.01, 10.0)

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
    )
    return runtime, repository


def _bars(symbols: tuple[str, ...], *, day: datetime | None = None) -> list[Bar]:
    timestamp = day or datetime(2025, 1, 1, tzinfo=UTC)
    return [
        Bar(timestamp, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY)
        for symbol in symbols
    ]


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
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")
    resumed = runtime_b.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]
    assert len(broker.submitted) == 2
    assert runtime_b.advance_rebalance() is None


def test_open_managed_sell_exempts_drift_after_restart() -> None:
    """Level: AlpacaBroker unit. After a restart the repository keeps
    reporting the working managed SELL, and that open order must exempt the
    ownership-drift shortfall (the missing quantity is attributable to the
    in-flight exit). Without the open order the same account fails closed."""
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(
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
            crypto_status="ACTIVE",
        ),
        get_all_positions=lambda: [
            SimpleNamespace(
                symbol="BTC/USD",
                qty="0.2",
                current_price="100000",
                unrealized_pl="0",
                unrealized_intraday_pl="0",
                asset_class="crypto",
                avg_entry_price="100000",
                qty_available="0.0",
            )
        ],
    )
    broker.ledger = None
    broker.portfolio_owned_quantities = lambda: {"BTC/USD": 0.5}
    broker._peak_equity = 0.0
    broker._save_peak_equity = lambda: None
    working_sell = SimpleNamespace(
        symbol="BTC/USD",
        side="sell",
        market=Market.CRYPTO,
        quantity=0.3,
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
    broker.positions = [_position("AAPL", 150)]
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
    # The SELL is canceled terminal: the BUY is re-planned and submitted once.
    _fill(repository, sell, cumulative=sell.quantity / 2, status="canceled")
    broker.open_symbols.discard("AAPL")
    resumed = runtime_b.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buys = [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert [order.symbol for order in buys] == ["MSFT"]
    assert len(broker.submitted) == 2


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
    broker.positions = [_position("AAPL", 150)]
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
    assert recovered is not None and recovered.status == "buy_phase_submitted"
    assert [order.side for order in broker.submitted] == [
        PortfolioOrderSide.SELL,
        PortfolioOrderSide.BUY,
    ]
    rows = repository.orders_for_decision(result.decision_id, side="sell")
    assert [str(row["status"]) for row in rows] == ["canceled"]
    # The rebalance advances to completion afterwards.
    for order in recovered.orders:
        _fill(repository, order)
    broker.positions = [_position("AAPL", 400), _position("MSFT", 200)]
    broker.open_symbols.discard(order.symbol)
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
        risk_reducing=False,
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


# ---------------------------------------------------------------------------
# 11. Restart after the universe state was persisted
# ---------------------------------------------------------------------------


def test_restart_after_universe_persisted_loads_validates_and_keeps_pins(
    tmp_path: Path,
) -> None:
    """Level: universe module. A persisted scanner snapshot survives a
    restart: the load path validates it (fresh, digest-matched, canonical)
    and the reference + managed pins are preserved."""
    config = EquityUniverseConfig(
        maximum_stream_symbols=6,
        active_universe_path=str(tmp_path / "active_equity.json"),
        active_universe_max_age_hours=48,
        require_scanner_config_digest_match=True,
    )
    scanner = EquityUniverseScanner(config)
    result = scanner.scan(
        [
            EquityCandidate("AAPL", 200, 600_000_000, 0.03, 1.0),
            EquityCandidate("MSFT", 300, 800_000_000, 0.02, 1.0),
            EquityCandidate("NVDA", 100, 900_000_000, 0.04, 1.0),
        ],
        data_timestamp=datetime.now(UTC),
        # Managed position pin: force-kept even before scoring.
        pinned_symbols=("AAPL",),
    )
    scanner.persist(result)
    # ---- restart: load the persisted snapshot through the validated path ----
    selected, status = validate_active_universe(config)
    assert status == "success"
    assert "SPY" in selected  # configured reference pin preserved
    assert "AAPL" in selected  # managed pin preserved
    assert "MSFT" in selected
    assert "NVDA" in selected
    loaded = load_active_symbols(config)
    assert loaded[0] == "SPY"
    assert set(selected).issubset(loaded)
    # A managed symbol absent from the last scan is re-pinned at load time.
    loaded_with_pin = load_active_symbols(config, pinned_symbols=("TSLA",))
    assert "TSLA" in loaded_with_pin


# ---------------------------------------------------------------------------
# 12. Managed symbol no longer in the scanner universe stays protected
# ---------------------------------------------------------------------------


def test_restart_with_managed_symbol_outside_scanner_universe_stays_pinned(
    tmp_path: Path,
) -> None:
    """Level: EngineRuntime unit. A managed crypto position persisted in the
    ledger is re-pinned on restart even when the scanner universe no longer
    contains the symbol: the pin keeps the symbol observable and protected."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    intent = OrderIntent(
        symbol="BTC/USD",
        market=Market.CRYPTO,
        direction=Direction.LONG,
        qty=0.5,
        entry_price=100_000.0,
        stop_price=90_000.0,
        target_price=120_000.0,
        strategy="crypto",
        signal_id="chaos-crypto-entry",
    )
    ledger.register_entry_order("acct", intent, "broker-entry")
    apply_entry_fill(
        ledger,
        "acct",
        intent,
        Fill(
            intent.client_order_id,
            "BTC/USD",
            0.5,
            100_000.0,
            Direction.LONG,
            datetime.now(UTC),
            0.0,
        ),
        "broker-entry",
    )
    assert [entry.canonical_symbol for entry in ledger.managed_entries_for_account("acct")] == [
        "BTC/USD"
    ]

    base = Config()
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.app_config = replace(
        base,
        universe=replace(
            base.universe,
            crypto=replace(
                base.universe.crypto,
                enabled=True,
                dynamic_selection_enabled=True,
            ),
        ),
    )
    runtime.broker = SimpleNamespace(
        account_id="acct",
        account=lambda: AccountSnapshot(100_000, 100_000, 100_000, 0.0, positions=()),
        portfolio_open_order_symbols=lambda: set(),
    )
    runtime.ledger = ledger
    runtime._portfolio_repository = None
    # Scanner universe (e.g. persisted snapshot) no longer includes BTC/USD.
    runtime._symbols_by_market = {
        Market.EQUITY: {"SPY", "AAPL"},
        Market.CRYPTO: set(),
    }
    runtime._strategy_enabled = {Market.EQUITY: True, Market.CRYPTO: True}
    runtime.market_state = SimpleNamespace(allowed_trade_symbols=set())
    runtime._not_ready_symbols = set()
    runtime.symbols = sorted(runtime._symbols_by_market[Market.EQUITY])
    runtime._pin_managed_symbols()
    assert "BTC/USD" in runtime._managed_pin_symbols
    assert "BTC/USD" in runtime._symbols_by_market[Market.CRYPTO]
    ledger.close()


def test_stream_worker_restart_limit_alerts_once_not_per_retry(monkeypatch: Any) -> None:
    """The circuit-breaker alert/metric fire once at the restart-limit
    crossing; retries past the limit keep the halt asserted but must not
    re-emit every backoff iteration (alert spam). A verified reconnect resets
    restart_count, which re-arms the edge."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    worker = WorkerHealth(started_at=now, last_heartbeat=now, restart_count=5, alive=True)
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
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)

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
