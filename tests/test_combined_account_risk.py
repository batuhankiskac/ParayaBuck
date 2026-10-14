"""Account risk invariant tests for the frozen equity portfolio.

The ACTUAL current product is equity_leveraged_trend_v1 (maximum allocation
0.98) on ONE brokerage account with the shipped risk limits (gross 0.98,
cash buffer 0.02).

These tests assert OBSERVABLE invariants of that contract, loaded at test time
from config/settings.yaml (via ``parayabuck.config.load_config``), so they fail
loudly if the frozen allocation/risk contract drifts:

  * the equity allocation fits inside the gross cap and leaves the cash
    buffer intact (0.98 == 0.98, cash >= 0.02);
  * the production risk overlay enforces the sleeve, position-count and
    per-asset caps on the account;
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

# Frozen validated spec (docs/validated_strategies.md, strategy freeze).
_PRODUCTION_EQUITY_PARAMETERS = {
    "signal_symbol": "QQQ",
    "hold_symbol": "QLD",
    "sma_bars": 200,
    "vol_target": 0.35,
    "vol_window": 20,
    "rebalance_frequency": "hours:24",
    # 2026-10-12 (e2_integration_v1): E2 2-of-3 trend vote.
    "trend_rule": "majority_sma_tsm_don",
    "tsm_bars": 252,
    "donchian_bars": 126,
    # 2026-10-14 (fast_reentry_integration_v1): R2 early re-entry path.
    "early_reentry_weight": 0.5,
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
    """The config selects the leveraged-trend equity PAPER spec; runtime mode
    stays paper (no live enablement)."""
    config = load_config()
    assert config.runtime.mode == RuntimeMode.PAPER
    assert config.strategy.equity.name == "equity_leveraged_trend_v1"
    assert dict(config.strategy.equity.parameters) == _PRODUCTION_EQUITY_PARAMETERS


def test_equity_allocation_fits_gross_cap_and_cash_buffer() -> None:
    """2026-09-29 (paper_config_v2): equity 0.98 == gross 0.98

    Invariant: effective allocation <= max_gross <= 1 - cash_buffer.
    This guarantees a stale snapshot cannot temporarily plan more than the
    gross cap.
    """
    config = load_config()
    equity_allocation = config.strategy.equity.maximum_allocation
    gross_cap = config.risk.max_gross_exposure_pct
    cash_buffer = config.risk.cash_buffer_pct
    assert equity_allocation == pytest.approx(0.98)
    assert config.risk.max_position_pct == pytest.approx(0.98)
    assert gross_cap == pytest.approx(0.98)
    assert cash_buffer == pytest.approx(0.02)
    # Invariant from config.__post_init__
    assert gross_cap + cash_buffer <= 1.0 + _EPS
    assert equity_allocation <= gross_cap + _EPS


def test_risk_overlay_enforces_account_limits_with_existing_inventory() -> None:
    """With managed equity inventory already on the account, the equity
    sleeve target is admitted up to its single-ETF weight and the account
    stays inside gross 0.98 / cash 0.02."""
    config = load_config()
    limits = portfolio_risk_limits_from_config(config.risk)
    overlay = PortfolioRiskOverlay(limits)

    # Account already carries 50% in managed equity positions; the equity
    # sleeve targets a single-ETF weight (0.93) on the same account.
    account = _account(
        equity_positions=(
            ("S00", 100.0, 100.0),
            ("S01", 100.0, 100.0),
            ("S02", 100.0, 100.0),
            ("S03", 100.0, 100.0),
            ("S04", 100.0, 100.0),
        )
    )
    approved_equity = ("S00", "S01", "S02", "S03", "S04", "QLD")
    equity_decision = overlay.apply(
        _target("equity_leveraged_trend_v1", (("QLD", 0.93),)),
        account,
        market=Market.EQUITY,
        approved_symbols=approved_equity,
        maximum_allocation=config.strategy.equity.maximum_allocation,
        mode="backtest",
    )
    assert not equity_decision.halted
    assert sum(w.weight for w in equity_decision.target.weights) == pytest.approx(0.93)
    gross = sum(w.weight for w in equity_decision.target.weights)
    assert gross <= config.risk.max_gross_exposure_pct + _EPS
    assert 1.0 - gross >= config.risk.cash_buffer_pct - _EPS


def test_frozen_equity_sleeve_respects_configured_caps() -> None:
    """9 proposed positions at 10% each are cut to max_positions (8) and stay
    inside the equity sleeve cap (0.98) and the per-asset cap."""
    config = load_config()
    overlay = PortfolioRiskOverlay(portfolio_risk_limits_from_config(config.risk))
    account = _account()
    weights = tuple((f"S{i:02d}", 0.10) for i in range(9))
    decision = overlay.apply(
        _target("equity_leveraged_trend_v1", weights),
        account,
        market=Market.EQUITY,
        approved_symbols=tuple(symbol for symbol, _weight in weights),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        mode="backtest",
    )
    assert "position_count_limit" in decision.reasons
    assert len(decision.target.weights) <= config.risk.max_positions
    assert sum(w.weight for w in decision.target.weights) <= config.strategy.equity.maximum_allocation + _EPS
    assert all(weight.weight <= config.risk.max_position_pct + _EPS for weight in decision.target.weights)


def test_risk_limits_dataclass_validates_contract() -> None:
    """The production risk-limits mapping preserves the frozen numbers."""
    config = load_config()
    limits = portfolio_risk_limits_from_config(config.risk)
    assert isinstance(limits, PortfolioRiskLimits)
    assert limits.maximum_gross == pytest.approx(0.98)
    assert limits.minimum_cash == pytest.approx(0.02)
