from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.backtest import BacktestEngine
from parayabuck.config import EngineConfig, RiskLimits
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    Fill,
    Market,
    OrderIntent,
    OrderState,
    PositionSnapshot,
    ProtectionState,
    Regime,
    Signal,
)
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.state_machine import OrderStateMachine
from parayabuck.features.indicators import ema
from parayabuck.markets.regime import RegimeAssessment
from parayabuck.monitoring import DecisionJournal
from parayabuck.risk import RiskManager
from parayabuck.strategies.base import Strategy, StrategyContext
from parayabuck.strategies.stock_mean_reversion import StockMeanReversion
from parayabuck.strategies.stock_trend import StockTrendPullback

pytestmark = pytest.mark.paper_chaos


class ShadowBroker:
    """Test broker that records submissions without creating real orders."""

    def __init__(self, account: AccountSnapshot) -> None:
        self._account = account
        self.observed: list[OrderIntent] = []

    def account(self) -> AccountSnapshot:
        return self._account

    def submit(self, order: OrderIntent) -> tuple[OrderState, Fill | None]:
        self.observed.append(order)
        return OrderState.ACCEPTED, None

    def reconcile(self) -> None:
        return


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


class _SymbolFailingStrategy(Strategy):
    name = "symbol_failure"
    market = Market.EQUITY
    minimum_bars = 1

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        del context
        if bars[-1].symbol == "TEST":
            raise ValueError("bad symbol data")
        return None


def test_ema_is_exponential_not_sma() -> None:
    assert ema([1.0, 2.0, 3.0, 10.0], 3) == pytest.approx(6.0)
    assert ema([1.0, 2.0, 3.0, 10.0], 3) != pytest.approx(sum([2.0, 3.0, 10.0]) / 3)


@pytest.mark.parametrize("strategy", [StockTrendPullback(), StockMeanReversion()])
def test_inverse_equity_entries_require_non_bullish_benchmark(strategy: Strategy) -> None:
    bars = [
        Bar(
            datetime(2025, 1, 1, 14, 30, tzinfo=UTC) + timedelta(minutes=5 * index),
            "SH",
            "5m",
            40,
            40.5,
            39.5,
            40,
            1000,
            Market.EQUITY,
        )
        for index in range(strategy.minimum_bars)
    ]
    regime = Regime.TREND_UP if isinstance(strategy, StockTrendPullback) else Regime.RANGE
    context = StrategyContext(
        RegimeAssessment(regime, 20, 0, 0, ()),
        benchmark_regime=Regime.TREND_UP,
    )

    evaluation = strategy.evaluate_with_trace(bars, context)
    benchmark = next(item for item in evaluation.conditions if item.code == "benchmark_regime")

    assert benchmark.passed is False
    assert evaluation.signal is None


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


def test_order_state_machine_accepts_immediate_partial_fill() -> None:
    intent = OrderIntent("sig", "TEST", Market.EQUITY, Direction.LONG, 2, 100, 98, 104, "test")
    machine = OrderStateMachine()
    machine.create(intent)
    machine.transition(intent.client_order_id, OrderState.VALIDATED)
    machine.transition(intent.client_order_id, OrderState.SUBMITTED)

    lifecycle = machine.transition(intent.client_order_id, OrderState.PARTIALLY_FILLED)

    assert lifecycle.state == OrderState.PARTIALLY_FILLED


def test_order_state_machine_ignores_duplicate_state_observation() -> None:
    intent = OrderIntent("sig", "TEST", Market.EQUITY, Direction.LONG, 1, 100, 98, 104, "test")
    machine = OrderStateMachine()
    lifecycle = machine.create(intent)
    machine.transition(intent.client_order_id, OrderState.VALIDATED)
    machine.transition(intent.client_order_id, OrderState.SUBMITTED)
    machine.transition(intent.client_order_id, OrderState.ACCEPTED, "trade_update")
    history_length = len(lifecycle.history)

    duplicate = machine.transition(intent.client_order_id, OrderState.ACCEPTED, "broker_response")

    assert duplicate is lifecycle
    assert duplicate.state == OrderState.ACCEPTED
    assert len(duplicate.history) == history_length


