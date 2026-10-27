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
from parayabuck.web.schemas.settings import ModePayload, SettingsPayload
from parayabuck.web.services.settings import SettingsService
from tests.helpers import web_client

WEB = Path(__file__).resolve().parents[1] / "src" / "parayabuck" / "web"
EMPTY_BLOCKS = "app:\nruntime:\nlive:\nstrategy:\nalpaca:\nrisk:\nengine:\n"
# Settings saves validate the full config with load_config, which requires
# at least one enabled market; fixtures carry the smallest loadable one.
_LOADABLE_MARKETS: dict[str, Any] = {
    "universe": {"equity": {"enabled": True}},
    "strategy": {"equity": {"enabled": True, "name": "equity_leveraged_trend_v1"}},
}


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
    raw = {"runtime": {"mode": "live"}, "live": {"enabled": True}}
    # _digest_config normalizes runtime.mode and excludes live.enabled; the
    # cap and approval path stay in the digest as live safety gates.
    expected_raw = {"runtime": {"mode": "paper"}, "live": {}}
    expected = hashlib.sha256(json.dumps(expected_raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    assert EngineManager._digest_config(raw, paper=True) == expected
    assert raw["runtime"]["mode"] == "live"


def test_digest_ignores_only_the_mode_flip_fields() -> None:
    """The arm→/api/mode toggle flips runtime.mode and live.enabled after the
    arm is verified; the digest excludes exactly those two. The dollar cap and
    the approval path stay in the digest so raising the cap or swapping the
    approval after arming invalidates the arm."""
    pre = {"runtime": {"mode": "paper"}, "live": {"enabled": False}}
    post = {
        "runtime": {"mode": "live"},
        "live": {"enabled": True},
    }
    assert EngineManager._digest_config(pre, paper=False) == EngineManager._digest_config(post, paper=False)
    tampered_cap = {
        "runtime": {"mode": "live"},
        "live": {"enabled": True, "hard_dollar_cap": 5000.0},
    }
    assert EngineManager._digest_config(pre, paper=False) != EngineManager._digest_config(tampered_cap, paper=False)
    tampered_approval = {
        "runtime": {"mode": "live"},
        "live": {"enabled": True, "operator_approval_path": "/tmp/approval.json"},
    }
    assert EngineManager._digest_config(pre, paper=False) != EngineManager._digest_config(
        tampered_approval, paper=False
    )


def test_get_settings_with_empty_blocks_returns_absent_block_digest(tmp_path: Path) -> None:
    empty = _manager(tmp_path, EMPTY_BLOCKS)
    without_runtime = yaml.safe_load(EMPTY_BLOCKS)
    del without_runtime["runtime"]

    data = asyncio.run(SettingsService(empty).get_settings())

    assert data["paper_mode"] is True
    assert data["config_digest"] == EngineManager._digest_config(without_runtime, paper=True)


def test_settings_save_with_empty_blocks_succeeds(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        EMPTY_BLOCKS
        + "universe:\n  equity:\n    enabled: true\n"
        + "strategy:\n  equity:\n    name: equity_leveraged_trend_v1\n",
    )

    result = asyncio.run(
        SettingsService(manager).save_settings(SettingsPayload(log_level="DEBUG", equity_trading_enabled=True))
    )

    assert result["status"] == "success"
    saved = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    assert saved["app"]["log_level"] == "DEBUG"
    assert saved["strategy"] == {"equity": {"name": "equity_leveraged_trend_v1", "enabled": True}}
    assert saved["live"] == {}


def test_toggle_mode_to_paper_with_empty_runtime_block(tmp_path: Path) -> None:
    # Empty runtime/live blocks plus the smallest loadable market: the mode
    # write is validated, so the file must load afterwards.
    manager = _manager(tmp_path, "app:\nruntime:\nlive:\nalpaca:\nrisk:\nengine:\n" + yaml.safe_dump(_LOADABLE_MARKETS))
    manager.reset_broker = lambda: None  # type: ignore[method-assign]

    result = web_client(manager).post("/api/mode", json={"paper": True}).json()

    assert result == {"status": "success", "paper": True}
    saved = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    assert saved["runtime"] == {"mode": "paper"}


def test_toggle_mode_never_writes_a_config_that_fails_to_load(tmp_path: Path) -> None:
    """Switching to live without a dollar cap used to write an invalid
    settings.yaml that broke the next restart (doctor ExecStartPre) and every
    backup run. The candidate is validated first; the file stays untouched."""
    manager = _manager(tmp_path, yaml.safe_dump({"runtime": {"mode": "paper"}, **_LOADABLE_MARKETS}))
    manager.reset_broker = lambda: None  # type: ignore[method-assign]
    manager.get_credentials = lambda: ("key", "secret")  # type: ignore[method-assign]
    manager.get_live_credentials = lambda: ("key", "secret")  # type: ignore[method-assign]
    manager._require_valid_live_arm = lambda *_a, **_k: None  # type: ignore[method-assign]
    before = manager.config_path.read_text(encoding="utf-8")

    response = web_client(manager).post("/api/mode", json={"paper": False})

    assert response.status_code == 400
    assert "hard dollar cap" in response.json()["detail"]
    assert manager.config_path.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob(".*.validate"))


def test_toggle_mode_waits_for_the_settings_lock(tmp_path: Path) -> None:
    """/api/mode and the settings drawer serialize on the same state lock."""
    import parayabuck.web.routers.safety as safety_router
    from parayabuck.web.state import WebState

    # Empty runtime/live blocks plus the smallest loadable market: the mode
    # write is validated, so the file must load afterwards.
    manager = _manager(tmp_path, "app:\nruntime:\nlive:\nalpaca:\nrisk:\nengine:\n" + yaml.safe_dump(_LOADABLE_MARKETS))
    manager.reset_broker = lambda: None  # type: ignore[method-assign]
    state = WebState(engine_manager=manager, backtest_manager=manager._backtest_manager)
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(web=state)))
    writes: list[str] = []
    real_write = safety_router._write_mode

    def _recording_write(path: Any, paper: bool) -> None:
        writes.append("mode")
        real_write(path, paper)

    async def scenario() -> None:
        safety_router._write_mode = _recording_write  # type: ignore[assignment]
        try:
            async with state.settings_lock:
                task = asyncio.create_task(safety_router.toggle_mode(ModePayload(paper=True), request))
                await asyncio.sleep(0.05)
                assert writes == []  # blocked behind the settings save
            assert (await task)["status"] == "success"
        finally:
            safety_router._write_mode = real_write  # type: ignore[assignment]

    asyncio.run(scenario())
    assert writes == ["mode"]


