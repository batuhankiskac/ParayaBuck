"""Ops router — read-only Mission 5 operational observability."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request

from parayabuck.web.deps import get_engine_manager
from parayabuck.web.services import ops as svc

router = APIRouter(prefix="/api/ops", tags=["ops"])


async def _request_config(request: Request) -> Any | None:
    """The request's own engine configuration (the app's WebState)."""
    return await asyncio.to_thread(svc.manager_config, get_engine_manager(request))


@router.get("/status")
async def get_ops_status(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_ops_status, await _request_config(request))


@router.get("/events")
async def get_ops_events(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    kind: str | None = None,
    severity: str | None = Query(
        None,
        description="Exact severity: DEBUG, INFO, WARNING or CRITICAL. Any other value matches no events.",
    ),
    market: str | None = None,
    decision_id: str | None = None,
    incident_id: str | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if kind:
        filters["kind"] = kind
    if severity:
        filters["severity"] = severity
    if market:
        filters["market"] = market
    if decision_id:
        filters["decision_id"] = decision_id
    if incident_id:
        filters["incident_id"] = incident_id
    events = await asyncio.to_thread(svc.query_events, limit, await _request_config(request), **filters)
    return {"events": events, "count": len(events)}


@router.get("/incidents")
async def get_ops_incidents(request: Request, limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    inc = await asyncio.to_thread(svc.get_incidents, limit, await _request_config(request))
    return {"incidents": inc}


@router.get("/decisions/latest")
async def get_ops_latest(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_latest_decision, await _request_config(request))


@router.get("/timeline/{decision_id}")
async def get_ops_timeline(decision_id: str, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_timeline, decision_id, await _request_config(request))
