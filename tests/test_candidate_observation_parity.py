from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parayabuck.backtest.observation_parity import (
    assert_strategy_identity,
    canonical_digest,
    compare_universe_bases,
    strategy_identity,
)
from parayabuck.backtest.product import build_product_universe
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot, hash_point_in_time_universe
from parayabuck.config import Config, StrategyConfig
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.universe.observation_set import build_observation_set

PARAMETERS: dict[str, object] = {
    "formation_bars": 60,
    "skip_bars": 5,
    "beta_window_bars": 80,
    "residual_vol_window": 20,
    "top_n": 4,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}


class AlphabeticalRanker(EquityResidualMomentum1dV3):
    name = "research_canary_alphabetical_v1"
    version = "1.0.0"

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        return tuple(sorted(context.history))


class ReverseRanker(EquityResidualMomentum1dV3):
    name = "research_canary_reverse_v1"
    version = "1.0.0"

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        return (*tuple(sorted(context.history, reverse=True)), "OUTSIDE_POOL")


class StatefulRanker(AlphabeticalRanker):
    name = "research_canary_stateful_v1"
    version = "1.0.0"

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        self.calls = 0
        super().__init__(parameters)

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        self.calls += 1
        return super().rank_candidates(context)


def _config() -> Config:
    base = Config()
    equity_universe = replace(
        base.universe.equity,
        maximum_stream_symbols=3,
        target_shortlist=7,
        reference_symbols=("SPY",),
        inverse_trade_symbols=(),
        fallback_trade_symbols=(),
        scanner=replace(
            base.universe.equity.scanner,
            minimum_price=1.0,
            minimum_median_dollar_volume=1.0,
            minimum_data_completeness=0.80,
            minimum_atr_pct=0.0001,
            maximum_atr_pct=0.20,
            maximum_per_sector=7,
        ),
    )
    return replace(
        base,
        universe=replace(base.universe, equity=equity_universe),
        strategy=StrategyConfig(
            equity=replace(
                base.strategy.equity,
                name="equity_residual_momentum_v3",
                timeframe="1d",
                parameters=dict(PARAMETERS),
            ),
            crypto=base.strategy.crypto,
        ),
    )


def _bars(days: int = 180) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    symbols = ("SPY", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
    bars: list[Bar] = []
    for day in range(days):
        timestamp = start + timedelta(days=day)
        for offset, symbol in enumerate(symbols):
            close = 100.0 * (1.0 + 0.0002 + offset * 0.00005) ** day
            close *= 1.0 + 0.01 * math.sin(day * 0.71 + offset)
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close * 0.999,
                    close * 1.006,
                    close * 0.994,
                    close,
                    2_000_000.0 + offset * 10_000.0,
                    Market.EQUITY,
                )
            )
    return bars


def _universe(strategy: PortfolioStrategy) -> PointInTimeUniverse:
    return build_product_universe(
        _config(),
        _bars(),
        market=Market.EQUITY,
        end=datetime(2024, 6, 28, tzinfo=UTC),
        timeframe="1d",
        observation_strategy_override=strategy,
    )


def test_default_product_universe_and_explicit_v3_override_have_zero_drift() -> None:
    config = _config()
    bars = _bars()
    default = build_product_universe(config, bars, market=Market.EQUITY, end=bars[-1].timestamp, timeframe="1d")
    explicit = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=bars[-1].timestamp,
        timeframe="1d",
        observation_strategy_override=portfolio_strategy_from(
            "equity_residual_momentum_v3", PARAMETERS, timeframe="1d"
        ),
    )
    assert hash_point_in_time_universe(default) == hash_point_in_time_universe(explicit)
    report = compare_universe_bases(default, explicit)
    assert report["passed"] is True
    assert report["mismatch_counts"]["eligible_symbols"] == 0


def test_changed_ranker_only_changes_observation_membership_and_identity() -> None:
    control = _universe(AlphabeticalRanker(PARAMETERS))
    candidate = _universe(ReverseRanker(PARAMETERS))
    report = compare_universe_bases(control, candidate)
    assert report["passed"] is True
    assert report["mismatch_counts"]["eligible_symbols"] == 0
    assert report["mismatch_counts"]["score_breakdown"] == 0
    assert report["mismatch_counts"]["configured_pins"] == 0
    assert report["mismatch_counts"]["maximum_stream_capacity"] == 0
    assert report["observation_set"]["sequence_change_count"] > 0
    assert report["observation_contract"]["candidate"]["observation_outside_pool_count"] == 0
    assert report["candidate_observation_strategy"]["names"] == ["research_canary_reverse_v1"]


