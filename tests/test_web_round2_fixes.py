from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from parayabuck.web import app as web_app
from parayabuck.web.managers.engine import EngineManager


def test_should_auto_start_treats_empty_app_section_as_defaults(tmp_path: Path) -> None:
    """``app:`` with no body loads as defaults in load_config; auto-start must agree."""
    config = tmp_path / "settings.yaml"
    config.write_text("app:\nruntime:\n  mode: paper\n", encoding="utf-8")
    manager = EngineManager()
    manager.config_path = config
    manager.get_credentials = lambda: ("key", "secret")  # type: ignore[method-assign]
    manager.security_warning = None

    assert manager.should_auto_start() is True


def test_web_main_tolerates_empty_app_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text("app:\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.delenv("HOST", raising=False)
    seen: dict[str, Any] = {}

    class _Stop(Exception):
        pass

    def _fake_logging(level: str) -> None:
        seen["level"] = level
        raise _Stop

    monkeypatch.setattr(web_app, "setup_logging", _fake_logging)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    with pytest.raises(_Stop):
        web_app.main()
    assert seen["level"] == "INFO"
