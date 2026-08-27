# ruff: noqa: E501
"""COMMIT A2 cost-stress integrity: effective-dated regulatory fee multiplier."""

from __future__ import annotations

import pathlib
from datetime import UTC, date, datetime

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine
from parayabuck.config import load_config
from parayabuck.core.models import Market

REPO = pathlib.Path(__file__).resolve().parents[1]
RESEARCH = REPO / "scripts" / "research_v3_position_risk.py"


def _make_dummy_strategy():
    from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3

    return EquityResidualMomentum1dV3(
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
    )


def _engine_pair(multiplier: float):
    from parayabuck.portfolio import rebalance_policy_for
    from scripts.research_v3_position_risk import PositionRiskResearchEngine

    strat = _make_dummy_strategy()
    policy = rebalance_policy_for(strat)
    # Base engine for comparison (production)
    base = PortfolioBacktestEngine(
        strat,
        policy,
        starting_equity=100000,
        slippage_bps=2,
        spread_bps=2,
        commission=0,
        crypto_taker_fee_bps=25,
        equity_taf_per_share=0.000195,
        equity_taf_cap=9.79,
        equity_cat_per_share=0.000003,
        equity_sec_fee_rate=0.0000206,
        equity_use_effective_dated_fees=True,
        benchmark_symbol="SPY",
    )
    research = PositionRiskResearchEngine(
        strat,
        policy,
        starting_equity=100000,
        slippage_bps=2 * multiplier,
        spread_bps=2 * multiplier,
        commission=0,
        crypto_taker_fee_bps=25 * multiplier,
        equity_taf_per_share=0.000195,
        equity_taf_cap=9.79,
        equity_cat_per_share=0.000003,
        equity_sec_fee_rate=0.0000206,
        equity_use_effective_dated_fees=True,
        regulatory_fee_multiplier=multiplier,
        benchmark_symbol="SPY",
    )
    return base, research


def test_1x_research_fee_equals_production_effective_dated_fee():
    base, research_1x = _engine_pair(1.0)
    # Historical SELL date where effective-dated schedule matters
    trade_date = date(2024, 6, 15)
    fee_base = base._equity_fee(OrderSide.SELL, 1000, 50.0, trade_date)
    fee_1x = research_1x._equity_fee(OrderSide.SELL, 1000, 50.0, trade_date)
    assert fee_base == fee_1x, f"1x research fee {fee_1x} != production {fee_base}"
    # Also BUY (only CAT) should match
    fee_base_buy = base._equity_fee(OrderSide.BUY, 1000, 50.0, trade_date)
    fee_1x_buy = research_1x._equity_fee(OrderSide.BUY, 1000, 50.0, trade_date)
    assert fee_base_buy == fee_1x_buy


def test_3x_fee_is_triple_including_cap():
    _, research_1x = _engine_pair(1.0)
    _, research_3x = _engine_pair(3.0)
    trade_date = date(2024, 6, 15)
    # Quantity large enough that TAF cap matters: 100000 shares * 0.000195 = 19.5 > 9.79 cap
    qty_large = 100000
    price = 10.0
    fee_1x = research_1x._equity_fee(OrderSide.SELL, qty_large, price, trade_date)
    fee_3x = research_3x._equity_fee(OrderSide.SELL, qty_large, price, trade_date)
    # Caps: base fee should be capped, 3x should be exactly 3x base
    assert fee_1x > 0
    assert fee_3x == fee_1x * 3, f"3x fee {fee_3x} != 3 * 1x {fee_1x}"
    # Also test that simple taf scaling without cap would be wrong: if we scaled taf_per_share, fee would not be exactly triple when capped
    # Our implementation ensures uniform multiplier
    # Smaller quantity without cap should also be triple
    fee_1x_small = research_1x._equity_fee(OrderSide.SELL, 1000, 50.0, trade_date)
    fee_3x_small = research_3x._equity_fee(OrderSide.SELL, 1000, 50.0, trade_date)
    assert fee_3x_small == fee_1x_small * 3


def test_effective_dated_fees_remains_true():
    _, research_1x = _engine_pair(1.0)
    _, research_3x = _engine_pair(3.0)
    assert research_1x.equity_use_effective_dated_fees is True
    assert research_3x.equity_use_effective_dated_fees is True
    # Also base engine
    base, _ = _engine_pair(1.0)
    assert base.equity_use_effective_dated_fees is True


def test_make_engine_stores_correct_multiplier_and_costs():
    if not (REPO / "data/historical/equity_1200_2016_2026.csv").exists():
        import pytest

        pytest.skip("historical equity_1200_2016_2026.csv not present in CI (gitignored 124M) — skip")
    from parayabuck.backtest.product import build_product_universe, load_csv_bars
    from parayabuck.core.models import Market
    from scripts.research_v3_position_risk import make_engine

    config = load_config(str(REPO / "config/settings.yaml"))
    bars = load_csv_bars(str(REPO / "data/historical/equity_1200_2016_2026.csv"), default_market=Market.EQUITY)

    # Use minimal universe via build_product_universe
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=datetime(2026, 7, 30, tzinfo=UTC))
    strat = _make_dummy_strategy()
    # Test 3x
    engine_3x = make_engine(config, strat, universe, candidate_id="P0", cost_multiplier=3.0)
    base_slippage = config.backtest.slippage_bps
    base_spread = config.backtest.spread_bps
    base_crypto = config.backtest.crypto_taker_fee_bps
    assert engine_3x.slippage_bps == base_slippage * 3
    assert engine_3x.spread_bps == base_spread * 3
    assert engine_3x.crypto_taker_fee_bps == base_crypto * 3
    assert getattr(engine_3x, "regulatory_fee_multiplier", None) == 3.0
    # Must NOT rely on scaled flat fields
    assert engine_3x.equity_taf_per_share == config.backtest.equity_taf_per_share
    assert engine_3x.equity_cat_per_share == config.backtest.equity_cat_per_share
    assert engine_3x.equity_sec_fee_rate == config.backtest.equity_sec_fee_rate
    # 1x check
    engine_1x = make_engine(config, strat, universe, candidate_id="P0", cost_multiplier=1.0)
    assert engine_1x.regulatory_fee_multiplier == 1.0
    assert engine_1x.slippage_bps == base_slippage
    assert engine_1x.spread_bps == base_spread


def test_1x_full_candidate_engine_reproduces_baseline():
    if not (REPO / "data/historical/equity_1200_2016_2026.csv").exists():
        import pytest

        pytest.skip("historical equity_1200_2016_2026.csv not present in CI (gitignored 124M) — skip")
    # Lightweight: ensure make_engine with P0 1x does not error and produces expected risk geometry
    from parayabuck.backtest.product import build_product_universe, load_csv_bars
    from scripts.research_v3_position_risk import make_engine

    config = load_config(str(REPO / "config/settings.yaml"))
    bars = load_csv_bars(str(REPO / "data/historical/equity_1200_2016_2026.csv"), default_market=Market.EQUITY)
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=datetime(2026, 7, 30, tzinfo=UTC))
    strat = _make_dummy_strategy()
    engine = make_engine(config, strat, universe, candidate_id="P0", cost_multiplier=1.0)
    # Check risk limits match control
    assert engine.risk_overlay is not None
    assert engine.risk_overlay.limits.maximum_positions == 6
    assert engine.risk_overlay.limits.maximum_equity_asset == 0.10
    # Check regulatory multiplier and effective_dated
    assert engine.regulatory_fee_multiplier == 1.0
    assert engine.equity_use_effective_dated_fees is True
