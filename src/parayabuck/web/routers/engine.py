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


@router.post("/start")
async def start_engine(request: Request) -> dict[str, str]:
    """POST /api/engine/start — ValueError → 400 (preserved verbatim)."""
    manager = get_engine_manager(request)
    try:
        await asyncio.to_thread(manager.start_engine)
        return {"status": "started", "message": "Bot engine started successfully."}
    except Exception as e:
        # Original server.py maps every failure to 400 with string detail.
        # Keep that contract; startup retries are auto_start-only.
        raise _http(400, str(e)) from e


@router.post("/stop")
async def stop_engine(request: Request) -> dict[str, str]:
    """POST /api/engine/stop — RuntimeError → 500, generic → 500."""
    manager = get_engine_manager(request)
    try:
        await asyncio.to_thread(manager.stop_engine)
    except RuntimeError as exc:
        log.error("Engine stop failed: %s", exc)
        raise _http(500, str(exc)) from exc
    except Exception:
        log.exception("Engine stop failed with an unexpected error")
        raise _http(500, "Engine could not be stopped") from None
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
        raise _http(500, "Safety lock reset failed") from None
    return {
        "status": "reset",
        "message": "Safety lock reset after broker reconciliation and data checks.",
    }
