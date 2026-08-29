"""Ensure WebState isolation between tests — prevents cross-test pollution via global singleton."""

from __future__ import annotations

import pytest

from parayabuck.web.state import get_web_state, set_web_state


@pytest.fixture(autouse=True)
def _restore_web_state():
    before = get_web_state()
    try:
        from parayabuck.web import server as srv

        orig_manager = srv.manager
        orig_backtest = srv.backtest_manager
    except Exception:
        orig_manager = None  # type: ignore[assignment]
        orig_backtest = None  # type: ignore[assignment]

    yield

    current = get_web_state()
    if current is not before:
        set_web_state(before)
    try:
        from parayabuck.web import server as srv2

        if before is not None and srv2.web_state is not before:
            srv2.web_state = before  # type: ignore[attr-defined]
            set_web_state(before)
        if orig_manager is not None and srv2.manager is not orig_manager:
            srv2.manager = orig_manager  # type: ignore[attr-defined]
        if orig_backtest is not None and srv2.backtest_manager is not orig_backtest:
            srv2.backtest_manager = orig_backtest  # type: ignore[attr-defined]
    except Exception:
        pass
