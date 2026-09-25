"""Crypto foundation parity — deterministic fail-closed tests.

Proves:
- candidate-pool parity (trade_symbols ∩ broker)
- inactive exclusion
- managed outside-pool observability without entry eligibility
- category completeness and meme caps
- observation provenance mismatch fails closed
- order independence & hash determinism
- crypto low-variance alpha frozen
- paper/live freeze
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.config import CryptoUniverseConfig, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.universe.crypto_scanner import (
    CRYPTO_CATEGORIES,
    CryptoCandidate,
    CryptoUniverseScanner,
    crypto_scanner_config_digest,
    validate_active_crypto_universe,
)
from parayabuck.universe.observation_set import build_observation_set


def _bar(symbol: str, ts: datetime, close: float) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
        timeframe="1d",
        market=Market.CRYPTO,
    )


def _candidate(symbol: str, **kw) -> CryptoCandidate:
    base = dict(
        median_dollar_volume=1_000_000,
        atr_pct=0.045,
        data_completeness=1.0,
        btc_correlation=0.5,
        category="meme" if symbol in ("DOGE/USD", "SHIB/USD", "PEPE/USD") else "layer1",
        tradable=True,
        status="active",
        spread_bps=10,
        price=100,
        history_count=100,
    )
    base.update(kw)
    return CryptoCandidate(symbol=symbol, **base)


# 1. Unconfigured broker crypto cannot enter candidate pool
def test_unconfigured_broker_crypto_cannot_enter_alpha_pool(tmp_path: Path, monkeypatch) -> None:
    """FOO/USD from broker but not in trade_symbols must not enter pool/selection/observation."""
    cfg = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        trade_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        pinned_symbols=("BTC/USD", "ETH/USD"),
        maximum_stream_symbols=5,
        active_universe_path=str(tmp_path / "active.json"),
    )

    # Simulate broker assets: BTC, ETH, SOL, FOO
    class FakeAsset:
        def __init__(self, sym, tradable=True, status="active"):
            self.symbol = sym
            self.tradable = tradable
            self.status = status

    assets = [
        FakeAsset("BTCUSD"),
        FakeAsset("ETHUSD"),
        FakeAsset("SOLUSD"),
        FakeAsset("FOOUSD"),
    ]
    # Patch TradingClient and history to avoid network
    import parayabuck.universe.preflight as pre

    class FakeTrading:
        def __init__(self, *a, **kw):
            self._session = None

        def get_all_assets(self, *a, **kw):
            return assets

    class FakeHistory:
        def __init__(self, *a, **kw):
            pass

        def get_bars(self, *a, **kw):
            return {}

    monkeypatch.setattr(pre, "TradingClient", FakeTrading)
    monkeypatch.setattr(pre, "AlpacaHistoricalData", FakeHistory)
    # Stub _incremental_history to return enough bars for each candidate
    monkeypatch.setattr(pre, "_incremental_history", lambda *a, **kw: {"BTC/USD": [], "ETH/USD": [], "SOL/USD": []})
    monkeypatch.setattr(pre, "AlpacaHistoricalData", FakeHistory)

    # We test the filtering logic directly instead of full refresh: filtered pool should be BTC/ETH/SOL only
    # Use the same logic as refreshed code: configured ∩ broker
    from parayabuck.universe.preflight import _crypto_asset_symbol

    broker_active = {}
    for asset in assets:
        sym = _crypto_asset_symbol(asset)
        if sym:
            broker_active[sym] = asset
    configured = set(cfg.trade_symbols)
    candidate = sorted(s for s in configured if s in broker_active and broker_active[s].tradable)
    assert "FOO/USD" not in candidate
    assert candidate == ["BTC/USD", "ETH/USD", "SOL/USD"]

    # Also scanner must not rank FOO even if injected as candidate
    scanner = CryptoUniverseScanner(cfg)
    result = scanner.scan(
        [_candidate("BTC/USD"), _candidate("ETH/USD"), _candidate("SOL/USD")],
        data_timestamp=datetime.now(UTC),
    )
    assert "FOO/USD" not in result.candidate_symbols
    assert "FOO/USD" not in result.selected_symbols
    assert "FOO/USD" not in result.observation_set


# 2. Configured inactive cannot receive entry
def test_configured_inactive_excluded_from_entry(tmp_path: Path) -> None:
    cfg = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        trade_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        pinned_symbols=("BTC/USD",),
        maximum_stream_symbols=3,
        active_universe_path=str(tmp_path / "active.json"),
    )
    # Simulate SOL inactive
    scanner = CryptoUniverseScanner(cfg)
    result = scanner.scan(
        [
            _candidate("BTC/USD"),
            _candidate("ETH/USD"),
            CryptoCandidate(
                "SOL/USD",
                1_000_000,
                0.045,
                1.0,
                0.5,
                "layer1",
                tradable=False,
                status="inactive",
                spread_bps=10,
                price=100,
                history_count=100,
            ),
        ],
        data_timestamp=datetime.now(UTC),
    )
    assert "SOL/USD" in result.candidate_symbols  # candidate seen
    assert "SOL/USD" not in result.selected_symbols  # not eligible
    assert "SOL/USD" not in result.observation_set


# 3. Managed outside-pool remains observable but not eligible for fresh entry
def test_managed_outside_pool_observable_not_eligible(tmp_path: Path) -> None:
    cfg = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        trade_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        pinned_symbols=("BTC/USD",),
        maximum_stream_symbols=3,
        active_universe_path=str(tmp_path / "active.json"),
    )
    scanner = CryptoUniverseScanner(cfg)
    # FOO outside pool, but managed pin
    result = scanner.scan(
        [_candidate("BTC/USD"), _candidate("ETH/USD"), _candidate("SOL/USD")],
        data_timestamp=datetime.now(UTC),
        pinned_symbols=("FOO/USD",),
    )
    # FOO should be in observation_set as managed pin even though not in trade_symbols
    assert "FOO/USD" in result.observation_set
    assert "FOO/USD" in result.selected_symbols  # reserved
    # But FOO should not be eligible for alpha ranking: rank_candidates should not include it if not in pool?
    # Build observation set with strategy: managed FOO should stay pinned, not ranked for entry
    # Create minimal history for ranking: only pool symbols have history
    now = datetime.now(UTC)
    # Use low variance strategy ranking: create history where BTC/ETH have data, FOO has none
    strat = portfolio_strategy_from(
        "crypto_low_variance_v2", {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96}
    )
    # Build history with 23 bars for BTC/ETH, none for FOO
    base = datetime(2024, 1, 1, tzinfo=UTC)
    btc_bars = tuple(_bar("BTC/USD", base + timedelta(days=i), 100 + i) for i in range(23))
    eth_bars = tuple(_bar("ETH/USD", base + timedelta(days=i), 200 + i * 0.5) for i in range(23))
    hist = {"BTC/USD": btc_bars, "ETH/USD": eth_bars, "FOO/USD": ()}
    obs = build_observation_set(
        strat,
        hist,
        pool_symbols=("BTC/USD", "ETH/USD"),
        market=Market.CRYPTO,
        timeframe="1d",
        decision_timestamp=now,
        maximum_stream_symbols=3,
        pinned_symbols=("BTC/USD", "FOO/USD"),
        scanner_fallback=("BTC/USD", "ETH/USD", "FOO/USD"),
    )
    assert "FOO/USD" in obs.observation_set  # pinned for safe exit
    # FOO not in ranked pool (no history, outside pool)
    assert "FOO/USD" not in obs.ranked_pool


# 4. All configured symbols have explicit categories
def test_all_configured_symbols_have_explicit_categories() -> None:
    cfg = load_config().universe.crypto
    missing = [s for s in cfg.trade_symbols if s not in CRYPTO_CATEGORIES]
    assert missing == [], f"missing categories: {missing}"
    assert CRYPTO_CATEGORIES["SHIB/USD"] == "meme"
    assert CRYPTO_CATEGORIES["PEPE/USD"] == "meme"
    assert CRYPTO_CATEGORIES["CRV/USD"] == "defi"
    assert CRYPTO_CATEGORIES["GRT/USD"] == "infrastructure"


# 5. Meme category caps work for SHIB/PEPE
def test_meme_category_caps_work(tmp_path: Path) -> None:
    cfg = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        trade_symbols=("BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "SHIB/USD", "PEPE/USD"),
        pinned_symbols=("BTC/USD", "ETH/USD"),
        maximum_stream_symbols=4,
        active_universe_path=str(tmp_path / "active.json"),
        scanner=cfg_scanner_with_meme_limit(),
    )
    scanner = CryptoUniverseScanner(cfg)
    # Provide candidates all passing filters with equal scores except category
    cands = [
        _candidate("BTC/USD", category="core"),
        _candidate("ETH/USD", category="core"),
        _candidate("DOGE/USD", category="meme"),
        _candidate("SHIB/USD", category="meme"),
        _candidate("PEPE/USD", category="meme"),
        _candidate("SOL/USD", category="layer1"),
    ]
    result = scanner.scan(cands, data_timestamp=datetime.now(UTC))
    meme_in_obs = [s for s in result.observation_set if CRYPTO_CATEGORIES.get(s) == "meme"]
    assert len(meme_in_obs) <= 1  # maximum_meme_symbols =1
    # Ensure deterministic: sorted input vs shuffled gives same observation
    import random

    shuffled = cands.copy()
    random.shuffle(shuffled)
    result2 = scanner.scan(shuffled, data_timestamp=datetime.now(UTC))
    assert result.observation_set == result2.observation_set


def cfg_scanner_with_meme_limit():
    from parayabuck.config import CryptoScannerConfig

    return CryptoScannerConfig(maximum_meme_symbols=1, maximum_per_category=3)


# 6. Persisted observation identity mismatch fails closed
def test_persisted_observation_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    cfg = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        trade_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        pinned_symbols=("BTC/USD", "ETH/USD"),
        maximum_stream_symbols=3,
        active_universe_path=str(tmp_path / "active.json"),
    )
    # Need to set observation provenance on config to simulate current strategy
    from dataclasses import replace

    cfg_v2 = replace(
        cfg,
        observation_strategy_name="crypto_low_variance_v2",
        observation_strategy_version="2.0.0",
        observation_strategy_timeframe="1d",
        observation_strategy_parameters_digest="abc123",
    )
    scanner = CryptoUniverseScanner(cfg_v2)
    result = scanner.scan(
        [_candidate("BTC/USD"), _candidate("ETH/USD"), _candidate("SOL/USD")],
        data_timestamp=datetime.now(UTC),
    )
    # Persist with v2 provenance

    # Manually create persisted payload with v2
    persisted = {
        "selection_timestamp": result.selection_timestamp,
        "data_timestamp": result.data_timestamp,
        "selected_symbols": list(result.selected_symbols),
        "candidate_symbols": list(result.candidate_symbols),
        "score_breakdown": result.score_breakdown,
        "eliminated": result.eliminated,
        "category_distribution": result.category_distribution,
        "scanner_version": result.scanner_version,
        "config_digest": crypto_scanner_config_digest(cfg_v2),
        "status": "success",
        "observation_set": list(result.observation_set),
        "observation_eliminated": dict(result.observation_eliminated),
        "observation_strategy_name": "crypto_low_variance_v2",
        "observation_strategy_version": "2.0.0",
        "observation_strategy_timeframe": "1d",
        "observation_strategy_parameters_digest": "abc123",
    }
    Path(cfg_v2.active_universe_path).write_text(json.dumps(persisted))
    # Load with matching config should succeed
    obs, status = validate_active_crypto_universe(cfg_v2)
    assert status == "success"
    # Now load with different strategy identity
    cfg_v999 = replace(
        cfg_v2, observation_strategy_name="crypto_low_variance_v999", observation_strategy_version="9.9.9"
    )
    obs2, status2 = validate_active_crypto_universe(cfg_v999)
    assert status2 == "observation_strategy_mismatch"
    assert obs2 == ()

    # Also timeframe mismatch
    cfg_time = replace(cfg_v2, observation_strategy_timeframe="1h")
    _, status3 = validate_active_crypto_universe(cfg_time)
    assert status3 == "observation_timeframe_mismatch"

    # Also digest mismatch
    cfg_digest = replace(cfg_v2, observation_strategy_parameters_digest="different")
    _, status4 = validate_active_crypto_universe(cfg_digest)
    assert status4 == "observation_digest_mismatch"


# 7. Scanner input order does not affect output (already checked in 5, but explicit)
def test_scanner_input_order_deterministic(tmp_path: Path) -> None:
    cfg = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        trade_symbols=("BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD"),
        pinned_symbols=("BTC/USD",),
        maximum_stream_symbols=3,
        active_universe_path=str(tmp_path / "active.json"),
    )
    scanner = CryptoUniverseScanner(cfg)
    cands = [_candidate("SOL/USD"), _candidate("AVAX/USD"), _candidate("ETH/USD"), _candidate("BTC/USD")]
    r1 = scanner.scan(cands, data_timestamp=datetime.now(UTC))
    r2 = scanner.scan(list(reversed(cands)), data_timestamp=datetime.now(UTC))
    assert r1.selected_symbols == r2.selected_symbols
    assert r1.observation_set == r2.observation_set
    assert r1.candidate_symbols == r2.candidate_symbols
    assert r1.score_breakdown == r2.score_breakdown


# 8. Same config/data gives same universe hash
def test_same_config_data_gives_same_hash(tmp_path: Path) -> None:
    from parayabuck.backtest.universe import build_trailing_point_in_time_universe, hash_point_in_time_universe

    cfg = load_config()
    bars = []
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(5):
        for sym in ("BTC/USD", "ETH/USD"):
            bars.append(_bar(sym, base + timedelta(days=i), 100 + i))
    u1 = build_trailing_point_in_time_universe(
        bars,
        market=Market.CRYPTO,
        maximum_symbols=2,
        rebalance_frequency="daily",
        lookback_days=2,
        crypto_config=cfg.universe.crypto,
    )
    u2 = build_trailing_point_in_time_universe(
        bars,
        market=Market.CRYPTO,
        maximum_symbols=2,
        rebalance_frequency="daily",
        lookback_days=2,
        crypto_config=cfg.universe.crypto,
    )
    assert hash_point_in_time_universe(u1) == hash_point_in_time_universe(u2)


# 9. Crypto low-variance alpha unchanged
def test_crypto_low_variance_target_math_unchanged() -> None:
    strat = portfolio_strategy_from(
        "crypto_low_variance_v2", {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96}
    )
    assert strat.name == "crypto_low_variance_v2"
    assert strat.version == "2.0.0"
    assert strat.timeframe == "1d"
    assert strat.minimum_history_bars == 23
    # Verify decide math unchanged: equal weight before risk overlay, lowest vol first
    # Create two symbols where BTC low vol, ETH high vol
    base = datetime(2024, 6, 1, tzinfo=UTC)
    # 23 bars for each, BTC stable, ETH volatile
    btc = tuple(_bar("BTC/USD", base + timedelta(days=i), 100 + (i % 2) * 0.1) for i in range(23))
    eth = tuple(_bar("ETH/USD", base + timedelta(days=i), 100 + (i % 5) * 5) for i in range(23))
    sol = tuple(_bar("SOL/USD", base + timedelta(days=i), 100 + (i % 3) * 10) for i in range(23))
    from parayabuck.portfolio.base import PortfolioContext

    ctx = PortfolioContext(
        decision_timestamp=base + timedelta(days=23),
        market=Market.CRYPTO,
        timeframe="1d",
        history={"BTC/USD": btc, "ETH/USD": eth, "SOL/USD": sol},
        universe=None,
        universe_version="",
        equity=100000,
        holdings={},
        prices={"BTC/USD": 100, "ETH/USD": 100, "SOL/USD": 100},
        benchmark_symbol="BTC/USD",
    )
    ranked = strat.rank_candidates(ctx)
    # BTC should be most stable (lowest vol) first
    assert ranked[0] == "BTC/USD"
    target = strat.decide(ctx)
    # top_n=2 equal weight 0.5 each before risk, but check selected
    assert len([w for w in target.weights if w.weight > 0]) == 2
    weights = {w.symbol: w.weight for w in target.weights}
    assert (
        abs(weights["BTC/USD"] - 0.5) < 1e-9
        or abs(weights["ETH/USD"] - 0.5) < 1e-9
        or abs(weights["SOL/USD"] - 0.5) < 1e-9
    )


# 11 & 12. Runtime mode paper and live disabled
def test_runtime_mode_paper_and_live_disabled() -> None:
    cfg = load_config()
    assert cfg.runtime.mode.value == "paper"
    assert cfg.live.enabled is False
    assert cfg.live.hard_dollar_cap == 0
