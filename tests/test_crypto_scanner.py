from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
import parayabuck.universe.preflight as preflight_module
from parayabuck.config import (
    Config,
    CryptoScannerConfig,
    CryptoUniverseConfig,
    EngineConfig,
    EquityUniverseConfig,
    RuntimeConfig,
    StrategyConfig,
    UniverseConfig,
)
from parayabuck.core.models import Market
from parayabuck.runtime import EngineRuntime
from parayabuck.universe import (
    CryptoCandidate,
    CryptoUniverseScanner,
    crypto_candidate_from_bars,
    crypto_scanner_config_digest,
    load_active_crypto_symbols,
    validate_active_crypto_universe,
)


def _config(tmp_path: Path, **changes: object) -> CryptoUniverseConfig:
    values: dict[str, object] = {
        "enabled": True,
        "dynamic_selection_enabled": True,
        "maximum_stream_symbols": 5,
        "pinned_symbols": ("BTC/USD", "ETH/USD"),
        "trade_symbols": (
            "BTC/USD",
            "ETH/USD",
            "SOL/USD",
            "ADA/USD",
            "DOGE/USD",
            "SHIB/USD",
            "ARB/USD",
            "HYPE/USD",
            "AVAX/USD",
            "DOT/USD",
        ),
        "active_universe_path": str(tmp_path / "active.json"),
        "scanner": CryptoScannerConfig(
            minimum_median_dollar_volume=100,
            maximum_per_category=2,
            maximum_meme_symbols=1,
            maximum_high_beta_symbols=1,
        ),
    }
    values.update(changes)
    return CryptoUniverseConfig(**values)


def _candidate(
    symbol: str,
    *,
    volume: float = 1_000_000,
    atr: float = 0.045,
    completeness: float = 1.0,
    correlation: float | None = 0.5,
    category: str = "unknown",
    tradable: bool = True,
) -> CryptoCandidate:
    return CryptoCandidate(
        symbol=symbol,
        median_dollar_volume=volume,
        atr_pct=atr,
        data_completeness=completeness,
        btc_correlation=correlation,
        category=category,
        tradable=tradable,
        spread_bps=10,
        price=100,
        history_count=100,
    )


def _candidates() -> list[CryptoCandidate]:
    return [
        _candidate("BTC/USD", correlation=None),
        _candidate("ETH/USD"),
        _candidate("SOL/USD"),
        _candidate("ADA/USD"),
        _candidate("DOGE/USD"),
        _candidate("SHIB/USD", category="meme"),
        _candidate("ARB/USD"),
        _candidate("HYPE/USD"),
        _candidate("AVAX/USD"),
        _candidate("DOT/USD"),
    ]


def test_crypto_liquidity_uses_daily_not_hourly_notional() -> None:
    bars = []
    start = datetime(2026, 7, 27, tzinfo=UTC)
    for day in range(2):
        for hour in range(4):
            bars.append(
                SimpleNamespace(
                    timestamp=start + timedelta(days=day, hours=hour),
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.0,
                    volume=4.0,
                )
            )

    candidate = crypto_candidate_from_bars(
        "SOL/USD",
        bars,
        expected_bar_count=8,
        tradable=True,
        status="active",
    )

    assert candidate.median_dollar_volume == pytest.approx(1_600.0)
    assert candidate.data_completeness == 1.0


def test_malformed_bar_is_skipped_not_crash_the_refresh() -> None:
    """A bar with a non-numeric close (e.g. None from a partial API response)
    must be filtered out, not raise through the whole crypto refresh."""
    start = datetime(2026, 7, 27, tzinfo=UTC)
    good = SimpleNamespace(
        timestamp=start,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=4.0,
    )
    bad = SimpleNamespace(
        timestamp=start + timedelta(hours=1),
        open=100.0,
        high=101.0,
        low=99.0,
        close=None,
        volume=None,
    )
    candidate = crypto_candidate_from_bars(
        "SOL/USD",
        [good, bad],
        expected_bar_count=2,
        tradable=True,
        status="active",
    )
    assert candidate.data_completeness == pytest.approx(0.5)
    assert candidate.median_dollar_volume == pytest.approx(400.0)


