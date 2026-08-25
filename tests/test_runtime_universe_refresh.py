from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import Config
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.runtime import EngineRuntime, WorkerHealth


def test_invalid_dynamic_snapshot_blocks_entries_unless_fallback_is_explicit() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.app_config = Config()
    runtime.universe_preflight = SimpleNamespace(equity_status="refresh_failed", crypto_status="disabled")

    assert runtime._initial_universe_halt(Market.EQUITY) == "invalid_dynamic_universe"

    runtime.app_config = replace(
        runtime.app_config,
        universe=replace(
            runtime.app_config.universe,
            equity=replace(
                runtime.app_config.universe.equity,
                allow_static_fallback_trading=True,
            ),
        ),
    )
    assert runtime._initial_universe_halt(Market.EQUITY) is None


def test_hot_swap_pins_positions_and_open_orders_within_stream_capacity() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        universe=replace(
            base.universe,
            equity=replace(base.universe.equity, maximum_stream_symbols=4),
        ),
    )
    runtime._symbols_by_market = {
        Market.EQUITY: {"SPY", "AAPL", "QQQ", "OLD"},
        Market.CRYPTO: set(),
    }
    runtime.symbols = sorted(runtime._symbols_by_market[Market.EQUITY])
    runtime._not_ready_symbols = set()
    runtime._ingestion_lock = threading.Lock()
    runtime._portfolio_lock = threading.RLock()
    runtime._state_lock = threading.RLock()
    runtime._historical = object()
    runtime.broker = SimpleNamespace(
        account_id="account",
        account=lambda: AccountSnapshot(
            10_000,
            10_000,
            10_000,
            0.0,
            positions=(PositionSnapshot("AAPL", 1, 100, 100, Market.EQUITY, ownership="managed", managed_qty=1),),
        ),
        portfolio_open_order_symbols=lambda: {"QQQ"},
    )
    runtime.ledger = SimpleNamespace(managed_entries_for_account=lambda _account: [])
    updated: list[tuple[str, ...]] = []
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            update_symbols=lambda symbols: updated.append(symbols),
        ),
    )
    runtime.market_state = SimpleNamespace(allowed_trade_symbols=set())
    subscriptions: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    lifecycle: list[str] = []

    def update_subscriptions(stocks: list[str], crypto: list[str]) -> None:
        lifecycle.append("subscribe")
        subscriptions.append((tuple(stocks), tuple(crypto)))

    runtime.stream = SimpleNamespace(update_subscriptions=update_subscriptions)

    def warmup(_history: object, symbols: list[str], *, retry: bool, staged_only: bool = False) -> None:
        assert symbols == ["MSFT"]
        assert not retry
        assert "MSFT" in runtime._not_ready_symbols
        lifecycle.append("warmup")

    runtime._warmup_symbols = warmup
    runtime._refresh_readiness = lambda: lifecycle.append("readiness")

    changes = runtime._apply_runtime_universes({Market.EQUITY: ("SPY", "MSFT", "NVDA", "TSLA")})

    assert runtime._symbols_by_market[Market.EQUITY] == {"SPY", "AAPL", "QQQ", "MSFT"}
    assert changes[Market.EQUITY] == {"added": ("MSFT",), "removed": ("OLD",)}
    assert updated == [("SPY", "AAPL", "QQQ", "MSFT")]
    assert subscriptions == [(("AAPL", "MSFT", "QQQ", "SPY"), ())]
    assert runtime._not_ready_symbols == {"MSFT"}
    assert lifecycle == ["warmup", "subscribe", "readiness"]


def test_hot_swap_does_not_pin_external_positions_by_default() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        universe=replace(
            base.universe,
            equity=replace(base.universe.equity, maximum_stream_symbols=4),
        ),
    )
    runtime.config = base.engine
    runtime._symbols_by_market = {
        Market.EQUITY: {"SPY", "AAPL", "QQQ", "OLD"},
        Market.CRYPTO: set(),
    }
    runtime.symbols = sorted(runtime._symbols_by_market[Market.EQUITY])
    runtime._not_ready_symbols = set()
    runtime._ingestion_lock = threading.Lock()
    runtime._portfolio_lock = threading.RLock()
    runtime._state_lock = threading.RLock()
    runtime._historical = object()
    runtime.broker = SimpleNamespace(
        account_id="account",
        account=lambda: AccountSnapshot(
            10_000,
            10_000,
            10_000,
            0.0,
            positions=(PositionSnapshot("AAPL", 1, 100, 100, Market.EQUITY, ownership="external"),),
        ),
        portfolio_open_order_symbols=lambda: set(),
    )
    runtime.ledger = SimpleNamespace(managed_entries_for_account=lambda _account: [])
    runtime._portfolio_runtimes = ()
    runtime.market_state = SimpleNamespace(allowed_trade_symbols=set())
    runtime.stream = SimpleNamespace(
        update_subscriptions=lambda stocks, crypto: None,
    )
    runtime._warmup_symbols = lambda _history, symbols, *, retry, staged_only=False: None
    runtime._refresh_readiness = lambda: None

    changes = runtime._apply_runtime_universes({Market.EQUITY: ("SPY", "MSFT", "NVDA", "TSLA")})

    assert runtime._symbols_by_market[Market.EQUITY] == {"SPY", "MSFT", "NVDA", "TSLA"}
    assert "AAPL" not in runtime._symbols_by_market[Market.EQUITY]
    assert changes[Market.EQUITY] == {
        "added": ("MSFT", "NVDA", "TSLA"),
        "removed": ("AAPL", "OLD", "QQQ"),
    }


