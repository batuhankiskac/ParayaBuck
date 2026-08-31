"""Combined-account risk invariant tests for the frozen dual-market portfolio.

The ACTUAL current product combination is equity_residual_momentum_v1
(maximum allocation 0.50) + crypto_low_variance_v1 (maximum allocation 0.10)
on ONE shared account with the frozen risk limits (gross 0.60, cash buffer
0.40, crypto exposure 0.10, crypto position 0.05, max 2 crypto positions).

These tests assert OBSERVABLE invariants of that contract, loaded at test time
from config/settings.yaml (via ``parayabuck.config.load_config``), so they fail
loudly if the frozen allocation/risk contract drifts:

  * equity sleeve + crypto sleeve allocations fit inside the gross cap and
    leave the cash buffer intact (0.50 + 0.10 == 0.60, cash >= 0.40);
  * crypto exposure <= 0.10, per-crypto position <= 0.05, crypto positions
    <= 2 — enforced by the production risk overlay on a shared account;
  * the frozen strategy parameters are exactly the validated spec.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parayabuck.config import RuntimeMode, load_config
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

_EPS = 1e-9

# Frozen validated specs (docs/validated_strategies.md, strategy freeze).
# v1/v2 = historical baselines; production selection is v3 (same weekly
# cadence, frozen MH_RAW scoring identity).
_FROZEN_EQUITY_PARAMETERS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
}
_FROZEN_CRYPTO_PARAMETERS = {
    "variance_lookback_days": 22,
    "top_n": 2,
    "rebalance_hours": 168,
}
_PRODUCTION_EQUITY_PARAMETERS = {
    **_FROZEN_EQUITY_PARAMETERS,
    "rebalance_frequency": "weekly",
}
_PRODUCTION_CRYPTO_PARAMETERS = {
    **_FROZEN_CRYPTO_PARAMETERS,
    "rebalance_hours": 96,
}


def _account(
    *,
    equity_positions: tuple[tuple[str, float, float], ...] = (),
    equity: float = 100_000.0,
) -> AccountSnapshot:
    positions = tuple(
        PositionSnapshot(
            symbol=symbol,
            qty=qty,
            avg_price=price,
            current_price=price,
            market=Market.EQUITY,
            qty_available=qty,
            ownership="managed",
            managed_qty=qty,
        )
        for symbol, qty, price in equity_positions
    )
    invested = sum(position.market_value for position in positions)
    return AccountSnapshot(
        cash=equity - invested,
        equity=equity,
        peak_equity=equity,
        daily_pnl=0.0,
        positions=positions,
    )


def _target(
    strategy_name: str,
    weights: tuple[tuple[str, float], ...],
) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name=strategy_name,
        strategy_version="1.0.0",
        decision_timestamp=datetime(2025, 1, 15, 4, 0, tzinfo=UTC),
        weights=tuple(TargetWeight(symbol, weight, "test") for symbol, weight in weights),
        cash_weight=max(0.0, 1.0 - sum(weight for _symbol, weight in weights)),
        universe_version="test",
        parameters=(),
    )


def test_frozen_strategy_parameters_and_paper_mode() -> None:
    """The config selects the validated V3 equity and T1 hysteresis crypto PAPER specs;
    runtime mode
    stays paper (no live enablement)."""
    config = load_config()
    assert config.runtime.mode == RuntimeMode.PAPER
    assert config.strategy.equity.name == "equity_residual_momentum_v3"
    assert dict(config.strategy.equity.parameters) == _PRODUCTION_EQUITY_PARAMETERS
    assert config.strategy.crypto.name == "crypto_low_variance_hysteresis_v1"
    assert dict(config.strategy.crypto.parameters) == _PRODUCTION_CRYPTO_PARAMETERS


def test_combined_sleeve_allocations_fit_gross_cap_and_cash_buffer() -> None:
    """2026-08-29 fix (kripto alım arızası): equity 0.55 + crypto 0.05 == gross 0.60

    Invariant: sum(effective allocations) <= max_gross <= 1 - cash_buffer.
    This guarantees deterministic sizing regardless of market processing order
    and that a stale snapshot cannot temporarily plan 65% gross.
    """
    config = load_config()
    equity_allocation = config.strategy.equity.maximum_allocation
    crypto_allocation = config.strategy.crypto.maximum_allocation
    gross_cap = config.risk.max_gross_exposure_pct
    cash_buffer = config.risk.cash_buffer_pct
    assert equity_allocation == pytest.approx(0.55)
    assert crypto_allocation == pytest.approx(0.05)
    assert gross_cap == pytest.approx(0.60)
    assert cash_buffer == pytest.approx(0.40)
    # Invariant from config.__post_init__
    assert equity_allocation + crypto_allocation <= gross_cap + _EPS
    assert gross_cap + cash_buffer <= 1.0 + _EPS
    assert equity_allocation <= gross_cap + _EPS
    assert crypto_allocation <= cash_buffer - _EPS


def test_crypto_position_caps_match_config() -> None:
    """Crypto: exposure 0.10, single position 0.05, at most 2 positions; the
    per-position caps are consistent with the sleeve cap."""
    config = load_config()
    assert config.risk.max_crypto_exposure_pct == pytest.approx(0.10)
    assert config.risk.max_crypto_position_pct == pytest.approx(0.05)
    assert config.risk.max_crypto_positions == 2
    assert config.risk.max_crypto_positions <= config.risk.max_positions
    # Two positions at the per-position cap exactly fill the sleeve cap.
    assert 2 * config.risk.max_crypto_position_pct == pytest.approx(config.risk.max_crypto_exposure_pct)

    # 2026-08-21: the sleeve allocation (0.05) is now TIGHTER than the
    # risk-layer exposure cap; the allocation binds first.
    assert config.strategy.crypto.maximum_allocation <= (config.risk.max_crypto_exposure_pct + _EPS)


def test_risk_overlay_enforces_combined_limits_on_shared_account() -> None:
    """With the equity sleeve already at its cap on the shared account,
    the crypto sleeve is still admitted up to its (halved) allocation and the
    COMBINED account stays inside gross 0.60 / cash 0.40."""
    config = load_config()
    limits = portfolio_risk_limits_from_config(config.risk)
    overlay = PortfolioRiskOverlay(limits)

    # Equity sleeve at its cap: 5 positions x 10% = 50% of the account.
    account = _account(
        equity_positions=(
            ("S00", 100.0, 100.0),
            ("S01", 100.0, 100.0),
            ("S02", 100.0, 100.0),
            ("S03", 100.0, 100.0),
            ("S04", 100.0, 100.0),
        )
    )
    equity_decision = overlay.apply(
        _target(
            "equity_residual_momentum_v1",
            (("S00", 0.10), ("S01", 0.10), ("S02", 0.10), ("S03", 0.10), ("S04", 0.10)),
        ),
        account,
        market=Market.EQUITY,
        approved_symbols=("S00", "S01", "S02", "S03", "S04"),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        mode="backtest",
    )
    assert not equity_decision.halted
    assert equity_decision.target.invested_weight <= 0.50 + _EPS

    # Crypto decides against the SAME account holding the equity positions.
    crypto_decision = overlay.apply(
        _target("crypto_low_variance_v1", (("BTC/USD", 0.05), ("ETH/USD", 0.05))),
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        maximum_allocation=config.strategy.crypto.maximum_allocation,
        mode="backtest",
    )
    assert not crypto_decision.halted
    # 2026-08-21: the halved crypto allocation (0.05) binds before the
    # risk-layer exposure cap.
    assert crypto_decision.target.invested_weight <= 0.05 + _EPS
    combined_gross = equity_decision.target.invested_weight + crypto_decision.target.invested_weight
    assert combined_gross <= config.risk.max_gross_exposure_pct + _EPS
    assert 1.0 - combined_gross >= config.risk.cash_buffer_pct - _EPS
    # Equity at its pre-existing 0.50 level + halved crypto 0.05.
    assert combined_gross == pytest.approx(0.55)


def test_six_equity_positions_blocks_crypto_and_enforces_gross() -> None:
    """Six 10% equity positions fill gross 60%; crypto must be scaled to 0.

    Reproduces the report's secondary allocation bug: 6×0.10 = 0.60 gross,
    crypto target 0.05 must be exposure_scaled_to_limit → 0, not 0.05.
    """
    config = load_config()
    limits = portfolio_risk_limits_from_config(config.risk)
    overlay = PortfolioRiskOverlay(limits)
    # 6×10% = 60% gross
    account = _account(
        equity_positions=(
            ("S00", 100.0, 100.0),
            ("S01", 100.0, 100.0),
            ("S02", 100.0, 100.0),
            ("S03", 100.0, 100.0),
            ("S04", 100.0, 100.0),
            ("S05", 100.0, 100.0),
        )
    )
    crypto_decision = overlay.apply(
        _target("crypto_low_variance_v1", (("BTC/USD", 0.025), ("ETH/USD", 0.025))),
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD", "ETH/USD"),
        maximum_allocation=config.strategy.crypto.maximum_allocation,
        mode="backtest",
    )
    assert crypto_decision.target.invested_weight == pytest.approx(0.0, abs=1e-9)
    assert "exposure_scaled_to_limit" in crypto_decision.reasons


def test_capital_allocation_order_invariant() -> None:
    """Equity and crypto decisions must be order-invariant: both orders yield same gross."""
    config = load_config()
    limits = portfolio_risk_limits_from_config(config.risk)
    overlay = PortfolioRiskOverlay(limits)
    account = _account()
    equity_target = _target("equity_residual_momentum_v1", (("S00", 0.55),))
    crypto_target = _target("crypto_low_variance_v1", (("BTC/USD", 0.05),))
    # Order A: equity then crypto
    eq_a = overlay.apply(
        equity_target,
        account,
        market=Market.EQUITY,
        approved_symbols=("S00",),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        mode="backtest",
    )
    cr_a = overlay.apply(
        crypto_target,
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD",),
        maximum_allocation=config.strategy.crypto.maximum_allocation,
        mode="backtest",
    )
    # Order B: crypto then equity (same account snapshot)
    cr_b = overlay.apply(
        crypto_target,
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD",),
        maximum_allocation=config.strategy.crypto.maximum_allocation,
        mode="backtest",
    )
    eq_b = overlay.apply(
        equity_target,
        account,
        market=Market.EQUITY,
        approved_symbols=("S00",),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        mode="backtest",
    )
    gross_a = eq_a.target.invested_weight + cr_a.target.invested_weight
    gross_b = eq_b.target.invested_weight + cr_b.target.invested_weight
    assert gross_a == pytest.approx(gross_b)
    assert gross_a <= config.risk.max_gross_exposure_pct + _EPS
    assert cr_a.target.invested_weight == pytest.approx(0.05)
    assert cr_b.target.invested_weight == pytest.approx(0.05)


def test_risk_overlay_caps_single_crypto_asset_and_position_count() -> None:
    """One low-variance crypto cannot absorb the sleeve: a 0.5 target is
    capped to 0.05, and a 3-symbol target is cut to at most 2 positions."""
    config = load_config()
    overlay = PortfolioRiskOverlay(portfolio_risk_limits_from_config(config.risk))
    account = _account()

    single = overlay.apply(
        _target("crypto_low_variance_v1", (("BTC/USD", 0.5),)),
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        maximum_allocation=config.strategy.crypto.maximum_allocation,
        mode="backtest",
    )
    btc_weight = single.target.weight_for("BTC/USD", Market.CRYPTO)
    assert btc_weight <= config.risk.max_crypto_position_pct + _EPS
    assert single.target.invested_weight <= config.risk.max_crypto_exposure_pct + _EPS

    many = overlay.apply(
        _target(
            "crypto_low_variance_v1",
            (("BTC/USD", 0.5), ("ETH/USD", 0.3), ("SOL/USD", 0.2)),
        ),
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        maximum_allocation=config.strategy.crypto.maximum_allocation,
        mode="backtest",
    )
    assert all(weight.weight <= config.risk.max_crypto_position_pct + _EPS for weight in many.target.weights)
    assert many.target.invested_weight <= config.risk.max_crypto_exposure_pct + _EPS


def test_equity_sleeve_respects_allocation_and_position_limits() -> None:
    """8 proposed positions at 10% each are cut to max_positions and the
    equity sleeve cap (0.55 after 2026-08-29 fix)."""
    config = load_config()
    overlay = PortfolioRiskOverlay(portfolio_risk_limits_from_config(config.risk))
    account = _account()
    weights = tuple((f"S{i:02d}", 0.10) for i in range(8))
    decision = overlay.apply(
        _target("equity_residual_momentum_v1", weights),
        account,
        market=Market.EQUITY,
        approved_symbols=tuple(symbol for symbol, _weight in weights),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        mode="backtest",
    )
    assert len(decision.target.weights) <= config.risk.max_positions
    assert decision.target.invested_weight <= 0.55 + _EPS
    assert all(weight.weight <= config.risk.max_position_pct + _EPS for weight in decision.target.weights)


def test_risk_limits_dataclass_validates_contract() -> None:
    """The production risk-limits mapping preserves the frozen numbers."""
    config = load_config()
    limits = portfolio_risk_limits_from_config(config.risk)
    assert isinstance(limits, PortfolioRiskLimits)
    assert limits.maximum_gross == pytest.approx(0.60)
    assert limits.minimum_cash == pytest.approx(0.40)
    assert limits.maximum_crypto == pytest.approx(0.10)
    assert limits.maximum_crypto_asset == pytest.approx(0.05)
    assert limits.maximum_crypto_positions == 2