def test_shadow_engine_journals_decision(tmp_path) -> None:
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    broker = ShadowBroker(account)
    journal = DecisionJournal(str(tmp_path / "decisions.sqlite3"))
    config = EngineConfig(warmup_bars=50)
    engine = TradingEngine(broker, config, (_OneShotStrategy(),), journal)
    orders = engine.process_cycle([_bar(0)])
    assert len(orders) == 1
    assert broker.observed == orders
    count = journal.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert count == 1
    order_count = journal.connection.execute("SELECT COUNT(*) FROM decision_orders").fetchone()[0]
    assert order_count == 3
    journal.close()


def test_strategy_failure_skips_only_the_bad_symbol() -> None:
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    broker = ShadowBroker(account)
    engine = TradingEngine(
        broker,
        EngineConfig(warmup_bars=50),
        (_SymbolFailingStrategy(), _OneShotStrategy()),
    )
    good_bar = Bar(**{**_bar(0).__dict__, "symbol": "GOOD"})

    orders = engine.process_cycle([_bar(0), good_bar])

    assert [order.symbol for order in orders] == ["GOOD"]
    assert not engine.has_data_gaps_for_symbol("TEST")
    assert not engine.has_data_gaps_for_symbol("GOOD")


def test_submit_exception_becomes_reconciliation_lock() -> None:
    class FailingBroker(ShadowBroker):
        def submit(self, order: OrderIntent) -> tuple[OrderState, Fill | None]:
            del order
            raise TimeoutError("broker timeout")

    engine = TradingEngine(
        FailingBroker(AccountSnapshot(10_000, 10_000, 10_000, 0)),
        EngineConfig(warmup_bars=50),
        (_OneShotStrategy(),),
    )

    orders = engine.process_cycle([_bar(0)])

    assert len(orders) == 1
    lifecycle = engine.orders.orders[orders[0].client_order_id]
    assert lifecycle.state == OrderState.RECONCILIATION_REQUIRED
    assert engine.entry_halt_reason == "ambiguous_order_state"


def test_stale_submit_response_does_not_regress_stream_fill() -> None:
    class RacingBroker(ShadowBroker):
        engine: TradingEngine

        def submit(self, order: OrderIntent) -> tuple[OrderState, Fill | None]:
            self.engine.orders.transition(
                order.client_order_id, OrderState.FILLED, "trade_update"
            )
            return OrderState.ACCEPTED, None

    broker = RacingBroker(AccountSnapshot(10_000, 10_000, 10_000, 0))
    engine = TradingEngine(
        broker,
        EngineConfig(warmup_bars=50),
        (_OneShotStrategy(),),
    )
    broker.engine = engine

    orders = engine.process_cycle([_bar(0)])

    assert len(orders) == 1
    assert engine.orders.orders[orders[0].client_order_id].state == OrderState.FILLED


def test_decision_journal_preserves_multiple_reasons_for_one_signal(tmp_path) -> None:
    journal = DecisionJournal(str(tmp_path / "decision-history.sqlite3"))
    signal = _OneShotStrategy().evaluate(
        [_bar(0)],
        StrategyContext(RegimeAssessment(Regime.RANGE, 1.0, 0, 0, ())),
    )
    assert signal is not None

    journal.record_decision(signal, False, "first_gate")
    journal.record_decision(signal, False, "second_gate")

    reasons = journal.connection.execute(
        "SELECT reason FROM decisions WHERE signal_id=? ORDER BY id",
        (signal.signal_id,),
    ).fetchall()
    assert reasons == [("first_gate",), ("second_gate",)]
    journal.close()


