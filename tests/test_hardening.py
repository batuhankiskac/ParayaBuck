from __future__ import annotations

import math
import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Market,
    PositionSnapshot,
)
from parayabuck.data.aggregator import TimeframeAggregator
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.markets.symbols import (
    canonical_symbol,
    normalize_symbol,
    same_symbol,
)
from parayabuck.runtime import EngineRuntime, WorkerHealth, _Event, _EventQueue, _symbol_batches
from parayabuck.utils import is_crypto
from parayabuck.web.managers.engine import EngineManager, LiveArmToken, _atomic_update_credentials
from tests.helpers import make_broker, web_client

pytestmark = pytest.mark.paper_chaos

_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}


def test_market_models_reject_non_finite_values() -> None:
    aware = datetime.now(UTC)
    with pytest.raises(ValueError, match="finite"):
        Bar(aware, "AAPL", "5m", 100, 101, 99, 100, math.nan, Market.EQUITY)


def test_execution_ledger_drops_the_unread_legacy_decision_journal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE orders (
            client_order_id TEXT NOT NULL, timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
            state TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (client_order_id, timestamp, state)
        );
        INSERT INTO orders VALUES (
            'legacy-order', '2026-07-27T12:00:00+00:00', 'AAPL',
            'submitted', 'broker_submit', '{}'
        );
        """
    )
    connection.close()

    ledger = ExecutionLedger(str(path))

    order_columns = {str(row[1]) for row in ledger.connection.execute("PRAGMA table_info(orders)")}
    assert "broker_order_id" in order_columns
    assert "parent_broker_order_id" in order_columns
    tables = {str(row[0]) for row in ledger.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "decision_orders" not in tables
    ledger.close()


def _bar(
    timestamp: datetime,
    symbol: str = "AAPL",
    close: float = 100,
    market: Market = Market.EQUITY,
) -> Bar:
    return Bar(timestamp, symbol, "5m", close, close + 1, close - 1, close, 100, market)


def test_canonical_crypto_symbols_and_adapters() -> None:
    legacy = normalize_symbol("btcusd", Market.CRYPTO)
    assert legacy.canonical == "BTC/USD"
    assert legacy.market_data_symbol == "BTC/USD"
    assert legacy.trading_symbol == "BTC/USD"
    assert legacy.asset_lookup_symbol == "BTCUSD"
    assert same_symbol("BTCUSD", "btc/usd")
    assert canonical_symbol("aapl") == "AAPL"
    assert not same_symbol("AAPL", "AAPL/USD")


def test_legacy_crypto_symbols_outside_the_allowlist_canonicalize() -> None:
    """Regression: an Alpaca position/order symbol like ``PAXGUSD`` (base
    not in the old allowlist) crashed account reconciliation with 'Cannot
    infer crypto base/quote'; the engine degraded on every refresh once a
    PAXG position existed. Explicit CRYPTO market must accept any base."""
    assert normalize_symbol("paxgusd", Market.CRYPTO).canonical == "PAXG/USD"
    assert canonical_symbol("PAXGUSD", Market.CRYPTO) == "PAXG/USD"
    assert canonical_symbol("USDGUSD", Market.CRYPTO) == "USDG/USD"
    assert canonical_symbol("BTCUSDT", Market.CRYPTO) == "BTC/USDT"
    # Universe bases now infer to CRYPTO in no-slash legacy form too.
    assert is_crypto("PAXGUSD")
    assert normalize_symbol("PAXGUSD").market == Market.CRYPTO
    # A bare quote or bare base is still not a pair.
    with pytest.raises(ValueError):
        canonical_symbol("USD", Market.CRYPTO)
    with pytest.raises(ValueError):
        canonical_symbol("PAXG", Market.CRYPTO)


def test_warmup_symbol_batches_stay_below_alpaca_row_limit() -> None:
    symbols = [f"SYM{index}" for index in range(21)]
    batches = list(_symbol_batches(symbols))

    assert [len(batch) for batch in batches] == [8, 8, 5]
    assert [symbol for batch in batches for symbol in batch] == symbols


def test_runtime_leaves_warmup_only_when_every_symbol_is_ready() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.symbols = ["AAPL", "DOG"]
    runtime._not_ready_symbols = {"AAPL", "DOG"}
    runtime._running = True
    runtime._state = "warming_up"
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            symbols=("AAPL", "DOG"),
            strategy=SimpleNamespace(minimum_history_bars=250),
            history={
                "AAPL": [SimpleNamespace(timestamp=datetime(2025, 1, 1, tzinfo=UTC))] * 250,
                "DOG": [SimpleNamespace(timestamp=datetime(2025, 1, 1, tzinfo=UTC))] * 241,
            },
        ),
    )

    runtime._refresh_readiness()

    assert runtime._not_ready_symbols == {"DOG"}
    assert runtime._state == "warming_up"

    runtime._portfolio_runtimes[0].history["DOG"] = [SimpleNamespace(timestamp=datetime(2025, 1, 1, tzinfo=UTC))] * 250
    runtime._refresh_readiness()
    assert runtime._not_ready_symbols == set()
    assert runtime._state == "running"


def test_runtime_stays_in_warmup_when_no_symbol_is_ready() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.symbols = ["DOG"]
    runtime._not_ready_symbols = {"DOG"}
    runtime._running = True
    runtime._state = "warming_up"
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            symbols=("DOG",),
            strategy=SimpleNamespace(minimum_history_bars=250),
            history={"DOG": [SimpleNamespace(timestamp=datetime(2025, 1, 1, tzinfo=UTC))] * 241},
        ),
    )

    runtime._refresh_readiness()

    assert runtime._not_ready_symbols == {"DOG"}
    assert runtime._state == "warming_up"


def test_blocking_trade_update_worker_stays_healthy_while_thread_is_alive() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    stale = now - timedelta(minutes=10)
    runtime.symbols = ["AAPL"]
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=15,
        maximum_account_age_for_entry_seconds=30,
        maximum_queue_age_seconds=30,
    )
    runtime._workers = {
        "pb-decisions": WorkerHealth(now, alive=True),
        "pb-account": WorkerHealth(now, alive=True),
        "pb-trade-updates": WorkerHealth(stale, alive=True),
        "pb-stocks": WorkerHealth(stale, last_event_at=now, alive=True),
    }
    runtime._running = True
    runtime._state = "running"
    runtime._last_account_at = now
    runtime._oldest_received_at = None
    runtime._circuit_breaker_reason = None
    runtime._events = SimpleNamespace(qsize=lambda: 0)
    runtime._dropped_events = 0
    runtime._coalesced_events = 0

    assert runtime.health().healthy is True
    assert runtime.health().state == "running"


def test_stale_stock_stream_event_during_the_session_degrades_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import parayabuck.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "is_session_open", lambda _now: True)
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    stale = now - timedelta(minutes=10)
    runtime.symbols = ["AAPL"]
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=15,
        maximum_account_age_for_entry_seconds=30,
        maximum_queue_age_seconds=30,
    )
    runtime._workers = {
        "pb-decisions": WorkerHealth(now, alive=True),
        "pb-account": WorkerHealth(now, alive=True),
        "pb-trade-updates": WorkerHealth(now, alive=True),
        "pb-stocks": WorkerHealth(stale, last_event_at=stale, alive=True),
    }
    runtime._running = True
    runtime._state = "running"
    runtime._last_account_at = now
    runtime._oldest_received_at = None
    runtime._circuit_breaker_reason = None
    runtime._events = SimpleNamespace(qsize=lambda: 0)
    runtime._dropped_events = 0
    runtime._coalesced_events = 0

    health = runtime.health()

    assert health.healthy is False
    assert health.state == "degraded"
    assert health.markets["equity"]["stream_healthy"] is False


def _daily(timestamp: datetime, symbol: str) -> Bar:
    return Bar(timestamp, symbol, "1d", 100, 101, 99, 100, 1_000, Market.EQUITY)


def test_queue_overflow_never_evicts_a_queued_decision_bar() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._events = _EventQueue(maxsize=2)
    runtime._dropped_events = 0
    runtime._coalesced_events = 0
    runtime._oldest_received_at = None
    runtime._circuit_breaker_reason = None
    timestamp = datetime.now(UTC)
    runtime._events.put_nowait(_Event(_daily(timestamp, "SPY"), timestamp))
    runtime._events.put_nowait(_Event(_daily(timestamp, "QQQ"), timestamp))

    runtime._put_event(_daily(timestamp, "QLD"))

    assert [item.bar.symbol for item in runtime._events.drain_for_test() if item is not None] == ["SPY", "QQQ"]
    assert runtime._circuit_breaker_reason == "final_market_event_queue_overflow"
    assert runtime._dropped_events == 1


def test_full_queue_coalesces_a_repeat_of_the_same_decision_bar() -> None:
    queue_ = _EventQueue(maxsize=1)
    timestamp = datetime.now(UTC)
    queue_.put_nowait(_Event(_daily(timestamp, "QQQ"), timestamp))
    revised = Bar(timestamp, "QQQ", "1d", 100, 101, 99, 100.5, 1_000, Market.EQUITY)

    assert queue_.put_market_event(_Event(revised, timestamp)) == "coalesced"
    assert queue_.put_market_event(_Event(_daily(timestamp, "QLD"), timestamp)) == "overflow"
    assert [item.bar.close for item in queue_.drain_for_test() if item is not None] == [100.5]


def test_queue_overflow_breaker_marks_runtime_halted() -> None:
    """The overflow breaker alerts "runtime halted"; the runtime's own state
    must agree so health() reports halted and reset_safety_lock can restore
    running."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._events = _EventQueue(maxsize=1)
    runtime._dropped_events = 0
    runtime._coalesced_events = 0
    runtime._oldest_received_at = None
    runtime._circuit_breaker_reason = None
    runtime._state = "running"
    timestamp = datetime.now(UTC)
    runtime._events.put_nowait(_Event(_daily(timestamp, "QQQ"), timestamp))

    runtime._put_event(_daily(timestamp, "QLD"))

    assert runtime._circuit_breaker_reason == "final_market_event_queue_overflow"
    assert runtime._state == "halted"


