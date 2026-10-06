"""The one place a router or service resolves the Web composition state.

Order (identical everywhere):

1. ``request.app.state.web`` — the state the app was created with;
2. ``parayabuck.web.state.get_web_state()`` — the process-wide state that
   ``parayabuck.web.server`` installs (used by request-less service calls).

There is no other fallback: a missing state is a 503, never a second,
silently created EngineManager.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from parayabuck.web.state import WebState, get_web_state


def resolve_web_state(request: Request | None = None) -> WebState:
    if request is not None:
        state = getattr(request.app.state, "web", None)
        if state is not None:
            return state  # type: ignore[no-any-return]
    state = get_web_state()
    if state is None:
        raise HTTPException(status_code=503, detail="Web state unavailable")
    return state


def get_engine_manager(request: Request) -> Any:
    """The active EngineManager, or 503."""
    manager = getattr(resolve_web_state(request), "engine_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Engine manager unavailable")
    return manager


def get_backtest_manager(request: Request) -> Any:
    """The active BacktestManager, or 503."""
    manager = getattr(resolve_web_state(request), "backtest_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Backtest manager unavailable")
    return manager


__all__ = ["get_backtest_manager", "get_engine_manager", "resolve_web_state"]
