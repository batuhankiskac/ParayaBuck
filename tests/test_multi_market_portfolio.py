"""Multi-market portfolio contract tests.

Equity and crypto strategies produce target portfolios independently; the
portfolio/risk layer then combines their account-level impact on ONE
authoritative account: gross exposure, cash buffer, per-market sleeve caps,
per-asset position limits and SELL->BUY ordering all hold across markets.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    TargetToOrderPlanner,
)
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
)


def _limits() -> PortfolioRiskLimits:
    return PortfolioRiskLimits(
        daily_loss_limit=0.005,
        drawdown_halt=0.05,
        maximum_gross=0.60,
        maximum_equity=0.60,
        maximum_crypto=0.10,
        maximum_equity_asset=0.10,
        maximum_crypto_asset=0.05,
        minimum_cash=0.40,
        maximum_positions=6,
        maximum_crypto_positions=2,
    )


def _account(
    *,
    equity_positions: tuple[tuple[str, float, float], ...] = (),
    crypto_positions: tuple[tuple[str, float, float], ...] = (),
    equity: float = 100_000.0,
) -> AccountSnapshot:
    positions = tuple(
        PositionSnapshot(
            symbol=symbol,
            qty=qty,
            avg_price=price,
            current_price=price,
            market=Market.EQUITY,
            qty_available=qty,
            ownership="managed",
            managed_qty=qty,
        )
        for symbol, qty, price in equity_positions
    ) + tuple(
        PositionSnapshot(
            symbol=symbol,
            qty=qty,
            avg_price=price,
            current_price=price,
            market=Market.CRYPTO,
            qty_available=qty,
            ownership="managed",
            managed_qty=qty,
        )
        for symbol, qty, price in crypto_positions
    )
    invested = sum(position.market_value for position in positions)
    return AccountSnapshot(
        cash=equity - invested,
        equity=equity,
        peak_equity=equity,
        daily_pnl=0.0,
        positions=positions,
    )


def _target(
    strategy_name: str,
    timestamp: datetime,
    weights: tuple[tuple[str, float], ...],
) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name=strategy_name,
        strategy_version="1.0.0",
        decision_timestamp=timestamp,
        weights=tuple(TargetWeight(symbol, weight, "test") for symbol, weight in weights),
        cash_weight=max(0.0, 1.0 - sum(weight for _symbol, weight in weights)),
        universe_version="test",
        parameters=(),
    )


def test_equity_and_crypto_targets_combine_within_account_limits() -> None:
    """Both sleeves are sized independently, then the shared risk layer keeps
    the COMBINED account inside gross, cash, sleeve and position limits."""
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    overlay = PortfolioRiskOverlay(_limits())
    account = _account(equity_positions=(("AAPL", 100.0, 100.0),))
    approved_equity = ("AAPL", "MSFT", "NVDA")
    approved_crypto = ("BTC/USD", "ETH/USD", "SOL/USD")

    equity_target = _target(
        "equity_residual_momentum_v1",
        timestamp,
        (("AAPL", 0.10), ("MSFT", 0.10), ("NVDA", 0.10)),
    )
    equity_decision = overlay.apply(
        equity_target,
        account,
        market=Market.EQUITY,
        approved_symbols=approved_equity,
        maximum_allocation=0.50,
        mode="paper",
    )
    assert not equity_decision.halted
    assert equity_decision.gross_after <= 0.30 + 0.10 + 1e-9  # AAPL exists: 10% target + external

    # Crypto decides against the SAME account (which now holds the equity
    # positions the broker filled).
    held_account = _account(
        equity_positions=(("AAPL", 100.0, 100.0), ("MSFT", 100.0, 100.0), ("NVDA", 100.0, 100.0)),
    )
    crypto_target = _target(
        "crypto_low_variance_v1",
        timestamp,
        (("BTC/USD", 0.05), ("ETH/USD", 0.05)),
    )
    crypto_decision = overlay.apply(
        crypto_target,
        held_account,
        market=Market.CRYPTO,
        approved_symbols=approved_crypto,
        maximum_allocation=0.10,
        mode="paper",
    )
    assert not crypto_decision.halted
    # The crypto MANAGED sleeve never exceeds its own cap even though the
    # account already holds equity positions (which count as immutable gross).
    managed_exposure = crypto_decision.target.invested_weight
    assert managed_exposure <= 0.10 + 1e-9
    # Combined account: equity 30% + crypto 10% <= max gross 60% and the cash
    # buffer (>=40%) holds.
    combined_gross = 0.30 + managed_exposure
    assert combined_gross <= 0.60 + 1e-9
    assert 1.0 - combined_gross >= 0.40 - 1e-9


def test_single_surviving_crypto_asset_cannot_absorb_the_sleeve() -> None:
    """One positive-trend crypto must be capped by the per-asset limit even
    when the strategy proposes a large within-sleeve weight."""
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    overlay = PortfolioRiskOverlay(_limits())
    account = _account()
    target = _target(
        "crypto_low_variance_v1",
        timestamp,
        (("BTC/USD", 0.5),),  # strategy would like half of its own sleeve
    )
    decision = overlay.apply(
        target,
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD", "ETH/USD"),
        maximum_allocation=0.10,
        mode="paper",
    )
    btc_weight = decision.target.weight_for("BTC/USD", Market.CRYPTO)
    assert btc_weight <= 0.05 + 1e-9  # max_crypto_position_pct
    assert decision.target.invested_weight <= 0.10 + 1e-9  # max_crypto_exposure


def test_equity_sleeve_respects_allocation_and_position_limits() -> None:
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    overlay = PortfolioRiskOverlay(_limits())
    account = _account()
    # 8 symbols would like 8 positions at 10% each: position count and the
    # equity sleeve cap (50%) must both bind.
    weights = tuple((f"S{i:02d}", 0.10) for i in range(8))
    target = _target("equity_residual_momentum_v1", timestamp, weights)
    decision = overlay.apply(
        target,
        account,
        market=Market.EQUITY,
        approved_symbols=tuple(symbol for symbol, _weight in weights),
        maximum_allocation=0.50,
        mode="paper",
    )
    assert len(decision.target.weights) <= 6  # maximum_positions
    assert decision.target.invested_weight <= 0.50 + 1e-9  # maximum_allocation


def _ranked_target(
    strategy_name: str,
    timestamp: datetime,
    weights: tuple[tuple[str, float], ...],
) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name=strategy_name,
        strategy_version="1.0.0",
        decision_timestamp=timestamp,
        weights=tuple(
            TargetWeight(symbol, weight, "test", rank=index)
            for index, (symbol, weight) in enumerate(weights, start=1)
        ),
        cash_weight=max(0.0, 1.0 - sum(weight for _symbol, weight in weights)),
        universe_version="test",
        parameters=(),
    )


def test_global_position_limit_counts_existing_cross_market_slots_and_keeps_equity_ranks() -> None:
    """The six-account-position cap includes already-held crypto positions."""
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    overlay = PortfolioRiskOverlay(_limits())
    account = _account(
        crypto_positions=(
            ("BTC/USD", 25.0, 100.0),
            ("ETH/USD", 25.0, 100.0),
        )
    )
    symbols = tuple((f"S{index:02d}", 0.10) for index in range(8))
    decision = overlay.apply(
        _ranked_target("equity_residual_momentum_v3", timestamp, symbols),
        account,
        market=Market.EQUITY,
        approved_symbols=tuple(symbol for symbol, _weight in symbols),
        maximum_allocation=0.55,
        mode="paper",
    )

    assert tuple(weight.symbol for weight in decision.target.weights) == ("S00", "S01", "S02", "S03")
    assert decision.gross_after == pytest.approx(0.45)
    assert len(account.positions) + len(decision.target.weights) == 6
    assert "position_count_limit" in decision.reasons


def test_same_cycle_reservation_coordinates_global_position_limit() -> None:
    """A prior market's same-cycle target reserves account-level slots."""
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    overlay = PortfolioRiskOverlay(_limits())
    account = _account()
    equity_symbols = tuple((f"S{index:02d}", 0.10) for index in range(6))
    reserved = tuple((Market.EQUITY, symbol) for symbol, _weight in equity_symbols)
    decision = overlay.apply(
        _target("crypto_low_variance_v1", timestamp, (("BTC/USD", 0.05), ("ETH/USD", 0.05))),
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD", "ETH/USD"),
        maximum_allocation=0.10,
        mode="paper",
        reserved_position_keys=reserved,
    )

    assert decision.target.weights == ()
    assert "position_count_limit" in decision.reasons