def test_scheduled_refresh_worker_invokes_refresh() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        runtime=replace(base.runtime, scanner_refresh_hours=0.000001),
    )
    runtime._running = True
    runtime._stop_event = threading.Event()
    now = runtime_module.datetime.now(runtime_module.UTC)
    runtime._workers = {"pb-universe-refresh": WorkerHealth(now, now)}
    calls: list[Market | None] = []

    def refresh(target: Market | None = None) -> None:
        calls.append(target)
        runtime._running = False

    runtime._refresh_runtime_universes = refresh
    # Only the crypto interval cadence may fire in this test; the equity
    # pre-market schedule would otherwise trigger a second refresh.
    runtime._next_equity_refresh_at = lambda _now: None
    runtime._universe_refresh_loop()

    assert calls == [Market.CRYPTO]


def test_scheduled_refresh_failure_does_not_busy_spin(monkeypatch) -> None:
    """A failed refresh must still advance its deadline: a stuck past
    deadline would otherwise re-run the scanner on the very next iteration."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        runtime=replace(base.runtime, scanner_refresh_hours=1 / 60),
    )
    runtime._running = True
    runtime._stop_event = threading.Event()
    now = runtime_module.datetime.now(runtime_module.UTC)
    runtime._workers = {"pb-universe-refresh": WorkerHealth(now, now)}
    calls: list[Market | None] = []
    waits = {"count": 0}

    def refresh(target: Market | None = None) -> None:
        calls.append(target)
        raise RuntimeError("scanner unavailable")

    runtime._refresh_runtime_universes = refresh
    runtime._next_equity_refresh_at = lambda _now: None

    # Deterministic clock schedule: m0 seeds next_interval=60; m2 makes the
    # first iteration due (62>=60); m4 (64) comes after the failure-advanced
    # deadline (122), so the second iteration must wait instead of re-firing.
    clock = {"n": 0}
    sequence = {0: 0.0, 1: 61.0, 2: 62.0, 3: 63.0, 4: 64.0}

    def fake_monotonic() -> float:
        index = clock["n"]
        clock["n"] += 1
        return sequence.get(index, 100.0)

    monkeypatch.setattr(runtime_module.time, "monotonic", fake_monotonic)

    def stop_on_second_wait(_timeout: float) -> None:
        waits["count"] += 1
        if waits["count"] >= 2:
            runtime._running = False

    runtime._stop_event.wait = stop_on_second_wait
    runtime._universe_refresh_loop()

    assert calls == [Market.CRYPTO], "a failed refresh must not re-fire immediately"


def test_next_equity_refresh_at_lands_pre_market_on_a_trading_day() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        universe=replace(
            base.universe,
            equity=replace(base.universe.equity, enabled=True, dynamic_selection_enabled=True),
        ),
    )
    from zoneinfo import ZoneInfo

    new_york = ZoneInfo("America/New_York")

    # Monday noon: the next refresh must be Tuesday 06:00 ET, never mid-session.
    monday_noon = datetime(2026, 8, 3, 12, 0, tzinfo=new_york)
    assert runtime._next_equity_refresh_at(monday_noon).astimezone(new_york) == datetime(
        2026, 8, 4, 6, 0, tzinfo=new_york
    )

    # Friday after the close: the next refresh skips the weekend.
    friday_evening = datetime(2026, 8, 7, 20, 0, tzinfo=new_york)
    assert runtime._next_equity_refresh_at(friday_evening).astimezone(new_york) == datetime(
        2026, 8, 10, 6, 0, tzinfo=new_york
    )

    # Early Monday morning (before the pre-market slot): today 06:00 ET.
    monday_early = datetime(2026, 8, 3, 5, 30, tzinfo=new_york)
    assert runtime._next_equity_refresh_at(monday_early).astimezone(new_york) == datetime(
        2026, 8, 3, 6, 0, tzinfo=new_york
    )


def test_next_equity_refresh_at_none_when_equity_universe_is_static() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        universe=replace(
            base.universe,
            equity=replace(base.universe.equity, dynamic_selection_enabled=False),
        ),
    )
    assert runtime._next_equity_refresh_at(datetime.now(UTC)) is None


def test_refresh_failure_with_no_valid_snapshot_halts_entries(monkeypatch) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        universe=replace(
            base.universe,
            crypto=replace(base.universe.crypto, enabled=False),
        ),
    )
    runtime.api_key = "key"
    runtime.secret_key = "secret"
    runtime._universe_refresh_status = {"equity": "success", "crypto": "disabled"}
    runtime._last_universe_refresh_error = {"equity": None, "crypto": None}
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    monkeypatch.setattr(
        runtime_module,
        "refresh_equity_universe",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("scanner unavailable")),
    )
    monkeypatch.setattr(runtime_module, "validate_active_universe", lambda _config: ((), "stale"))

    runtime._refresh_runtime_universes()

    assert runtime._universe_refresh_status["equity"] == "refresh_failed"
    assert runtime._market_entry_halts[Market.EQUITY] == "invalid_dynamic_universe"


def _hot_swap_runtime(equity_symbols: set[str]) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        universe=replace(
            base.universe,
            equity=replace(base.universe.equity, maximum_stream_symbols=4),
        ),
    )
    runtime.config = base.engine
    runtime._symbols_by_market = {
        Market.EQUITY: equity_symbols,
        Market.CRYPTO: set(),
    }
    runtime.symbols = sorted(runtime._symbols_by_market[Market.EQUITY])
    runtime._not_ready_symbols = set()
    runtime._ingestion_lock = threading.Lock()
    runtime._portfolio_lock = threading.RLock()
    runtime._state_lock = threading.RLock()
    runtime._historical = object()
    runtime.broker = SimpleNamespace(
        account_id="account",
        account=lambda: AccountSnapshot(10_000, 10_000, 10_000, 0.0, positions=()),
        portfolio_open_order_symbols=lambda: set(),
    )
    runtime.ledger = SimpleNamespace(managed_entries_for_account=lambda _account: [])
    runtime._portfolio_runtimes = ()
    runtime.market_state = SimpleNamespace(allowed_trade_symbols=set())
    return runtime


def test_hot_swap_stages_warmup_bars_and_seeds_after_membership_commit() -> None:
    """New members are fetched during warmup but seeded only after the
    runtime universe includes them; otherwise the strategy history of a new
    symbol is silently dropped and the member stays warming for the retry."""
    from parayabuck.core.models import Bar
    from parayabuck.portfolio.equity_residual_momentum import EquityResidualMomentum1dV1

    runtime = _hot_swap_runtime({"SPY", "OLD"})
    runtime.aggregators = {"1d": object()}
    runtime_seeds: list[list[Bar]] = []
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            symbols=("SPY", "OLD"),
            strategy=EquityResidualMomentum1dV1({"top_n": 2}),
            update_symbols=lambda symbols: None,
            seed=lambda bars: runtime_seeds.append(bars),
        ),
    )
    seeded_market: list[list[Bar]] = []

    def seed_market(bars: list[Bar]) -> None:
        seeded_market.append(bars)

    runtime.market_state = SimpleNamespace(allowed_trade_symbols=set(), seed=seed_market)
    runtime.stream = SimpleNamespace(
        update_subscriptions=lambda stocks, crypto: None,
    )
    runtime._refresh_readiness = lambda: None

    bar = Bar(
        datetime(2025, 1, 1, tzinfo=UTC),
        "MSFT",
        "1d",
        100.0,
        100.0,
        100.0,
        100.0,
        1_000_000,
        Market.EQUITY,
    )

    def get_bars_batch(symbols, timeframe, *, start, adjustment="raw"):
        # The warmup contract now passes the adjustment basis explicitly
        # (raw for execution state, splits for the strategy signal series).
        return {symbol: [bar] for symbol in symbols}

    runtime._historical = SimpleNamespace(get_bars_batch=get_bars_batch)

    runtime._apply_runtime_universes({Market.EQUITY: ("SPY", "MSFT", "NVDA", "TSLA")})

    assert seeded_market, "staged bars must be seeded into the market state"
    assert all(b.symbol == "MSFT" and b.timeframe == "1d" for b in seeded_market[0]), (
        "staged bars must be the new member's bars"
    )
    assert runtime_seeds, "staged bars must be seeded into the portfolio runtime"


def test_hot_swap_warmup_failure_preserves_previous_universe() -> None:
    # _warmup_symbols is currently best-effort (it swallows per-batch
    # failures), so this guards the transactional contract: IF warmup ever
    # raises, the previous universe must remain fully subscribed and the
    # warming marks must be rolled back before the stream changes.
    runtime = _hot_swap_runtime({"SPY", "OLD"})
    subscribed: list[tuple[list[str], list[str]]] = []
    runtime.stream = SimpleNamespace(
        update_subscriptions=lambda stocks, crypto: subscribed.append((stocks, crypto)),
    )

    def fail_warmup(_history: object, symbols: list[str], *, retry: bool, staged_only: bool = False) -> None:
        raise RuntimeError("historical backfill failed")

    runtime._warmup_symbols = fail_warmup
    runtime._refresh_readiness = lambda: (_ for _ in ()).throw(
        AssertionError("readiness must not run on a failed transition")
    )

    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="historical backfill failed"):
        runtime._apply_runtime_universes({Market.EQUITY: ("SPY", "MSFT", "NVDA", "TSLA")})

    assert runtime._symbols_by_market[Market.EQUITY] == {"SPY", "OLD"}
    assert runtime.symbols == ["OLD", "SPY"]
    assert subscribed == []
    assert runtime._not_ready_symbols == set()


def test_hot_swap_subscribe_failure_preserves_previous_universe() -> None:
    runtime = _hot_swap_runtime({"SPY", "OLD"})
    runtime._warmup_symbols = lambda _history, symbols, *, retry, staged_only=False: None
    runtime._refresh_readiness = lambda: (_ for _ in ()).throw(
        AssertionError("readiness must not run on a failed transition")
    )

    def fail_subscribe(stocks: list[str], crypto: list[str]) -> None:
        raise RuntimeError("websocket refused subscription")

    runtime.stream = SimpleNamespace(update_subscriptions=fail_subscribe)

    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="websocket refused subscription"):
        runtime._apply_runtime_universes({Market.EQUITY: ("SPY", "MSFT", "NVDA", "TSLA")})

    assert runtime._symbols_by_market[Market.EQUITY] == {"SPY", "OLD"}
    assert runtime._not_ready_symbols == set()


def test_hot_swap_commit_failure_restores_stream_and_state() -> None:
    runtime = _hot_swap_runtime({"SPY", "OLD"})
    runtime._warmup_symbols = lambda _history, symbols, *, retry, staged_only=False: None
    runtime._refresh_readiness = lambda: (_ for _ in ()).throw(
        AssertionError("readiness must not run on a failed transition")
    )
    calls: list[tuple[list[str], list[str]]] = []

    def track_subscribe(stocks: list[str], crypto: list[str]) -> None:
        calls.append((list(stocks), list(crypto)))

    runtime.stream = SimpleNamespace(update_subscriptions=track_subscribe)

    fail_count = {"value": 0}

    def fail_update_symbols(
        _symbols: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        fail_count["value"] += 1
        if fail_count["value"] == 1:
            raise ValueError("empty universe rejected")
        return ((), ())

    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            symbols=("SPY", "OLD"),
            update_symbols=fail_update_symbols,
        ),
    )

    import pytest as _pytest

    with _pytest.raises(ValueError, match="empty universe rejected"):
        runtime._apply_runtime_universes({Market.EQUITY: ("SPY", "MSFT", "NVDA", "TSLA")})

    assert runtime._symbols_by_market[Market.EQUITY] == {"SPY", "OLD"}
    assert runtime.symbols == ["OLD", "SPY"]
    assert runtime._not_ready_symbols == set()
    # The stream converged to the new set, then the failed commit rolled it
    # back to the previous subscription.
    assert calls == [
        (["MSFT", "NVDA", "SPY", "TSLA"], []),
        (["OLD", "SPY"], []),
    ]


def _crypto_disabled_config(tmp_path) -> Config:
    base = Config()
    return replace(
        base,
        runtime=replace(base.runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")),
        universe=replace(
            base.universe,
            crypto=replace(
                base.universe.crypto,
                enabled=True,
                dynamic_selection_enabled=True,
            ),
        ),
    )


def test_disabled_crypto_strategy_without_positions_streams_nothing(tmp_path, monkeypatch) -> None:
    """Crypto strategy disabled + no managed crypto: universe discovery stays
    enabled but no crypto market-data stream or observation membership."""
    monkeypatch.setattr(
        runtime_module,
        "ensure_active_universes",
        lambda config, api_key, secret_key: SimpleNamespace(equity_status="success", crypto_status="success"),
    )
    monkeypatch.setattr(
        runtime_module,
        "load_active_symbols",
        lambda config, log_rejection=True: ("SPY", "AAPL"),
    )
    monkeypatch.setattr(
        runtime_module,
        "load_active_crypto_symbols",
        lambda config, log_rejection=True: ("BTC/USD", "ETH/USD", "SOL/USD"),
    )
    runtime = EngineRuntime("key", "secret", _crypto_disabled_config(tmp_path))
    assert runtime._strategy_enabled[Market.CRYPTO] is False
    assert runtime._symbols_by_market[Market.CRYPTO] == set()
    assert runtime._market_enabled[Market.CRYPTO] is False
    # No crypto strategy runtime is configured.
    assert all(strategy.market != Market.CRYPTO for strategy, _s, _c in runtime._strategy_specs)
    runtime._portfolio_repository.close()


def test_disabled_crypto_strategy_with_managed_position_stays_protected(tmp_path, monkeypatch) -> None:
    """Crypto strategy disabled BUT a managed crypto position exists: the
    position remains in the observation membership so it is monitored and
    protected."""
    from parayabuck.execution.portfolio import (
        PortfolioOrderRequest,
        PortfolioOrderSide,
        RiskOverlayDecision,
    )
    from parayabuck.portfolio.models import TargetPortfolio
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository

    monkeypatch.setattr(
        runtime_module,
        "ensure_active_universes",
        lambda config, api_key, secret_key: SimpleNamespace(equity_status="success", crypto_status="success"),
    )
    monkeypatch.setattr(
        runtime_module,
        "load_active_symbols",
        lambda config, log_rejection=True: ("SPY", "AAPL"),
    )
    monkeypatch.setattr(
        runtime_module,
        "load_active_crypto_symbols",
        lambda config, log_rejection=True: ("BTC/USD", "ETH/USD"),
    )
    runtime = EngineRuntime("key", "secret", _crypto_disabled_config(tmp_path))
    assert runtime._symbols_by_market[Market.CRYPTO] == set()
    # Seed a managed crypto position through the repository ("unknown" account
    # is what the runtime resolves before the first broker account() call).
    repo: PortfolioStateRepository = runtime._portfolio_repository
    session = repo.begin_session("unknown", "digest", "paper")
    timestamp = datetime(2026, 8, 1, tzinfo=UTC)
    target = TargetPortfolio(
        strategy_name="crypto",
        strategy_version="1",
        decision_timestamp=timestamp,
        weights=(),
        cash_weight=1.0,
    )
    repo.record_decision(
        session,
        "unknown",
        "digest",
        target,
        RiskOverlayDecision(target, False, (), 0.0, 0.0),
        Market.CRYPTO,
    )
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="pbp-crypto-entry",
            decision_id=repo.store.connection.execute("SELECT decision_id FROM portfolio_decisions LIMIT 1").fetchone()[
                0
            ],
            symbol="BTC/USD",
            market=Market.CRYPTO,
            side=PortfolioOrderSide.BUY,
            quantity=0.5,
            reference_price=100000.0,
            decision_timestamp=timestamp,
            strategy_name="crypto",
            strategy_version="1",
            risk_reducing=False,
        ),
        "submitted",
    )
    repo.apply_trade_update(
        client_order_id="pbp-crypto-entry",
        broker_order_id="broker-entry",
        status="fill",
        cumulative_filled_quantity=0.5,
        average_fill_price=100000.0,
        occurred_at=timestamp,
        activity_id="activity-entry",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    # Replace the network broker with a fake holding the managed position.
    runtime.broker = SimpleNamespace(
        account_id="unknown",
        account=lambda: AccountSnapshot(
            100_000,
            100_000,
            100_000,
            0.0,
            positions=(
                PositionSnapshot(
                    "BTC/USD",
                    0.5,
                    100000,
                    100000,
                    Market.CRYPTO,
                    qty_available=0.5,
                    ownership="managed",
                    managed_qty=0.5,
                ),
            ),
        ),
        portfolio_open_order_symbols=lambda: set(),
    )
    runtime.ledger = SimpleNamespace(managed_entries_for_account=lambda account_id: [])
    runtime._pin_managed_symbols()
    assert "BTC/USD" in runtime._symbols_by_market[Market.CRYPTO]
    assert runtime._market_enabled[Market.CRYPTO] is True
    assert "BTC/USD" in runtime._managed_pin_symbols
    # Configured (strategy) pins are NOT streamed: only the managed pin.
    assert "ETH/USD" not in runtime._symbols_by_market[Market.CRYPTO]
    repo.close()


def test_hot_swap_rollback_failure_halts_entries_and_resyncs() -> None:
    """If the stream rollback itself fails after a hot-swap error, the socket
    membership is unknown: entries halt for the market and the stream is
    forced to reconnect from the authoritative desired membership."""
    runtime = _hot_swap_runtime({"SPY", "OLD"})
    runtime._warmup_symbols = lambda _history, symbols, *, retry, staged_only=False: None
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._resync_pending = False

    class _UnknownStateStream:
        rollback_failed = True

        def clear_rollback_failure(self) -> None:
            self.rollback_failed = False

        def update_subscriptions(self, stocks: list[str], crypto: list[str]) -> None:
            if self.rollback_failed:
                raise RuntimeError("websocket refused subscription (rollback failed)")
            self.calls.append((list(stocks), list(crypto)))

    failing = _UnknownStateStream()
    failing.calls = []
    runtime.stream = failing
    with pytest.raises(RuntimeError, match="websocket refused"):
        runtime._apply_runtime_universes({Market.EQUITY: ("SPY", "MSFT", "NVDA", "TSLA")})

    # Membership was never committed; entries halt with an unknown-socket state.
    assert runtime._symbols_by_market[Market.EQUITY] == {"SPY", "OLD"}
    assert runtime._market_entry_halts[Market.EQUITY] == "subscription_state_unknown"
    assert runtime._resync_pending is True
    # A subsequent resync reconnects from the authoritative membership.
    runtime._symbols_by_market[Market.EQUITY] = {"SPY", "MSFT"}
    runtime._resync_stream_if_needed()
    assert runtime._resync_pending is False
    assert runtime._market_entry_halts[Market.EQUITY] is None
    assert failing.calls == [(["MSFT", "SPY"], [])]


def test_update_subscriptions_marks_rollback_failure_when_rollback_leg_fails() -> None:
    """A rollback leg failure must flag the socket state as unknown so the
    runtime never assumes the previous subscription survived."""
    import pytest as _pytest

    from parayabuck.data.alpaca_stream import AlpacaBarStream

    class _FailingRollbackStream:
        def __init__(self) -> None:
            self.subscribe_calls = 0

        def subscribe_bars(self, handler: Any, *symbols: str) -> None:
            self.subscribe_calls += 1
            # Call 1: initial subscribe. Call 2: forward leg (added NVDA).
            # Call 3: rollback re-subscribe of the removed AAPL -> fails.
            if self.subscribe_calls == 3:
                raise RuntimeError("rollback subscribe rejected")

        def unsubscribe_bars(self, *symbols: str) -> None:
            pass

    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stream.stock = _FailingRollbackStream()

    class _FailingCrypto:
        def subscribe_bars(self, handler: Any, *symbols: str) -> None:
            raise RuntimeError("crypto subscribe rejected")

        def unsubscribe_bars(self, *symbols: str) -> None:
            pass

    stream.crypto = _FailingCrypto()

    async def handler(_bar: Any) -> None:
        pass

    stream.subscribe(["AAPL"], [], handler)
    # The crypto market fails after the stock market applied its change; the
    # stock rollback then also fails -> the socket state is unknown.
    with _pytest.raises(RuntimeError, match="crypto subscribe rejected"):
        stream.update_subscriptions(["NVDA"], ["BTC/USD"])
    assert stream.rollback_failed is True


def test_retry_backoff_escalates_and_caps_at_sixty_minutes(monkeypatch) -> None:
    """A failed refresh schedules a bounded backoff retry (~5 min, then
    ~15, ~30, capped at ~60 min); a success clears the market's retry state
    so a later failure restarts from the first rung."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._universe_retry_at = {}
    runtime._universe_retry_attempt = {}
    clock = {"t": 1000.0}
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: clock["t"])

    runtime._mark_universe_refresh_outcome(Market.CRYPTO, False)
    assert runtime._universe_retry_at[Market.CRYPTO] == 1300.0  # +300
    assert runtime._universe_retry_attempt[Market.CRYPTO] == 1

    runtime._mark_universe_refresh_outcome(Market.CRYPTO, False)
    assert runtime._universe_retry_at[Market.CRYPTO] == 1900.0  # +900
    assert runtime._universe_retry_attempt[Market.CRYPTO] == 2

    runtime._mark_universe_refresh_outcome(Market.CRYPTO, False)
    assert runtime._universe_retry_at[Market.CRYPTO] == 2800.0  # +1800
    assert runtime._universe_retry_attempt[Market.CRYPTO] == 3

    runtime._mark_universe_refresh_outcome(Market.CRYPTO, False)
    assert runtime._universe_retry_at[Market.CRYPTO] == 4600.0  # +3600 (cap)
    assert runtime._universe_retry_attempt[Market.CRYPTO] == 4

    runtime._mark_universe_refresh_outcome(Market.CRYPTO, False)
    assert runtime._universe_retry_at[Market.CRYPTO] == 4600.0  # still +3600 (cap)
    assert runtime._universe_retry_attempt[Market.CRYPTO] == 5

    runtime._mark_universe_refresh_outcome(Market.CRYPTO, True)
    assert runtime._universe_retry_at == {}
    assert runtime._universe_retry_attempt == {}


