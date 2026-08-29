"""Research explorer router — read-only, no promotion."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from parayabuck.web.services import research as svc

router = APIRouter(prefix="/api/research", tags=["research"])

_DEFAULT_ROOT = Path("research_runs")


def _root() -> Path:
    # Allow override via env for tests
    import os

    p = os.getenv("PARAYABUCK_RESEARCH_ROOT")
    return Path(p) if p else _DEFAULT_ROOT


@router.get("/runs")
async def list_runs(
    limit: int = Query(100, ge=1, le=500),
    strategy: str | None = None,
    market: str | None = None,
    status: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if strategy:
        filters["strategy"] = strategy
    if market:
        filters["market"] = market
    if status:
        filters["status"] = status
    if tag:
        filters["tag"] = tag
    result = await asyncio.to_thread(svc.list_runs, _root(), limit, **filters)
    if isinstance(result, dict) and "runs" in result:
        return result
    if isinstance(result, list):
        return {"runs": result, "count": len(result)}
    return {"runs": result, "count": 0}  # type: ignore


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    try:
        detail = await asyncio.to_thread(svc.get_run_detail, run_id, _root())
        return detail
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="research read failed") from exc


@router.get("/compare")
async def compare_runs(
    run_a: str = Query(..., description="first run id"),
    run_b: str = Query(..., description="second run id"),
) -> dict[str, Any]:
    try:
        comp = await asyncio.to_thread(svc.compare_runs, run_a, run_b, _root())
        return comp
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="compare failed") from exc


# Legacy aliases for older frontend / external callers (trials/summary -> runs)
@router.get("/trials")
async def list_trials_legacy(
    limit: int = Query(100, ge=1, le=500),
    strategy: str | None = None,
    market: str | None = None,
    status: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Legacy alias: /api/research/trials -> /api/research/runs (bounded, read-only)."""
    return await list_runs(limit=limit, strategy=strategy, market=market, status=status, tag=tag)


@router.get("/summary")
async def get_summary_legacy(
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Legacy alias: /api/research/summary -> /api/research/runs (returns same shape)."""
    result = await asyncio.to_thread(svc.list_runs, _root(), limit)
    # Normalize to {summary: [...]} for very old JS that expects summary
    if isinstance(result, dict) and "runs" in result:
        return {"summary": result["runs"], "runs": result["runs"], "count": result.get("count", 0)}
    return {"summary": result, "count": 0}  # type: ignore
