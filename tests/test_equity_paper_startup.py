"""Equity PAPER startup contract (offline, paper-chaos gate).

Verifies the startup contract WITHOUT any network or real broker:
runtime.mode=paper, equity enabled, LIVE disabled, static universe ->
warmup -> startup catch-up -> strategy runtime -> 24h rebalance cadence ->
account reconciliation -> ready state. Restart must never re-run the
catch-up. No real PAPER orders are ever placed: the fake broker only records
submitted orders.

The strategy parameters below are the validated PRODUCTION specs (they mirror
the shipped config block); they are asserted, never changed.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.config import RuntimeMode, load_config
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
)
from parayabuck.runtime_universe import ActiveProductUniverse

pytestmark = pytest.mark.paper_chaos

ROOT = Path(__file__).resolve().parents[1]

PRODUCTION_EQUITY = {
    "signal_symbol": "QQQ",
    "hold_symbol": "QLD",
    "sma_bars": 200,
    "vol_target": 0.35,
    "vol_window": 20,
    "rebalance_frequency": "hours:24",
}

EQUITY_SYMBOLS = ("QQQ", "QLD")

_SUBMITTED_STATUSES = {"submitted", "sell_phase_submitted", "buy_phase_submitted"}


class _PaperBroker:
    """Offline paper broker: records submitted orders, reports a flat account."""

    def __init__(self) -> None:
        self.submitted: list[PortfolioOrderRequest] = []
        self.positions: tuple[PositionSnapshot, ...] = ()

    def account(self) -> AccountSnapshot:
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(
            cash=100_000.0 - value,
            equity=100_000.0,
            peak_equity=100_000.0,
            daily_pnl=0.0,
            positions=self.positions,
        )

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(symbol, market, 0.001, 0.001, 0.001, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


def _bars(symbols: tuple[str, ...], days: int, *, start: datetime | None = None) -> list[Bar]:
    """Deterministic daily bars with distinct per-symbol drift and noise.

    Every symbol trends up, so the equity trend filter (QQQ close above its
    200-session SMA) is deterministically in an uptrend.
    """
    start = start or datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            drift = 1.0015 + offset * 0.00003
            noise_amp = 0.004 + offset * 0.0005
            close = 100.0 * (drift**index) * (1.0 + noise_amp * math.sin(index * 0.9 + offset * 1.7))
            bars.append(Bar(timestamp, symbol, "1d", close, close * 1.001, close * 0.999, close, 1_000_000.0))
    return bars


def _assert_frozen_config():
    """The validated strategy/risk specs are FROZEN: assert, never change."""
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    assert config.runtime.mode == RuntimeMode.PAPER
    assert config.universe.equity.enabled
    assert config.strategy.equity.enabled
    assert config.live.enabled is False
    assert config.strategy.equity.name == "equity_leveraged_trend_v1"
    # 2026-09-29 (paper_config_v2): equity 0.98 == gross cap
    assert config.strategy.equity.maximum_allocation == 0.98
    assert config.strategy.equity.parameters == PRODUCTION_EQUITY
    risk = config.risk
    assert risk.max_gross_exposure_pct == 0.98
    assert risk.cash_buffer_pct == 0.02
    return config


def _equity_runtime(
    repository: PortfolioStateRepository,
    broker: _PaperBroker,
    config,
) -> PortfolioRuntime:
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", PRODUCTION_EQUITY)
    assert strategy.timeframe == "1d"
    assert rebalance_policy_for(strategy).hours == 24  # one decision per session
    return PortfolioRuntime(
        strategy,
        EQUITY_SYMBOLS,
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        account_id="paper-account",
        universe=ActiveProductUniverse.configured(Market.EQUITY, EQUITY_SYMBOLS),
        universe_version="paper-equity-policy",
    )


def test_config_contract_paper_equity_live_disabled() -> None:
    _assert_frozen_config()


def test_equity_startup_contract_offline(tmp_path: Path) -> None:
    """Static universe -> warmup -> runtime -> reconciliation -> ready."""
    config = _assert_frozen_config()
    assert config.universe.equity.symbols, "the static equity universe must yield symbols"

    repository = PortfolioStateRepository(tmp_path / "paper.sqlite3")
    broker = _PaperBroker()
    equity_runtime = _equity_runtime(repository, broker, config)
    assert equity_runtime.universe is not None and not equity_runtime.allow_static_universe

    # Warmup: seed 300 daily bars into the strategy history.
    equity_bars = _bars(EQUITY_SYMBOLS, 360)
    by_day = {
        timestamp: [bar for bar in equity_bars if bar.timestamp == timestamp]
        for timestamp in sorted({bar.timestamp for bar in equity_bars})
    }
    days = sorted(by_day)
    equity_runtime.seed([bar for day in days[:300] for bar in by_day[day]])
    assert all(
        len(series) >= equity_runtime.strategy.minimum_history_bars for series in equity_runtime.history.values()
    )

    # STARTUP CATCH-UP: a fresh, flat account decides immediately on the
    # latest closed warmup bars (backtest/PAPER parity), tagged
    # decision_origin=startup_catch_up. This is the engine's
    # _run_startup_catch_up path, exercised at the runtime level.
    catch_up = equity_runtime.startup_catch_up()
    assert catch_up.status == "submitted"
    assert equity_runtime.startup_catch_up_status == "submitted"
    assert catch_up.target is not None
    assert catch_up.target.metadata.get("decision_origin") == "startup_catch_up"
    assert sum(w.weight for w in catch_up.target.weights) > 0

    # Account reconciliation: a fresh broker snapshot before any decision.
    account = broker.account()
    assert account.cash == 100_000.0 and account.equity == 100_000.0

    # Ready: stream the remaining bars day by day (as the decision loop
    # would) until the strategy hits its rebalance boundaries.
    decisions: list[datetime] = []
    for day in days[300:]:
        result = equity_runtime.process_closed_bars(by_day[day])
        if result.status in _SUBMITTED_STATUSES:
            decisions.append(day)
    assert broker.submitted, "the catch-up must submit an order"
    # Cadence contract: equity rebalances on 24h boundaries (every session).
    for previous, current in zip(decisions, decisions[1:], strict=False):
        assert int(current.timestamp()) // (24 * 3600) - int(previous.timestamp()) // (24 * 3600) >= 1

    connection = repository.store.connection
    row = connection.execute(
        "SELECT target_json FROM portfolio_decisions WHERE market='equity' ORDER BY decision_timestamp ASC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert TargetPortfolio.from_dict(json.loads(str(row[0]))).metadata.get("decision_origin") == "startup_catch_up"

    # Frozen risk caps hold on the latest decision.
    row = connection.execute(
        "SELECT target_json FROM portfolio_decisions WHERE market='equity' ORDER BY decision_timestamp DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    target = TargetPortfolio.from_dict(json.loads(str(row[0])))
    assert sum(w.weight for w in target.weights) <= 0.50 + 1e-9
    assert len(target.weights) <= config.risk.max_positions
    markets = {str(row[0]) for row in connection.execute("SELECT DISTINCT market FROM portfolio_decisions")}
    assert markets == {"equity"}
    decision_count = connection.execute("SELECT COUNT(*) FROM portfolio_decisions").fetchone()[0]
    equity_runtime.close()

    # RESTART: a fresh runtime on the SAME repository must never re-run the
    # catch-up: any prior decision for the digest supersedes it.
    restart = _equity_runtime(repository, _PaperBroker(), config)
    restart.seed([bar for day in days for bar in by_day[day]])
    status = restart.startup_catch_up().status
    assert status in {"catch_up_superseded", "duplicate_decision"}
    assert restart.startup_catch_up_status == status
    restart.close()
    assert connection.execute("SELECT COUNT(*) FROM portfolio_decisions").fetchone()[0] == decision_count
