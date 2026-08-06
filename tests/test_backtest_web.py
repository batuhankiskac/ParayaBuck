from __future__ import annotations

import asyncio
from typing import Any

import pytest

from parayabuck.web import server


def _payload(**overrides: Any) -> dict[str, Any]:
    values = {
        "profile": "live",
        "market": "equity",
        "years": 3,
        "days": 90,
        "symbols": "AAPL",
        "max_symbols": 5,
        "adjustment": "splits",
    }
    values.update(overrides)
    return values


def test_backtest_start_and_status(monkeypatch: Any) -> None:
    thread_box: dict[str, Any] = {}

    class FakeThread:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            thread_box["args"] = args
            thread_box["kwargs"] = kwargs

        def start(self) -> None:
            self.started = True

        def is_alive(self) -> bool:
            return False

    server.backtest_manager._thread_factory = FakeThread
    try:
        payload = server.BacktestPayload(**_payload())
        response = asyncio.run(server.run_backtest(payload))
    finally:
        server.backtest_manager._thread_factory = None
    assert response["status"] == "started"
    assert response["run_id"].startswith("web-")

    argv = thread_box["kwargs"]["args"][0]
    assert argv[0] == "--no-monte-carlo"
    assert "--output-dir" in argv
    assert argv[argv.index("--output-dir") + 1] == server.backtest_manager.output_dir
    assert "--profile" in argv and argv[argv.index("--profile") + 1] == "live"
    assert "--days" in argv and argv[argv.index("--days") + 1] == "90"
    assert "--market" in argv and argv[argv.index("--market") + 1] == "equity"
    assert "--symbols" in argv and argv[argv.index("--symbols") + 1] == "AAPL"

    status = asyncio.run(server.backtest_status())
    assert status["state"] in {"running", "idle", "done"}
    assert status["run_id"] == response["run_id"]
    assert status["output_dir"].startswith("reports/")


def test_backtest_rejects_second_run_while_running(monkeypatch: Any) -> None:
    class AliveThread:
        def is_alive(self) -> bool:
            return True

    server.backtest_manager._thread = AliveThread()
    server.backtest_manager.state = "running"
    try:
        with pytest.raises(Exception, match="zaten çalışıyor"):
            asyncio.run(server.run_backtest(server.BacktestPayload(**_payload())))
    finally:
        server.backtest_manager._thread = None
        server.backtest_manager.state = "idle"
        server.backtest_manager._thread_factory = None


def test_backtest_argv_zero_days_uses_years() -> None:
    manager = server.BacktestManager()
    argv = manager._build_argv(
        {"profile": "auto", "market": "equity", "days": 0, "years": 10},
        "/tmp/out",
    )
    assert "--days" not in argv
    assert "--years" in argv and argv[argv.index("--years") + 1] == "10"


def test_backtest_manager_stream_captures_progress() -> None:
    manager = server.BacktestManager()
    stream = manager._stream()
    stream.write("satır bir\n")
    stream.write("satır iki")
    stream.close()
    assert manager.progress == ["satır bir", "satır iki"]
