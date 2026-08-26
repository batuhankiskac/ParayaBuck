"""Multi-market portfolio contract tests.

Equity and crypto strategies produce target portfolios independently; the
portfolio/risk layer then combines their account-level impact on ONE
authoritative account: gross exposure, cash buffer, per-market sleeve caps,
per-asset position limits and SELL->BUY ordering all hold across markets.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
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