def test_equity_retry_deferred_to_premarket_when_backoff_lands_mid_session(
    monkeypatch,
) -> None:
    """An equity scanner retry whose backoff deadline would land inside a
    regular session must be deferred to the next pre-market slot: a
    mid-session refresh reads a partial daily bar into the scanner metrics
    and could change the universe (the pre-market-only invariant). Crypto
    keeps the plain bounded backoff."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._universe_retry_at = {}
    runtime._universe_retry_attempt = {}
    clock = {"t": 1000.0}
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: clock["t"])
    premarket = datetime.now(UTC) + timedelta(hours=20)
    runtime._next_equity_refresh_at = lambda when: premarket  # type: ignore[attr-defined]

    # Mid-session failure: the 300s backoff would land inside the session.
    monkeypatch.setattr(runtime_module, "is_regular_equity_time", lambda when: True)
    runtime._mark_universe_refresh_outcome(Market.EQUITY, False)
    deadline = runtime._universe_retry_at[Market.EQUITY]
    assert deadline > clock["t"] + 3600  # deferred ~20h, not +300s
    assert runtime._universe_retry_attempt[Market.EQUITY] == 1

    # Pre-market failure: plain backoff applies.
    monkeypatch.setattr(runtime_module, "is_regular_equity_time", lambda when: False)
    runtime._mark_universe_refresh_outcome(Market.EQUITY, False)
    assert runtime._universe_retry_at[Market.EQUITY] == clock["t"] + 900  # +900 (attempt 2)
    assert runtime._universe_retry_attempt[Market.EQUITY] == 2

    # Crypto never defers to the equity pre-market slot.
    runtime._mark_universe_refresh_outcome(Market.CRYPTO, False)
    assert runtime._universe_retry_at[Market.CRYPTO] == clock["t"] + 300  # +300 (attempt 1)


def test_scheduled_refresh_failure_retries_bounded_and_restores_health(monkeypatch) -> None:
    """A failed scheduled refresh must self-heal with a bounded retry (~5
    min) instead of waiting for the next full interval; a successful retry
    clears the retry state and restores a healthy scanner status."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(base, runtime=replace(base.runtime, scanner_refresh_hours=1))
    runtime._running = True
    runtime._stop_event = threading.Event()
    now = runtime_module.datetime.now(runtime_module.UTC)
    runtime._workers = {"pb-universe-refresh": WorkerHealth(now, now)}
    runtime._universe_refresh_status = {"equity": "success", "crypto": "success"}
    runtime._last_universe_refresh_error = {"equity": None, "crypto": None}
    calls: list[Market] = []
    call_times: list[float] = []
    failures = {"remaining": 1}
    clock = {"t": 0.0}
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: clock["t"])

    def refresh(target: Market | None = None) -> None:
        assert target is Market.CRYPTO
        calls.append(target)
        call_times.append(clock["t"])
        if failures["remaining"] > 0:
            failures["remaining"] -= 1
            runtime._universe_refresh_status["crypto"] = "refresh_failed"
            runtime._last_universe_refresh_error["crypto"] = "scanner unavailable"
        else:
            runtime._universe_refresh_status["crypto"] = "success"
            runtime._last_universe_refresh_error["crypto"] = None
            runtime._running = False

    runtime._refresh_runtime_universes = refresh
    runtime._next_equity_refresh_at = lambda _now: None
    captured_deadline = {}

    def advance_wait(timeout: float) -> None:
        clock["t"] += timeout
        if clock["t"] > 3600.0 and "value" not in captured_deadline:
            # First wake after the failed slot: the published next-refresh
            # deadline must be the ~5 min retry, not the next 1h interval.
            captured_deadline["value"] = runtime._next_universe_refresh_at

    runtime._stop_event.wait = advance_wait
    runtime._universe_refresh_loop()

    # The retry fired at the ~300s backoff deadline (not the next 1h slot).
    assert calls == [Market.CRYPTO, Market.CRYPTO]
    assert call_times == [3600.0, 3900.0]
    # The health/status deadline accounted for the pending retry.
    retry_deadline = captured_deadline["value"] - runtime_module.datetime.now(runtime_module.UTC)
    assert 280.0 <= retry_deadline.total_seconds() <= 310.0
    # Retry state cleared and scanner status restored on success.
    assert runtime._universe_retry_at == {}
    assert runtime._universe_retry_attempt == {}
    assert runtime._universe_refresh_status["crypto"] == "success"
    assert runtime._last_universe_refresh_error["crypto"] is None


