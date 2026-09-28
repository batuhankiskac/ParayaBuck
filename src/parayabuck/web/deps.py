"""Dependency / DI helpers for Web platform.

Centralizes the WebState / EngineManager resolution that was previously
copied verbatim into every router as `_get_manager`.  Routers can now do::

    from parayabuck.web.deps import get_engine_manager

which tries, in priority order:

1. request.app.state.web.engine_manager (factory-injected)
2. parayabuck.web.state.get_web_state().engine_manager
3. parayabuck.web.server.manager (legacy compat, tests patch this)

No circular imports: `parayabuck.web.server` is imported lazily inside
the function, not at module load time.  The helpers are deliberately
sync (no async) so they can be used from both sync and async contexts.

Concurrency helpers (locks) are also re-exported here for discoverability
— the actual RLock instances live on the managers (EngineManager._state_lock,
BacktestManager.lock) so there is a single source of truth.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request


def get_web_state() -> Any | None:
    try:
        from parayabuck.web.state import get_web_state as _get

        return _get()
    except Exception:
        return None


def get_engine_manager(request: Request | None = None) -> Any:
    """Resolve the active EngineManager or raise 500."""
    if request is not None:
        web_state = getattr(request.app.state, "web", None)
        if web_state is not None and hasattr(web_state, "engine_manager"):
            mgr = getattr(web_state, "engine_manager", None)
            if mgr is not None:
                return mgr
    ws = get_web_state()
    if ws is not None and hasattr(ws, "engine_manager"):
        mgr = getattr(ws, "engine_manager", None)
        if mgr is not None:
            return mgr
        # WebState itself might be an EngineManager in tests
        if hasattr(ws, "current_engine_state"):
            return ws
    try:
        import parayabuck.web.server as server_mod

        mgr = getattr(server_mod, "manager", None)
        if mgr is not None:
            return mgr
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="Engine manager unavailable") from exc
    raise HTTPException(status_code=500, detail="Engine manager unavailable")
