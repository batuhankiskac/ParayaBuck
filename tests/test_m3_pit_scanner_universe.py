from __future__ import annotations

import ast
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_m3_pit_scanner_universe as pit  # noqa: E402
import m3_data_gate_helpers as helpers  # noqa: E402

from parayabuck.backtest.universe import build_trailing_point_in_time_universe  # noqa: E402
from parayabuck.config import EquityScannerConfig, EquityUniverseConfig  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.universe.scanner import scanner_config_digest  # noqa: E402


def _config() -> EquityUniverseConfig:
    return EquityUniverseConfig(
        maximum_stream_symbols=4,
        target_shortlist=6,
        reference_symbols=("SPY",),
        scanner=EquityScannerConfig(
            minimum_price=5.0,
            minimum_median_dollar_volume=20_000_000.0,
            minimum_data_completeness=0.95,
            minimum_atr_pct=0.005,
            maximum_atr_pct=0.12,
            maximum_per_sector=5,
            volatility_optimum_pct=0.03,
            spread_quality_ceiling_bps=50.0,
            unknown_sector_penalty=2.0,
        ),
    )


def _bars(symbols: tuple[str, ...] = ("AAA", "BBB", "CCC", "SPY", "IWM", "QQQ"), days: int = 100) -> list[Bar]:
    values: list[Bar] = []
    start = datetime(2024, 1, 2, 5, tzinfo=UTC)
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for symbol_index, symbol in enumerate(symbols):
            close = 100.0 + symbol_index * 10.0 + index * (0.15 + symbol_index * 0.01)
            values.append(
                Bar(
                    timestamp=timestamp,
                    symbol=symbol,
                    timeframe="1d",
                    open=close,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=1_000_000.0,
                    market=Market.EQUITY,
                )
            )
    return values


def _expected_snapshots(bars: list[Bar], config: EquityUniverseConfig) -> list[dict[str, object]]:
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=config.maximum_stream_symbols,
        rebalance_frequency="daily",
        lookback_days=60,
        minimum_history_ratio=config.scanner.minimum_data_completeness,
        minimum_price=config.scanner.minimum_price,
        minimum_dollar_volume=config.scanner.minimum_median_dollar_volume,
        pinned_symbols=(*config.reference_symbols, *config.inverse_trade_symbols),
        target_shortlist=config.target_shortlist,
        equity_config=config,
        observation_strategy=None,
    )
    return [
        {
            "effective_at": helpers.canonical_timestamp(snapshot.effective_at),
            "symbols": sorted(snapshot.symbols),
            "scanner_version": snapshot.scanner_version,
            "config_digest": snapshot.config_digest,
            "candidate_count": snapshot.candidate_count,
        }
        for snapshot in universe.snapshots
    ]


def test_streaming_population_matches_production_scanner_small_fixture() -> None:
    bars = _bars(days=75)
    config = _config()
    actual = pit.build_population_from_bars(bars, equity_config=config)
    expected = _expected_snapshots(bars, config)
    assert actual["snapshots"]
    assert [
        {
            key: snapshot[key]
            for key in ("effective_at", "symbols", "scanner_version", "config_digest", "candidate_count")
        }
        for snapshot in actual["snapshots"]  # type: ignore[index]
    ] == expected


def test_streaming_population_matches_production_scanner_medium_fixture() -> None:
    bars = _bars(symbols=("AAA", "BBB", "CCC", "DDD", "EEE", "SPY", "IWM", "QQQ"), days=130)
    config = _config()
    actual = pit.build_population_from_bars(bars, equity_config=config)
    expected = _expected_snapshots(bars, config)
    assert [
        (snapshot["effective_at"], snapshot["symbols"], snapshot["candidate_count"])
        for snapshot in actual["snapshots"]  # type: ignore[index]
    ] == [(item["effective_at"], item["symbols"], item["candidate_count"]) for item in expected]


def test_population_digest_is_invariant_to_input_permutation() -> None:
    bars = _bars(days=85)
    shuffled = list(bars)
    random.Random(42).shuffle(shuffled)
    config = _config()
    first = pit.build_population_from_bars(bars, equity_config=config)
    second = pit.build_population_from_bars(shuffled, equity_config=config)
    assert first["population"] == second["population"]
    assert first["snapshots"] == second["snapshots"]


def test_strict_prior_boundary_and_future_bar_noninclusion() -> None:
    bars = _bars(days=80)
    config = _config()
    future = Bar(
        timestamp=datetime(2025, 1, 2, 5, tzinfo=UTC),
        symbol="FUTURE",
        timeframe="1d",
        open=10_000.0,
        high=10_100.0,
        low=9_900.0,
        close=10_000.0,
        volume=100_000_000.0,
        market=Market.EQUITY,
    )
    without_future = pit.build_population_from_bars(bars, equity_config=config)
    with_future = pit.build_population_from_bars([*bars, future], equity_config=config)
    first = with_future["snapshots"][0]  # type: ignore[index]
    assert first["strictly_prior_data"] is True
    assert first["latest_input_timestamp"] < first["effective_at"]
    assert first["symbols"] == without_future["snapshots"][0]["symbols"]  # type: ignore[index]
    assert "FUTURE" not in first["symbols"]


