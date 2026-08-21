"""Dual-market PAPER startup contract (offline, paper-chaos gate).

Verifies the Phase-4 startup contract WITHOUT any network or real broker:
runtime.mode=paper, equity+crypto enabled, LIVE disabled, universe
preflight -> warmup -> startup catch-up -> strategy runtimes -> different
rebalance cadences (equity 1d weekly vs crypto 1d 96h) -> account
reconciliation -> ready state, with both strategies deciding simultaneously
against ONE repository and ONE fake broker. Restart must never re-run the
catch-up. No real PAPER orders are ever placed: the fake broker only records
submitted orders.

The strategy parameters below are the validated PRODUCTION v2 specs (same
alpha as the frozen v1 baselines, new cadence); they are asserted, never
changed.
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
from parayabuck.universe.crypto_scanner import load_active_crypto_symbols
from parayabuck.universe.scanner import load_active_symbols

pytestmark = pytest.mark.paper_chaos

ROOT = Path(__file__).resolve().parents[1]

PRODUCTION_EQUITY = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}
PRODUCTION_CRYPTO = {
    "variance_lookback_days": 22,
    "top_n": 2,
    "rebalance_hours": 96,
}

EQUITY_SYMBOLS = ("SPY", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
CRYPTO_SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD")

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


def _bars(
    symbols: tuple[str, ...],
    days: int,
    market: Market,
    *,
    start: datetime | None = None,
) -> list[Bar]:
    """Deterministic daily bars with distinct per-symbol drift and noise.

    Higher-index symbols grow faster and noisier, so the residual-momentum
    ranking is deterministic and produces positive scores for the trailing
    symbols while SPY (low drift) stays a pure benchmark.
    """
    start = start or datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            drift = 1.0015 + offset * 0.00003
            noise_amp = 0.004 + offset * 0.0005
            close = (
                100.0 * (drift**index) * (1.0 + noise_amp * math.sin(index * 0.9 + offset * 1.7))
            )
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


def _assert_frozen_config():
    """The validated strategy/risk specs are FROZEN: assert, never change."""
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    assert config.runtime.mode == RuntimeMode.PAPER
    assert config.universe.equity.enabled and config.universe.crypto.enabled
    assert config.strategy.equity.enabled and config.strategy.crypto.enabled
    assert config.live.enabled is False
    assert config.strategy.equity.name == "equity_residual_momentum_v2"
    assert config.strategy.crypto.name == "crypto_low_variance_v2"
    # 2026-08-21 operator decisions: docs/optimization_experiments_2026_08.md
    assert config.strategy.equity.maximum_allocation == 0.60
    assert config.strategy.crypto.maximum_allocation == 0.05
    risk = config.risk
    assert risk.max_crypto_exposure_pct == 0.10
    assert risk.max_crypto_position_pct == 0.05
    assert risk.max_crypto_positions == 2
    assert risk.max_gross_exposure_pct == 0.60
    assert risk.cash_buffer_pct == 0.40
    return config


def _equity_runtime(
    repository: PortfolioStateRepository,
    broker: _PaperBroker,
    config,
) -> PortfolioRuntime:
    strategy = portfolio_strategy_from("equity_residual_momentum_v2", PRODUCTION_EQUITY)
    assert strategy.timeframe == "1d"
    assert rebalance_policy_for(strategy).frequency == "weekly"  # 1d weekly cadence
    return PortfolioRuntime(
        strategy,
        EQUITY_SYMBOLS,
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        account_id="paper-account",
    )


def _crypto_runtime(
    repository: PortfolioStateRepository,
    broker: _PaperBroker,
    config,
) -> PortfolioRuntime:
    strategy = portfolio_strategy_from("crypto_low_variance_v2", PRODUCTION_CRYPTO)
    assert strategy.timeframe == "1d"
    assert rebalance_policy_for(strategy).hours == 96  # 96h cadence
    return PortfolioRuntime(
        strategy,
        CRYPTO_SYMBOLS,
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.10,
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        account_id="paper-account",
    )


def test_config_contract_paper_dual_market_live_disabled() -> None:
    _assert_frozen_config()


def test_dual_market_startup_contract_offline(tmp_path: Path) -> None:
    """Universe preflight -> warmup -> runtimes -> reconciliation -> ready."""
    config = _assert_frozen_config()
    transitions: list[str] = []

    # Stage 1: universe preflight (offline fallback loaders; no network).
    equity_universe = load_active_symbols(config.universe.equity)
    crypto_universe = load_active_crypto_symbols(config.universe.crypto)
    assert equity_universe, "equity universe preflight must yield symbols"
    assert crypto_universe, "crypto universe preflight must yield symbols"
    transitions.append(
        f"universe_preflight equity={len(equity_universe)} crypto={len(crypto_universe)}"
    )

    repository = PortfolioStateRepository(tmp_path / "dual.sqlite3")
    broker = _PaperBroker()
    equity_runtime = _equity_runtime(repository, broker, config)
    crypto_runtime = _crypto_runtime(repository, broker, config)

    # Stage 2: warmup — seed 300 daily bars into both strategy histories.
    total_days = 360
    equity_bars = _bars(EQUITY_SYMBOLS, total_days, Market.EQUITY)
    crypto_bars = _bars(CRYPTO_SYMBOLS, total_days, Market.CRYPTO)
    equity_by_day = {
        timestamp: [bar for bar in equity_bars if bar.timestamp == timestamp]
        for timestamp in sorted({bar.timestamp for bar in equity_bars})
    }
    crypto_by_day = {
        timestamp: [bar for bar in crypto_bars if bar.timestamp == timestamp]
        for timestamp in sorted({bar.timestamp for bar in crypto_bars})
    }
    equity_days = sorted(equity_by_day)
    crypto_days = sorted(crypto_by_day)
    equity_runtime.seed([bar for day in equity_days[:300] for bar in equity_by_day[day]])
    crypto_runtime.seed([bar for day in crypto_days[:300] for bar in crypto_by_day[day]])
    assert all(
        len(series) >= equity_runtime.strategy.minimum_history_bars
        for series in equity_runtime.history.values()
    )
    assert all(
        len(series) >= crypto_runtime.strategy.minimum_history_bars
        for series in crypto_runtime.history.values()
    )
    transitions.append("warmup equity_ready crypto_ready")

    # Stage 3: STARTUP CATCH-UP — a fresh, flat account decides immediately on
    # the latest closed warmup bars (backtest/PAPER parity), once per market,
    # tagged decision_origin=startup_catch_up. This is the engine's
    # _run_startup_catch_up path, exercised at the runtime level.
    equity_catch_up = equity_runtime.startup_catch_up()
    assert equity_catch_up.status == "submitted"
    assert equity_runtime.startup_catch_up_status == "submitted"
    assert equity_catch_up.target is not None
    assert equity_catch_up.target.metadata.get("decision_origin") == "startup_catch_up"
    assert equity_catch_up.target.invested_weight > 0

    crypto_catch_up = crypto_runtime.startup_catch_up()
    assert crypto_catch_up.status == "submitted"
    assert crypto_runtime.startup_catch_up_status == "submitted"
    assert crypto_catch_up.target is not None
    assert crypto_catch_up.target.metadata.get("decision_origin") == "startup_catch_up"
    assert crypto_catch_up.target.invested_weight > 0
    transitions.append(
        f"startup_catch_up equity=submitted crypto=submitted orders={len(broker.submitted)}"
    )

    # Stage 4: account reconciliation — a fresh broker snapshot is taken
    # before any decision, exactly like the runtime does between phases.
    account = broker.account()
    assert account.cash == 100_000.0 and account.equity == 100_000.0
    transitions.append(f"account_reconciliation cash={account.cash:.0f}")

    # Stage 5: ready — stream the remaining bars day by day (as the decision
    # loop would) until each market hits its own rebalance boundary.
    equity_decisions: list[datetime] = []
    crypto_decisions: list[datetime] = []
    for day in equity_days[300:]:
        equity_batch = equity_by_day[day]
        crypto_batch = crypto_by_day[day]
        equity_result = equity_runtime.process_closed_bars(equity_batch)
        if equity_result.status in _SUBMITTED_STATUSES:
            equity_decisions.append(day)
        crypto_result = crypto_runtime.process_closed_bars(crypto_batch)
        if crypto_result.status in _SUBMITTED_STATUSES:
            crypto_decisions.append(day)

    assert equity_decisions, "equity strategy must reach a decision"
    assert crypto_decisions, "crypto strategy must reach a decision"
    assert len(broker.submitted) >= 4  # catch-up (2 markets) + first cadence rebalances

    # Cadence contract: equity rebalances only on ISO-week starts (weekly).
    assert all(
        decision.isocalendar()[:2] != (decision - timedelta(days=1)).isocalendar()[:2]
        for decision in equity_decisions
    )
    # Cadence contract: crypto rebalances on 96h boundaries.
    for previous, current in zip(crypto_decisions, crypto_decisions[1:], strict=False):
        epoch_delta = int(current.timestamp()) // (96 * 3600) - int(previous.timestamp()) // (
            96 * 3600
        )
        assert epoch_delta >= 1

    # Persisted catch-up decisions carry the startup_catch_up origin.
    connection = repository.store.connection
    for market in ("equity", "crypto"):
        row = connection.execute(
            "SELECT target_json FROM portfolio_decisions WHERE market=? "
            "ORDER BY decision_timestamp ASC LIMIT 1",
            (market,),
        ).fetchone()
        assert row is not None
        first = TargetPortfolio.from_dict(json.loads(str(row[0])))
        assert first.metadata.get("decision_origin") == "startup_catch_up"

    # Frozen risk caps hold on the submitted decisions.
    equity_invested = crypto_invested = 0.0
    for market, runtime in ((Market.EQUITY, equity_runtime), (Market.CRYPTO, crypto_runtime)):
        row = connection.execute(
            "SELECT target_json FROM portfolio_decisions WHERE market=? "
            "ORDER BY decision_timestamp DESC LIMIT 1",
            (market.value,),
        ).fetchone()
        assert row is not None
        target = TargetPortfolio.from_dict(json.loads(str(row[0])))
        if market == Market.EQUITY:
            equity_invested = target.invested_weight
            assert equity_invested <= 0.50 + 1e-9
            assert len(target.weights) <= 6  # max_positions
        else:
            crypto_invested = target.invested_weight
            assert crypto_invested <= 0.10 + 1e-9
            assert len(target.weights) <= 2  # max_crypto_positions
            assert all(weight.weight <= 0.05 + 1e-9 for weight in target.weights)
    assert equity_invested + crypto_invested <= 0.60 + 1e-9

    # The persisted repository is the single authoritative record: both
    # markets have decisions in ONE database (extended, not duplicated).
    markets = {
        str(row[0]) for row in connection.execute("SELECT DISTINCT market FROM portfolio_decisions")
    }
    assert markets == {"equity", "crypto"}
    equity_decision_count = connection.execute(
        "SELECT COUNT(*) FROM portfolio_decisions WHERE market='equity'"
    ).fetchone()[0]
    crypto_decision_count = connection.execute(
        "SELECT COUNT(*) FROM portfolio_decisions WHERE market='crypto'"
    ).fetchone()[0]

    equity_runtime.close()
    crypto_runtime.close()

    # Stage 6: RESTART — a fresh runtime on the SAME repository must never
    # re-run the catch-up: any prior decision for the digest supersedes it.
    equity_restart = _equity_runtime(repository, _PaperBroker(), config)
    crypto_restart = _crypto_runtime(repository, _PaperBroker(), config)
    equity_restart.seed([bar for day in equity_days for bar in equity_by_day[day]])
    crypto_restart.seed([bar for day in crypto_days for bar in crypto_by_day[day]])
    assert equity_restart.startup_catch_up().status == "catch_up_superseded"
    assert equity_restart.startup_catch_up_status == "catch_up_superseded"
    assert crypto_restart.startup_catch_up().status == "catch_up_superseded"
    assert crypto_restart.startup_catch_up_status == "catch_up_superseded"
    equity_restart.close()
    crypto_restart.close()
    # No duplicate decision rows after the restart attempt.
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM portfolio_decisions WHERE market='equity'"
        ).fetchone()[0]
        == equity_decision_count
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM portfolio_decisions WHERE market='crypto'"
        ).fetchone()[0]
        == crypto_decision_count
    )
    transitions.append("restart no_duplicate_catch_up")

    transitions.append(
        f"ready equity_decisions={len(equity_decisions)} "
        f"crypto_decisions={len(crypto_decisions)} orders={len(broker.submitted)}"
    )
    transitions.append("state=ready both_markets_deciding")
    # Observed transitions recorded for the report (all asserted above).
    assert transitions[0].startswith("universe_preflight")
    assert transitions[1] == "warmup equity_ready crypto_ready"
    assert transitions[2].startswith("startup_catch_up")
    assert transitions[3].startswith("account_reconciliation")
    assert transitions[-1] == "state=ready both_markets_deciding"
