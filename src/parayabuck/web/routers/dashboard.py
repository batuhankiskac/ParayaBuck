"""Dashboard router — read-only operator views over same state as /api/status."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from parayabuck.web.services import dashboard as svc

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard/overview")
async def dashboard_overview() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_overview_payload)


@router.get("/api/equity")
async def dashboard_equity() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_panel, "equity")


@router.get("/api/crypto")
async def dashboard_crypto() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_panel, "crypto")


@router.get("/api/risk")
async def dashboard_risk() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_panel, "risk")


@router.get("/api/execution")
async def dashboard_execution() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_panel, "execution")


@router.get("/api/universe")
async def dashboard_universe() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_panel, "universe")


@router.get("/api/health")
async def dashboard_health() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_panel, "health")


@router.get("/api/explanations")
async def dashboard_explanations() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_panel, "explanations")


@router.get("/api/metrics")
async def dashboard_metrics() -> dict[str, Any]:
    return await asyncio.to_thread(svc.get_panel, "metrics")
