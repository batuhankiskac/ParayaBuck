"""Compatibility shim — thin bootstrap for Web platform (Mission 6)."""

from __future__ import annotations

import asyncio
import sys
from typing import Any  # noqa: F401  # used in payload: Any annotations

from parayabuck.web.application import create_app
from parayabuck.web.managers.backtest import BacktestManager
from parayabuck.web.managers.engine import _PLACEHOLDERS, EngineManager, LiveArmToken  # noqa: F401
from parayabuck.web.managers.websocket import ticket_store as _ticket_store
from parayabuck.web.schemas.settings import (
    BacktestPayload,
    LiveConfirmationPayload,
    ModePayload,
    SettingsPayload,
    ValidatedSettings,
    WebSocketTicketPayload,
)
from parayabuck.web.state import WebState, set_web_state


# Keep config.load_config and universe validators in sync when tests patch server.*
class _ServerModule(sys.modules[__name__].__class__):  # type: ignore[misc]
    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name == "load_config":
            try:
                import parayabuck.config as _cfg

                _cfg.load_config = value
            except Exception:
                pass
        if name in ("validate_active_universe", "validate_active_crypto_universe"):
            try:
                import parayabuck.universe as _u

                setattr(_u, name, value)
            except Exception:
                pass
        if name == "manager":
            try:
                from parayabuck.web.state import get_web_state

                ws = get_web_state()
                if ws is not None:
                    ws.engine_manager = value
                try:
                    web_state.engine_manager = value
                except Exception:
                    pass
                try:
                    backtest_manager.bind_engine_state_provider(value.current_engine_state)
                except Exception:
                    pass
                try:
                    value.bind_backtest_manager(backtest_manager)
                except Exception:
                    pass
                try:
                    value.bind_ticket_store(web_state.ticket_store)
                except Exception:
                    pass
            except Exception:
                pass


sys.modules[__name__].__class__ = _ServerModule

# Singletons — shared with application factory
manager = EngineManager()
backtest_manager = BacktestManager(engine_state_provider=manager.current_engine_state)
try:
    backtest_manager.bind_engine_state_provider(manager.current_engine_state)
except Exception:
    pass

try:
    import parayabuck.web.managers.engine as _eng_mod

    _eng_mod.backtest_manager = backtest_manager
except Exception:
    pass

web_state = WebState(engine_manager=manager, backtest_manager=backtest_manager, ticket_store=_ticket_store)
web_state.bind()
set_web_state(web_state)

app = create_app(web_state)

_settings_lock = asyncio.Lock()


# --- Settings wrappers (used by tests via direct call) ---
async def get_settings() -> dict[str, Any]:
    from parayabuck.web.services.settings import SettingsService

    svc = SettingsService(manager, _settings_lock)
    return await svc.get_settings()


async def _handle_save_settings(payload: SettingsPayload) -> dict[str, Any]:
    from parayabuck.web.services.settings import SettingsService

    svc = SettingsService(manager, _settings_lock)
    return await svc.save_settings(payload)


# --- Health / pages (simple delegation, no Request needed for healthz) ---
try:
    from parayabuck.web.routers.health import healthz as _healthz  # noqa: F401
    from parayabuck.web.routers.health import readyz as _readyz

    healthz = _healthz
    readyz = _readyz
except Exception:
    healthz = None
    readyz = None

try:
    from parayabuck.web.routers.pages import serve_index as _serve_index  # noqa: F401
    from parayabuck.web.routers.pages import serve_tools as _serve_tools

    serve_index = _serve_index
    serve_tools = _serve_tools
except Exception:
    serve_index = None
    serve_tools = None


async def get_control_token() -> Any:
    """Direct-call compatibility wrapper for the legacy server surface."""
    from fastapi import HTTPException as _HTTP
    from fastapi.responses import JSONResponse

    token = getattr(manager, "control_token", None)
    if not isinstance(token, str) or not token:
        raise _HTTP(status_code=503, detail="Control token unavailable")
    response = JSONResponse({"control_token": token})
    response.headers["Cache-Control"] = "no-store"
    return response


# --- Status / account / ops (wrappers without Request for direct calls) ---
async def get_status() -> dict[str, Any]:
    import asyncio as _aio

    from parayabuck.web.services.status import build_status_snapshot

    return await _aio.to_thread(build_status_snapshot, manager)


