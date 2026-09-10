from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.config import Config
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    RiskOverlayDecision,
)
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum import EquityResidualMomentum1dV1
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import (
    REBALANCE_PHASE_BLOCKED,
    REBALANCE_PHASE_SELL_PENDING,
    PortfolioRuntime,
    PortfolioStateRepository,
    configuration_digest,
)


class _Broker:
    def __init__(self) -> None:
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(100_000, 100_000, 100_000, 0.0)

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(symbol, market, 0.01, 0.01, 0.01, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


def _fixture_strategy() -> EquityResidualMomentum1dV1:
    """A real product strategy (frozen v1 baseline) with reduced windows so
    the 220-bar runtime fixture satisfies warmup and produces targets."""
    return EquityResidualMomentum1dV1(
        {
            "formation_bars": 60,
            "skip_bars": 5,
            "beta_window_bars": 66,
            "residual_vol_window": 20,
            "top_n": 2,
            "exclude_symbols": ["SPY"],
        }
    )


class _TestUniverse:
    """Permissive PIT universe for runtime tests: contains all fixture symbols."""

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return True


def _runtime(tmp_path, broker: _Broker, *, mode: str = "paper", cap: float = 0.0):
    return PortfolioRuntime(
        _fixture_strategy(),
        EQUITY_UNIVERSE,
        broker,
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        mode=mode,
        maximum_allocation=0.50,
        live_dollar_cap=cap,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
    )


def _bars() -> list[Bar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(220):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(EQUITY_UNIVERSE):
            close = 100 * (1 + 0.0003 + offset * 0.00001) ** index
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close,
                    close,
                    close,
                    close,
                    1_000_000,
                    Market.EQUITY,
                )
            )
    return bars


