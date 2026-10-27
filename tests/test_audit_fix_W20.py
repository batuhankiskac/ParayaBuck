"""Audit V6: operator-safety ceilings and floors on the configuration.

Every bound here must keep accepting the shipped ``config/settings.yaml``;
each rejection case mutates a copy of that file and loads it through
``load_config``, which is the path an operator's edit takes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from parayabuck import load_config
from parayabuck.config import AlertingConfig, EngineConfig, ExecutionConfig, RiskLimits

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"


def _shipped_raw() -> dict[str, Any]:
    return yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))


def _load_mutated(tmp_path: Path, mutate: Any) -> Any:
    raw = _shipped_raw()
    mutate(raw)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(str(path))


def test_shipped_config_passes_every_new_bound() -> None:
    config = load_config(str(SHIPPED))

    assert config.execution.minimum_order_notional == 10
    assert config.risk.daily_loss_limit_pct == 0.20
    assert config.risk.max_drawdown_pct == 0.75
    assert config.strategy.equity.parameters["sma_bars"] == 200
    assert config.strategy.equity.parameters["vol_target"] == 0.35
    assert config.engine.account_poll_interval_seconds == 15
    assert config.engine.maximum_account_age_for_entry_seconds == 30
    assert config.engine.decision_retry_window_minutes == 40
    assert config.execution.december_downsize_deferral_sessions == 10
    assert config.risk.max_positions == 8
    assert config.alerting.email.enabled is False


def test_ceilings_accept_their_exact_limit() -> None:
    ExecutionConfig(minimum_order_notional=1000.0, rebalance_band_pct=0.2, downsize_gain_band=None)
    ExecutionConfig(december_downsize_deferral_sessions=20)
    RiskLimits(daily_loss_limit_pct=0.5, max_drawdown_pct=0.9)
    EngineConfig(
        account_poll_interval_seconds=60.0,
        maximum_account_age_for_entry_seconds=60.0,
        decision_retry_window_minutes=60.0,
    )


def _set(section: str, key: str, value: object) -> Any:
    def mutate(raw: dict[str, Any]) -> None:
        raw.setdefault(section, {})[key] = value

    return mutate


def _set_param(key: str, value: object) -> Any:
    def mutate(raw: dict[str, Any]) -> None:
        raw["strategy"]["equity"]["parameters"][key] = value

    return mutate


def _set_gain_band(**fields: object) -> Any:
    def mutate(raw: dict[str, Any]) -> None:
        raw["execution"]["downsize_gain_band"].update(fields)

    return mutate


def _set_alerting_email(value: object) -> Any:
    def mutate(raw: dict[str, Any]) -> None:
        raw["alerting"]["email"] = value

    return mutate


# V6-1 minimum_order_notional ceiling, V6-2 daily-loss and drawdown ceilings,
# V6-3 sma_bars floor, V6-4 poll interval vs the 120 s watchdog gate, V6-5
# vol_target ceiling, V6-6 account age vs poll, V6-7 gain-band ceiling and
# tier floor, V6-8 max_positions type, V6-9 alerting.email type, V6-10
# December deferral cap, V6-11 retry window ceiling, V6-12 per-asset cap vs
# equity allocation.
@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        pytest.param(_set("execution", "minimum_order_notional", 1500), "minimum_order_notional", id="V6-1"),
        pytest.param(_set("risk", "daily_loss_limit_pct", 0.9), "daily_loss_limit_pct", id="V6-2-daily"),
        pytest.param(_set("risk", "max_drawdown_pct", 0.95), "max_drawdown_pct", id="V6-2-drawdown"),
        pytest.param(_set_param("sma_bars", 0), "sma_bars", id="V6-3"),
        pytest.param(_set("engine", "account_poll_interval_seconds", 300), "account_poll_interval_seconds", id="V6-4"),
        pytest.param(_set_param("vol_target", 0.9), "vol_target", id="V6-5"),
        pytest.param(
            _set("engine", "maximum_account_age_for_entry_seconds", 10),
            "maximum_account_age_for_entry_seconds",
            id="V6-6",
        ),
        pytest.param(_set_gain_band(bands=[0.005, 0.03, 0.5]), "bands must be in", id="V6-7-ceiling"),
        pytest.param(_set_gain_band(bands=[0.001, 0.03, 0.08]), "at least rebalance_band_pct", id="V6-7-floor"),
        pytest.param(_set("execution", "rebalance_band_pct", 0.5), "rebalance_band_pct must be in", id="V6-7-base"),
        pytest.param(_set("risk", "max_positions", 8.0), "max_positions must be an integer", id="V6-8"),
        pytest.param(_set_alerting_email(True), "alerting.email must be a mapping", id="V6-9"),
        pytest.param(
            _set("execution", "december_downsize_deferral_sessions", 21),
            "december_downsize_deferral_sessions",
            id="V6-10",
        ),
        pytest.param(
            _set("engine", "decision_retry_window_minutes", 90),
            "decision_retry_window_minutes must be at most",
            id="V6-11",
        ),
        pytest.param(_set("risk", "max_position_pct", 0.9), "max_position_pct", id="V6-12"),
    ],
)
def test_each_unsafe_value_is_rejected_at_load(tmp_path: Path, mutate: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _load_mutated(tmp_path, mutate)


def test_sma_zero_is_rejected_under_the_plain_sma_rule(tmp_path: Path) -> None:
    # Under ``trend_rule: sma`` the strategy class accepts sma_bars 0, so only
    # the config-level floor stops a silent buy-and-hold in production.
    def mutate(raw: dict[str, Any]) -> None:
        parameters = raw["strategy"]["equity"]["parameters"]
        for key in ("tsm_bars", "donchian_bars", "early_reentry_weight"):
            parameters.pop(key, None)
        parameters["trend_rule"] = "sma"
        parameters["sma_bars"] = 0

    with pytest.raises(ValueError, match="sma_bars must be at least 2"):
        _load_mutated(tmp_path, mutate)


def test_engine_poll_interval_rejects_above_the_watchdog_margin() -> None:
    with pytest.raises(ValueError, match="account_poll_interval_seconds must be at most 60"):
        EngineConfig(account_poll_interval_seconds=61.0, maximum_account_age_for_entry_seconds=61.0)


def test_alerting_email_mapping_still_loads(tmp_path: Path) -> None:
    config = _load_mutated(tmp_path, _set_alerting_email({"enabled": False}))

    assert isinstance(config.alerting, AlertingConfig)
    assert config.alerting.email.enabled is False
