"""The session must be unable to authenticate or reach anything off the host.

The repository ``.env`` holds the shared Alpaca PAPER key of the account the VM
trades, so the suite's two load-bearing guarantees are enforced rather than
assumed: no dotenv file inside the repository is ever loaded, and no socket
connection leaves the loopback interface.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import dotenv
import dotenv.main
import pytest

from tests.conftest import OutboundNetworkBlocked

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_a_dotenv_file_inside_the_repository_is_never_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test run must not pick up the operator's key from the checkout.

    ``find_dotenv`` is pointed at the shipped ``.env.example`` — a real file
    inside the repository — so the guard is exercised against the path the
    bare ``load_dotenv()`` in load_config()/the CLI would resolve on the
    trading host, without a real ``.env`` being read or written.
    """
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.setattr(dotenv.main, "find_dotenv", lambda *args, **kwargs: str(_REPO_ROOT / ".env.example"))

    assert dotenv.load_dotenv() is False
    assert "ALPACA_API_KEY" not in os.environ


def test_a_dotenv_file_outside_the_repository_still_loads(tmp_path: Path) -> None:
    """The guard is scoped to the checkout, not a blanket refusal."""
    env_file = tmp_path / ".env"
    env_file.write_text('SOME_FIXTURE_VALUE="loaded"\n', encoding="utf-8")

    try:
        assert dotenv.load_dotenv(env_file) is True
        assert os.environ["SOME_FIXTURE_VALUE"] == "loaded"
    finally:
        os.environ.pop("SOME_FIXTURE_VALUE", None)


def test_a_connection_off_the_loopback_interface_is_refused() -> None:
    """Every attempt to reach a broker or a data vendor raises."""
    with pytest.raises(OutboundNetworkBlocked):
        socket.create_connection(("paper-api.alpaca.markets", 443), timeout=1)
    with pytest.raises(OutboundNetworkBlocked):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("192.0.2.1", 9))


def test_the_web_engine_manager_holds_no_credentials() -> None:
    """``get_credentials`` is what the auto-start and start paths authenticate with."""
    from parayabuck.web.managers.engine import EngineManager

    assert EngineManager().get_credentials() == (None, None)


def test_loopback_sockets_still_work_for_in_process_servers() -> None:
    """In-process test servers bind 127.0.0.1 and must stay reachable."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        client = socket.create_connection(listener.getsockname(), timeout=5)
        try:
            connection, _address = listener.accept()
            connection.close()
        finally:
            client.close()
    finally:
        listener.close()
