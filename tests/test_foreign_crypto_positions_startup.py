"""Startup safety for foreign inventory in the shared brokerage account.

ParayaBuck is equity-only: crypto support was removed. The account is shared,
so it can also hold equity the strategy does not trade (a ``paper_smoke`` run,
a manual position). Inventory the bot does not own — crypto, and equity
outside the strategy universe that no lineage claims — must neither crash
nor silently pass: the runtime raises ONE ``risk_incident`` alert plus a
WARNING log naming it, and leaves the positions alone — never pinned,
streamed, protected or traded.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import parayabuck.runtime as runtime_module
from parayabuck.config import RiskLimits, RuntimeMode, load_config
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.runtime import EngineRuntime

ROOT = Path(__file__).resolve().parents[1]

_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}


def _capture_alerts(monkeypatch) -> list[tuple[str, str, dict[str, Any]]]:
    emitted: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        runtime_module.alerts,
        "emit",
        lambda kind, *, message, **fields: emitted.append((kind, message, fields)),
    )
    return emitted


def _account(*positions: PositionSnapshot) -> AccountSnapshot:
    return AccountSnapshot(cash=50_000.0, equity=100_000.0, peak_equity=100_000.0, daily_pnl=0.0, positions=positions)


def _crypto_account() -> AccountSnapshot:
    return _account(
        PositionSnapshot("QLD", 10, 100.0, 100.0, Market.EQUITY, managed_qty=10),
        PositionSnapshot("BTC/USD", 0.5, 60_000.0, 60_000.0, Market.CRYPTO),
        PositionSnapshot("ETHUSD", 2.0, 3_000.0, 3_000.0, Market.CRYPTO, managed_qty=2.0),
        PositionSnapshot("DOGE/USD", 0.0, 0.1, 0.1, Market.CRYPTO),  # flat: not held
    )


def _ledger(entries: tuple[Any, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        managed_entries_for_account=lambda _account_id: entries,
        load_safety_incidents=lambda _account_id: (),
        load_risk_state=lambda _account_id, _day: None,
        clear_safety_incident=lambda _account_id, _reason=None: None,
        save_safety_incident=lambda *_args, **_kwargs: None,
        save_risk_state=lambda *_args, **_kwargs: None,
        total_fees_for_account=lambda _account_id: 0.0,
    )


def _pinning_runtime(account: AccountSnapshot, entries: tuple[Any, ...] = ()) -> EngineRuntime:
    """Minimal runtime shell exercising ``_pin_managed_symbols``."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.app_config = SimpleNamespace(
        universe=SimpleNamespace(equity=SimpleNamespace(maximum_stream_symbols=30, symbols=("SPY", "QQQ", "QLD")))
    )
    runtime._strategy_enabled = {Market.EQUITY: True}
    runtime._market_enabled = {Market.EQUITY: True}
    runtime._symbols_by_market = {Market.EQUITY: {"SPY", "QQQ", "QLD"}}
    runtime._not_ready_symbols = set()
    runtime.broker = SimpleNamespace(
        account_id="acct",
        account=lambda: account,
        portfolio_open_order_symbols=lambda: set(),
    )
    runtime._portfolio_repository = SimpleNamespace(owned_symbols=lambda _account_id: {"QLD", "ETH/USD"})
    runtime.ledger = _ledger(entries)
    return runtime


