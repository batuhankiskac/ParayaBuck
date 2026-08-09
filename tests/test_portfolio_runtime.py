from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
)
from parayabuck.portfolio.equity_dynamic_trend import EquityDynamicTrend1dV1
from parayabuck.portfolio.runtime_service import (
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

    def open_portfolio_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


def _runtime(tmp_path, broker: _Broker, *, mode: str = "paper", cap: float = 0.0):
    return PortfolioRuntime(
        EquityDynamicTrend1dV1({"top_n": 2, "exclude_symbols": ["SPY"]}),
        EQUITY_UNIVERSE,
        broker,
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        mode=mode,
        maximum_allocation=0.50,
        live_dollar_cap=cap,
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
    strategy = EquityDynamicTrend1dV1({"top_n": 2, "exclude_symbols": ["SPY"]})
    limits = PortfolioRiskLimits()
    first_runtime = PortfolioRuntime(
        strategy,
        EQUITY_UNIVERSE,
        _Broker(),
        PortfolioStateRepository(tmp_path / "first.sqlite3"),
        mode="live",
        maximum_allocation=0.50,
        risk_limits=limits,
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
        universe_version="scanner-policy",
        live_dollar_cap=1_000,
    )

    assert first_runtime.configuration_digest == second_runtime.configuration_digest
    assert first_runtime.configuration_digest != configuration_digest(
        strategy, "live", 0.50, "changed-policy", limits
    )


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
        row = connection.execute(
            "SELECT fee, realized_pnl, unrealized_pnl, slippage FROM portfolio_fills"
        ).fetchone()
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
        for position in self.positions:
            if position.symbol == symbol:
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
    broker.ledger = (
        ExecutionLedger(str(ledger_path)) if with_ledger and ledger_path is not None else None
    )
    broker.portfolio_owned_symbols = repo.owned_symbols
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
    broker._peak_state_path = (
        Path(tmp_path) / "peak.json" if tmp_path is not None else Path("/tmp/peak.json")
    )
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
        EquityDynamicTrend1dV1({"top_n": 2, "exclude_symbols": ["SPY"]}),
        EQUITY_UNIVERSE,
        broker,
        repo,
        mode="paper",
        maximum_allocation=0.50,
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
    assert repo.owned_symbols() == owned

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
    assert repo.owned_symbols() == set()

    exited_snapshot = broker.account()
    assert all(position.ownership == "external" for position in exited_snapshot.positions)


def test_portfolio_ownership_survives_runtime_restart(tmp_path) -> None:
    """Ownership derives from the persisted repository, not process memory."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    first = PortfolioRuntime(
        EquityDynamicTrend1dV1({"top_n": 2, "exclude_symbols": ["SPY"]}),
        EQUITY_UNIVERSE,
        _portfolio_broker(client, repo, tmp_path),
        repo,
        mode="paper",
        maximum_allocation=0.50,
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
        next(
            position for position in restarted_snapshot.positions if position.symbol == order.symbol
        ).ownership
        == "managed"
    )


def test_partial_exit_keeps_ownership_until_fully_flat(tmp_path) -> None:
    """A partial SELL reduces the net position but ownership persists until the
    last share is gone; only a full exit reverts the symbol to external."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    runtime = PortfolioRuntime(
        EquityDynamicTrend1dV1({"top_n": 2, "exclude_symbols": ["SPY"]}),
        EQUITY_UNIVERSE,
        _portfolio_broker(client, repo, tmp_path),
        repo,
        mode="paper",
        maximum_allocation=0.50,
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
    assert order.symbol in repo.owned_symbols()

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
    assert order.symbol in repo.owned_symbols()


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
    broker.portfolio_owned_symbols = lambda: {"BTC/USD"}

    broker.reconcile()

    stops = [order for order in client.submitted if getattr(order, "side", None) == "sell"]
    assert len(stops) == 1
    expected_stop = 100_000 * (1 - 75.0 / 10_000)
    assert float(stops[0].stop_price) == pytest.approx(expected_stop)
