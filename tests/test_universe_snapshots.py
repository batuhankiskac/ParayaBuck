from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    UniverseSnapshot,
    build_crypto_point_in_time_universe,
    build_trailing_point_in_time_universe,
    hash_point_in_time_universe,
    verify_persisted_universe,
)
from parayabuck.config import (
    Config,
    CryptoScannerConfig,
    CryptoUniverseConfig,
    EquityScannerConfig,
    EquityUniverseConfig,
)
from parayabuck.core.models import Bar, Market
from parayabuck.universe.crypto_scanner import (
    CryptoUniverseScanner,
    crypto_candidate_from_bars,
)
from parayabuck.universe.scanner import EquityUniverseScanner, equity_candidate_from_bars


def _crypto_bar(timestamp: datetime, symbol: str, close: float, volume: float) -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1h",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        volume,
        Market.CRYPTO,
    )


def _crypto_history(days: int = 240, volume: float = 100_000.0) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    symbols = ("BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD")
    bars: list[Bar] = []
    for index in range(days * 24):
        timestamp = start + timedelta(hours=index)
        for symbol in symbols:
            drift = 0.0001 * (index % 7)
            bars.append(_crypto_bar(timestamp, symbol, 100.0 * (1 + drift), volume))
    return bars


def test_universe_hash_is_deterministic() -> None:
    history = _crypto_history()
    first = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    second = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    assert hash_point_in_time_universe(first) == hash_point_in_time_universe(second)


def test_universe_hash_differs_for_different_methodology_or_members() -> None:
    history = _crypto_history()
    universe = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    base_hash = hash_point_in_time_universe(universe)
    # Same snapshot members, different market label -> different hash.
    relabeled = PointInTimeUniverse(Market.EQUITY, universe.snapshots, universe.methodology)
    assert hash_point_in_time_universe(relabeled) != base_hash
    # Different methodology text -> different hash.
    other_method = PointInTimeUniverse(universe.market, universe.snapshots, "other_methodology")
    assert hash_point_in_time_universe(other_method) != base_hash
    # Different snapshot membership -> different hash.
    changed_members = (
        UniverseSnapshot(
            universe.snapshots[0].effective_at,
            frozenset({"BTC/USD", "ETH/USD"}),
        ),
    )
    different = PointInTimeUniverse(Market.CRYPTO, changed_members, universe.methodology)
    assert hash_point_in_time_universe(different) != base_hash


def test_verify_persisted_universe_rejects_silent_recompute() -> None:
    history = _crypto_history()
    universe = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    persisted = hash_point_in_time_universe(universe)
    verify_persisted_universe(universe, persisted)  # no error
    different = build_crypto_point_in_time_universe(history, maximum_symbols=2)
    with pytest.raises(ValueError, match="mismatch"):
        verify_persisted_universe(different, persisted)
    # Empty expected hash means "no constraint" (legacy experiments).
    verify_persisted_universe(different, "")


def test_persisted_snapshot_records_round_trip() -> None:
    history = _crypto_history()
    universe = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    assert universe.snapshots
    assert hash_point_in_time_universe(universe) == hash_point_in_time_universe(universe)
    first_snapshot = universe.snapshots[0]
    assert first_snapshot.symbols
    allowed = ("BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD")
    assert all(symbol in allowed for symbol in first_snapshot.symbols)


def test_historical_equity_adapter_uses_same_production_scanner_policy() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = [
        Bar(
            start + timedelta(days=day),
            symbol,
            "1d",
            100,
            102,
            98,
            100 + day * drift,
            volume,
            Market.EQUITY,
        )
        for day in range(30)
        for symbol, drift, volume in (("AAA", 0.01, 100_000), ("ZZZ", 0.02, 2_000_000))
    ]
    config = EquityUniverseConfig(
        maximum_stream_symbols=1,
        target_shortlist=1,
        reference_symbols=(),
        scanner=EquityScannerConfig(
            minimum_price=1,
            minimum_median_dollar_volume=1,
            minimum_data_completeness=0.9,
            minimum_atr_pct=0.001,
        ),
    )
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=1,
        lookback_days=30,
        minimum_history_ratio=0.9,
        equity_config=config,
    )
    snapshot = universe.snapshots[-1]
    history = {
        symbol: [
            bar for bar in bars if bar.symbol == symbol and bar.timestamp < snapshot.effective_at
        ]
        for symbol in ("AAA", "ZZZ")
    }
    direct = EquityUniverseScanner(config).scan(
        [
            equity_candidate_from_bars(symbol, series, expected_bar_count=20)
            for symbol, series in history.items()
        ],
        data_timestamp=max(bar.timestamp for series in history.values() for bar in series),
        selection_timestamp=snapshot.effective_at,
    )
    assert snapshot.symbols == frozenset(direct.selected_symbols)
    assert snapshot.scanner_version == direct.scanner_version


