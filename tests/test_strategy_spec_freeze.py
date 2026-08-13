"""Frozen validated strategy specification guards.

The productization mission freezes the economically validated parameter sets
of ``equity_residual_momentum_v1`` and ``crypto_low_variance_v1``. These
tests fail loudly on any drift in config, strategy-class defaults, or the
PAPER/LIVE posture — the "same name, different parameters is a NEW strategy"
rule.

They do not depend on broker connectivity; they read the repository config
and construct strategy classes offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from parayabuck.config import Config, load_config
from parayabuck.portfolio import portfolio_strategy_from, supported_portfolio_strategies

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "settings.yaml"

EQUITY_FROZEN_PARAMETERS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
}

CRYPTO_FROZEN_PARAMETERS = {
    "variance_lookback_days": 22,
    "top_n": 2,
    "rebalance_hours": 168,
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


def test_equity_validated_spec_in_config(config: Config) -> None:
    equity = config.strategy.equity
    assert equity.enabled is True
    assert equity.name == "equity_residual_momentum_v1"
    assert equity.maximum_allocation == 0.50
    assert _params_dict(equity.parameters) == EQUITY_FROZEN_PARAMETERS


def test_crypto_validated_spec_in_config(config: Config) -> None:
    crypto = config.strategy.crypto
    assert crypto.enabled is True
    assert crypto.name == "crypto_low_variance_v1"
    assert crypto.maximum_allocation == 0.10
    assert _params_dict(crypto.parameters) == CRYPTO_FROZEN_PARAMETERS


def test_crypto_risk_limits_frozen(config: Config) -> None:
    risk = config.risk
    assert risk.max_crypto_exposure_pct == CRYPTO_FROZEN_RISK["max_crypto_exposure_pct"]
    assert risk.max_crypto_position_pct == CRYPTO_FROZEN_RISK["max_crypto_position_pct"]
    assert risk.max_crypto_positions == CRYPTO_FROZEN_RISK["max_crypto_positions"]


def test_strategy_class_defaults_match_frozen_spec() -> None:
    """Constructing the strategy with NO parameters must reproduce the
    validated economics — class defaults ARE the frozen spec."""
    equity = portfolio_strategy_from("equity_residual_momentum_v1")
    assert equity.name == "equity_residual_momentum_v1"
    assert equity.market.value == "equity"
    assert equity.timeframe == "1d"
    assert equity.rebalance_policy_declared == "monthly"
    # formation 252 + skip 21 + 1 history anchor => the class default derives
    # the exact validated history requirement.
    assert equity.minimum_history_bars == 273 + 1

    crypto = portfolio_strategy_from("crypto_low_variance_v1")
    assert crypto.name == "crypto_low_variance_v1"
    assert crypto.market.value == "crypto"
    assert crypto.timeframe == "1d"
    assert crypto.default_rebalance_hours == 168
    assert crypto.minimum_history_bars == 22 + 1


def test_frozen_parameter_sets_are_constructible() -> None:
    """Passing the frozen parameter dicts must round-trip verbatim."""
    equity = portfolio_strategy_from("equity_residual_momentum_v1", EQUITY_FROZEN_PARAMETERS)
    assert dict(equity.parameter_tuple()) == {
        key: (list(value) if isinstance(value, tuple) else value)
        for key, value in EQUITY_FROZEN_PARAMETERS.items()
    }

    crypto = portfolio_strategy_from("crypto_low_variance_v1", CRYPTO_FROZEN_PARAMETERS)
    assert dict(crypto.parameter_tuple()) == CRYPTO_FROZEN_PARAMETERS


def test_strategy_names_registered_and_unique() -> None:
    registered = supported_portfolio_strategies()
    assert "equity_residual_momentum_v1" in registered
    assert "crypto_low_variance_v1" in registered
    assert len(registered) == len(set(registered))


def test_crypto_strategy_declares_validated_contract() -> None:
    crypto = portfolio_strategy_from("crypto_low_variance_v1")
    spec = crypto.spec()
    assert spec.market.value == "crypto"
    assert spec.primary_timeframe == "1d"
    assert spec.rebalance_policy == "hours:168"
    assert spec.minimum_history_bars == 23  # 22-day lookback + 1


def test_equity_strategy_declares_validated_contract() -> None:
    equity = portfolio_strategy_from("equity_residual_momentum_v1")
    spec = equity.spec()
    assert spec.market.value == "equity"
    assert spec.primary_timeframe == "1d"
    assert spec.rebalance_policy == "monthly"


def test_runtime_is_paper_and_live_disabled(config: Config) -> None:
    assert config.runtime.mode.value == "paper"
    assert config.live.enabled is False
    assert config.live.hard_dollar_cap == 0.0
