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
        try:
            if hasattr(self.backtest_manager, "bind_engine_state_provider"):
                self.backtest_manager.bind_engine_state_provider(self.engine_manager.current_engine_state)
        except Exception:
            pass


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
