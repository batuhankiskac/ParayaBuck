"""Safety / liveness router — live arming, mode toggle, risk-epoch, ws-ticket.

Verbatim extraction of server.py sections:
  /api/safety/risk-epoch (1945-1957), /api/mode (1959-1993),
  /api/live/* (1994-2023), /api/ws-ticket (2024-2030).

All handlers resolve EngineManager via request.app.state.web or
get_web_state (state injection for tests), then translate domain errors
via a small helper to 400/500 — no magic global handler. Paths are
preserved so OpenAPI contract is unchanged. No secrets are returned.
"""

from __future__ import annotations

import asyncio
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request

from parayabuck.logger import get_logger
from parayabuck.utils.io import atomic_write
from parayabuck.web.schemas.settings import LiveConfirmationPayload, ModePayload, WebSocketTicketPayload

log = get_logger("parayabuck.web.routers.safety")

router = APIRouter(prefix="/api", tags=["safety"])


def _get_manager(request: Request) -> Any:
    web_state = getattr(request.app.state, "web", None)
    if web_state is not None and hasattr(web_state, "engine_manager"):
        return web_state.engine_manager
    try:
        from parayabuck.web.state import get_web_state

        ws = get_web_state()
        if ws is not None and hasattr(ws, "engine_manager"):
            return ws.engine_manager
    except Exception:
        pass
    try:
        import parayabuck.web.server as server_mod  # type: ignore

        return server_mod.manager
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="Engine manager unavailable") from exc


def _http(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


# ---------------------------------------------------------------------------
# POST /api/safety/risk-epoch
# ---------------------------------------------------------------------------
@router.post("/safety/risk-epoch")
async def start_new_risk_epoch(request: Request) -> dict[str, Any]:
    """Operator-verified drawdown-baseline reset (rebases peak to current equity).

    Starts a new risk epoch — explicit recovery for portfolio_drawdown_halt.
    """
    manager = _get_manager(request)
    try:
        return await asyncio.to_thread(manager.start_new_risk_epoch)
    except ValueError as exc:
        raise _http(400, str(exc)) from exc
    except Exception:
        log.exception("Risk epoch reset failed with an unexpected error")
        raise _http(500, "Risk epoch reset failed") from None


# ---------------------------------------------------------------------------
# POST /api/mode
# ---------------------------------------------------------------------------
@router.post("/mode")
async def toggle_mode(payload: ModePayload, request: Request) -> dict[str, Any]:
    """POST /api/mode — toggle paper/live mode; 400 if engine active or live guard fails."""
    manager = _get_manager(request)
    if manager.has_active_runtime():
        raise _http(
            400,
            "Cannot toggle trading mode while engine is running. Stop the bot first.",
        )

    try:
        if not payload.paper:
            key, secret = manager.get_credentials()
            if not key or not secret:
                raise ValueError("Alpaca credentials are required")
            manager._require_valid_live_arm(key, secret, consume=False)
        raw: dict[str, Any] = {}
        if manager.config_path.exists():
            with open(manager.config_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        raw.setdefault("runtime", {})["mode"] = "paper" if payload.paper else "live"
        if not payload.paper:
            raw.setdefault("live", {})["enabled"] = True
        atomic_write(
            manager.config_path,
            yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
        )

        manager.reset_broker()  # Force broker reset
        return {"status": "success", "paper": payload.paper}
    except ValueError as e:
        raise _http(400, str(e)) from e
    except Exception:
        log.exception("Trading-mode toggle failed with an unexpected error")
        raise _http(500, "Trading mode could not be changed") from None


# ---------------------------------------------------------------------------
# GET /api/live/prerequisites, POST /api/live/arming/* — no secrets in response
# ---------------------------------------------------------------------------
@router.get("/live/prerequisites")
async def live_prerequisites(request: Request) -> dict[str, Any]:
    """Live-arm readiness checklist (no secrets in the response)."""
    manager = _get_manager(request)
    return await asyncio.to_thread(manager.live_prerequisites)


@router.post("/live/arming/request")
async def request_live_arming(request: Request) -> dict[str, str]:
    manager = _get_manager(request)
    try:
        suffix = await asyncio.to_thread(manager.request_live_arming)
        return {
            "status": "confirmation_required",
            "account_suffix": suffix,
        }
    except ValueError as exc:
        raise _http(400, str(exc)) from exc


@router.post("/live/arming/confirm")
async def confirm_live_arming(payload: LiveConfirmationPayload, request: Request) -> dict[str, str]:
    manager = _get_manager(request)
    try:
        manager.confirm_live_arming(payload.phrase)
        return {
            "status": "armed",
            "message": "Live mode armed for this server process. Start remains a separate action.",
        }
    except ValueError as exc:
        raise _http(400, str(exc)) from exc


# ---------------------------------------------------------------------------
# POST /api/ws-ticket
# ---------------------------------------------------------------------------
@router.post("/ws-ticket")
async def websocket_ticket(payload: WebSocketTicketPayload, request: Request) -> dict[str, str]:
    manager = _get_manager(request)
    try:
        return {"ticket": manager.issue_ws_ticket(payload.kind)}
    except ValueError as exc:
        raise _http(400, str(exc)) from exc
