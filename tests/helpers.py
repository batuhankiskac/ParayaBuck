from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger


def make_broker(
    tmp_path: Path,
    *,
    client: Any = None,
    ledger: ExecutionLedger | None = None,
    peak_state_path: Path | str | None = None,
    **kwargs: Any,
) -> AlpacaBroker:
    """Build a real ``AlpacaBroker`` for a test.

    Tests must never bypass ``AlpacaBroker.__init__``: an uninitialised broker
    owns no service objects, so it can only reach fallback code inside the
    facade instead of the production service implementations. The peak
    sidecar is always confined to ``tmp_path`` so no test touches a
    production state path.
    """
    return AlpacaBroker(
        "test-api-key",
        "test-secret-key",
        paper=True,
        client=client if client is not None else SimpleNamespace(),
        ledger=ledger,
        peak_state_path=(peak_state_path if peak_state_path is not None else tmp_path / "paper_equity_peak.json"),
        **kwargs,
    )


_TEST_CONTROL_TOKEN = "test-control-token"


def web_client(manager: Any, backtest_manager: Any | None = None) -> Any:
    """``TestClient`` over an app composed around ``manager`` (no lifespan).

    The client is a loopback peer and sends the manager's control token, so
    mutating routes pass the local-only middleware exactly like the browser
    UI. Fakes without a token get a fixed test token.
    """
    from fastapi.testclient import TestClient

    from parayabuck.web.application import create_app
    from parayabuck.web.state import WebState

    token = getattr(manager, "control_token", None)
    if not isinstance(token, str) or not token:
        token = _TEST_CONTROL_TOKEN
        manager.control_token = token
    state = WebState(
        engine_manager=manager,
        backtest_manager=(
            backtest_manager
            if backtest_manager is not None
            else getattr(manager, "_backtest_manager", None) or SimpleNamespace()
        ),
    )
    return TestClient(
        create_app(state),
        raise_server_exceptions=False,
        client=("127.0.0.1", 50000),
        headers={"X-Parayabuck-Control-Token": token},
    )


def make_manager(root: Path, *, credentials: tuple[str, str] = ("pk-fake-key", "fake-secret-value")) -> Any:
    """Stubbed ``EngineManager`` bound to ``root`` settings (no network)."""
    from parayabuck.web.managers.engine import EngineManager

    manager = EngineManager()
    manager.config_path = root / "settings.yaml"
    manager.env_path = root / ".env"
    manager.get_credentials = lambda: credentials  # type: ignore[method-assign]
    # B3: LIVE reads its own key pair; the stub serves both modes alike.
    manager.get_live_credentials = lambda: credentials  # type: ignore[method-assign]
    manager.get_paper_credentials = lambda: credentials  # type: ignore[method-assign]
    return manager


__all__ = ["make_broker", "make_manager", "web_client"]