async def get_account() -> dict[str, Any]:
    import asyncio as _aio

    return await _aio.to_thread(manager.fetch_account_snapshot)


async def get_ops_status() -> dict[str, Any]:
    import asyncio as _aio

    from parayabuck.web.services.ops import get_ops_status as _ops

    return await _aio.to_thread(_ops)


# --- Engine / safety (wrappers) ---
async def start_engine() -> dict[str, Any]:
    import asyncio as _aio

    from fastapi import HTTPException as _HTTP

    try:
        await _aio.to_thread(manager.start_engine)
        return {"status": "started", "message": "Bot engine started successfully."}
    except Exception as e:
        raise _HTTP(status_code=400, detail=str(e)) from e


async def stop_engine() -> dict[str, Any]:
    import asyncio as _aio

    from fastapi import HTTPException as _HTTP

    try:
        await _aio.to_thread(manager.stop_engine)
    except RuntimeError as exc:
        raise _HTTP(status_code=500, detail=str(exc)) from exc
    except Exception:
        raise _HTTP(status_code=500, detail="Engine could not be stopped") from None
    return {"status": "stopped", "message": "Bot engine stopped."}


async def reset_safety_lock() -> dict[str, Any]:
    import asyncio as _aio

    from fastapi import HTTPException as _HTTP

    try:
        await _aio.to_thread(manager.reset_safety_lock)
    except ValueError as exc:
        raise _HTTP(status_code=400, detail=str(exc)) from exc
    except Exception:
        raise _HTTP(status_code=500, detail="Safety lock reset failed") from None
    return {"status": "reset", "message": "Safety lock reset after broker reconciliation and data checks."}


# --- Engine / safety direct wrappers (for tests that call server.* without Request) ---
async def live_prerequisites() -> dict[str, Any]:
    import asyncio as _aio

    from fastapi import HTTPException as _HTTP

    try:
        return await _aio.to_thread(manager.live_prerequisites)
    except Exception as exc:
        raise _HTTP(status_code=400, detail=str(exc)) from exc


