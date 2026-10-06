"""Safety / liveness router — live arming, mode toggle, risk-epoch, ws-ticket.

Verbatim extraction of server.py sections:
  /api/safety/risk-epoch (1945-1957), /api/mode (1959-1993),
  /api/live/* (1994-2023), /api/ws-ticket (2024-2030).

All handlers resolve EngineManager through ``web.deps`` (the request's
WebState), then translate domain errors via a small helper to 400/500. Paths are
preserved so OpenAPI contract is unchanged. No secrets are returned.
"""

from __future__ import annotations

import asyncio
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request

from parayabuck.config import load_config
from parayabuck.logger import get_logger
from parayabuck.utils.io import atomic_write
from parayabuck.web.deps import get_engine_manager, resolve_web_state
from parayabuck.web.managers.engine import _yaml_block
from parayabuck.web.schemas.settings import LiveConfirmationPayload, ModePayload, WebSocketTicketPayload

log = get_logger("parayabuck.web.routers.safety")

router = APIRouter(prefix="/api", tags=["safety"])


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
    manager = get_engine_manager(request)
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
def _write_mode(config_path: Any, paper: bool) -> None:
    """Rewrite runtime.mode (and live.enabled) only if the result loads."""
    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    _yaml_block(raw, "runtime")["mode"] = "paper" if paper else "live"
    if not paper:
        _yaml_block(raw, "live")["enabled"] = True
    text = yaml.safe_dump(raw, sort_keys=False, default_flow_style=False)
    candidate = config_path.with_name(f".{config_path.name}.validate")
    atomic_write(candidate, text)
    try:
        load_config(str(candidate))
    finally:
        candidate.unlink(missing_ok=True)
    atomic_write(config_path, text)


@router.post("/mode")
async def toggle_mode(payload: ModePayload, request: Request) -> dict[str, Any]:
    """POST /api/mode — toggle paper/live mode; 400 if engine active or live guard fails."""
    manager = get_engine_manager(request)
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
        # Same lock as the settings drawer, and the same validate-then-replace
        # discipline: an unvalidated write (e.g. live without a dollar cap)
        # broke the next restart (doctor ExecStartPre) and every backup run.
        async with resolve_web_state(request).settings_lock:
            await asyncio.to_thread(_write_mode, manager.config_path, payload.paper)

        manager.reset_broker()  # Force broker reset
        return {"status": "success", "paper": payload.paper}
    except (ValueError, TypeError) as e:
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
    manager = get_engine_manager(request)
    return await asyncio.to_thread(manager.live_prerequisites)


@router.post("/live/arming/request")
async def request_live_arming(request: Request) -> dict[str, str]:
    manager = get_engine_manager(request)
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
    manager = get_engine_manager(request)
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
    manager = get_engine_manager(request)
    try:
        return {"ticket": manager.issue_ws_ticket(payload.kind)}
    except ValueError as exc:
        raise _http(400, str(exc)) from exc
