"""Status + account routers — extracted from web/server.py.

Both are read-only; status aggregates via services/status.py (no mutation,
no secrets). Account uses services/account.py with TTL 5s + lock.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from parayabuck.web.services import account as account_service
from parayabuck.web.services import status as status_service

router = APIRouter(prefix="/api", tags=["status"])


def _get_manager(request: Request) -> Any:
    """Resolve EngineManager via app.state.web, get_web_state, or server fallback.

    Priority:
      1. request.app.state.web.engine_manager (factory-injected state)
      2. parayabuck.web.state.get_web_state().engine_manager
      3. legacy parayabuck.web.server.manager singleton (tests patching it)
    """
    web_state = getattr(request.app.state, "web", None)
    if web_state is not None and hasattr(web_state, "engine_manager"):
        return web_state.engine_manager
    try:
        from parayabuck.web.state import get_web_state

        ws = get_web_state()
        if ws is not None and hasattr(ws, "engine_manager"):
            return ws.engine_manager
    except Exception:
        pass
    # Fallback for legacy tests / server.py import path
    try:
        import parayabuck.web.server as server_mod

        return server_mod.manager
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="Engine manager unavailable") from exc


def _http_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """GET /api/status — read-only aggregated status (no secrets, no mutation)."""
    manager = _get_manager(request)
    # build_status_snapshot is sync and touches filesystem + runtime.health();
    # run off the event loop like the original: asyncio.to_thread(manager...)
    try:
        return await asyncio.to_thread(status_service.build_status_snapshot, manager)
    except Exception as exc:  # pragma: no cover
        # Never leak internals; status is polled every 2s, a failure degrades gracefully
        raise _http_error(500, "Status unavailable") from exc


@router.get("/account")
async def get_account(request: Request) -> dict[str, Any]:
    """GET /api/account — broker account snapshot with 5s cache."""
    manager = _get_manager(request)
    # account_service owns TTL + lock policy over manager's broker
    return await asyncio.to_thread(account_service.fetch_account_snapshot, manager)