# --- F2: production risk limits round-trip through the settings schema -----


def test_settings_schema_accepts_production_risk_limits_below_100() -> None:
    payload = SettingsPayload(daily_loss_limit_pct=20, max_drawdown_pct=75)
    assert (payload.daily_loss_limit_pct, payload.max_drawdown_pct) == (20, 75)
    SettingsPayload(daily_loss_limit_pct=50, max_drawdown_pct=90)
    # A 100 % latch can never trip (Q3E-7): rejected like RiskLimits does.
    with pytest.raises(ValidationError):
        SettingsPayload(daily_loss_limit_pct=100)
    with pytest.raises(ValidationError):
        SettingsPayload(max_drawdown_pct=100)
    with pytest.raises(ValidationError):
        SettingsPayload(daily_loss_limit_pct=100.1)
    with pytest.raises(ValidationError):
        SettingsPayload(max_drawdown_pct=100.1)


def test_saving_production_risk_limits_round_trips(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        yaml.safe_dump(
            {
                "runtime": {"mode": "paper"},
                "risk": {"daily_loss_limit_pct": 0.20, "max_drawdown_pct": 0.75},
                **_LOADABLE_MARKETS,
            }
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


def _js_runtime() -> list[str] | None:
    """Command prefix of an installed JS runtime that evaluates a script string."""
    if shutil.which("node"):
        return ["node", "-e"]
    if shutil.which("deno"):
        return ["deno", "eval"]
    return None


@pytest.mark.skipif(_js_runtime() is None, reason="no JS runtime (node/deno) is installed")
def test_detail_text_joins_fastapi_validation_messages() -> None:
    source = (WEB / "static" / "js" / "api.js").read_text(encoding="utf-8")
    script = (
        "globalThis.window = globalThis;"
        "globalThis.document = { querySelector: () => null };"
        f"(0, eval)({json.dumps(source)});"
        "const t = window.PBApi.detailText;"
        "console.log(JSON.stringify(["
        "t({detail: [{loc: ['body', 'max_drawdown_pct'], msg: 'Input should be less than or equal to 100'},"
        "            {loc: ['body', 'port'], msg: 'bad'}]}, 'fb'),"
        "t({detail: 'plain'}, 'fb'),"
        "t({}, 'fb'),"
        "t({detail: []}, 'fb')]));"
    )
    runtime = _js_runtime()
    assert runtime is not None
    out = subprocess.run([*runtime, script], capture_output=True, text=True, timeout=30, check=True)

    assert json.loads(out.stdout) == [
        "max_drawdown_pct: Input should be less than or equal to 100; port: bad",
        "plain",
        "fb",
        "fb",
    ]


def test_detail_text_contract_is_pinned_without_a_js_runtime() -> None:
    """Python-level pin of the detailText join contract (runs everywhere).

    The behavioural test above needs a JS runtime; this one fails if the
    array join, the ``body`` loc filter, the ``loc: msg`` shape, the empty
    -array fallback or the object stringify is dropped from api.js.
    """
    source = (WEB / "static" / "js" / "api.js").read_text(encoding="utf-8")
    start = source.index("function detailText(payload, fallback)")
    body = source[start : source.index("async function fetchJson", start)]
    for fragment in (
        "Array.isArray(detail)",
        'p !== "body"',
        '.join(".")',
        'loc ? loc + ": " + msg : msg',
        'parts.length ? parts.join("; ") : fallback',
        "JSON.stringify(detail)",
        "detail ? String(detail) : fallback",
    ):
        assert fragment in body, fragment
    assert "detailText" in source[source.index("global.PBApi") :]


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


# --- Equity-only settings surface ------------------------------------------


def test_settings_expose_no_crypto_toggle(tmp_path: Path) -> None:
    manager = _manager(tmp_path, yaml.safe_dump({"runtime": {"mode": "paper"}}))

    data = asyncio.run(SettingsService(manager).get_settings())

    assert data["equity_trading_enabled"] is True
    assert not any("crypto" in key for key in data)
    with pytest.raises(ValidationError):
        SettingsPayload(crypto_trading_enabled=False)  # type: ignore[call-arg]


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
    manager = _manager(
        tmp_path, yaml.safe_dump({"runtime": {"mode": "live"}, "live": {"enabled": True}, **_LOADABLE_MARKETS})
    )

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
