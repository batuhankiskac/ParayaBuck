from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits, PortfolioRiskOverlay
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from research.spy_defensive_overlay_v1.candidate import (
    EquitySpyDefensiveOverlayV1,
    OverlayArm,
    SpyRegime,
    compose_overlay_target,
    regime_for_trend,
    spy_trend_return,
)

START = datetime(2020, 1, 1, tzinfo=UTC)
V3_PARAMETERS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}


def _bars(symbol: str, closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            START + timedelta(days=index),
            symbol,
            "1d",
            close,
            close,
            close,
            close,
            1_000_000,
            Market.EQUITY,
        )
        for index, close in enumerate(closes)
    )


def _context(spy_closes: list[float], *, sh: bool = True) -> PortfolioContext:
    history = {"SPY": _bars("SPY", spy_closes)}
    if sh:
        history["SH"] = _bars("SH", [25.0] * len(spy_closes))
    return PortfolioContext(
        decision_timestamp=START + timedelta(days=len(spy_closes) - 1),
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=None,
        universe_version="research-test",
        equity=100_000.0,
        holdings={},
        prices={symbol: bars[-1].close for symbol, bars in history.items()},
        benchmark_symbol="SPY",
    )


def _v3_target(weights: list[tuple[str, float, int]]) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="equity_residual_momentum_v3",
        strategy_version="3.0.0",
        decision_timestamp=START,
        weights=tuple(TargetWeight(symbol, weight, "residual_momentum", rank) for symbol, weight, rank in weights),
        cash_weight=1.0 - sum(weight for _symbol, weight, _rank in weights),
        rebalance_reason=RebalanceReason.SCHEDULED,
        parameters=tuple(sorted(V3_PARAMETERS.items())),
        metadata={"source": "unchanged_v3"},
    )


def _weights(target: TargetPortfolio) -> dict[str, float]:
    return {weight.symbol: weight.weight for weight in target.weights}


def _diagnostics(target: TargetPortfolio) -> dict[str, object]:
    value = target.metadata["spy_defensive_overlay"]
    assert isinstance(value, dict)
    return value


def test_signal_uses_exact_completed_t_and_t_minus_252_closes() -> None:
    closes = [100.0] * 253
    closes[-1] = 90.0
    context = _context(closes)
    assert spy_trend_return(context) == pytest.approx(-0.10)
    assert regime_for_trend(spy_trend_return(context)) == SpyRegime.DEFENSIVE

    future = _bars("SPY", [*closes, 1_000.0])
    future_context = PortfolioContext(**{**context.__dict__, "history": {**context.history, "SPY": future}})
    assert spy_trend_return(future_context) == pytest.approx(-0.10)


def test_signal_requires_253_current_completed_observations_and_zero_is_defensive() -> None:
    with pytest.raises(ValueError, match="253"):
        spy_trend_return(_context([100.0] * 252))
    assert regime_for_trend(0.0) == SpyRegime.DEFENSIVE
    assert regime_for_trend(1e-12) == SpyRegime.NORMAL


def _risk_target(target: TargetPortfolio) -> TargetPortfolio:
    return (
        PortfolioRiskOverlay(
            PortfolioRiskLimits(
                maximum_positions=6,
                maximum_equity_asset=0.10,
                maximum_equity=0.60,
                maximum_gross=0.60,
                minimum_cash=0.40,
            )
        )
        .apply(
            target,
            AccountSnapshot(100_000.0, 100_000.0, 100_000.0, 0.0),
            market=Market.EQUITY,
            approved_symbols=tuple(weight.symbol for weight in target.weights),
            maximum_allocation=0.55,
            mode="backtest",
        )
        .target
    )


def test_incumbent_and_normal_arms_preserve_exact_v3_target_before_risk() -> None:
    source = _v3_target([(f"S{rank}", 0.10, rank) for rank in range(1, 9)])
    for arm in OverlayArm:
        regime = SpyRegime.DEFENSIVE if arm == OverlayArm.INCUMBENT else SpyRegime.NORMAL
        target = compose_overlay_target(source, arm=arm, regime=regime, trend_return=0.05)
        assert target.weights == source.weights
        assert target.cash_weight == source.cash_weight
        assert _risk_target(target).weights == _risk_target(source).weights
        diagnostics = _diagnostics(target)
        assert diagnostics["intended_v3_exposure"] == pytest.approx(0.55)
        assert diagnostics["total_positions"] == 8


def test_defensive_cash_applies_fixed_045_over_055_scale_to_every_v3_name() -> None:
    source = _v3_target([(f"S{rank}", 0.10, rank) for rank in range(1, 9)])
    target = compose_overlay_target(
        source,
        arm=OverlayArm.DEFENSIVE_CASH,
        regime=SpyRegime.DEFENSIVE,
        trend_return=-0.05,
    )
    assert len(target.weights) == 8
    assert all(weight == pytest.approx(0.10 * 0.45 / 0.55) for weight in _weights(target).values())
    assert "SH" not in _weights(target)
    assert target.cash_weight == pytest.approx(1.0 - 0.8 * 0.45 / 0.55)
    diagnostics = _diagnostics(target)
    assert diagnostics["intended_overlay_sh_exposure"] == 0.0
    assert diagnostics["realized_overlay_sh_exposure"] == 0.0