def test_decision_journal_preserves_repeated_order_state_within_one_second(
    tmp_path,
) -> None:
    journal = DecisionJournal(str(tmp_path / "order-history.sqlite3"))
    order = OrderIntent(
        "signal",
        "TEST",
        Market.EQUITY,
        Direction.LONG,
        1,
        100,
        98,
        104,
        "test",
    )

    journal.record_order(order, OrderState.ACCEPTED, "stream")
    journal.record_order(order, OrderState.ACCEPTED, "reconcile")

    reasons = journal.connection.execute(
        "SELECT reason FROM decision_orders WHERE client_order_id=? ORDER BY timestamp",
        (order.client_order_id,),
    ).fetchall()
    assert reasons == [("stream",), ("reconcile",)]
    journal.close()


def test_engine_accepts_broker_partial_fill_response() -> None:
    class PartialFillBroker(ShadowBroker):
        def submit(self, order: OrderIntent) -> tuple[OrderState, Fill | None]:
            fill = Fill(
                order.client_order_id,
                order.symbol,
                order.qty / 2,
                order.entry_price,
                order.direction,
                order.created_at,
            )
            return OrderState.PARTIALLY_FILLED, fill

    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    broker = PartialFillBroker(account)
    engine = TradingEngine(
        broker,
        EngineConfig(warmup_bars=50),
        (_OneShotStrategy(),),
    )

    orders = engine.process_cycle([_bar(0)])

    assert len(orders) == 1
    lifecycle = engine.orders.orders[orders[0].client_order_id]
    assert lifecycle.state == OrderState.PARTIALLY_FILLED


def test_backtest_fills_next_bar_and_honors_target() -> None:
    config = EngineConfig(warmup_bars=50)
    backtest = BacktestEngine(config, strategies=(_OneShotStrategy(),), slippage_bps=0)
    bars = [_bar(0, 100), _bar(1, 100), _bar(2, 102, high=103)]
    result = backtest.run(bars)
    assert result.total_trades == 1
    assert result.realized_pnls[0] == pytest.approx(20)
    assert result.ending_equity == pytest.approx(10_020)


def test_alpaca_reconcile_restores_missing_position_protection() -> None:
    submitted: list[tuple[str, float]] = []

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.account = lambda: AccountSnapshot(
        cash=9_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(PositionSnapshot("AAPL", 10, 100, 101, Market.EQUITY),),
    )
    broker._has_protective_order = lambda _symbol, _is_long, _qty: False

    def submit_stop(
        symbol: str,
        _market: Market,
        _qty: float,
        stop_price: float,
        _position_is_long: bool,
        client_order_id: str | None = None,
    ) -> bool:
        submitted.append((symbol, stop_price))
        return True

    broker._submit_stop = submit_stop
    broker.reconcile()

    assert submitted == [("AAPL", pytest.approx(96.0))]


def test_reconcile_does_not_treat_reserved_crypto_as_unprotected() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._asset_cache = {"BTC/USD": SimpleNamespace(min_trade_increment="0.000000001")}
    broker.account = lambda: AccountSnapshot(
        cash=9_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(PositionSnapshot("BTC/USD", 1, 100, 101, Market.CRYPTO, qty_available=0),),
    )
    broker._has_protective_order = lambda _symbol, _is_long, _qty: True
    broker._available_position_qty = lambda *_args, **_kwargs: pytest.fail(
        "available quantity should not be queried for an already protected position"
    )

    broker.reconcile()


def test_reconcile_accepts_fee_adjusted_crypto_stop_as_full_protection() -> None:
    checked_required_qty: list[float] = []
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._asset_cache = {"ADAUSD": SimpleNamespace(min_trade_increment="0.000000001")}
    broker.account = lambda: AccountSnapshot(
        cash=9_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(
            PositionSnapshot(
                "ADAUSD", 57371.241146769, 0.1741, 0.175, Market.CRYPTO, qty_available=0
            ),
        ),
    )

    def has_stop(_symbol: str, _is_long: bool, required_qty: float) -> bool:
        checked_required_qty.append(required_qty)
        return required_qty == pytest.approx(57227.813043902)

    broker._has_protective_order = has_stop
    broker._available_position_qty = lambda *_args, **_kwargs: pytest.fail(
        "all sellable crypto is already reserved by the fee-adjusted stop"
    )

    broker.reconcile()

    assert checked_required_qty == [pytest.approx(57227.813043902)]


