from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.config import EngineConfig, EquityScannerConfig, EquityUniverseConfig
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    ExecutionEvent,
    ExecutionTransition,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
    Regime,
    Signal,
    StrategyContext,
)
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import AlpacaBroker, AmbiguousOrderState
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream, TradeUpdate
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.risk.manager import RiskDecision
from parayabuck.runtime import EngineRuntime, WorkerHealth
from parayabuck.universe import (
    EquityCandidate,
    EquityUniverseScanner,
    load_active_symbols,
    validate_active_universe,
)
from tests.helpers import Strategy

pytestmark = pytest.mark.paper_chaos


class _ScoredStrategy(Strategy):
    minimum_bars = 1

    def __init__(self, market: Market, score: float) -> None:
        self.market = market
        self.name = f"{market.value}_{score}"
        self.score = score

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        del context
        bar = bars[-1]
        return Signal(
            bar.symbol,
            bar.market,
            Direction.LONG,
            self.name,
            self.score,
            0.9,
            bar.close,
            bar.close - 1,
            bar.close + 2,
            bar.timestamp,
            regime=Regime.RANGE,
        )


def _bar(symbol: str, market: Market, timestamp: datetime) -> Bar:
    return Bar(timestamp, symbol, "5m", 100, 101, 99, 100, 1_000, market)


@pytest.mark.parametrize(
    ("equity_score", "crypto_score", "expected"),
    [(95.0, 66.0, "AAPL"), (66.0, 95.0, "SOL/USD")],
)
def test_global_ranker_compares_equity_and_crypto(
    equity_score: float, crypto_score: float, expected: str
) -> None:
    submitted: list[str] = []

    class Broker:
        def account(self) -> AccountSnapshot:
            return AccountSnapshot(10_000, 10_000, 10_000, 0)

        def submit(self, order: OrderIntent) -> tuple[OrderState, None]:
            submitted.append(order.symbol)
            return OrderState.ACCEPTED, None

        def reconcile(self) -> None:
            return None

    timestamp = datetime(2026, 7, 28, 12, tzinfo=UTC)
    engine = TradingEngine(
        Broker(),
        EngineConfig(warmup_bars=50, crypto_trading_enabled=True),
        (
            _ScoredStrategy(Market.EQUITY, equity_score),
            _ScoredStrategy(Market.CRYPTO, crypto_score),
        ),
    )
    engine.process_cycle(
        [_bar("AAPL", Market.EQUITY, timestamp), _bar("SOL/USD", Market.CRYPTO, timestamp)]
    )
    assert submitted == [expected]


def test_replayed_lifecycle_is_not_resubmitted_to_broker() -> None:
    submitted: list[str] = []

    class Broker:
        def account(self) -> AccountSnapshot:
            return AccountSnapshot(10_000, 10_000, 10_000, 0)

        def submit(self, order: OrderIntent) -> tuple[OrderState, None]:
            submitted.append(order.client_order_id)
            return OrderState.ACCEPTED, None

        def reconcile(self) -> None:
            return None

    engine = TradingEngine(
        Broker(),
        EngineConfig(warmup_bars=50),
        (_ScoredStrategy(Market.EQUITY, 95.0),),
    )
    timestamp = datetime(2026, 7, 28, 12, tzinfo=UTC)
    signal = Signal(
        "AAPL",
        Market.EQUITY,
        Direction.LONG,
        "test",
        95.0,
        0.9,
        100.0,
        99.0,
        102.0,
        timestamp,
        regime=Regime.RANGE,
        signal_id="replayed-signal",
    )
    order = OrderIntent(
        signal_id="replayed-signal",
        symbol="AAPL",
        market=Market.EQUITY,
        direction=Direction.LONG,
        qty=10.0,
        entry_price=100.0,
        stop_price=99.0,
        target_price=102.0,
        strategy="test",
        client_order_id="order-1",
    )
    # The lifecycle already advanced past CREATED (e.g. a replay after a
    # restart rehydrated the same order id).
    engine.orders.create(order)
    engine.orders.transition("order-1", OrderState.VALIDATED, "test")
    engine.orders.transition("order-1", OrderState.SUBMITTED, "test")
    engine.risk.evaluate = lambda _signal, _account: RiskDecision(
        True, "approved", order, risk_amount=10.0
    )

    accepted, _attempted, _executions = engine._execute_candidates(
        [signal], set(), {"replayed-signal": []}, AccountSnapshot(10_000, 10_000, 10_000, 0)
    )

    assert submitted == []
    assert accepted == [order]