def test_paper_runtime_needs_no_research_manifest_and_is_idempotent(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    result = runtime.process_closed_bars(_bars())
    assert result.status == "submitted"
    assert result.orders
    assert len(broker.submitted) == len(result.orders)
    assert runtime.process_closed_bars(_bars()).status == "duplicate_decision"
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM portfolio_decisions").fetchone()[0] == 1


def test_live_runtime_requires_a_positive_hard_cap(tmp_path) -> None:
    with pytest.raises(ValueError, match="positive hard dollar cap"):
        _runtime(tmp_path, _Broker(), mode="live")


def test_live_runtime_scales_allocation_to_hard_cap(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker, mode="live", cap=1_000)
    result = runtime.process_closed_bars(_bars())
    assert result.risk is not None
    assert result.risk.gross_after <= 0.0100001


def test_runtime_rejects_partial_strategy_batch_even_when_top_n_is_ready(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    bars = _bars()
    last_timestamp = max(bar.timestamp for bar in bars)

    result = runtime.process_closed_bars(
        [bar for bar in bars if not (bar.timestamp == last_timestamp and bar.symbol == "AAPL")]
    )

    assert result.status == "incomplete_strategy_batch"
    assert not result.orders
    assert not broker.submitted
    next_timestamp = last_timestamp + timedelta(days=1)
    next_batch = [
        Bar(
            next_timestamp,
            symbol,
            "1d",
            100.0,
            100.0,
            100.0,
            100.0,
            1_000_000,
            Market.EQUITY,
        )
        for symbol in EQUITY_UNIVERSE
    ]
    assert runtime.process_closed_bars(next_batch).status == "not_scheduled"


def test_restart_restores_last_decision_boundary_without_extra_rebalance(tmp_path) -> None:
    initial_bars = _bars()
    first = _runtime(tmp_path, _Broker())
    assert first.process_closed_bars(initial_bars).status == "submitted"
    first.close()

    restarted = _runtime(tmp_path, _Broker())
    restarted.seed(initial_bars)
    previous = max(bar.timestamp for bar in initial_bars)
    next_timestamp = previous + timedelta(days=1)
    next_bars = [
        Bar(
            next_timestamp,
            symbol,
            "1d",
            110.0,
            110.0,
            110.0,
            110.0,
            1_000_000,
            Market.EQUITY,
        )
        for symbol in EQUITY_UNIVERSE
    ]

    assert restarted.process_closed_bars(next_bars).status == "not_scheduled"


def test_live_policy_digest_is_independent_of_ephemeral_active_symbols(tmp_path) -> None:
    strategy = _fixture_strategy()
    limits = PortfolioRiskLimits()
    first_runtime = PortfolioRuntime(
        strategy,
        EQUITY_UNIVERSE,
        _Broker(),
        PortfolioStateRepository(tmp_path / "first.sqlite3"),
        mode="live",
        maximum_allocation=0.50,
        risk_limits=limits,
        universe=_TestUniverse(),
        universe_version="scanner-policy",
        live_dollar_cap=1_000,
    )
    second_runtime = PortfolioRuntime(
        strategy,
        EQUITY_UNIVERSE[:-1],
        _Broker(),
        PortfolioStateRepository(tmp_path / "second.sqlite3"),
        mode="live",
        maximum_allocation=0.50,
        risk_limits=limits,
        universe=_TestUniverse(),
        universe_version="scanner-policy",
        live_dollar_cap=1_000,
    )

    assert first_runtime.configuration_digest == second_runtime.configuration_digest
    assert first_runtime.configuration_digest != configuration_digest(strategy, "live", 0.50, "changed-policy", limits)


def test_portfolio_fill_chain_is_idempotent_and_keeps_pnl_fee_slippage_separate(
    tmp_path,
) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    result = runtime.process_closed_bars(_bars())
    order = result.orders[0]
    repository = runtime.repository
    for _ in range(2):
        repository.apply_trade_update(
            client_order_id=order.client_order_id,
            broker_order_id="broker-1",
            status="fill",
            cumulative_filled_quantity=order.quantity,
            average_fill_price=order.reference_price + 0.05,
            occurred_at=order.decision_timestamp,
            activity_id="activity-1",
            fee=1.25,
            realized_pnl=12.0,
            unrealized_pnl=-3.0,
        )
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        row = connection.execute("SELECT fee, realized_pnl, unrealized_pnl, slippage FROM portfolio_fills").fetchone()
        assert row[:3] == (1.25, 12.0, -3.0)
        assert row[3] != 0
        assert connection.execute("SELECT COUNT(*) FROM portfolio_fills").fetchone()[0] == 1


EQUITY_UNIVERSE = ("AAPL", "MSFT", "NVDA", "SPY")


class _FakeAlpacaClient:
    """Minimal Alpaca REST surface used by the portfolio execution path."""

    def __init__(self) -> None:
        self.positions: list[SimpleNamespace] = []
        self.submitted: list[object] = []
        self.open_orders: list[SimpleNamespace] = []
        self.filled_orders: dict[str, SimpleNamespace] = {}

    def get_order_by_client_id(self, client_order_id: str) -> SimpleNamespace | None:
        return self.filled_orders.get(client_order_id)

    def get_order_by_id(self, order_id: str) -> SimpleNamespace | None:
        for order in self.filled_orders.values():
            if order.id == order_id:
                return order
        return None

    def get_account(self) -> SimpleNamespace:
        return SimpleNamespace(
            id="acct",
            account_number="acct",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
            equity="100000",
            last_equity="100000",
            cash="90000",
            non_marginable_buying_power="100000",
            crypto_status="ACTIVE",
        )

    def get_all_positions(self) -> list[SimpleNamespace]:
        return self.positions

    def get_open_position(self, symbol: str) -> SimpleNamespace:
        # Alpaca resolves both the canonical and legacy crypto forms to the
        # same position; the broker queries by the legacy asset symbol.
        wanted = canonical_symbol(symbol)
        for position in self.positions:
            if canonical_symbol(position.symbol) == wanted:
                return position
        raise ValueError(f"no open position for {symbol}")

    def get_asset(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(
            symbol=symbol,
            tradable=True,
            min_order_size="0.01",
            min_trade_increment="0.01",
            price_increment="0.01",
            min_order_notional="1.0",
        )

    def get_orders(self, *_args: object, **_kwargs: object) -> list[SimpleNamespace]:
        return self.open_orders

    def submit_order(self, request: object) -> SimpleNamespace:
        self.submitted.append(request)
        return SimpleNamespace(id=f"broker-{getattr(request, 'client_order_id', '')}")


def _portfolio_broker(
    client: _FakeAlpacaClient,
    repo: PortfolioStateRepository,
    tmp_path=None,
    *,
    with_ledger: bool = True,
) -> AlpacaBroker:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    ledger_path = tmp_path / "execution.sqlite3" if tmp_path is not None else None
    broker.ledger = ExecutionLedger(str(ledger_path)) if with_ledger and ledger_path is not None else None
    broker.portfolio_owned_quantities = lambda: repo.owned_quantities(broker.account_id)
    broker.portfolio_open_orders = lambda: repo.open_orders(broker.account_id)
    broker._account_id = "acct"
    broker._client = client
    broker._asset_cache = {}
    broker._symbol_exit_locks = {}
    broker.crypto_emergency_exit_bps = 75.0
    broker.crypto_stop_limit_offset_bps = 25.0
    broker.crypto_stop_cancel_timeout_seconds = 5.0
    broker.crypto_stop_cancel_poll_interval_seconds = 0.25
    broker.manage_external_positions = False
    broker._peak_equity = 0.0
    broker._peak_state_path = Path(tmp_path) / "peak.json" if tmp_path is not None else Path("/tmp/peak.json")
    return broker


def test_portfolio_buy_fill_is_managed_and_full_exit_returns_to_external(
    tmp_path,
) -> None:
    """The P0-3 chain: BUY submit -> fill -> account snapshot managed.

    A portfolio-owned position must be visible as ``managed`` to the broker
    account snapshot (so the planner may SELL it and risk treats it as
    mutable), and a fully exited position must revert to ``external``.
    """
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    broker = _portfolio_broker(client, repo, tmp_path)
    runtime = PortfolioRuntime(
        _fixture_strategy(),
        EQUITY_UNIVERSE,
        broker,
        repo,
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
        account_id="acct",
    )

    result = runtime.process_closed_bars(_bars())
    assert result.status == "submitted"
    buy_orders = [order for order in result.orders if order.side == PortfolioOrderSide.BUY]
    assert buy_orders

    for order in buy_orders:
        repo.apply_trade_update(
            client_order_id=order.client_order_id,
            broker_order_id=f"broker-{order.client_order_id}",
            status="fill",
            cumulative_filled_quantity=order.quantity,
            average_fill_price=order.reference_price,
            occurred_at=order.decision_timestamp,
            activity_id=f"activity-{order.client_order_id}",
            fee=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )
    client.positions = [
        SimpleNamespace(
            symbol=order.symbol,
            qty=str(order.quantity),
            avg_entry_price=str(order.reference_price),
            current_price=str(order.reference_price),
            qty_available=str(order.quantity),
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="us_equity",
        )
        for order in buy_orders
    ]

    snapshot = broker.account()
    owned = {position.symbol for position in snapshot.positions}
    assert owned == {order.symbol for order in buy_orders}
    assert all(position.ownership == "managed" for position in snapshot.positions)
    assert repo.owned_symbols("acct") == owned

    # Full exit: SELL fills reduce net quantity to zero.
    for order in buy_orders:
        repo.record_order(
            PortfolioOrderRequest(
                client_order_id=f"exit-{order.client_order_id}",
                decision_id=result.decision_id or "exit-decision",
                symbol=order.symbol,
                market=order.market,
                side=PortfolioOrderSide.SELL,
                quantity=order.quantity,
                reference_price=order.reference_price,
                decision_timestamp=order.decision_timestamp,
                strategy_name=order.strategy_name,
                strategy_version=order.strategy_version,
                risk_reducing=True,
            ),
            "submitting",
        )
        repo.apply_trade_update(
            client_order_id=f"exit-{order.client_order_id}",
            broker_order_id=f"broker-exit-{order.client_order_id}",
            status="fill",
            cumulative_filled_quantity=order.quantity,
            average_fill_price=order.reference_price,
            occurred_at=order.decision_timestamp,
            activity_id=f"exit-activity-{order.client_order_id}",
            fee=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )
    assert repo.owned_symbols("acct") == set()

    exited_snapshot = broker.account()
    assert all(position.ownership == "external" for position in exited_snapshot.positions)


def test_portfolio_ownership_survives_runtime_restart(tmp_path) -> None:
    """Ownership derives from the persisted repository, not process memory."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    first = PortfolioRuntime(
        _fixture_strategy(),
        EQUITY_UNIVERSE,
        _portfolio_broker(client, repo, tmp_path),
        repo,
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
        account_id="acct",
    )
    result = first.process_closed_bars(_bars())
    order = next(o for o in result.orders if o.side == PortfolioOrderSide.BUY)
    repo.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-1",
        status="fill",
        cumulative_filled_quantity=order.quantity,
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id="activity-1",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    client.positions = [
        SimpleNamespace(
            symbol=order.symbol,
            qty=str(order.quantity),
            avg_entry_price=str(order.reference_price),
            current_price=str(order.reference_price),
            qty_available=str(order.quantity),
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="us_equity",
        )
    ]
    first.close()

    restarted_broker = _portfolio_broker(client, repo, tmp_path)
    restarted_snapshot = restarted_broker.account()
    assert order.symbol in {position.symbol for position in restarted_snapshot.positions}
    assert (
        next(position for position in restarted_snapshot.positions if position.symbol == order.symbol).ownership
        == "managed"
    )


def test_partial_exit_keeps_ownership_until_fully_flat(tmp_path) -> None:
    """A partial SELL reduces the net position but ownership persists until the
    last share is gone; only a full exit reverts the symbol to external."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    runtime = PortfolioRuntime(
        _fixture_strategy(),
        EQUITY_UNIVERSE,
        _portfolio_broker(client, repo, tmp_path),
        repo,
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
        account_id="acct",
    )
    result = runtime.process_closed_bars(_bars())
    order = next(o for o in result.orders if o.side == PortfolioOrderSide.BUY)
    repo.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-1",
        status="fill",
        cumulative_filled_quantity=order.quantity,
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id="activity-1",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    assert order.symbol in repo.owned_symbols("acct")

    partial = order.quantity / 2
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id=f"exit-{order.client_order_id}",
            decision_id=result.decision_id or "exit-decision",
            symbol=order.symbol,
            market=order.market,
            side=PortfolioOrderSide.SELL,
            quantity=partial,
            reference_price=order.reference_price,
            decision_timestamp=order.decision_timestamp,
            strategy_name=order.strategy_name,
            strategy_version=order.strategy_version,
            risk_reducing=True,
        ),
        "submitting",
    )
    repo.apply_trade_update(
        client_order_id=f"exit-{order.client_order_id}",
        broker_order_id="broker-exit",
        status="fill",
        cumulative_filled_quantity=partial,
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id="exit-activity-1",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    # Still half-owned: managed.
    assert order.symbol in repo.owned_symbols("acct")


def test_mixed_position_splits_managed_and_external_quantity(tmp_path) -> None:
    """A manually opened 10-share AAPL lot plus a bot-owned 2 shares must
    expose managed_qty=2/external_qty=10 so the planner can only sell 2."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    repo.begin_session("acct", "digest", "paper")
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="AAPL",
            qty="12",
            avg_entry_price="100",
            current_price="100",
            qty_available="12",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="us_equity",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    broker.portfolio_owned_quantities = lambda: {"AAPL": 2.0}

    position = broker.account().positions[0]

    assert position.ownership == "mixed"
    assert position.managed_qty == 2.0
    assert position.external_qty == 10.0
    assert position.qty == 12.0


def test_duplicate_canonical_broker_positions_fail_closed(tmp_path) -> None:
    """Two broker rows for one canonical symbol must never be merged silently."""
    from parayabuck.execution.alpaca import OwnershipDriftError

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="AAPL",
            qty="2",
            avg_entry_price="100",
            current_price="100",
            qty_available="2",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="us_equity",
        ),
        SimpleNamespace(
            symbol="aapl",
            qty="3",
            avg_entry_price="101",
            current_price="101",
            qty_available="3",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="us_equity",
        ),
    ]
    broker = _portfolio_broker(client, repo, tmp_path)

    with pytest.raises(OwnershipDriftError, match="Duplicate broker position"):
        broker.account()


def test_emergency_stop_fill_releases_portfolio_ownership(tmp_path) -> None:
    """Stop submit -> fill -> repository net quantity zero -> external/flat:
    the full protective-stop lifecycle must be visible to the portfolio
    repository, otherwise a fill leaves stale managed ownership behind."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session_id = repo.begin_session("acct", "digest", "paper")
    decision_timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    decision_id = repo.record_decision(
        session_id,
        "acct",
        "digest",
        _decision_target(decision_timestamp),
        _risk_decision(decision_timestamp),
        Market.CRYPTO,
    )
    entry = PortfolioOrderRequest(
        client_order_id="pbp-entry",
        decision_id=decision_id,
        symbol="BTC/USD",
        market=Market.CRYPTO,
        side=PortfolioOrderSide.BUY,
        quantity=0.5,
        reference_price=100000.0,
        decision_timestamp=decision_timestamp,
        strategy_name="crypto",
        strategy_version="1",
        risk_reducing=False,
    )
    repo.record_order(entry, "submitting")
    repo.apply_trade_update(
        client_order_id="pbp-entry",
        broker_order_id="broker-entry",
        status="fill",
        cumulative_filled_quantity=0.5,
        average_fill_price=100000.0,
        occurred_at=decision_timestamp,
        activity_id="activity-entry",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    # The buy fill is booked net of the crypto taker fee (the acquired
    # position is 0.25% smaller than the reported fill).
    assert repo.owned_quantities("acct") == {"BTC/USD": 0.49875}

    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.49875",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.49875",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    recorded: dict[str, str] = {}

    def record_stop(symbol: str, market: Market, qty: float, client_order_id: str, reference_price: float) -> None:
        recorded["id"] = client_order_id
        repo.record_protective_order("acct", client_order_id, symbol, market, qty, reference_price)

    broker.portfolio_stop_recorded = record_stop

    broker.reconcile()

    assert recorded["id"]
    assert repo.has_order(recorded["id"])

    # The protective stop fills: the SELL leg decrements net ownership even
    # though the broker position may still be open in the snapshot. The stop
    # can only ever sell the acquired (net) quantity.
    repo.apply_trade_update(
        client_order_id=recorded["id"],
        broker_order_id="broker-stop",
        status="fill",
        cumulative_filled_quantity=0.49875,
        average_fill_price=99000.0,
        occurred_at=decision_timestamp,
        activity_id="activity-stop",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    assert repo.owned_quantities("acct") == {}
    assert repo.owned_symbols("acct") == set()

    snapshot = broker.account()
    assert snapshot.positions[0].ownership == "external"
    assert snapshot.positions[0].managed_qty == 0.0


def _owned_crypto_entry(repo: PortfolioStateRepository, *, quantity: float) -> None:
    session_id = repo.begin_session("acct", "digest", "paper")
    decision_timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    decision_id = repo.record_decision(
        session_id,
        "acct",
        "digest",
        _decision_target(decision_timestamp),
        _risk_decision(decision_timestamp),
        Market.CRYPTO,
    )
    entry = PortfolioOrderRequest(
        client_order_id="pbp-entry",
        decision_id=decision_id,
        symbol="BTC/USD",
        market=Market.CRYPTO,
        side=PortfolioOrderSide.BUY,
        quantity=quantity,
        reference_price=100000.0,
        decision_timestamp=decision_timestamp,
        strategy_name="crypto",
        strategy_version="1",
        risk_reducing=False,
    )
    repo.record_order(entry, "submitting")
    repo.apply_trade_update(
        client_order_id="pbp-entry",
        broker_order_id="broker-entry",
        status="fill",
        cumulative_filled_quantity=quantity,
        average_fill_price=100000.0,
        occurred_at=decision_timestamp,
        activity_id="activity-entry",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


class _RetryStopClient(_FakeAlpacaClient):
    """Fake whose first stop submission is rejected with an available-qty
    error, forcing the broker's internal retry to use a fresh order id."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_first_stop = True

    def submit_order(self, request: object) -> SimpleNamespace:
        if self.fail_first_stop:
            self.fail_first_stop = False
            import json

            raise RuntimeError(json.dumps({"available": 0.4}))
        self.submitted.append(request)
        return SimpleNamespace(id=f"broker-{getattr(request, 'client_order_id', '')}")


def test_emergency_stop_retry_fill_is_attributed_to_accepted_order_id(tmp_path) -> None:
    """When the first stop submission is rejected and the broker retries with
    a fresh client order id, the recorded stop must use the accepted id so the
    eventual fill decrements the repository-owned quantity."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    client = _RetryStopClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.5",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.5",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    recorded: dict[str, str] = {}

    def record_stop(symbol: str, market: Market, qty: float, client_order_id: str, reference_price: float) -> None:
        recorded["id"] = client_order_id
        repo.record_protective_order("acct", client_order_id, symbol, market, qty, reference_price)

    broker.portfolio_stop_recorded = record_stop

    broker.reconcile()

    assert recorded["id"], "the accepted (retried) stop id must be recorded"
    assert repo.has_order(recorded["id"])
    assert len(client.submitted) == 1, "first submission rejected, retry accepted"

    # The retry accepted only 0.4 of the 0.5 position; its fill decrements the
    # owned quantity by exactly that amount.
    repo.apply_trade_update(
        client_order_id=recorded["id"],
        broker_order_id="broker-stop",
        status="fill",
        cumulative_filled_quantity=0.4,
        average_fill_price=99000.0,
        occurred_at=datetime(2025, 1, 1, tzinfo=UTC),
        activity_id="activity-stop",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    assert repo.owned_quantities("acct") == pytest.approx({"BTC/USD": 0.09875})


def test_reconcile_clears_stale_ownership_when_broker_is_flat(tmp_path) -> None:
    """A missed stop fill or a manual sell leaves the repository claiming a
    managed position the broker no longer holds; reconcile must clear it."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    assert repo.owned_quantities("acct") == {"BTC/USD": 0.49875}

    client = _FakeAlpacaClient()  # no positions, no open orders
    broker = _portfolio_broker(client, repo, tmp_path)
    broker.portfolio_flat_reconciled = lambda symbol, market: repo.clear_stale_ownership("acct", symbol, market)

    broker.reconcile()

    assert repo.owned_quantities("acct") == {}
    assert repo.owned_symbols("acct") == set()


def test_reconcile_keeps_ownership_when_broker_still_holds_the_position(tmp_path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.5",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.5",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    broker.portfolio_flat_reconciled = lambda symbol, market: repo.clear_stale_ownership("acct", symbol, market)

    broker.reconcile()

    assert repo.owned_quantities("acct") == {"BTC/USD": 0.49875}


def test_reconcile_protects_portfolio_owned_crypto_with_emergency_stop(
    tmp_path,
) -> None:
    """P0-4: portfolio crypto positions without a ledger lot still get a stop."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.5",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.5",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    broker.portfolio_owned_quantities = lambda: {"BTC/USD": 0.5}

    broker.reconcile()

    stops = [order for order in client.submitted if getattr(order, "side", None) == "sell"]
    assert len(stops) == 1
    expected_stop = 100_000 * (1 - 75.0 / 10_000)
    assert float(stops[0].stop_price) == pytest.approx(expected_stop)


def test_reconcile_keeps_full_size_crypto_stop_in_fee_reserve_band(tmp_path) -> None:
    """Regression: with qty_available == 0 the required quantity is
    fee-adjusted, so the engine's own full-size protective stop reads
    "over". It must be kept — not cancelled and re-submitted every cycle
    (which raised "No available quantity to protect" on the stale
    pre-cancel snapshot and left the position unprotected)."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.5",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0",  # fully locked by the working stop
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    client.open_orders = [
        SimpleNamespace(
            id="stop-1",
            client_order_id="parayabuck-stop-aaaa",
            symbol="BTC/USD",
            side="sell",
            type="stop",
            qty="0.5",
            stop_price="99000",
            status="accepted",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    broker.portfolio_owned_quantities = lambda: {"BTC/USD": 0.5}

    broker.reconcile()

    assert client.submitted == []  # no cancel/resubmit churn
    assert client.open_orders  # the healthy stop is still in place


def test_reconcile_counts_only_remaining_qty_of_partially_filled_stop(tmp_path) -> None:
    """A partially filled protective stop protects only its still-working
    remainder: the filled portion must not be counted as protection, and
    reconcile must keep the remainder (not churn it)."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="PAXG/USD",
            qty="0.862154915",  # position after the stop sold 0.2608
            avg_entry_price="4364.597",
            current_price="4330",
            qty_available="0",  # fully locked by the working stop
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    client.open_orders = [
        SimpleNamespace(
            id="stop-1",
            client_order_id="parayabuck-stop-bbbb",
            symbol="PAXG/USD",
            side="sell",
            type="stop_limit",
            qty="1.122930907",
            filled_qty="0.260775992",
            stop_price="4331.86",
            status="accepted",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    broker.portfolio_owned_quantities = lambda: {"PAXG/USD": 0.862154915}

    broker.reconcile()

    assert client.submitted == []  # no re-submission of the working remainder
    assert client.open_orders  # the still-working stop is kept


def _decision_target(timestamp: datetime) -> TargetPortfolio:
    return TargetPortfolio(
        weights=(),
        cash_weight=1.0,
        decision_timestamp=timestamp,
        strategy_name="eq",
        strategy_version="1",
    )


def _risk_decision(timestamp: datetime) -> RiskOverlayDecision:
    return RiskOverlayDecision(_decision_target(timestamp), False, (), 0.0, 0.0)


def test_owned_quantities_are_isolated_per_account(tmp_path) -> None:
    """A stale paper session must not mark symbols as owned for another
    (live) account sharing the same repository file."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    paper = repo.begin_session("paper-acct", "digest-a", "paper")
    live = repo.begin_session("live-acct", "digest-b", "live")
    base = PortfolioOrderRequest(
        client_order_id="order-1",
        decision_id="",
        symbol="AAPL",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=10.0,
        reference_price=100.0,
        decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        strategy_name="eq",
        strategy_version="1",
        risk_reducing=False,
    )
    paper_decision = repo.record_decision(
        paper,
        "paper-acct",
        "digest-a",
        _decision_target(base.decision_timestamp),
        _risk_decision(base.decision_timestamp),
        Market.EQUITY,
    )
    repo.record_decision(
        live,
        "live-acct",
        "digest-b",
        _decision_target(base.decision_timestamp),
        _risk_decision(base.decision_timestamp),
        Market.EQUITY,
    )
    base = dataclasses.replace(base, decision_id=paper_decision)
    repo.record_order(base, "submitting")
    repo.apply_trade_update(
        client_order_id="order-1",
        broker_order_id="broker-1",
        status="fill",
        cumulative_filled_quantity=10.0,
        average_fill_price=100.0,
        occurred_at=base.decision_timestamp,
        activity_id="activity-1",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    assert repo.owned_quantities("paper-acct") == {"AAPL": 10.0}
    assert repo.owned_symbols("paper-acct") == {"AAPL"}
    assert repo.owned_quantities("live-acct") == {}
    assert repo.owned_symbols("live-acct") == set()


def test_decision_identity_is_scoped_to_account_and_configuration(tmp_path) -> None:
    """Two accounts sharing the same configuration digest and decision
    timestamp must produce distinct, coexistable decisions."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session_a = repo.begin_session("acct-a", "same-digest", "paper")
    session_b = repo.begin_session("acct-b", "same-digest", "paper")
    timestamp = datetime(2026, 8, 10, tzinfo=UTC)
    id_a = repo.record_decision(
        session_a,
        "acct-a",
        "same-digest",
        _decision_target(timestamp),
        _risk_decision(timestamp),
        Market.EQUITY,
    )
    id_b = repo.record_decision(
        session_b,
        "acct-b",
        "same-digest",
        _decision_target(timestamp),
        _risk_decision(timestamp),
        Market.EQUITY,
    )
    assert id_a != id_b
    assert repo.has_decision("acct-a", "same-digest", timestamp, Market.EQUITY)
    assert repo.has_decision("acct-b", "same-digest", timestamp, Market.EQUITY)
    assert not repo.has_decision("acct-c", "same-digest", timestamp, Market.EQUITY)
    assert repo.latest_decision_timestamp("acct-a", "same-digest", Market.EQUITY) == timestamp
    assert repo.latest_decision_timestamp("acct-b", "same-digest", Market.EQUITY) == timestamp
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM portfolio_decisions").fetchone()[0] == 2


def test_partial_sell_fill_is_booked_before_the_drift_snapshot(tmp_path) -> None:
    """A SELL fill that brings the broker position below the repository-owned
    quantity must be booked before the account snapshot; snapshotting first
    would trip the fail-closed drift check and lose the fill forever."""
    import asyncio

    from parayabuck.core.models import OrderRole
    from parayabuck.execution.alpaca_updates import TradeUpdate
    from parayabuck.runtime import EngineRuntime

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    with repo.store.lock, repo.store.connection:
        entry_decision_id = repo.store.connection.execute(
            "SELECT decision_id FROM portfolio_orders WHERE client_order_id = 'pbp-entry'"
        ).fetchone()[0]
    # A SELL exit order for the full quantity.
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="pbp-sell",
            decision_id=str(entry_decision_id),
            symbol="BTC/USD",
            market=Market.CRYPTO,
            side=PortfolioOrderSide.SELL,
            quantity=0.5,
            reference_price=100000.0,
            decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            strategy_name="crypto",
            strategy_version="1",
            risk_reducing=True,
        ),
        "submitting",
    )
    client = _FakeAlpacaClient()
    # The broker already reflects the partial fill.
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.2",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.2",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._portfolio_repository = repo
    runtime.broker = broker
    runtime._last_account_at = None
    runtime._persist_risk_state = lambda *args, **kwargs: None

    update = TradeUpdate(
        event="fill",
        client_order_id="pbp-sell",
        broker_order_id="broker-sell",
        parent_order_id=None,
        role=OrderRole.MANUAL_EXIT,
        symbol="BTC/USD",
        market=Market.CRYPTO,
        requested_qty=0.5,
        filled_qty=0.3,
        average_fill_price=99000.0,
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        fee=0.0,
        activity_id="activity-sell-1",
    )
    asyncio.run(runtime._handle_trade_update(update))

    assert repo.owned_quantities("acct") == pytest.approx({"BTC/USD": 0.19875})


def test_terminal_fill_invalidates_cached_account_snapshot(tmp_path) -> None:
    """A fill cannot leave the next decision trusting a pre-fill snapshot."""
    import asyncio

    from parayabuck.core.models import OrderRole
    from parayabuck.execution.alpaca_updates import TradeUpdate
    from parayabuck.runtime import EngineRuntime

    stale = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    held = PositionSnapshot(
        "AAPL",
        1.0,
        100.0,
        100.0,
        Market.EQUITY,
        qty_available=1.0,
        ownership="managed",
        managed_qty=1.0,
    )
    fresh = AccountSnapshot(99_900, 100_000, 100_000, 0.0, positions=(held,))

    class _Repository:
        def has_order(self, client_order_id: str) -> bool:
            return client_order_id == "buy-1"

        def apply_trade_update(self, **_kwargs: object) -> None:
            return None

    class _Broker:
        account_id = "acct"

        def __init__(self) -> None:
            self.account_calls = 0

        def snapshot_if_fresh(self, _max_age: float) -> AccountSnapshot:
            return stale

        def account(self) -> AccountSnapshot:
            self.account_calls += 1
            return fresh

    broker = _Broker()
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._portfolio_repository = _Repository()
    runtime.broker = broker
    now = datetime.now(UTC)
    runtime._last_account_at = now
    runtime._last_account_snapshot = stale
    runtime._last_account_snapshot_at = now
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=15.0,
        maximum_account_age_for_entry_seconds=30.0,
    )
    runtime._persist_risk_state = lambda *_args, **_kwargs: None
    runtime._advance_portfolio_rebalance = lambda _market: None
    update = TradeUpdate(
        event="fill",
        client_order_id="buy-1",
        broker_order_id="broker-buy-1",
        parent_order_id=None,
        role=OrderRole.ENTRY,
        symbol="AAPL",
        market=Market.EQUITY,
        requested_qty=1.0,
        filled_qty=1.0,
        average_fill_price=100.0,
        timestamp=now,
        fee=0.0,
        activity_id="activity-buy-1",
    )

    asyncio.run(runtime._handle_trade_update(update))

    assert runtime._fresh_account_snapshot() is fresh
    assert broker.account_calls == 1


def test_inflight_account_fetch_cannot_republish_after_execution_invalidation() -> None:
    """An execution invalidation racing an account fetch must force a second
    broker snapshot instead of publishing the pre-fill result as fresh."""
    from parayabuck.runtime import EngineRuntime

    stale = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    fresh = AccountSnapshot(99_900, 100_000, 100_000, 0.0)
    started = threading.Event()
    release = threading.Event()

    class _Broker:
        account_id = "acct"

        def __init__(self) -> None:
            self.account_calls = 0

        def account(self) -> AccountSnapshot:
            self.account_calls += 1
            if self.account_calls == 1:
                started.set()
                assert release.wait(2.0)
            return fresh

    broker = _Broker()
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = broker
    runtime.config = SimpleNamespace(maximum_account_age_for_entry_seconds=30.0)
    now = datetime.now(UTC)
    runtime._last_account_snapshot = stale
    runtime._last_account_snapshot_at = now - timedelta(seconds=60)
    runtime._last_account_at = now - timedelta(seconds=60)
    runtime._account_snapshot_lock = threading.Lock()
    runtime._account_snapshot_generation = 0
    result: list[AccountSnapshot] = []
    errors: list[BaseException] = []

    def fetch() -> None:
        try:
            result.append(runtime._fresh_account_snapshot())
        except BaseException as exc:  # pragma: no cover - diagnostic assertion
            errors.append(exc)

    worker = threading.Thread(target=fetch)
    worker.start()
    assert started.wait(2.0)
    runtime._invalidate_account_snapshot_after_execution()
    release.set()
    worker.join(3.0)

    assert not worker.is_alive()
    assert errors == []
    assert result == [fresh]
    assert broker.account_calls == 2


def test_account_snapshot_generation_lease_rejects_invalidation_before_use() -> None:
    """A decision lease must fail closed when a fill invalidates its cache."""
    from parayabuck.runtime import EngineRuntime

    account = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.config = SimpleNamespace(maximum_account_age_for_entry_seconds=30.0)
    runtime._account_snapshot_lock = threading.Lock()
    runtime._account_snapshot_generation = 0
    runtime._last_account_snapshot = account
    runtime._last_account_snapshot_at = datetime.now(UTC)

    lease = runtime._fresh_account_snapshot_lease()

    runtime._invalidate_account_snapshot_after_execution()

    assert lease.snapshot is account
    assert lease.generation == 0
    assert not runtime._account_snapshot_lease_is_current(lease)


def test_account_snapshot_lease_defers_interleaved_invalidation_until_release() -> None:
    """An invalidation after the atomic claim cannot interrupt decision use."""
    from parayabuck.runtime import EngineRuntime

    account = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.config = SimpleNamespace(maximum_account_age_for_entry_seconds=30.0)
    runtime._account_snapshot_lock = threading.Lock()
    runtime._account_snapshot_generation = 0
    runtime._last_account_snapshot = account
    runtime._last_account_snapshot_at = datetime.now(UTC)

    lease = runtime._fresh_account_snapshot_lease()
    assert runtime._claim_account_snapshot_lease(lease)

    # Deterministic interleaving: an execution arrives after claim but before
    # the decision finishes. The invalidation is deferred, not lost.
    runtime._invalidate_account_snapshot_after_execution()
    assert runtime._account_snapshot_lease_is_current(lease)

    runtime._release_account_snapshot_lease(lease)
    assert not runtime._account_snapshot_lease_is_current(lease)


def test_portfolio_decision_skips_when_lease_invalidates_at_handoff() -> None:
    """A deterministic fill interleaving cannot enter the planner."""
    from parayabuck.runtime import EngineRuntime

    account = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    calls: list[object] = []
    portfolio_runtime = SimpleNamespace(
        market=Market.EQUITY,
        symbols=("AAPL",),
        strategy=SimpleNamespace(timeframe="1d", name="equity_test"),
        process_closed_bars=lambda *_args, **_kwargs: calls.append(object()),
    )
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._portfolio_lock = threading.RLock()
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._portfolio_runtimes = (portfolio_runtime,)
    runtime._entry_readiness_reasons = lambda _market: ()
    runtime._equity_signal_basis_latch = lambda _timestamp: None
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.config = SimpleNamespace(maximum_account_age_for_entry_seconds=30.0)
    runtime._account_snapshot_lock = threading.Lock()
    runtime._account_snapshot_generation = 0
    runtime._last_account_snapshot = account
    runtime._last_account_snapshot_at = datetime.now(UTC)

    def invalidate_before_use(_lease: object) -> bool:
        runtime._invalidate_account_snapshot_after_execution()
        return False

    runtime._account_snapshot_lease_is_current = invalidate_before_use
    timestamp = datetime.now(UTC)
    runtime._process_portfolio_timestamp(
        [Bar(timestamp, "AAPL", "1d", 100.0, 100.0, 100.0, 100.0, 1_000.0, Market.EQUITY)]
    )

    assert calls == []
    assert runtime._account_snapshot_generation == 1


def test_ownership_drift_is_exempt_while_an_open_order_is_working(tmp_path) -> None:
    """A symbol mid-exit (open order still working) must not trip the
    fail-closed drift check: the shortfall is attributable to the exit."""
    from parayabuck.execution.alpaca import OwnershipDriftError

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    entry_decision_id = repo.store.connection.execute(
        "SELECT decision_id FROM portfolio_orders WHERE client_order_id = 'pbp-entry'"
    ).fetchone()[0]
    # The portfolio path has a working closing SELL for the missing quantity.
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="pbp-exit-sell",
            decision_id=str(entry_decision_id),
            symbol="BTC/USD",
            market=Market.CRYPTO,
            side=PortfolioOrderSide.SELL,
            quantity=0.3,
            reference_price=100000.0,
            decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            strategy_name="crypto",
            strategy_version="1",
            risk_reducing=True,
        ),
        "submitted",
    )
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.2",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.0",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    assert broker.account() is not None

    # A portfolio SELL too small to explain the discrepancy stays fail-closed.
    repo.update_order("pbp-exit-sell", "filled")
    with pytest.raises(OwnershipDriftError, match="Ownership drift"):
        broker.account()


def test_manual_open_buy_does_not_exempt_ownership_drift(tmp_path) -> None:
    """A manual BUY open order never explains missing managed inventory."""
    from parayabuck.execution.alpaca import OwnershipDriftError

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.2",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.0",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    client.open_orders = [SimpleNamespace(symbol="BTC/USD", qty="0.3", side="buy", status="new", type="market")]
    broker = _portfolio_broker(client, repo, tmp_path)
    with pytest.raises(OwnershipDriftError, match="Ownership drift"):
        broker.account()


def test_manual_open_sell_does_not_exempt_ownership_drift(tmp_path) -> None:
    """A manual SELL open order is not repository inventory: drift still
    raises unless manage_external_positions explicitly adopts the order."""
    from parayabuck.execution.alpaca import OwnershipDriftError

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.2",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.0",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    client.open_orders = [SimpleNamespace(symbol="BTC/USD", qty="0.3", side="sell", status="new", type="limit")]
    broker = _portfolio_broker(client, repo, tmp_path)
    with pytest.raises(OwnershipDriftError, match="Ownership drift"):
        broker.account()


def test_portfolio_open_buy_does_not_exempt_ownership_drift(tmp_path) -> None:
    """A portfolio BUY open order cannot explain a missing managed position."""
    from parayabuck.execution.alpaca import OwnershipDriftError

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    entry_decision_id = repo.store.connection.execute(
        "SELECT decision_id FROM portfolio_orders WHERE client_order_id = 'pbp-entry'"
    ).fetchone()[0]
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="pbp-extra-buy",
            decision_id=str(entry_decision_id),
            symbol="BTC/USD",
            market=Market.CRYPTO,
            side=PortfolioOrderSide.BUY,
            quantity=0.3,
            reference_price=100000.0,
            decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            strategy_name="crypto",
            strategy_version="1",
            risk_reducing=False,
        ),
        "submitted",
    )
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.2",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.0",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    with pytest.raises(OwnershipDriftError, match="Ownership drift"):
        broker.account()