def test_shutdown_cancels_pending_retry(monkeypatch) -> None:
    """Shutdown cancels a pending retry: the loop exits and no further
    refresh attempts happen, even though a retry deadline is pending."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(base, runtime=replace(base.runtime, scanner_refresh_hours=1 / 60))
    runtime._running = True
    runtime._stop_event = threading.Event()
    now = runtime_module.datetime.now(runtime_module.UTC)
    runtime._workers = {"pb-universe-refresh": WorkerHealth(now, now)}
    runtime._universe_refresh_status = {"equity": "success", "crypto": "success"}
    runtime._last_universe_refresh_error = {"equity": None, "crypto": None}
    calls: list[Market] = []
    clock = {"t": 0.0}
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: clock["t"])

    def refresh(target: Market | None = None) -> None:
        calls.append(target)
        runtime._universe_refresh_status["crypto"] = "refresh_failed"
        runtime._last_universe_refresh_error["crypto"] = "scanner unavailable"

    runtime._refresh_runtime_universes = refresh
    runtime._next_equity_refresh_at = lambda _now: None

    def shutdown_wait(timeout: float) -> None:
        clock["t"] += timeout
        if calls:
            runtime._running = False

    runtime._stop_event.wait = shutdown_wait
    runtime._universe_refresh_loop()

    assert calls == [Market.CRYPTO], "a pending retry must not fire after shutdown"
    # The pending retry deadline survives for observability but is never
    # attempted once the loop has exited.
    assert runtime._universe_retry_at[Market.CRYPTO] == 360.0
    assert runtime._universe_retry_attempt[Market.CRYPTO] == 1


def test_disabled_market_failure_does_not_degrade_active_market(monkeypatch) -> None:
    """A crypto scanner failure (no crypto exposure) must not degrade the
    active equity market: both markets refresh independently in the same due
    iteration, and the crypto retry stays isolated per market."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(base, runtime=replace(base.runtime, scanner_refresh_hours=1))
    runtime._running = True
    runtime._stop_event = threading.Event()
    now = datetime.now(UTC)
    runtime._workers = {"pb-universe-refresh": WorkerHealth(now, now)}
    runtime._universe_refresh_status = {"equity": "success", "crypto": "success"}
    runtime._last_universe_refresh_error = {"equity": None, "crypto": None}
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    calls: list[Market] = []
    call_times: list[float] = []
    clock = {"t": 0.0}
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: clock["t"])

    class _FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 3, tzinfo=tz or UTC) + timedelta(seconds=clock["t"])

    monkeypatch.setattr(runtime_module, "datetime", _FakeDatetime)

    def refresh(target: Market | None = None) -> None:
        assert target is not None
        calls.append(target)
        call_times.append(clock["t"])
        if target is Market.CRYPTO:
            runtime._universe_refresh_status["crypto"] = "apply_failed"
            runtime._last_universe_refresh_error["crypto"] = "universe rejected"
            runtime._market_entry_halts[Market.CRYPTO] = "invalid_dynamic_universe"
            raise RuntimeError("universe rejected")
        runtime._universe_refresh_status["equity"] = "success"
        runtime._last_universe_refresh_error["equity"] = None
        runtime._market_entry_halts[Market.EQUITY] = None
        runtime._running = False

    runtime._refresh_runtime_universes = refresh
    # Equity shares the first due iteration with the crypto interval.
    runtime._next_equity_refresh_at = lambda _now: _FakeDatetime.now(UTC) + timedelta(seconds=3600)

    def advance_wait(timeout: float) -> None:
        clock["t"] += timeout
        if len(calls) >= 2:
            runtime._running = False

    runtime._stop_event.wait = advance_wait
    runtime._universe_refresh_loop()

    # Both markets refreshed in the same due iteration. The crypto refresh
    # raises (apply failure) yet the equity refresh still runs: the failing
    # market must not block or degrade the active market.
    assert calls == [Market.CRYPTO, Market.EQUITY]
    assert call_times == [3600.0, 3600.0]
    assert runtime._universe_refresh_status["equity"] == "success"
    assert runtime._market_entry_halts[Market.EQUITY] is None
    assert Market.EQUITY not in runtime._universe_retry_at
    # The failing market keeps its bounded retry, isolated from equity.
    assert runtime._universe_refresh_status["crypto"] == "apply_failed"
    assert runtime._market_entry_halts[Market.CRYPTO] == "invalid_dynamic_universe"
    assert runtime._universe_retry_at[Market.CRYPTO] == 3900.0
    assert runtime._universe_retry_attempt[Market.CRYPTO] == 1


