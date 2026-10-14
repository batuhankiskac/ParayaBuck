"""Pages router — Jinja HTML entrypoints.

Extracted from web/server.py lines 1560-1583. Serves:

* GET /      -> templates/index.html  (context: is_paper, control_token)
* GET /tools -> templates/tools.html (context: control_token)

Both responses carry ``Cache-Control: no-store`` so a stale control_token
is never cached. Token and is_paper are resolved per request from the
app's own WebState (web.deps), so each app embeds its own token.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from parayabuck.web.deps import get_engine_manager
from parayabuck.web.security import is_trusted_client

router = APIRouter(tags=["pages"])

# Cache-busting query for /static assets; bump when the UI bundle changes.
ASSET_VERSION = "20261014-1"

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _control_token(request: Request) -> str:
    """The control token, embedded only for peers the token endpoint trusts.

    The Host header is client-supplied; a remote peer must not read the
    token out of the HTML when ``/api/control-token`` refuses it.
    """
    if not is_trusted_client(request.client.host if request.client is not None else None):
        return ""
    return str(getattr(get_engine_manager(request), "control_token", ""))


def _is_paper(request: Request) -> bool:
    try:
        return bool(get_engine_manager(request).is_paper_mode())
    except Exception:
        return True


@router.get("/", response_class=HTMLResponse)
async def serve_index(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "is_paper": _is_paper(request),
            "control_token": _control_token(request),
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
        context={"control_token": _control_token(request), "asset_version": ASSET_VERSION},
    )
    response.headers["Cache-Control"] = "no-store"
    return response
