"""Release integration: strategy-identity migration on one account.

PAPER promotion changes ``configuration_digest`` (strategy parameters). The
migration contract, exercised on ``equity_leveraged_trend_v1``:

- existing managed positions MUST stay managed under the promoted runtime
  (ownership is ledger/repository based, never digest-scoped);
- the promoted startup catch-up MUST NOT fire over managed positions
  (``already_invested``) and MUST NOT be tripped by the previous decisions
  (digest-scoped duplicate/superseded guards);
- the promoted runtime rebalances through the normal planner within the
  shipped risk caps, and a restart does not duplicate the decision.

Offline: fake Alpaca client + local SQLite, same seam as the paper-chaos
gate. No real PAPER orders are ever placed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alpaca.trading.models import Order

from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    PortfolioOrderSide,
    portfolio_risk_limits_from_config,
)
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioRuntimeResult,
    PortfolioStateRepository,
)

pytestmark = pytest.mark.paper_chaos

EQUITY_SYMBOLS = ("QQQ", "QLD")

TREND_EQUITY = {
    "signal_symbol": "QQQ",
    "hold_symbol": "QLD",
    "sma_bars": 200,
    "vol_target": 0.35,
    "vol_window": 20,
    "rebalance_frequency": "hours:24",
}


def _daily_bars(
    symbols: tuple[str, ...],
    days: int,
    market: Market,
    *,
    start: datetime | None = None,
) -> list[Bar]:
    """Deterministic daily bars: higher-index symbols drift faster/noisier."""
    start = start or datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            amplitude = 1.0 + offset * 0.02
            drift = 1.0015 + offset * 0.00003
            noise = (index % 7) * amplitude * 0.001
            close = 100.0 * (drift**index) * (1.0 + noise)
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close,
                    close * 1.001,
                    close * 0.999,
                    close,
                    1_000_000.0,
                    market,
                )
            )
    return bars


class _FakeAlpacaClient:
    """Minimal Alpaca REST surface used by the portfolio execution path."""

    def __init__(self) -> None:
        self.positions: list[SimpleNamespace] = []
        self.filled_orders: dict[str, SimpleNamespace] = {}
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
        )

    def get_all_positions(self) -> list[SimpleNamespace]:
        return self.positions

    def get_open_position(self, symbol: str) -> SimpleNamespace:
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

    def get(self, path: str, params: dict[str, object] | None = None) -> list[Order]:
        if path != "/orders":
            raise ValueError(f"unsupported GET {path}")
        return [Order.model_construct(**vars(order)) for order in self.open_orders]

    def submit_order(self, request: object) -> SimpleNamespace:
        return SimpleNamespace(id=f"broker-{getattr(request, 'client_order_id', '')}")

    def get_order_by_client_id(self, client_order_id: str) -> SimpleNamespace | None:
        return self.filled_orders.get(client_order_id)


def _broker(client: _FakeAlpacaClient, repo: PortfolioStateRepository, tmp_path: Path) -> AlpacaBroker:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.ledger = ExecutionLedger(str(tmp_path / "execution.sqlite3"))
    broker.portfolio_owned_quantities = lambda: repo.owned_quantities(broker.account_id)
    broker.portfolio_open_orders = lambda: repo.open_orders(broker.account_id)
    broker._account_id = "acct"
    broker._client = client
    broker._asset_cache = {}
    broker._symbol_exit_locks = {}
    broker.manage_external_positions = False
    broker._peak_equity = 0.0
    broker._peak_state_path = Path(tmp_path) / "peak.json"
    broker._stop_state_path = Path(tmp_path) / "stops.json"
    return broker


def _runtime(
    strategy_name: str,
    parameters: dict[str, object],
    symbols: tuple[str, ...],
    repository: PortfolioStateRepository,
    broker: AlpacaBroker,
    market: Market,
    *,
    maximum_allocation: float,
) -> PortfolioRuntime:
    strategy = portfolio_strategy_from(strategy_name, parameters)
    assert strategy.market == market
    return PortfolioRuntime(
        strategy,
        symbols,
        broker,
        repository,
        mode="paper",
        maximum_allocation=maximum_allocation,
        risk_limits=portfolio_risk_limits_from_config(load_config().risk),
        account_id="acct",
        allow_static_universe=True,
    )


def _fill_orders(result: PortfolioRuntimeResult, repo: PortfolioStateRepository, client: _FakeAlpacaClient) -> None:
    """Apply every BUY order of the latest decision as a broker fill and
    mirror the resulting positions on the fake client."""
    for order in result.orders:
        if order.side != PortfolioOrderSide.BUY:
            continue
        repo.apply_trade_update(
            client_order_id=order.client_order_id,
            broker_order_id="broker-" + order.client_order_id,
            status="fill",
            cumulative_filled_quantity=order.quantity,
            average_fill_price=order.reference_price,
            occurred_at=order.decision_timestamp,
            activity_id="activity-" + order.client_order_id,
            fee=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )
        client.filled_orders[order.client_order_id] = SimpleNamespace(
            id="broker-" + order.client_order_id,
            status="filled",
            qty=str(order.quantity),
            filled_qty=str(order.quantity),
            symbol=order.symbol,
            side="buy",
        )
        client.positions.append(
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
        )


def _decision_count(repo: PortfolioStateRepository, market: Market, digest: str) -> int:
    with repo.store.lock:
        row = repo.store.connection.execute(
            "SELECT COUNT(*) FROM portfolio_decisions WHERE market=? AND configuration_digest=?",
            (market.value, digest),
        ).fetchone()
    return int(row[0])


def test_equity_parameter_promotion_uses_normal_delta_and_restart_idempotency(
    tmp_path: Path,
) -> None:
    """A promotion to a new parameter spec (new ``configuration_digest``)
    consumes the existing holdings through the normal planner.

    No strategy-change liquidation path is allowed: the promoted target,
    current broker positions, ownership ledger, pending orders and restart
    guards are all exercised on one deterministic fake PAPER account.
    """
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    broker = _broker(client, repo, tmp_path)
    bars = _daily_bars(EQUITY_SYMBOLS, 360, Market.EQUITY)
    days = sorted({bar.timestamp for bar in bars})
    promoted = {**TREND_EQUITY, "sma_bars": 50, "vol_target": 0.20}

    baseline = _runtime(
        "equity_leveraged_trend_v1",
        TREND_EQUITY,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.93,
    )
    baseline.seed([bar for bar in bars if bar.timestamp <= days[299]])
    baseline_result = baseline.process_closed_bars(
        [bar for bar in bars if bar.timestamp == days[299]],
        force_scheduled=True,
        decision_origin="baseline_paper_seed",
    )
    assert baseline_result.status == "submitted"
    assert baseline_result.orders
    _fill_orders(baseline_result, repo, client)
    baseline.close()
    held = {position.symbol: float(position.qty) for position in client.positions}
    assert held

    runtime = _runtime(
        "equity_leveraged_trend_v1",
        promoted,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.93,
    )
    assert runtime.configuration_digest != baseline.configuration_digest
    runtime.seed([bar for bar in bars if bar.timestamp <= days[299]])

    # Baseline positions stay managed under the promoted digest (ledger
    # ownership, not digest-scoped).
    snapshot = broker.account()
    for symbol in held:
        position = next(p for p in snapshot.positions if p.symbol == symbol)
        assert position.managed_qty > 1e-12, f"{symbol} lost managed ownership after promotion"
        assert position.ownership in {"managed", "mixed"}

    # The promoted catch-up must not fire over managed positions nor be
    # confused by the baseline decision (digest-scoped guards).
    assert runtime.startup_catch_up().status == "already_invested"
    assert _decision_count(repo, Market.EQUITY, runtime.configuration_digest) == 0
    first = runtime.process_closed_bars(
        [bar for bar in bars if bar.timestamp == days[300]],
        force_scheduled=True,
    )
    assert first.status in {"submitted", "sell_phase_submitted"}
    assert first.target is not None
    assert first.target.strategy_name == "equity_leveraged_trend_v1"
    assert first.target.strategy_version == "1.0.0"
    # Shipped equity risk caps: at most 8 positions, 0.93 per asset / total.
    assert len(first.target.weights) <= 8
    assert all(weight.weight <= 0.93 for weight in first.target.weights)
    assert first.target.invested_weight <= 0.93
    paper_observability = first.target.metadata["paper_observability"]
    assert paper_observability["strategy_name"] == "equity_leveraged_trend_v1"
    assert paper_observability["decision_timestamp"] == days[300].isoformat()
    assert paper_observability["symbols"]

    by_symbol = {}
    for order in first.orders:
        by_symbol.setdefault(order.symbol, set()).add(order.side)
        assert order.strategy_name == "equity_leveraged_trend_v1"
        assert order.strategy_version == "1.0.0"
        if order.side == PortfolioOrderSide.SELL:
            assert order.quantity <= held.get(order.symbol, 0.0) + 1e-9
    assert all(len(sides) == 1 for sides in by_symbol.values())

    with repo.store.lock:
        before_orders = repo.store.connection.execute("SELECT COUNT(*) FROM portfolio_orders").fetchone()[0]
        before_decisions = repo.store.connection.execute(
            "SELECT COUNT(*) FROM portfolio_decisions WHERE strategy_name=?",
            ("equity_leveraged_trend_v1",),
        ).fetchone()[0]
    runtime.close()

    restarted = _runtime(
        "equity_leveraged_trend_v1",
        promoted,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.93,
    )
    restarted.seed([bar for bar in bars if bar.timestamp <= days[300]])
    duplicate = restarted.process_closed_bars(
        [bar for bar in bars if bar.timestamp == days[300]],
        force_scheduled=True,
    )
    assert duplicate.status == "duplicate_decision"
    with repo.store.lock:
        after_orders = repo.store.connection.execute("SELECT COUNT(*) FROM portfolio_orders").fetchone()[0]
        after_decisions = repo.store.connection.execute(
            "SELECT COUNT(*) FROM portfolio_decisions WHERE strategy_name=?",
            ("equity_leveraged_trend_v1",),
        ).fetchone()[0]
    assert (after_orders, after_decisions) == (before_orders, before_decisions)
    restarted.close()


def test_equity_runtime_signal_basis_latch_blocks_entries_until_verified(
    tmp_path: Path,
) -> None:
    """The shared production entry latch applies to the equity runtime
    without new math."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    broker = _broker(client, repo, tmp_path)
    bars = _daily_bars(EQUITY_SYMBOLS, 360, Market.EQUITY)
    days = sorted({bar.timestamp for bar in bars})
    runtime = _runtime(
        "equity_leveraged_trend_v1",
        TREND_EQUITY,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.93,
    )
    runtime.seed([bar for bar in bars if bar.timestamp <= days[299]])

    blocked = runtime.process_closed_bars(
        [bar for bar in bars if bar.timestamp == days[300]],
        force_scheduled=True,
        entry_latch="signal_adjustment_unverified",
    )
    assert blocked.target is not None
    assert all(order.side != PortfolioOrderSide.BUY for order in blocked.orders)
    assert blocked.target.metadata["paper_observability"]["blocked_or_skipped_reason"] == "signal_adjustment_unverified"

    verified_through = {symbol: days[301] for symbol in EQUITY_SYMBOLS}
    runtime.signal_verified_through_by_symbol = verified_through
    proceeded = runtime.process_closed_bars(
        [bar for bar in bars if bar.timestamp == days[301]],
        force_scheduled=True,
    )
    assert proceeded.status == "submitted"
    assert any(order.side == PortfolioOrderSide.BUY for order in proceeded.orders)
    assert proceeded.target is not None
    assert proceeded.target.metadata["paper_observability"]["signal_verified_through"] == days[301].isoformat()
    runtime.close()