def test_portfolio_open_sell_only_exempts_the_explained_quantity(tmp_path) -> None:
    """A portfolio closing SELL exempts drift only up to its remaining qty."""
    from parayabuck.execution.alpaca import OwnershipDriftError

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    entry_decision_id = repo.store.connection.execute(
        "SELECT decision_id FROM portfolio_orders WHERE client_order_id = 'pbp-entry'"
    ).fetchone()[0]
    # The closing SELL is partially filled: 0.15 sold, 0.05 still working.
    # Owned quantity after the fill is 0.35; the working remainder is 0.05.
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="pbp-exit-sell",
            decision_id=str(entry_decision_id),
            symbol="BTC/USD",
            market=Market.CRYPTO,
            side=PortfolioOrderSide.SELL,
            quantity=0.2,
            reference_price=100000.0,
            decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            strategy_name="crypto",
            strategy_version="1",
            risk_reducing=True,
        ),
        "submitted",
    )
    repo.apply_trade_update(
        client_order_id="pbp-exit-sell",
        broker_order_id="broker-exit",
        status="partially_filled",
        cumulative_filled_quantity=0.15,
        average_fill_price=99000.0,
        occurred_at=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        activity_id="activity-partial",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    client = _FakeAlpacaClient()
    # Shortfall is 0.35 - 0.25 = 0.10; the working SELL remainder is 0.05,
    # which cannot explain the discrepancy: drift stays fail-closed.
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.25",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.0",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    with pytest.raises(OwnershipDriftError, match="Ownership drift"):
        broker.account()

    # With the broker holding 0.30 the shortfall (0.35 - 0.30) is covered by
    # the 0.05 working remainder: drift passes.
    client.positions[0].qty = "0.30"
    assert broker.account() is not None


def test_decision_path_drift_halts_with_resettable_reason() -> None:
    """A drift raised while a decision is being processed must halt the
    runtime with the resettable ownership_drift reason."""
    from parayabuck.execution.alpaca import OwnershipDriftError
    from parayabuck.runtime import EngineRuntime

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._state = "running"
    runtime._portfolio_lock = threading.RLock()

    def process_closed_bars(_bars, *, entry_latch=None):
        raise OwnershipDriftError("Ownership drift for BTC/USD")

    drift_runtime = SimpleNamespace(
        market=Market.CRYPTO,
        strategy=SimpleNamespace(timeframe="1h"),
        symbols=("BTC/USD",),
        process_closed_bars=process_closed_bars,
    )
    runtime._portfolio_runtimes = (drift_runtime,)
    runtime._entry_readiness_reasons = lambda _market: []

    runtime._process_portfolio_timestamp(
        [
            Bar(
                datetime(2025, 1, 1, tzinfo=UTC),
                "BTC/USD",
                "1h",
                100.0,
                101.0,
                99.0,
                100.0,
                1000.0,
                Market.CRYPTO,
            )
        ]
    )

    assert runtime._state == "halted"
    assert runtime._circuit_breaker_reason == "ownership_drift"


def test_legacy_decisions_table_migrates_to_account_scoped_identity(tmp_path) -> None:
    """A pre-account database (UNIQUE on digest+timestamp+market) must be
    rebuilt so a second account with the same digest and timestamp is
    accepted instead of being swallowed by INSERT OR IGNORE."""
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE portfolio_runtime_sessions (
                session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                configuration_digest TEXT NOT NULL, mode TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL
            );
            CREATE TABLE portfolio_decisions (
                decision_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                configuration_digest TEXT NOT NULL, decision_timestamp TEXT NOT NULL,
                market TEXT NOT NULL, strategy_name TEXT NOT NULL,
                target_json TEXT NOT NULL, risk_json TEXT NOT NULL,
                UNIQUE(configuration_digest, decision_timestamp, market),
                FOREIGN KEY(session_id) REFERENCES portfolio_runtime_sessions(session_id)
            );
            INSERT INTO portfolio_runtime_sessions
                VALUES ('s1', 'acct-a', 'd', 'paper', '2026-01-01T00:00:00+00:00',
                        NULL, 'running');
            INSERT INTO portfolio_decisions
                VALUES ('legacy-id', 's1', 'd', '2026-08-10T00:00:00+00:00',
                        'equity', 'eq', '{}', '{}');
            """
        )
    repo = PortfolioStateRepository(path)
    timestamp = datetime(2026, 8, 10, tzinfo=UTC)
    assert repo.has_decision("acct-a", "d", timestamp, Market.EQUITY)
    session_b = repo.begin_session("acct-b", "d", "paper")
    id_b = repo.record_decision(
        session_b,
        "acct-b",
        "d",
        _decision_target(timestamp),
        _risk_decision(timestamp),
        Market.EQUITY,
    )
    assert id_b != "legacy-id"
    assert repo.has_decision("acct-b", "d", timestamp, Market.EQUITY)
    assert repo.latest_decision_timestamp("acct-b", "d", Market.EQUITY) == timestamp


def test_ownership_drift_below_repository_quantity_is_fail_closed(tmp_path) -> None:
    """Broker qty below the repository-managed qty on a live position is
    fail-closed: the bot must not silently reattribute the surviving shares."""
    from parayabuck.execution.alpaca import OwnershipDriftError

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.3",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.3",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    with pytest.raises(OwnershipDriftError, match="Ownership drift"):
        broker.account()


def test_flat_position_with_stale_ownership_does_not_raise_drift(tmp_path) -> None:
    """A fully flat stale position is the reconcile-heal path, not a drift
    halt: the heal books the missing exit leg and clears ownership."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    broker = _portfolio_broker(_FakeAlpacaClient(), repo, tmp_path)
    snapshot = broker.account()
    assert snapshot.positions == ()


def test_protective_stop_slippage_references_stop_price_not_entry(tmp_path) -> None:
    """The protective-stop SELL leg must carry the stop trigger as its
    reference price; entry-to-stop loss is not execution slippage."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="BTC/USD",
            qty="0.5",
            avg_entry_price="100000",
            current_price="100000",
            qty_available="0.5",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="crypto",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path)
    recorded: dict[str, object] = {}

    def record_stop(symbol: str, market: Market, qty: float, client_order_id: str, reference_price: float) -> None:
        recorded["id"] = client_order_id
        recorded["reference_price"] = reference_price
        repo.record_protective_order("acct", client_order_id, symbol, market, qty, reference_price)

    broker.portfolio_stop_recorded = record_stop
    broker.reconcile()

    expected_stop = 100_000 * (1 - 75.0 / 10_000)
    assert recorded["reference_price"] == pytest.approx(expected_stop)
    repo.apply_trade_update(
        client_order_id=str(recorded["id"]),
        broker_order_id="broker-stop",
        status="fill",
        cumulative_filled_quantity=0.5,
        average_fill_price=99_000.0,
        occurred_at=datetime(2025, 1, 1, tzinfo=UTC),
        activity_id="activity-stop",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        slippage = connection.execute(
            "SELECT slippage FROM portfolio_fills WHERE activity_id = 'activity-stop'"
        ).fetchone()[0]
    # (stop - fill) * qty * -1 for a SELL leg: fill below the stop is
    # positive slippage measured against the stop trigger.
    assert slippage == pytest.approx((expected_stop - 99_000.0) * 0.5)


def test_account_loop_drift_halts_runtime_and_persists_safety_state(tmp_path) -> None:
    """Ownership drift during account polling must halt the runtime and
    persist the safety state instead of being retried as a transient error."""
    import time

    from parayabuck.execution.alpaca import OwnershipDriftError
    from parayabuck.risk.state import RiskState
    from parayabuck.runtime import EngineRuntime, WorkerHealth

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    ledger = ExecutionLedger(str(tmp_path / "execution.sqlite3"))
    ledger.save_risk_state(
        RiskState(
            account_id="acct",
            session_date=datetime.now(UTC).date(),
            day_start_equity=100_000.0,
            peak_equity=105_000.0,
            realized_pnl=1_000.0,
            unrealized_pnl=-500.0,
            fees=10.0,
            consecutive_losses=1,
            circuit_breaker_reason=None,
            updated_at=datetime.now(UTC),
        )
    )

    class _DriftingBroker:
        account_id = "acct"

        def account(self) -> AccountSnapshot:
            raise OwnershipDriftError("Ownership drift for AAPL: managed 10 exceeds broker 2")

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = _DriftingBroker()
    runtime.ledger = ledger
    runtime._portfolio_repository = repo
    runtime._running = True
    runtime._stop_event = threading.Event()
    runtime._state = "running"
    runtime._circuit_breaker_reason = None
    runtime._state_lock = threading.RLock()
    runtime._risk_state_lock = threading.Lock()
    runtime._workers = {
        "pb-account": WorkerHealth(
            started_at=datetime.now(UTC),
            last_heartbeat=datetime.now(UTC),
            last_event_at=datetime.now(UTC),
            last_error=None,
        )
    }
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=0.05,
        maximum_account_age_for_entry_seconds=30.0,
        maximum_queue_age_seconds=30.0,
    )
    runtime.symbols = []
    runtime._events = SimpleNamespace(qsize=lambda: 0, empty=lambda: True)
    runtime._last_account_at = datetime.now(UTC)
    runtime._oldest_received_at = None
    runtime._dropped_events = 0
    runtime._coalesced_events = 0

    thread = threading.Thread(target=runtime._account_loop, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while (
        runtime._circuit_breaker_reason != "ownership_drift" or runtime._state != "halted"
    ) and time.monotonic() < deadline:
        time.sleep(0.02)
    runtime._running = False
    runtime._stop_event.set()
    thread.join(timeout=5.0)

    assert runtime._state == "halted"
    assert runtime._circuit_breaker_reason == "ownership_drift"
    # Risk-increasing orders are blocked at the readiness gate.
    assert "ownership_drift" in runtime._entry_readiness_reasons(Market.EQUITY)
    # The safety state survived the halt with the drift reason.
    persisted = ledger.load_risk_state("acct", datetime.now(UTC).date())
    assert persisted is not None
    assert persisted.circuit_breaker_reason == "ownership_drift"
    assert persisted.peak_equity == pytest.approx(105_000.0)
    assert persisted.consecutive_losses == 1
    # The incident table (authoritative for restore) records the drift too.
    incidents = ledger.load_safety_incidents("acct")
    assert any(incident.reason == "ownership_drift" for incident in incidents)
    ledger.close()


def _owned_equity_entry(repo: PortfolioStateRepository, *, quantity: float, symbol: str) -> None:
    session_id = repo.begin_session("acct", "digest", "paper")
    decision_timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    decision_id = repo.record_decision(
        session_id,
        "acct",
        "digest",
        _decision_target(decision_timestamp),
        _risk_decision(decision_timestamp),
        Market.EQUITY,
    )
    entry = PortfolioOrderRequest(
        client_order_id=f"pbp-entry-{symbol}",
        decision_id=decision_id,
        symbol=symbol,
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=quantity,
        reference_price=100.0,
        decision_timestamp=decision_timestamp,
        strategy_name="equity",
        strategy_version="1",
        risk_reducing=False,
    )
    repo.record_order(entry, "submitting")
    repo.apply_trade_update(
        client_order_id=entry.client_order_id,
        broker_order_id=f"broker-{symbol}",
        status="fill",
        cumulative_filled_quantity=quantity,
        average_fill_price=100.0,
        occurred_at=decision_timestamp,
        activity_id=f"activity-{symbol}",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _ledger_entry(ledger: ExecutionLedger, *, quantity: float, symbol: str) -> None:
    from parayabuck.core.models import Direction, ExecutionEvent, OrderIntent, OrderRole, OrderState

    intent = OrderIntent(
        "signal",
        symbol,
        Market.EQUITY,
        Direction.LONG,
        quantity,
        100,
        95,
        110,
        "test",
        client_order_id=f"pbp-legacy-{symbol}",
    )
    ledger.register_entry_order("acct", intent, f"broker-legacy-{symbol}")
    ledger.apply_execution_event(
        ExecutionEvent(
            "acct",
            f"pbp-legacy-{symbol}",
            f"broker-legacy-{symbol}",
            None,
            OrderRole.ENTRY,
            symbol,
            Market.EQUITY,
            OrderState.FILLED,
            quantity,
            quantity,
            100.0,
            datetime(2025, 1, 1, tzinfo=UTC),
        )
    )


def test_legacy_ledger_ownership_splits_mixed_position(tmp_path) -> None:
    """2 legacy-managed + 10 manual on one symbol: managed 2, external 10."""
    from parayabuck.execution.ledger import ExecutionLedger

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    ledger = ExecutionLedger(str(tmp_path / "legacy.sqlite3"))
    _ledger_entry(ledger, quantity=2, symbol="GE")
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="GE",
            qty="12",
            avg_entry_price="100",
            current_price="100",
            qty_available="12",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="us_equity",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path, with_ledger=False)
    broker.ledger = ledger
    snapshot = broker.account()
    position = snapshot.positions[0]
    assert position.managed_qty == pytest.approx(2.0)
    assert position.external_qty == pytest.approx(10.0)
    assert position.ownership == "mixed"
    ledger.close()


def test_portfolio_ownership_splits_mixed_position(tmp_path) -> None:
    """2 portfolio-managed + 10 manual on one symbol: managed 2, external 10."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_equity_entry(repo, quantity=2, symbol="GE")
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="GE",
            qty="12",
            avg_entry_price="100",
            current_price="100",
            qty_available="12",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="us_equity",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path, with_ledger=False)
    snapshot = broker.account()
    position = snapshot.positions[0]
    assert position.managed_qty == pytest.approx(2.0)
    assert position.external_qty == pytest.approx(10.0)
    assert position.ownership == "mixed"