def test_definite_broker_rejection_does_not_consume_global_capacity() -> None:
    submitted: list[str] = []

    class Broker:
        def account(self) -> AccountSnapshot:
            return AccountSnapshot(10_000, 10_000, 10_000, 0)

        def submit(self, order: OrderIntent) -> tuple[OrderState, None]:
            submitted.append(order.symbol)
            return (
                (OrderState.REJECTED, None) if len(submitted) == 1 else (OrderState.ACCEPTED, None)
            )

        def reconcile(self) -> None:
            return None

    timestamp = datetime(2026, 7, 28, 12, tzinfo=UTC)
    engine = TradingEngine(
        Broker(),
        EngineConfig(warmup_bars=50, crypto_trading_enabled=True),
        (
            _ScoredStrategy(Market.CRYPTO, 95),
            _ScoredStrategy(Market.EQUITY, 90),
        ),
    )
    engine.process_cycle(
        [_bar("BTC/USD", Market.CRYPTO, timestamp), _bar("AAPL", Market.EQUITY, timestamp)]
    )
    assert submitted == ["BTC/USD", "AAPL"]


def _readiness_runtime() -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._state = "running"
    runtime._circuit_breaker_reason = None
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._last_account_at = datetime.now(UTC)
    runtime.config = EngineConfig()
    now = datetime.now(UTC)
    runtime._workers = {
        name: WorkerHealth(now, now, alive=True)
        for name in ("pb-decisions", "pb-account", "pb-trade-updates", "pb-stocks", "pb-crypto")
    }
    runtime.health = lambda: SimpleNamespace(queue_age_seconds=0.0)
    runtime.engine = SimpleNamespace(has_data_gaps=False)
    runtime._engine_has_market_gap = lambda _market: False
    return runtime


def test_market_worker_failure_is_isolated_and_shared_failure_is_global() -> None:
    runtime = _readiness_runtime()
    runtime._workers["pb-crypto"].alive = False
    assert runtime._entry_readiness_reasons(Market.EQUITY) == ()
    assert "pb-crypto" in ",".join(runtime._entry_readiness_reasons(Market.CRYPTO))
    runtime._workers["pb-decisions"].alive = False
    assert "pb-decisions" in ",".join(runtime._entry_readiness_reasons(Market.EQUITY))
    assert "pb-decisions" in ",".join(runtime._entry_readiness_reasons(Market.CRYPTO))


def test_verified_market_event_clears_only_that_market_halt() -> None:
    runtime = _readiness_runtime()
    runtime._workers["pb-crypto"].last_error = "disconnect"
    runtime._workers["pb-crypto"].restart_count = 6
    runtime._market_entry_halts[Market.CRYPTO] = "pb-crypto_restart_limit"
    runtime._market_entry_halts[Market.EQUITY] = "equity_reason"
    runtime._mark_market_stream_recovered(Market.CRYPTO)
    assert runtime._market_entry_halts[Market.CRYPTO] is None
    assert runtime._market_entry_halts[Market.EQUITY] == "equity_reason"
    assert runtime._workers["pb-crypto"].restart_count == 0


def _crypto_bar() -> Bar:
    return Bar(
        datetime(2026, 7, 28, 12, tzinfo=UTC),
        "BTC/USD",
        "1m",
        100,
        101,
        99,
        100,
        1000,
        Market.CRYPTO,
    )


def test_transient_crypto_protection_failure_clears_on_next_verified_pass() -> None:
    """A one-off watcher failure must halt entries only until the next
    verified price pass proves health; it must not brick crypto entries for
    the whole process lifetime."""
    runtime = _readiness_runtime()
    runtime._market_entry_halts[Market.CRYPTO] = "crypto_protection_failure"
    attempts = {"count": 0}

    def flaky(_observation: object) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("transient api blip")

    runtime.broker = SimpleNamespace(process_crypto_price=flaky)
    bar = _crypto_bar()

    asyncio.run(runtime._process_crypto_protection(bar, source="one_minute_bar"))
    assert runtime._market_entry_halts[Market.CRYPTO] == "crypto_protection_failure"
    asyncio.run(runtime._process_crypto_protection(bar, source="one_minute_bar"))
    assert runtime._market_entry_halts[Market.CRYPTO] is None
    assert attempts["count"] == 2


def test_ambiguous_crypto_exit_halt_survives_verified_passes() -> None:
    """Ambiguity is a reconciliation matter: a successful watcher pass must
    never clear an ambiguous_crypto_exit halt."""
    runtime = _readiness_runtime()
    runtime._market_entry_halts[Market.CRYPTO] = "ambiguous_crypto_exit"
    runtime.broker = SimpleNamespace(process_crypto_price=lambda _observation: None)
    bar = _crypto_bar()

    asyncio.run(runtime._process_crypto_protection(bar, source="one_minute_bar"))

    assert runtime._market_entry_halts[Market.CRYPTO] == "ambiguous_crypto_exit"