class _ClearingHalts(dict):  # type: ignore[type-arg]
    """Simulates another thread clearing the halt right after it is read."""

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        value = super().get(key, default)
        self[key] = None
        return value


def test_entry_readiness_never_reports_a_concurrently_cleared_halt_as_none() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.config = SimpleNamespace(maximum_queue_age_seconds=30, maximum_account_age_for_entry_seconds=300)
    runtime._last_account_at = datetime.now(UTC)
    runtime._circuit_breaker_reason = None
    runtime._workers = {}
    runtime._state = "running"
    runtime._market_entry_halts = _ClearingHalts({Market.EQUITY: "rebalance_reconciliation_required"})

    reasons = runtime._entry_readiness_reasons(Market.EQUITY, health=SimpleNamespace(queue_age_seconds=0))

    assert reasons == ("rebalance_reconciliation_required",)
    assert runtime._entry_readiness_reasons(Market.EQUITY, health=SimpleNamespace(queue_age_seconds=0)) == ()


def test_failed_no_trade_journal_emit_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken journal must not silently drop the operator's "why did
    nothing trade" record: the readiness-blocked path logs the failure."""
    from parayabuck.monitoring import journal as journal_module

    def broken_emit(_event: object) -> None:
        raise OSError("journal unwritable")

    monkeypatch.setattr(journal_module, "emit_journal", broken_emit)
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(timeframe="1d", name="synthetic"),
            symbols=("AAPL",),
        ),
    )
    runtime._entry_readiness_reasons = lambda _market: ("account_snapshot_stale",)
    bar = Bar(datetime(2025, 1, 2, tzinfo=UTC), "AAPL", "1d", 100, 101, 99, 100, 1_000, Market.EQUITY)

    runtime._process_portfolio_timestamp([bar])

    assert "No-trade journal emit failed for market=equity" in caplog.text


