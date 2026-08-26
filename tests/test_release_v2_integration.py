"""Release integration: v1 -> v2 strategy-identity migration on one account.

PAPER promotion changes ``configuration_digest`` (strategy name + cadence
parameters). The migration contract:

- existing v1-managed positions MUST stay managed under the v2 runtime
  (ownership is ledger/repository based, never digest-scoped);
- the v2 startup catch-up MUST NOT fire over managed positions
  (``already_invested``) and MUST NOT be tripped by v1 decisions
  (digest-scoped duplicate/superseded guards);
- v2's first scheduled boundary decision rebalances the v1 holdings exactly
  once, within the frozen risk caps, without layering (no double buy);
- the runtime-path rebalance policy for the v2 identities is weekly / 96h.

Offline: fake Alpaca client + local SQLite, same seam as the paper-chaos
gate. No real PAPER orders are ever placed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    PortfolioOrderSide,
    portfolio_risk_limits_from_config,
)
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioRuntimeResult,
    PortfolioStateRepository,
)

pytestmark = pytest.mark.paper_chaos

EQUITY_SYMBOLS = ("SPY", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
CRYPTO_SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD")

V1_EQUITY = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
}
V1_CRYPTO = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 168}
V2_EQUITY = {**V1_EQUITY, "rebalance_frequency": "weekly"}
V2_CRYPTO = {**V1_CRYPTO, "rebalance_hours": 96}
V3_EQUITY = {**V1_EQUITY, "rebalance_frequency": "weekly"}


def _daily_bars(
    symbols: tuple[str, ...],
    days: int,
    market: Market,
    *,
    start: datetime | None = None,
) -> list[Bar]:
    """Deterministic daily bars: higher-index symbols drift faster/noisier
    (equity momentum ranking) and a separate volatility schedule for crypto
    (BTC/ETH low-variance, SOL/XRP high-variance)."""
    start = start or datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            if market == Market.CRYPTO:
                # BTC/ETH: slow steady drift; SOL/XRP: choppy.
                high_vol = symbol in ("SOL/USD", "XRP/USD")
                amplitude = 2.0 if high_vol else 0.2
                drift = 1.0004 + (0.0 if high_vol else 0.0002 * (2 - offset))
            else:
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


def _by_day(bars: list[Bar]) -> dict[datetime, list[Bar]]:
    return {
        timestamp: [bar for bar in bars if bar.timestamp == timestamp]
        for timestamp in sorted({bar.timestamp for bar in bars})
    }


class _FakeAlpacaClient:
    """Minimal Alpaca REST surface used by the portfolio execution path."""

    def __init__(self) -> None:
        self.positions: list[SimpleNamespace] = []
        self.filled_orders: dict[str, SimpleNamespace] = {}

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
        return []

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
    broker.crypto_emergency_exit_bps = 75.0
    broker.crypto_stop_limit_offset_bps = 25.0
    broker.crypto_stop_cancel_timeout_seconds = 5.0
    broker.crypto_stop_cancel_poll_interval_seconds = 0.25
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
                asset_class="us_equity" if order.market == Market.EQUITY else "crypto",
            )
        )


def _decision_count(repo: PortfolioStateRepository, market: Market, digest: str) -> int:
    with repo.store.lock:
        row = repo.store.connection.execute(
            "SELECT COUNT(*) FROM portfolio_decisions WHERE market=? AND configuration_digest=?",
            (market.value, digest),
        ).fetchone()
    return int(row[0])


def test_v1_to_v2_migration_keeps_positions_managed_and_rebalances_once(
    tmp_path: Path,
) -> None:
    """G1/G2: v1-managed positions survive the digest switch; the v2 catch-up
    is blocked; the first v2 boundary rebalances exactly once without a
    double buy."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    broker = _broker(client, repo, tmp_path)

    total_days = 360
    equity_bars = _daily_bars(EQUITY_SYMBOLS, total_days, Market.EQUITY)
    crypto_bars = _daily_bars(CRYPTO_SYMBOLS, total_days, Market.CRYPTO)
    equity_days = sorted({bar.timestamp for bar in equity_bars})
    crypto_days = sorted({bar.timestamp for bar in crypto_bars})

    # --- Phase 1: the account runs v1 and owns managed positions -------------
    v1_equity = _runtime(
        "equity_residual_momentum_v1",
        V1_EQUITY,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.50,
    )
    assert rebalance_policy_for(v1_equity.strategy).frequency == "monthly"
    v1_equity.seed([bar for bar in equity_bars if bar.timestamp <= equity_days[299]])
    v1_decided = v1_equity.process_closed_bars(
        [bar for bar in equity_bars if bar.timestamp == equity_days[299]],
        force_scheduled=True,
        decision_origin="v1_monthly",
    )
    assert v1_decided.status == "submitted", v1_decided.status
    v1_equity_orders = list(v1_decided.orders)
    assert v1_equity_orders and any(o.side == PortfolioOrderSide.BUY for o in v1_equity_orders)

    v1_crypto = _runtime(
        "crypto_low_variance_v1",
        V1_CRYPTO,
        CRYPTO_SYMBOLS,
        repo,
        broker,
        Market.CRYPTO,
        maximum_allocation=0.10,
    )
    assert rebalance_policy_for(v1_crypto.strategy).hours == 168
    v1_crypto.seed([bar for bar in crypto_bars if bar.timestamp <= crypto_days[299]])
    v1_crypto_decided = v1_crypto.process_closed_bars(
        [bar for bar in crypto_bars if bar.timestamp == crypto_days[299]],
        force_scheduled=True,
        decision_origin="v1_weekly",
    )
    assert v1_crypto_decided.status == "submitted", v1_crypto_decided.status
    v1_crypto_orders = list(v1_crypto_decided.orders)
    assert v1_crypto_orders and any(o.side == PortfolioOrderSide.BUY for o in v1_crypto_orders)

    _fill_orders(v1_decided, repo, client)
    _fill_orders(v1_crypto_decided, repo, client)
    v1_equity.close()
    v1_crypto.close()

    held_equity = {p.symbol for p in client.positions if p.asset_class == "us_equity"}
    held_crypto = {p.symbol for p in client.positions if p.asset_class == "crypto"}
    assert held_equity and held_crypto

    # --- Phase 2: promote to v2 on the SAME account/repository --------------
    v2_equity = _runtime(
        "equity_residual_momentum_v2",
        V2_EQUITY,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.50,
    )
    assert rebalance_policy_for(v2_equity.strategy).frequency == "weekly"  # G4 runtime-path
    v2_equity_digest = v2_equity.configuration_digest
    v2_crypto = _runtime(
        "crypto_low_variance_v2",
        V2_CRYPTO,
        CRYPTO_SYMBOLS,
        repo,
        broker,
        Market.CRYPTO,
        maximum_allocation=0.10,
    )
    assert rebalance_policy_for(v2_crypto.strategy).hours == 96  # G4 runtime-path
    v2_crypto_digest = v2_crypto.configuration_digest
    assert v2_equity_digest != v1_equity.configuration_digest
    assert v2_crypto_digest != v1_crypto.configuration_digest

    v2_equity.seed([bar for bar in equity_bars if bar.timestamp <= equity_days[299]])
    v2_crypto.seed([bar for bar in crypto_bars if bar.timestamp <= crypto_days[299]])

    # v1 positions stay managed under v2 (ledger ownership, not digest).
    snapshot = broker.account()
    for symbol in held_equity | held_crypto:
        position = next(p for p in snapshot.positions if p.symbol == symbol)
        assert position.managed_qty > 1e-12, f"{symbol} lost managed ownership under v2"
        assert position.ownership in {"managed", "mixed"}

    # v2 catch-up must NOT fire over managed positions and must not be
    # confused by the v1 decisions (digest-scoped guards).
    assert v2_equity.startup_catch_up().status == "already_invested"
    assert v2_crypto.startup_catch_up().status == "already_invested"
    assert _decision_count(repo, Market.EQUITY, v2_equity_digest) == 0
    assert _decision_count(repo, Market.CRYPTO, v2_crypto_digest) == 0

    # --- Phase 3: v2's first boundary rebalances exactly once ---------------
    # Equity: first ISO-week start after day 300 (bars start on a Monday).
    # Crypto: first 96h block boundary after day 300.
    first_weekly_start = next(
        day for day in equity_days[301:] if day.isocalendar()[:2] != (day - timedelta(days=1)).isocalendar()[:2]
    )
    equity_decisions: list[datetime] = []
    crypto_decisions: list[datetime] = []
    for day in equity_days[300:]:
        equity_result = v2_equity.process_closed_bars([bar for bar in equity_bars if bar.timestamp == day])
        if equity_result.status in {"submitted", "sell_phase_submitted", "buy_phase_submitted"}:
            assert equity_result.target is not None
            assert equity_result.target.metadata.get("decision_origin") is None
            assert day.isocalendar()[:2] != (day - timedelta(days=1)).isocalendar()[:2], (
                "equity decision off the weekly cadence"
            )
            equity_decisions.append(day)
        crypto_result = v2_crypto.process_closed_bars([bar for bar in crypto_bars if bar.timestamp == day])
        if crypto_result.status in {"submitted", "sell_phase_submitted", "buy_phase_submitted"}:
            assert crypto_result.target is not None
            assert crypto_result.target.metadata.get("decision_origin") is None
            crypto_decisions.append(day)
    # The takeover fires at the FIRST v2 boundary, exactly once; the rest of
    # the window then follows the normal cadence (no gap, no duplicate).
    assert equity_decisions and equity_decisions[0] == first_weekly_start, (
        "v2 equity takeover must fire at the first weekly boundary"
    )
    assert len(equity_decisions) == len(set(equity_decisions)) >= 2
    for previous, current in zip(equity_decisions, equity_decisions[1:], strict=False):
        assert current.isocalendar()[:2] != previous.isocalendar()[:2]
    assert crypto_decisions and len(crypto_decisions) == len(set(crypto_decisions)) >= 2
    for previous, current in zip(crypto_decisions, crypto_decisions[1:], strict=False):
        epoch_delta = int(current.timestamp()) // (96 * 3600) - int(previous.timestamp()) // (96 * 3600)
        assert epoch_delta >= 1, "crypto decisions off the 96h cadence"
    assert _decision_count(repo, Market.EQUITY, v2_equity_digest) == len(equity_decisions)
    assert _decision_count(repo, Market.CRYPTO, v2_crypto_digest) == len(crypto_decisions)

    # No double buy: every BUY is a gap-close toward the target, never a layering
    # on top of the full target. Sells never exceed the managed quantity.
    with repo.store.lock:
        rows = repo.store.connection.execute(
            "SELECT decision_id, market, target_json FROM portfolio_decisions WHERE configuration_digest IN (?, ?)",
            (v2_equity_digest, v2_crypto_digest),
        ).fetchall()
    for _decision_id, market_value, target_json in rows:
        target = TargetPortfolio.from_dict(json.loads(str(target_json)))
        target_by_symbol = {w.symbol: w.weight for w in target.weights}
        order_rows = repo.store.connection.execute(
            "SELECT symbol, side, quantity, reference_price FROM portfolio_orders WHERE decision_id=?",
            (_decision_id,),
        ).fetchall()
        for symbol, side, qty, price in order_rows:
            held = next((float(p.qty) for p in client.positions if p.symbol == symbol), 0.0)
            if side == "buy":
                target_qty = target_by_symbol.get(symbol, 0.0) * 100_000.0 / float(price)
                assert float(qty) <= target_qty + 1.0, f"double-buy layering on {symbol}"
            else:
                assert float(qty) <= held + 1e-9, f"SELL exceeds managed qty on {symbol}"

    # Risk caps still hold on the takeover targets.
    for market, digest, cap, max_positions in (
        (Market.EQUITY, v2_equity_digest, 0.50, 6),
        (Market.CRYPTO, v2_crypto_digest, 0.10, 2),
    ):
        row = repo.store.connection.execute(
            "SELECT target_json FROM portfolio_decisions WHERE market=? AND configuration_digest=? "
            "ORDER BY decision_timestamp DESC LIMIT 1",
            (market.value, digest),
        ).fetchone()
        target = TargetPortfolio.from_dict(json.loads(str(row[0])))
        assert target.invested_weight <= cap + 1e-9
        assert len(target.weights) <= max_positions

    v2_equity.close()
    v2_crypto.close()


