from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.backtest import cli as run_backtest
from parayabuck.backtest.cli import _resolve_symbols, validate_range


def _bar(day_offset: int, symbol: str, timeframe: str = "1d") -> Any:
    timestamp = datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=day_offset)
    return SimpleNamespace(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000.0,
        market="equity",
    )


def _args(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "market": "both",
        "years": 3,
        "days": 0,
        "start": None,
        "end": None,
        "timeframe": None,
        "symbols": None,
        "symbol": None,
        "csv": None,
        "universe": None,
        "max_symbols": 40,
        "starting_equity": 100_000.0,
        "slippage_bps": 2.0,
        "spread_bps": 0.0,
        "commission": 0.0,
        "crypto_taker_fee_bps": 5.0,
        "output": None,
        "output_dir": "reports",
        "no_monte_carlo": False,
        "walk_forward": False,
        "wf_train": 1250,
        "wf_test": 250,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeHistory:
    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str = "splits",
    ) -> list[Any]:
        days = max(1, min(120, int((end - start).days)))
        return [_bar(offset, symbol, timeframe) for offset in range(days)]


# --------------------------------------------------------------------------
# CSV modu
# --------------------------------------------------------------------------


@dataclass
class _MinimalResult:
    equity_curve: tuple[float, ...] = ()
    realized_pnls: tuple[float, ...] = ()
    total_trades: int = 0
    total_return: float = 0.0
    benchmark_return: float = 0.0
    excess_return: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float | None = None
    profit_factor_is_infinite: bool = False
    expectancy: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    starting_equity: float = 100_000.0
    ending_equity: float = 100_000.0
    open_positions: int = 0
    unrealized_pnl: float = 0.0
    open_risk: float = 0.0
    liquidated_at_end: bool = False


def test_csv_mode_enables_simulated_execution(tmp_path: Path, monkeypatch: Any) -> None:
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-01-02T14:30:00Z,AAPL,100,101,99,100,1000\n",
        encoding="utf-8",
    )
    observed: dict[str, Any] = {}

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            observed["trading_enabled"] = config.trading_enabled
            observed["timeframe"] = config.timeframe
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, _bars: Any) -> _MinimalResult:
            return _MinimalResult()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(
        "sys.argv",
        ["run_backtest.py", str(csv_path), "--timeframe", "5m"],
    )

    run_backtest.main()

    assert observed["trading_enabled"] is True
    assert observed["timeframe"] == "5m"


def test_csv_mode_defaults_to_5m_when_timeframe_omitted(tmp_path: Path, monkeypatch: Any) -> None:
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n2026-01-02T14:30:00Z,100,101,99,100,1000\n",
        encoding="utf-8",
    )
    observed: dict[str, Any] = {}

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            observed["timeframe"] = config.timeframe
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, _bars: Any) -> _MinimalResult:
            return _MinimalResult()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr("sys.argv", ["run_backtest.py", str(csv_path), "--symbol", "AAPL"])

    run_backtest.main()

    assert observed["timeframe"] == "5m"


def test_csv_mode_requires_symbol_without_symbol_column(tmp_path: Path, monkeypatch: Any) -> None:
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n2026-01-02T14:30:00Z,100,101,99,100,1000\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["run_backtest.py", str(csv_path)])

    with pytest.raises(SystemExit, match="2"):
        run_backtest.main()


def test_no_args_prints_usage(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr("sys.argv", ["run_backtest.py"])

    exit_code = run_backtest.main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "ParayaBuck Backtest" in captured.out
    assert "--generate-sample" in captured.out


def test_generate_sample_runs_backtest(tmp_path: Path, monkeypatch: Any) -> None:
    observed: dict[str, Any] = {}
    sample_path = tmp_path / "sample.csv"

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            observed["timeframe"] = config.timeframe
            observed["symbols"] = _kwargs["allowed_trade_symbols"]
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, bars: Any) -> _MinimalResult:
            observed["bars"] = bars
            return _MinimalResult()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--generate-sample",
            "--symbol",
            "MES",
            "--bars",
            "100",
            "--sample-output",
            str(sample_path),
            "--output-dir",
            str(tmp_path),
        ],
    )

    run_backtest.main()

    assert sample_path.exists()
    assert observed["timeframe"] == "5m"
    assert observed["symbols"] == {"MES"}
    assert len(observed["bars"]) == 100
    assert (tmp_path / "report.json").exists()


# --------------------------------------------------------------------------
# Sembol çözümleme ve pencere doğrulama
# --------------------------------------------------------------------------


def test_resolve_symbols_splits_mixed_symbols() -> None:
    config = SimpleNamespace(
        universe=SimpleNamespace(
            equity=SimpleNamespace(fallback_trade_symbols=("AAPL", "MSFT")),
            crypto=SimpleNamespace(trade_symbols=("BTC/USD", "ETH/USD")),
        )
    )
    resolved = _resolve_symbols(_args(symbols="AAPL,BTC/USD,eth/usd"), config)
    assert resolved["equity"] == ["AAPL"]
    assert resolved["crypto"] == ["BTC/USD", "ETH/USD"]