class _CoordinatedPortfolioRuntime:
    """Small engine-facing seam test double using the real risk overlay."""

    def __init__(
        self,
        market: Market,
        target: TargetPortfolio,
        symbols: tuple[str, ...],
        maximum_allocation: float,
        *,
        emit_orders: bool = True,
    ) -> None:
        self.market = market
        self.strategy = SimpleNamespace(timeframe="1d", name=f"{market.value}_test")
        self.symbols = symbols
        self.target = target
        self.maximum_allocation = maximum_allocation
        self.emit_orders = emit_orders
        self.overlay = PortfolioRiskOverlay(_limits())
        self.reservations: list[tuple[tuple[Market, str], ...]] = []
        self.accounts_seen: list[AccountSnapshot | None] = []
        self.last_target: TargetPortfolio | None = None
        self.history: dict[str, list[Bar]] = {}
        self.account = _account()

    def process_closed_bars(
        self,
        _bars: list[Bar],
        *,
        entry_latch: str | None = None,
        account: AccountSnapshot | None = None,
        reserved_position_keys: tuple[tuple[Market, str], ...] = (),
    ) -> SimpleNamespace:
        del entry_latch
        assert account is not None
        self.accounts_seen.append(account)
        self.reservations.append(reserved_position_keys)
        decision = self.overlay.apply(
            self.target,
            account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=self.maximum_allocation,
            mode="paper",
            reserved_position_keys=reserved_position_keys,
        )
        self.last_target = decision.target
        orders = (
            tuple(
                SimpleNamespace(
                    symbol=weight.symbol,
                    market=self.market,
                    side="buy",
                )
                for weight in decision.target.weights
                if weight.weight > 0.0
            )
            if self.emit_orders
            else ()
        )
        return SimpleNamespace(
            status="submitted",
            target=decision.target,
            risk=None,
            orders=orders,
            submitted_order_ids=(),
        )

    def startup_catch_up(
        self,
        *,
        entry_latch: str | None = None,
        account: AccountSnapshot | None = None,
        reserved_position_keys: tuple[tuple[Market, str], ...] = (),
    ) -> SimpleNamespace:
        del entry_latch
        self.accounts_seen.append(account)
        self.reservations.append(reserved_position_keys)
        decision = self.overlay.apply(
            self.target,
            account or self.account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=self.maximum_allocation,
            mode="paper",
            reserved_position_keys=reserved_position_keys,
        )
        self.last_target = decision.target
        orders = (
            tuple(
                SimpleNamespace(
                    symbol=weight.symbol,
                    market=self.market,
                    side="buy",
                )
                for weight in decision.target.weights
                if weight.weight > 0.0
            )
            if self.emit_orders
            else ()
        )
        return SimpleNamespace(
            status="submitted",
            decision_id=f"{self.market.value}-catch-up",
            target=decision.target,
            orders=orders,
        )


