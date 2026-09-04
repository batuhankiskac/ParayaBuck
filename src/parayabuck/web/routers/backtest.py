"""Backtest router — POST /api/backtest/run, GET /api/backtest/status.

Delegates to ``BacktestManager`` resolved via ``WebState``; engine mutual
exclusion is enforced inside ``BacktestManager.start`` (forbidden while engine
active, checked under the backtest lock against ``current_engine_state``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from parayabuck.web.schemas.settings import BacktestPayload

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


def get_backtest_manager(request: Request) -> Any:
    """Resolve the app-local ``BacktestManager`` from ``WebState``."""
    state = getattr(request.app.state, "web", None)
    manager = getattr(state, "backtest_manager", None) if state is not None else None
    if manager is None:
        raise HTTPException(status_code=503, detail="Backtest manager unavailable")
    return manager


@router.post("/run", summary="Run backtest")
async def run_backtest(payload: BacktestPayload, manager: Any = Depends(get_backtest_manager)) -> dict[str, Any]:
    try:
        run_id = await asyncio.to_thread(manager.start, payload.model_dump())
        return {"status": "started", "run_id": run_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/status", summary="Backtest status")
async def backtest_status(manager: Any = Depends(get_backtest_manager)) -> dict[str, Any]:
    return await asyncio.to_thread(manager.status)
