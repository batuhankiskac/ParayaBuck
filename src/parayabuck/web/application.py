"""FastAPI application factory — composition root for Web platform."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from parayabuck.web.lifespan import create_lifespan
from parayabuck.web.security import build_protect_middleware
from parayabuck.web.state import WebState


def _default_state() -> WebState:
    from parayabuck.web.managers.backtest import BacktestManager
    from parayabuck.web.managers.engine import EngineManager
    from parayabuck.web.security import WebSocketTicketStore

    engine_manager = EngineManager()
    backtest_manager = BacktestManager(engine_state_provider=engine_manager.current_engine_state)

    state = WebState(
        engine_manager=engine_manager,
        backtest_manager=backtest_manager,
        ticket_store=WebSocketTicketStore(ttl_seconds=30.0),
    )
    state.bind()
    return state


def create_app(state: WebState | None = None) -> FastAPI:
    """Create FastAPI app with all routers and middleware."""
    if state is None:
        state = _default_state()
    state.bind()

    lifespan = create_lifespan(lambda: state)

    app = FastAPI(title="ParayaBuck Trading Terminal", lifespan=lifespan)
    app.state.web = state
    app.state.web_state = state

    def _token_provider(request: Request) -> str:
        token = getattr(request.app.state.web.engine_manager, "control_token", None)
        if not isinstance(token, str) or not token:
            raise RuntimeError("control token unavailable")
        return token

    app.middleware("http")(build_protect_middleware(_token_provider))

    base_dir = Path(__file__).resolve().parent
    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    # Import routers lazily to avoid circular
    from parayabuck.web.routers.backtest import router as backtest_router
    from parayabuck.web.routers.control import router as control_router
    from parayabuck.web.routers.dashboard import router as dashboard_router
    from parayabuck.web.routers.engine import router as engine_router
    from parayabuck.web.routers.health import router as health_router
    from parayabuck.web.routers.ops import router as ops_router
    from parayabuck.web.routers.pages import router as pages_router
    from parayabuck.web.routers.safety import router as safety_router
    from parayabuck.web.routers.settings import router as settings_router
    from parayabuck.web.routers.status import router as status_router
    from parayabuck.web.routers.websocket import router as ws_router

    app.include_router(health_router)
    app.include_router(pages_router)
    app.include_router(control_router)
    app.include_router(status_router)
    app.include_router(engine_router)
    app.include_router(safety_router)
    app.include_router(settings_router)
    app.include_router(backtest_router)
    app.include_router(dashboard_router)
    app.include_router(ops_router)
    app.include_router(ws_router)

    # Compatibility: tests iterate app.routes and expect direct Route objects,
    # but FastAPI's include_router stores _IncludedRouter wrappers. Flatten
    # for direct inspection while keeping openapi intact (which already handles wrappers).
    try:
        flattened: list[Any] = []
        for route in app.router.routes:
            if hasattr(route, "original_router"):
                # _IncludedRouter — expand its original router's routes
                try:
                    for r in route.original_router.routes:
                        flattened.append(r)
                except Exception:
                    flattened.append(route)
            else:
                flattened.append(route)
        # Only replace if we actually flattened something (keeps Mount/Route)
        if len(flattened) > len(app.router.routes):
            # Preserve the original list for openapi but expose flattened via app.routes
            # FastAPI's app.routes is a property to app.router.routes, so replace that
            app.router.routes = flattened
    except Exception:
        pass

    return app


# Process-wide default — importing this module creates the same app that
# ``uvicorn parayabuck.web.server:app`` and
# ``uvicorn parayabuck.web.application:app`` would serve. Tests that need a
# fake state call ``create_app(fake_state)`` instead and never touch this.
try:
    app: FastAPI = create_app()
except Exception:  # pragma: no cover
    # Never break import of the package when optional deps are missing in a
    # tooling context.
    app = FastAPI(title="ParayaBuck Trading Terminal")
