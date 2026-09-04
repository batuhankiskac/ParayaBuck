"""Dashboard router — read-only operator views over same state as /api/status."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from parayabuck.web.services import dashboard as svc

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard/overview")
async def dashboard_overview(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return await asyncio.to_thread(svc.get_overview_payload, state)


@router.get("/api/equity")
async def dashboard_equity(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return await asyncio.to_thread(svc.get_panel, "equity", state)


@router.get("/api/crypto")
async def dashboard_crypto(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return await asyncio.to_thread(svc.get_panel, "crypto", state)


@router.get("/api/risk")
async def dashboard_risk(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return await asyncio.to_thread(svc.get_panel, "risk", state)


@router.get("/api/execution")
async def dashboard_execution(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return await asyncio.to_thread(svc.get_panel, "execution", state)


@router.get("/api/universe")
async def dashboard_universe(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return await asyncio.to_thread(svc.get_panel, "universe", state)


@router.get("/api/health")
async def dashboard_health(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return await asyncio.to_thread(svc.get_panel, "health", state)


@router.get("/api/explanations")
async def dashboard_explanations(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return await asyncio.to_thread(svc.get_panel, "explanations", state)


@router.get("/api/metrics")
async def dashboard_metrics(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "web", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return await asyncio.to_thread(svc.get_panel, "metrics", state)
