"""Golden VT35 product backtest on a small committed real-data fixture.

``tests/fixtures/vt35_golden_bars.csv`` is the QQQ/QLD/SPY 2019-01-02 ..
2022-12-30 subset of the committed ``data/historical/stress_robustness_v1_daily.csv``
(split-adjusted OHLC rounded to 4 decimals, naive New York session dates).
It replaces ``benchmarks/parity.py``, whose dataset was pruned: any change to
the strategy, risk overlay, fill or cost model that moves a VT35 trade moves
these numbers. Update them only for an intended behaviour change.
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


def test_vt35_golden_backtest_is_unchanged(tmp_path) -> None:
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    config = replace(config, backtest=replace(config.backtest, output_dir=str(tmp_path)))
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
    digest = hashlib.sha256()
    for fill in result.fills:
        digest.update(
            f"{fill.timestamp.date()}|{fill.symbol}|{fill.side.value}|{fill.qty:.4f}|{fill.fill_price:.4f}\n".encode()
        )
    assert digest.hexdigest() == "9d2bc7c934b9f27ebaf1538875fc79298133b49c89ec94d1088c493cfa23e1c4"
