from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import parayabuck.cli as cli_module
from parayabuck.backtest.product import optimize_grid, ranking_score, run_product_backtest
from parayabuck.config import Config, StrategyConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType


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
                    close * 1.006,
                    close * 0.994,
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
    assert payload["combination_count"] == 2
    assert len(list((tmp_path / "reports").glob("*/report.json"))) == 1


@pytest.mark.parametrize(
    ("metric", "expected"),
    (("sharpe", 1.0), ("sortino", 2.0), ("calmar", 3.0)),
)
def test_optimizer_ranking_metric_is_honored(metric: str, expected: float) -> None:
    result = SimpleNamespace(
        sharpe=1.0,
        sortino=2.0,
        calmar=3.0,
        max_drawdown=0.1,
        closed_position_events=tuple(range(5)),
    )

    assert ranking_score(result, metric) == expected


def test_optimizer_rejects_overlapping_train_and_oos_windows(tmp_path) -> None:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="must not overlap"):
        optimize_grid(
            _config(tmp_path),
            _bars(),
            market=Market.EQUITY,
            grid={"momentum_window": [20]},
            train_start=start,
            train_end=start + timedelta(days=300),
            oos_start=start + timedelta(days=200),
            oos_end=start + timedelta(days=400),
        )

    with pytest.raises(ValueError, match="explicit TRAIN"):
        optimize_grid(
            _config(tmp_path),
            _bars(),
            market=Market.EQUITY,
            grid={"momentum_window": [20]},
            oos_start=start + timedelta(days=300),
            oos_end=start + timedelta(days=400),
        )


def test_optimizer_uses_configured_metric_for_full_trial_ordering(tmp_path) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        backtest=replace(config.backtest, ranking_metric="sharpe"),
    )
    payload = optimize_grid(
        config,
        _bars(280),
        market=Market.EQUITY,
        grid={"momentum_window": [20, 30]},
    )
    training = payload["training_results"]

    assert payload["ranking"] == "sharpe"
    assert all(item["score"] == item["sharpe"] for item in training)
    assert training[0]["sharpe"] >= training[1]["sharpe"]


def test_optimizer_rejects_excessive_grid_before_running_trials(tmp_path) -> None:
    with pytest.raises(ValueError, match="maximum is 256"):
        optimize_grid(
            _config(tmp_path),
            _bars(),
            market=Market.EQUITY,
            grid={"a": list(range(17)), "b": list(range(17))},
        )


def test_backtest_cli_runs_offline_csv_end_to_end(tmp_path, monkeypatch, capsys) -> None:
    bars = [bar for bar in _bars(220) if bar.timestamp.weekday() < 5]
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


def test_equity_product_passes_regulatory_fees_to_execution_engine(tmp_path) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        backtest=replace(
            config.backtest,
            equity_taf_per_share=0.0002,
            equity_taf_cap=10.0,
            equity_cat_per_share=0.00003,
            equity_sec_fee_rate=0.00002,
        ),
    )

    run = run_product_backtest(config, _bars(), market=Market.EQUITY)

    assert run.result.fee_total > 0


def test_product_backtest_credits_explicit_dividend_once(tmp_path) -> None:
    config = _config(tmp_path)
    bars = _bars()
    action = CorporateAction(
        datetime(2023, 10, 28, tzinfo=UTC),
        "FFF",
        CorporateActionType.DIVIDEND,
        1.0,
    )

    run = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        corporate_actions=[action],
    )

    assert run.result.dividend_total > 0
    assert "explicit dividend cash flows" in run.report.summary["price_adjustment_model"]


def test_equity_csv_rejects_weekend_execution_bars(tmp_path) -> None:
    csv_path = tmp_path / "weekend.csv"
    csv_path.write_text(
        "timestamp,symbol,timeframe,open,high,low,close,volume,market\n"
        "2025-01-04T00:00:00+00:00,SPY,1d,100,101,99,100,1000,equity\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="weekend"):
        cli_module.load_csv_bars(csv_path, default_market=Market.EQUITY)


def test_download_candidates_use_quality_shortlist_not_alphabetical_asset_list(tmp_path) -> None:
    config = _config(tmp_path)
    candidate_path = tmp_path / "equity-candidates.json"
    candidate_path.write_text(
        json.dumps(
            {
                "symbols": ["AAA", "BBB", "ZZZ"],
                "shortlist_symbols": ["ZZZ"],
            }
        ),
        encoding="utf-8",
    )
    config = replace(
        config,
        universe=replace(
            config.universe,
            equity=replace(
                config.universe.equity,
                candidate_universe_path=str(candidate_path),
            ),
        ),
    )

    assert cli_module._candidate_symbols(config, Market.EQUITY, None, 1) == ["SPY", "ZZZ"]
