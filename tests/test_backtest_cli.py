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
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES


def _bar_at(timestamp: datetime, symbol: str, timeframe: str = "1d") -> Any:
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
        "min_coverage_pct": 50.0,
        "max_gap_multiplier": 12.0,
        "allow_incomplete_data": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeHistory:
    """Returns full, realistic coverage for any requested window."""

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str = "splits",
    ) -> list[Any]:
        del adjustment
        minutes = TIMEFRAME_MINUTES[timeframe]
        step = timedelta(minutes=minutes)
        total = int((end - start).total_seconds() // 60 // minutes) + 1
        count = min(total, 10_000)
        return [_bar_at(start + step * index, symbol, timeframe) for index in range(count)]


class _ThinHistory(_FakeHistory):
    """Returns a tiny fraction of the window — coverage far below threshold."""

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str = "splits",
    ) -> list[Any]:
        del adjustment
        return [
            _bar_at(start + timedelta(minutes=5 * index), symbol, timeframe) for index in range(5)
        ]


class _GappedHistory(_FakeHistory):
    """Returns daily bars with a 20-day hole in the middle of the window."""

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str = "splits",
    ) -> list[Any]:
        del adjustment
        bars: list[Any] = []
        timestamp = start
        while timestamp < start + timedelta(days=5):
            bars.append(_bar_at(timestamp, symbol, timeframe))
            timestamp += timedelta(days=1)
        timestamp = start + timedelta(days=25)
        while timestamp < end:
            bars.append(_bar_at(timestamp, symbol, timeframe))
            timestamp += timedelta(days=1)
        return bars


class _LateHistory(_FakeHistory):
    """Session-consistent bars starting 50 days into a 90-day window.

    Only the regular session (weekdays 13:30-19:59 UTC, 5m) is generated, so
    the coverage ratio is proportional to the late start: ~44% of the window,
    below the default 50% threshold.
    """

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str = "splits",
    ) -> list[Any]:
        del adjustment
        minutes = TIMEFRAME_MINUTES[timeframe]
        bars: list[Any] = []
        cursor = start + timedelta(days=50)
        while cursor < end:
            if (
                cursor.weekday() < 5
                and cursor.hour >= 13
                and cursor.hour <= 19
                and not (cursor.hour == 13 and cursor.minute < 30)
            ):
                bars.append(_bar_at(cursor, symbol, timeframe))
            cursor += timedelta(minutes=minutes)
        return bars


class _MixedHistory(_FakeHistory):
    """Full coverage for AAPL, no data at all for EMPTY."""

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str = "splits",
    ) -> list[Any]:
        if symbol == "EMPTY":
            return []
        return super().get_bars(symbol, timeframe, start, end, adjustment=adjustment)


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
        ["run_backtest.py", str(csv_path), "--timeframe", "5m", "--output-dir", str(tmp_path)],
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
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            str(csv_path),
            "--symbol",
            "AAPL",
            "--output-dir",
            str(tmp_path),
        ],
    )

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
            equity=SimpleNamespace(
                dynamic_selection_enabled=False,
                reference_symbols=("SPY",),
                trade_reference_symbols=False,
                fallback_trade_symbols=("AAPL", "MSFT"),
                maximum_stream_symbols=30,
            ),
            crypto=SimpleNamespace(
                dynamic_selection_enabled=False,
                pinned_symbols=("BTC/USD",),
                trade_symbols=("BTC/USD", "ETH/USD"),
                maximum_stream_symbols=12,
            ),
        )
    )
    resolved = _resolve_symbols(_args(market="equity"), config)
    assert resolved["crypto"] == []
    assert "AAPL" in resolved["equity"]
    # Reference-only symbols (SPY) are not part of the trade universe.
    assert "SPY" not in resolved["equity"]


