"""Drawdown liquidation brake (``risk.drawdown_liquidation``, default off).

A drawdown from peak equity at or above the threshold zeroes every MANAGED
target; external units are never sold, the reduction is a risk-driven one (it
bypasses the gain band and the December deferral), and the RED-state can be
carried between decisions by an injected ``DrawdownLiquidationState``. Every
fixture is synthetic and deterministic: no network, no production path, no
files outside the pytest-provided ``tmp_path``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from parayabuck.config import DownsizeGainBand, DrawdownLiquidationConfig, RiskLimits, load_config
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    DRAWDOWN_LIQUIDATION_REASON,
    AssetTradingRules,
    DrawdownLiquidationState,
    PortfolioExecutionPolicy,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    TargetToOrderPlanner,
    gain_band_bypass_symbols,
    portfolio_risk_limits_from_config,
)
from parayabuck.markets.nyse_calendar import NEW_YORK
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

SYMBOL = "QLD"
PRICE = 100.0
T0 = datetime(2026, 10, 5, 20, tzinfo=UTC)  # NYSE session 2026-10-05
DECEMBER = datetime(2025, 12, 16, tzinfo=NEW_YORK)  # inside the last 10 December sessions
ON = DrawdownLiquidationConfig(enabled=True, threshold_pct=0.45, rearm_recovery_pct=0.30, cooldown_sessions=5)
REPO_SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"


def _limits(**overrides: object) -> PortfolioRiskLimits:
    values: dict[str, object] = {
        "daily_loss_limit": 0.20,
        "drawdown_halt": 0.75,
        "maximum_gross": 0.98,
        "maximum_equity": 0.98,
        "maximum_equity_asset": 0.98,
        "minimum_cash": 0.02,
        "maximum_positions": 8,
    }
    values.update(overrides)
    return PortfolioRiskLimits(**values)  # type: ignore[arg-type]


def _account(
    *,
    equity: float = 110_000.0,
    peak: float = 110_000.0,
    managed: float = 0.0,
    external: float = 0.0,
    daily_pnl: float = 0.0,
) -> AccountSnapshot:
    qty = managed + external
    positions: tuple[PositionSnapshot, ...] = ()
    if qty > 0:
        positions = (
            PositionSnapshot(
                SYMBOL,
                qty,
                PRICE,
                PRICE,
                Market.EQUITY,
                qty_available=qty,
                ownership="managed",
                managed_qty=managed,
            ),
        )
    return AccountSnapshot(
        cash=equity - qty * PRICE,
        equity=equity,
        peak_equity=peak,
        daily_pnl=daily_pnl,
        positions=positions,
    )


def _tw(weight: float) -> TargetWeight:
    """An overlay-produced target weight (the overlay stamps its own source)."""
    return TargetWeight(SYMBOL, weight, "portfolio_risk_overlay")


def _target(weight: float, timestamp: datetime = T0) -> TargetPortfolio:
    weights = (TargetWeight(SYMBOL, weight),) if weight > 0 else ()
    return TargetPortfolio("vt35", "1.2.0", timestamp, weights, 1.0 - weight, universe_version="test")


def _decide(
    account: AccountSnapshot,
    *,
    weight: float = 0.5,
    liquidation: DrawdownLiquidationConfig | None = None,
    state: DrawdownLiquidationState | None = None,
    session: date | None = None,
    enforce: bool = True,
    limits: PortfolioRiskLimits | None = None,
    timestamp: datetime = T0,
):
    return PortfolioRiskOverlay(limits or _limits(), drawdown_liquidation=liquidation).apply(
        _target(weight, timestamp),
        account,
        market=Market.EQUITY,
        approved_symbols=(SYMBOL,),
        maximum_allocation=1.0,
        mode="paper",
        enforce_drawdown_halt=enforce,
        drawdown_liquidation_state=state,
        session_date=session,
    )


def _orders(decision, account, *, policy: PortfolioExecutionPolicy | None = None, deferrals=None, holds=None):
    return TargetToOrderPlanner(policy or PortfolioExecutionPolicy()).plan(
        decision.target,
        account,
        market=Market.EQUITY,
        prices={SYMBOL: PRICE},
        rules={SYMBOL: AssetTradingRules(0.001, 0.001, 1.0)},
        approved_symbols=(SYMBOL,),
        decision_id="dd-liquidation",
        gain_band_holds=holds,
        december_deferral_holds=deferrals,
    )


# Scenarios where today's behaviour is pinned: a breached latch caps targets
# at the current weight, a deep drawdown below the halt keeps the target, and
# a clean account is untouched. The disabled brake must reproduce each one.
SCENARIOS = {
    "healthy": (_account(), 0.5, _limits()),
    "drawdown_below_halt": (_account(equity=120_000.0, peak=200_000.0, managed=500.0), 0.3, _limits()),
    "deep_drawdown_no_halt": (_account(equity=80_000.0, peak=200_000.0, managed=500.0), 0.5, _limits()),
    "drawdown_halt_latch": (
        _account(equity=100_000.0, peak=200_000.0, managed=500.0),
        0.7,
        _limits(drawdown_halt=0.45),
    ),
    "daily_loss_latch": (_account(daily_pnl=-30_000.0, managed=500.0), 0.3, _limits()),
    "mixed_external": (_account(managed=300.0, external=600.0), 0.8, _limits()),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_disabled_brake_reproduces_todays_overlay_exactly(name: str) -> None:
    account, weight, limits = SCENARIOS[name]
    baseline = _decide(account, weight=weight, limits=limits)
    no_config = PortfolioRiskOverlay(limits).apply(
        _target(weight),
        account,
        market=Market.EQUITY,
        approved_symbols=(SYMBOL,),
        maximum_allocation=1.0,
        mode="paper",
    )
    # Even an aggressive threshold is inert while the brake is disabled.
    disabled = _decide(
        account,
        weight=weight,
        limits=limits,
        liquidation=DrawdownLiquidationConfig(enabled=False, threshold_pct=0.10, rearm_recovery_pct=0.05),
    )
    assert disabled == baseline == no_config
    assert DRAWDOWN_LIQUIDATION_REASON not in disabled.reasons
    assert disabled.drawdown_liquidation_state is None


def test_disabled_brake_passes_an_injected_state_through_unchanged() -> None:
    state = DrawdownLiquidationState(liquidated_at=date(2026, 10, 1), rearmed=True)
    decision = _decide(_account(equity=100_000.0, peak=200_000.0), liquidation=DrawdownLiquidationConfig(), state=state)
    assert decision.drawdown_liquidation_state == state
    assert decision.target.weights == (_tw(0.5),)


def test_default_risk_mapping_is_unchanged_by_the_brake() -> None:
    # The LIVE configuration digest hashes asdict(PortfolioRiskLimits): the
    # brake must not add a field to it.
    assert set(asdict(PortfolioRiskLimits())) == {
        "daily_loss_limit",
        "drawdown_halt",
        "maximum_gross",
        "maximum_equity",
        "maximum_equity_asset",
        "minimum_cash",
        "maximum_positions",
    }
    assert portfolio_risk_limits_from_config(RiskLimits()) == PortfolioRiskLimits(
        daily_loss_limit=0.20,
        drawdown_halt=0.75,
        maximum_gross=0.98,
        maximum_equity=0.98,
        maximum_equity_asset=0.98,
        minimum_cash=0.02,
        maximum_positions=8,
    )


def test_config_block_defaults_are_off_and_validated() -> None:
    default = DrawdownLiquidationConfig()
    assert (default.enabled, default.threshold_pct, default.rearm_recovery_pct, default.cooldown_sessions) == (
        False,
        0.45,
        0.30,
        5,
    )
    assert RiskLimits().drawdown_liquidation == default
    for bad in (
        {"threshold_pct": 1.0},
        {"threshold_pct": 0.0},
        {"rearm_recovery_pct": 0.45},
        {"rearm_recovery_pct": -0.1},
        {"cooldown_sessions": -1},
        {"cooldown_sessions": True},
        {"enabled": "yes"},
    ):
        with pytest.raises(ValueError):
            DrawdownLiquidationConfig(**{**asdict(default), **bad})


def test_yaml_block_parses_and_the_shipped_default_is_off(tmp_path: Path) -> None:
    assert load_config(str(REPO_SETTINGS)).risk.drawdown_liquidation.enabled is False
    raw = yaml.safe_load(REPO_SETTINGS.read_text(encoding="utf-8"))
    raw["risk"]["drawdown_liquidation"] = {"enabled": True, "threshold_pct": 0.40, "cooldown_sessions": 3}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    parsed = load_config(str(path)).risk.drawdown_liquidation
    assert parsed == DrawdownLiquidationConfig(
        enabled=True, threshold_pct=0.40, rearm_recovery_pct=0.30, cooldown_sessions=3
    )


def test_liquidates_at_the_threshold_and_not_below_it() -> None:
    at = _decide(_account(equity=110_000.0, peak=200_000.0, managed=900.0), liquidation=ON, session=date(2026, 10, 5))
    assert at.target.weights == ()
    assert at.reasons == (DRAWDOWN_LIQUIDATION_REASON,)
    assert at.target.cash_weight == pytest.approx(1.0)

    below = _decide(_account(equity=112_000.0, peak=200_000.0, managed=900.0), liquidation=ON)
    assert below.target.weights == (_tw(0.5),)
    assert DRAWDOWN_LIQUIDATION_REASON not in below.reasons


def test_external_units_are_kept_and_only_managed_units_are_sold() -> None:
    account = _account(equity=110_000.0, peak=200_000.0, managed=300.0, external=600.0)
    decision = _decide(account, weight=0.8, liquidation=ON, session=date(2026, 10, 5))
    external_weight = 600.0 * PRICE / 110_000.0
    assert [weight.weight for weight in decision.target.weights] == [pytest.approx(external_weight)]
    orders = _orders(decision, account)
    assert len(orders) == 1
    assert orders[0].side == PortfolioOrderSide.SELL
    assert orders[0].quantity == pytest.approx(300.0)


def test_a_pure_external_position_is_never_sold() -> None:
    account = _account(equity=110_000.0, peak=200_000.0, external=600.0)
    decision = _decide(account, weight=0.5, liquidation=ON, session=date(2026, 10, 5))
    assert decision.target.weights == ()
    assert _orders(decision, account) == ()


def test_reason_is_recorded_and_the_reduction_is_risk_driven() -> None:
    account = _account(equity=110_000.0, peak=200_000.0, managed=900.0)
    decision = _decide(account, liquidation=ON, session=date(2026, 10, 5))
    assert DRAWDOWN_LIQUIDATION_REASON in decision.reasons
    assert DRAWDOWN_LIQUIDATION_REASON in decision.target.metadata["risk_overlay_reasons"]
    assert decision.halted is False
    assert gain_band_bypass_symbols(decision.target, Market.EQUITY) == frozenset({SYMBOL})


def test_reduction_is_not_held_by_the_gain_band_or_december_deferral() -> None:
    policy = PortfolioExecutionPolicy(downsize_gain_band=DownsizeGainBand(), december_deferral_sessions=10)
    mixed = _account(equity=110_000.0, peak=200_000.0, managed=300.0, external=600.0)
    liquidated = _decide(mixed, weight=0.8, liquidation=ON, session=date(2025, 12, 16), timestamp=DECEMBER)
    deferrals: list = []
    orders = _orders(liquidated, mixed, policy=policy, deferrals=deferrals)
    assert deferrals == []
    assert [(order.side, order.quantity) for order in orders] == [(PortfolioOrderSide.SELL, pytest.approx(300.0))]

    # Control: the same account and a strategy trim with the brake NOT tripped
    # is a discretionary downsize, so the December window postpones it.
    calm = _account(equity=110_000.0, peak=110_000.0, managed=300.0, external=600.0)
    trim = _decide(calm, weight=0.6, liquidation=ON, timestamp=DECEMBER)
    control_deferrals: list = []
    assert _orders(trim, calm, policy=policy, deferrals=control_deferrals) == ()
    assert len(control_deferrals) == 1


def test_shadow_mode_records_the_reason_without_capping() -> None:
    account = _account(equity=110_000.0, peak=200_000.0, managed=900.0)
    decision = _decide(account, weight=0.5, liquidation=ON, enforce=False, session=date(2026, 10, 5))
    assert DRAWDOWN_LIQUIDATION_REASON in decision.reasons
    assert decision.target.weights == (_tw(0.5),)


def test_stateless_rule_holds_red_only_while_drawdown_is_at_threshold() -> None:
    breached = _decide(_account(equity=110_000.0, peak=200_000.0, managed=900.0), liquidation=ON)
    assert breached.target.weights == ()
    assert breached.drawdown_liquidation_state is None
    # Documented limitation: with no state, recovery re-admits at once.
    recovered = _decide(_account(equity=150_000.0, peak=200_000.0), liquidation=ON)
    assert recovered.target.weights == (_tw(0.5),)


def test_injected_state_holds_red_until_rearm_and_cooldown_both_pass() -> None:
    state = DrawdownLiquidationState()
    steps = [
        # (session, equity, expected RED)
        (date(2026, 10, 5), 108_000.0, True),  # drawdown 0.46: breach, latched
        (date(2026, 10, 6), 130_000.0, True),  # 0.35: above the 0.30 re-arm level
        (date(2026, 10, 7), 150_000.0, True),  # 0.25: re-armed, 2 sessions in cash
        (date(2026, 10, 9), 150_000.0, True),  # 4 sessions in cash
    ]
    for session, equity, red in steps:
        decision = _decide(
            _account(equity=equity, peak=200_000.0, managed=0.0),
            liquidation=ON,
            state=state,
            session=session,
        )
        assert (DRAWDOWN_LIQUIDATION_REASON in decision.reasons) is red, session
        assert decision.target.weights == (), session
        state = decision.drawdown_liquidation_state
        assert state is not None

    assert state == DrawdownLiquidationState(liquidated_at=date(2026, 10, 5), rearmed=True)
    released = _decide(
        _account(equity=150_000.0, peak=200_000.0),
        liquidation=ON,
        state=state,
        session=date(2026, 10, 12),  # 5 NYSE sessions after the breach
    )
    assert DRAWDOWN_LIQUIDATION_REASON not in released.reasons
    assert released.target.weights == (_tw(0.5),)
    assert released.drawdown_liquidation_state == DrawdownLiquidationState()


def test_a_breach_after_rearm_starts_a_new_episode() -> None:
    latched = DrawdownLiquidationState(liquidated_at=date(2026, 10, 5), rearmed=True)
    decision = _decide(
        _account(equity=108_000.0, peak=200_000.0),
        liquidation=ON,
        state=latched,
        session=date(2026, 10, 8),
    )
    assert DRAWDOWN_LIQUIDATION_REASON in decision.reasons
    assert decision.drawdown_liquidation_state == DrawdownLiquidationState(
        liquidated_at=date(2026, 10, 8), rearmed=False
    )


def test_session_defaults_to_the_decision_session() -> None:
    decision = _decide(_account(equity=108_000.0, peak=200_000.0), liquidation=ON, state=DrawdownLiquidationState())
    assert decision.drawdown_liquidation_state == DrawdownLiquidationState(
        liquidated_at=datetime(2026, 10, 5, 20, tzinfo=UTC).astimezone(NEW_YORK).date(),
        rearmed=False,
    )


def test_daily_loss_latch_behaviour_is_unchanged_below_the_threshold() -> None:
    account = _account(daily_pnl=-30_000.0, managed=500.0)
    assert _decide(account, weight=0.3, liquidation=ON) == _decide(account, weight=0.3)
    decision = _decide(account, weight=0.3, liquidation=ON)
    assert "daily_loss_limit" in decision.reasons
    assert DRAWDOWN_LIQUIDATION_REASON not in decision.reasons
    assert decision.halted is True
    assert [weight.weight for weight in decision.target.weights] == [pytest.approx(0.3)]
