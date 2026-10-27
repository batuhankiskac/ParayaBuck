"""Wiring of the drawdown liquidation brake into the portfolio runtime.

The overlay itself is covered by ``test_drawdown_liquidation_2026_10_27.py``.
These tests cover what the runtime adds on top: the persisted RED-state per
account and market, its reload after a restart, the configuration digest and
the configuration fingerprint (both unchanged while the brake is off). Every
fixture is synthetic and deterministic: a fake broker, a SQLite database under
``tmp_path``, no network and no credentials.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from parayabuck.config import DrawdownLiquidationConfig, load_config
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    DRAWDOWN_LIQUIDATION_REASON,
    AssetTradingRules,
    DrawdownLiquidationState,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
)
from parayabuck.live_safety import config_fingerprint
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import configuration_digest
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.portfolio.runtime_service import PortfolioRuntime

ON = DrawdownLiquidationConfig(enabled=True, threshold_pct=0.45, rearm_recovery_pct=0.30, cooldown_sessions=5)
REPO_SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
# Closing bars (21:00 UTC is the 16:00 New York close in January).
DAY1 = datetime(2025, 1, 6, 21, tzinfo=UTC)
DAY2 = datetime(2025, 1, 7, 21, tzinfo=UTC)  # one NYSE session after DAY1
DAY3 = datetime(2025, 1, 21, 21, tzinfo=UTC)  # well past the five-session cooldown
LIMITS = PortfolioRiskLimits(
    daily_loss_limit=0.20,
    drawdown_halt=0.75,
    maximum_gross=0.98,
    maximum_equity=0.98,
    maximum_equity_asset=0.98,
    minimum_cash=0.02,
    maximum_positions=8,
)
SELL = PortfolioOrderSide.SELL
BUY = PortfolioOrderSide.BUY


class _Strategy:
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = targets
        self.parameters = {"targets": dict(targets)}

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(self.parameters.items()))

    def requires_intermediate_risk_rebalance(self, context) -> bool:  # noqa: ANN001
        return True

    def decide(self, context) -> TargetPortfolio:  # noqa: ANN001
        weights = tuple(TargetWeight(symbol, weight) for symbol, weight in self._targets.items())
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(self._targets.values()),
            universe_version="test",
        )


class _Broker:
    """Controlled paper broker whose equity is set by the test (peak fixed at 100k)."""

    account_id = "acct"

    def __init__(self, positions: tuple[PositionSnapshot, ...] = (), *, equity: float = 100_000.0) -> None:
        self.positions = list(positions)
        self.equity_value = equity
        self.open_symbols: set[str] = set()
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(
            cash=self.equity_value - value,
            equity=self.equity_value,
            peak_equity=100_000.0,
            daily_pnl=0.0,
            positions=tuple(self.positions),
        )

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(1.0, 1.0, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set(self.open_symbols)

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        if order.symbol in self.open_symbols:
            raise ValueError(f"Open order already exists for {order.symbol}")
        self.submitted.append(order)
        self.open_symbols.add(order.symbol)
        return "broker-" + order.client_order_id

    def resolve_portfolio_submission(self, client_order_id: str, broker_order_id: str = "") -> str:
        return "canceled"


def _position(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol,
        qty,
        100.0,
        100.0,
        Market.EQUITY,
        qty_available=qty,
        ownership="managed",
        managed_qty=qty,
    )


def _runtime(
    directory: Path,
    broker: _Broker,
    targets: dict[str, float],
    *,
    liquidation: DrawdownLiquidationConfig | None,
) -> tuple[PortfolioRuntime, PortfolioStateRepository]:
    directory.mkdir(parents=True, exist_ok=True)
    repository = PortfolioStateRepository(directory / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(targets),
        tuple(sorted(targets)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        risk_limits=LIMITS,
        account_id="acct",
        drawdown_liquidation=liquidation,
    )
    return runtime, repository


def _bars(day: datetime) -> list[Bar]:
    return [Bar(day, "AAPL", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY)]


def _order_summary(result) -> list[tuple[PortfolioOrderSide, str, float]]:  # noqa: ANN001
    return [(order.side, order.symbol, order.quantity) for order in result.orders]


def _fill_completely(repository: PortfolioStateRepository, order: PortfolioOrderRequest) -> None:
    repository.apply_trade_update(
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


def _state_rows(path: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(str(path)) as connection:
        return connection.execute(
            "SELECT account_id, market, liquidated_at, rearmed FROM portfolio_drawdown_liquidation_states"
        ).fetchall()


def test_breach_sells_every_managed_unit_records_the_reason_and_persists_the_state(tmp_path) -> None:
    broker = _Broker((_position("AAPL", 10),), equity=50_000.0)
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.30}, liquidation=ON)

    result = runtime.process_closed_bars(_bars(DAY1), force_scheduled=True)

    # A sells-only decision has no BUY leg, so it takes the immediate-submit path.
    assert result.status == "submitted"
    assert [(order.side, order.symbol, order.quantity) for order in broker.submitted] == [(SELL, "AAPL", 10.0)]
    assert DRAWDOWN_LIQUIDATION_REASON in result.risk.reasons
    stored = json.loads(repository.decision_row(result.decision_id)["risk_json"])
    assert DRAWDOWN_LIQUIDATION_REASON in stored["reasons"]
    assert stored["drawdown_liquidation_state"] == {"liquidated_at": "2025-01-06", "rearmed": False}
    assert repository.drawdown_liquidation_state("acct", Market.EQUITY) == DrawdownLiquidationState(
        liquidated_at=date(2025, 1, 6),
        rearmed=False,
    )


def test_restart_reloads_the_latched_state_and_blocks_reentry_until_recovery_and_cooldown(tmp_path) -> None:
    directory = tmp_path / "runtime"
    broker = _Broker((_position("AAPL", 10),), equity=50_000.0)
    runtime, repository = _runtime(directory, broker, {"AAPL": 0.30}, liquidation=ON)
    first = runtime.process_closed_bars(_bars(DAY1), force_scheduled=True)
    sell = next(order for order in first.orders if order.side == SELL)
    _fill_completely(repository, sell)
    broker.positions = []
    broker.open_symbols.clear()
    runtime.close()

    # Restart: a new repository and runtime read the state from disk only.
    assert PortfolioStateRepository(directory / "runtime.sqlite3").drawdown_liquidation_state(
        "acct", Market.EQUITY
    ) == DrawdownLiquidationState(liquidated_at=date(2025, 1, 6), rearmed=False)
    broker.equity_value = 80_000.0  # 20 % drawdown: below the 30 % re-arm level
    restarted, _ = _runtime(directory, broker, {"AAPL": 0.30}, liquidation=ON)
    blocked = restarted.process_closed_bars(_bars(DAY2), force_scheduled=True)

    assert DRAWDOWN_LIQUIDATION_REASON in blocked.risk.reasons
    assert [order for order in blocked.orders if order.side == BUY] == []
    assert _state_rows(directory / "runtime.sqlite3") == [("acct", "equity", "2025-01-06", 1)]

    released = restarted.process_closed_bars(_bars(DAY3), force_scheduled=True)

    assert DRAWDOWN_LIQUIDATION_REASON not in released.risk.reasons
    assert [(order.side, order.symbol) for order in released.orders] == [(BUY, "AAPL")]
    assert _state_rows(directory / "runtime.sqlite3") == [("acct", "equity", None, 0)]


@pytest.mark.parametrize("policy", [None, DrawdownLiquidationConfig()], ids=["no_config", "explicit_disabled"])
def test_disabled_brake_never_reads_or_writes_the_state(tmp_path, monkeypatch, policy) -> None:
    def _forbidden(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("drawdown liquidation state accessed while the brake is off")

    monkeypatch.setattr(PortfolioStateRepository, "drawdown_liquidation_state", _forbidden)
    monkeypatch.setattr(PortfolioStateRepository, "put_drawdown_liquidation_state", _forbidden)
    broker = _Broker((_position("AAPL", 10),), equity=50_000.0)
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.30}, liquidation=policy)

    result = runtime.process_closed_bars(_bars(DAY1), force_scheduled=True)

    assert DRAWDOWN_LIQUIDATION_REASON not in result.risk.reasons
    assert _state_rows(tmp_path / "runtime.sqlite3") == []
    stored = json.loads(repository.decision_row(result.decision_id)["risk_json"])
    assert "drawdown_liquidation_state" not in stored


def test_disabled_brake_gives_the_same_orders_as_a_runtime_without_the_block(tmp_path) -> None:
    def _run(name: str, policy: DrawdownLiquidationConfig | None) -> tuple[object, ...]:
        broker = _Broker((_position("AAPL", 10),), equity=50_000.0)
        runtime, _ = _runtime(tmp_path / name, broker, {"AAPL": 0.30}, liquidation=policy)
        result = runtime.process_closed_bars(_bars(DAY1), force_scheduled=True)
        return (result.status, _order_summary(result), tuple(result.risk.reasons), runtime.configuration_digest)

    assert _run("no_config", None) == _run("explicit_disabled", DrawdownLiquidationConfig())


class _DigestStrategy:
    name = "fixed_target"
    version = "1.0.0"

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return ()


def test_disabled_brake_leaves_the_configuration_digest_byte_identical() -> None:
    strategy = _DigestStrategy()
    policy = PortfolioExecutionPolicy()
    pre_brake_payload = {
        "strategy": "fixed_target",
        "version": "1.0.0",
        "parameters": (),
        "mode": "paper",
        "maximum_allocation": 0.5,
        "scanner_policy_digest": "v1",
        "risk_limits": asdict(LIMITS),
        "execution_policy": asdict(policy),
    }
    pre_brake = hashlib.sha256(json.dumps(pre_brake_payload, sort_keys=True, default=str).encode()).hexdigest()
    arguments = (strategy, "paper", 0.5, "v1", LIMITS, policy)

    assert configuration_digest(*arguments) == pre_brake
    assert configuration_digest(*arguments, drawdown_liquidation=DrawdownLiquidationConfig()) == pre_brake
    assert configuration_digest(*arguments, drawdown_liquidation=ON) != pre_brake


def test_config_fingerprint_ignores_the_disabled_block_and_changes_when_enabled() -> None:
    config = load_config(str(REPO_SETTINGS))
    assert config.risk.drawdown_liquidation.enabled is False
    document = asdict(config)
    document["risk"].pop("drawdown_liquidation")
    without_block = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()

    assert config_fingerprint(config) == without_block
    enabled = replace(config, risk=replace(config.risk, drawdown_liquidation=ON))
    assert config_fingerprint(enabled) != without_block