def test_dual_positive_ownership_is_fail_closed(tmp_path) -> None:
    """Positive ownership in both ledger and portfolio systems is an
    explicit reconciliation invariant: fail closed instead of guessing."""
    from parayabuck.execution.alpaca import OwnershipDriftError
    from parayabuck.execution.ledger import ExecutionLedger

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_equity_entry(repo, quantity=2, symbol="GE")
    ledger = ExecutionLedger(str(tmp_path / "legacy.sqlite3"))
    _ledger_entry(ledger, quantity=2, symbol="GE")
    client = _FakeAlpacaClient()
    client.positions = [
        SimpleNamespace(
            symbol="GE",
            qty="4",
            avg_entry_price="100",
            current_price="100",
            qty_available="4",
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="us_equity",
        )
    ]
    broker = _portfolio_broker(client, repo, tmp_path, with_ledger=False)
    broker.ledger = ledger
    with pytest.raises(OwnershipDriftError, match="Dual ownership lineage"):
        broker.account()
    ledger.close()


def test_operations_snapshot_is_isolated_between_two_accounts(tmp_path) -> None:
    """Two accounts sharing one SQLite file never see each other's orders,
    fills or decisions in the operator snapshot."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    for account in ("acct-a", "acct-b"):
        session = repo.begin_session(account, "digest", "paper")
        timestamp = datetime(2025, 1, 1, tzinfo=UTC)
        decision_id = repo.record_decision(
            session,
            account,
            "digest",
            _decision_target(timestamp),
            _risk_decision(timestamp),
            Market.EQUITY,
        )
        repo.record_order(
            PortfolioOrderRequest(
                client_order_id=f"pbp-order-{account}",
                decision_id=decision_id,
                symbol="GE",
                market=Market.EQUITY,
                side=PortfolioOrderSide.BUY,
                quantity=2,
                reference_price=100.0,
                decision_timestamp=timestamp,
                strategy_name="equity",
                strategy_version="1",
                risk_reducing=False,
            ),
            "submitted",
        )
        repo.apply_trade_update(
            client_order_id=f"pbp-order-{account}",
            broker_order_id=f"broker-{account}",
            status="fill",
            cumulative_filled_quantity=2,
            average_fill_price=100.0,
            occurred_at=timestamp,
            activity_id=f"activity-{account}",
            fee=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )
        # Account A keeps a working order; account B's order is fully filled.
        if account == "acct-a":
            repo.record_order(
                PortfolioOrderRequest(
                    client_order_id=f"pbp-open-{account}",
                    decision_id=decision_id,
                    symbol="AAPL",
                    market=Market.EQUITY,
                    side=PortfolioOrderSide.BUY,
                    quantity=1,
                    reference_price=100.0,
                    decision_timestamp=timestamp,
                    strategy_name="equity",
                    strategy_version="1",
                    risk_reducing=False,
                ),
                "submitted",
            )

    snapshot_a = repo.operations_snapshot("acct-a")
    snapshot_b = repo.operations_snapshot("acct-b")

    assert snapshot_a["last_decision"]["strategy"] == "eq"
    assert snapshot_b["last_decision"]["strategy"] == "eq"
    assert {order["client_order_id"] for order in snapshot_a["pending_orders"]} == {"pbp-open-acct-a"}
    assert snapshot_b["pending_orders"] == []
    assert {fill["activity_id"] for fill in snapshot_a["recent_fills"]} == {"activity-acct-a"}
    assert {fill["activity_id"] for fill in snapshot_b["recent_fills"]} == {"activity-acct-b"}
    # No cross-account leakage in either direction.
    assert all("acct-b" not in str(fill) for fill in snapshot_a["recent_fills"])
    assert all("acct-a" not in str(fill) for fill in snapshot_b["recent_fills"])


def test_negative_net_ownership_is_an_invariant_violation(tmp_path) -> None:
    """owned_quantities must never silently hide negative net ownership."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    session = repo.begin_session("acct", "digest", "paper")
    decision_id = repo.record_decision(
        session,
        "acct",
        "digest",
        _decision_target(datetime(2025, 1, 2, tzinfo=UTC)),
        _risk_decision(datetime(2025, 1, 2, tzinfo=UTC)),
        Market.CRYPTO,
    )
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="pbp-over-sell",
            decision_id=decision_id,
            symbol="BTC/USD",
            market=Market.CRYPTO,
            side=PortfolioOrderSide.SELL,
            quantity=0.8,
            reference_price=100000.0,
            decision_timestamp=datetime(2025, 1, 2, tzinfo=UTC),
            strategy_name="crypto",
            strategy_version="1",
            risk_reducing=True,
        ),
        "submitted",
    )
    repo.apply_trade_update(
        client_order_id="pbp-over-sell",
        broker_order_id="broker-over-sell",
        status="fill",
        cumulative_filled_quantity=0.8,
        average_fill_price=99000.0,
        occurred_at=datetime(2025, 1, 2, tzinfo=UTC),
        activity_id="activity-over-sell",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    # 0.5 bought, 0.8 sold: net -0.3 must raise, not silently vanish.
    with pytest.raises(RuntimeError, match="Negative net portfolio ownership"):
        repo.owned_quantities("acct")


def test_stale_flat_position_records_adjustment_not_fake_fill(tmp_path) -> None:
    """No real fill in broker history: reconcile records an explicit
    ownership adjustment instead of a synthetic precise SELL fill."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    client = _FakeAlpacaClient()  # flat, no filled orders
    broker = _portfolio_broker(client, repo, tmp_path)
    broker.portfolio_flat_reconciled = lambda symbol, market: repo.clear_stale_ownership("acct", symbol, market)

    broker.reconcile()

    assert repo.owned_quantities("acct") == {}
    with repo.store.lock:
        adjustments = repo.store.connection.execute(
            "SELECT kind, quantity, consumed_quantity FROM portfolio_ownership_adjustments"
        ).fetchall()
        synthetic_sells = repo.store.connection.execute(
            "SELECT COUNT(*) FROM portfolio_orders WHERE client_order_id LIKE 'pbp-reconcile-%'"
        ).fetchone()[0]
    assert adjustments == [("stale_ownership_clear", 0.49875, 0.0)]
    assert synthetic_sells == 0


def test_reconcile_recovers_real_fill_from_broker_history(tmp_path) -> None:
    """A real missing fill in Alpaca history is recovered at its real price
    instead of being replaced by an adjustment."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    entry_decision_id = repo.store.connection.execute(
        "SELECT decision_id FROM portfolio_orders WHERE client_order_id = 'pbp-entry'"
    ).fetchone()[0]
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="pbp-exit-sell",
            decision_id=str(entry_decision_id),
            symbol="BTC/USD",
            market=Market.CRYPTO,
            side=PortfolioOrderSide.SELL,
            quantity=0.49875,
            reference_price=100000.0,
            decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            strategy_name="crypto",
            strategy_version="1",
            risk_reducing=True,
        ),
        "submitted",
    )
    client = _FakeAlpacaClient()
    # Alpaca confirms the exit order filled at 97_500 while the stream was down.
    client.filled_orders["pbp-exit-sell"] = SimpleNamespace(
        id="broker-exit",
        status="filled",
        filled_qty="0.49875",
        filled_avg_price="97500",
        filled_at="2025-01-01T15:00:00+00:00",
    )
    broker = _portfolio_broker(client, repo, tmp_path)

    def recover_fill(
        client_order_id: str,
        broker_order_id: str,
        filled_qty: float,
        avg_price: float,
        occurred_at: datetime,
        activity_id: str,
        fee: float,
    ) -> None:
        repo.apply_trade_update(
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

    broker.portfolio_fill_recovered = recover_fill
    broker.portfolio_flat_reconciled = lambda symbol, market: repo.clear_stale_ownership("acct", symbol, market)

    broker.reconcile()

    assert repo.owned_quantities("acct") == {}
    with repo.store.lock:
        adjustments = repo.store.connection.execute("SELECT COUNT(*) FROM portfolio_ownership_adjustments").fetchone()[
            0
        ]
        fill = repo.store.connection.execute(
            "SELECT price, quantity FROM portfolio_fills WHERE client_order_id = 'pbp-exit-sell'"
        ).fetchone()
    assert adjustments == 0
    # The recovered fill carries the real exit price, not an invented one.
    assert fill == (97500.0, 0.49875)


def test_late_real_fill_after_reconciliation_consumes_adjustment(tmp_path) -> None:
    """A stop fill arriving after reconcile wrote an adjustment cancels the
    adjustment instead of booking a second SELL on top of it."""
    from parayabuck.core.models import OrderRole
    from parayabuck.execution.alpaca_updates import TradeUpdate
    from parayabuck.runtime import EngineRuntime

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_crypto_entry(repo, quantity=0.5)
    entry_decision_id = repo.store.connection.execute(
        "SELECT decision_id FROM portfolio_orders WHERE client_order_id = 'pbp-entry'"
    ).fetchone()[0]
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="pbp-exit-sell",
            decision_id=str(entry_decision_id),
            symbol="BTC/USD",
            market=Market.CRYPTO,
            side=PortfolioOrderSide.SELL,
            quantity=0.49875,
            reference_price=100000.0,
            decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            strategy_name="crypto",
            strategy_version="1",
            risk_reducing=True,
        ),
        "submitted",
    )
    # Reconcile runs while the broker is flat and no fill is visible yet.
    client = _FakeAlpacaClient()
    broker = _portfolio_broker(client, repo, tmp_path)
    broker.portfolio_flat_reconciled = lambda symbol, market: repo.clear_stale_ownership("acct", symbol, market)
    broker.reconcile()
    assert repo.owned_quantities("acct") == {}

    # The real stop fill arrives late with the actual exit price.
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._portfolio_repository = repo
    runtime.broker = broker
    runtime._last_account_at = None
    runtime._persist_risk_state = lambda *args, **kwargs: None
    update = TradeUpdate(
        event="fill",
        client_order_id="pbp-exit-sell",
        broker_order_id="broker-exit",
        parent_order_id=None,
        role=OrderRole.MANUAL_EXIT,
        symbol="BTC/USD",
        market=Market.CRYPTO,
        requested_qty=0.49875,
        filled_qty=0.49875,
        average_fill_price=97500.0,
        timestamp=datetime(2025, 1, 1, 15, 0, tzinfo=UTC),
        fee=0.0,
        activity_id="activity-late-exit",
    )
    import asyncio

    asyncio.run(runtime._handle_trade_update(update))

    # Owned stays at zero: the real fill consumed the provisional adjustment
    # instead of double-selling the position into negative territory.
    assert repo.owned_quantities("acct") == {}
    with repo.store.lock:
        adjustments = repo.store.connection.execute(
            "SELECT quantity, consumed_quantity FROM portfolio_ownership_adjustments"
        ).fetchall()
        fills = repo.store.connection.execute(
            "SELECT price, quantity FROM portfolio_fills WHERE client_order_id = 'pbp-exit-sell'"
        ).fetchall()
    assert adjustments == [(0.49875, 0.49875)]
    # The real fill was booked at the real price.
    assert fills == [(97500.0, 0.49875)]


