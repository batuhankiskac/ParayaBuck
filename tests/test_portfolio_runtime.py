from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest
from parayabuck.portfolio.approval import (
    ApprovalError,
    ApprovalStage,
    StrategyApprovalManifest,
    module_sha256,
)
from parayabuck.portfolio.equity_covariance_trend import (
    EQUITY_ALL_WEATHER_UNIVERSE,
    EquityCovarianceTrend1dV1,
)
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
    RolloutController,
    RolloutEvidence,
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


def _manifest(stage: ApprovalStage) -> StrategyApprovalManifest:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return StrategyApprovalManifest(
        strategy_name=EquityCovarianceTrend1dV1.name,
        strategy_version=EquityCovarianceTrend1dV1.version,
        import_path=("parayabuck.portfolio.equity_covariance_trend:EquityCovarianceTrend1dV1"),
        module_sha256=module_sha256(EquityCovarianceTrend1dV1),
        parameters={},
        market=Market.EQUITY,
        symbols=EQUITY_ALL_WEATHER_UNIVERSE,
        maximum_allocation=0.50,
        gate_policy_version="portfolio-conservative-v1",
        dataset_ids=("dataset",),
        dataset_hashes=("hash",),
        validation_record_ids=("validation",),
        holdout_record_ids=("holdout",) if stage != ApprovalStage.SHADOW else (),
        source_revision="test",
        approved_at=now,
        expires_at=now + timedelta(days=365),
        approved_stage=stage,
    ).signed()


def _bars() -> list[Bar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(220):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(EQUITY_ALL_WEATHER_UNIVERSE):
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


def test_shadow_runtime_records_orders_without_broker_writes(tmp_path) -> None:
    broker = _Broker()
    database = tmp_path / "runtime.sqlite3"
    repository = PortfolioStateRepository(database)
    runtime = PortfolioRuntime(_manifest(ApprovalStage.SHADOW), broker, repository)
    result = runtime.process_closed_bars(_bars())
    assert result.status == "shadow_planned"
    assert result.orders
    assert broker.submitted == []
    # Replaying the same batch cannot produce another decision/order set.
    runtime.process_closed_bars(_bars())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM portfolio_decisions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM portfolio_orders").fetchone()[0] == len(
            result.orders
        )


def test_paper_runtime_submits_sells_before_buys_through_same_planner(tmp_path) -> None:
    broker = _Broker()
    runtime = PortfolioRuntime(
        _manifest(ApprovalStage.PAPER),
        broker,
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
    )
    result = runtime.process_closed_bars(_bars())
    assert result.status == "submitted"
    assert len(broker.submitted) == len(result.orders) > 0
    assert result.submitted_order_ids


def test_live_manifest_stays_disabled_without_manual_cap(tmp_path) -> None:
    runtime = PortfolioRuntime(
        _manifest(ApprovalStage.LIVE),
        _Broker(),
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
    )
    with pytest.raises(ApprovalError, match="Canary/live execution is disabled"):
        runtime.process_closed_bars(_bars())


def test_canary_manifest_stays_disabled_without_manual_cap(tmp_path) -> None:
    runtime = PortfolioRuntime(
        _manifest(ApprovalStage.CANARY),
        _Broker(),
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
    )
    with pytest.raises(ApprovalError, match="Canary/live execution is disabled"):
        runtime.process_closed_bars(_bars())


def test_rollout_controller_enforces_time_fill_and_incident_evidence(tmp_path) -> None:
    controller = RolloutController(tmp_path / "rollout.json")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    shadow = RolloutEvidence(ApprovalStage.SHADOW, start, {"equity": 0})
    assert not controller.can_advance(shadow, start + timedelta(days=29))[0]
    assert controller.can_advance(shadow, start + timedelta(days=30))[0]
    paper = RolloutEvidence(ApprovalStage.PAPER, start, {"equity": 50, "crypto": 50})
    assert controller.can_advance(paper, start + timedelta(days=90))[0]
    broken = RolloutEvidence(
        ApprovalStage.PAPER,
        start,
        {"equity": 50},
        unresolved_reconciliations=1,
    )
    assert not controller.can_advance(broken, start + timedelta(days=120))[0]
    canary = RolloutEvidence(
        ApprovalStage.CANARY,
        start,
        {"equity": 50},
        operator_live_approval=True,
        live_dollar_cap=1_000,
    )
    controller.save(canary)
    assert controller.load() == canary
    assert controller.can_advance(canary, start + timedelta(days=1))[0]


def test_portfolio_fill_chain_is_idempotent_and_keeps_pnl_fee_slippage_separate(
    tmp_path,
) -> None:
    broker = _Broker()
    database = tmp_path / "runtime.sqlite3"
    repository = PortfolioStateRepository(database)
    runtime = PortfolioRuntime(_manifest(ApprovalStage.SHADOW), broker, repository)
    result = runtime.process_closed_bars(_bars())
    order = result.orders[0]
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
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT fee, realized_pnl, unrealized_pnl, slippage FROM portfolio_fills"
        ).fetchone()
        assert row[:3] == (1.25, 12.0, -3.0)
        assert row[3] != 0
        assert connection.execute("SELECT COUNT(*) FROM portfolio_fills").fetchone()[0] == 1
