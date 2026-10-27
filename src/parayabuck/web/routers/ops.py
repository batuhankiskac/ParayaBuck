"""Ops router — read-only Mission 5 operational observability."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from parayabuck.web.deps import get_engine_manager, resolve_web_state
from parayabuck.web.services import dashboard
from parayabuck.web.services import ops as svc

router = APIRouter(prefix="/api/ops", tags=["ops"])


async def _request_config(request: Request) -> Any | None:
    """The request's own engine configuration (the app's WebState)."""
    return await asyncio.to_thread(svc.manager_config, get_engine_manager(request))


def _unavailable(body: dict[str, Any]) -> JSONResponse:
    """A failed read answers 503 with an explicit unavailable body, never an empty 200."""
    return JSONResponse(status_code=503, content={**body, "unavailable": True})


@router.get("/status")
async def get_ops_status(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_ops_status, await _request_config(request))


@router.get("/events", response_model=None)
async def get_ops_events(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    kind: str | None = Query(None, max_length=128),
    severity: str | None = Query(
        None,
        max_length=32,
        description="Exact severity: DEBUG, INFO, WARNING or CRITICAL. Any other value matches no events.",
    ),
    market: str | None = Query(None, max_length=32),
    decision_id: str | None = Query(None, max_length=128),
    incident_id: str | None = Query(None, max_length=128),
) -> JSONResponse | dict[str, Any]:
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
    try:
        events = await asyncio.to_thread(svc.query_events, limit, await _request_config(request), **filters)
    except svc.OpsReadUnavailable:
        return _unavailable({"events": [], "count": 0, "error": "events unavailable"})
    return {"events": events, "count": len(events)}


@router.get("/incidents", response_model=None)
async def get_ops_incidents(request: Request, limit: int = Query(100, ge=1, le=1000)) -> JSONResponse | dict[str, Any]:
    # Scoped to the dashboard's account: a pre-rotation account's incidents are not shown as current.
    account_id = await asyncio.to_thread(dashboard.account_id_for, resolve_web_state(request))
    try:
        inc = await asyncio.to_thread(svc.get_incidents, limit, await _request_config(request), account_id=account_id)
    except svc.OpsReadUnavailable:
        return _unavailable({"incidents": [], "error": "incidents unavailable"})
    return {"incidents": inc}


@router.get("/decisions/latest")
async def get_ops_latest(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_latest_decision, await _request_config(request))


@router.get("/timeline/{decision_id}")
async def get_ops_timeline(decision_id: str, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_timeline, decision_id, await _request_config(request))
