"""Dashboard router — read-only operator views over same state as /api/status."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from parayabuck.web.services import dashboard as svc

router = APIRouter(tags=["dashboard"])


def _require_state(request: Request) -> Any:
    """Bound WebState, or the 503 every read-only dashboard route answers."""
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return state


@router.get("/api/dashboard/overview")
async def dashboard_overview(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_overview_payload, _require_state(request))


@router.get("/api/performance")
async def dashboard_performance(request: Request, period: str = "1M") -> dict[str, Any]:
    """Broker equity history plus a VOO benchmark for the performance chart."""
    from parayabuck.web.deps import get_engine_manager
    from parayabuck.web.services import performance

    manager = get_engine_manager(request)
    try:
        return await asyncio.to_thread(performance.portfolio_performance, manager, period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