def test_refresh_failure_keeps_pinned_membership_fail_closed(monkeypatch) -> None:
    """A scanner failure must keep the previous membership — including
    managed pins — fully protected: entries stay blocked and the active
    universe is unchanged."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        universe=replace(
            base.universe,
            equity=replace(
                base.universe.equity,
                enabled=True,
                dynamic_selection_enabled=True,
            ),
        ),
    )
    runtime.api_key = "key"
    runtime.secret_key = "secret"
    runtime._universe_refresh_status = {"equity": "success", "crypto": "disabled"}
    runtime._last_universe_refresh_error = {"equity": None, "crypto": None}
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._symbols_by_market = {Market.EQUITY: {"AAPL", "SPY"}, Market.CRYPTO: set()}
    runtime._managed_pin_symbols = {"AAPL"}
    monkeypatch.setattr(
        runtime_module,
        "refresh_equity_universe",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("scanner unavailable")),
    )
    monkeypatch.setattr(runtime_module, "validate_active_universe", lambda _config: ((), "stale"))

    runtime._refresh_runtime_universes(Market.EQUITY)

    assert runtime._universe_refresh_status["equity"] == "refresh_failed"
    assert runtime._market_entry_halts[Market.EQUITY] == "invalid_dynamic_universe"
    # Previous membership — including the managed pin — is untouched.
    assert runtime._symbols_by_market[Market.EQUITY] == {"AAPL", "SPY"}
    assert runtime._managed_pin_symbols == {"AAPL"}


def test_old_scanner_result_with_mismatched_digest_cannot_become_valid(monkeypatch) -> None:
    """An old scanner result whose config digest no longer matches stays
    fail-closed: the refresh failure halts entries instead of accepting the
    stale universe."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = replace(
        base,
        universe=replace(
            base.universe,
            crypto=replace(base.universe.crypto, enabled=False),
        ),
    )
    runtime.api_key = "key"
    runtime.secret_key = "secret"
    runtime._universe_refresh_status = {"equity": "success", "crypto": "disabled"}
    runtime._last_universe_refresh_error = {"equity": None, "crypto": None}
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    monkeypatch.setattr(
        runtime_module,
        "refresh_equity_universe",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("scanner unavailable")),
    )
    monkeypatch.setattr(runtime_module, "validate_active_universe", lambda _config: ((), "digest_mismatch"))

    runtime._refresh_runtime_universes()

    assert runtime._universe_refresh_status["equity"] == "refresh_failed"
    assert runtime._market_entry_halts[Market.EQUITY] == "invalid_dynamic_universe"


