from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestResult, RebalanceRecord
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from research.spy_defensive_overlay_v1.candidate import EquitySpyDefensiveOverlayV1, OverlayArm
from research.spy_defensive_overlay_v1.replay import (
    CampaignRun,
    capacity_rows,
    period_metrics,
    scheduled_weekly_timestamps,
    whole_account_capacity_scenarios,
)


def test_whole_account_capacity_counts_sh_as_real_slot() -> None:
    scenarios = whole_account_capacity_scenarios()
    assert scenarios[-1] == {
        "crypto_positions": 2,
        "available_equity_slots": 4,
        "defensive_sh_slots": 1,
        "maximum_non_sh_v3_slots": 3,
        "maximum_equity_exposure_at_0p10_cap": pytest.approx(0.4),
        "equity_under_utilization": pytest.approx(0.15),
    }


def test_capacity_rows_separates_overlay_owned_sh() -> None:
    timestamp = datetime(2022, 1, 3, tzinfo=UTC)
    strategy = EquitySpyDefensiveOverlayV1(
        OverlayArm.DEFENSIVE_SH,
        EquityResidualMomentum1dV3(
            {
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
        ),
    )
    strategy.decision_diagnostics[timestamp] = {
        "regime": "DEFENSIVE",
        "trend_return": -0.1,
        "intended_v3_exposure": 0.45,
        "intended_overlay_sh_exposure": 0.10,
        "v3_owned_sh": 0.02,
        "capacity_loss_reasons": [],
        "source_v3_weights": [{"symbol": "AAA", "weight": 0.45}],
    }
    record = RebalanceRecord(
        decision_timestamp=timestamp,
        reason="scheduled",
        target_weights=(("AAA", 0.30), ("SH", 0.10)),
        target_cash_weight=0.60,
        pre_trade_weights=(),
        signed_trade_weights=(),
        post_fill_actual_weights=(),
        target_vs_actual_drift=0.0,
        unfilled_notional=0.0,
        rejected_notional=0.0,
        expected_turnover=0.0,
        realized_turnover=0.0,
        cost=0.0,
        executed_weights=(),
        universe_version="u",
        universe_snapshot_id="s",
        equity_at_decision=100_000,
        rebalance_number=1,
    )
    result = object.__new__(PortfolioBacktestResult)
    object.__setattr__(result, "rebalances", (record,))
    run = CampaignRun(OverlayArm.DEFENSIVE_SH, 1, strategy, result)
    row = capacity_rows(run)[0]
    assert row["realized_overlay_sh_exposure"] == pytest.approx(0.08)
    assert row["realized_v3_exposure"] == pytest.approx(0.32)
    assert row["capacity_loss_unused_equity_allocation"] == pytest.approx(0.15)
    assert "position_slot_limit" in row["capacity_loss_reasons"]


def test_weekly_schedule_uses_first_spy_session() -> None:
    from parayabuck.core.models import Bar, Market

    start = datetime(2017, 1, 3, tzinfo=UTC)
    bars = [Bar(start + timedelta(days=offset), "SPY", "1d", 1, 1, 1, 1, 1, Market.EQUITY) for offset in (0, 1, 6, 7)]
    assert scheduled_weekly_timestamps(bars) == [start, start + timedelta(days=6)]


def test_period_metrics_rejects_empty_window() -> None:
    result = object.__new__(PortfolioBacktestResult)
    object.__setattr__(result, "curve_timestamps", ())
    object.__setattr__(result, "equity_curve", ())
    with pytest.raises(ValueError, match="No curve points"):
        period_metrics(result, datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 2, 1, tzinfo=UTC))
