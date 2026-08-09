from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import parayabuck.runtime as runtime_module
from parayabuck.config import Config
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.runtime import EngineRuntime, WorkerHealth


def test_invalid_dynamic_snapshot_blocks_entries_unless_fallback_is_explicit() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.app_config = Config()
    runtime.universe_preflight = SimpleNamespace(
        equity_status="refresh_failed", crypto_status="disabled"
    )

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
            positions=(
                PositionSnapshot(
                    "AAPL", 1, 100, 100, Market.EQUITY, ownership="managed", managed_qty=1
                ),
            ),
        ),
        open_portfolio_order_symbols=lambda: {"QQQ"},
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

    def warmup(
        _history: object, symbols: list[str], *, retry: bool, staged_only: bool = False
    ) -> None:
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
        open_portfolio_order_symbols=lambda: set(),
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
        open_portfolio_order_symbols=lambda: set(),
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
    from parayabuck.portfolio.equity_dynamic_trend import EquityDynamicTrend1dV1

    runtime = _hot_swap_runtime({"SPY", "OLD"})
    runtime.aggregators = {"1d": object()}
    runtime_seeds: list[list[Bar]] = []
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            symbols=("SPY", "OLD"),
            strategy=EquityDynamicTrend1dV1({"top_n": 2, "exclude_symbols": []}),
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

    def get_bars_batch(symbols, timeframe, *, start):
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

    def fail_warmup(
        _history: object, symbols: list[str], *, retry: bool, staged_only: bool = False
    ) -> None:
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
