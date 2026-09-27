"""Ops router — read-only Mission 5 operational observability."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from parayabuck.web.services import ops as svc

router = APIRouter(prefix="/api/ops", tags=["ops"])


@router.get("/status")
async def get_ops_status() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_ops_status)


@router.get("/events")
async def get_ops_events(
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
    events = await asyncio.to_thread(svc.query_events, limit, **filters)
    return {"events": events, "count": len(events)}


@router.get("/incidents")
async def get_ops_incidents(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    inc = await asyncio.to_thread(svc.get_incidents, limit)
    return {"incidents": inc}


@router.get("/decisions/latest")
async def get_ops_latest() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_latest_decision)


@router.get("/timeline/{decision_id}")
async def get_ops_timeline(decision_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_timeline, decision_id)
