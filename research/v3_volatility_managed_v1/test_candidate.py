from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3

try:
    from .candidate import (
        AuditedControlV3,
        ScalerObservation,
        VolatilityManagedEquityV3,
        daily_spy_volatility_observations,
        scaler_for_decision,
    )
except ImportError:  # pragma: no cover - direct pytest collection fallback
    from candidate import (
        AuditedControlV3,
        ScalerObservation,
        VolatilityManagedEquityV3,
        daily_spy_volatility_observations,
        scaler_for_decision,
    )


def _bar(index: int, close: float, symbol: str = "SPY") -> Bar:
    timestamp = datetime(2016, 1, 4, tzinfo=UTC)
    for _ in range(index):
        timestamp += timedelta(days=1)
        while timestamp.weekday() >= 5:
            timestamp += timedelta(days=1)
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        market=Market.EQUITY,
    )


def test_scaler_uses_prior_252_observations_and_never_exceeds_one() -> None:
    origin = datetime(2020, 1, 1, tzinfo=UTC)
    prior = tuple((origin + timedelta(days=index), 0.20) for index in range(252))
    current_timestamp = origin + timedelta(days=252)
    result = scaler_for_decision(current_timestamp, (*prior, (current_timestamp, 0.40)))
    assert result.baseline_count == 252
    assert result.basevol == pytest.approx(0.20)
    assert result.scale == pytest.approx(0.50)
    lower = scaler_for_decision(current_timestamp, (*prior, (current_timestamp, 0.10)))
    assert lower.scale == pytest.approx(1.0)
    assert 0.0 <= result.scale <= 1.0


def test_current_volatility_is_excluded_from_baseline_median() -> None:
    origin = datetime(2020, 1, 1, tzinfo=UTC)
    prior = tuple((origin + timedelta(days=index), 1.0 if index < 126 else 2.0) for index in range(252))
    current_timestamp = origin + timedelta(days=252)
    result = scaler_for_decision(current_timestamp, (*prior, (current_timestamp, 100.0)))
    assert result.basevol == pytest.approx(1.5)
    assert result.scale == pytest.approx(0.015)


def test_completed_bar_watermark_ignores_future_volatility() -> None:
    bars = [_bar(index, 100.0 + (index % 7) * 0.4) for index in range(420)]
    observations = daily_spy_volatility_observations(bars)
    decision = observations[260][0] + timedelta(hours=1)
    baseline = scaler_for_decision(decision, observations)
    future = (*observations, (decision + timedelta(days=30), 99.0))
    assert scaler_for_decision(decision, future) == baseline
    assert baseline.current_vol_timestamp is not None
    assert baseline.current_vol_timestamp <= decision


def test_treatment_preserves_v3_selection_and_scales_only_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    params = {
        "formation_bars": 252,
        "skip_bars": 21,
        "beta_window_bars": 273,
        "residual_vol_window": 63,
        "top_n": 2,
        "max_single_weight": 0.10,
        "ivol_reject_quantile": 0.70,
        "exclude_symbols": ["SPY"],
        "rebalance_frequency": "weekly",
    }

    def fixed_scores(self: object, context: PortfolioContext) -> tuple[tuple[tuple[float, str], ...], dict[str, float]]:
        del self, context
        return ((0.90, "AAA"), (0.80, "BBB")), {"AAA": 0.10, "BBB": 0.20}

    monkeypatch.setattr(EquityResidualMomentum1dV3, "_score_candidates", fixed_scores)
    timestamp = datetime(2021, 1, 4, tzinfo=UTC)
    context = PortfolioContext(
        decision_timestamp=timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history={"SPY": tuple(_bar(index, 100.0 + index * 0.1) for index in range(274))},
        universe=None,
        universe_version="synthetic",
        equity=100_000.0,
        holdings={},
        prices={"SPY": 127.3},
        benchmark_symbol="SPY",
    )
    base = EquityResidualMomentum1dV3(params).decide(context)
    control_strategy = AuditedControlV3(params)
    control = control_strategy.decide(context)
    observation = ScalerObservation(timestamp, timestamp, 0.40, 0.80, 0.50, "scaled", 252)
    treatment = VolatilityManagedEquityV3(params, scaler_by_decision={timestamp: observation}).decide(context)
    base_map = {weight.symbol: weight.weight for weight in base.weights}
    treatment_map = {weight.symbol: weight.weight for weight in treatment.weights}
    assert {weight.symbol for weight in base.weights} == {"AAA", "BBB"}
    assert treatment.metadata["selected_symbols"] == base.metadata["selected_symbols"]
    assert treatment.metadata["explanations"].keys() == base.metadata["explanations"].keys()
    assert treatment_map == {symbol: pytest.approx(weight * 0.50) for symbol, weight in base_map.items()}
    assert {weight.symbol: weight.weight for weight in control.weights} == base_map
    assert control.metadata["selected_symbols"] == base.metadata["selected_symbols"]
    assert control_strategy.audit_rows[0]["selected_symbols"] == list(base.metadata["selected_symbols"])
