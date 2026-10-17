"""The gate set: one invariant per test, on synthetic records (no simulation)."""

from __future__ import annotations

import numpy as np

from research.gate_power_v2 import stages
from research.fast_engine.fe_config import E2_START_YEARS, INSTRUMENTS, OOS_WINDOWS, TAX_WINDOWS

MODELS = ("base", "calibrated")
BLOCKS = ("block10", "block40")
MODES = ("progressive", "flat")


def _cells(cagr=0.157, dd=0.40, turnover=5.0, subs=(0.13, 0.15, 0.33)):
    return {
        "cagr": cagr,
        "max_dd": dd,
        "turnover_annual": turnover,
        "calmar": cagr / dd,
        "2017_end": {"cagr": subs[2]},
        "subperiods": {p: {"cagr": c} for p, c in zip(("P1", "P2", "P3"), subs)},
    }


def control(**over):
    """The synthetic control record: every gate's reference numbers, all weaker than `record()`."""
    r = {
        "main": {md: {"m1": _cells(0.16), "m2": _cells(0.157)} for md in MODELS},
        "after_tax": {wn: {"after_tax_cagr": 0.10} for wn in TAX_WINDOWS},
        "boot": {
            "pre": {md: {"median_delta_pp": 0.1} for md in MODELS},
            "tax": {"progressive": {"median_delta_pp": 0.0, "share_gt_0": 0.50},
                    "flat": {"median_delta_pp": 0.0, "share_gt_0": 0.50}},
        },
        "oos": {k: {"cagr": 0.10, "max_dd": 0.40} for k in OOS_WINDOWS},
        "oos_pooled": {"median_delta_pp": 0.0},
        "etf_after_tax": {k: {"after_tax_cagr": 0.05} for k in INSTRUMENTS},
        "rolling": {str(y): {"after_tax_cagr": 0.10} for y in E2_START_YEARS},
        "fresh": {blk: {"tax": {md: np.zeros(20) for md in MODES}} for blk in BLOCKS},
    }
    _apply(r, over)
    return r


def record(shift=0.0, **over):
    """A synthetic arm record that clears every gate at `shift = 0`."""
    r = control()
    for md in MODELS:
        r["main"][md]["m2"]["subperiods"]["P1"]["cagr"] = 0.14
        r["main"][md]["m2"]["subperiods"]["P2"]["cagr"] = 0.16
    r["after_tax"] = {wn: {"after_tax_cagr": 0.101 + shift} for wn in TAX_WINDOWS}
    r["boot"]["pre"] = {md: {"median_delta_pp": 0.2} for md in MODELS}
    r["boot"]["tax"] = {"progressive": {"median_delta_pp": 0.250699, "share_gt_0": 0.635},
                        "flat": {"median_delta_pp": 0.331078, "share_gt_0": 0.683}}
    r["oos"] = {k: {"cagr": 0.101 + shift, "max_dd": 0.40} for k in OOS_WINDOWS}
    r["oos_pooled"] = {"median_delta_pp": 0.05}
    r["etf_after_tax"] = {k: {"after_tax_cagr": 0.051 + shift} for k in INSTRUMENTS}
    r["rolling"] = {str(y): {"after_tax_cagr": 0.101 + shift} for y in E2_START_YEARS}
    r["fresh"] = {blk: {"tax": {md: np.full(20, 0.01) for md in MODES}} for blk in BLOCKS}
    _apply(r, over)
    return r


def _apply(r, over):
    """Apply dotted overrides (`"main.base.m2.max_dd": 0.42`)."""
    for path, value in over.items():
        node = r
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[int(k)] if k.isdigit() else node[k]
        node[keys[-1]] = value


def ctrl_at(r):
    return control()["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"]


def test_all_gates_pass_on_a_dominating_arm():
    g = stages.gates(record(), control())
    assert g["pass"] and g["failed"] == []


def test_g1_fails_when_the_arm_loses_on_return():
    r = record()
    r["main"]["base"]["m2"] = _cells(0.150, dd=0.35)          # CAGR < K, Calmar < 1.1 x K
    r["main"]["calibrated"]["m2"] = _cells(0.150, dd=0.35)
    g = stages.gates(r, control())
    assert not g["G1_base"] and not g["G1_calibrated"] and not g["pass"]
    assert {"G1_base", "G1_calibrated"} <= set(g["failed"])


def test_g1_accepts_calmar_when_it_is_1_1x_and_within_half_a_point():
    r = record()
    ctrl = control()
    r["main"]["base"]["m2"] = _cells(0.1565, dd=0.30)          # -0.05 pp CAGR, Calmar 1.4x
    g = stages.gates(r, ctrl)
    assert g["G1_base"] and g["G1_calibrated"]


def test_g2_fails_above_one_point_of_extra_drawdown():
    r = record()
    r["main"]["base"]["m2"] = _cells(0.157, dd=0.411)
    g = stages.gates(r, control())
    assert not g["G2_base"] and g["G2_calibrated"]
    r = record()
    ctrl = control()
    r["main"]["base"]["m2"] = _cells(0.157, dd=ctrl["main"]["base"]["m2"]["max_dd"] + stages.G2_DD_PP / 100)
    g = stages.gates(r, control())  # exactly at the limit passes (<=)
    assert g["G2_base"]


def test_g3_fails_when_the_2017_leg_lags_more_than_one_point():
    r = record()
    r["main"]["base"]["m2"]["2017_end"] = {"cagr": 0.31}
    g = stages.gates(r, control())
    assert not g["G3_base"] and g["pass"] is False and "G3_base" in g["failed"]