class _RecoveryPortfolioRuntime:
    """Engine-facing restart-recovery double that records shared context."""

    def __init__(self, market: Market, buy_symbols: tuple[str, ...]) -> None:
        self.market = market
        self.strategy = SimpleNamespace(timeframe="1d", name=f"{market.value}_recovery")
        self.buy_symbols = buy_symbols
        self.calls: list[tuple[AccountSnapshot | None, tuple[tuple[Market, str], ...]]] = []

    def advance_rebalance(
        self,
        *,
        account: AccountSnapshot | None = None,
        reserved_position_keys: tuple[tuple[Market, str], ...] = (),
    ) -> SimpleNamespace:
        self.calls.append((account, reserved_position_keys))
        return SimpleNamespace(
            status="buy_phase_submitted",
            decision_id=f"{self.market.value}-recovery",
            orders=tuple(
                SimpleNamespace(symbol=symbol, market=self.market, side=PortfolioOrderSide.BUY)
                for symbol in self.buy_symbols
            ),
        )


def test_engine_coordinates_crypto_before_equity_for_global_slots() -> None:
    """Crypto reservation is evaluated before the equity sleeve."""
    from parayabuck.runtime import EngineRuntime

    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    equity_symbols = tuple(f"S{index:02d}" for index in range(6))
    equity_runtime = _CoordinatedPortfolioRuntime(
        Market.EQUITY,
        _ranked_target(
            "equity_test",
            timestamp,
            tuple((symbol, 0.10) for symbol in equity_symbols),
        ),
        equity_symbols,
        0.60,
    )
    crypto_runtime = _CoordinatedPortfolioRuntime(
        Market.CRYPTO,
        _target("crypto_test", timestamp, (("BTC/USD", 0.05), ("ETH/USD", 0.05))),
        ("BTC/USD", "ETH/USD"),
        0.10,
    )
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._portfolio_lock = threading.RLock()
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._portfolio_runtimes = (equity_runtime, crypto_runtime)
    runtime._entry_readiness_reasons = lambda _market: ()
    runtime._equity_signal_basis_latch = lambda _timestamp: None
    runtime._last_portfolio_status = {}
    account = _account()
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime._fresh_account_snapshot = lambda: account

    runtime._process_portfolio_timestamp(
        [
            Bar(timestamp, "S00", "1d", 100.0, 100.0, 100.0, 100.0, 1_000.0, Market.EQUITY),
            Bar(timestamp, "BTC/USD", "1d", 100.0, 100.0, 100.0, 100.0, 1_000.0, Market.CRYPTO),
        ]
    )

    assert equity_runtime.last_target is not None
    assert crypto_runtime.last_target is not None
    assert len(equity_runtime.last_target.weights) + len(crypto_runtime.last_target.weights) == 6
    assert tuple(weight.symbol for weight in crypto_runtime.last_target.weights) == ("BTC/USD", "ETH/USD")
    assert tuple(weight.symbol for weight in equity_runtime.last_target.weights) == equity_symbols[:4]
    assert crypto_runtime.reservations == [()]
    assert equity_runtime.reservations == [
        ((Market.CRYPTO, "BTC/USD"), (Market.CRYPTO, "ETH/USD"))
    ]


