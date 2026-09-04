"""Control token router — local-only token fetch.

Extracted from web/server.py lines 1661-1665::

    @app.get("/api/control-token")
    async def get_control_token() -> JSONResponse:
        response = JSONResponse({"control_token": manager.control_token})
        response.headers["Cache-Control"] = "no-store"
        return response

Preserved verbatim: the JSON shape (``{"control_token": ...}``), the
``no-store`` cache directive (the token must never be cached), and the
fact that it is a GET endpoint protected by the global
``protect_local_control_api`` middleware (Host check only for GET, so the
token is fetchable by the local frontend but not by a remote host).

No circular import: manager is resolved lazily per request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["control"])


def _resolve_manager(request: Request) -> Any:
    state = getattr(request.app.state, "web", None)
    manager = getattr(state, "engine_manager", None) if state is not None else None
    if manager is None:
        raise HTTPException(status_code=503, detail="Engine manager unavailable")
    return manager


@router.get("/api/control-token")
async def get_control_token(request: Request) -> JSONResponse:
    manager = _resolve_manager(request)
    token = getattr(manager, "control_token", None)
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=503, detail="Control token unavailable")
    response = JSONResponse({"control_token": token})
    response.headers["Cache-Control"] = "no-store"
    return response