def test_old_database_with_child_rows_migrates_cleanly(tmp_path) -> None:
    """A pre-account database with full child data (session, decisions,
    orders, fills, high water) survives the account-scoped migration with
    every row, FK and duplicate-decision semantic intact."""
    path = tmp_path / "legacy_full.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE portfolio_runtime_sessions (
                session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                configuration_digest TEXT NOT NULL, mode TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL
            );
            CREATE TABLE portfolio_decisions (
                decision_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                configuration_digest TEXT NOT NULL, decision_timestamp TEXT NOT NULL,
                market TEXT NOT NULL, strategy_name TEXT NOT NULL,
                target_json TEXT NOT NULL, risk_json TEXT NOT NULL,
                UNIQUE(configuration_digest, decision_timestamp, market),
                FOREIGN KEY(session_id) REFERENCES portfolio_runtime_sessions(session_id)
            );
            CREATE TABLE portfolio_orders (
                client_order_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL,
                symbol TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
                quantity REAL NOT NULL, reference_price REAL NOT NULL,
                status TEXT NOT NULL, broker_order_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(decision_id) REFERENCES portfolio_decisions(decision_id)
            );
            CREATE TABLE portfolio_fills (
                activity_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, quantity REAL NOT NULL,
                price REAL NOT NULL, fee REAL NOT NULL,
                realized_pnl REAL NOT NULL, unrealized_pnl REAL NOT NULL,
                slippage REAL NOT NULL,
                FOREIGN KEY(client_order_id) REFERENCES portfolio_orders(client_order_id)
            );
            CREATE TABLE portfolio_high_water (
                account_id TEXT PRIMARY KEY, equity REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO portfolio_runtime_sessions
                VALUES ('s1', 'acct-legacy', 'digest-v1', 'paper',
                        '2026-01-01T00:00:00+00:00', NULL, 'running');
            INSERT INTO portfolio_decisions VALUES
                ('dec-1', 's1', 'digest-v1', '2026-08-01T00:00:00+00:00',
                 'equity', 'equity_dynamic_trend_1d_v1', '{"weights":[]}', '{}'),
                ('dec-2', 's1', 'digest-v1', '2026-08-08T00:00:00+00:00',
                 'equity', 'equity_dynamic_trend_1d_v1', '{"weights":[]}', '{}');
            INSERT INTO portfolio_orders VALUES
                ('order-buy-1', 'dec-1', 'AAPL', 'equity', 'buy', 10, 200.0,
                 'filled', 'broker-buy-1', '2026-08-01T00:05:00+00:00'),
                ('order-sell-1', 'dec-2', 'AAPL', 'equity', 'sell', 4, 210.0,
                 'filled', 'broker-sell-1', '2026-08-08T00:05:00+00:00');
            INSERT INTO portfolio_fills VALUES
                ('fill-buy-1', 'order-buy-1', '2026-08-01T00:06:00+00:00',
                 10, 200.5, 0.0, 0.0, 0.0, 0.0),
                ('fill-sell-1', 'order-sell-1', '2026-08-08T00:06:00+00:00',
                 4, 209.5, 0.0, 0.0, 0.0, 0.0);
            INSERT INTO portfolio_high_water
                VALUES ('acct-legacy', 105000.0, '2026-08-08T00:06:00+00:00');
            """
        )
    repo = PortfolioStateRepository(path)
    connection = repo.store.connection
    # Every row survived the migration.
    assert connection.execute("SELECT COUNT(*) FROM portfolio_runtime_sessions").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM portfolio_decisions").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM portfolio_orders").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM portfolio_fills").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM portfolio_high_water").fetchone()[0] == 1
    # account_id was backfilled onto decisions from the session lineage.
    backfilled = connection.execute("SELECT DISTINCT account_id FROM portfolio_decisions").fetchall()
    assert backfilled == [("acct-legacy",)]
    # Orders still reference decisions; fills still reference orders.
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM portfolio_orders o JOIN portfolio_decisions d"
            " ON d.decision_id = o.decision_id WHERE d.decision_id IN ('dec-1','dec-2')"
        ).fetchone()[0]
        == 2
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM portfolio_fills f JOIN portfolio_orders o"
            " ON o.client_order_id = f.client_order_id WHERE o.client_order_id LIKE 'order-%'"
        ).fetchone()[0]
        == 2
    )
    # Fills landed on the newly added cumulative quantity columns: the
    # migration restored real booked quantities from the legacy fills table.
    assert (
        connection.execute(
            "SELECT cumulative_filled_quantity FROM portfolio_orders WHERE client_order_id='order-buy-1'"
        ).fetchone()[0]
        == 10
    )
    assert (
        connection.execute(
            "SELECT cumulative_filled_quantity FROM portfolio_orders WHERE client_order_id='order-sell-1'"
        ).fetchone()[0]
        == 4
    )
    # Referential integrity holds after the table rebuilds.
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    # The account-scoped unique constraint accepts a duplicate decision from a
    # different account with the same digest and timestamp.
    session_b = repo.begin_session("acct-new", "digest-v1", "paper")
    timestamp = datetime(2026, 8, 8, tzinfo=UTC)
    id_b = repo.record_decision(
        session_b,
        "acct-new",
        "digest-v1",
        _decision_target(timestamp),
        _risk_decision(timestamp),
        Market.EQUITY,
    )
    assert id_b != "dec-2"
    assert repo.has_decision("acct-new", "digest-v1", timestamp, Market.EQUITY)
    assert repo.latest_decision_timestamp("acct-new", "digest-v1", Market.EQUITY) == timestamp
    # Owned quantities attributed through the backfilled lineage.
    assert repo.owned_quantities("acct-legacy") == {"AAPL": 6}


class _FixedTargetStrategy(PortfolioStrategy):
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = targets
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

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

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True


class _PositionedBroker(_Broker):
    def __init__(self, positions: tuple[PositionSnapshot, ...]) -> None:
        super().__init__()
        self._positions = positions

    def account(self) -> AccountSnapshot:
        equity = 100_000.0
        value = sum(position.market_value for position in self._positions)
        return AccountSnapshot(
            cash=equity - value,
            equity=equity,
            peak_equity=equity,
            daily_pnl=0.0,
            positions=self._positions,
        )


def test_entry_risk_latch_blocks_entries_but_allows_sells(tmp_path) -> None:
    """A latched market still runs decisions: SELL exits flow, BUYs are
    dropped instead of freezing the whole decision pipeline."""

    broker = _PositionedBroker(
        (
            PositionSnapshot(
                "AAPL",
                100,
                100,
                100,
                Market.EQUITY,
                qty_available=100,
                ownership="managed",
                managed_qty=100,
            ),
        )
    )
    runtime = PortfolioRuntime(
        _FixedTargetStrategy({"AAPL": 0.05, "MSFT": 0.10}),
        ("AAPL", "MSFT"),
        broker,
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
    )
    bars = _bars()
    unlatched = runtime.process_closed_bars(bars)
    assert any(order.side == PortfolioOrderSide.BUY for order in unlatched.orders)
    # The unlatched decision has both legs: the risk-reducing SELL is
    # submitted immediately and the dependent BUY is deferred until the sell
    # is terminal.
    assert unlatched.status == "sell_phase_submitted"
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    sell = next(order for order in unlatched.orders if order.side == PortfolioOrderSide.SELL)
    assert sell.symbol == "AAPL"

    # The latched decision runs on a NEW bar timestamp (the previous one was
    # already recorded as a decision). While the two-phase rebalance is still
    # in flight no new decision is planned: stacking a fresh BUY on top of a
    # not-yet-filled SELL could exceed the safe exposure.
    next_day = max(bar.timestamp for bar in bars) + timedelta(days=1)
    next_batch = [
        Bar(
            next_day,
            symbol,
            "1d",
            100.0,
            100.0,
            100.0,
            100.0,
            1_000_000,
            Market.EQUITY,
        )
        for symbol in ("AAPL", "MSFT")
    ]
    latched = runtime.process_closed_bars(next_batch, entry_latch="daily_loss_limit")
    assert latched.status == "rebalance_in_progress"
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]

    # The AAPL sell fills; the deferred BUY phase resumes with a fresh
    # account, submitting the re-planned MSFT BUY exactly once.
    runtime.repository.apply_trade_update(
        client_order_id=sell.client_order_id,
        broker_order_id=f"broker-{sell.client_order_id}",
        status="fill",
        cumulative_filled_quantity=sell.quantity,
        average_fill_price=sell.reference_price,
        occurred_at=sell.decision_timestamp,
        activity_id=f"activity-{sell.client_order_id}",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    assert [order.side for order in broker.submitted] == [
        PortfolioOrderSide.SELL,
        PortfolioOrderSide.BUY,
    ]
    # The deferred BUY fills and the rebalance completes.
    for order in resumed.orders:
        runtime.repository.apply_trade_update(
            client_order_id=order.client_order_id,
            broker_order_id=f"broker-{order.client_order_id}",
            status="fill",
            cumulative_filled_quantity=order.quantity,
            average_fill_price=order.reference_price,
            occurred_at=order.decision_timestamp,
            activity_id=f"activity-{order.client_order_id}",
            fee=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )
    completed = runtime.advance_rebalance()
    assert completed is not None and completed.status == "rebalance_complete"

    # After the rebalance completes, a latched decision is risk-reducing
    # only: the cap drops the MSFT entry entirely.
    after = runtime.process_closed_bars(next_batch, entry_latch="daily_loss_limit")
    assert after.status != "rebalance_in_progress"
    assert after.orders
    assert all(order.side == PortfolioOrderSide.SELL for order in after.orders)
    assert all(order.symbol != "MSFT" for order in after.orders)


def test_entry_risk_latch_evaluates_from_account_poll(tmp_path) -> None:
    """The account-poll cadence latches entries on daily loss and drawdown
    and clears the latch when the condition recovers; a drawdown breach is
    mirrored as a persisted safety incident until recovery."""
    from parayabuck.runtime import EngineRuntime

    incidents: list[tuple[str, str | None]] = []

    def _save(account_id: str, reason: str) -> None:
        incidents.append((account_id, reason))

    def _clear(account_id: str, reason: str | None = None) -> None:
        incidents.append((account_id, reason))

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.app_config = Config()
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = SimpleNamespace(save_safety_incident=_save, clear_safety_incident=_clear)
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    # 1% daily loss on a $100k account trips the 0.5% daily-loss limit.
    runtime._evaluate_entry_risk_latch(AccountSnapshot(99_000, 99_000, 100_000, -1_000))
    assert runtime._entry_risk_latch[Market.EQUITY] == "daily_loss_limit"
    # Recovery clears the latch.
    runtime._evaluate_entry_risk_latch(AccountSnapshot(100_000, 100_000, 100_000, 0.0))
    assert runtime._entry_risk_latch[Market.EQUITY] is None
    # A 6% drawdown from the peak trips the drawdown halt and persists the
    # operational incident.
    runtime._evaluate_entry_risk_latch(AccountSnapshot(94_000, 94_000, 100_000, 0.0))
    assert runtime._entry_risk_latch[Market.EQUITY] == "portfolio_drawdown_halt"
    assert ("acct", "portfolio_drawdown_halt") in incidents
    # The incident clears once the breach recovers.
    runtime._evaluate_entry_risk_latch(AccountSnapshot(100_000, 100_000, 100_000, 0.0))
    assert runtime._entry_risk_latch[Market.EQUITY] is None
    assert ("acct", "portfolio_drawdown_halt") in incidents[-1:]


def test_health_exposes_entry_risk_latch() -> None:
    from parayabuck.runtime import EngineRuntime

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._entry_risk_latch = {Market.EQUITY: "daily_loss_limit", Market.CRYPTO: None}
    runtime._state = "running"
    runtime._running = True
    runtime._workers = {}
    runtime._events = SimpleNamespace(qsize=lambda: 0, empty=lambda: True)
    runtime._last_account_at = datetime.now(UTC)
    runtime._oldest_received_at = None
    runtime._dropped_events = 0
    runtime._coalesced_events = 0
    runtime.symbols = []
    runtime._symbols_by_market = {Market.EQUITY: set(), Market.CRYPTO: set()}
    runtime._market_enabled = {Market.EQUITY: False, Market.CRYPTO: False}
    runtime._not_ready_symbols = set()
    runtime._circuit_breaker_reason = None
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=15.0,
        maximum_account_age_for_entry_seconds=30.0,
        maximum_queue_age_seconds=30.0,
    )
    runtime._universe_refresh_status = {}
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._last_reconciliation_at = None
    runtime._last_universe_refresh_at = None
    runtime._next_universe_refresh_at = None
    runtime.strategy_status = "configured"
    runtime._strategy_specs = ()
    runtime._portfolio_repository = None
    runtime.ledger = None
    runtime.market_state = SimpleNamespace(has_data_gaps=False)
    runtime.broker = SimpleNamespace(account_id="acct")
    health = runtime.health()
    assert health.entry_risk_latch == {"equity": "daily_loss_limit", "crypto": None}


def _health_runtime() -> object:
    from parayabuck.runtime import EngineRuntime, WorkerHealth

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._state = "running"
    runtime._running = True
    now = datetime.now(UTC)
    runtime._workers = {
        "pb-decisions": WorkerHealth(now, now, alive=True),
        "pb-trade-updates": WorkerHealth(now, now, alive=True),
        "pb-account": WorkerHealth(now, now, alive=True),
        "pb-stocks": WorkerHealth(now, now, now, alive=True),
    }
    runtime._events = SimpleNamespace(qsize=lambda: 0, empty=lambda: True)
    runtime._last_account_at = datetime.now(UTC)
    runtime._oldest_received_at = None
    runtime._dropped_events = 0
    runtime._coalesced_events = 0
    runtime.symbols = ["SPY"]
    runtime._symbols_by_market = {Market.EQUITY: {"SPY"}, Market.CRYPTO: set()}
    runtime._market_enabled = {Market.EQUITY: True, Market.CRYPTO: False}
    runtime._strategy_enabled = {Market.EQUITY: True, Market.CRYPTO: False}
    runtime._not_ready_symbols = set()
    runtime._circuit_breaker_reason = None
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=15.0,
        maximum_account_age_for_entry_seconds=30.0,
        maximum_queue_age_seconds=30.0,
    )
    runtime._universe_refresh_status = {"equity": "success", "crypto": "refresh_failed"}
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._last_reconciliation_at = None
    runtime._last_universe_refresh_at = None
    runtime._next_universe_refresh_at = None
    runtime.strategy_status = "configured"
    runtime._strategy_specs = ()
    runtime._portfolio_repository = None
    runtime.ledger = None
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.market_state = SimpleNamespace(has_data_gaps=False, has_data_gaps_for_market=lambda market: False)
    return runtime


def test_disabled_market_scanner_failure_is_not_critical() -> None:
    """Equity enabled, crypto disabled, no crypto positions: a crypto
    discovery failure is informational and the trading runtime stays
    healthy."""
    runtime = _health_runtime()
    health = runtime.health()
    assert health.healthy is True
    crypto_health = health.markets["crypto"]
    assert crypto_health["scanner_status"] == "refresh_failed"
    assert crypto_health["scanner_critical"] is False
    equity_health = health.markets["equity"]
    assert equity_health["scanner_status"] == "success"


def test_enabled_market_scanner_failure_is_critical() -> None:
    """The enabled equity strategy makes an equity scanner failure critical:
    the runtime is degraded."""
    runtime = _health_runtime()
    runtime._universe_refresh_status = {"equity": "refresh_failed", "crypto": "success"}
    health = runtime.health()
    assert health.healthy is False
    assert health.state == "degraded"
    assert health.markets["equity"]["scanner_critical"] is True


def test_managed_position_makes_disabled_market_scanner_critical() -> None:
    """A managed crypto position keeps crypto scanner/stream/protection health
    operationally relevant even with the strategy disabled."""
    runtime = _health_runtime()
    runtime._portfolio_repository = SimpleNamespace(
        owned_symbols=lambda account_id, market: {"BTC/USD"} if market == Market.CRYPTO else set(),
        open_orders=lambda account_id, market: (),
    )
    health = runtime.health()
    assert health.healthy is False
    assert health.state == "degraded"
    assert health.markets["crypto"]["scanner_critical"] is True


# ---------------------------------------------------------------------------
# Startup catch-up: backtest/runtime parity decision
# ---------------------------------------------------------------------------


def test_rebalance_policy_next_boundary_after() -> None:
    """The pure observability helper returns a true boundary strictly after
    ``previous`` for every cadence (monthly / weekly / hours:N)."""
    monthly = RebalancePolicy(Market.EQUITY, "monthly")
    # Mid-month: first day of the next month at the same time-of-day.
    assert monthly.next_boundary_after(datetime(2025, 1, 15, 14, 30, tzinfo=UTC)) == datetime(
        2025, 2, 1, 14, 30, tzinfo=UTC
    )
    # Already a boundary: exactly one period later.
    assert monthly.next_boundary_after(datetime(2025, 1, 1, 14, 30, tzinfo=UTC)) == datetime(
        2025, 2, 1, 14, 30, tzinfo=UTC
    )
    # December rolls the year.
    assert monthly.next_boundary_after(datetime(2025, 12, 31, 9, 0, tzinfo=UTC)) == datetime(
        2026, 1, 1, 9, 0, tzinfo=UTC
    )

    weekly = RebalancePolicy(Market.EQUITY, "weekly")
    # 2025-01-08 is a Wednesday -> the next ISO Monday (2025-01-13).
    assert weekly.next_boundary_after(datetime(2025, 1, 8, tzinfo=UTC)) == datetime(2025, 1, 13, tzinfo=UTC)
    # Already a Monday: one week later.
    assert weekly.next_boundary_after(datetime(2025, 1, 6, tzinfo=UTC)) == datetime(2025, 1, 13, tzinfo=UTC)
    # Sunday: the next day.
    assert weekly.next_boundary_after(datetime(2025, 1, 12, tzinfo=UTC)) == datetime(2025, 1, 13, tzinfo=UTC)

    hours = RebalancePolicy(Market.CRYPTO, "hours:24")
    # Mid-block: the start of the next 24h UTC block.
    assert hours.next_boundary_after(datetime(2025, 1, 1, 5, 0, tzinfo=UTC)) == datetime(2025, 1, 2, 0, 0, tzinfo=UTC)
    # Exactly on a block boundary: previous + N hours.
    assert hours.next_boundary_after(datetime(2025, 1, 1, 0, 0, tzinfo=UTC)) == datetime(2025, 1, 2, 0, 0, tzinfo=UTC)

    # Invariant: the result is always a real boundary strictly after previous.
    for policy, previous in (
        (monthly, datetime(2025, 6, 15, 10, 0, tzinfo=UTC)),
        (weekly, datetime(2025, 6, 17, 10, 0, tzinfo=UTC)),
        (hours, datetime(2025, 6, 15, 10, 0, tzinfo=UTC)),
    ):
        boundary = policy.next_boundary_after(previous)
        assert boundary > previous
        assert policy.is_period_start(previous, boundary)


def test_backtest_decides_but_runtime_waits_for_boundary_then_catch_up_fires(
    tmp_path,
) -> None:
    """Backtest/runtime parity mismatch, proven deterministically: the SAME
    completed history, universe and account make the backtest engine decide on
    its first usable bar while a seeded runtime mid-period reports
    ``not_scheduled`` — the startup catch-up closes the gap with an immediate
    full-target decision."""
    strategy = _fixture_strategy()
    bars = _bars()  # 220 daily bars; the last bar is mid-month
    last_timestamp = max(bar.timestamp for bar in bars)
    assert (
        RebalancePolicy(Market.EQUITY, "monthly").next_boundary_after(last_timestamp) > last_timestamp
    )  # warmup ends between cadence boundaries

    # Backtest: first usable bar is a period start (previous=None), so the
    # engine decides within the same history instead of waiting for the end.
    engine = PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=100_000.0,
        allow_static_universe=True,
    )
    engine.run(bars, liquidate_at_end=False)
    assert engine._rebalances
    timestamps = sorted({bar.timestamp for bar in bars})
    first_usable_index = strategy.minimum_history_bars
    first_decision_ts = next(
        timestamps[index]
        for index in range(first_usable_index, len(timestamps))
        if timestamps[index].month != timestamps[index - 1].month
    )
    assert engine._rebalances[0].decision_timestamp == first_decision_ts

    # Runtime without catch-up: after mid-period warmup the normal cadence
    # waits for the next boundary -> not_scheduled (the mismatch).
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    runtime.seed(bars)
    last_batch = [bar for bar in bars if bar.timestamp == last_timestamp]
    assert runtime.process_closed_bars(last_batch).status == "not_scheduled"

    # Startup catch-up decides immediately with the FULL target (pure buys on
    # a fresh account) and records the origin.
    result = runtime.startup_catch_up()
    assert result.status == "submitted"
    assert result.target is not None
    assert result.target.metadata.get("decision_origin") == "startup_catch_up"
    assert result.target.invested_weight > 0
    assert result.orders
    assert all(order.side == PortfolioOrderSide.BUY for order in result.orders)
    assert len(broker.submitted) == len(result.orders)
    assert runtime.startup_catch_up_status == "submitted"
    assert runtime.startup_catch_up_at is not None


def test_startup_catch_up_fires_full_target_and_never_reenters(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    runtime.seed(_bars())
    assert runtime.startup_catch_up_status is None
    assert runtime.startup_catch_up_at is None

    result = runtime.startup_catch_up()
    assert result.status == "submitted"
    assert result.target is not None
    assert result.target.metadata.get("decision_origin") == "startup_catch_up"
    assert result.orders and all(order.side == PortfolioOrderSide.BUY for order in result.orders)
    assert runtime.startup_catch_up_status == "submitted"
    assert runtime.startup_catch_up_at is not None
    decision = runtime.repository.decision_row(result.decision_id)
    assert decision is not None
    assert json.loads(decision["target_json"])["metadata"]["decision_origin"] == "startup_catch_up"

    # Restart-safety: a decision at the same timestamp never re-enters.
    again = runtime.startup_catch_up()
    assert again.status == "duplicate_decision"
    assert runtime.startup_catch_up_status == "duplicate_decision"


def test_startup_catch_up_warming_up_with_short_or_empty_history(tmp_path) -> None:
    runtime = _runtime(tmp_path, _Broker())
    # No history at all.
    assert runtime.startup_catch_up().status == "warming_up"
    assert runtime.startup_catch_up_status == "warming_up"
    # Too few bars (below minimum_history_bars=151).
    runtime.seed(_bars()[:40])
    result = runtime.startup_catch_up()
    assert result.status == "warming_up"
    assert runtime.startup_catch_up_status == "warming_up"


def test_startup_catch_up_incomplete_strategy_batch_when_timestamps_diverge(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path, _Broker())
    bars = _bars()
    runtime.seed(bars)
    later = max(bar.timestamp for bar in bars) + timedelta(days=1)
    runtime.history["AAPL"].append(Bar(later, "AAPL", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY))
    result = runtime.startup_catch_up()
    assert result.status == "incomplete_strategy_batch"
    assert runtime.startup_catch_up_status == "incomplete_strategy_batch"


def test_startup_catch_up_duplicate_decision_at_same_timestamp(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    bars = _bars()
    runtime.seed(bars)
    last_timestamp = max(bar.timestamp for bar in bars)
    first = runtime.process_closed_bars(
        [bar for bar in bars if bar.timestamp == last_timestamp],
        force_scheduled=True,
        decision_origin="manual_test",
    )
    assert first.status == "submitted"
    result = runtime.startup_catch_up()
    assert result.status == "duplicate_decision"
    assert runtime.startup_catch_up_status == "duplicate_decision"


def test_startup_catch_up_superseded_after_any_prior_decision(tmp_path) -> None:
    """Restart safety: any prior decision for this deployment (even at an
    earlier timestamp) means the cadence is already running — the catch-up
    never re-enters."""
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    bars = _bars()
    timestamps = sorted({bar.timestamp for bar in bars})
    earlier = timestamps[runtime.strategy.minimum_history_bars]
    runtime.seed([bar for bar in bars if bar.timestamp <= earlier])
    decided = runtime.process_closed_bars(
        [bar for bar in bars if bar.timestamp == earlier],
        force_scheduled=True,
        decision_origin="test_origin",
    )
    assert decided.status == "submitted"
    # Warmup later advances past the decision; the catch-up must be superseded.
    runtime.seed(bars)
    result = runtime.startup_catch_up()
    assert result.status == "catch_up_superseded"
    assert runtime.startup_catch_up_status == "catch_up_superseded"


def test_startup_catch_up_already_invested_skips_when_managed_position_exists(
    tmp_path,
) -> None:
    class _InvestedBroker(_Broker):
        def account(self) -> AccountSnapshot:
            position = PositionSnapshot(
                "AAPL",
                10.0,
                100.0,
                100.0,
                Market.EQUITY,
                qty_available=10.0,
                ownership="managed",
                managed_qty=10.0,
            )
            return AccountSnapshot(90_000, 100_000, 100_000, 0.0, positions=(position,))

    runtime = _runtime(tmp_path, _InvestedBroker())
    runtime.seed(_bars())
    result = runtime.startup_catch_up()
    assert result.status == "already_invested"
    assert runtime.startup_catch_up_status == "already_invested"
    assert runtime.repository.latest_decision_timestamp("unknown", runtime.configuration_digest, Market.EQUITY) is None


def test_startup_catch_up_rebalance_in_progress_defers(tmp_path) -> None:
    """A rebalance execution left in flight by a DIFFERENT deployment digest
    (config change mid-rebalance) defers the catch-up: the gate is scoped to
    account+market, not the new configuration digest."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "legacy-digest", "paper")
    legacy_target = TargetPortfolio(
        strategy_name="equity_dynamic_trend_1d_v1",
        strategy_version="1.0.0",
        decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        weights=(TargetWeight("AAPL", 0.10, "test"),),
        cash_weight=0.9,
    )
    legacy_risk = RiskOverlayDecision(legacy_target, False, (), 0.0, 0.10)
    decision_id = repo.record_decision(session, "acct", "legacy-digest", legacy_target, legacy_risk, Market.EQUITY)
    repo.save_rebalance_execution(decision_id, "acct", Market.EQUITY, REBALANCE_PHASE_SELL_PENDING)
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="legacy-sell-1",
            decision_id=decision_id,
            symbol="AAPL",
            market=Market.EQUITY,
            side=PortfolioOrderSide.SELL,
            quantity=1.0,
            reference_price=100.0,
            decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            strategy_name="equity_dynamic_trend_1d_v1",
            strategy_version="1.0.0",
            risk_reducing=True,
        ),
        "submitting",
    )
    runtime = PortfolioRuntime(
        _fixture_strategy(),
        EQUITY_UNIVERSE,
        _Broker(),
        repo,
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
        account_id="acct",
    )
    runtime.seed(_bars())
    result = runtime.startup_catch_up()
    assert result.status == "rebalance_in_progress"
    assert runtime.startup_catch_up_status == "rebalance_in_progress"


