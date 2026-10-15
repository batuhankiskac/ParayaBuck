"""Golden VT35 product backtest on a small committed real-data fixture.

``tests/fixtures/vt35_golden_bars.csv`` is the QQQ/QLD/SPY 2019-01-02 ..
2022-12-30 subset of the committed ``data/historical/stress_robustness_v1_daily.csv``
(split-adjusted OHLC rounded to 4 decimals, naive New York session dates).
It replaces ``benchmarks/parity.py``, whose dataset was pruned: any change to
the strategy, risk overlay, fill or cost model that moves a VT35 trade moves
these numbers. Update them only for an intended behaviour change.

Each identity is pinned twice: with the shipped execution policy's
``downsize_gain_band`` (A1u) disabled, which reproduces the pre-A1u numbers
exactly — the engine/fill/cost fingerprint — and with the shipped schedule, so
the band's effect on the same fixture is pinned too
(``docs/research/gain_band_integration_v1.md``).

The shipped config selects the E2 trend vote plus the R2 early re-entry path
(``docs/research/e2_integration_v1.md``, ``docs/research/fast_reentry_integration_v1.md``);
the VT35 golden pins the ``trend_rule: sma`` rule, unchanged since before E2,
and the E2 golden pins the shipped rule on the same fixture.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from parayabuck.backtest.product import load_csv_bars, run_product_backtest
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.portfolio.equity_leveraged_trend import MAJORITY_ONLY_PARAMETERS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "vt35_golden_bars.csv"


@dataclass(frozen=True)
class _Golden:
    """One pinned product-backtest fingerprint of the committed fixture."""

    vt35: bool
    e2_only: bool
    gain_band: bool
    start_date: str
    end_date: str
    rebalances: int
    fills: int
    ending_equity: float
    cagr: float
    max_drawdown: float
    benchmark_return: float
    fill_digest: str
    # Downsizes the gain band held across the run (0 when it is disabled).
    suppressed: int = 0


_VT35 = _Golden(
    vt35=True,
    e2_only=False,
    gain_band=False,
    # No --start: the 200-session SMA warmup is excluded from evaluation.
    start_date="2019-10-16T00:00:00-04:00",
    end_date="2022-12-30T00:00:00-05:00",
    rebalances=809,
    fills=228,
    ending_equity=210_284.95,
    cagr=0.260721,
    max_drawdown=0.277902,
    benchmark_return=0.281602,
    fill_digest="9d2bc7c934b9f27ebaf1538875fc79298133b49c89ec94d1088c493cfa23e1c4",
)

_VT35_WITH_BAND = replace(
    _VT35,
    gain_band=True,
    fills=122,
    ending_equity=210_744.83,
    cagr=0.26158,
    max_drawdown=0.28264,
    suppressed=149,
    fill_digest="1aa3ecc766c806cf555651eca7b19050ba330ad622933e08d9dced9bfb7c436e",
)

_E2_ONLY = _Golden(
    vt35=False,
    e2_only=True,
    gain_band=False,
    start_date="2020-01-02T00:00:00-05:00",
    end_date="2022-12-30T00:00:00-05:00",
    rebalances=756,
    fills=230,
    ending_equity=174_227.06,
    cagr=0.203702,
    max_drawdown=0.278088,
    benchmark_return=0.177179,
    fill_digest="f5a2fffd6b6bdd3dcae3fd812a5f7c9286fa4d0760036cd3f3a91061389b4362",
)

_E2_ONLY_WITH_BAND = replace(
    _E2_ONLY,
    gain_band=True,
    fills=126,
    ending_equity=175_653.46,
    cagr=0.206984,
    max_drawdown=0.278136,
    suppressed=134,
    fill_digest="63bd0ba807f91d70ff90be5b44dae56187ef3a9914a883bee19ee197712a3ed1",
)

_E2 = _Golden(
    vt35=False,
    e2_only=False,
    gain_band=False,
    # No --start: the 397-session warmup (the R2 replay's first 252-session vol
    # median plus its 126-session window) is excluded, so evaluation opens on
    # the fixture's 397th session.
    start_date="2020-07-29T00:00:00-04:00",
    end_date="2022-12-30T00:00:00-05:00",
    rebalances=612,
    fills=187,
    ending_equity=149_267.49,
    cagr=0.179857,
    max_drawdown=0.253766,
    benchmark_return=0.176273,
    fill_digest="973ae38617bfc0c6a0204e5a4bce7e8f0d3c6de78ca0a5d419ffef35c04edef1",
)

_E2_WITH_BAND = replace(
    _E2,
    gain_band=True,
    fills=93,
    ending_equity=148_552.15,
    cagr=0.17752,
    max_drawdown=0.255391,
    suppressed=124,
    fill_digest="ec9667ae421f07f7a93f90dfefae379551abddddcf0a29031c4a25fa594ea978",
)


def _config(tmp_path, *, vt35: bool, e2_only: bool = False, gain_band: bool):
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    equity = config.strategy.equity
    if e2_only:
        parameters = {key: value for key, value in equity.parameters.items() if key != "early_reentry_weight"}
        equity = replace(equity, parameters=parameters)
    if vt35:
        parameters = {key: value for key, value in equity.parameters.items() if key not in MAJORITY_ONLY_PARAMETERS}
        parameters["trend_rule"] = "sma"
        equity = replace(equity, parameters=parameters)
    execution = config.execution if gain_band else replace(config.execution, downsize_gain_band=None)
    return replace(
        config,
        backtest=replace(config.backtest, output_dir=str(tmp_path)),
        strategy=replace(config.strategy, equity=equity),
        execution=execution,
    )


def _fill_digest(fills) -> str:
    digest = hashlib.sha256()
    for fill in fills:
        digest.update(
            f"{fill.timestamp.date()}|{fill.symbol}|{fill.side.value}|{fill.qty:.4f}|{fill.fill_price:.4f}\n".encode()
        )
    return digest.hexdigest()


def _assert_golden(tmp_path, golden: _Golden) -> None:
    if golden.gain_band:
        assert load_config(str(ROOT / "config" / "settings.yaml")).execution.downsize_gain_band is not None
    config = _config(tmp_path, vt35=golden.vt35, e2_only=golden.e2_only, gain_band=golden.gain_band)
    bars = load_csv_bars(FIXTURE, default_market=Market.EQUITY)

    run = run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=False)
    result = run.result

    assert result.start_date == golden.start_date
    assert result.end_date == golden.end_date
    assert len(result.rebalances) == golden.rebalances
    assert len(result.fills) == golden.fills
    assert {fill.symbol for fill in result.fills} == {"QLD"}
    assert result.ending_equity == pytest.approx(golden.ending_equity, abs=0.01)
    assert result.cagr == pytest.approx(golden.cagr, abs=1e-6)
    assert result.max_drawdown == pytest.approx(golden.max_drawdown, abs=1e-6)
    assert result.benchmark_return == pytest.approx(golden.benchmark_return, abs=1e-6)
    assert _fill_digest(result.fills) == golden.fill_digest
    suppressed = sum(len(record.gain_band_holds) for record in result.rebalances)
    assert suppressed == golden.suppressed
    if not golden.gain_band:
        assert suppressed == 0


def test_vt35_golden_backtest_is_unchanged(tmp_path) -> None:
    """VT35 (``trend_rule: sma``) with the gain band off: the pre-A1u numbers."""
    _assert_golden(tmp_path, _VT35)


def test_vt35_with_the_gain_band_golden_backtest_is_unchanged(tmp_path) -> None:
    """The shipped execution policy on VT35: the band suppresses QLD downsizes."""
    _assert_golden(tmp_path, _VT35_WITH_BAND)


def test_e2_without_the_early_path_golden_backtest_is_unchanged(tmp_path) -> None:
    """E2 1.1.0 (the rule before R2) with the gain band off keeps its pre-A1u
    golden numbers exactly."""
    config = _config(tmp_path, vt35=False, e2_only=True, gain_band=False)
    assert config.strategy.equity.parameters["trend_rule"] == "majority_sma_tsm_don"
    assert "early_reentry_weight" not in config.strategy.equity.parameters
    _assert_golden(tmp_path, _E2_ONLY)


def test_e2_without_the_early_path_with_the_gain_band_golden_backtest_is_unchanged(tmp_path) -> None:
    """The shipped execution policy on E2 1.1.0."""
    _assert_golden(tmp_path, _E2_ONLY_WITH_BAND)


def test_e2_golden_backtest_is_unchanged(tmp_path) -> None:
    """The shipped rule with the gain band off: the R2 numbers as shipped
    before A1u, reproduced exactly on the same fixture."""
    config = _config(tmp_path, vt35=False, gain_band=False)
    assert config.strategy.equity.parameters["trend_rule"] == "majority_sma_tsm_don"
    assert config.strategy.equity.parameters["early_reentry_weight"] == 0.5
    _assert_golden(tmp_path, _E2)


def test_e2_with_the_gain_band_golden_backtest_is_unchanged(tmp_path) -> None:
    """The shipped identity: E2 + R2 + A1u on the committed fixture."""
    _assert_golden(tmp_path, _E2_WITH_BAND)
