"""Status + account routers — extracted from web/server.py.

Both are read-only; status aggregates via services/status.py (no mutation,
no secrets). Account uses EngineManager.fetch_account_snapshot (TTL cache + lock).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from parayabuck.web.deps import get_engine_manager
from parayabuck.web.services import status as status_service

router = APIRouter(prefix="/api", tags=["status"])


def _http_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """GET /api/status — read-only aggregated status (no secrets, no mutation)."""
    manager = get_engine_manager(request)
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
    manager = get_engine_manager(request)
    return await asyncio.to_thread(manager.fetch_account_snapshot)