def test_resolve_symbols_uses_live_selected_universe(monkeypatch: Any) -> None:
    config = SimpleNamespace(
        universe=SimpleNamespace(
            equity=SimpleNamespace(
                dynamic_selection_enabled=True,
                reference_symbols=("SPY",),
                trade_reference_symbols=False,
                fallback_trade_symbols=("AAPL",),
                maximum_stream_symbols=30,
            ),
            crypto=SimpleNamespace(
                dynamic_selection_enabled=True,
                pinned_symbols=("BTC/USD",),
                trade_symbols=("BTC/USD", "ETH/USD"),
                maximum_stream_symbols=12,
            ),
        )
    )
    monkeypatch.setattr(
        run_backtest,
        "load_active_symbols",
        lambda _config, log_rejection=True: ("SPY", "AAPL", "MSFT", "GOOGL"),
    )
    monkeypatch.setattr(
        run_backtest,
        "load_active_crypto_symbols",
        lambda _config, log_rejection=True: ("BTC/USD", "ETH/USD", "SOL/USD"),
    )
    resolved = _resolve_symbols(_args(), config)
    # The validated live selection is used; SPY stays benchmark-only.
    assert resolved["equity"] == ["AAPL", "MSFT", "GOOGL"]
    assert resolved["crypto"] == ["BTC/USD", "ETH/USD", "SOL/USD"]


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
        run_backtest,
        "ensure_active_universes",
        lambda _config, _key, _secret: SimpleNamespace(
            equity_refreshed=False, crypto_refreshed=False
        ),
    )
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
    # BTC/USD comes from the configured trade universe, so being the benchmark
    # must not exclude it from trading — the live BTC strategy stays tested.
    allowed = observed["engine_kwargs"]["allowed_trade_symbols"]
    assert "BTC/USD" in allowed


def test_main_runs_universe_preflight(tmp_path: Path, monkeypatch: Any) -> None:
    called: dict[str, Any] = {}

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

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, bars: Any) -> Result:
            return Result()

    def fake_preflight(config: Any, api_key: str, secret_key: str) -> Any:
        del config, secret_key
        called["api_key"] = api_key
        return SimpleNamespace(equity_refreshed=False, crypto_refreshed=False)

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _FakeHistory())
    monkeypatch.setattr(run_backtest, "ensure_active_universes", fake_preflight)
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

    # Default universe resolution must refresh stale scanner output first,
    # exactly like the live runtime does before building subscriptions.
    assert called["api_key"] is not None


def test_main_fails_fast_on_low_coverage(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _ThinHistory())
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

    with pytest.raises(SystemExit, match="Veri kalitesi"):
        run_backtest.main()

    assert not (tmp_path / "report.json").exists()


def test_main_fails_fast_on_large_gap(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _GappedHistory())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--symbols",
            "AAPL",
            "--days",
            "90",
            "--timeframe",
            "1d",
            "--output-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit, match="Veri kalitesi"):
        run_backtest.main()

    assert not (tmp_path / "report.json").exists()


def test_main_fails_fast_on_empty_symbol(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _MixedHistory())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--symbols",
            "AAPL,EMPTY",
            "--days",
            "90",
            "--output-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit, match="Veri kalitesi"):
        run_backtest.main()

    assert not (tmp_path / "report.json").exists()


def test_main_late_starting_data_fails_by_default(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: _LateHistory())
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

    # A broken cache / partial fetch looks like a late listing; without an
    # explicit opt-in it must fail rather than silently pass validation.
    with pytest.raises(SystemExit, match="Veri kalitesi"):
        run_backtest.main()

    assert not (tmp_path / "report.json").exists()


def test_main_allow_incomplete_data_exempts_late_and_empty(
    tmp_path: Path, monkeypatch: Any
) -> None:
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

    class FakeBacktestEngine:
        ai_journal = None

        def __init__(self, config: Any, **_kwargs: Any) -> None:
            self.curve_timestamps: list[datetime] = []
            self.broker = SimpleNamespace(trades=())

        def run(self, bars: Any) -> Result:
            return Result()

    class LateAndEmptyHistory:
        """Late-starting data for AAPL, no data at all for EMPTY."""

        def get_bars(
            self,
            symbol: str,
            timeframe: str,
            start: datetime,
            end: datetime,
            adjustment: str = "splits",
        ) -> list[Any]:
            del adjustment
            if symbol == "EMPTY":
                return []
            minutes = TIMEFRAME_MINUTES[timeframe]
            late_start = start + timedelta(days=50)
            total = int((end - late_start).total_seconds() // 60 // minutes) + 1
            return [
                _bar_at(late_start + timedelta(minutes=minutes) * index, symbol, timeframe)
                for index in range(total)
            ]

    monkeypatch.setattr(run_backtest, "BacktestEngine", FakeBacktestEngine)
    monkeypatch.setattr(run_backtest, "AlpacaHistoricalData", lambda *a, **k: LateAndEmptyHistory())
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--symbols",
            "AAPL,EMPTY",
            "--days",
            "90",
            "--allow-incomplete-data",
            "--output-dir",
            str(tmp_path),
        ],
    )

    run_backtest.main()  # explicit opt-in: late data and empties are tolerated

    assert (tmp_path / "report.json").exists()


def test_main_passes_data_quality_gate_with_complete_data(tmp_path: Path, monkeypatch: Any) -> None:
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

    run_backtest.main()  # complete fake coverage must pass the gate

    assert (tmp_path / "report.json").exists()


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
