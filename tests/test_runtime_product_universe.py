from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import Config, RuntimeMode
from parayabuck.core.models import Market
from parayabuck.portfolio import product_strategy_from
from parayabuck.runtime import EngineRuntime
from parayabuck.runtime_universe import ActiveProductUniverse, load_active_product_universe

_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}


def test_active_product_universe_uses_eligible_pool_for_membership(tmp_path) -> None:
    path = tmp_path / "active.json"
    payload = {
        "status": "success",
        "scanner_version": "test-scanner-v1",
        "config_digest": "policy-digest",
        "selection_timestamp": datetime(2026, 8, 29, 5, 0, tzinfo=UTC).isoformat(),
        "data_timestamp": datetime(2026, 8, 29, 4, 0, tzinfo=UTC).isoformat(),
        "selected_symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
        "observation_set": ["BTC/USD", "ETH/USD"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    universe = load_active_product_universe(
        path,
        market=Market.CRYPTO,
        expected_observation_symbols=("BTC/USD", "ETH/USD"),
        expected_config_digest="policy-digest",
    )

    assert universe.contains("SOLUSD", Market.CRYPTO, datetime.now(UTC))
    assert not universe.contains("AAPL", Market.EQUITY, datetime.now(UTC))
    assert universe.observation_symbols == ("BTC/USD", "ETH/USD")
    assert universe.snapshot_id


def test_active_product_universe_rejects_raced_or_wrong_snapshot(tmp_path) -> None:
    path = tmp_path / "active.json"
    path.write_text(
        json.dumps(
            {
                "status": "success",
                "scanner_version": "test-scanner-v1",
                "config_digest": "wrong",
                "selection_timestamp": datetime.now(UTC).isoformat(),
                "data_timestamp": datetime.now(UTC).isoformat(),
                "selected_symbols": ["SPY", "AAPL"],
                "observation_set": ["SPY"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="config digest"):
        load_active_product_universe(
            path,
            market=Market.EQUITY,
            expected_observation_symbols=("SPY",),
            expected_config_digest="expected",
        )


def test_start_composition_passes_product_universe_to_runtime(monkeypatch) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    config = Config()
    strategy = product_strategy_from("equity_residual_momentum_v3", config.strategy.equity.parameters)
    universe = ActiveProductUniverse.configured(
        Market.EQUITY,
        ("SPY", "AAPL"),
        snapshot_id="composition-test",
    )
    runtime.mode = RuntimeMode.PAPER
    runtime.paper = True
    runtime._running = False
    runtime._state = "stopped"
    runtime._stop_event = threading.Event()
    runtime._state_lock = threading.RLock()
    runtime._portfolio_lock = threading.RLock()
    runtime._symbols_by_market = {Market.EQUITY: {"SPY", "AAPL"}, Market.CRYPTO: set()}
    runtime._strategy_specs = ((strategy, ("SPY", "AAPL"), 0.55),)
    runtime._product_universes_by_market = {Market.EQUITY: universe}
    runtime._portfolio_runtimes = ()
    runtime._portfolio_repository = object()
    runtime._last_reconciliation_at = None
    runtime._pin_managed_symbols = lambda: None
    runtime._restore_persistent_state = lambda: (_ for _ in ()).throw(RuntimeError("after composition"))
    runtime._circuit_breaker_reason = None
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: dict(_CLEAN_RECONCILIATION))
    runtime.ledger = SimpleNamespace(clear_safety_incident=lambda _account, _reason=None: None)
    runtime.app_config = config

    captured: list[dict[str, object]] = []

    class _Runtime:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.append(kwargs)

    monkeypatch.setattr(runtime_module, "PortfolioRuntime", _Runtime)

    with pytest.raises(RuntimeError, match="after composition"):
        runtime.start()

    assert captured[0]["universe"] is runtime._product_universes_by_market[Market.EQUITY]
    assert captured[0]["universe"].snapshot_id == universe.snapshot_id
    assert captured[0]["universe_version"]
    assert captured[0].get("allow_static_universe", False) is False
