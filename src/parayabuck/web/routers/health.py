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

The EngineManager is resolved per request through ``web.deps`` (the app's
own state first, then the process-wide state).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from parayabuck.config import load_config
from parayabuck.logger import get_logger
from parayabuck.web.deps import get_engine_manager

log = get_logger("parayabuck.web.health")

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Process liveness — always 200 when the process can serve HTTP."""
    return {"status": "ok"}


def _intent_degraded_reasons(health: object) -> list[str]:
    """Active intent-watchdog alarm codes from a RuntimeHealth (or none)."""
    from parayabuck.monitoring.intent_watchdog import degraded_reasons

    return degraded_reasons(health)


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Deployment/trading readiness, distinct from /healthz process liveness."""
    manager = get_engine_manager(request)

    try:
        cfg = load_config(str(getattr(manager, "config_path", "config/settings.yaml")))
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