def test_equity_hour_buckets_anchor_at_0930_new_york_across_dst() -> None:
    aggregator = TimeframeAggregator("1h")
    winter = datetime(2025, 1, 2, 15, 31, tzinfo=UTC)  # 10:31 ET
    summer = datetime(2025, 7, 2, 14, 31, tzinfo=UTC)  # 10:31 ET
    assert aggregator._bucket(winter) == datetime(2025, 1, 2, 15, 30, tzinfo=UTC)
    assert aggregator._bucket(summer) == datetime(2025, 7, 2, 14, 30, tzinfo=UTC)


def test_external_position_is_not_modified_by_default(tmp_path: Path) -> None:
    broker = make_broker(
        tmp_path,
        client=SimpleNamespace(
            _request=lambda *_args, **_kwargs: [],
            get=lambda *_args, **_kwargs: [],
        ),
    )
    broker._account_id = "acct"
    broker.account = lambda: AccountSnapshot(
        1_000,
        10_000,
        10_000,
        0,
        positions=(PositionSnapshot("AAPL", 10, 100, 101, Market.EQUITY),),
    )
    # The client has no order-submission surface: reconciliation only
    # reports the unowned position, it never trades or protects it.
    assert broker.reconcile()["BROKER_STALE"] == ["AAPL"]


