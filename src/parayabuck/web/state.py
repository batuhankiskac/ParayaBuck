"""Composition root for Web platform."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


# Forward references avoid circular imports at import time.
@dataclass
class WebState:
    engine_manager: Any
    backtest_manager: Any
    ticket_store: Any | None = None
    # Serializes every settings.yaml rewrite of this app (settings drawer and
    # /api/mode): one lock per state, never one per request.
    settings_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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


# The process-wide state ``parayabuck.web.server`` installs; request-less
# service calls resolve it through ``web.deps``. Importing this module never
# starts engines or contacts brokers.
_web_state: WebState | None = None


def set_web_state(state: WebState | None) -> None:
    global _web_state
    _web_state = state


def get_web_state() -> WebState | None:
    return _web_state
