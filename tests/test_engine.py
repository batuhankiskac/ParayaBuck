from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.broker_models import Account as LegacyAccount
from parayabuck.broker_models import Order as LegacyOrder
from parayabuck.broker_models import OrderStatus as LegacyOrderStatus
from parayabuck.broker_models import Position as LegacyPosition
from parayabuck.broker_models import Side as LegacySide
from parayabuck.backtest import BacktestEngine
from parayabuck.config import RiskLimits, EngineConfig
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    Market,
    OrderIntent,
    OrderState,
    Regime,
    Signal,
)
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import AlpacaExecutionBroker
from parayabuck.execution.shadow import ShadowBroker
from parayabuck.execution.state_machine import OrderStateMachine
from parayabuck.features.indicators import ema
from parayabuck.markets.regime import RegimeAssessment
from parayabuck.monitoring import DecisionJournal
from parayabuck.risk import RiskManager
from parayabuck.strategies.base import Strategy, StrategyContext


def _bar(index: int, close: float = 100.0, high: float | None = None) -> Bar:
    timestamp = datetime(2025, 1, 1, 14, 30, tzinfo=UTC) + timedelta(minutes=5 * index)
    return Bar(
        timestamp,
        "TEST",
        "5m",
        close,
        high if high is not None else close + 0.5,
        close - 0.5,
        close,
        1000,
        Market.EQUITY,
    )


class _OneShotStrategy(Strategy):
    name = "one_shot"
    market = Market.EQUITY
    minimum_bars = 1

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        if context.position is not None:
            return None
        bar = bars[-1]
        return Signal(
            bar.symbol,
            bar.market,
            Direction.LONG,
            self.name,
            90,
            0.9,
            bar.close,
            bar.close - 1,
            bar.close + 2,
            bar.timestamp,
            ("test_setup",),
            Regime.RANGE,
        )


def test_ema_is_exponential_not_sma() -> None:
    assert ema([1.0, 2.0, 3.0, 10.0], 3) == pytest.approx(6.0)
    assert ema([1.0, 2.0, 3.0, 10.0], 3) != pytest.approx(sum([2.0, 3.0, 10.0]) / 3)


def test_risk_manager_sizes_from_stop_distance() -> None:
    context = StrategyContext(RegimeAssessment(Regime.RANGE, 1, 0, 0, ()))
    signal = _OneShotStrategy().evaluate([_bar(0)], context)
    assert signal is not None
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    decision = RiskManager(RiskLimits(risk_per_trade_pct=0.005)).evaluate(signal, account)
    assert decision.approved
    assert decision.order is not None
    assert decision.risk_amount == pytest.approx(50)
    assert decision.order.qty == pytest.approx(10)  # capped at 10% position value


def test_order_state_machine_rejects_invalid_transition() -> None:
    intent = OrderIntent("sig", "TEST", Market.EQUITY, Direction.LONG, 1, 100, 98, 104, "test")
    machine = OrderStateMachine()
    machine.create(intent)
    machine.transition(intent.client_order_id, OrderState.VALIDATED)
    with pytest.raises(ValueError):
        machine.transition(intent.client_order_id, OrderState.FILLED)


def test_shadow_engine_journals_decision(tmp_path) -> None:
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    broker = ShadowBroker(account)
    journal = DecisionJournal(str(tmp_path / "test.sqlite3"))
    config = EngineConfig(warmup_bars=50, shadow_mode=True)
    engine = TradingEngine(broker, config, (_OneShotStrategy(),), journal)
    orders = engine.process_cycle([_bar(0)])
    assert len(orders) == 1
    assert broker.observed == []
    count = journal.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert count == 1
    journal.close()


def test_backtest_fills_next_bar_and_honors_target() -> None:
    config = EngineConfig(warmup_bars=50, shadow_mode=True)
    backtest = BacktestEngine(config, strategies=(_OneShotStrategy(),), slippage_bps=0)
    bars = [_bar(0, 100), _bar(1, 100), _bar(2, 102, high=103)]
    result = backtest.run(bars)
    assert result.total_trades == 1
    assert result.realized_pnls[0] == pytest.approx(20)
    assert result.ending_equity == pytest.approx(10_020)


def test_alpaca_reconcile_restores_missing_position_protection() -> None:
    submitted: list[tuple[str, float]] = []

    class FakeBroker:
        def get_account(self) -> LegacyAccount:
            return LegacyAccount(
                balance=9_000,
                equity=10_000,
                positions={"AAPL": LegacyPosition("AAPL", 10, 100, 101)},
            )

        def has_open_order(self, symbol: str) -> bool:
            return False

        def submit_protective_stop(
            self,
            symbol: str,
            qty: float,
            stop_price: float,
            position_is_long: bool,
        ) -> LegacyOrder:
            submitted.append((symbol, stop_price))
            order = LegacyOrder(symbol, LegacySide.SELL, qty)
            order.status = LegacyOrderStatus.PENDING
            return order

    adapter = AlpacaExecutionBroker.__new__(AlpacaExecutionBroker)
    adapter._broker = FakeBroker()
    adapter.reconcile()

    assert submitted == [("AAPL", pytest.approx(96.0))]
