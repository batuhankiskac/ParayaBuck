"""Engine lifecycle router — POST /api/engine/*.

Copied verbatim from server.py handler bodies; only import / manager
resolution is adjusted to use WebState injection. Domain errors are
translated to HTTP via a small helper (400/500), not a magic global
handler. Concurrent start/start is serialized by EngineManager._state_lock
(blocking_states + retry guards) — router does not add its own lock.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from parayabuck.logger import get_logger
from parayabuck.web.deps import get_engine_manager

log = get_logger("parayabuck.web.routers.engine")

router = APIRouter(prefix="/api/engine", tags=["engine"])


def _http(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _redacted(manager: object, text: str) -> str:
    """``text`` with the configured Alpaca keys removed (managers without the helper pass through)."""
    redact = getattr(manager, "redact_secrets", None)
    return redact(text) if callable(redact) else text


@router.post("/start")
async def start_engine(request: Request) -> dict[str, str]:
    """POST /api/engine/start — ValueError → 400 (operator refusals), anything else → generic 500."""
    manager = get_engine_manager(request)
    try:
        await asyncio.to_thread(manager.start_engine)
        return {"status": "started", "message": "Bot engine started successfully."}
    except ValueError as e:
        # Refusals (already running, halt, guards) are operator-readable text.
        # Startup retries are auto_start-only.
        raise _http(400, _redacted(manager, str(e))) from e
    except Exception:
        # Library errors can carry request details, so they are logged, not echoed.
        log.exception("Engine start failed with an unexpected error")
        raise _http(500, "Motor başlatılamadı; ayrıntılar uygulama günlüğünde.") from None


@router.post("/stop")
async def stop_engine(request: Request) -> dict[str, str]:
    """POST /api/engine/stop — RuntimeError → 500, generic → 500."""
    manager = get_engine_manager(request)
    try:
        # An explicit Stop is durable: it survives a restart (see should_auto_start).
        await asyncio.to_thread(manager.stop_engine, operator_request=True)
    except RuntimeError as exc:
        log.error("Engine stop failed: %s", exc)
        raise _http(500, _redacted(manager, str(exc))) from exc
    except Exception:
        log.exception("Engine stop failed with an unexpected error")
        raise _http(500, "Motor durdurulamadı; ayrıntılar uygulama günlüğünde.") from None
    return {"status": "stopped", "message": "Bot engine stopped."}


@router.post("/reset-safety-lock")
async def reset_safety_lock(request: Request) -> dict[str, str]:
    """POST /api/engine/reset-safety-lock — ValueError 400, else 500."""
    manager = get_engine_manager(request)
    try:
        await asyncio.to_thread(manager.reset_safety_lock)
    except ValueError as exc:
        raise _http(400, str(exc)) from exc
    except Exception:
        log.exception("Safety lock reset failed with an unexpected error")
        raise _http(500, "Güvenlik kilidi açılamadı; ayrıntılar uygulama günlüğünde.") from None
    return {
        "status": "reset",
        "message": "Safety lock reset after broker reconciliation and data checks.",
    }