def test_g4_needs_two_winning_subperiods():
    r = record()
    ctrl = control()
    r["main"]["base"]["m2"]["subperiods"] = {"P1": {"cagr": 0.12}, "P2": {"cagr": 0.14}, "P3": {"cagr": 0.34}}
    g = stages.gates(r, ctrl)
    assert not g["G4"] and g["G4_wins"] == ["P3"]


def test_g5_uses_the_pre_tax_bootstrap_medians():
    r = record()
    r["boot"]["pre"]["calibrated"]["median_delta_pp"] = -0.001
    g = stages.gates(r, control())
    assert g["G5_base"] and not g["G5_calibrated"]


def test_g6_is_g1_at_m_equals_1():
    r = record()
    r["main"]["base"]["m1"] = _cells(0.150, dd=0.35)
    g = stages.gates(r, control())
    assert not g["G6_base"] and g["G1_base"]


def test_g7_rejects_a_single_bad_window_and_a_too_large_drawdown():
    r = record()
    ctrl = control()
    for k in list(OOS_WINDOWS)[:2]:
        r["oos"][k]["cagr"] = ctrl["oos"][k]["cagr"] - 0.006
    g = stages.gates(r, ctrl)
    assert not g["G7"] and g["G7_detail"]["n_cagr_within"] == 4
    r = record()
    r["oos"]["M3"]["max_dd"] = ctrl["oos"]["M3"]["max_dd"] + 0.09
    g = stages.gates(r, ctrl)
    assert not g["G7"] and g["G7_detail"]["max_dd_delta_pp"] > 8.0


def test_g8_uses_the_pooled_oos_median():
    r = record()
    r["oos_pooled"] = {"median_delta_pp": -0.01}
    g = stages.gates(r, control())
    assert not g["G8"] and g["G8_median_pp"] == -0.01


def test_g9_allows_exactly_twice_the_turnover():
    r = record()
    ctrl = control()
    r["main"]["base"]["m2"]["turnover_annual"] = ctrl["main"]["base"]["m2"]["turnover_annual"] * 2
    r["main"]["calibrated"]["m2"]["turnover_annual"] = ctrl["main"]["calibrated"]["m2"]["turnover_annual"] * 2
    assert stages.gates(r, ctrl)["G9"]
    r["main"]["base"]["m2"]["turnover_annual"] *= 1.0001
    assert not stages.gates(r, ctrl)["G9"]


def test_after_tax_needs_all_three_windows_and_both_bootstraps():
    r = record()
    r["after_tax"]["2010_01_to_end"]["after_tax_cagr"] = ctrl_at(r)
    at = stages.after_tax_gates(r, control())
    assert at["windows"]["2000_03_to_2026_09"]["pass"] and not at["windows"]["2010_01_to_end"]["pass"]
    assert not at["pass"]
    r = record()
    r["boot"]["tax"]["flat"]["share_gt_0"] = 0.549
    at = stages.after_tax_gates(r, control())
    assert at["B1_recent"]["pass"] and not at["B2_flat"]["pass"] and not at["pass"]


def test_e1_and_e2_need_a_median_and_a_share():
    ctrl = control()
    r = record()
    bad = list(INSTRUMENTS)[:9]                    # 9/21 < 60 %
    for k in bad:
        r["etf_after_tax"][k]["after_tax_cagr"] = ctrl["etf_after_tax"][k]["after_tax_cagr"]
    assert not stages.e1_gate(r, ctrl)["pass"]
    assert stages.e1_gate(record(), ctrl)["pass"]
    r = record()
    for y in list(r["rolling"])[:9]:
        r["rolling"][y]["after_tax_cagr"] = ctrl["rolling"][y]["after_tax_cagr"] - 0.001
    assert not stages.e2_gate(r, ctrl)["pass"]
    assert stages.e2_gate(record(), ctrl)["pass"]


def test_fresh_gate_checks_every_block_and_mode():
    ctrl = control()
    r = record()
    r["fresh"]["block40"]["tax"]["flat"] = np.zeros(20)   # ties the control -> median 0, share 0
    g = stages.fresh_gate(r, ctrl)
    assert not g["cells"]["block40_flat"]["pass"] and g["cells"]["block10_progressive"]["pass"]
    assert not g["pass"]


def test_stage1_rule_is_fixed_and_narrow():
    """The documented stage-1 rule: after-tax 2000 Δ > 0, B1 median > 0, DD ≤ K + 2 pp,
    turnover ≤ 2 × K -- each one rejects on its own."""
    a, c = record(), control()
    ok, g = stages.stage1_pass(a, c)
    assert ok and g["after_tax_2000_delta_pp"] > 0 and g["b1_median_delta_pp"] > 0
    assert stages.S1_MAX_DD_PP == 2.0 and stages.S1_TURNOVER_MULT == 2.0 and stages.SHARE_BOOT == 0.55

    tie = record()
    tie["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"] = c["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"]
    assert not stages.stage1_pass(tie, c)[0]                      # Δ must be strictly > 0

    zero = record()
    zero["boot"]["tax"]["progressive"]["median_delta_pp"] = 0.0
    assert not stages.stage1_pass(zero, c)[0]

    dd = record()
    dd["main"]["calibrated"]["m2"]["max_dd"] = c["main"]["calibrated"]["m2"]["max_dd"] + 0.020001
    assert not stages.stage1_pass(dd, c)[0]

    turn = record()
    turn["main"]["base"]["m2"]["turnover_annual"] = c["main"]["base"]["m2"]["turnover_annual"] * 2.0001
    assert not stages.stage1_pass(turn, c)[0]