def test_reconcile_adds_underprotected_crypto_remainder_without_cancelling() -> None:
    checked_required_qty: list[float] = []
    submitted: list[float] = []
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.account = lambda: AccountSnapshot(
        cash=9_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(PositionSnapshot("BTCUSD", 1, 100, 101, Market.CRYPTO, qty_available=0.01),),
    )
    def has_protection(_symbol: str, _is_long: bool, required_qty: float) -> bool:
        checked_required_qty.append(required_qty)
        broker._last_protection_mismatch = "under"
        return False

    broker._has_protective_order = has_protection
    broker._cancel_protective_orders = lambda *_args: pytest.fail(
        "undersized protection must remain active while the remainder is added"
    )
    broker._available_position_qty = lambda *_args, **_kwargs: 0.01
    broker._submit_stop = lambda _symbol, _market, qty, _stop, _is_long, **_kwargs: (
        submitted.append(qty) or True
    )

    broker.reconcile()

    assert checked_required_qty == [1]
    assert submitted == [0.01]


def test_reconcile_accepts_broker_qty_within_crypto_fee_reserve() -> None:
    ownership = SimpleNamespace(
        owned_qty=1.0,
        entry_client_order_id="entry",
        original_stop=95.0,
        broker_order_id=None,
    )
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._account_id = "acct"
    broker.ledger = SimpleNamespace(
        managed_for_account=lambda _account: {"BTC/USD": ownership},
        managed_entries_for_account=lambda _account: (),
        set_protection_state=lambda *_args: None,
    )
    broker.account = lambda: AccountSnapshot(
        cash=9_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(
            PositionSnapshot(
                "BTC/USD", 0.9975, 100, 101, Market.CRYPTO, qty_available=0
            ),
        ),
    )
    broker._round_qty_down = lambda _symbol, qty: qty
    broker._has_protective_order = lambda _symbol, _long, qty: qty == pytest.approx(0.9975)

    broker.reconcile()


def test_cancel_protective_orders_waits_for_confirmation() -> None:
    statuses = iter(("pending_cancel", "canceled"))
    cancelled: list[str] = []
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.crypto_stop_cancel_timeout_seconds = 0.2
    broker.crypto_stop_cancel_poll_interval_seconds = 0.001
    broker._open_protective_orders = lambda *_args: [SimpleNamespace(id="stop-1")]
    broker._client = SimpleNamespace(
        cancel_order_by_id=cancelled.append,
        get_order_by_id=lambda _order_id: SimpleNamespace(status=next(statuses)),
    )

    broker._cancel_protective_orders("AAPL", True)

    assert cancelled == ["stop-1"]


def test_reconcile_protects_only_unreserved_equity_qty() -> None:
    submitted: list[float] = []
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.account = lambda: AccountSnapshot(
        cash=9_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(PositionSnapshot("AAPL", 1, 100, 101, Market.EQUITY, qty_available=0.5),),
    )
    broker._has_protective_order = lambda _symbol, _is_long, _qty: False
    broker._submit_stop = lambda _symbol, _market, qty, _stop, _is_long, **_kwargs: (
        submitted.append(qty) or True
    )

    broker.reconcile()

    assert submitted == [pytest.approx(0.5)]


def test_reconcile_finds_held_stop_on_filled_bracket_parent() -> None:
    class FakeClient:
        def get_order_by_id(self, order_id: str, request: Any) -> Any:
            assert order_id == "entry-order"
            assert request.nested is True
            return SimpleNamespace(
                symbol="XLE",
                side="buy",
                type="market",
                status="filled",
                legs=(
                    SimpleNamespace(
                        symbol="XLE",
                        side="sell",
                        type="limit",
                        status="new",
                        qty="170",
                        stop_price=None,
                        legs=None,
                    ),
                    SimpleNamespace(
                        symbol="XLE",
                        side="sell",
                        type="stop",
                        status="held",
                        qty="170",
                        stop_price="57.99",
                        legs=None,
                    ),
                ),
            )

    ownership = SimpleNamespace(
        original_stop=57.99,
        owned_qty=170,
        broker_order_id="entry-order",
        entry_client_order_id="parayabuck-entry",
    )
    protection_states: list[ProtectionState] = []
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    broker._account_id = "acct"
    broker.manage_external_positions = False
    broker.ledger = SimpleNamespace(
        managed_for_account=lambda _account: {"XLE": ownership},
        set_protection_state=lambda _account, _entry, state: protection_states.append(state),
    )
    broker.account = lambda: AccountSnapshot(
        cash=9_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(PositionSnapshot("XLE", 170, 58.59, 58.7, Market.EQUITY, qty_available=0),),
    )
    broker._has_protective_order = lambda *_args: False
    broker._submit_stop = lambda *_args, **_kwargs: pytest.fail(
        "held bracket stop already protects the position"
    )

    broker.reconcile()

    assert protection_states == [ProtectionState.ACTIVE]


