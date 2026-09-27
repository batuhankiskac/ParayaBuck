"""Pages router — Jinja HTML entrypoints.

Extracted from web/server.py lines 1560-1583. Serves:

* GET /      -> templates/index.html  (context: is_paper, control_token)
* GET /tools -> templates/tools.html (context: control_token)

Both responses carry ``Cache-Control: no-store`` so a stale control_token
is never cached. Token and is_paper are resolved lazily per-request from
WebState / server compat shim so TestClient with fake state sees the fake
token immediately. No circular import: resolution happens inside handlers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])

# Cache-busting query for /static assets; bump when the UI bundle changes.
ASSET_VERSION = "20260925-1"

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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


def _control_token() -> str:
    mgr = _resolve_manager()
    if mgr is not None:
        return str(getattr(mgr, "control_token", ""))
    return ""


def _is_paper() -> bool:
    mgr = _resolve_manager()
    if mgr is not None:
        try:
            return bool(mgr.is_paper_mode())
        except Exception:
            return True
    return True


@router.get("/", response_class=HTMLResponse)
async def serve_index(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "is_paper": _is_paper(),
            "control_token": _control_token(),
            "asset_version": ASSET_VERSION,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/tools", response_class=HTMLResponse)
async def serve_tools(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="tools.html",
        context={"control_token": _control_token(), "asset_version": ASSET_VERSION},
    )
    response.headers["Cache-Control"] = "no-store"
    return response
