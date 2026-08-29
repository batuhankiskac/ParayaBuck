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

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["control"])


def _resolve_manager() -> Any:
    try:
        from parayabuck.web.state import get_web_state

        state = get_web_state()
        if state is not None:
            mgr = getattr(state, "engine_manager", None)
            if mgr is not None:
                return mgr
            if hasattr(state, "current_engine_state"):
                return state
    except Exception:
        pass
    try:
        from parayabuck.web import server as server_mod

        mgr = getattr(server_mod, "manager", None)
        if mgr is not None:
            return mgr
    except Exception:
        pass
    return None


@router.get("/api/control-token")
async def get_control_token() -> JSONResponse:
    manager = _resolve_manager()
    token = getattr(manager, "control_token", "") if manager is not None else ""
    response = JSONResponse({"control_token": token})
    response.headers["Cache-Control"] = "no-store"
    return response
