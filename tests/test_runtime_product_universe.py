from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import Config, RuntimeMode
from parayabuck.core.models import Market
from parayabuck.portfolio import product_strategy_from
from parayabuck.runtime import EngineRuntime
from parayabuck.runtime_universe import ActiveProductUniverse

_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}


def test_configured_universe_uses_eligible_pool_for_membership() -> None:
    universe = ActiveProductUniverse.configured(
        Market.EQUITY,
        ("SPY", "QQQ", "QLD"),
        snapshot_id="equity:configured:digest",
        config_digest="digest",
    )

    assert universe.contains("QLD", Market.EQUITY, datetime.now(UTC))
    assert not universe.contains("AAPL", Market.EQUITY, datetime.now(UTC))
    assert universe.observation_symbols == ("SPY", "QQQ", "QLD")
    assert universe.snapshot_id == "equity:configured:digest"


def test_managed_pins_extend_observation_but_not_eligibility() -> None:
    universe = ActiveProductUniverse.configured(Market.EQUITY, ("SPY", "QLD"), snapshot_id="pins")

    pinned = universe.with_observation_symbols(("SPY", "QLD", "AAPL"))

    assert pinned.observation_symbols == ("SPY", "QLD", "AAPL")
    assert pinned.eligible_symbols == universe.eligible_symbols
    assert pinned.snapshot_id == universe.snapshot_id
    assert not pinned.contains("AAPL", Market.EQUITY, datetime.now(UTC))


def test_configured_universe_rejects_empty_membership() -> None:
    with pytest.raises(ValueError, match="eligible symbols"):
        ActiveProductUniverse.configured(Market.EQUITY, (), snapshot_id="empty")


def test_start_composition_passes_product_universe_to_runtime(monkeypatch) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    config = Config()
    strategy = product_strategy_from("equity_leveraged_trend_v1", config.strategy.equity.parameters)
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
    runtime._symbols_by_market = {Market.EQUITY: {"SPY", "AAPL"}}
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
