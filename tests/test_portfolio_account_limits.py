"""Account-level portfolio contract tests.

The equity strategy produces a target portfolio; the portfolio/risk layer
then sizes it against ONE authoritative brokerage account: gross exposure,
cash buffer, sleeve cap, per-asset position limits, account-wide position
slots (including foreign positions the bot does not trade) and SELL->BUY
ordering all hold.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

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
        maximum_equity_asset=0.10,
        minimum_cash=0.40,
        maximum_positions=6,
    )


def _account(
    *,
    equity_positions: tuple[tuple[str, float, float], ...] = (),
    foreign_crypto_positions: tuple[tuple[str, float, float], ...] = (),
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
        # Foreign crypto held in the shared brokerage account: classified by
        # market, never owned or traded by the equity-only bot.
        PositionSnapshot(
            symbol=symbol,
            qty=qty,
            avg_price=price,
            current_price=price,
            market=Market.CRYPTO,
            qty_available=qty,
        )
        for symbol, qty, price in foreign_crypto_positions
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


def test_equity_target_stays_within_account_limits() -> None:
    """The equity sleeve is sized against the shared account and stays inside
    gross, cash, sleeve and position limits."""
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    overlay = PortfolioRiskOverlay(_limits())
    account = _account(equity_positions=(("AAPL", 100.0, 100.0),))
    approved_equity = ("AAPL", "MSFT", "NVDA")

    equity_target = _target(
        "equity_leveraged_trend_v1",
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
    assert equity_decision.gross_after <= 0.60 + 1e-9
    assert 1.0 - equity_decision.gross_after >= 0.40 - 1e-9


def test_equity_sleeve_respects_allocation_and_position_limits() -> None:
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    overlay = PortfolioRiskOverlay(_limits())
    account = _account()
    # 8 symbols would like 8 positions at 10% each: position count and the
    # equity sleeve cap (50%) must both bind.
    weights = tuple((f"S{i:02d}", 0.10) for i in range(8))
    target = _target("equity_leveraged_trend_v1", timestamp, weights)
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
            TargetWeight(symbol, weight, "test", rank=index) for index, (symbol, weight) in enumerate(weights, start=1)
        ),
        cash_weight=max(0.0, 1.0 - sum(weight for _symbol, weight in weights)),
        universe_version="test",
        parameters=(),
    )


def test_global_position_limit_counts_foreign_crypto_slots_and_keeps_equity_ranks() -> None:
    """The six-account-position cap includes foreign crypto positions held in
    the shared account; they also count as immutable gross."""
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    overlay = PortfolioRiskOverlay(_limits())
    account = _account(
        foreign_crypto_positions=(
            ("BTC/USD", 25.0, 100.0),
            ("ETH/USD", 25.0, 100.0),
        )
    )
    symbols = tuple((f"S{index:02d}", 0.10) for index in range(8))
    decision = overlay.apply(
        _ranked_target("equity_leveraged_trend_v1", timestamp, symbols),
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


def test_reserved_position_keys_consume_global_position_slots() -> None:
    """Working reservations occupy account-level slots before new targets."""
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    overlay = PortfolioRiskOverlay(_limits())
    account = _account()
    reserved = tuple((Market.EQUITY, f"R{index:02d}") for index in range(6))
    decision = overlay.apply(
        _target("equity_leveraged_trend_v1", timestamp, (("S00", 0.05), ("S01", 0.05))),
        account,
        market=Market.EQUITY,
        approved_symbols=("S00", "S01"),
        maximum_allocation=0.50,
        mode="paper",
        reserved_position_keys=reserved,
    )

    assert decision.target.weights == ()
    assert "position_count_limit" in decision.reasons


def test_planner_orders_sells_before_buys() -> None:
    planner = TargetToOrderPlanner(PortfolioExecutionPolicy())
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    account = _account(equity_positions=(("AAPL", 100.0, 100.0),))
    target = _target(
        "equity_leveraged_trend_v1",
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

    sell_indexes = [index for index, order in enumerate(orders) if order.side == PortfolioOrderSide.SELL]
    buy_indexes = [index for index, order in enumerate(orders) if order.side == PortfolioOrderSide.BUY]
    assert sell_indexes and buy_indexes
    assert max(sell_indexes) < min(buy_indexes)  # risk-reducing sells first


def test_planner_ignores_foreign_crypto_open_orders() -> None:
    """Foreign crypto orders working in the shared account never crash or
    suppress the equity plan."""
    planner = TargetToOrderPlanner(PortfolioExecutionPolicy())
    timestamp = datetime(2025, 1, 15, 4, 0, tzinfo=UTC)
    equity_target = _target(
        "equity_leveraged_trend_v1",
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
        open_order_symbols={"BTC/USD", "ETH/USD"},  # working foreign crypto orders only
    )
    assert [order.symbol for order in equity_orders] == ["IWM"]


class _Broker:
    """One synthetic brokerage account."""

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


def _daily_bars(symbols: tuple[str, ...], days: int = 320) -> list[Bar]:
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
                    Market.EQUITY,
                )
            )
    return bars


def test_repository_restart_resumes_equity_decision_boundary(tmp_path) -> None:
    """Decisions persist into the repository; a restart resumes the equity
    decision boundary without a duplicate rebalance."""
    repository = PortfolioStateRepository(tmp_path / "shared.sqlite3")
    broker = _Broker()
    equity = portfolio_strategy_from(
        "equity_leveraged_trend_v1", {"signal_symbol": "QQQ", "hold_symbol": "QLD", "sma_bars": 5}
    )
    equity_runtime = PortfolioRuntime(
        equity,
        ("QQQ", "QLD"),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        risk_limits=_limits(),
        allow_static_universe=True,
    )
    equity_result = equity_runtime.process_closed_bars(_daily_bars(("QQQ", "QLD")))
    assert equity_result.status == "submitted"
    equity_runtime.close()

    with sqlite3.connect(tmp_path / "shared.sqlite3") as connection:
        markets = {str(row[0]) for row in connection.execute("SELECT DISTINCT market FROM portfolio_decisions")}
        sessions = connection.execute("SELECT COUNT(*) FROM portfolio_runtime_sessions").fetchone()[0]
    assert markets == {"equity"}
    assert sessions >= 1

    # Restart: a fresh runtime on the same repository resumes the last
    # decision boundary (no duplicate decision on the same bars).
    broker2 = _Broker()
    equity_restart = PortfolioRuntime(
        equity,
        ("QQQ", "QLD"),
        broker2,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        risk_limits=_limits(),
        allow_static_universe=True,
    )
    result = equity_restart.process_closed_bars(_daily_bars(("QQQ", "QLD")))
    assert result.status in {"duplicate_decision", "not_scheduled"}
    equity_restart.close()
