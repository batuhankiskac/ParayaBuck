"""Golden VT35 product backtest on a small committed real-data fixture.

``tests/fixtures/vt35_golden_bars.csv`` is the QQQ/QLD/SPY 2019-01-02 ..
2022-12-30 subset of the committed ``data/historical/stress_robustness_v1_daily.csv``
(split-adjusted OHLC rounded to 4 decimals, naive New York session dates).
It replaces ``benchmarks/parity.py``, whose dataset was pruned: any change to
the strategy, risk overlay, fill or cost model that moves a VT35 trade moves
these numbers. Update them only for an intended behaviour change.

The shipped config selects the E2 trend vote (``docs/research/e2_integration_v1.md``);
the VT35 golden pins the ``trend_rule: sma`` rule, unchanged since before E2,
and the E2 golden pins the shipped rule on the same fixture.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from parayabuck.backtest.product import load_csv_bars, run_product_backtest
from parayabuck.config import load_config
from parayabuck.core.models import Market

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "vt35_golden_bars.csv"


def _config(tmp_path, *, vt35: bool):
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    equity = config.strategy.equity
    if vt35:
        majority_only = {"tsm_bars", "donchian_bars"}
        parameters = {key: value for key, value in equity.parameters.items() if key not in majority_only}
        parameters["trend_rule"] = "sma"
        equity = replace(equity, parameters=parameters)
    return replace(
        config,
        backtest=replace(config.backtest, output_dir=str(tmp_path)),
        strategy=replace(config.strategy, equity=equity),
    )


def _fill_digest(fills) -> str:
    digest = hashlib.sha256()
    for fill in fills:
        digest.update(
            f"{fill.timestamp.date()}|{fill.symbol}|{fill.side.value}|{fill.qty:.4f}|{fill.fill_price:.4f}\n".encode()
        )
    return digest.hexdigest()


def test_vt35_golden_backtest_is_unchanged(tmp_path) -> None:
    config = _config(tmp_path, vt35=True)
    bars = load_csv_bars(FIXTURE, default_market=Market.EQUITY)

    run = run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=False)
    result = run.result

    # No --start: the 200-session SMA warmup is excluded from evaluation.
    assert result.start_date == "2019-10-16T00:00:00-04:00"
    assert result.end_date == "2022-12-30T00:00:00-05:00"
    assert len(result.rebalances) == 809
    assert len(result.fills) == 228
    assert {fill.symbol for fill in result.fills} == {"QLD"}
    assert result.ending_equity == pytest.approx(210_284.95, abs=0.01)
    assert result.cagr == pytest.approx(0.260721, abs=1e-6)
    assert result.max_drawdown == pytest.approx(0.277902, abs=1e-6)
    assert result.benchmark_return == pytest.approx(0.281602, abs=1e-6)
    assert _fill_digest(result.fills) == "9d2bc7c934b9f27ebaf1538875fc79298133b49c89ec94d1088c493cfa23e1c4"


def test_e2_golden_backtest_is_unchanged(tmp_path) -> None:
    config = _config(tmp_path, vt35=False)
    assert config.strategy.equity.parameters["trend_rule"] == "majority_sma_tsm_don"
    bars = load_csv_bars(FIXTURE, default_market=Market.EQUITY)

    run = run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=False)
    result = run.result

    # No --start: the 253-session warmup (252-session momentum plus its base
    # close) is excluded, so evaluation opens on the fixture's 253rd session.
    assert result.start_date == "2020-01-02T00:00:00-05:00"
    assert result.end_date == "2022-12-30T00:00:00-05:00"
    assert len(result.rebalances) == 756
    assert len(result.fills) == 230
    assert {fill.symbol for fill in result.fills} == {"QLD"}
    assert result.ending_equity == pytest.approx(174_227.06, abs=0.01)
    assert result.cagr == pytest.approx(0.203702, abs=1e-6)
    assert result.max_drawdown == pytest.approx(0.278088, abs=1e-6)
    assert result.benchmark_return == pytest.approx(0.177179, abs=1e-6)
    assert _fill_digest(result.fills) == "f5a2fffd6b6bdd3dcae3fd812a5f7c9286fa4d0760036cd3f3a91061389b4362"
