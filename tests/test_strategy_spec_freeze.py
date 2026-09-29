"""Strategy specification freeze guards — TWO contracts.

A. HISTORICAL SPEC FREEZE (v1)
   ``crypto_low_variance_v1`` is the frozen historical crypto baseline: it
   must remain constructible, unchanged, reproducible, with exact historical
   parameter specs. v2 evidence is defined relative to v1, so this freeze is
   permanent. The equity residual-momentum V1/V2/V3 and dividend-sleeve
   identities were removed from the codebase (2026-09-25) and are recoverable
   from git history; nothing freezes them here.

   B. CURRENT PRODUCTION SELECTION
   The PAPER config selects the approved identities with their exact
   parameters. A config change here is a deliberate promotion, not
   drift: the v1 freeze above still protects the crypto baseline.

These tests fail loudly on any drift in config, strategy-class defaults, or
the PAPER/LIVE posture — the "same name, different parameters is a NEW
strategy" rule. They do not depend on broker connectivity; they read the
repository config and construct strategy classes offline.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from parayabuck.config import Config, load_config
from parayabuck.portfolio import (
    portfolio_strategy_from,
    rebalance_policy_for,
    supported_portfolio_strategies,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "settings.yaml"

# --- A. HISTORICAL v1 SPECS (frozen baselines) -------------------------------
CRYPTO_FROZEN_PARAMETERS = {
    "variance_lookback_days": 22,
    "top_n": 2,
    "rebalance_hours": 168,
}

# --- B. CURRENT PAPER SELECTIONS ---------------------------------------------
PRODUCTION_EQUITY_PARAMETERS = {
    "signal_symbol": "QQQ",
    "hold_symbol": "QLD",
    "sma_bars": 200,
    "vol_target": 0.35,
    "vol_window": 20,
    "rebalance_frequency": "hours:24",
}

PRODUCTION_CRYPTO_PARAMETERS = {
    **CRYPTO_FROZEN_PARAMETERS,
    "rebalance_hours": 96,
}

CRYPTO_FROZEN_RISK = {
    "max_crypto_exposure_pct": 0.10,
    "max_crypto_position_pct": 0.05,
    "max_crypto_positions": 2,
}


@pytest.fixture(scope="module")
def config() -> Config:
    assert CONFIG_PATH.exists(), f"repository config missing: {CONFIG_PATH}"
    return load_config(str(CONFIG_PATH))


def _params_dict(raw: dict[str, object]) -> dict[str, object]:
    """Config YAML values round-trip through the loader; compare verbatim."""
    return dict(raw)


# ---------------------------------------------------------------------------
# B. CURRENT PRODUCTION SELECTION
# ---------------------------------------------------------------------------


def test_production_equity_selection_is_leveraged_trend_v1(config: Config) -> None:
    equity = config.strategy.equity
    assert equity.enabled is True
    assert equity.name == "equity_leveraged_trend_v1"
    # 2026-09-29 (paper_config_v2): crypto disabled, equity 0.98 == max_gross,
    # cash buffer 0.02. Alpha params are the frozen VT35 set; the BIL
    # idle-cash sweep failed its Stage B gate and stays off.
    assert equity.maximum_allocation == 0.98
    assert _params_dict(equity.parameters) == PRODUCTION_EQUITY_PARAMETERS
    strategy = portfolio_strategy_from(equity.name, equity.parameters)
    assert rebalance_policy_for(strategy).hours == 24


def test_production_crypto_selection_is_hysteresis_v1(config: Config) -> None:
    crypto = config.strategy.crypto
    # 2026-08-27 T1 PROMOTION: PAPER now hysteresis v1 (same alpha 22/2/96 + rank<=4)
    # v2 and v1 stay frozen historical baselines for rollback.
    assert crypto.maximum_allocation == 0.05
    # 2026-09-29 (paper_config_v2): disabled at the config level; the identity
    # stays registered until the full crypto code deletion.
    assert crypto.enabled is False
    assert crypto.name == "crypto_low_variance_hysteresis_v1"
    assert _params_dict(crypto.parameters) == PRODUCTION_CRYPTO_PARAMETERS
    strategy = portfolio_strategy_from(crypto.name, crypto.parameters)
    assert strategy.RETENTION_RANK == 4  # frozen retention
    assert rebalance_policy_for(strategy).hours == 96


def test_production_selections_are_the_only_product_strategies(config: Config) -> None:
    """The product surface is exactly the current selections plus frozen v1/v2 baselines."""
    registered = set(supported_portfolio_strategies())
    assert registered == {
        "equity_leveraged_trend_v1",
        "crypto_low_variance_v1",
        "crypto_low_variance_v2",
        "crypto_low_variance_hysteresis_v1",
    }
    assert {config.strategy.equity.name, config.strategy.crypto.name} <= registered


# Historical unsupported identities remain non-constructible. Construction
# must fail loudly — no silent fallback or automatic substitution.
UNSUPPORTED_STRATEGY_NAMES = (
    "equity_dynamic_trend_1d_v1",
    "equity_long_horizon_momentum_1d_v2",
    "crypto_cross_sectional_momentum_1h_v1",
    "crypto_time_series_momentum_v1",
    "crypto_cross_sectional_reversal_v1",
    "crypto_high_proximity_v1",
    "crypto_low_disagreement_v1",
)


@pytest.mark.parametrize("name", UNSUPPORTED_STRATEGY_NAMES)
def test_unsupported_strategies_are_not_constructible(name: str) -> None:
    with pytest.raises(ValueError, match=rf"Unsupported portfolio strategy: {re.escape(name)}"):
        portfolio_strategy_from(name)


def test_crypto_risk_limits_frozen(config: Config) -> None:
    risk = config.risk
    assert risk.max_crypto_exposure_pct == CRYPTO_FROZEN_RISK["max_crypto_exposure_pct"]
    assert risk.max_crypto_position_pct == CRYPTO_FROZEN_RISK["max_crypto_position_pct"]
    assert risk.max_crypto_positions == CRYPTO_FROZEN_RISK["max_crypto_positions"]


# ---------------------------------------------------------------------------
# A. HISTORICAL v1 FREEZE (permanent baselines)
# ---------------------------------------------------------------------------


def test_v1_historical_specs_stay_registered_and_constructible() -> None:
    registered = supported_portfolio_strategies()
    assert "crypto_low_variance_v1" in registered

    crypto = portfolio_strategy_from("crypto_low_variance_v1", CRYPTO_FROZEN_PARAMETERS)
    assert crypto.name == "crypto_low_variance_v1"
    assert crypto.version == "1.0.0"
    assert rebalance_policy_for(crypto).hours == 168


def test_v1_crypto_spec_unchanged_and_exact() -> None:
    crypto = portfolio_strategy_from("crypto_low_variance_v1", CRYPTO_FROZEN_PARAMETERS)
    assert dict(crypto.parameter_tuple()) == CRYPTO_FROZEN_PARAMETERS
    assert crypto.default_rebalance_hours == 168
    assert crypto.minimum_history_bars == 22 + 1


def test_v1_contracts_declared() -> None:
    crypto = portfolio_strategy_from("crypto_low_variance_v1")
    assert crypto.spec().rebalance_policy == "hours:168"
    assert crypto.spec().minimum_history_bars == 23


def test_v2_crypto_spec_is_the_same_alpha_at_new_cadence() -> None:
    """v2 must not touch any alpha parameter: every v1 alpha key is present
    with the same value; only the cadence key differs."""

    crypto_v2 = portfolio_strategy_from("crypto_low_variance_v2", PRODUCTION_CRYPTO_PARAMETERS)
    for key, value in CRYPTO_FROZEN_PARAMETERS.items():
        if key == "rebalance_hours":
            continue  # cadence key: the v2 production value is 96h
        assert dict(crypto_v2.parameter_tuple())[key] == value
    assert dict(crypto_v2.parameter_tuple())["rebalance_hours"] == 96
    assert crypto_v2.version == "2.0.0"


def test_runtime_is_paper_and_live_disabled(config: Config) -> None:
    assert config.runtime.mode.value == "paper"
    assert config.live.enabled is False
    assert config.live.hard_dollar_cap == 0.0