def test_no_alpha_and_reference_exclusion_are_explicit() -> None:
    population = pit.build_population_from_bars(_bars(days=75), equity_config=_config())
    assert population["observation_strategy"] is None
    assert population["alpha_ranking_used"] is False
    assert population["no_alpha_assertion"]["observation_strategy_is_none"] is True  # type: ignore[index]
    assert population["no_alpha_assertion"]["candidate_rankings_present"] is False  # type: ignore[index]
    assert all("observation_set" not in snapshot for snapshot in population["snapshots"])  # type: ignore[operator]
    assert all(
        not set(snapshot["eligible_symbols"]) & set(helpers.REFERENCE_SYMBOLS)
        for snapshot in helpers.deterministic_candidate_population(population["snapshots"])["snapshots"]  # type: ignore[index]
    )
    assert set(population["population"]["reference_exclusion_counts"]) == {  # type: ignore[index]
        "reference_observation_symbol"
    }


def test_scanner_version_and_digest_are_frozen() -> None:
    config = _config()
    population = pit.build_population_from_bars(_bars(days=75), equity_config=config)
    assert population["scanner_versions"] == ["equity-scanner-v3"]
    assert population["scanner_config_digests"] == [scanner_config_digest(config)]
    assert population["population"]["unique_candidate_count"] > 0  # type: ignore[index]


def test_empty_population_blocks() -> None:
    with pytest.raises(helpers.GateError, match="missing_pit_universe|empty_candidate_population|deterministic PIT"):
        pit.build_population_from_bars([], equity_config=_config())


def test_wrong_timeframe_blocks() -> None:
    bar = _bars(days=1)[0]
    wrong = Bar(
        timestamp=bar.timestamp,
        symbol=bar.symbol,
        timeframe="5m",
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        market=Market.EQUITY,
    )
    with pytest.raises(helpers.GateError, match="timeframe"):
        pit.build_population_from_bars([wrong], equity_config=_config())


def test_frozen_canonical_provider_timeframe_alias_is_normalized() -> None:
    raw = {
        "timestamp": "2024-01-02T05:00:00Z",
        "symbol": "AAA",
        "timeframe": "1Day",
        "market": "EQUITY",
        "open": "100.0",
        "high": "101.0",
        "low": "99.0",
        "close": "100.5",
        "volume": "1000000",
    }

    bar = pit._parse_bar(raw, 2)

    assert bar.timeframe == "1d"
    assert bar.market is Market.EQUITY


def test_builder_has_no_network_or_broker_imports() -> None:
    tree = ast.parse((ROOT / "scripts/build_m3_pit_scanner_universe.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint({"alpaca", "requests", "urllib", "httpx", "yfinance"})


def test_r4_excludes_high_volume_references_before_stage_a_shortlist() -> None:
    population = pit.build_population_from_bars(
        _bars(days=75),
        equity_config=_config(),
        mission=pit.R4_MISSION,
    )

    assert population["methodology"] == "strictly_prior_data_production_scanner_eligible_universe"
    snapshots = population["snapshots"]
    assert snapshots
    assert all(
        not set(snapshot["eligible_symbols"]) & set(helpers.REFERENCE_SYMBOLS)  # type: ignore[index]
        for snapshot in snapshots  # type: ignore[union-attr]
    )
    assert all(
        set(snapshot["reference_symbols_excluded"]) <= set(helpers.REFERENCE_SYMBOLS)  # type: ignore[index]
        for snapshot in snapshots  # type: ignore[union-attr]
    )
    assert set(population["population"]["reference_exclusion_counts"]) == {  # type: ignore[index]
        "pre_shortlist_reference_symbols",
        "candidate_membership_reference_symbols",
    }


def test_r4_builder_accepts_extended_predeclared_population_contract(tmp_path: Path) -> None:
    contract, identity = pit._verify_contract(
        ROOT / "data/research_manifests/equity_m3_data_gate_2026_09_r4.json",
        tmp_path,
    )
    assert contract["mission"] == "equity_m3_data_gate_2026_09_r4"
    assert identity["path"] == "data/research_manifests/equity_m3_data_gate_2026_09_r4.json"


def test_r4_records_empty_rebalance_snapshot_without_inheriting_membership() -> None:
    config = _config()
    bars = _bars(symbols=("AAA", "SPY", "IWM", "QQQ"), days=122)
    start = min(bar.timestamp for bar in bars)
    groups: list[tuple[datetime, list[Bar]]] = []
    for index in range(122):
        timestamp = start + timedelta(days=index)
        current = [bar for bar in bars if bar.timestamp == timestamp and (index <= 60 or bar.symbol != "AAA")]
        groups.append((timestamp, current))

    snapshots = pit._scan_groups(
        groups,
        equity_config=config,
        rebalance_frequency="daily",
        explicit_empty_snapshots=True,
    )

    assert snapshots[0]["scanner_result_empty"] is True
    assert snapshots[0]["eligible_symbols"] == []
    populated = [snapshot for snapshot in snapshots if "AAA" in snapshot["eligible_symbols"]]  # type: ignore[index]
    assert populated
    final = snapshots[-1]
    assert final["scanner_result_empty"] is True
    assert final["eligible_symbols"] == []
    assert final["effective_at"] > populated[-1]["effective_at"]