def test_shipped_config_builds_an_equity_only_runtime(tmp_path) -> None:
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    config = replace(config, runtime=replace(config.runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")))
    runtime = EngineRuntime("key", "secret", config)
    try:
        assert [strategy.market for strategy, _symbols, _cap in runtime._strategy_specs] == [Market.EQUITY]
        assert runtime._strategy_enabled == {Market.EQUITY: True}
        assert set(runtime._symbols_by_market) == {Market.EQUITY}
        assert set(runtime._product_universes_by_market) == {Market.EQUITY}
        assert runtime._market_entry_halts == {Market.EQUITY: None}
        assert runtime._foreign_inventory == ()
        assert all("/" not in symbol for symbol in runtime.symbols)
    finally:
        runtime._portfolio_repository.close()


def test_no_crypto_positions_raise_no_alert(monkeypatch) -> None:
    emitted = _capture_alerts(monkeypatch)
    runtime = _pinning_runtime(_account(PositionSnapshot("QLD", 10, 100.0, 100.0, Market.EQUITY, managed_qty=10)))

    runtime._pin_managed_symbols()
    runtime._warn_foreign_inventory()

    assert runtime._foreign_inventory == ()
    assert emitted == []


def test_foreign_positions_raise_one_alert_and_are_never_pinned(monkeypatch, caplog) -> None:
    emitted = _capture_alerts(monkeypatch)
    ledger_row = SimpleNamespace(canonical_symbol="SOL/USD", market=Market.CRYPTO, status="open")
    runtime = _pinning_runtime(
        _account(
            PositionSnapshot("QLD", 10, 100.0, 100.0, Market.EQUITY, managed_qty=10),
            PositionSnapshot("BTC/USD", 0.5, 60_000.0, 60_000.0, Market.CRYPTO),
            PositionSnapshot("AAPL", 3, 100.0, 100.0, Market.EQUITY),
        ),
        entries=(ledger_row,),
    )

    runtime._pin_managed_symbols()
    with caplog.at_level(logging.WARNING, logger="parayabuck.runtime"):
        runtime._warn_foreign_inventory()

    assert [(kind, fields) for kind, _message, fields in emitted] == [
        (
            "risk_incident",
            {
                "foreign": [
                    {"market": "crypto", "symbol": "BTC/USD"},
                    {"market": "crypto", "symbol": "SOL/USD"},
                    {"market": "equity", "symbol": "AAPL"},
                ]
            },
        )
    ]
    message = emitted[0][1]
    assert "crypto support was removed" in message
    assert "BTC/USD, SOL/USD" in message and "AAPL" in message
    assert "crypto support was removed" in caplog.text
    # Classified only: never pinned, observed or made tradable.
    assert runtime._managed_pin_symbols == {"QLD"}
    assert set(runtime._symbols_by_market) == {Market.EQUITY}
    assert runtime._symbols_by_market[Market.EQUITY] == {"SPY", "QQQ", "QLD"}
    assert runtime.symbols == ["QLD", "QQQ", "SPY"]
    assert runtime._not_ready_symbols == {"QLD"}


def test_unreadable_account_at_pin_time_does_not_crash(monkeypatch) -> None:
    emitted = _capture_alerts(monkeypatch)
    runtime = _pinning_runtime(_account())

    def _account_down() -> AccountSnapshot:
        raise ConnectionError("broker down")

    runtime.broker.account = _account_down

    runtime._pin_managed_symbols()
    runtime._warn_foreign_inventory()

    assert runtime._foreign_inventory == ()
    assert emitted == []


def _startable_runtime(account: AccountSnapshot, subscribed: list[tuple[Any, ...]]) -> EngineRuntime:
    """A paper runtime whose start() runs end to end against fakes."""
    runtime = _pinning_runtime(account)
    runtime.mode = RuntimeMode.PAPER
    runtime.paper = True
    runtime._running = False
    runtime._state = "stopped"
    runtime._stop_event = threading.Event()
    runtime._state_lock = threading.RLock()
    runtime._portfolio_lock = threading.RLock()
    runtime._strategy_specs = ()
    runtime._portfolio_runtimes = ()
    runtime._managed_pin_symbols = set()
    runtime._circuit_breaker_reason = None
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._runtime_session_id = None
    runtime.symbols = []
    runtime.broker.reconcile = lambda: dict(_CLEAN_RECONCILIATION)
    runtime.stream = SimpleNamespace(subscribe=lambda *args, **kwargs: subscribed.append(args), run_stock=lambda: None)
    runtime.trading_updates = SimpleNamespace(run=lambda: None)
    runtime.app_config = SimpleNamespace(
        runtime=SimpleNamespace(execution_timeframe="5m", market_state_timeframe="5m"),
        universe=runtime.app_config.universe,
        risk=RiskLimits(),
        live=SimpleNamespace(
            enabled=False, hard_dollar_cap=1000, operator_approval_key_env="", operator_approval_path=""
        ),
        alpaca=SimpleNamespace(stock_historical_feed="iex", data_delayed=False),
        execution=SimpleNamespace(rebalance_band_pct=0.02, minimum_order_notional=10.0),
    )
    runtime.aggregators = {}
    runtime._events = None
    runtime._workers = {}
    runtime._decision_loop = lambda: None
    runtime._account_loop = lambda: None
    runtime._run_stream_worker = lambda name, target: None
    runtime._signal_adjustment_loop = lambda: None
    runtime._warmup = lambda: None
    runtime.stop = lambda: True
    return runtime


def test_runtime_starts_equity_only_while_crypto_is_held(monkeypatch) -> None:
    emitted = _capture_alerts(monkeypatch)
    subscribed: list[tuple[Any, ...]] = []
    runtime = _startable_runtime(_crypto_account(), subscribed)

    runtime.start()

    assert runtime._startup_committed
    assert runtime._state in {"running", "warming_up"}
    incidents = [fields for kind, _message, fields in emitted if kind == "risk_incident"]
    assert incidents == [
        {
            "foreign": [
                {"market": "crypto", "symbol": "BTC/USD"},
                {"market": "crypto", "symbol": "ETH/USD"},
            ]
        }
    ]
    # Only equity symbols are streamed; no crypto stream worker exists.
    assert len(subscribed) == 1
    assert sorted(subscribed[0][0]) == ["QLD", "QQQ", "SPY"]
    assert "pb-crypto" not in runtime._workers
    assert set(runtime._workers) >= {"pb-decisions", "pb-account", "pb-trade-updates", "pb-stocks"}
    assert all("/" not in symbol for symbol in runtime.symbols)