def test_reconcile_finds_held_stop_across_multiple_managed_entries() -> None:
    class FakeClient:
        def get_order_by_id(self, order_id: str, request: Any) -> Any:
            assert request.nested is True
            legs = ()
            if order_id == "new-entry":
                legs = (
                    SimpleNamespace(
                        symbol="MSFT",
                        side="sell",
                        type="stop",
                        status="held",
                        qty="25",
                        stop_price="392.87",
                        legs=None,
                    ),
                )
            return SimpleNamespace(symbol="MSFT", legs=legs)

    ownership = SimpleNamespace(
        original_stop=387.34,
        owned_qty=50,
        broker_order_id="old-entry",
        entry_client_order_id="parayabuck-old-entry",
    )
    protection_states: list[ProtectionState] = []
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    broker._account_id = "acct"
    broker.manage_external_positions = False
    broker.ledger = SimpleNamespace(
        managed_for_account=lambda _account: {"MSFT": ownership},
        managed_entry_broker_order_ids=lambda _account, _symbol: {
            "old-entry",
            "new-entry",
        },
        set_protection_state=lambda _account, _entry, state: protection_states.append(state),
    )
    broker.account = lambda: AccountSnapshot(
        cash=9_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(PositionSnapshot("MSFT", 25, 400, 401, Market.EQUITY, qty_available=0),),
    )
    broker._has_protective_order = lambda *_args: False
    broker._submit_stop = lambda *_args, **_kwargs: pytest.fail(
        "the held stop on the newer bracket protects the broker position"
    )

    broker.reconcile()

    assert protection_states == [ProtectionState.ACTIVE]


def test_crypto_order_flattens_when_protection_fails() -> None:
    submitted: list[Any] = []
    flattened: list[tuple[str, float]] = []

    class FakeClient:
        def submit_order(self, request: Any) -> Any:
            submitted.append(request)
            return SimpleNamespace(id="entry-id")

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    broker._validated_values = lambda intent: (
        intent.qty,
        intent.stop_price,
        intent.target_price,
    )
    intent = OrderIntent(
        "signal",
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        0.01,
        100,
        95,
        110,
        "test",
    )
    fill = Fill(
        intent.client_order_id,
        intent.symbol,
        intent.qty,
        100,
        intent.direction,
        intent.created_at,
    )
    broker._wait_for_fill = lambda *_args: (OrderState.FILLED, fill)
    broker._submit_crypto_stop = lambda *_args: False
    broker._flatten = lambda order, qty: flattened.append((order.symbol, qty))

    state, returned_fill = broker.submit(intent)

    assert state == OrderState.REJECTED
    assert returned_fill == fill
    assert len(submitted) == 1
    assert flattened == [("BTC/USD", 0.01)]


def test_submit_recovers_order_accepted_before_network_timeout() -> None:
    recovered = SimpleNamespace(id="entry-id", status="new")

    class FakeClient:
        def submit_order(self, _request: Any) -> Any:
            raise TimeoutError("response timed out")

        def get_order_by_client_id(self, _client_order_id: str) -> Any:
            return recovered

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()

    result = broker._submit_with_recovery(SimpleNamespace(), "parayabuck-test")

    assert result is recovered