def test_historical_crypto_adapter_uses_same_production_scanner_policy() -> None:
    bars = _crypto_history(days=4)
    scanner_config = CryptoScannerConfig(
        lookback_days=2,
        minimum_data_completeness=0.8,
        minimum_median_dollar_volume=1,
        minimum_atr_pct=0.001,
    )
    config = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        maximum_stream_symbols=3,
        scanner=scanner_config,
    )
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.CRYPTO,
        maximum_symbols=3,
        lookback_days=2,
        minimum_history_ratio=0.8,
        crypto_config=config,
    )
    snapshot = universe.snapshots[-1]
    history = {
        symbol: [
            bar for bar in bars if bar.symbol == symbol and bar.timestamp < snapshot.effective_at
        ]
        for symbol in {bar.symbol for bar in bars}
    }
    direct = CryptoUniverseScanner(config).scan(
        [
            crypto_candidate_from_bars(
                symbol,
                series,
                expected_bar_count=48,
                tradable=True,
                status="active",
                btc_bars=history["BTC/USD"],
            )
            for symbol, series in history.items()
        ],
        data_timestamp=max(bar.timestamp for series in history.values() for bar in series),
        selection_timestamp=snapshot.effective_at,
    )
    assert snapshot.symbols == frozenset(direct.selected_symbols)
    assert snapshot.scanner_version == direct.scanner_version


def test_historical_stage_a_shortlist_admits_formerly_liquid_symbols() -> None:
    """A symbol liquid in 2022 but absent from today's shortlist must still be
    evaluated: the historical Stage-A shortlist is per-date trailing volume,
    never the current candidate file."""
    start = datetime(2022, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for day in range(60):
        timestamp = start + timedelta(days=day)
        bars.append(
            Bar(
                timestamp,
                "OLD_LIQUID",
                "1d",
                100,
                101,
                99,
                100,
                5_000_000,
                Market.EQUITY,
            )
        )
        bars.append(
            Bar(
                timestamp,
                "TODAY_LIQUID",
                "1d",
                100,
                101,
                99,
                100,
                5_000_000,
                Market.EQUITY,
            )
        )
        bars.append(
            Bar(
                timestamp,
                "TINY",
                "1d",
                100,
                101,
                99,
                100,
                100,
                Market.EQUITY,
            )
        )
    config = EquityUniverseConfig(
        maximum_stream_symbols=2,
        target_shortlist=2,
        reference_symbols=(),
        scanner=EquityScannerConfig(
            minimum_price=1,
            minimum_median_dollar_volume=1,
            minimum_data_completeness=0.9,
            minimum_atr_pct=0.001,
            maximum_per_sector=2,
        ),
    )
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=2,
        rebalance_frequency="monthly",
        lookback_days=40,
        minimum_history_ratio=0.9,
        target_shortlist=2,
        equity_config=config,
    )
    snapshot = universe.snapshots[-1]
    # TINY is in the downloaded set but fails the Stage-A volume gate; the
    # scanner only ever saw the two liquid names.
    assert snapshot.symbols == frozenset({"OLD_LIQUID", "TODAY_LIQUID"})
    assert snapshot.candidate_count == 2
    assert "TINY" not in snapshot.symbols
    assert "Stage-A shortlist capped at 2 symbols" in universe.methodology


def _equity_refresh_config(tmp_path: Path) -> Config:
    from dataclasses import replace as _replace

    base = Config()
    equity = _replace(
        EquityUniverseConfig(
            maximum_stream_symbols=3,
            target_shortlist=3,
            reference_symbols=("SPY",),
            scanner=EquityScannerConfig(
                minimum_price=1,
                minimum_median_dollar_volume=1,
                minimum_data_completeness=0.9,
                minimum_atr_pct=0.001,
                maximum_per_sector=5,
            ),
        ),
        candidate_universe_path=str(tmp_path / "equity_candidates.json"),
        active_universe_path=str(tmp_path / "active_equity_universe.json"),
    )
    return _replace(
        base,
        universe=_replace(base.universe, equity=equity),
    )