def test_candidate_observation_matches_direct_builder_and_is_pit_safe() -> None:
    bars = _bars()
    config = _config()
    strategy = ReverseRanker(PARAMETERS)
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=bars[-1].timestamp,
        timeframe="1d",
        observation_strategy_override=strategy,
    )
    snapshot = universe.snapshots[-1]
    history = {
        symbol: tuple(bar for bar in bars if bar.symbol == symbol and bar.timestamp < snapshot.effective_at)
        for symbol in snapshot.symbols
    }
    decision_timestamp = max(series[-1].timestamp for series in history.values() if series)
    direct = build_observation_set(
        strategy,
        history,
        tuple(sorted(snapshot.symbols)),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=decision_timestamp,
        maximum_stream_symbols=snapshot.maximum_stream_symbols,
        pinned_symbols=snapshot.pinned_symbols,
    )
    assert snapshot.observation_set == direct.observation_set
    assert "OUTSIDE_POOL" not in snapshot.observation_set
    # A changed future cannot alter the strictly-prior snapshot.
    probe = universe.snapshots[-2]
    future = [bar for bar in bars if bar.timestamp > probe.effective_at]
    changed = [
        Bar(
            bar.timestamp,
            bar.symbol,
            bar.timeframe,
            bar.close * 0.999,
            bar.close * 4.0,
            bar.close * 0.996,
            bar.close * 3.9,
            bar.volume * 0.01,
            bar.market,
        )
        for bar in future
    ]
    prefix = [bar for bar in bars if bar.timestamp <= probe.effective_at]
    mutated = build_product_universe(
        config,
        [*prefix, *changed],
        market=Market.EQUITY,
        end=bars[-1].timestamp,
        timeframe="1d",
        observation_strategy_override=ReverseRanker(PARAMETERS),
    )
    assert next(item for item in mutated.snapshots if item.effective_at == probe.effective_at) == probe


def test_override_validation_is_fail_closed() -> None:
    config = _config()
    bars = _bars()
    with pytest.raises(TypeError, match="PortfolioStrategy"):
        build_product_universe(
            config,
            bars,
            market=Market.EQUITY,
            timeframe="1d",
            observation_strategy_override=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="market"):
        build_product_universe(
            config,
            bars,
            market=Market.EQUITY,
            timeframe="1d",
            observation_strategy_override=portfolio_strategy_from("crypto_low_variance_v1"),
        )
    with pytest.raises(ValueError, match="timeframe"):
        build_product_universe(
            config,
            bars,
            market=Market.EQUITY,
            timeframe="1h",
            observation_strategy_override=AlphabeticalRanker(PARAMETERS),
        )


def test_comparator_fails_closed_on_missing_or_reordered_snapshot_boundaries() -> None:
    first = UniverseSnapshot(datetime(2025, 1, 1, tzinfo=UTC), frozenset({"SPY", "AAA"}))
    second = UniverseSnapshot(datetime(2025, 1, 2, tzinfo=UTC), frozenset({"SPY", "BBB"}))
    control = PointInTimeUniverse(Market.EQUITY, (first, second), "same")
    missing = PointInTimeUniverse(Market.EQUITY, (first,), "same")
    missing_report = compare_universe_bases(control, missing)
    assert missing_report["passed"] is False
    assert missing_report["effective_at_alignment"]["missing_from_candidate"]
    reordered = SimpleNamespace(market=Market.EQUITY, methodology="same", snapshots=(second, first))
    reordered_report = compare_universe_bases(control, reordered)  # type: ignore[arg-type]
    assert reordered_report["passed"] is False
    assert reordered_report["effective_at_alignment"]["reordered"] is True


def test_comparator_reports_pin_and_capacity_contract_violations() -> None:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    control = PointInTimeUniverse(
        Market.EQUITY,
        (
            UniverseSnapshot(
                timestamp,
                frozenset({"SPY", "AAA", "BBB"}),
                observation_set=("SPY", "AAA"),
                maximum_stream_symbols=2,
                pinned_symbols=("SPY",),
            ),
        ),
        "same",
    )
    invalid = PointInTimeUniverse(
        Market.EQUITY,
        (
            UniverseSnapshot(
                timestamp,
                frozenset({"SPY", "AAA", "BBB"}),
                observation_set=("AAA", "SPY", "AAA"),
                maximum_stream_symbols=2,
                pinned_symbols=("SPY",),
            ),
        ),
        "same",
    )
    report = compare_universe_bases(control, invalid)
    assert report["passed"] is False
    assert report["observation_contract"]["candidate"]["observation_duplicate_count"] == 1
    assert report["observation_contract"]["candidate"]["pins_not_first_count"] == 1


def test_strategy_identity_digest_and_fresh_instances() -> None:
    first = StatefulRanker(PARAMETERS)
    second = StatefulRanker(PARAMETERS)
    assert strategy_identity(first)["parameter_digest_sha256"] == strategy_identity(second)["parameter_digest_sha256"]
    assert canonical_digest({"x": -0.0}) == canonical_digest({"x": 0.0})
    assert canonical_digest({"x": (1, 2)}) == canonical_digest({"x": [1, 2]})
    assert_strategy_identity(first, second)
    with pytest.raises(ValueError, match="fresh instances"):
        assert_strategy_identity(first, first)


def test_research_prepare_window_uses_fresh_candidate_ranker_and_records_basis() -> None:
    from scripts import research_v3_equity_roadmap as roadmap

    config = _config()
    bars = _bars(120)
    created: list[StatefulRanker] = []

    def factory() -> PortfolioStrategy:
        strategy = StatefulRanker(PARAMETERS)
        created.append(strategy)
        return strategy

    window = roadmap.prepare_window(
        config,
        bars,
        key="candidate-canary",
        start=None,
        end=bars[-1].timestamp,
        observation_strategy_factory=factory,
        execution_strategy_factory=factory,
    )
    assert window.observation_basis == "candidate_owned"
    assert window.observation_strategy_name == StatefulRanker.name
    assert window.observation_strategy_version == StatefulRanker.version
    assert window.observation_strategy_parameter_digest
    assert len(created) == 2
    assert created[0] is not created[1]