def test_pre_submit_rejects_equity_order_while_market_is_closed() -> None:
    class FakeClient:
        def get_clock(self) -> Any:
            return SimpleNamespace(is_open=False)

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    intent = OrderIntent("signal", "AAPL", Market.EQUITY, Direction.LONG, 1, 100, 95, 110, "test")

    with pytest.raises(ValueError, match="market is closed"):
        broker._validate_pre_submit(intent)


def test_pre_submit_rejects_symbol_with_existing_open_order() -> None:
    class FakeClient:
        def get_orders(self, _request: Any) -> list[Any]:
            return [SimpleNamespace(id="existing-order")]

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    intent = OrderIntent(
        "signal", "BTC/USD", Market.CRYPTO, Direction.LONG, 0.01, 100, 95, 110, "test"
    )

    with pytest.raises(ValueError, match="Open order already exists"):
        broker._validate_pre_submit(intent)


def test_crypto_partial_fill_is_cancelled_before_stop_is_submitted() -> None:
    cancelled: list[str] = []
    protected: list[float] = []

    class FakeClient:
        def submit_order(self, _request: Any) -> Any:
            return SimpleNamespace(id="entry-id")

        def cancel_order_by_id(self, order_id: str) -> None:
            cancelled.append(order_id)

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    broker._validated_values = lambda intent: (
        intent.qty,
        intent.stop_price,
        intent.target_price,
    )
    intent = OrderIntent(
        "signal", "BTC/USD", Market.CRYPTO, Direction.LONG, 0.02, 100, 95, 110, "test"
    )
    partial_fill = Fill(
        intent.client_order_id,
        intent.symbol,
        0.01,
        100,
        intent.direction,
        intent.created_at,
    )
    responses = iter(
        [
            (OrderState.PARTIALLY_FILLED, partial_fill),
            (OrderState.CANCELLED, partial_fill),
        ]
    )
    broker._wait_for_fill = lambda *_args, **_kwargs: next(responses)
    broker._submit_crypto_stop = lambda _intent, qty, _stop: protected.append(qty) or True

    state, fill = broker.submit(intent)

    assert state == OrderState.CANCELLED
    assert fill == partial_fill
    assert cancelled == ["entry-id"]
    assert protected == [0.01]


def test_crypto_stop_uses_available_qty_after_fee() -> None:
    submitted: list[Any] = []

    class FakeClient:
        def get_open_position(self, _symbol: str) -> Any:
            return SimpleNamespace(qty="0.15207612", qty_available="0.151695929")

        def submit_order(self, request: Any) -> Any:
            submitted.append(request)
            return SimpleNamespace(id="stop-id")

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    broker._asset_cache = {
        "BTC/USD": SimpleNamespace(
            min_trade_increment="0.000000001",
            price_increment="0.01",
        )
    }
    intent = OrderIntent(
        "signal",
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        0.15207612,
        100,
        95,
        110,
        "test",
    )

    assert broker._submit_crypto_stop(intent, intent.qty, intent.stop_price)
    assert len(submitted) == 1
    assert float(submitted[0].qty) == pytest.approx(0.151695929)


def test_crypto_stop_retries_with_available_qty_from_alpaca_error() -> None:
    submitted: list[Any] = []

    class FakeClient:
        def submit_order(self, request: Any) -> Any:
            submitted.append(request)
            if len(submitted) == 1:
                raise RuntimeError(
                    '{"available":"0.151695929","balance":"0.151695929",'
                    '"code":40310000,"message":"insufficient balance for BTC",'
                    '"symbol":"USD"}'
                )
            return SimpleNamespace(id="stop-id")

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    broker._asset_cache = {
        "BTC/USD": SimpleNamespace(
            min_trade_increment="0.000000001",
            price_increment="0.01",
        )
    }

    assert broker._submit_stop("BTC/USD", Market.CRYPTO, 0.15207612, 95, True)
    assert len(submitted) == 2
    assert float(submitted[1].qty) == pytest.approx(0.151695929)


