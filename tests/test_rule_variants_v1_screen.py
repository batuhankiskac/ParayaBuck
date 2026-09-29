"""Invariants of the research-only rule variants v1 screen (synthetic data, no I/O)."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

_DIR = Path(__file__).resolve().parents[1] / "research" / "rule_variants_v1"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"rule_variants_v1_{name}", _DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


screen = _load("screen")
download = _load("download_data")


def test_exit_buffer_holds_through_shallow_dip_and_enters_only_above_sma():
    assert screen.buffered_regime(True, 98.5, 100.0, 0.02) is True
    assert screen.buffered_regime(True, 97.9, 100.0, 0.02) is False
    assert screen.buffered_regime(False, 99.9, 100.0, 0.02) is False
    assert screen.buffered_regime(False, 100.1, 100.0, 0.02) is True


def test_min_hold_blocks_reversal_until_enough_sessions():
    assert screen.min_hold_regime(True, False, 4, 5) is True
    assert screen.min_hold_regime(True, False, 5, 5) is False
    assert screen.min_hold_regime(False, False, 0, 5) is False


def test_staged_exposure_moves_in_thirds_and_clamps():
    path, s = [], 0.0
    for up in (True, True, True, True, False):
        s = screen.staged_exposure(s, up)
        path.append(round(s, 6))
    assert path == [0.333333, 0.666667, 1.0, 1.0, 0.666667]


def test_trend_strength_levels():
    assert screen.trend_strength_level(106.0, 100.0) == 2
    assert screen.trend_strength_level(103.0, 100.0) == 1
    assert screen.trend_strength_level(99.0, 100.0) == 0


def test_dividend_rate_restated_to_split_adjusted_basis():
    splits = [("QLD", date(2021, 5, 25), 2.0), ("BIL", date(2017, 11, 30), 0.5)]
    assert download.restate_to_split_basis(0.4, "QLD", date(2020, 12, 23), splits) == pytest.approx(0.2)
    assert download.restate_to_split_basis(0.4, "QLD", date(2021, 6, 1), splits) == pytest.approx(0.4)
    assert download.restate_to_split_basis(0.05, "BIL", date(2017, 10, 2), splits) == pytest.approx(0.10)


def test_switch_count_starts_from_cash_and_counts_each_component():
    regimes = [(True, False), (True, True), (False, True), (True, False)]
    assert screen.switches(regimes) == [3, 2]


def test_simulator_credits_dividend_and_charges_cost_on_turnover():
    days = ["d0", "d1", "d2"]
    bars = {"SPY": {d: (1.0, 1.0) for d in days}, "X": {"d0": (10.0, 10.0), "d1": (10.0, 10.0), "d2": (9.9, 9.9)}}
    market = SimpleNamespace(days=days, first=1, bars=bars, divs={"X": {"d2": 0.1}, "SPY": {}})
    curve, regimes, traded = screen.simulate(market, lambda i: ({"X": 0.5}, (True,)), mult=1)
    # 50% bought at d1 open (cost 4 bps on 0.5 notional); d2 ex-dividend offsets the 0.1 price drop.
    assert traded == pytest.approx(0.5)
    assert curve[-1][1] == pytest.approx(1 - 0.0004 * 0.5)
    assert regimes == [(True,), (True,)]