def test_config_canonicalizes_and_validates_crypto_symbols(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        trade_symbols=("btcusd", "ethusd"),
        pinned_symbols=("btc/usd", "ETH/USD"),
    )
    assert config.trade_symbols == ("BTC/USD", "ETH/USD")
    assert config.pinned_symbols == ("BTC/USD", "ETH/USD")

    with pytest.raises(ValueError, match="unique"):
        _config(tmp_path, trade_symbols=("BTCUSD", "BTC/USD"), pinned_symbols=())
    with pytest.raises(ValueError, match="included"):
        _config(tmp_path, trade_symbols=("BTC/USD",), pinned_symbols=("ETH/USD",))
    with pytest.raises(ValueError, match="Pinned"):
        _config(
            tmp_path,
            maximum_stream_symbols=1,
            trade_symbols=("BTC/USD", "ETH/USD"),
            pinned_symbols=("BTC/USD", "ETH/USD"),
        )
    with pytest.raises(ValueError, match="Stablecoin"):
        _config(tmp_path, trade_symbols=("BTC/USD", "USDC/USD"), pinned_symbols=("BTC/USD",))


def test_scanner_is_order_independent_pins_core_and_limits_capacity(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scanner = CryptoUniverseScanner(config)
    timestamp = datetime(2026, 7, 29, 12, tzinfo=UTC)
    first = scanner.scan(_candidates(), data_timestamp=timestamp, selection_timestamp=timestamp)
    second = scanner.scan(
        list(reversed(_candidates())),
        data_timestamp=timestamp,
        selection_timestamp=timestamp,
    )
    assert first == second
    assert first.selected_symbols[:2] == ("BTC/USD", "ETH/USD")
    # The eligible POOL can exceed the stream capacity; the OBSERVATION SET
    # is the streamed subset at the capacity.
    assert len(first.selected_symbols) > config.maximum_stream_symbols
    assert len(first.observation_set) == config.maximum_stream_symbols
    assert len(set(first.observation_set)) == len(first.observation_set)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"volume": 1}, "minimum_liquidity"),
        ({"atr": 0.3}, "atr_range"),
        ({"completeness": 0.5}, "data_completeness"),
        ({"tradable": False}, "not_active_tradable"),
    ],
)
def test_candidate_filters(tmp_path: Path, changes: dict[str, object], reason: str) -> None:
    config = _config(
        tmp_path,
        maximum_stream_symbols=3,
        trade_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
    )
    candidate = _candidate("SOL/USD", **changes)  # type: ignore[arg-type]
    result = CryptoUniverseScanner(config).scan(
        [_candidate("BTC/USD"), _candidate("ETH/USD"), candidate],
        data_timestamp=datetime.now(UTC),
    )
    assert result.eliminated["SOL/USD"] == reason


def test_category_limits_and_score_penalties(tmp_path: Path) -> None:
    config = _config(tmp_path, maximum_stream_symbols=10)
    result = CryptoUniverseScanner(config).scan(
        _candidates(),
        data_timestamp=datetime.now(UTC),
    )
    # Category diversity is an OBSERVATION-SET constraint; the pool keeps
    # every eligible pair.
    assert sum(symbol in result.observation_set for symbol in ("DOGE/USD", "SHIB/USD")) == 1
    assert sum(symbol in result.observation_set for symbol in ("ARB/USD", "HYPE/USD")) == 1
    assert (
        sum(
            symbol in result.observation_set
            for symbol in ("SOL/USD", "ADA/USD", "AVAX/USD", "DOT/USD")
        )
        == 2
    )
    assert len(result.selected_symbols) > len(result.observation_set)

    normal = _candidate("SOL/USD", correlation=0.5)
    correlated = _candidate("ADA/USD", correlation=0.99)
    unknown = _candidate("SHIB/USD", category="unknown")
    scored = CryptoUniverseScanner(config).scan(
        [_candidate("BTC/USD"), _candidate("ETH/USD"), normal, correlated, unknown],
        data_timestamp=datetime.now(UTC),
    )
    assert scored.score_breakdown["SOL/USD"]["total"] > scored.score_breakdown["ADA/USD"]["total"]
    assert scored.score_breakdown["SOL/USD"]["total"] > scored.score_breakdown["SHIB/USD"]["total"]