def test_engine_startup_catch_up_coordinates_crypto_before_equity() -> None:
    """Startup catch-up applies the same crypto-first slot policy."""
    from parayabuck.runtime import EngineRuntime

    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    equity_symbols = tuple(f"S{index:02d}" for index in range(6))
    equity_runtime = _CoordinatedPortfolioRuntime(
        Market.EQUITY,
        _ranked_target(
            "equity_test",
            timestamp,
            tuple((symbol, 0.10) for symbol in equity_symbols),
        ),
        equity_symbols,
        0.60,
    )
    crypto_runtime = _CoordinatedPortfolioRuntime(
        Market.CRYPTO,
        _target("crypto_test", timestamp, (("BTC/USD", 0.05), ("ETH/USD", 0.05))),
        ("BTC/USD", "ETH/USD"),
        0.10,
    )
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._portfolio_lock = threading.RLock()
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._portfolio_runtimes = (equity_runtime, crypto_runtime)
    runtime._entry_readiness_reasons = lambda _market: ()
    runtime._equity_signal_basis_latch = lambda _timestamp: None

    account = _account()
    runtime._run_startup_catch_up(account=account)

    assert equity_runtime.last_target is not None
    assert crypto_runtime.last_target is not None
    assert len(equity_runtime.last_target.weights) + len(crypto_runtime.last_target.weights) == 6
    assert tuple(weight.symbol for weight in crypto_runtime.last_target.weights) == ("BTC/USD", "ETH/USD")
    assert tuple(weight.symbol for weight in equity_runtime.last_target.weights) == equity_symbols[:4]
    assert crypto_runtime.reservations == [()]
    assert equity_runtime.reservations == [
        ((Market.CRYPTO, "BTC/USD"), (Market.CRYPTO, "ETH/USD"))
    ]
    assert crypto_runtime.accounts_seen == [account]
    assert equity_runtime.accounts_seen == [account]


def test_restart_recovery_is_crypto_first_with_one_account_and_reservation_set() -> None:
    """Restarted deferred BUY phases share one fresh account and reserve
    crypto targets before equity, regardless of runtime construction order."""
    from parayabuck.runtime import EngineRuntime

    equity_runtime = _RecoveryPortfolioRuntime(Market.EQUITY, ("S00",))
    crypto_runtime = _RecoveryPortfolioRuntime(Market.CRYPTO, ("BTC/USD", "ETH/USD"))
    account = _account()
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._portfolio_lock = threading.RLock()
    runtime._circuit_breaker_reason = None
    runtime._portfolio_runtimes = (equity_runtime, crypto_runtime)

    runtime._resume_startup_rebalances(account=account)

    assert [item.market for item in (crypto_runtime, equity_runtime) if item.calls] == [
        Market.CRYPTO,
        Market.EQUITY,
    ]
    assert crypto_runtime.calls == [(account, ())]
    assert equity_runtime.calls == [
        (
            account,
            ((Market.CRYPTO, "BTC/USD"), (Market.CRYPTO, "ETH/USD")),
        )
    ]