async def start_new_risk_epoch() -> dict[str, Any]:
    import asyncio as _aio

    from fastapi import HTTPException as _HTTP

    try:
        return await _aio.to_thread(manager.start_new_risk_epoch)
    except ValueError as exc:
        raise _HTTP(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise _HTTP(status_code=500, detail=str(exc)) from exc


async def request_live_arming(payload: Any = None) -> dict[str, Any]:
    import asyncio as _aio

    from fastapi import HTTPException as _HTTP

    try:
        # manager.request_live_arming may not exist in stub, handle gracefully
        fn = getattr(manager, "request_live_arming", None)
        if fn is None:
            raise ValueError("request_live_arming not available")
        return await _aio.to_thread(fn, payload) if payload is not None else await _aio.to_thread(fn)
    except Exception as exc:
        raise _HTTP(status_code=400, detail=str(exc)) from exc


async def confirm_live_arming(payload: Any = None) -> dict[str, Any]:
    import asyncio as _aio

    from fastapi import HTTPException as _HTTP

    try:
        fn = getattr(manager, "confirm_live_arming", None)
        if fn is None:
            raise ValueError("confirm_live_arming not available")
        return await _aio.to_thread(fn, payload) if payload is not None else await _aio.to_thread(fn)
    except Exception as exc:
        raise _HTTP(status_code=400, detail=str(exc)) from exc


# Keep router functions available under _router_* for app factory (not used by tests)
try:
    from parayabuck.web.routers.safety import (  # noqa: F401
        confirm_live_arming as _router_confirm_live_arming,
    )
    from parayabuck.web.routers.safety import (
        live_prerequisites as _router_live_prerequisites,
    )
    from parayabuck.web.routers.safety import (
        request_live_arming as _router_request_live_arming,
    )
    from parayabuck.web.routers.safety import (
        start_new_risk_epoch as _router_start_new_risk_epoch,
    )
    from parayabuck.web.routers.safety import (
        toggle_mode as _router_toggle_mode,
    )
    from parayabuck.web.routers.safety import (
        websocket_ticket as _router_websocket_ticket,
    )
except Exception:
    _router_confirm_live_arming = None
    _router_live_prerequisites = None
    _router_request_live_arming = None
    _router_start_new_risk_epoch = None
    _router_toggle_mode = None
    _router_websocket_ticket = None

# Expose for __all__ and for tests that import server.toggle_mode etc (HTTP layer)
toggle_mode = _router_toggle_mode
websocket_ticket = _router_websocket_ticket

# --- Settings wrappers ---
# Direct-call wrappers for tests (bypass Depends). Keep router versions as _router_*.
try:
    from parayabuck.web.routers.settings import patch_settings as _router_patch_settings  # noqa: F401
    from parayabuck.web.routers.settings import save_settings as _router_save_settings  # noqa: F401
except Exception:
    _router_patch_settings = None
    _router_save_settings = None


async def save_settings(payload: SettingsPayload) -> dict[str, Any]:
    from fastapi import HTTPException as _HTTP

    from parayabuck.logger import get_logger
    from parayabuck.web.services.settings import SettingsService

    log = get_logger("parayabuck.web.server")
    svc = SettingsService(manager, _settings_lock)
    try:
        return await svc.save_settings(payload)
    except _HTTP:
        raise
    except ValueError as e:
        raise _HTTP(status_code=400, detail=str(e)) from e
    except Exception:
        log.exception("Settings save failed with an unexpected error")
        raise _HTTP(status_code=500, detail="Settings could not be saved") from None


async def patch_settings(payload: SettingsPayload) -> dict[str, Any]:
    from fastapi import HTTPException as _HTTP

    from parayabuck.logger import get_logger
    from parayabuck.web.services.settings import SettingsService

    log = get_logger("parayabuck.web.server")
    svc = SettingsService(manager, _settings_lock)
    try:
        return await svc.save_settings(payload)
    except _HTTP:
        raise
    except ValueError as e:
        raise _HTTP(status_code=400, detail=str(e)) from e
    except Exception:
        log.exception("Settings save failed with an unexpected error")
        raise _HTTP(status_code=500, detail="Settings could not be saved") from None


async def run_backtest(payload: BacktestPayload) -> dict[str, Any]:
    # Direct call wrapper for tests that call server.run_backtest(payload) without HTTP
    # Use backtest_manager directly, not router (which needs Depends)
    import asyncio as _aio

    from fastapi import HTTPException as _HTTP

    try:
        # payload is BacktestPayload, need dict
        data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        run_id = await _aio.to_thread(backtest_manager.start, data)
        return {"status": "started", "run_id": run_id}
    except Exception as exc:
        raise _HTTP(status_code=400, detail=str(exc)) from exc


async def backtest_status() -> dict[str, Any]:
    import asyncio as _aio

    return await _aio.to_thread(backtest_manager.status)


# --- Dashboard wrappers (for tests that call server.dashboard_overview etc.) ---
async def dashboard_overview() -> dict[str, Any]:
    import asyncio as _aio

    from parayabuck.web.services.dashboard import get_overview_payload

    return await _aio.to_thread(get_overview_payload, web_state)


async def _dashboard_panel(key: str) -> dict[str, Any]:
    import asyncio as _aio

    from parayabuck.web.services.dashboard import get_panel

    return await _aio.to_thread(get_panel, key, web_state)


async def dashboard_equity() -> dict[str, Any]:
    return await _dashboard_panel("equity")


async def dashboard_crypto() -> dict[str, Any]:
    return await _dashboard_panel("crypto")


async def dashboard_risk() -> dict[str, Any]:
    return await _dashboard_panel("risk")


async def dashboard_execution() -> dict[str, Any]:
    return await _dashboard_panel("execution")


async def dashboard_universe() -> dict[str, Any]:
    return await _dashboard_panel("universe")


async def dashboard_health() -> dict[str, Any]:
    return await _dashboard_panel("health")


async def dashboard_explanations() -> dict[str, Any]:
    return await _dashboard_panel("explanations")


async def dashboard_metrics() -> dict[str, Any]:
    return await _dashboard_panel("metrics")


# --- Universe validation shims (for tests that patch server.validate_active_universe) ---
try:
    from parayabuck.universe import (
        validate_active_crypto_universe as _orig_validate_active_crypto_universe,  # noqa: F401
    )
    from parayabuck.universe import validate_active_universe as _orig_validate_active_universe  # noqa: F401

    validate_active_universe = _orig_validate_active_universe
    validate_active_crypto_universe = _orig_validate_active_crypto_universe
except Exception:
    validate_active_universe = None  # type: ignore[assignment]
    validate_active_crypto_universe = None  # type: ignore[assignment]

# Re-export _atomic_update_credentials for hardening tests
try:
    from parayabuck.web.managers.engine import _atomic_update_credentials  # noqa: F401
except Exception:
    _atomic_update_credentials = None  # type: ignore[assignment]


# --- Legacy helpers for dashboard tests ---
def _dashboard_payload(*args: Any, **kwargs: Any) -> Any:
    # Sync wrapper for dashboard tests and services/dashboard delegation.
    # Try legacy sync implementation first, then thin's own.
    try:
        import parayabuck.web.server_legacy as _leg

        # The legacy implementation now receives explicit per-request state.
        context = kwargs.pop("context", None)
        if context is None:
            context = _leg.DashboardContext(
                engine_manager=web_state.engine_manager,
                backtest_manager=web_state.backtest_manager,
            )
        call_kwargs = dict(kwargs)
        call_kwargs["context"] = context
        return _leg._dashboard_payload(*args, **call_kwargs)
    except Exception:
        pass
    try:
        # Avoid recursion: if we're being called from get_overview_payload, don't call back
        # Instead, try to build payload via status service directly
        import parayabuck.web.services.dashboard as _dash_svc

        # fallback: try to call the service's internal helper if available
        return _dash_svc.get_overview_payload(*args, **kwargs)
    except Exception as exc:
        return {"unavailable": True, "error": str(exc)}


def _dashboard_market_decisions(*args: Any, **kwargs: Any) -> Any:
    try:
        import parayabuck.web.server_legacy as leg

        return leg._dashboard_market_decisions(*args, **kwargs)
    except Exception:
        try:
            from parayabuck.web.services.status import (  # type: ignore[attr-defined]
                _dashboard_market_decisions as _orig,
            )

            return _orig(*args, **kwargs)
        except Exception:
            return {}


# --- WebSocket wrappers ---
try:
    from parayabuck.web.routers.websocket import websocket_logs, websocket_stream  # noqa: F401
except Exception:
    websocket_stream = None
    websocket_logs = None

# --- Config and lifespan ---
try:
    from parayabuck.config import load_config  # noqa: F401
except Exception:
    load_config = None  # type: ignore[assignment]

try:
    from parayabuck.web.lifespan import create_lifespan as _create_lifespan  # noqa: F401

    app_lifespan = _create_lifespan(lambda: web_state)
except Exception:
    app_lifespan = None

try:
    from fastapi import HTTPException  # noqa: F401
except Exception:
    HTTPException = Exception

# Also expose _settings_lock already defined
__all__ = [
    "app",
    "create_app",
    "manager",
    "backtest_manager",
    "EngineManager",
    "BacktestManager",
    "LiveArmToken",
    "WebState",
    "web_state",
    "SettingsPayload",
    "BacktestPayload",
    "ModePayload",
    "LiveConfirmationPayload",
    "WebSocketTicketPayload",
    "ValidatedSettings",
    "_settings_lock",
    "get_settings",
    "_handle_save_settings",
    "save_settings",
    "patch_settings",
    "load_config",
    "app_lifespan",
    "healthz",
    "readyz",
    "serve_index",
    "serve_tools",
    "get_control_token",
    "get_status",
    "get_account",
    "get_ops_status",
    "start_engine",
    "stop_engine",
    "reset_safety_lock",
    "confirm_live_arming",
    "live_prerequisites",
    "request_live_arming",
    "start_new_risk_epoch",
    "toggle_mode",
    "websocket_ticket",
    "run_backtest",
    "backtest_status",
    "dashboard_overview",
    "dashboard_equity",
    "dashboard_crypto",
    "dashboard_risk",
    "dashboard_execution",
    "dashboard_universe",
    "dashboard_health",
    "dashboard_explanations",
    "dashboard_metrics",
    "_dashboard_payload",
    "_dashboard_market_decisions",
    "validate_active_universe",
    "validate_active_crypto_universe",
    "websocket_stream",
    "websocket_logs",
    "HTTPException",
]
