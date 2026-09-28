from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.routers import safety
from parayabuck.web.schemas.settings import ModePayload, SettingsPayload
from parayabuck.web.services.settings import SettingsService

WEB = Path(__file__).resolve().parents[1] / "src" / "parayabuck" / "web"
EMPTY_BLOCKS = "app:\nruntime:\nlive:\nstrategy:\nalpaca:\nrisk:\nengine:\n"


def _manager(tmp_path: Path, config_text: str) -> EngineManager:
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.config_path.write_text(config_text, encoding="utf-8")
    manager.env_path = tmp_path / ".env"
    manager.get_credentials = lambda: ("key", "secret")  # type: ignore[method-assign]
    manager.security_warning = None
    manager.engine_state = "stopped"
    return manager


# --- F1: empty YAML blocks ------------------------------------------------


def test_should_auto_start_treats_empty_runtime_and_alpaca_blocks_as_absent(tmp_path: Path) -> None:
    manager = _manager(tmp_path, EMPTY_BLOCKS)

    assert manager.should_auto_start() is True


def test_digest_treats_empty_runtime_block_like_absent_block() -> None:
    assert EngineManager._digest_config({"runtime": None}, paper=True) == EngineManager._digest_config({}, paper=True)


def test_digest_of_dict_blocks_is_unchanged() -> None:
    raw = {"runtime": {"mode": "live", "execution_timeframe": "5m"}, "live": {"enabled": True}}
    expected_raw = {"runtime": {"mode": "paper", "execution_timeframe": "5m"}, "live": {"enabled": True}}
    expected = hashlib.sha256(json.dumps(expected_raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    assert EngineManager._digest_config(raw, paper=True) == expected
    assert raw["runtime"]["mode"] == "live"


def test_get_settings_with_empty_blocks_returns_absent_block_digest(tmp_path: Path) -> None:
    empty = _manager(tmp_path, EMPTY_BLOCKS)
    without_runtime = yaml.safe_load(EMPTY_BLOCKS)
    del without_runtime["runtime"]

    data = asyncio.run(SettingsService(empty).get_settings())

    assert data["paper_mode"] is True
    assert data["config_digest"] == EngineManager._digest_config(without_runtime, paper=True)


def test_settings_save_with_empty_blocks_succeeds(tmp_path: Path) -> None:
    manager = _manager(tmp_path, EMPTY_BLOCKS + "strategy:\n  equity:\n  crypto:\n")

    result = asyncio.run(
        SettingsService(manager).save_settings(
            SettingsPayload(log_level="DEBUG", equity_trading_enabled=True, crypto_trading_enabled=False)
        )
    )

    assert result["status"] == "success"
    saved = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    assert saved["app"]["log_level"] == "DEBUG"
    assert saved["strategy"] == {"equity": {"enabled": True}, "crypto": {"enabled": False}}
    assert saved["live"] == {}


def test_toggle_mode_to_paper_with_empty_runtime_block(tmp_path: Path) -> None:
    manager = _manager(tmp_path, EMPTY_BLOCKS)
    manager.reset_broker = lambda: None  # type: ignore[method-assign]
    request: Any = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(web=SimpleNamespace(engine_manager=manager)))
    )

    result = asyncio.run(safety.toggle_mode(ModePayload(paper=True), request))

    assert result == {"status": "success", "paper": True}
    saved = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    assert saved["runtime"] == {"mode": "paper"}


# --- F2: production risk limits round-trip through the settings schema -----


def test_settings_schema_accepts_production_risk_limits_up_to_100() -> None:
    payload = SettingsPayload(daily_loss_limit_pct=20, max_drawdown_pct=75)
    assert (payload.daily_loss_limit_pct, payload.max_drawdown_pct) == (20, 75)
    SettingsPayload(daily_loss_limit_pct=100, max_drawdown_pct=100)
    with pytest.raises(ValidationError):
        SettingsPayload(daily_loss_limit_pct=100.1)
    with pytest.raises(ValidationError):
        SettingsPayload(max_drawdown_pct=100.1)


def test_saving_production_risk_limits_round_trips(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        yaml.safe_dump(
            {"runtime": {"mode": "paper"}, "risk": {"daily_loss_limit_pct": 0.20, "max_drawdown_pct": 0.75}}
        ),
    )
    service = SettingsService(manager)
    current = asyncio.run(service.get_settings())

    result = asyncio.run(
        service.save_settings(
            SettingsPayload(
                paper_mode=current["paper_mode"],
                daily_loss_limit_pct=current["daily_loss_limit_pct"],
                max_drawdown_pct=current["max_drawdown_pct"],
                log_level="DEBUG",
            )
        )
    )

    assert result["status"] == "success"
    saved = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    assert saved["risk"]["daily_loss_limit_pct"] == pytest.approx(0.20)
    assert saved["risk"]["max_drawdown_pct"] == pytest.approx(0.75)
    assert saved["app"]["log_level"] == "DEBUG"