def test_duplicate_candidate_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Duplicate canonical"):
        CryptoUniverseScanner(_config(tmp_path)).scan(
            [_candidate("BTCUSD"), _candidate("BTC/USD")],
            data_timestamp=datetime.now(UTC),
        )


def test_persist_validate_and_fallback_rejections(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scanner = CryptoUniverseScanner(config)
    now = datetime.now(UTC)
    result = scanner.scan(_candidates(), data_timestamp=now, selection_timestamp=now)
    scanner.persist(result)
    assert validate_active_crypto_universe(config) == (result.observation_set, "success")
    assert load_active_crypto_symbols(config) == result.observation_set
    assert not Path(config.active_universe_path + ".tmp").exists()
    history_path = Path(config.active_universe_path + ".history.jsonl")
    assert len(history_path.read_text(encoding="utf-8").splitlines()) == 1
    assert crypto_scanner_config_digest(config) == crypto_scanner_config_digest(replace(config))

    payload = json.loads(Path(config.active_universe_path).read_text())
    payload["selection_timestamp"] = (now - timedelta(days=2)).isoformat()
    Path(config.active_universe_path).write_text(json.dumps(payload))
    assert validate_active_crypto_universe(config)[1] == "stale"
    assert load_active_crypto_symbols(config) == config.trade_symbols[:5]

    payload["selection_timestamp"] = now.isoformat()
    payload["config_digest"] = "wrong"
    Path(config.active_universe_path).write_text(json.dumps(payload))
    assert validate_active_crypto_universe(config)[1] == "digest_mismatch"

    payload["config_digest"] = crypto_scanner_config_digest(config)
    payload["selected_symbols"] = ["BTC/USD", "SOL/USD"]
    Path(config.active_universe_path).write_text(json.dumps(payload))
    assert validate_active_crypto_universe(config)[1] == "missing_pins"

    payload["selected_symbols"] = ["BTC/USD", "ETH/USD", "LINK/USD"]
    Path(config.active_universe_path).write_text(json.dumps(payload))
    assert validate_active_crypto_universe(config)[1] == "outside_candidate_pool"

    Path(config.active_universe_path).write_text("{broken")
    assert validate_active_crypto_universe(config)[1] == "invalid"


def test_dynamic_loader_reserves_managed_position_and_static_mode_is_unchanged(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, maximum_stream_symbols=3)
    assert load_active_crypto_symbols(config, pinned_symbols=("SOL/USD",)) == (
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
    )
    static = replace(config, dynamic_selection_enabled=False)
    assert load_active_crypto_symbols(static) == static.trade_symbols


def test_runtime_uses_only_active_crypto_for_stream_and_trade_permission(
    tmp_path: Path, monkeypatch: Any
) -> None:
    crypto = _config(tmp_path, maximum_stream_symbols=3)
    scanner = CryptoUniverseScanner(crypto)
    now = datetime.now(UTC)
    scanner.persist(scanner.scan(_candidates(), data_timestamp=now, selection_timestamp=now))
    config = Config(
        universe=UniverseConfig(
            equity=EquityUniverseConfig(
                enabled=False,
                dynamic_selection_enabled=False,
                reference_symbols=(),
            ),
            crypto=crypto,
        ),
        engine=EngineConfig(),
        runtime=RuntimeConfig(decision_db_path=str(tmp_path / "runtime.sqlite3")),
        strategy=StrategyConfig(
            equity=replace(StrategyConfig().equity, enabled=False),
            crypto=replace(StrategyConfig().crypto, enabled=True),
        ),
    )

    class FakeEngine:
        def __init__(self, *_args: object, allowed_trade_symbols: set[str], **_kwargs: object):
            self.allowed_trade_symbols = allowed_trade_symbols
            self.orders = object()
            self.risk = object()

    class FakeLedger:
        def __init__(self, _path: str, **_kwargs: object) -> None:
            pass

        def managed_entries_for_account(self, _account: str) -> tuple[object, ...]:
            return ()

    class FakeBroker:
        account_id = "acct"

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class Sink:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(runtime_module, "ExecutionLedger", FakeLedger)
    monkeypatch.setattr(runtime_module, "AlpacaBroker", FakeBroker)
    monkeypatch.setattr(runtime_module, "RuntimeMarketState", FakeEngine)
    monkeypatch.setattr(runtime_module, "AlpacaBarStream", Sink)
    monkeypatch.setattr(runtime_module, "AlpacaTradingUpdateStream", Sink)
    monkeypatch.setattr(runtime_module, "TimeframeAggregator", Sink)

    runtime = EngineRuntime("key", "secret", config)
    active = set(load_active_crypto_symbols(crypto))
    assert runtime._symbols_by_market[Market.CRYPTO] == active
    assert runtime.market_state.allowed_trade_symbols == active
    assert set(crypto.trade_symbols) - active


def test_runtime_managed_crypto_pin_replaces_unmanaged_capacity_member(tmp_path: Path) -> None:
    crypto = _config(tmp_path, maximum_stream_symbols=3)
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.app_config = SimpleNamespace(universe=SimpleNamespace(crypto=crypto))
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (
            SimpleNamespace(canonical_symbol="SOL/USD", market=Market.CRYPTO),
        )
    )
    runtime._symbols_by_market = {
        Market.EQUITY: set(),
        Market.CRYPTO: {"BTC/USD", "ETH/USD", "ADA/USD"},
    }
    runtime.market_state = SimpleNamespace(allowed_trade_symbols={"BTC/USD", "ETH/USD", "ADA/USD"})
    runtime._not_ready_symbols = set()

    runtime._pin_managed_symbols()

    assert runtime._symbols_by_market[Market.CRYPTO] == {
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
    }
    assert runtime.market_state.allowed_trade_symbols == {
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
    }