def test_atomic_credentials_preserve_unmanaged_fields_and_never_touch_example(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    env.write_text("UNMANAGED=value\nALPACA_API_KEY=old\n")
    example.write_text("ALPACA_SECRET_KEY=your_secret_key_here\n")
    before = example.read_bytes()
    _atomic_update_credentials(env, "new-key", "new-secret")
    written = env.read_text()
    assert "UNMANAGED=value" in written
    # Values are quoted so '#'/'=' inside a key or secret cannot corrupt the
    # dotenv file on the next load.
    assert 'ALPACA_API_KEY="new-key"' in written
    assert 'ALPACA_SECRET_KEY="new-secret"' in written
    assert example.read_bytes() == before
    assert os.stat(env).st_mode & 0o777 == 0o600


def test_atomic_credentials_quote_values_with_special_characters(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("")
    _atomic_update_credentials(env, "key#with=sign", "secret with spaces")
    assert 'ALPACA_API_KEY="key#with=sign"' in env.read_text()
    assert 'ALPACA_SECRET_KEY="secret with spaces"' in env.read_text()


def test_direct_settings_call_cannot_auto_restart_into_live(tmp_path: Path, monkeypatch: Any) -> None:
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.env_path = tmp_path / ".env"
    manager.engine_state = "running"
    manager._live_armed = True
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
                "runtime": {"mode": "paper", "execution_timeframe": "5m"},
                "symbols": ["AAPL"],
            }
        )
    )
    response = web_client(manager).post("/api/settings", json={"paper_mode": False, "restart_engine": True})
    assert response.status_code == 400


def test_aggregator_waits_for_finalization_grace() -> None:
    aggregator = TimeframeAggregator("5m", finalization_grace_seconds=2)
    start = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    aggregator.update(_bar(start))
    assert aggregator.flush_until(start + timedelta(minutes=5, seconds=1)) == []
    assert len(aggregator.flush_until(start + timedelta(minutes=5, seconds=2))) == 1


def test_aggregator_late_minute_does_not_close_the_forming_bucket_early() -> None:
    aggregator = TimeframeAggregator("5m")
    start = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)  # 10:00 New York

    def minute(offset: int, price: float) -> Bar:
        return Bar(start + timedelta(minutes=offset), "SPY", "1m", price, price, price, price, 1, Market.EQUITY)

    emitted = [aggregator.update(minute(offset, 100 + offset)) for offset in range(7)]
    assert [bar.timestamp for bar in emitted if bar is not None] == [start]
    # A late minute of the already-closed 10:00 bucket arrives mid 10:05 bucket.
    assert aggregator.update(minute(4, 999)) is None
    for offset in (7, 8, 9):
        assert aggregator.update(minute(offset, 100 + offset)) is None
    closed = aggregator.update(minute(10, 110))
    assert closed is not None
    assert closed.timestamp == start + timedelta(minutes=5)
    assert (closed.open, closed.close, closed.volume) == (105, 109, 5)