# --- F3: 422 detail arrays render as text ----------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_detail_text_joins_fastapi_validation_messages() -> None:
    script = (
        "globalThis.window = globalThis;"
        "globalThis.document = { querySelector: () => null };"
        f"require({json.dumps(str(WEB / 'static' / 'js' / 'api.js'))});"
        "const t = window.PBApi.detailText;"
        "console.log(JSON.stringify(["
        "t({detail: [{loc: ['body', 'max_drawdown_pct'], msg: 'Input should be less than or equal to 100'},"
        "            {loc: ['body', 'port'], msg: 'bad'}]}, 'fb'),"
        "t({detail: 'plain'}, 'fb'),"
        "t({}, 'fb'),"
        "t({detail: []}, 'fb')]));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True)

    assert json.loads(out.stdout) == [
        "max_drawdown_pct: Input should be less than or equal to 100; port: bad",
        "plain",
        "fb",
        "fb",
    ]


def test_every_error_site_uses_detail_text() -> None:
    js = WEB / "static" / "js"
    for name in ("api.js", "ui.js", "websocket.js", "tools.js"):
        source = (js / name).read_text(encoding="utf-8")
        assert "new Error(payload.detail" not in source
        assert "new Error(data.detail" not in source
        assert "detailText(" in source


# --- F4: reset_broker / stop_engine broker ownership ----------------------


class _Broker:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_reset_broker_keeps_running_runtime_broker_and_closes_lazy_one(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "{}\n")
    runtime_broker, lazy = _Broker(), _Broker()
    manager.runtime = SimpleNamespace(broker=runtime_broker)
    manager.broker = lazy

    manager.reset_broker()

    assert manager.broker is runtime_broker
    assert lazy.closed == 1
    assert runtime_broker.closed == 0

    manager.reset_broker()
    assert manager.broker is runtime_broker
    assert runtime_broker.closed == 0


def test_reset_broker_without_runtime_closes_and_drops_broker(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "{}\n")
    lazy = _Broker()
    manager.broker = lazy

    manager.reset_broker()

    assert manager.broker is None
    assert lazy.closed == 1


def test_stop_engine_closes_lazy_broker_but_not_runtime_broker(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "{}\n")
    runtime_broker, lazy = _Broker(), _Broker()
    manager.runtime = SimpleNamespace(broker=runtime_broker, stop=lambda: True)
    manager.broker = lazy
    manager.engine_state = "running"

    manager.stop_engine()

    assert manager.engine_state == "stopped"
    assert manager.broker is None
    assert lazy.closed == 1
    assert runtime_broker.closed == 0


def test_stop_engine_does_not_close_runtime_owned_broker(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "{}\n")
    runtime_broker = _Broker()
    manager.runtime = SimpleNamespace(broker=runtime_broker, stop=lambda: True)
    manager.broker = runtime_broker
    manager.engine_state = "running"

    manager.stop_engine()

    assert manager.broker is None
    assert runtime_broker.closed == 0


# --- F5: crypto drawer default matches load_config --------------------------


def test_get_settings_crypto_default_is_disabled(tmp_path: Path) -> None:
    manager = _manager(tmp_path, yaml.safe_dump({"runtime": {"mode": "paper"}, "strategy": {"crypto": {}}}))

    data = asyncio.run(SettingsService(manager).get_settings())

    assert data["crypto_trading_enabled"] is False


# --- Live switch: paper_mode is the single PAPER/LIVE control --------------


def test_settings_drawer_no_longer_has_live_trading_checkbox() -> None:
    html = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
    ui = (WEB / "static" / "js" / "ui.js").read_text(encoding="utf-8")

    assert "liveTradingCheck" not in html
    assert "liveTradingCheck" not in ui
    assert "live_trading_enabled" not in ui
    assert 'id="paperModeCheck"' in html
    assert 'paper_mode: byId("paperModeCheck").checked' in ui


def test_paper_mode_save_from_live_config_still_switches_to_paper(tmp_path: Path) -> None:
    manager = _manager(tmp_path, yaml.safe_dump({"runtime": {"mode": "live"}, "live": {"enabled": True}}))

    result = asyncio.run(SettingsService(manager).save_settings(SettingsPayload(paper_mode=True)))

    assert result["status"] == "success"
    saved = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    assert saved["runtime"]["mode"] == "paper"
    assert saved["live"]["enabled"] is False


def test_paper_mode_false_still_requires_live_arming(tmp_path: Path) -> None:
    manager = _manager(tmp_path, yaml.safe_dump({"runtime": {"mode": "paper"}}))

    with pytest.raises(ValueError):
        asyncio.run(SettingsService(manager).save_settings(SettingsPayload(paper_mode=False, restart_engine=False)))
    assert yaml.safe_load(manager.config_path.read_text(encoding="utf-8")) == {"runtime": {"mode": "paper"}}
