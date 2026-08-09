from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
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
