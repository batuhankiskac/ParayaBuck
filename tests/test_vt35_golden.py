"""Golden VT35 product backtest on a small committed real-data fixture.

``tests/fixtures/vt35_golden_bars.csv`` is the QQQ/QLD/SPY 2019-01-02 ..
2022-12-30 subset of the committed ``data/historical/stress_robustness_v1_daily.csv``
(split-adjusted OHLC rounded to 4 decimals, naive New York session dates).
It replaces ``benchmarks/parity.py``, whose dataset was pruned: any change to
the strategy, risk overlay, fill or cost model that moves a VT35 trade moves
these numbers. Update them only for an intended behaviour change.

Each identity is pinned three times: with the shipped execution policy's
``downsize_gain_band`` (A1u) disabled and the December deferral (H2) off,
which reproduces the pre-A1u numbers exactly — the engine/fill/cost
fingerprint; with the shipped schedule alone; and with the shipped policy,
band and deferral together, so both rules' (and their composition's) effect on
the same fixture is pinned too (``docs/research/gain_band_integration_v1.md``,
``docs/research/band_h2_combo_v1.md``).

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
    # The shipped December deferral window, on unless the variant turns it off
    # (the pre-H2 fingerprints keep it off).
    deferral: bool = False
    # Downsizes the December window postponed across the run (0 when off).
    deferred: int = 0


_VT35 = _Golden(
    vt35=True,
    e2_only=False,
    gain_band=False,
    # No --start: the 200-session SMA warmup is excluded from evaluation.
    start_date="2019-10-16T00:00:00-04:00",
    end_date="2022-12-30T00:00:00-05:00",
    rebalances=809,
    fills=227,
    ending_equity=210_288.53,
    cagr=0.260728,
    max_drawdown=0.277902,
    benchmark_return=0.281602,
    fill_digest="820637fc4c628eab3d70499fe294cb503f64ce5996141fe897e0b4d53c4bb7af",
)

_VT35_WITH_BAND = replace(
    _VT35,
    gain_band=True,
    # Review fix (2026-10-15): a reduction the risk overlay produced keeps the
    # constant band, so VT35's binding 98 % managed-exposure cap trades its
    # drift trims again (122 -> 123 fills, 149 -> 137 held).
    fills=122,
    ending_equity=210_831.44,
    cagr=0.261742,
    max_drawdown=0.282176,
    suppressed=137,
    fill_digest="2754070b4375d1d9795c116881184cb77ef8f2c113ec83f8b6a968d4ef037b57",
)

_E2_ONLY = _Golden(
    vt35=False,
    e2_only=True,
    gain_band=False,
    start_date="2020-01-02T00:00:00-05:00",
    end_date="2022-12-30T00:00:00-05:00",
    rebalances=756,
    fills=230,
    ending_equity=174_217.87,
    cagr=0.203681,
    max_drawdown=0.278088,
    benchmark_return=0.177179,
    fill_digest="e4f4db1fa9e313fd42753e13f1b3f08e6ec15cb10514b4ecc764f6e1916c9777",
)

_E2_ONLY_WITH_BAND = replace(
    _E2_ONLY,
    gain_band=True,
    fills=126,
    ending_equity=175_650.94,
    cagr=0.206978,
    max_drawdown=0.278136,
    suppressed=134,
    fill_digest="2585edacd0fd49649d2f288636954d4ede1e43e48bdb18e2b80bb7e4564ff003",
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
    ending_equity=149_261.69,
    cagr=0.179838,
    max_drawdown=0.253764,
    benchmark_return=0.176273,
    fill_digest="77eefae66eff6d423c1cc874f6381b825102583aee33e1aa473298df8be1bfbc",
)

_E2_WITH_BAND = replace(
    _E2,
    gain_band=True,
    fills=93,
    ending_equity=148_551.80,
    cagr=0.177518,
    max_drawdown=0.255391,
    suppressed=124,
    fill_digest="e610df392d9ef153109e0acca623b833590dae5330a6646524f955df0036b6f9",
)

# The shipped policy: A1u's band and H2's December deferral together. The
# downsizes the December window postpones stop counting as band holds (H2 is
# tested first, as in the research composition K), so `suppressed` falls as
# `deferred` rises.
_VT35_WITH_BAND_AND_DEFERRAL = replace(
    _VT35,
    gain_band=True,
    deferral=True,
    fills=118,
    ending_equity=211_402.83,
    cagr=0.262807,
    max_drawdown=0.282176,
    suppressed=132,
    deferred=9,
    fill_digest="f19c564e25d8628cb6ad7f895c9f72e47bc3848307852d344b21f37bf41b63d6",
)

_E2_ONLY_WITH_BAND_AND_DEFERRAL = replace(
    _E2_ONLY,
    gain_band=True,
    deferral=True,
    fills=122,
    ending_equity=176_126.98,
    cagr=0.208069,
    max_drawdown=0.278136,
    suppressed=129,
    deferred=9,
    fill_digest="a6ac147223669c841d5ef4f609f011a178ddce6beb30f5a54280c25be9b07225",
)

_E2_WITH_BAND_AND_DEFERRAL = replace(
    _E2,
    gain_band=True,
    deferral=True,
    fills=89,
    ending_equity=148_954.41,
    cagr=0.178835,
    max_drawdown=0.253373,
    suppressed=119,
    deferred=9,
    fill_digest="a764c61c23f51bbb43afc890a5ff358a098cde4d3f5f4a30cb58e6923431f8eb",
)


def _config(tmp_path, *, vt35: bool, e2_only: bool = False, gain_band: bool, deferral: bool = False):
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
    # The pre-H2 fingerprints pin the deferral off explicitly; the shipped
    # variants read the value the packaged configuration ships.
    execution = replace(
        execution,
        december_downsize_deferral_sessions=(config.execution.december_downsize_deferral_sessions if deferral else 0),
    )
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
    shipped_execution = load_config(str(ROOT / "config" / "settings.yaml")).execution
    if golden.gain_band:
        assert shipped_execution.downsize_gain_band is not None
    if golden.deferral:
        assert shipped_execution.december_downsize_deferral_sessions == 10
    config = _config(
        tmp_path, vt35=golden.vt35, e2_only=golden.e2_only, gain_band=golden.gain_band, deferral=golden.deferral
    )
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
    deferred = sum(len(record.december_deferral_holds) for record in result.rebalances)
    assert deferred == golden.deferred
    if not golden.deferral:
        assert deferred == 0


def test_vt35_golden_backtest_is_unchanged(tmp_path) -> None:
    """VT35 (``trend_rule: sma``) with the gain band off: the pre-A1u numbers."""
    _assert_golden(tmp_path, _VT35)


def test_vt35_with_the_gain_band_golden_backtest_is_unchanged(tmp_path) -> None:
    """The gain band alone on VT35: the band suppresses QLD downsizes."""
    _assert_golden(tmp_path, _VT35_WITH_BAND)


def test_vt35_with_the_shipped_policy_golden_backtest_is_unchanged(tmp_path) -> None:
    """The shipped policy on VT35: A1u's band plus H2's December deferral."""
    _assert_golden(tmp_path, _VT35_WITH_BAND_AND_DEFERRAL)


def test_e2_without_the_early_path_golden_backtest_is_unchanged(tmp_path) -> None:
    """E2 1.1.0 (the rule before R2) with the gain band off keeps its pre-A1u
    golden numbers exactly."""
    config = _config(tmp_path, vt35=False, e2_only=True, gain_band=False)
    assert config.strategy.equity.parameters["trend_rule"] == "majority_sma_tsm_don"
    assert "early_reentry_weight" not in config.strategy.equity.parameters
    _assert_golden(tmp_path, _E2_ONLY)


def test_e2_without_the_early_path_with_the_gain_band_golden_backtest_is_unchanged(tmp_path) -> None:
    """The gain band alone on E2 1.1.0."""
    _assert_golden(tmp_path, _E2_ONLY_WITH_BAND)


def test_e2_without_the_early_path_with_the_shipped_policy_golden_backtest_is_unchanged(tmp_path) -> None:
    """The shipped policy on E2 1.1.0."""
    _assert_golden(tmp_path, _E2_ONLY_WITH_BAND_AND_DEFERRAL)


def test_e2_golden_backtest_is_unchanged(tmp_path) -> None:
    """The shipped rule with the gain band off: the R2 numbers as shipped
    before A1u, reproduced exactly on the same fixture."""
    config = _config(tmp_path, vt35=False, gain_band=False)
    assert config.strategy.equity.parameters["trend_rule"] == "majority_sma_tsm_don"
    assert config.strategy.equity.parameters["early_reentry_weight"] == 0.5
    _assert_golden(tmp_path, _E2)


def test_e2_with_the_gain_band_golden_backtest_is_unchanged(tmp_path) -> None:
    """E2 + R2 + A1u on the committed fixture."""
    _assert_golden(tmp_path, _E2_WITH_BAND)


def test_e2_with_the_shipped_policy_golden_backtest_is_unchanged(tmp_path) -> None:
    """The shipped identity: E2 + R2 + A1u + H2 on the committed fixture."""
    _assert_golden(tmp_path, _E2_WITH_BAND_AND_DEFERRAL)