def test_startup_catch_up_propagates_rebalance_blocked(tmp_path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "legacy-digest", "paper")
    target = TargetPortfolio(
        strategy_name="equity_dynamic_trend_1d_v1",
        strategy_version="1.0.0",
        decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        weights=(TargetWeight("AAPL", 0.10, "test"),),
        cash_weight=0.9,
    )
    risk = RiskOverlayDecision(target, False, (), 0.0, 0.10)
    decision_id = repo.record_decision(session, "acct", "legacy-digest", target, risk, Market.EQUITY)
    repo.save_rebalance_execution(
        decision_id,
        "acct",
        Market.EQUITY,
        REBALANCE_PHASE_BLOCKED,
        status="reconciliation_required",
    )
    runtime = PortfolioRuntime(
        _fixture_strategy(),
        EQUITY_UNIVERSE,
        _Broker(),
        repo,
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
        account_id="acct",
    )
    runtime.seed(_bars())

    result = runtime.startup_catch_up()

    assert result.status == "rebalance_blocked"
    assert runtime.startup_catch_up_status == "rebalance_blocked"
    assert result.decision_id == decision_id


def test_forced_scheduled_applies_full_target_and_leaves_normal_cadence_unchanged(
    tmp_path,
) -> None:
    """force_scheduled on mid-period bars applies the FULL target (buys on an
    empty account); non-forced calls keep the not_scheduled cadence gate and
    normal scheduled decisions carry no decision_origin label."""
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    bars = _bars()
    runtime.seed(bars)
    last_timestamp = max(bar.timestamp for bar in bars)
    last_batch = [bar for bar in bars if bar.timestamp == last_timestamp]

    plain = runtime.process_closed_bars(last_batch)
    assert plain.status == "not_scheduled"
    assert plain.target is None

    forced = runtime.process_closed_bars(last_batch, force_scheduled=True, decision_origin="test_origin")
    assert forced.status == "submitted"
    assert forced.target is not None
    assert forced.target.metadata.get("decision_origin") == "test_origin"
    assert forced.target.invested_weight > 0
    assert forced.orders and all(order.side == PortfolioOrderSide.BUY for order in forced.orders)

    # A later normal scheduled decision is not labelled as a catch-up.
    next_boundary = RebalancePolicy(Market.EQUITY, "monthly").next_boundary_after(last_timestamp)
    next_batch = [
        Bar(next_boundary, symbol, "1d", 110.0, 110.0, 110.0, 110.0, 1_000_000, Market.EQUITY)
        for symbol in EQUITY_UNIVERSE
    ]
    scheduled = runtime.process_closed_bars(next_batch)
    assert scheduled.status == "submitted"
    assert scheduled.target is not None
    assert "decision_origin" not in scheduled.target.metadata


