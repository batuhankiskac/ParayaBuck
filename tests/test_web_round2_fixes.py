from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.data.cache as data_cache
from parayabuck.web import app as web_app
from parayabuck.web import server_legacy as leg
from parayabuck.web.managers.engine import EngineManager


class _FakeWarmupCache:
    calls = 0

    def get(self, symbol: str, timeframe: str, **_: Any) -> list[Any]:
        type(self).calls += 1
        return [SimpleNamespace(close=100.0 + (i % 3)) for i in range(40)]

    def close(self) -> None:
        pass


def _lock_checked_cache() -> dict[Any, Any]:
    class _Checked(dict):  # type: ignore[type-arg]
        def _held(self) -> None:
            assert leg._crypto_vol_cache_lock.locked(), "crypto vol cache touched without its lock"

        def __iter__(self):  # type: ignore[no-untyped-def]
            self._held()
            return super().__iter__()

        def get(self, *args: Any) -> Any:  # type: ignore[override]
            self._held()
            return super().get(*args)

        def pop(self, *args: Any) -> Any:  # type: ignore[override]
            self._held()
            return super().pop(*args)

        def __setitem__(self, key: Any, value: Any) -> None:
            self._held()
            super().__setitem__(key, value)

    return _Checked()


def test_crypto_realized_vols_hits_its_cache_within_one_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stale-bucket sweep must not evict the current bucket's own entry."""
    _FakeWarmupCache.calls = 0
    monkeypatch.setattr(data_cache, "WarmupCache", _FakeWarmupCache)
    monkeypatch.setattr(leg, "_crypto_vol_cache", {})
    monkeypatch.setattr(leg, "time", SimpleNamespace(monotonic=lambda: 600.0))

    first = leg._crypto_realized_vols(["BTC/USD"])
    second = leg._crypto_realized_vols(["BTC/USD"])

    assert first == second
    assert first["BTC/USD"] is not None
    assert _FakeWarmupCache.calls == 1


def test_crypto_realized_vols_only_touches_cache_under_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent payload builds share the dict; every access holds the lock."""
    monkeypatch.setattr(data_cache, "WarmupCache", _FakeWarmupCache)
    monkeypatch.setattr(leg, "_crypto_vol_cache", _lock_checked_cache())
    clock = iter([60.0, 120.0])
    monkeypatch.setattr(leg, "time", SimpleNamespace(monotonic=lambda: next(clock)))

    leg._crypto_realized_vols(["ETH/USD"])
    leg._crypto_realized_vols(["ETH/USD"])  # next bucket: sweeps the stale entry

    assert [key[1] for key in dict.keys(leg._crypto_vol_cache)] == [2]


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
