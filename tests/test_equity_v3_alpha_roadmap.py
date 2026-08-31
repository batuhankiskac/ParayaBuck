from __future__ import annotations

import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import EquityResidualMomentum1dV3, supported_portfolio_strategies
from parayabuck.portfolio.base import PortfolioContext

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_v3_equity_roadmap as roadmap  # noqa: E402


class _Universe:
    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        del symbol, market, timestamp
        return True


def _context() -> PortfolioContext:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    history: dict[str, tuple[Bar, ...]] = {}
    for symbol, drift, phase in (
        ("SPY", 0.0002, 0.0),
        ("AAA", 0.0012, 0.4),
        ("BBB", 0.0008, 1.1),
        ("CCC", -0.0004, 1.8),
        ("DDD", 0.0005, 2.5),
        ("EEE", -0.0001, 3.2),
    ):
        close = 100.0
        bars: list[Bar] = []
        for index in range(300):
            close *= 1.0 + drift + 0.002 * math.sin(index * 1.7 + phase)
            bars.append(
                Bar(
                    timestamp=start + timedelta(days=index),
                    symbol=symbol,
                    timeframe="1d",
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1_000_000,
                    market=Market.EQUITY,
                )
            )
        history[symbol] = tuple(bars)
    return PortfolioContext(
        decision_timestamp=start + timedelta(days=299),
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=_Universe(),
        universe_version="test-universe",
        equity=100_000.0,
        holdings={},
        prices={symbol: bars[-1].close for symbol, bars in history.items()},
        benchmark_symbol="SPY",
    )


def test_manifest_is_predeclared_and_candidates_stay_out_of_product_registry() -> None:
    manifest = roadmap.load_and_validate_manifest()
    assert manifest["declared_before_results"] is True
    assert roadmap.CURRENT_EQUITY_ALLOCATION == 0.55
    registered = set(supported_portfolio_strategies())
    for cls, _overrides in roadmap.VARIANTS.values():
        if cls is not roadmap.RoadmapControl:
            assert cls.name not in registered
    assert "equity_residual_momentum_v3" in registered


def test_roadmap_control_matches_direct_v3_targets_and_ranks() -> None:
    context = _context()
    direct = EquityResidualMomentum1dV3(dict(roadmap.FROZEN_PARAMETERS))
    control = roadmap.RoadmapControl(dict(roadmap.FROZEN_PARAMETERS))
    assert control.rank_candidates(context) == direct.rank_candidates(context)
    direct_target = direct.decide(context)
    control_target = control.decide(context)
    assert [(item.symbol, item.weight, item.rank) for item in control_target.weights] == [
        (item.symbol, item.weight, item.rank) for item in direct_target.weights
    ]
    assert control_target.cash_weight == pytest.approx(direct_target.cash_weight)


def test_hysteresis_keeps_rank_9_to_12_and_refills_only_from_entry_band() -> None:
    ranked = tuple((1.0 - index / 20.0, f"S{index:02d}") for index in range(12))
    selected = roadmap.hysteresis_selection(
        ranked,
        {"S09": 1.0, "OUT": 2.0},
        top_n=8,
        entry_rank=8,
        hold_rank=12,
    )
    assert selected == ("S09", "S00", "S01", "S02", "S03", "S04", "S05", "S06")
    strategy = roadmap.RoadmapHysteresis(dict(roadmap.FROZEN_PARAMETERS))
    assert strategy._target_rank_by_symbol(ranked, selected)["S09"] == 10


def test_path_id_and_breadth_gate_are_predeclared() -> None:
    residuals = [0.01, 0.01, 0.01, -0.001, 0.0]
    assert roadmap.information_discreteness(residuals, formation=4, skip=1) == pytest.approx(-0.5)
    mixed = [-0.01] * 63 + [0.003] * 189 + [0.0] * 21
    breadth = roadmap.RoadmapBreadth(dict(roadmap.FROZEN_PARAMETERS))
    assert breadth._candidate_passes_gate(mixed, -0.1, formation=252, skip=21)


def test_capped_waterfill_hits_target_and_per_name_cap() -> None:
    weights = roadmap.capped_waterfill({"A": 1.0, "B": 0.5, "C": 0.25}, total=0.25, cap=0.10)
    assert sum(weights.values()) == pytest.approx(0.25)
    assert max(weights.values()) <= 0.10 + 1e-12
    assert weights["A"] >= weights["B"] >= weights["C"]


def test_score_risk_uses_symbol_to_score_mapping() -> None:
    strategy = roadmap.RoadmapScoreRisk(dict(roadmap.FROZEN_PARAMETERS))
    weights = strategy._position_weights_for_ranked(
        tuple((score, symbol) for score, symbol in zip((0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2), "ABCDEFGH")),
        tuple("ABCDEFGH"),
        dict.fromkeys("ABCDEFGH", 0.2),
        _context(),
    )
    assert sum(weights.values()) == pytest.approx(0.55)
    assert max(weights.values()) <= 0.10 + 1e-12
    assert weights["A"] >= weights["B"] >= weights["C"]


def test_deployable_six_keeps_v3_rank_and_targets_equal_ten_percent() -> None:
    strategy = roadmap.build_strategy("deployable6_equal")
    assert strategy.name == "equity_residual_momentum_deployable6_equal_v1"
    assert strategy.param_int("top_n", 8) == 6

    selected = tuple("ABCDEF")
    weights = strategy._position_weights(selected, dict.fromkeys(selected, 0.2))
    assert weights == dict.fromkeys(selected, 0.10)
    assert sum(weights.values()) == pytest.approx(0.60)
    assert strategy._weight_diagnostics[-1]["method"] == "equal_weight_deployable_six"


def test_high_proximity_ignores_future_bar_and_reversal_uses_last_five() -> None:
    context = _context()
    symbol = "AAA"
    history = list(context.history[symbol])
    future = history[-1]
    history.append(
        Bar(
            timestamp=context.decision_timestamp + timedelta(days=1),
            symbol=symbol,
            timeframe="1d",
            open=10_000.0,
            high=10_000.0,
            low=10_000.0,
            close=10_000.0,
            volume=1_000_000,
            market=Market.EQUITY,
        )
    )
    future_context = PortfolioContext(
        decision_timestamp=context.decision_timestamp,
        market=context.market,
        timeframe=context.timeframe,
        history={**context.history, symbol: tuple(history)},
        universe=context.universe,
        universe_version=context.universe_version,
        equity=context.equity,
        holdings=context.holdings,
        prices=context.prices,
        benchmark_symbol=context.benchmark_symbol,
    )
    high = roadmap.Roadmap52WeekHigh(dict(roadmap.FROZEN_PARAMETERS))
    transformed = high._transform_cross_sectional_scores_with_context(
        future_context,
        [(0.1, symbol)],
        {symbol: [0.001] * 273},
        {symbol: 0.1},
        formation=252,
        skip=21,
    )
    expected = future.close / max(bar.close for bar in context.history[symbol][-252:])
    assert transformed[0][0] == pytest.approx(expected)

    reversal = roadmap.RoadmapReversal(dict(roadmap.FROZEN_PARAMETERS))
    residuals = [0.0] * 268 + [-0.01, -0.01, -0.01, -0.01, -0.01]
    assert reversal._candidate_score(residuals, formation=252, skip=21) > 0
