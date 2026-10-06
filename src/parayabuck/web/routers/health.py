"""Health vs readiness routers — distinct liveness / readiness layers.

Extracted verbatim from web/server.py lines 1586-1658 to preserve the
four-layer readiness contract documented in docs/operations.md:

* process_alive — /healthz liveness (always 200 {"status":"ok"})
* config_valid — parseable settings.yaml
* trading_expected — auto_start_engine in paper mode OR active runtime OR engine_error
* trading_ready — runtime.health().healthy

A failed auto-start (config parses, engine in error) is NOT ready (503).
An operator-intentional stop (auto_start false, engine stopped) IS ready
(200). Readiness never derives from ``runtime is None`` alone.

No circular imports: the EngineManager / WebState is resolved lazily
inside each handler via ``get_web_state()`` or the injected provider.
Tests can monkeypatch parayabuck.web.server.manager or inject a fake
state via create_app — both still work because this router also checks
the server compat shim as fallback.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.health")

router = APIRouter(tags=["health"])


def _resolve_manager() -> Any:
    """Resolve the active EngineManager.

    Preference:
    1. WebState singleton via web.state.get_web_state() (new factory path)
    2. parayabuck.web.server.manager compat shim (older direct monkeypatch tests)
    """
    # 1. New factory state
    try:
        from parayabuck.web.state import get_web_state

        state = get_web_state()
        if state is not None:
            mgr = getattr(state, "engine_manager", None)
            if mgr is not None:
                return mgr
            # state itself may be the manager in some test fakes
            if hasattr(state, "current_engine_state"):
                return state
    except Exception:
        pass
    # 2. Compat shim — server.py global manager
    try:
        from parayabuck.web import server as server_mod

        mgr = getattr(server_mod, "manager", None)
        if mgr is not None:
            return mgr
    except Exception:
        pass
    return None


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Process liveness — always 200 when the process can serve HTTP."""
    return {"status": "ok"}


def _intent_degraded_reasons(health: object) -> list[str]:
    """Active intent-watchdog alarm codes from a RuntimeHealth (or none)."""
    watchdog = getattr(health, "intent_watchdog", None) if health is not None else None
    reasons = watchdog.get("degraded_reasons") if isinstance(watchdog, dict) else None
    return [str(reason) for reason in reasons] if isinstance(reasons, list) else []


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """Deployment/trading readiness, distinct from /healthz process liveness."""
    # Lazy import so config loader can be monkeypatched in tests.
    try:
        from parayabuck.config import load_config as _load_config
    except Exception:  # pragma: no cover
        _load_config = None  # type: ignore[assignment]

    # Allow tests to monkeypatch parayabuck.web.server.load_config
    try:
        from parayabuck.web import server as _server_mod

        load_config = getattr(_server_mod, "load_config", _load_config)
    except Exception:
        load_config = _load_config

    manager = _resolve_manager()

    # If no manager resolved (e.g. in a minimal TestClient), fall back to a
    # synthetic stopped manager so the endpoint still answers structurally.
    if manager is None:

        class _StubManager:  # pragma: no cover
            config_path = "config/settings.yaml"
            runtime = None

            def current_engine_state(self) -> str:
                return "stopped"

            def has_active_runtime(self) -> bool:
                return False

        manager = _StubManager()

    try:
        cfg = (
            load_config(str(getattr(manager, "config_path", "config/settings.yaml")))
            if load_config is not None
            else None
        )
        config_valid = True
        config_error = None
    except Exception as exc:
        config_valid = False
        config_error = str(exc)
        cfg = None

    # engine_state is the single source of truth — tolerate stub managers.
    try:
        engine_state = manager.current_engine_state()
    except Exception:
        engine_state = getattr(manager, "engine_state", "stopped")

    runtime = getattr(manager, "runtime", None)
    health = None
    if runtime is not None and hasattr(runtime, "health"):
        try:
            health = runtime.health()
        except Exception as exc:  # pragma: no cover
            log.warning("Readiness health probe failed: %s", exc)

    trading_ready = bool(health and getattr(health, "healthy", False))

    app_config = getattr(cfg, "app", None) if cfg is not None else None
    runtime_config = getattr(cfg, "runtime", None) if cfg is not None else None
    configured_mode = getattr(runtime_config, "mode", None) if runtime_config is not None else None
    configured_to_trade = bool(
        app_config is not None
        and configured_mode is not None
        and getattr(app_config, "auto_start_engine", False)
        and getattr(configured_mode, "value", None) == "paper"
    )
    engine_failed = engine_state == "error"
    # Why the engine is not running, for an operator probing /readyz: a
    # failed start must name its cause and its retry count, not just the state. Both attributes are absent on stub
    # managers used by tests.
    engine_failure = getattr(manager, "last_engine_error", None)
    retry_attempt = getattr(manager, "_retry_attempt", 0)
    # has_active_runtime may be missing on stub fakes
    try:
        has_active = manager.has_active_runtime()
    except Exception:
        has_active = False

    trading_expected = configured_to_trade or has_active or engine_failed
    degraded_reasons = _intent_degraded_reasons(health)
    watchdog_config = getattr(cfg, "intent_watchdog", None) if cfg is not None else None
    fail_on_intent = bool(getattr(watchdog_config, "fail_readiness", True))
    ready = (
        config_valid
        and (not trading_expected or trading_ready)
        and not (trading_expected and fail_on_intent and degraded_reasons)
    )

    return JSONResponse(
        {
            "ready": ready,
            "process_alive": True,
            "config_valid": config_valid,
            "trading_expected": trading_expected,
            "trading_ready": trading_ready,
            "engine_state": engine_state,
            "runtime_expected": has_active,
            "degraded_reasons": degraded_reasons,
            "engine_error": engine_failure,
            "start_retry_attempt": retry_attempt if isinstance(retry_attempt, int) else 0,
            "error": config_error,
        },
        status_code=200 if ready else 503,
    )