def test_defensive_treatment_reserves_real_sh_slot_and_five_v3_names() -> None:
    source = _v3_target([(f"S{rank}", 0.10, rank) for rank in range(1, 9)])
    target = compose_overlay_target(
        source,
        arm=OverlayArm.DEFENSIVE_SH,
        regime=SpyRegime.DEFENSIVE,
        trend_return=-0.05,
    )
    weights = _weights(target)
    assert len(weights) == 9
    assert weights["SH"] == pytest.approx(0.10)
    assert sum(symbol != "SH" for symbol in weights) == 8
    assert next(weight for weight in target.weights if weight.symbol == "SH").rank == 1
    post_risk = _risk_target(target)
    assert len(post_risk.weights) == 6
    assert post_risk.weight_for("SH", Market.EQUITY) == pytest.approx(0.10)
    assert sum(weight.symbol != "SH" for weight in post_risk.weights) == 5
    diagnostics = _diagnostics(target)
    assert diagnostics["pre_overlay_v3_exposure"] == pytest.approx(0.8 * 0.45 / 0.55)
    assert diagnostics["realized_overlay_sh_exposure"] == pytest.approx(0.10)
    assert diagnostics["sh_slot_reserved"] is True


def test_v3_owned_sh_is_merged_capped_and_overlay_shortfall_stays_cash() -> None:
    source = _v3_target([("SH", 0.10, 8), *((f"S{rank}", 0.10, rank) for rank in range(1, 8))])
    target = compose_overlay_target(
        source,
        arm=OverlayArm.DEFENSIVE_SH,
        regime=SpyRegime.DEFENSIVE,
        trend_return=-0.05,
    )
    weights = _weights(target)
    diagnostics = _diagnostics(target)
    assert len(weights) == 8
    assert weights["SH"] == pytest.approx(0.10)
    assert diagnostics["v3_owned_sh"] == pytest.approx(0.10 * 0.45 / 0.55)
    assert diagnostics["realized_overlay_sh_exposure"] == pytest.approx(0.10 - 0.10 * 0.45 / 0.55)
    assert "sh_overlap_per_asset_cap" in diagnostics["capacity_loss_reasons"]
    assert _risk_target(target).weight_for("SH", Market.EQUITY) == pytest.approx(0.10)


def test_defensive_v3_uses_only_fixed_downscale_and_never_renormalizes() -> None:
    source = _v3_target([("AAA", 0.08, 1), ("BBB", 0.07, 2)])
    target = compose_overlay_target(
        source,
        arm=OverlayArm.DEFENSIVE_SH,
        regime=SpyRegime.DEFENSIVE,
        trend_return=-0.05,
    )
    weights = _weights(target)
    assert weights == {
        "AAA": pytest.approx(0.08 * 0.45 / 0.55),
        "BBB": pytest.approx(0.07 * 0.45 / 0.55),
        "SH": pytest.approx(0.10),
    }
    diagnostics = _diagnostics(target)
    assert diagnostics["v3_scale"] == pytest.approx(0.45 / 0.55)
    assert diagnostics["no_unused_allocation_reallocated"] is True


def test_wrapper_delegates_rank_and_decide_to_exact_v3(monkeypatch: pytest.MonkeyPatch) -> None:
    v3 = EquityResidualMomentum1dV3(dict(V3_PARAMETERS))
    source = _v3_target([(f"S{rank}", 0.10, rank) for rank in range(1, 9)])
    calls = {"rank": 0, "decide": 0}

    def rank(_context: PortfolioContext) -> tuple[str, ...]:
        calls["rank"] += 1
        return ("AAA", "BBB")

    def decide(_context: PortfolioContext) -> TargetPortfolio:
        calls["decide"] += 1
        return source

    monkeypatch.setattr(v3, "rank_candidates", rank)
    monkeypatch.setattr(v3, "decide", decide)
    strategy = EquitySpyDefensiveOverlayV1(OverlayArm.DEFENSIVE_SH, v3)
    context = _context([100.0] * 252 + [90.0])

    assert strategy.rebalance_policy_declared == "weekly"
    assert rebalance_policy_for(strategy).frequency == "weekly"
    assert strategy.rank_candidates(context) == ("AAA", "BBB")
    target = strategy.decide(context)
    assert calls == {"rank": 1, "decide": 1}
    assert target.strategy_name == strategy.name
    assert _diagnostics(target)["regime"] == "DEFENSIVE"
    assert strategy.decision_diagnostics[context.decision_timestamp] == _diagnostics(target)


def test_defensive_sh_support_fails_closed_without_current_real_sh_bar() -> None:
    v3 = EquityResidualMomentum1dV3(dict(V3_PARAMETERS))
    strategy = EquitySpyDefensiveOverlayV1(OverlayArm.DEFENSIVE_SH, v3)
    context = _context([100.0] * 273 + [90.0], sh=False)
    assert strategy.supports(context) is False