def test_crypto_closing_qty_reserves_max_taker_fee_when_position_lookup_fails() -> None:
    class FakeClient:
        pass

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    broker._asset_cache = {"BTC/USD": SimpleNamespace(min_trade_increment="0.000000001")}

    qty = broker._available_position_qty(
        "BTC/USD",
        0.15207612,
        reserve_fee_on_lookup_failure=True,
    )

    assert qty == pytest.approx(0.151695929)


def test_only_stop_like_closing_order_counts_as_position_protection() -> None:
    class FakeClient:
        orders: list[Any] = []

        def get_orders(self, _request: Any) -> list[Any]:
            return self.orders

    broker = AlpacaBroker.__new__(AlpacaBroker)
    client = FakeClient()
    broker._client = client
    client.orders = [SimpleNamespace(side="sell", type="limit", stop_price=None, legs=None)]
    assert not broker._has_protective_order("BTC/USD", True, 1)

    client.orders = [
        SimpleNamespace(side="sell", type="stop_limit", stop_price="95", qty="0.5", legs=None)
    ]
    assert not broker._has_protective_order("BTC/USD", True, 1)

    client.orders = [
        SimpleNamespace(side="sell", type="stop_limit", stop_price="95", qty="1", legs=None)
    ]
    assert broker._has_protective_order("BTC/USD", True, 1)

    client.orders = [
        SimpleNamespace(
            side="sell",
            type="limit",
            status="new",
            client_order_id="parayabuck-parent",
            legs=[
                SimpleNamespace(
                    side="sell",
                    type="stop_limit",
                    status="canceled",
                    stop_price="95",
                    qty="1",
                    legs=None,
                )
            ],
        )
    ]
    assert not broker._has_protective_order("BTC/USD", True, 1)


def test_validated_values_rounds_equity_bracket_to_alpaca_rules() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._asset_cache = {
        "XLU": SimpleNamespace(
            tradable=True,
            fractionable=True,
            min_order_size=None,
            min_trade_increment=None,
            price_increment=None,
        )
    }
    intent = OrderIntent(
        "sig",
        "XLU",
        Market.EQUITY,
        Direction.LONG,
        217.6323,
        45.91,
        45.4509,
        46.73638,
        "test",
    )

    qty, stop, target = broker._validated_values(intent)

    assert qty == 217
    assert isinstance(qty, int)
    assert stop == pytest.approx(45.45)
    assert target == pytest.approx(46.74)


def test_validated_values_rejects_equity_order_below_one_share() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._asset_cache = {
        "XLU": SimpleNamespace(
            tradable=True,
            fractionable=True,
            min_order_size=None,
            min_trade_increment=None,
            price_increment=None,
        )
    }
    intent = OrderIntent(
        "sig", "XLU", Market.EQUITY, Direction.LONG, 0.6, 45.91, 45.45, 46.74, "test"
    )

    with pytest.raises(ValueError, match="rounds to zero"):
        broker._validated_values(intent)


def test_validated_values_rounds_crypto_with_asset_increments() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._asset_cache = {
        "BTC/USD": SimpleNamespace(
            tradable=True,
            fractionable=True,
            min_order_size="0.001",
            min_trade_increment="0.0001",
            price_increment="0.01",
        )
    }
    intent = OrderIntent(
        "sig",
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        0.012345678,
        100,
        95.123456,
        110.987,
        "test",
    )

    qty, stop, target = broker._validated_values(intent)

    assert qty == pytest.approx(0.0123)
    assert stop == pytest.approx(95.12)
    assert target == pytest.approx(110.99)


def test_validated_values_does_not_round_crypto_up_past_risk_limit() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._asset_cache = {
        "BTC/USD": SimpleNamespace(
            tradable=True,
            fractionable=True,
            min_order_size="0.001",
            min_trade_increment="0.0001",
            price_increment="0.01",
        )
    }
    intent = OrderIntent(
        "sig", "BTC/USD", Market.CRYPTO, Direction.LONG, 0.00099, 100, 95, 110, "test"
    )

    with pytest.raises(ValueError, match="below the minimum"):
        broker._validated_values(intent)


