from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import parayabuck.cli as cli_module
from parayabuck.backtest.product import optimize_grid, run_product_backtest
from parayabuck.config import Config, StrategyConfig
from parayabuck.core.models import Bar, Market


def _bars(days: int = 420) -> list[Bar]:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    symbols = ("SPY", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "LOSER")
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            drift = -0.0005 if symbol == "LOSER" else 0.0004 + offset * 0.00002
            close = 100 * (1 + drift) ** index
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close * 0.999,
                    close * 1.002,
                    close * 0.998,
                    close,
                    2_000_000,
                    Market.EQUITY,
                )
            )
    return bars


def _config(tmp_path) -> Config:
    base = Config()
    parameters = {
        **base.strategy.equity.parameters,
        "trend_window": 60,
        "momentum_window": 30,
        "volatility_window": 15,
        "top_n": 4,
    }
    return replace(
        base,
        runtime=replace(base.runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")),
        backtest=replace(base.backtest, output_dir=str(tmp_path / "reports")),
        strategy=StrategyConfig(
            equity=replace(base.strategy.equity, parameters=parameters),
            crypto=base.strategy.crypto,
        ),
    )


def test_product_backtest_uses_production_strategy_risk_and_next_open_fills(tmp_path) -> None:
    config = _config(tmp_path)
    run = run_product_backtest(config, _bars(), market=Market.EQUITY)
    assert run.result.fills
    assert all(fill.timestamp > fill.decision_timestamp for fill in run.result.fills)
    assert run.report.summary["fill_model"] == "decision on closed bar; next tradable bar open"
    assert run.report.summary["maximum_exposure"] <= config.strategy.equity.maximum_allocation
    assert (tmp_path / "reports" / run.run_id / "drawdowns.csv").exists()
    assert (tmp_path / "reports" / run.run_id / "universe.json").exists()
    report = json.loads((tmp_path / "reports" / run.run_id / "report.json").read_text())
    assert "survivorship_bias_limitation" in report["summary"]


def test_grid_optimizer_ranks_train_and_evaluates_oos_once(tmp_path) -> None:
    config = _config(tmp_path)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    payload = optimize_grid(
        config,
        _bars(),
        market=Market.EQUITY,
        grid={"momentum_window": [20, 30]},
        train_start=start + timedelta(days=100),
        train_end=start + timedelta(days=299),
        oos_start=start + timedelta(days=300),
        oos_end=start + timedelta(days=419),
    )
    training = payload["training_results"]
    assert len(training) == 2
    assert training[0]["score"] >= training[1]["score"]
    assert payload["out_of_sample"]["summary"]["start_date"].startswith("2023")


def test_backtest_cli_runs_offline_csv_end_to_end(tmp_path, monkeypatch, capsys) -> None:
    bars = _bars(150)
    csv_path = tmp_path / "bars.csv"
    rows = ["timestamp,symbol,timeframe,open,high,low,close,volume,market"]
    rows.extend(
        ",".join(
            (
                bar.timestamp.isoformat(),
                bar.symbol,
                bar.timeframe,
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
                bar.market.value,
            )
        )
        for bar in bars
    )
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    config = _config(tmp_path)
    monkeypatch.setattr(cli_module, "load_config", lambda _path: config)

    exit_code = cli_module.main(
        [
            "backtest",
            "--market",
            "equity",
            "--data",
            str(csv_path),
            "--output-dir",
            str(tmp_path / "cli-reports"),
        ]
    )

    assert exit_code == 0
    assert "equity_dynamic_trend_1d_v1" in capsys.readouterr().out
    assert list((tmp_path / "cli-reports").glob("*/report.json"))