def test_resolve_symbols_uses_universe_config() -> None:
    config = SimpleNamespace(
        universe=SimpleNamespace(
            equity=SimpleNamespace(fallback_trade_symbols=("AAPL", "MSFT")),
            crypto=SimpleNamespace(trade_symbols=("BTC/USD", "ETH/USD")),
        )
    )
    resolved = _resolve_symbols(_args(market="equity"), config)
    assert resolved["crypto"] == []
    assert "AAPL" in resolved["equity"]


def test_validate_range_rejects_long_equity_5m_window() -> None:
    end = datetime.now(UTC)
    start = end - timedelta(days=500)
    with pytest.raises(ValueError, match="limited to ~120 days"):
        validate_range(["AAPL", "MSFT"], "5m", start, end)


def test_validate_range_accepts_10_year_daily_window() -> None:
    end = datetime.now(UTC)
    start = end - timedelta(days=3_650)
    validate_range(["AAPL", "BTC/USD"], "1d", start, end)


# --------------------------------------------------------------------------
# Alpaca modu uçtan uca
# --------------------------------------------------------------------------


def test_main_runs_full_pipeline(tmp_path: Path, monkeypatch: Any) -> None:
    observed: dict[str, Any] = {}

    @dataclass
    class Result:
        equity_curve: tuple[float, ...] = (100_000.0, 110_000.0)
        realized_pnls: tuple[float, ...] = (1_000.0, -500.0)
        total_trades: int = 2
        total_return: float = 0.1
        benchmark_return: float = 0.05
        excess_return: float = 0.05
        max_drawdown: float = 0.02
        win_rate: float = 0.5
        profit_factor: float | None = 2.0
        profit_factor_is_infinite: bool = False
        expectancy: float = 250.0
        sharpe: float = 1.2
        sortino: float = 1.0
        starting_equity: float = 100_000.0
        ending_equity: float = 110_000.0
        open_positions: int = 0
        unrealized_pnl: float = 0.0
        open_risk: float = 0.0
        liquidated_at_end: bool = False

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            observed["engine_kwargs"] = _kwargs
            observed["config_crypto_trading_enabled"] = config.crypto_trading_enabled
            observed["config_trading_enabled"] = config.trading_enabled
            observed["config_ai_logging_enabled"] = config.ai_logging_enabled
            observed["timeframe"] = config.timeframe
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, bars: Any) -> Result:
            observed["bars"] = bars
            return Result()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _FakeHistory())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--symbols",
            "AAPL,MSFT,BTC/USD",
            "--days",
            "90",
            "--output-dir",
            str(tmp_path),
        ],
    )

    run_backtest.main()

    # A 90-day window now defaults to the validated live 5m + 1h profile
    # instead of the 1d deep profile, so the backtest matches the live engine.
    assert observed["timeframe"] == "5m"
    assert observed["engine_kwargs"]["benchmark_symbol"] == "SPY"
    assert observed["config_crypto_trading_enabled"] is True
    assert observed["config_ai_logging_enabled"] is False
    assert observed["config_trading_enabled"] is True
    assert len(observed["bars"]) > 0
    report = (tmp_path / "report.json").read_text(encoding="utf-8")
    assert "summary" in report
    assert "trades" in report
    assert (tmp_path / "summary.csv").exists()


def test_main_crypto_only_uses_crypto_benchmark(tmp_path: Path, monkeypatch: Any) -> None:
    observed: dict[str, Any] = {}

    @dataclass
    class Result:
        equity_curve: tuple[float, ...] = ()
        realized_pnls: tuple[float, ...] = ()
        total_trades: int = 0
        total_return: float = 0.0
        benchmark_return: float = 0.0
        excess_return: float = 0.0
        max_drawdown: float = 0.0
        win_rate: float = 0.0
        profit_factor: float | None = None
        profit_factor_is_infinite: bool = False
        expectancy: float = 0.0
        sharpe: float = 0.0
        sortino: float = 0.0
        starting_equity: float = 100_000.0
        ending_equity: float = 100_000.0
        open_positions: int = 0
        unrealized_pnl: float = 0.0
        open_risk: float = 0.0
        liquidated_at_end: bool = False

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            observed["engine_kwargs"] = _kwargs
            observed["config_crypto_trading_enabled"] = config.crypto_trading_enabled
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, bars: Any) -> Result:
            return Result()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _FakeHistory())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--market",
            "crypto",
            "--start",
            "2024-01-01",
            "--output-dir",
            str(tmp_path),
        ],
    )

    run_backtest.main()

    assert observed["engine_kwargs"]["benchmark_symbol"] == "BTC/USD"
    assert observed["config_crypto_trading_enabled"] is True


