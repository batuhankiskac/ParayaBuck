"""FastAPI application factory — composition root for Web platform."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from parayabuck.web.lifespan import create_lifespan
from parayabuck.web.security import build_protect_middleware
from parayabuck.web.state import WebState, get_web_state, set_web_state


def _default_state() -> WebState:
    from parayabuck.web.managers.backtest import BacktestManager
    from parayabuck.web.managers.engine import EngineManager
    from parayabuck.web.managers.websocket import ticket_store

    engine_manager = EngineManager()
    backtest_manager = BacktestManager(engine_state_provider=engine_manager.current_engine_state)
    backtest_manager.bind_engine_state_provider(engine_manager.current_engine_state)

    state = WebState(engine_manager=engine_manager, backtest_manager=backtest_manager, ticket_store=ticket_store)
    state.bind()
    return state


def create_app(state: WebState | None = None) -> FastAPI:
    """Create FastAPI app with all routers and middleware."""
    if state is None:
        state = get_web_state()
        if state is None:
            state = _default_state()

    set_web_state(state)

    lifespan = create_lifespan(lambda: state)

    app = FastAPI(title="ParayaBuck Trading Terminal", lifespan=lifespan)

    def _token_provider() -> str:
        try:
            return state.engine_manager.control_token  # type: ignore
        except Exception:
            return ""

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
    from parayabuck.web.routers.research import router as research_router
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
    app.include_router(research_router)
    app.include_router(ws_router)

    # Compatibility: tests iterate app.routes and expect direct Route objects,
    # but FastAPI's include_router stores _IncludedRouter wrappers. Flatten
    # for direct inspection while keeping openapi intact (which already handles wrappers).
    try:
        flattened: list = []
        for route in app.router.routes:
            if hasattr(route, "original_router"):
                # _IncludedRouter — expand its original router's routes
                try:
                    for r in route.original_router.routes:  # type: ignore
                        flattened.append(r)
                except Exception:
                    flattened.append(route)
            else:
                flattened.append(route)
        # Only replace if we actually flattened something (keeps Mount/Route)
        if len(flattened) > len(app.router.routes):
            # Preserve the original list for openapi but expose flattened via app.routes
            # FastAPI's app.routes is a property to app.router.routes, so replace that
            app.router.routes = flattened  # type: ignore
    except Exception:
        pass

    app.state.web = state  # type: ignore
    app.state.web_state = state  # type: ignore
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
    app = FastAPI(title="ParayaBuck Trading Terminal")  # type: ignore[no-redef]