def test_forced_scheduled_with_entry_latch_caps_entries_and_keeps_origin(tmp_path) -> None:
    """A forced catch-up under an entry-risk latch still caps risk-increasing
    entries (SELL-only) and carries the decision_origin on the final target."""
    broker = _PositionedBroker(
        (
            PositionSnapshot(
                "AAPL",
                100,
                100,
                100,
                Market.EQUITY,
                qty_available=100,
                ownership="managed",
                managed_qty=100,
            ),
        )
    )
    runtime = PortfolioRuntime(
        _FixedTargetStrategy({"AAPL": 0.05, "MSFT": 0.10}),
        ("AAPL", "MSFT"),
        broker,
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
    )
    bars = _bars()
    runtime.seed(bars)
    last_batch = [bar for bar in bars if bar.timestamp == max(b.timestamp for b in bars)]
    latched = runtime.process_closed_bars(
        last_batch,
        entry_latch="daily_loss_limit",
        force_scheduled=True,
        decision_origin="startup_catch_up",
    )
    assert latched.status == "submitted"
    assert latched.target is not None
    assert latched.target.metadata.get("decision_origin") == "startup_catch_up"
    assert latched.target.metadata.get("entry_risk_latch") == "daily_loss_limit"
    assert latched.orders
    assert all(order.side == PortfolioOrderSide.SELL for order in latched.orders)
    assert all(order.symbol == "AAPL" for order in latched.orders)