def test_main_sweep_writes_sweep_csv(tmp_path: Path, monkeypatch: Any) -> None:
    @dataclass
    class Result:
        equity_curve: tuple[float, ...] = (100_000.0, 110_000.0)
        realized_pnls: tuple[float, ...] = (500.0, 300.0)
        total_trades: int = 5
        total_return: float = 0.1
        benchmark_return: float = 0.05
        excess_return: float = 0.05
        max_drawdown: float = 0.02
        win_rate: float = 0.6
        profit_factor: float | None = 2.0
        profit_factor_is_infinite: bool = False
        expectancy: float = 160.0
        sharpe: float = 1.2
        sortino: float = 1.0
        starting_equity: float = 100_000.0
        ending_equity: float = 110_000.0
        open_positions: int = 0
        unrealized_pnl: float = 0.0
        open_risk: float = 0.0
        liquidated_at_end: bool = False
        cagr: float = 0.1

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            self.config = config
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, bars: Any) -> Result:
            return Result()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _FakeHistory())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--symbols",
            "AAPL,BTC/USD",
            "--days",
            "90",
            "--sweep",
            "--sweep-scores",
            "60,70",
            "--sweep-risks",
            "0.005",
            "--sweep-positions",
            "0.10",
            "--sweep-min-trades",
            "1",
            "--output-dir",
            str(tmp_path),
        ],
    )

    run_backtest.main()

    sweep_csv = tmp_path / "sweep.csv"
    assert sweep_csv.exists()
    lines = sweep_csv.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # header + 2 combinations
    assert "minimum_signal_score" in lines[0]


def test_main_deep_profile_for_multi_year_window(tmp_path: Path, monkeypatch: Any) -> None:
    observed: dict[str, Any] = {}

    @dataclass
    class Result:
        equity_curve: tuple[float, ...] = ()
        realized_pnls: tuple[float, ...] = ()
        total_trades: int = 0
        total_return: float = 0.0
        benchmark_return: float = 0.0
        excess_return: float = 0.0
        max_drawdown: float = 0.0
        win_rate: float = 0.0
        profit_factor: float | None = None
        profit_factor_is_infinite: bool = False
        expectancy: float = 0.0
        sharpe: float = 0.0
        sortino: float = 0.0
        starting_equity: float = 100_000.0
        ending_equity: float = 100_000.0
        open_positions: int = 0
        unrealized_pnl: float = 0.0
        open_risk: float = 0.0
        liquidated_at_end: bool = False

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            observed["timeframe"] = config.timeframe
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, bars: Any) -> Result:
            return Result()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _FakeHistory())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--symbols",
            "AAPL",
            "--years",
            "10",
            "--output-dir",
            str(tmp_path),
        ],
    )

    run_backtest.main()

    # Multi-year windows cannot use the live 5m profile, so the default is 1d.
    assert observed["timeframe"] == "1d"


def test_main_explicit_live_profile_forces_5m(tmp_path: Path, monkeypatch: Any) -> None:
    observed: dict[str, Any] = {}

    @dataclass
    class Result:
        equity_curve: tuple[float, ...] = ()
        realized_pnls: tuple[float, ...] = ()
        total_trades: int = 0
        total_return: float = 0.0
        benchmark_return: float = 0.0
        excess_return: float = 0.0
        max_drawdown: float = 0.0
        win_rate: float = 0.0
        profit_factor: float | None = None
        profit_factor_is_infinite: bool = False
        expectancy: float = 0.0
        sharpe: float = 0.0
        sortino: float = 0.0
        starting_equity: float = 100_000.0
        ending_equity: float = 100_000.0
        open_positions: int = 0
        unrealized_pnl: float = 0.0
        open_risk: float = 0.0
        liquidated_at_end: bool = False

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            observed["timeframe"] = config.timeframe
            observed["higher_timeframe"] = config.higher_timeframe
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, bars: Any) -> Result:
            return Result()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _FakeHistory())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--symbols",
            "AAPL",
            "--days",
            "90",
            "--profile",
            "live",
            "--output-dir",
            str(tmp_path),
        ],
    )

    run_backtest.main()

    assert observed["timeframe"] == "5m"
    assert observed["higher_timeframe"] == "1h"


def test_main_writes_run_manifest(tmp_path: Path, monkeypatch: Any) -> None:
    @dataclass
    class Result:
        equity_curve: tuple[float, ...] = (100_000.0, 110_000.0)
        realized_pnls: tuple[float, ...] = (500.0,)
        total_trades: int = 1
        total_return: float = 0.1
        benchmark_return: float = 0.05
        excess_return: float = 0.05
        max_drawdown: float = 0.02
        win_rate: float = 1.0
        profit_factor: float | None = None
        profit_factor_is_infinite: bool = True
        expectancy: float = 500.0
        sharpe: float = 1.2
        sortino: float = 1.0
        starting_equity: float = 100_000.0
        ending_equity: float = 110_000.0
        open_positions: int = 0
        unrealized_pnl: float = 0.0
        open_risk: float = 0.0
        liquidated_at_end: bool = False
        cagr: float = 0.1
        benchmark_symbol: str = "SPY"

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, bars: Any) -> Result:
            return Result()

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _FakeHistory())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--symbols",
            "AAPL",
            "--days",
            "90",
            "--output-dir",
            str(tmp_path),
        ],
    )

    run_backtest.main()

    manifest_path = tmp_path / "run_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["timeframe"] == "5m"
    assert manifest["benchmark_symbol"] == "SPY"
    assert manifest["summary"]["total_return"] == 0.1