def test_validated_values_rejects_crypto_short() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._asset_cache = {
        "BTC/USD": SimpleNamespace(
            tradable=True,
            min_order_size="0.0001",
            min_trade_increment="0.0001",
            price_increment="0.01",
        )
    }
    intent = OrderIntent(
        "sig", "BTC/USD", Market.CRYPTO, Direction.SHORT, 0.01, 100, 105, 90, "test"
    )

    with pytest.raises(ValueError, match="does not support short"):
        broker._validated_values(intent)


def test_submit_stop_preserves_fractional_equity_qty_with_day_tif() -> None:
    submitted: list[Any] = []

    class FakeClient:
        def submit_order(self, request: Any) -> Any:
            submitted.append(request)
            return SimpleNamespace(id="stop-id")

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    broker._asset_cache = {"AAPL": SimpleNamespace(price_increment=None)}

    assert broker._submit_stop("AAPL", Market.EQUITY, 10.7, 96.0345, True)
    assert len(submitted) == 1
    assert submitted[0].qty == pytest.approx(10.7)
    assert str(submitted[0].time_in_force).lower().endswith("day")
    assert float(submitted[0].stop_price) == pytest.approx(96.03)


def test_risk_manager_sizes_equity_in_whole_shares() -> None:
    signal = Signal(
        "XLU",
        Market.EQUITY,
        Direction.LONG,
        "stock_mean_reversion",
        80,
        0.8,
        45.91,
        45.4509,
        46.7364,
        datetime(2026, 7, 22, 18, 40, tzinfo=UTC),
    )
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    limits = RiskLimits(risk_per_trade_pct=0.005, max_position_pct=0.5)

    decision = RiskManager(limits).evaluate(signal, account)

    assert decision.approved
    assert decision.order is not None
    assert decision.order.qty == 54
    assert float(decision.order.qty).is_integer()


def test_risk_manager_rejects_invalid_long_geometry() -> None:
    signal = Signal(
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        "test",
        80,
        0.8,
        100,
        101,
        110,
        datetime.now(UTC),
    )

    decision = RiskManager(RiskLimits()).evaluate(
        signal, AccountSnapshot(10_000, 10_000, 10_000, 0)
    )

    assert not decision.approved
    assert decision.reason == "invalid_long_price_geometry"


def test_risk_manager_uses_crypto_buying_power_and_minimum_notional() -> None:
    signal = Signal(
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        "test",
        80,
        0.8,
        100,
        99,
        102,
        datetime.now(UTC),
    )
    account = AccountSnapshot(
        10_000,
        10_000,
        10_000,
        0,
        crypto_buying_power=9,
    )

    decision = RiskManager(RiskLimits()).evaluate(signal, account)

    assert not decision.approved
    assert decision.reason == "order_below_minimum_notional"


def test_risk_manager_caps_crypto_order_at_alpaca_notional_limit() -> None:
    signal = Signal(
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        "test",
        80,
        0.8,
        100,
        99,
        102,
        datetime.now(UTC),
    )
    limits = RiskLimits(
        risk_per_trade_pct=1,
        max_position_pct=1,
        max_crypto_exposure_pct=1,
        max_sector_exposure_pct=1,
        max_gross_exposure_pct=1,
    )
    account = AccountSnapshot(1_000_000, 1_000_000, 1_000_000, 0)

    decision = RiskManager(limits).evaluate(signal, account)

    assert decision.approved
    assert decision.order is not None
    assert decision.order.qty * signal.entry_price <= 200_000


def test_fill_fee_reconciliation_uses_client_relative_activity_path() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    class FakeClient:
        def _request(self, method: str, path: str, data: dict[str, object]) -> list[object]:
            calls.append((method, path, data))
            return []

    class FakeLedger:
        def apply_fee_activity(self, *_args: object, **_kwargs: object) -> bool:
            return False

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = FakeClient()
    broker.ledger = FakeLedger()
    broker._account_id = "account-id"

    broker._reconcile_fill_activity_fees()

    assert calls == [("GET", "/account/activities/FILL", {"direction": "desc", "page_size": 100})]