@pytest.mark.parametrize("timeframe", ["5m", "1d"])
def test_aggregator_late_minute_after_a_flush_does_not_emit_a_second_final_candle(timeframe: str) -> None:
    aggregator = TimeframeAggregator(timeframe, finalization_grace_seconds=2.0)
    start = datetime(2026, 9, 28, 19, 55, tzinfo=UTC)  # 15:55 New York

    def minute(offset: int, price: float) -> Bar:
        return Bar(start + timedelta(minutes=offset), "SPY", "1m", price, price, price, price, 10, Market.EQUITY)

    for offset in range(4):
        assert aggregator.update(minute(offset, 100 + offset)) is None
    session_close = start + timedelta(minutes=5)
    flushed = aggregator.flush_session_close(session_close)
    assert len(flushed) == 1
    # The 15:59 minute arrives after the session-close flush already
    # emitted its bucket: it must not reopen that bucket as a stub.
    assert aggregator.update(minute(4, 999)) is None
    assert aggregator.flush_session_close(session_close + timedelta(seconds=1)) == []
    assert aggregator.flush_until(session_close + timedelta(days=2)) == []


def test_aggregator_late_minute_after_a_grace_flush_does_not_emit_a_stub_candle() -> None:
    aggregator = TimeframeAggregator("5m", finalization_grace_seconds=2.0)
    start = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)  # 10:00 New York

    def minute(offset: int, price: float) -> Bar:
        return Bar(start + timedelta(minutes=offset), "SPY", "1m", price, price, price, price, 1, Market.EQUITY)

    for offset in range(4):
        assert aggregator.update(minute(offset, 100 + offset)) is None
    flushed = aggregator.flush_until(start + timedelta(minutes=5, seconds=3))
    assert [bar.timestamp for bar in flushed] == [start]
    assert aggregator.update(minute(4, 999)) is None
    for offset in range(5, 10):
        assert aggregator.update(minute(offset, 100 + offset)) is None
    closed = aggregator.update(minute(10, 110))
    assert closed is not None
    assert closed.timestamp == start + timedelta(minutes=5)
    assert (closed.open, closed.close, closed.volume) == (105, 109, 5)


def test_aggregator_logs_a_dropped_late_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dropped late minute is permanent data loss for the closed candle and
    must not be silent, or a too-short finalization grace goes unnoticed."""
    import parayabuck.data.aggregator as aggregator_module

    warnings: list[str] = []
    monkeypatch.setattr(
        aggregator_module,
        "log",
        SimpleNamespace(warning=lambda message, *args: warnings.append(message % args)),
    )
    aggregator = TimeframeAggregator("5m", finalization_grace_seconds=2.0)
    start = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)  # 10:00 New York

    def minute(offset: int) -> Bar:
        return Bar(start + timedelta(minutes=offset), "SPY", "1m", 100, 100, 100, 100, 1, Market.EQUITY)

    for offset in range(4):
        aggregator.update(minute(offset))
    aggregator.flush_until(start + timedelta(minutes=5, seconds=3))
    assert warnings == []
    assert aggregator.update(minute(4)) is None
    assert len(warnings) == 1
    assert "symbol=SPY timeframe=5m" in warnings[0]


def test_safety_lock_reset_requires_clean_reconciliation() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "ambiguous_order_state"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: None}
    reconciled: list[bool] = []
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: reconciled.append(True) or dict(_CLEAN_RECONCILIATION),
        account=lambda: account,
    )
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (),
        clear_safety_incident=lambda _account, _reason=None: None,
    )
    persisted: list[AccountSnapshot] = []
    runtime._persist_risk_state = persisted.append
    runtime._entry_risk_latch = {Market.EQUITY: None}

    runtime.reset_safety_lock()

    assert reconciled == [True]
    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    assert persisted == [account]
    assert runtime._market_entry_halts == {Market.EQUITY: None}


def test_safety_lock_reset_is_granular_per_market_halt() -> None:
    """Global ownership drift + a blocked rebalance: resolving ownership
    must NOT clear the market halt whose rebalance state is unresolved."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "ownership_drift"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: "rebalance_reconciliation_required"}
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),  # ownership reconciliation succeeds
        account=lambda: account,
    )
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (),
        clear_safety_incident=lambda _account, _reason=None: None,
    )
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_risk_latch = {Market.EQUITY: None}
    # The blocked rebalance still has working orders (not provably terminal).
    runtime._rebalance_state_reconciled = lambda _market: False

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None  # ownership resolved
    # The blocked rebalance halt was NOT erased: no terminal-state proof.
    assert runtime._market_entry_halts[Market.EQUITY] == "rebalance_reconciliation_required"

    # Once the rebalance orders are provably terminal (verified
    # reconciliation), a later reset clears the market halt too.
    runtime._rebalance_state_reconciled = lambda _market: True
    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime.reset_safety_lock()
    assert runtime._market_entry_halts[Market.EQUITY] is None