def test_engine_run_startup_catch_up_guards_skip_when_not_ready() -> None:
    """The engine skips the catch-up entirely while warming up, halted by a
    circuit breaker, failed reconciliation, or not running."""
    runtime = _catch_up_engine()
    fake = _CatchUpRecorder()
    runtime._portfolio_runtimes = (fake,)
    runtime._warming_up = True
    runtime._run_startup_catch_up()
    assert fake.calls == []
    assert fake.startup_catch_up_status is None

    runtime._warming_up = False
    runtime._circuit_breaker_reason = "ownership_drift"
    runtime._run_startup_catch_up()
    assert fake.calls == []

    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = True
    runtime._run_startup_catch_up()
    assert fake.calls == []

    runtime._reconcile_failed = False
    runtime._running = False
    runtime._run_startup_catch_up()
    assert fake.calls == []


def test_engine_run_startup_catch_up_blocked_by_readiness_reasons() -> None:
    runtime = _catch_up_engine()
    runtime._entry_readiness_reasons = lambda _market: (
        "account_snapshot_stale",
        "worker_unavailable:pb-stocks",
    )
    fake = _CatchUpRecorder()
    runtime._portfolio_runtimes = (fake,)
    runtime._run_startup_catch_up()
    assert fake.calls == []
    assert fake.startup_catch_up_status == "blocked:account_snapshot_stale,worker_unavailable:pb-stocks"


def test_engine_run_startup_catch_up_propagates_entry_latch() -> None:
    runtime = _catch_up_engine()
    fake = _CatchUpRecorder()
    runtime._portfolio_runtimes = (fake,)
    runtime._entry_risk_latch = {Market.EQUITY: "daily_loss_limit", Market.CRYPTO: None}
    runtime._run_startup_catch_up()
    assert fake.calls == ["daily_loss_limit"]
    # A success status is owned by the portfolio runtime itself.
    assert fake.startup_catch_up_status is None


def test_engine_run_startup_catch_up_propagates_invariant_failure() -> None:
    """Unexpected catch-up exceptions are startup failures, not no-trade
    statuses that may be hidden while the engine appears healthy."""
    runtime = _catch_up_engine()

    class _BoomCatchUp(_CatchUpRecorder):
        def startup_catch_up(self, *, entry_latch=None):
            raise RuntimeError("catch-up boom")

    fake = _BoomCatchUp()
    runtime._portfolio_runtimes = (fake,)
    with pytest.raises(RuntimeError, match="catch-up boom"):
        runtime._run_startup_catch_up()
    assert fake.startup_catch_up_status is None
    assert runtime._state == "warming_up"
    assert runtime._circuit_breaker_reason is None


def test_health_exposes_startup_catch_up_fields() -> None:
    runtime = _health_runtime()
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            startup_catch_up_status="submitted",
            startup_catch_up_at=datetime(2025, 1, 1, tzinfo=UTC),
            previous_timestamp=datetime(2025, 1, 15, tzinfo=UTC),
            policy=RebalancePolicy(Market.EQUITY, "monthly"),
        ),
    )
    health = runtime.health()
    equity_catch_up = health.markets["equity"]["catch_up"]
    assert equity_catch_up == {
        "status": "submitted",
        "at": "2025-01-01T00:00:00+00:00",
        "next_boundary_after": "2025-02-01T00:00:00+00:00",
    }
    # A market without a portfolio runtime reports an empty catch-up snapshot.
    assert health.markets["crypto"]["catch_up"] == {
        "status": None,
        "at": None,
        "next_boundary_after": None,
    }


class _CatchUpRecorder:
    def __init__(self) -> None:
        self.calls: list[str | None] = []
        self.startup_catch_up_status: str | None = None
        self.market = Market.EQUITY
        self.result_status = "submitted"
        self.result_decision_id = "catchup-decision-1"
        # Signal-basis gating reads the newest seeded bar; the recorder
        # carries an empty history so the gate short-circuits to None.
        self.history: dict[str, list] = {}
        self.symbols: tuple[str, ...] = ()
        self.strategy = SimpleNamespace(timeframe="1d", minimum_history_bars=1)
        self.signal_verified_through_by_symbol: dict = {}
        self.signal_last_verified_at = None
        self.previous_timestamp = None

    def adopt_authoritative_signal_history(self, provider, watermarks) -> int:
        return 0

    def startup_catch_up(self, *, entry_latch: str | None = None) -> object:
        self.calls.append(entry_latch)
        return SimpleNamespace(status=self.result_status, decision_id=self.result_decision_id)


def _catch_up_engine() -> object:
    from parayabuck.runtime import EngineRuntime

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._state = "warming_up"
    runtime._portfolio_lock = threading.RLock()
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._portfolio_runtimes = ()
    runtime._entry_readiness_reasons = lambda _market: []
    return runtime