def test_quantity_lookup_failure_is_fail_closed() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.ledger = None
    broker._client = SimpleNamespace(
        get_open_position=lambda _symbol: (_ for _ in ()).throw(TimeoutError())
    )
    broker._asset_cache = {"BTC/USD": SimpleNamespace(min_trade_increment="0.00000001")}
    result = broker.resolve_available_position_qty("BTC/USD", 1.0, attempts=2)
    assert not result.verified
    assert result.available_qty == 0
    assert result.source == "broker_lookup_failed"


def test_fee_activity_is_idempotent_and_realized_pnl_is_net(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    order = OrderIntent(
        "sig",
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        1,
        100,
        95,
        110,
        "test",
        client_order_id="entry",
    )
    ledger.register_entry_order("acct", order, "broker-entry")
    entry = ExecutionEvent(
        "acct",
        "entry",
        "broker-entry",
        None,
        OrderRole.ENTRY,
        "BTC/USD",
        Market.CRYPTO,
        OrderState.FILLED,
        1,
        1,
        100,
        datetime.now(UTC),
        1.0,
        "fee-entry",
    )
    ledger.apply_execution_event(entry)
    ledger.apply_execution_event(entry)
    exit_event = ExecutionEvent(
        "acct",
        "entry-target-exit",
        "broker-exit",
        None,
        OrderRole.TAKE_PROFIT,
        "BTC/USD",
        Market.CRYPTO,
        OrderState.FILLED,
        1,
        1,
        110,
        datetime.now(UTC),
        2.0,
        "fee-exit",
    )
    ledger.apply_execution_event(exit_event)
    ledger.apply_execution_event(exit_event)
    position = ledger.connection.execute(
        """
        SELECT entry_fees, exit_fees, realized_pnl
        FROM managed_positions WHERE account_id='acct' AND entry_client_order_id='entry'
        """
    ).fetchone()
    assert position is not None
    assert position[0] == pytest.approx(1)
    assert position[1] == pytest.approx(2)
    assert position[2] == pytest.approx(7)
    assert ledger.total_fees_for_account("acct") == pytest.approx(3)
    ledger.close()


def test_scanner_unknown_sectors_fill_capacity_and_reject_nan(tmp_path: Path) -> None:
    config = EquityUniverseConfig(
        maximum_stream_symbols=8,
        reference_symbols=("SPY",),
        fallback_trade_symbols=(),
        active_universe_path=str(tmp_path / "active.json"),
        scanner=EquityScannerConfig(maximum_per_sector=1),
    )
    scanner = EquityUniverseScanner(config)
    candidates = [
        EquityCandidate(f"U{index}", 20, 100_000_000, 0.03, 1.0, 2, None) for index in range(10)
    ]
    result = scanner.scan(candidates, data_timestamp=datetime.now(UTC))
    assert len(result.selected_symbols) == 8
    assert result.sector_distribution["unknown"] == 7
    bad = EquityCandidate("BAD", 20, 100_000_000, float("nan"), 1.0, 2)
    assert scanner._filter_reason(bad) == "atr_range"


def test_active_universe_stale_and_digest_mismatch_fall_back(tmp_path: Path) -> None:
    active = tmp_path / "active.json"
    config = EquityUniverseConfig(
        reference_symbols=("SPY",),
        fallback_trade_symbols=("AAPL",),
        active_universe_path=str(active),
        active_universe_max_age_hours=1,
    )
    scanner = EquityUniverseScanner(config)
    old = datetime.now(UTC) - timedelta(hours=2)
    result = scanner.scan([], data_timestamp=old, selection_timestamp=old)
    scanner.persist(result)
    assert validate_active_universe(config)[1] == "stale"
    assert load_active_symbols(config) == ("SPY", "AAPL")
    payload = json.loads(active.read_text())
    payload["selection_timestamp"] = datetime.now(UTC).isoformat()
    payload["config_digest"] = "wrong"
    active.write_text(json.dumps(payload))
    assert validate_active_universe(config)[1] == "digest_mismatch"


def test_critical_journal_failure_opens_global_breaker() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = None
    runtime._on_critical_journal_failure("disk full")
    assert runtime._circuit_breaker_reason == "critical_journal_failure"


def test_ambiguous_recovered_crypto_protection_does_not_escape_stream_callback() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    order = OrderIntent(
        "signal",
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        0.1,
        100,
        95,
        110,
        "test",
    )
    runtime.broker = SimpleNamespace(
        account_id="acct",
        protect_recovered_crypto_entry=lambda _order: (_ for _ in ()).throw(
            AmbiguousOrderState("unconfirmed")
        ),
    )
    runtime.execution = SimpleNamespace(
        apply=lambda _event: ExecutionTransition(
            None,
            OrderState.FILLED,
            0.1,
            0.1,
            0.0,
            order.client_order_id,
        )
    )
    runtime.engine = SimpleNamespace(
        orders=SimpleNamespace(orders={order.client_order_id: SimpleNamespace(order=order)})
    )
    runtime.ledger = SimpleNamespace()
    runtime.ai_journal = None
    runtime._circuit_breaker_reason = None
    runtime._market_entry_halts = {Market.CRYPTO: None}
    update = TradeUpdate(
        "fill",
        order.client_order_id,
        "broker-entry",
        None,
        OrderRole.ENTRY,
        order.symbol,
        order.market,
        order.qty,
        order.qty,
        order.entry_price,
        datetime.now(UTC),
    )

    asyncio.run(runtime._on_trade_update(update))

    assert runtime._market_entry_halts[Market.CRYPTO] == "protection_reconciliation_required"


def test_stop_cancel_pending_then_canceled_needs_no_manual_intervention() -> None:
    statuses = iter(("pending_cancel", "canceled"))
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.ledger = None
    broker.crypto_stop_cancel_timeout_seconds = 0.2
    broker.crypto_stop_cancel_poll_interval_seconds = 0.001
    broker._account_id = "acct"
    broker._client = SimpleNamespace(
        get_order_by_id=lambda _order_id: SimpleNamespace(
            status=next(statuses), filled_qty=0, filled_avg_price=None
        )
    )
    manual: list[str] = []
    broker.ledger = SimpleNamespace(
        apply_execution_event=lambda _event: None,
        mark_manual_intervention=lambda _account, entry: manual.append(entry),
    )
    position = SimpleNamespace(
        canonical_symbol="BTC/USD",
        entry_client_order_id="entry",
        protective_stop_client_order_id="entry-stop",
        owned_qty=1,
    )
    _, status = broker._poll_stop_cancellation(position, "stop-id")
    assert status == "canceled"
    assert manual == []


def test_unresolved_stop_cancel_requires_reconciliation() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.ledger = None
    broker.crypto_stop_cancel_timeout_seconds = 0.01
    broker.crypto_stop_cancel_poll_interval_seconds = 0.001
    broker._account_id = "acct"
    broker._client = SimpleNamespace(
        get_order_by_id=lambda _order_id: SimpleNamespace(
            status="accepted", filled_qty=0, filled_avg_price=None
        )
    )
    manual: list[str] = []
    broker.ledger = SimpleNamespace(
        apply_execution_event=lambda _event: None,
        mark_manual_intervention=lambda _account, entry: manual.append(entry),
    )
    position = SimpleNamespace(
        canonical_symbol="BTC/USD",
        entry_client_order_id="entry",
        protective_stop_client_order_id="entry-stop",
        owned_qty=1,
    )
    with pytest.raises(AmbiguousOrderState, match="unconfirmed"):
        broker._poll_stop_cancellation(position, "stop-id")
    assert manual == ["entry"]


@pytest.mark.parametrize(
    ("client_id", "parent_id", "order_type", "stop_price", "expected_role"),
    [
        ("custom-bracket-entry", None, "market", None, OrderRole.ENTRY),
        ("equity-stop", "parent", "stop", 95, OrderRole.PROTECTIVE_STOP),
        ("equity-target", "parent", "limit", None, OrderRole.TAKE_PROFIT),
        ("parayabuck-entry-stop", None, "stop_limit", 95, OrderRole.PROTECTIVE_STOP),
        ("parayabuck-entry-target-exit", None, "market", None, OrderRole.TAKE_PROFIT),
        ("parayabuck-entry-emergency-exit", None, "market", None, OrderRole.EMERGENCY_EXIT),
        ("manual-order", None, "market", None, OrderRole.MANUAL_EXIT),
    ],
)
def test_trade_update_role_mapping(
    client_id: str,
    parent_id: str | None,
    order_type: str,
    stop_price: float | None,
    expected_role: OrderRole,
) -> None:
    updates: list[Any] = []

    async def handler(update: Any) -> None:
        updates.append(update)

    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    stream._handler = handler
    raw = {
        "event": "fill",
        "timestamp": datetime.now(UTC),
        "order": {
            "id": "broker-id",
            "client_order_id": client_id,
            "parent_order_id": parent_id,
            "symbol": "BTC/USD" if "equity-" not in client_id else "AAPL",
            "asset_class": "crypto" if "equity-" not in client_id else "us_equity",
            "type": order_type,
            "order_class": "bracket" if client_id == "custom-bracket-entry" else "",
            "stop_price": stop_price,
            "qty": 1,
            "filled_qty": 1,
            "filled_avg_price": 100,
        },
    }
    asyncio.run(stream._on_update(raw))
    assert updates[0].role == expected_role