def test_safety_lock_reset_retains_unrecognized_halt() -> None:
    """A market halt with no verified recovery path is never erased."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: "unknown_halt_reason"}
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),
        account=lambda: account,
    )
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (),
        clear_safety_incident=lambda _account, _reason=None: None,
    )
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_risk_latch = {Market.EQUITY: None}

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    assert runtime._market_entry_halts[Market.EQUITY] == "unknown_halt_reason"


def test_safety_lock_reset_refuses_manual_intervention() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "protection_failure"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: dict(_CLEAN_RECONCILIATION))
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (SimpleNamespace(status="manual_intervention_required"),),
        clear_safety_incident=lambda _account, _reason=None: None,
    )

    with pytest.raises(ValueError, match="manual intervention"):
        runtime.reset_safety_lock()


def test_live_arm_token_is_bound_to_credentials_and_expires_on_change(tmp_path: Path, monkeypatch: Any) -> None:
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.config_path.write_text(yaml.safe_dump({"alpaca": {"paper": True}, "symbols": ["AAPL"]}))
    credentials = ["key-a", "secret-a"]
    monkeypatch.setattr(manager, "get_credentials", lambda: tuple(credentials))
    now = datetime.now(UTC)
    manager._pending_live_arm = LiveArmToken(
        "account-a",
        "1234",
        manager._credential_fingerprint(*credentials),
        manager._config_digest(paper=False),
        now,
        now + timedelta(minutes=1),
    )
    manager.confirm_live_arming("LIVE 1234")
    manager._require_valid_live_arm(*credentials)

    credentials[1] = "secret-b"
    with pytest.raises(ValueError, match="fresh confirmation"):
        manager._require_valid_live_arm(*credentials)
    assert not manager._live_armed


def test_session_close_flush_waits_for_the_finalization_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 15:59 minute may arrive just after 16:00:00: the session-close
    flush honors a close-specific grace (longer than flush_until's), so the
    daily and 15:55 5m candles still include it."""
    import parayabuck.runtime as runtime_module
    from parayabuck.markets.timeframes import NEW_YORK

    close_grace = runtime_module._SESSION_CLOSE_FLUSH_GRACE_SECONDS

    clock = {"now": datetime(2026, 9, 25, 16, 0, 0, 500_000, tzinfo=NEW_YORK).astimezone(UTC)}

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return clock["now"]

    monkeypatch.setattr(runtime_module, "datetime", _Clock)
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.config = SimpleNamespace(data_finalization_grace_seconds=2.0)
    runtime._ingestion_lock = threading.RLock()
    runtime.aggregators = {tf: TimeframeAggregator(tf, 2.0) for tf in ("5m", "1d")}
    emitted: list[Bar] = []
    runtime._emit_finalized_bar = lambda _tf, bar: emitted.append(bar)  # type: ignore[method-assign]

    def minute(hh: int, mm: int, close: float) -> Bar:
        stamp = datetime(2026, 9, 25, hh, mm, tzinfo=NEW_YORK).astimezone(UTC)
        return Bar(stamp, "SPY", "1m", close, close, close, close, 100, Market.EQUITY)

    for tf_aggregator in runtime.aggregators.values():
        for mm, close in ((55, 100.0), (56, 101.0), (57, 102.0), (58, 103.0)):
            tf_aggregator.update(minute(15, mm, close))

    runtime._flush_aggregators()
    assert emitted == []  # inside the grace: not yet final

    for tf_aggregator in runtime.aggregators.values():
        tf_aggregator.update(minute(15, 59, 104.0))  # late 15:59 minute
    clock["now"] = datetime(2026, 9, 25, 16, 0, 0, tzinfo=NEW_YORK).astimezone(UTC) + timedelta(
        seconds=close_grace + 0.1
    )
    runtime._flush_aggregators()

    assert sorted(bar.timeframe for bar in emitted) == ["1d", "5m"]
    assert all(bar.close == 104.0 for bar in emitted)
