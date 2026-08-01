from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts import run_backtest


def test_cli_enables_simulated_execution(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-01-02T14:30:00Z,AAPL,100,101,99,100,1000\n",
        encoding="utf-8",
    )
    observed: dict[str, Any] = {}

    @dataclass
    class Result:
        equity_curve: tuple[float, ...] = ()
        realized_pnls: tuple[float, ...] = ()
        total_trades: int = 0

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            observed["trading_enabled"] = config.trading_enabled

        def run(self, _bars: Any) -> Result:
            return Result()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(
        "sys.argv",
        ["run_backtest.py", str(csv_path), "--timeframe", "5m"],
    )

    run_backtest.main()

    assert observed["trading_enabled"] is True


def test_cli_rejects_unvalidated_timeframe(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["run_backtest.py", "bars.csv", "--timeframe", "15m"],
    )

    with pytest.raises(SystemExit, match="2"):
        run_backtest.main()