def test_v2_to_v3_paper_promotion_uses_normal_delta_and_restart_idempotency(
    tmp_path: Path,
) -> None:
    """V3 promotion consumes existing V2 holdings through the normal planner.

    No strategy-change liquidation path is allowed: the V3 target, current
    broker positions, ownership ledger, pending orders and restart guards are
    all exercised on one deterministic fake PAPER account.
    """
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    broker = _broker(client, repo, tmp_path)
    bars = _daily_bars(EQUITY_SYMBOLS, 360, Market.EQUITY)
    days = sorted({bar.timestamp for bar in bars})

    v2 = _runtime(
        "equity_residual_momentum_v2",
        V2_EQUITY,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.60,
    )
    v2.seed([bar for bar in bars if bar.timestamp <= days[299]])
    v2_result = v2.process_closed_bars(
        [bar for bar in bars if bar.timestamp == days[299]],
        force_scheduled=True,
        decision_origin="v2_paper_seed",
    )
    assert v2_result.status == "submitted"
    assert v2_result.orders
    _fill_orders(v2_result, repo, client)
    v2.close()
    held_symbols = {position.symbol for position in client.positions if position.asset_class == "us_equity"}
    assert held_symbols

    v3 = _runtime(
        "equity_residual_momentum_v3",
        V3_EQUITY,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.60,
    )
    v3.seed([bar for bar in bars if bar.timestamp <= days[299]])
    assert v3.startup_catch_up().status == "already_invested"
    first = v3.process_closed_bars(
        [bar for bar in bars if bar.timestamp == days[300]],
        force_scheduled=True,
    )
    assert first.status in {"submitted", "sell_phase_submitted"}
    assert first.target is not None
    assert first.target.strategy_name == "equity_residual_momentum_v3"
    assert first.target.strategy_version == "3.0.0"
    assert len(first.target.weights) <= 6
    assert all(weight.weight <= 0.10 for weight in first.target.weights)
    assert first.target.invested_weight <= 0.60
    paper_observability = first.target.metadata["paper_observability"]
    assert paper_observability["strategy_name"] == "equity_residual_momentum_v3"
    assert paper_observability["decision_timestamp"] == days[300].isoformat()
    assert paper_observability["symbols"]

    by_symbol = {}
    for order in first.orders:
        by_symbol.setdefault(order.symbol, set()).add(order.side)
        assert order.strategy_name == "equity_residual_momentum_v3"
        assert order.strategy_version == "3.0.0"
    assert all(len(sides) == 1 for sides in by_symbol.values())
    assert all(not (order.side == PortfolioOrderSide.SELL and order.quantity > 100.0) for order in first.orders)

    with repo.store.lock:
        before_orders = repo.store.connection.execute("SELECT COUNT(*) FROM portfolio_orders").fetchone()[0]
        before_decisions = repo.store.connection.execute(
            "SELECT COUNT(*) FROM portfolio_decisions WHERE strategy_name=?",
            ("equity_residual_momentum_v3",),
        ).fetchone()[0]
    v3.close()

    restarted = _runtime(
        "equity_residual_momentum_v3",
        V3_EQUITY,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.60,
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
            ("equity_residual_momentum_v3",),
        ).fetchone()[0]
    assert (after_orders, after_decisions) == (before_orders, before_decisions)
    restarted.close()


def test_v3_runtime_signal_basis_latch_blocks_entries_until_verified(
    tmp_path: Path,
) -> None:
    """The shared production entry latch applies to V3 without new math."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    client = _FakeAlpacaClient()
    broker = _broker(client, repo, tmp_path)
    bars = _daily_bars(EQUITY_SYMBOLS, 360, Market.EQUITY)
    days = sorted({bar.timestamp for bar in bars})
    runtime = _runtime(
        "equity_residual_momentum_v3",
        V3_EQUITY,
        EQUITY_SYMBOLS,
        repo,
        broker,
        Market.EQUITY,
        maximum_allocation=0.60,
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