def test_asset_api_failure_preserves_previous_success(tmp_path: Path, monkeypatch: Any) -> None:
    crypto = _config(tmp_path, maximum_stream_symbols=3)
    active = Path(crypto.active_universe_path)
    active.write_text('{"old":"success"}')
    config = Config(
        universe=UniverseConfig(
            equity=EquityUniverseConfig(
                enabled=False,
                dynamic_selection_enabled=False,
                reference_symbols=(),
            ),
            crypto=crypto,
        ),
        engine=EngineConfig(),
        strategy=StrategyConfig(
            equity=replace(StrategyConfig().equity, enabled=False),
            crypto=replace(StrategyConfig().crypto, enabled=True),
        ),
    )

    class FailingTradingClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def get_all_assets(self, _request: object) -> list[object]:
            raise RuntimeError("asset API unavailable")

    monkeypatch.setattr(preflight_module, "TradingClient", FailingTradingClient)

    with pytest.raises(RuntimeError, match="asset API unavailable"):
        preflight_module.refresh_crypto_universe(config, "test-key", "test-secret")
    assert active.read_text() == '{"old":"success"}'


def test_historical_api_failure_preserves_previous_success(
    tmp_path: Path, monkeypatch: Any
) -> None:
    crypto = _config(
        tmp_path,
        maximum_stream_symbols=3,
        trade_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
    )
    active = Path(crypto.active_universe_path)
    active.write_text('{"old":"success"}')
    config = Config(
        universe=UniverseConfig(
            equity=EquityUniverseConfig(
                enabled=False,
                dynamic_selection_enabled=False,
                reference_symbols=(),
            ),
            crypto=crypto,
        ),
        engine=EngineConfig(),
        strategy=StrategyConfig(
            equity=replace(StrategyConfig().equity, enabled=False),
            crypto=replace(StrategyConfig().crypto, enabled=True),
        ),
    )

    class TradingClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def get_all_assets(self, _request: object) -> list[object]:
            return [
                SimpleNamespace(symbol=symbol, tradable=True, status="active")
                for symbol in crypto.trade_symbols
            ]

    class FailingHistory:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def get_bars_batch(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("historical API unavailable")

    monkeypatch.setattr(preflight_module, "TradingClient", TradingClient)
    monkeypatch.setattr(preflight_module, "AlpacaHistoricalData", FailingHistory)

    with pytest.raises(RuntimeError, match="historical API unavailable"):
        preflight_module.refresh_crypto_universe(config, "test-key", "test-secret")
    assert active.read_text() == '{"old":"success"}'


def test_startup_preflight_refreshes_both_missing_dynamic_universes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    crypto = _config(tmp_path, maximum_stream_symbols=3)
    equity = EquityUniverseConfig(
        dynamic_selection_enabled=True,
        reference_symbols=("SPY",),
        fallback_trade_symbols=("AAPL",),
        active_universe_path=str(tmp_path / "active-equity.json"),
        candidate_universe_path=str(tmp_path / "candidates.json"),
    )
    config = Config(universe=UniverseConfig(equity=equity, crypto=crypto))
    calls: list[str] = []

    def refresh_equity(*_args: object) -> SimpleNamespace:
        calls.append("equity")
        return SimpleNamespace(selected_symbols=("SPY", "AAPL"))

    def refresh_crypto(*_args: object) -> SimpleNamespace:
        calls.append("crypto")
        return SimpleNamespace(
            selected_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
            eliminated={},
        )

    monkeypatch.setattr(preflight_module, "refresh_equity_universe", refresh_equity)
    monkeypatch.setattr(preflight_module, "refresh_crypto_universe", refresh_crypto)

    result = preflight_module.ensure_active_universes(config, "key", "secret")

    assert calls == ["equity", "crypto"]
    assert result.equity_status == "success"
    assert result.crypto_status == "success"
    assert result.equity_refreshed
    assert result.crypto_refreshed


def test_startup_preflight_failure_is_nonfatal_and_keeps_fallback(
    tmp_path: Path, monkeypatch: Any
) -> None:
    crypto = _config(tmp_path, maximum_stream_symbols=3)
    config = Config(
        universe=UniverseConfig(
            equity=EquityUniverseConfig(
                enabled=False,
                dynamic_selection_enabled=False,
                reference_symbols=(),
            ),
            crypto=crypto,
        ),
        engine=EngineConfig(),
        strategy=StrategyConfig(
            equity=replace(StrategyConfig().equity, enabled=False),
            crypto=replace(StrategyConfig().crypto, enabled=True),
        ),
    )

    def fail(*_args: object) -> None:
        raise RuntimeError("temporary API failure")

    monkeypatch.setattr(preflight_module, "refresh_crypto_universe", fail)

    result = preflight_module.ensure_active_universes(config, "key", "secret")

    # The failed refresh must be reported as refresh_failed, not as the stale
    # validation reason: "missing"/"stale" describe the file, not that the
    # refresh itself just failed.
    assert result.crypto_status == "refresh_failed"
    assert not result.crypto_refreshed
    assert load_active_crypto_symbols(crypto, log_rejection=False) == (
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
    )


def test_startup_preflight_skips_api_when_active_file_is_valid(
    tmp_path: Path, monkeypatch: Any
) -> None:
    crypto = _config(tmp_path, maximum_stream_symbols=3)
    now = datetime.now(UTC)
    scanner = CryptoUniverseScanner(crypto)
    scanner.persist(scanner.scan(_candidates(), data_timestamp=now, selection_timestamp=now))
    config = Config(
        universe=UniverseConfig(
            equity=EquityUniverseConfig(
                enabled=False,
                dynamic_selection_enabled=False,
                reference_symbols=(),
            ),
            crypto=crypto,
        ),
        engine=EngineConfig(),
        strategy=StrategyConfig(
            equity=replace(StrategyConfig().equity, enabled=False),
            crypto=replace(StrategyConfig().crypto, enabled=True),
        ),
    )

    def unexpected(*_args: object) -> None:
        raise AssertionError("valid active universe must not call Alpaca")

    monkeypatch.setattr(preflight_module, "refresh_crypto_universe", unexpected)

    result = preflight_module.ensure_active_universes(config, "key", "secret")

    assert result.crypto_status == "success"
    assert not result.crypto_refreshed