def test_engine_does_not_reserve_unordered_positive_target_slots() -> None:
    """A positive target with no executable BUY must not starve the other sleeve."""
    from parayabuck.runtime import EngineRuntime

    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    equity_symbols = tuple(f"S{index:02d}" for index in range(6))
    equity_runtime = _CoordinatedPortfolioRuntime(
        Market.EQUITY,
        _ranked_target(
            "equity_test",
            timestamp,
            tuple((symbol, 0.10) for symbol in equity_symbols),
        ),
        equity_symbols,
        0.60,
    )
    crypto_runtime = _CoordinatedPortfolioRuntime(
        Market.CRYPTO,
        _target("crypto_test", timestamp, (("BTC/USD", 0.05), ("ETH/USD", 0.05))),
        ("BTC/USD", "ETH/USD"),
        0.10,
        emit_orders=False,
    )
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._portfolio_lock = threading.RLock()
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._portfolio_runtimes = (equity_runtime, crypto_runtime)
    runtime._entry_readiness_reasons = lambda _market: ()
    runtime._equity_signal_basis_latch = lambda _timestamp: None
    runtime._last_portfolio_status = {}
    account = _account()
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime._fresh_account_snapshot = lambda: account

    runtime._process_portfolio_timestamp(
        [
            Bar(timestamp, "S00", "1d", 100.0, 100.0, 100.0, 100.0, 1_000.0, Market.EQUITY),
            Bar(timestamp, "BTC/USD", "1d", 100.0, 100.0, 100.0, 100.0, 1_000.0, Market.CRYPTO),
        ]
    )

    assert tuple(weight.symbol for weight in equity_runtime.last_target.weights) == equity_symbols
    assert equity_runtime.reservations == [()]


def test_planner_orders_sells_before_buys_across_markets() -> None:
    planner = TargetToOrderPlanner(PortfolioExecutionPolicy())
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    account = _account(equity_positions=(("AAPL", 100.0, 100.0),))
    target = _target(
        "equity_residual_momentum_v1",
        timestamp,
        (("AAPL", 0.05), ("MSFT", 0.05)),  # sell AAPL down, buy MSFT
    )
    rules = {
        "AAPL": AssetTradingRules("AAPL", Market.EQUITY, 0.01, 0.01, 0.01, 1.0),
        "MSFT": AssetTradingRules("MSFT", Market.EQUITY, 0.01, 0.01, 0.01, 1.0),
    }
    orders = planner.plan(
        target,
        account,
        market=Market.EQUITY,
        prices={"AAPL": 100.0, "MSFT": 100.0},
        rules=rules,
        approved_symbols=("AAPL", "MSFT"),
    )
    assert orders
    from parayabuck.execution.portfolio import PortfolioOrderSide

    sell_indexes = [index for index, order in enumerate(orders) if order.side == PortfolioOrderSide.SELL]
    buy_indexes = [index for index, order in enumerate(orders) if order.side == PortfolioOrderSide.BUY]
    assert sell_indexes and buy_indexes
    assert max(sell_indexes) < min(buy_indexes)  # risk-reducing sells first