def _daily_bars(end: datetime, days: int, symbol: str, volume: float) -> list[Bar]:
    return [
        Bar(
            end - timedelta(days=days - 1 - index),
            symbol,
            "1d",
            100,
            101,
            99,
            100,
            volume,
            Market.EQUITY,
        )
        for index in range(days)
    ]


def test_incremental_universe_matches_naive_trailing_windows_per_snapshot() -> None:
    """The rolling-window builder must produce byte-identical snapshots to a
    naive per-timestamp reconstruction of the trailing window."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    # Three symbols with different liquidity profiles, including one with a
    # mid-series gap so eviction is exercised.
    for day in range(120):
        timestamp = start + timedelta(days=day)
        if day < 50 or day >= 80:
            close = 100 + day * 0.01
            bars.append(
                Bar(
                    timestamp,
                    "GAPPY",
                    "1d",
                    close - 1,
                    close + 2,
                    close - 2,
                    close,
                    2_000_000,
                    Market.EQUITY,
                )
            )
        close = 100 + day * 0.02
        bars.append(
            Bar(
                timestamp,
                "AAA",
                "1d",
                close - 1,
                close + 2,
                close - 2,
                close,
                5_000_000,
                Market.EQUITY,
            )
        )
        close = 100 + day * 0.03
        bars.append(
            Bar(
                timestamp,
                "ZZZ",
                "1d",
                close - 1,
                close + 2,
                close - 2,
                close,
                500_000,
                Market.EQUITY,
            )
        )
    config = EquityUniverseConfig(
        maximum_stream_symbols=1,
        target_shortlist=1,
        reference_symbols=(),
        scanner=EquityScannerConfig(
            minimum_price=1,
            minimum_median_dollar_volume=1,
            minimum_data_completeness=0.8,
            minimum_atr_pct=0.001,
        ),
    )
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=1,
        lookback_days=60,
        minimum_history_ratio=0.8,
        equity_config=config,
    )
    assert len(universe.snapshots) > 10, "daily rebalance must produce many snapshots"
    for snapshot in universe.snapshots:
        cutoff = snapshot.effective_at - timedelta(days=60)
        by_symbol: dict[str, list[Bar]] = {}
        for bar in bars:
            if bar.timestamp >= snapshot.effective_at:
                break
            if bar.timestamp >= cutoff:
                by_symbol.setdefault(bar.symbol, []).append(bar)
        # Mirror the builder exactly: no Stage-A shortlist here (the builder
        # ran with target_shortlist=None), so EVERY trailing-window symbol
        # reaches the scanner and the eligible pool is the snapshot.
        candidates = [
            equity_candidate_from_bars(symbol, history, expected_bar_count=20)
            for symbol, history in sorted(by_symbol.items())
        ]
        direct = EquityUniverseScanner(config).scan(
            candidates,
            data_timestamp=max(
                (bar.timestamp for history in by_symbol.values() for bar in history),
                default=snapshot.effective_at - timedelta(days=1),
            ),
            selection_timestamp=snapshot.effective_at,
            pinned_symbols=(),
        )
        assert snapshot.symbols == frozenset(direct.selected_symbols), (
            f"snapshot mismatch at {snapshot.effective_at}"
        )


def test_incremental_universe_handles_wide_datasets_without_rescanning() -> None:
    """A broad multi-year universe must build in linear time: a wide dataset
    completes well within a bound that the old per-timestamp full rescan
    (hundreds of thousands of window iterations) would exceed."""
    import time

    start = datetime(2021, 1, 1, tzinfo=UTC)
    symbols = tuple(f"SYM{i:03d}" for i in range(60))
    bars = [
        Bar(
            start + timedelta(days=day),
            symbol,
            "1d",
            100 + day * 0.001 - 1,
            100 + day * 0.001 + 2,
            100 + day * 0.001 - 2,
            100 + day * 0.001,
            2_000_000,
            Market.EQUITY,
        )
        for day in range(1200)
        for symbol in symbols
    ]
    config = EquityUniverseConfig(
        maximum_stream_symbols=8,
        target_shortlist=8,
        reference_symbols=(),
        scanner=EquityScannerConfig(
            minimum_price=1,
            minimum_median_dollar_volume=1,
            minimum_data_completeness=0.5,
            minimum_atr_pct=0.001,
        ),
    )
    began = time.monotonic()
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=8,
        lookback_days=60,
        minimum_history_ratio=0.5,
        equity_config=config,
    )
    elapsed = time.monotonic() - began
    assert universe.snapshots, "wide dataset must produce snapshots"
    assert elapsed < 30.0, f"incremental builder too slow: {elapsed:.1f}s"


def test_equity_refresh_uses_rolling_cache_delta_windows_and_real_spread(
    tmp_path, monkeypatch
) -> None:
    from parayabuck.universe import preflight as preflight_module

    config = _equity_refresh_config(tmp_path)
    asset = SimpleNamespace(sector="technology", exchange="NASDAQ", tradable=True, status="active")
    now = datetime.now(UTC)
    cached_history = {
        "AAA": [
            (bar.timestamp.isoformat(), 100, 101, 99, 100, 5_000_000)
            for bar in _daily_bars(now, 20, "AAA", 5_000_000)
        ],
        "BBB": [
            (bar.timestamp.isoformat(), 100, 101, 99, 100, 1_000_000)
            for bar in _daily_bars(now, 20, "BBB", 1_000_000)
        ],
    }
    (tmp_path / "equity_candidates.json").write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
                "symbols": ["AAA", "BBB"],
                "shortlist_symbols": ["AAA", "BBB"],
                "candidates": [],
                "history": cached_history,
            }
        ),
        encoding="utf-8",
    )
    windows: list[tuple[str, ...]] = []
    quote_requests: list[tuple[str, ...]] = []

    class FakeTrading:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    class FakeHistory:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def feed_status(self) -> dict[str, object]:
            return {
                "configured": "sip",
                "effective": "sip",
                "sip_fallback_active": False,
                "note": "",
            }

        def get_latest_stock_quotes(self, symbols: list[str]) -> dict[str, object]:
            quote_requests.append(tuple(symbols))
            return {
                "AAA": SimpleNamespace(bid_price=99.5, ask_price=100.5),
            }

        def get_bars_batch(
            self,
            symbols: list[str],
            timeframe: str,
            *,
            start: datetime,
            end: datetime,
            adjustment: str = "raw",
        ) -> dict[str, list[Bar]]:
            windows.append((tuple(symbols), timeframe, start.isoformat(), end.isoformat()))
            # "NEW" appears only in the incremental pass with a liquidity spike.
            volume = 10_000_000 if "NEW" in symbols else 1_000_000
            span = (end - start).days
            bar_count = 20 if span > 30 else 3
            return {symbol: _daily_bars(end, bar_count, symbol, volume) for symbol in symbols}

    monkeypatch.setattr(
        preflight_module,
        "eligible_equity_assets",
        lambda _c, _t: {"AAA": asset, "BBB": asset, "NEW": asset},
    )
    monkeypatch.setattr(preflight_module, "TradingClient", FakeTrading)
    monkeypatch.setattr(preflight_module, "AlpacaHistoricalData", FakeHistory)

    result = preflight_module.refresh_equity_universe(config, "key", "secret")

    # NEW had no cached history: it gets the full 45d backfill window first,
    # then the shared incremental window covers every symbol.
    full_calls = [w for w in windows if (now - datetime.fromisoformat(w[2])).days > 30]
    assert any("NEW" in w[0] for w in full_calls)
    delta_calls = [w for w in windows if (now - datetime.fromisoformat(w[2])).days <= 10]
    assert all(("AAA" in w[0] or "BBB" in w[0] or "NEW" in w[0]) for w in delta_calls)
    assert delta_calls

    # "NEW" became liquid and enters the Stage-A shortlist the same refresh;
    # the quote for AAA flowed into the candidate spread score.
    assert "NEW" in result.selected_symbols
    assert quote_requests
    saved = json.loads((tmp_path / "equity_candidates.json").read_text(encoding="utf-8"))
    saved_aaa = next(item for item in saved["candidates"] if item["symbol"] == "AAA")
    assert saved_aaa["spread_bps"] == pytest.approx(100.0)
    assert saved["history"].get("NEW")
    assert result.sector_metadata_coverage == pytest.approx(1.0)


def test_equity_refresh_full_mode_when_no_cache_and_neutral_spread(tmp_path, monkeypatch) -> None:
    from parayabuck.universe import preflight as preflight_module

    config = _equity_refresh_config(tmp_path)
    asset = SimpleNamespace(sector=None, exchange="NASDAQ", tradable=True, status="active")
    windows: list[tuple[str, ...]] = []

    class FakeTrading:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    class FakeHistory:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def feed_status(self) -> dict[str, object]:
            return {
                "configured": "sip",
                "effective": "sip",
                "sip_fallback_active": False,
                "note": "",
            }

        def get_latest_stock_quotes(self, symbols: list[str]) -> dict[str, object]:
            raise RuntimeError("quotes unavailable")

        def get_bars_batch(
            self,
            symbols: list[str],
            timeframe: str,
            *,
            start: datetime,
            end: datetime,
            adjustment: str = "raw",
        ) -> dict[str, list[Bar]]:
            windows.append((tuple(symbols), start.isoformat(), end.isoformat()))
            return {symbol: _daily_bars(end, 20, symbol, 5_000_000) for symbol in symbols}

    monkeypatch.setattr(
        preflight_module,
        "eligible_equity_assets",
        lambda _c, _t: {"AAA": asset, "BBB": asset},
    )
    monkeypatch.setattr(preflight_module, "TradingClient", FakeTrading)
    monkeypatch.setattr(preflight_module, "AlpacaHistoricalData", FakeHistory)

    result = preflight_module.refresh_equity_universe(config, "key", "secret")

    # Call 1: the scanner's full 45d pass. Call 2: the observation-set
    # builder's strategy-warmup batch fetch over the eligible pool (the
    # stream limit != opportunity universe pipeline).
    assert len(windows) == 2
    observation_window = windows[1]
    assert observation_window[0] == ("SPY", "AAA", "BBB")
    saved = json.loads((tmp_path / "equity_candidates.json").read_text(encoding="utf-8"))
    for item in saved["candidates"]:
        assert item["spread_bps"] is None  # neutral spread score
    assert result.sector_metadata_coverage == 0.0  # no sector metadata anywhere


def test_rolling_cache_last_write_wins_for_same_day_bars(tmp_path, monkeypatch) -> None:
    """A cached intraday partial bar must be replaced by the newer final bar
    for the same day; the delta download is not wasted."""
    from parayabuck.universe import preflight as preflight_module

    config = _equity_refresh_config(tmp_path)
    asset = SimpleNamespace(sector=None, exchange="NASDAQ", tradable=True, status="active")
    now = datetime.now(UTC)
    cached_rows = [
        (now.isoformat(), 100, 101, 99, 100, 100_000),  # partial volume
    ]
    (tmp_path / "equity_candidates.json").write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
                "symbols": ["AAA"],
                "shortlist_symbols": ["AAA"],
                "candidates": [],
                "history": {"AAA": cached_rows},
            }
        ),
        encoding="utf-8",
    )

    class FakeTrading:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    class FakeHistory:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def feed_status(self) -> dict[str, object]:
            return {
                "configured": "sip",
                "effective": "sip",
                "sip_fallback_active": False,
                "note": "",
            }

        def get_latest_stock_quotes(self, symbols: list[str]) -> dict[str, object]:
            raise RuntimeError("no quotes")

        def get_bars_batch(
            self,
            symbols: list[str],
            timeframe: str,
            *,
            start: datetime,
            end: datetime,
            adjustment: str = "raw",
        ) -> dict[str, list[Bar]]:
            return {
                # Same timestamp as the cached partial bar, now with the final
                # volume and a moved close.
                "AAA": [Bar(now, "AAA", "1d", 100, 101, 99, 100.5, 9_000_000, Market.EQUITY)],
            }

    monkeypatch.setattr(preflight_module, "eligible_equity_assets", lambda _c, _t: {"AAA": asset})
    monkeypatch.setattr(preflight_module, "TradingClient", FakeTrading)
    monkeypatch.setattr(preflight_module, "AlpacaHistoricalData", FakeHistory)

    preflight_module.refresh_equity_universe(config, "key", "secret")

    saved = json.loads((tmp_path / "equity_candidates.json").read_text(encoding="utf-8"))
    rows = saved["history"]["AAA"]
    day_rows = [row for row in rows if row[0] == now.isoformat()]
    assert day_rows == [[now.isoformat(), 100.0, 101.0, 99.0, 100.5, 9_000_000.0]]
    assert len(rows) == 1  # replaced, not duplicated
