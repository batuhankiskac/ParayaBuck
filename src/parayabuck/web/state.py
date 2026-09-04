"""Composition root for Web platform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Forward references avoid circular imports at import time.
@dataclass
class WebState:
    engine_manager: Any
    backtest_manager: Any
    ticket_store: Any | None = None

    def bind(self) -> None:
        """Wire cross-dependencies (backtest ↔ engine)."""
        # Both managers must use this composition-owned pair.  The backtest
        # lock is acquired before the engine state lock in both start paths.
        bind_engine = getattr(self.engine_manager, "bind_backtest_manager", None)
        if callable(bind_engine):
            bind_engine(self.backtest_manager)
        bind_tickets = getattr(self.engine_manager, "bind_ticket_store", None)
        if self.ticket_store is not None and callable(bind_tickets):
            bind_tickets(self.ticket_store)
        bind_backtest = getattr(self.backtest_manager, "bind_engine_state_provider", None)
        if callable(bind_backtest):
            bind_backtest(self.engine_manager.current_engine_state)


# Global singleton used by server.py compatibility layer and by tests that
# call `get_web_state()` via lazy import.  The factory in application.py
# will replace this on app creation, but importing `web.state` never
# starts engines or contacts brokers.
_web_state: WebState | None = None


def set_web_state(state: WebState | None) -> None:
    global _web_state
    _web_state = state


def get_web_state() -> WebState | None:
    return _web_state
