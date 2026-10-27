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
        raise _http(500, "Yeni risk dönemi başlatılamadı; ayrıntılar uygulama günlüğünde.") from None


# ---------------------------------------------------------------------------
# POST /api/mode
# ---------------------------------------------------------------------------
def _write_mode(config_path: Any, paper: bool) -> None:
    """Rewrite runtime.mode (and live.enabled) only if the result loads."""
    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    runtime = _yaml_block(raw, "runtime")
    # Same predicate as the settings drawer twin (EngineManager): a missing
    # runtime.mode with legacy alpaca.paper=false is a live configuration.
    current_mode = runtime.get("mode")
    if current_mode is None:
        currently_live = (raw.get("alpaca") or {}).get("paper") is False
    else:
        currently_live = str(current_mode).lower() == "live"
    runtime["mode"] = "paper" if paper else "live"
    live = _yaml_block(raw, "live")
    if not paper:
        live["enabled"] = True
    else:
        live["enabled"] = False
        if currently_live:
            # Leaving LIVE must heal the whole live block: doctor --strict
            # --paper rejects hard_dollar_cap>0, so a paper toggle that kept
            # it would strand the next restart (ExecStartPre). A paper->paper
            # no-op keeps a cap/approval staged for the next LIVE arming.
            live["hard_dollar_cap"] = 0.0
            live["operator_approval_path"] = ""
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
    if await asyncio.to_thread(manager.has_active_runtime):
        raise _http(
            400,
            "Motor çalışırken işlem modu değiştirilemez. Önce botu durdurun.",
        )

    try:
        # Same lock as the settings drawer, and the same validate-then-replace
        # discipline: an unvalidated write (e.g. live without a dollar cap)
        # broke the next restart (doctor ExecStartPre) and every backup run.
        # The LIVE arm is checked under the same lock as the write, so an arm
        # that expires or is invalidated in between cannot authorize the toggle.
        # The idle check repeats under the manager's state lock: a start that
        # began after the check above must not read the config mid-toggle.
        async with resolve_web_state(request).settings_lock:
            if not payload.paper:
                # B3: the LIVE arm is bound to the LIVE key pair only.
                key, secret = await asyncio.to_thread(manager.get_live_credentials)
                if not key or not secret:
                    raise ValueError("CANLI Alpaca anahtarları (ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY) gerekli.")
                await asyncio.to_thread(manager._require_valid_live_arm, key, secret, consume=False)
            await asyncio.to_thread(manager.write_mode_if_idle, payload.paper, _write_mode)

        await asyncio.to_thread(manager.reset_broker)  # Force broker reset
        return {"status": "success", "paper": payload.paper}
    except (ValueError, TypeError) as e:
        raise _http(400, str(e)) from e
    except Exception:
        log.exception("Trading-mode toggle failed with an unexpected error")
        raise _http(500, "İşlem modu değiştirilemedi; ayrıntılar uygulama günlüğünde.") from None


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
        await asyncio.to_thread(manager.confirm_live_arming, payload.phrase)
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
