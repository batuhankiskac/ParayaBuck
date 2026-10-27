from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

from parayabuck.web.managers.backtest import BacktestManager
from tests.helpers import web_client


def _payload(**overrides: Any) -> dict[str, Any]:
    values = {
        "market": "equity",
        "strategy": "equity_leveraged_trend_v1",
        "parameters": {"vol_target": 0.35},
        "years": 3,
        "days": 90,
        "symbols": "SPY,QQQ,QLD",
    }
    values.update(overrides)
    return values


def test_backtest_start_and_status(monkeypatch: Any, tmp_path: Any) -> None:
    thread_box: dict[str, Any] = {}

    class FakeThread:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            thread_box["args"] = args
            thread_box["kwargs"] = kwargs

        def start(self) -> None:
            self.started = True

        def is_alive(self) -> bool:
            return False

    backtests = BacktestManager()
    # Explicit root: a run must never prune the repository's reports/ (Q2-5-4).
    backtests.reports_root = tmp_path
    backtests._thread_factory = FakeThread
    client = web_client(SimpleNamespace(current_engine_state=lambda: "stopped"), backtest_manager=backtests)
    response = client.post("/api/backtest/run", json=_payload()).json()
    assert response["status"] == "started"
    assert response["run_id"].startswith("web-")

    argv = thread_box["kwargs"]["args"][0]
    assert argv[0] == "backtest"
    assert "--output-dir" in argv
    assert argv[argv.index("--output-dir") + 1] == backtests.output_dir
    assert "--start" in argv and "--end" in argv
    assert "--market" in argv and argv[argv.index("--market") + 1] == "equity"
    assert "--symbols" in argv and argv[argv.index("--symbols") + 1] == "SPY,QQQ,QLD"
    assert "--strategy" in argv
    assert "--param" in argv and argv[argv.index("--param") + 1] == "vol_target=0.35"

    status = client.get("/api/backtest/status").json()
    assert status["state"] in {"running", "idle", "done"}
    assert status["run_id"] == response["run_id"]
    assert status["output_dir"].startswith(str(tmp_path) + "/")


def test_backtest_rejects_second_run_while_running(monkeypatch: Any) -> None:
    class AliveThread:
        def is_alive(self) -> bool:
            return True

    backtests = BacktestManager()
    backtests._thread = AliveThread()
    backtests.state = "running"
    client = web_client(SimpleNamespace(current_engine_state=lambda: "stopped"), backtest_manager=backtests)
    response = client.post("/api/backtest/run", json=_payload())
    assert response.status_code == 400
    assert "zaten çalışıyor" in response.json()["detail"]


def test_backtest_argv_zero_days_uses_years() -> None:
    manager = BacktestManager()
    argv = manager._build_argv(
        {"market": "equity", "days": 0, "years": 10},
        "/tmp/out",
    )
    assert "--start" in argv and "--end" in argv
    start = argv[argv.index("--start") + 1]
    end = argv[argv.index("--end") + 1]
    window_days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    assert 3648 <= window_days <= 3650


def test_backtest_manager_stream_captures_progress() -> None:
    manager = BacktestManager()
    stream = manager._stream()
    stream.write("satır bir\n")
    stream.write("satır iki")
    stream.close()
    assert manager.progress == ["satır bir", "satır iki"]
