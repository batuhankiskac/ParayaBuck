from __future__ import annotations

import threading
from dataclasses import replace
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
            positions=(PositionSnapshot("AAPL", 1, 100, 100, Market.EQUITY),),
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

    def warmup(_history: object, symbols: list[str], *, retry: bool) -> None:
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
    assert lifecycle == ["subscribe", "warmup", "readiness"]


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
    calls: list[bool] = []

    def refresh() -> None:
        calls.append(True)
        runtime._running = False

    runtime._refresh_runtime_universes = refresh
    runtime._universe_refresh_loop()

    assert calls == [True]


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
