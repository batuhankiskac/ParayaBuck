"""Backtest router — POST /api/backtest/run, GET /api/backtest/status.

Delegates to ``BacktestManager`` resolved via ``WebState``; engine mutual
exclusion is enforced inside ``BacktestManager.start`` (forbidden while engine
active, checked under the backtest lock against ``current_engine_state``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from parayabuck.web.schemas.settings import BacktestPayload

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


def get_backtest_manager() -> Any:
    """Resolve ``BacktestManager`` from ``WebState`` or legacy singleton."""
    try:
        from parayabuck.web.state import get_web_state

        state = get_web_state()
        if state is not None and getattr(state, "backtest_manager", None) is not None:
            return state.backtest_manager
    except Exception:
        pass
    try:
        from parayabuck.web.server import backtest_manager as legacy_manager

        return legacy_manager
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("BacktestManager could not be resolved") from exc


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
