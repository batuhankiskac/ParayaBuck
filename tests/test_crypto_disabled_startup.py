"""Startup with the crypto sleeve disabled (paper_config_v2, phase 1).

The shipped config disables the crypto strategy and universe. A runtime built
from it must start with an equity-only strategy set, stream no crypto, and
stay silent when no crypto inventory exists. Bot-owned inventory left in a
disabled market is observed (pinned) but no strategy exits it, so startup
raises a ``risk_incident`` alert instead of orphaning it silently.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import parayabuck.runtime as runtime_module
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.runtime import EngineRuntime

ROOT = Path(__file__).resolve().parents[1]


def _shipped_runtime(tmp_path, monkeypatch) -> EngineRuntime:
    monkeypatch.setattr(
        runtime_module,
        "ensure_active_universes",
        lambda config, api_key, secret_key: SimpleNamespace(equity_status="static", crypto_status="disabled"),
    )
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    config = replace(config, runtime=replace(config.runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")))
    return EngineRuntime("key", "secret", config)


def _capture_alerts(monkeypatch) -> list[tuple[str, dict]]:
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        runtime_module.alerts, "emit", lambda kind, *, message, **fields: emitted.append((kind, fields))
    )
    return emitted


def test_shipped_config_starts_equity_only_without_crypto_streams(tmp_path, monkeypatch) -> None:
    runtime = _shipped_runtime(tmp_path, monkeypatch)
    try:
        assert [strategy.market for strategy, _symbols, _cap in runtime._strategy_specs] == [Market.EQUITY]
        assert runtime._strategy_enabled == {Market.EQUITY: True, Market.CRYPTO: False}
        assert runtime._symbols_by_market[Market.CRYPTO] == set()
        assert Market.CRYPTO not in runtime._product_universes_by_market
        assert runtime._market_entry_halts[Market.CRYPTO] is None
    finally:
        runtime._portfolio_repository.close()


def test_disabled_crypto_without_inventory_raises_no_alert(tmp_path, monkeypatch) -> None:
    runtime = _shipped_runtime(tmp_path, monkeypatch)
    emitted = _capture_alerts(monkeypatch)
    try:
        runtime._managed_pin_symbols = {"QLD"}  # equity inventory belongs to an enabled strategy
        runtime._warn_disabled_market_inventory()
        assert emitted == []
    finally:
        runtime._portfolio_repository.close()


def test_disabled_crypto_with_inventory_alerts_instead_of_orphaning_silently(tmp_path, monkeypatch, caplog) -> None:
    runtime = _shipped_runtime(tmp_path, monkeypatch)
    emitted = _capture_alerts(monkeypatch)
    try:
        runtime._managed_pin_symbols = {"QLD", "BTC/USD"}
        with caplog.at_level(logging.WARNING):
            runtime._warn_disabled_market_inventory()
        assert emitted == [("risk_incident", {"market": "crypto", "symbols": ["BTC/USD"]})]
        assert "crypto strategy is disabled" in caplog.text and "BTC/USD" in caplog.text
    finally:
        runtime._portfolio_repository.close()
