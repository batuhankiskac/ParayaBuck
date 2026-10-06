"""Process-wide Web composition: the one EngineManager this process serves.

``uvicorn parayabuck.web.server:app`` (and ``parayabuck web``) serve ``app``.
Importing this module builds exactly one WebState — engine manager, backtest
manager and WebSocket ticket store — and installs it as the process-wide
state that request-less service calls resolve through ``web.deps``. Tests
that need an isolated app call ``create_app(WebState(...))`` instead.
"""

from __future__ import annotations

from parayabuck.web.application import create_app
from parayabuck.web.managers.backtest import BacktestManager
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.managers.websocket import ticket_store
from parayabuck.web.state import WebState, set_web_state

manager = EngineManager()
backtest_manager = BacktestManager(engine_state_provider=manager.current_engine_state)
web_state = WebState(engine_manager=manager, backtest_manager=backtest_manager, ticket_store=ticket_store)
set_web_state(web_state)

app = create_app(web_state)

__all__ = ["app", "backtest_manager", "manager", "web_state"]
