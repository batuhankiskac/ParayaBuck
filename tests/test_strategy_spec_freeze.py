"""Strategy specification freeze guards.

The PAPER config selects the approved equity identity with its exact
parameters. A config change here is a deliberate promotion, not drift. The
equity residual-momentum V1/V2/V3 and dividend-sleeve identities (removed
2026-09-25) and the crypto sleeve identities (removed 2026-09-30) are
recoverable from git history; they must stay non-constructible here.

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
    PRODUCT_STRATEGY_NAMES,
    portfolio_strategy_from,
    rebalance_policy_for,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "settings.yaml"

PRODUCTION_EQUITY_PARAMETERS = {
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
}


@pytest.fixture(scope="module")
def config() -> Config:
    assert CONFIG_PATH.exists(), f"repository config missing: {CONFIG_PATH}"
    return load_config(str(CONFIG_PATH))


def _params_dict(raw: dict[str, object]) -> dict[str, object]:
    """Config YAML values round-trip through the loader; compare verbatim."""
    return dict(raw)


def test_production_equity_selection_is_leveraged_trend_v1(config: Config) -> None:
    equity = config.strategy.equity
    assert equity.enabled is True
    assert equity.name == "equity_leveraged_trend_v1"
    # 2026-09-29 (paper_config_v2): equity 0.98 == max_gross, cash buffer
    # 0.02. Alpha params are the frozen VT35 set; the BIL idle-cash sweep
    # failed its Stage B gate and was removed.
    assert equity.maximum_allocation == 0.98
    assert _params_dict(equity.parameters) == PRODUCTION_EQUITY_PARAMETERS
    strategy = portfolio_strategy_from(equity.name, equity.parameters)
    assert rebalance_policy_for(strategy).hours == 24


def test_production_selection_is_the_only_product_strategy(config: Config) -> None:
    registered = set(PRODUCT_STRATEGY_NAMES)
    assert registered == {"equity_leveraged_trend_v1"}
    assert config.strategy.equity.name in registered


# Historical unsupported identities remain non-constructible. Construction
# must fail loudly — no silent fallback or automatic substitution.
UNSUPPORTED_STRATEGY_NAMES = (
    "equity_dynamic_trend_1d_v1",
    "equity_long_horizon_momentum_1d_v2",
    "crypto_low_variance_v1",
    "crypto_low_variance_v2",
    "crypto_low_variance_hysteresis_v1",
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


def test_runtime_is_paper_and_live_disabled(config: Config) -> None:
    assert config.runtime.mode.value == "paper"
    assert config.live.enabled is False
    assert config.live.hard_dollar_cap == 0.0