def test_planner_ignores_cross_market_open_orders() -> None:
    """Open orders from the OTHER market never crash or suppress this
    market's plan (regression: a working equity IWM order canonicalized as
    crypto raised ``Cannot infer crypto base/quote`` in the crypto planner
    and aborted the crypto startup catch-up)."""
    planner = TargetToOrderPlanner(PortfolioExecutionPolicy())
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)

    crypto_target = _target(
        "crypto_low_variance_v1",
        timestamp,
        (("BTC/USD", 0.05),),
    )
    crypto_orders = planner.plan(
        crypto_target,
        _account(),
        market=Market.CRYPTO,
        prices={"BTC/USD": 50_000.0},
        rules={"BTC/USD": AssetTradingRules("BTC/USD", Market.CRYPTO, 1e-8, 1e-8, 1e-8, 1.0)},
        approved_symbols=("BTC/USD",),
        open_order_symbols={"IWM", "C", "JNJ"},  # working equity orders only
    )
    assert [order.symbol for order in crypto_orders] == ["BTC/USD"]

    # A working BTC/USD order suppresses only the crypto order, and a
    # working crypto order never leaks into the equity plan.
    suppressed = planner.plan(
        crypto_target,
        _account(),
        market=Market.CRYPTO,
        prices={"BTC/USD": 50_000.0},
        rules={"BTC/USD": AssetTradingRules("BTC/USD", Market.CRYPTO, 1e-8, 1e-8, 1e-8, 1.0)},
        approved_symbols=("BTC/USD",),
        open_order_symbols={"BTC/USD"},
    )
    assert suppressed == ()

    equity_target = _target(
        "equity_residual_momentum_v1",
        timestamp,
        (("IWM", 0.05),),
    )
    equity_orders = planner.plan(
        equity_target,
        _account(),
        market=Market.EQUITY,
        prices={"IWM": 200.0},
        rules={"IWM": AssetTradingRules("IWM", Market.EQUITY, 0.01, 0.01, 0.01, 1.0)},
        approved_symbols=("IWM",),
        open_order_symbols={"BTC/USD", "ETH/USD"},  # working crypto orders only
    )
    assert [order.symbol for order in equity_orders] == ["IWM"]


class _SharedBroker:
    """One account shared by both market runtimes."""

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


def _daily_bars(symbols: tuple[str, ...], days: int = 320, market: Market = Market.EQUITY) -> list[Bar]:
    import math

    start = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            close = 100 * (1.001 + offset * 0.0001) ** index + 2.0 * math.sin(index * 0.8 + offset)
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close,
                    close,
                    close,
                    close,
                    1_000_000.0,
                    market,
                )
            )
    return bars


def test_shared_repository_restart_recovers_both_markets(tmp_path) -> None:
    """Both markets persist decisions into ONE repository; a restart resumes
    each market's decision boundary without duplicate rebalances."""
    repository = PortfolioStateRepository(tmp_path / "shared.sqlite3")
    broker = _SharedBroker()
    equity = portfolio_strategy_from("equity_residual_momentum_v1", {"top_n": 2, "exclude_symbols": ["SPY"]})
    crypto = portfolio_strategy_from(
        "crypto_low_variance_v1",
        {"top_n": 2, "rebalance_hours": 168},
    )
    equity_runtime = PortfolioRuntime(
        equity,
        ("AAPL", "MSFT", "NVDA", "SPY"),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        risk_limits=_limits(),
        allow_static_universe=True,
    )
    crypto_runtime = PortfolioRuntime(
        crypto,
        ("BTC/USD", "ETH/USD"),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.10,
        risk_limits=_limits(),
        account_id="shared-account",
        allow_static_universe=True,
    )
    equity_result = equity_runtime.process_closed_bars(_daily_bars(("AAPL", "MSFT", "NVDA", "SPY")))
    assert equity_result.status == "submitted"
    crypto_result = crypto_runtime.process_closed_bars(_daily_bars(("BTC/USD", "ETH/USD"), market=Market.CRYPTO))
    assert crypto_result.status == "submitted"
    equity_runtime.close()
    crypto_runtime.close()

    with sqlite3.connect(tmp_path / "shared.sqlite3") as connection:
        markets = {str(row[0]) for row in connection.execute("SELECT DISTINCT market FROM portfolio_decisions")}
        sessions = connection.execute("SELECT COUNT(*) FROM portfolio_runtime_sessions").fetchone()[0]
    assert markets == {"equity", "crypto"}
    assert sessions >= 2

    # Restart: fresh runtimes on the same repository resume each market's
    # last decision boundary (no duplicate decision on the same bars).
    broker2 = _SharedBroker()
    equity_restart = PortfolioRuntime(
        equity,
        ("AAPL", "MSFT", "NVDA", "SPY"),
        broker2,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        risk_limits=_limits(),
        allow_static_universe=True,
    )
    result = equity_restart.process_closed_bars(_daily_bars(("AAPL", "MSFT", "NVDA", "SPY")))
    assert result.status in {"duplicate_decision", "not_scheduled"}
    equity_restart.close()