def _health_payload_runtime() -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._state = "running"
    runtime._running = True
    now = datetime.now(UTC)
    runtime._workers = {
        "pb-decisions": WorkerHealth(now, now, alive=True),
        "pb-trade-updates": WorkerHealth(now, now, alive=True),
        "pb-account": WorkerHealth(now, now, alive=True),
    }
    runtime._events = SimpleNamespace(qsize=lambda: 0, empty=lambda: True)
    runtime._last_account_at = datetime.now(UTC)
    runtime._oldest_received_at = None
    runtime._dropped_events = 0
    runtime._coalesced_events = 0
    runtime.symbols = []
    runtime._symbols_by_market = {Market.EQUITY: set(), Market.CRYPTO: set()}
    runtime._market_enabled = {Market.EQUITY: False, Market.CRYPTO: False}
    runtime._strategy_enabled = {Market.EQUITY: False, Market.CRYPTO: False}
    runtime._not_ready_symbols = set()
    runtime._circuit_breaker_reason = None
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=15.0,
        maximum_account_age_for_entry_seconds=30.0,
        maximum_queue_age_seconds=30.0,
    )
    runtime._universe_refresh_status = {"equity": "success", "crypto": "refresh_failed"}
    runtime._last_universe_refresh_error = {"equity": None, "crypto": "scanner unavailable"}
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._last_reconciliation_at = None
    runtime._last_universe_refresh_at = None
    runtime._next_universe_refresh_at = None
    runtime.strategy_status = "configured"
    runtime._strategy_specs = ()
    runtime._portfolio_repository = None
    runtime.ledger = None
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.market_state = SimpleNamespace(has_data_gaps=False, has_data_gaps_for_market=lambda market: False)
    return runtime


def test_health_payload_exposes_scanner_retry_state() -> None:
    """The health/status payload surfaces the pending per-market retry
    deadline and attempt count next to the existing scanner status."""
    runtime = _health_payload_runtime()
    runtime._universe_retry_at = {Market.CRYPTO: runtime_module.time.monotonic() + 300.0}
    runtime._universe_retry_attempt = {Market.CRYPTO: 2}
    payload = runtime.health().universe_refresh
    assert payload["retry_attempt"] == {"crypto": 2}
    retry_at = datetime.fromisoformat(payload["retry_at"]["crypto"])
    assert retry_at > datetime.now(UTC)
    # A disabled market with no exposure keeps the runtime healthy while its
    # scanner is failing and a retry is pending.
    health = runtime.health()
    assert health.healthy is True
    assert health.markets["crypto"]["scanner_critical"] is False
