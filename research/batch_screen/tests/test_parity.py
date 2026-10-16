"""The framework's parity cells: its drivers against the engine and the old pure-Python code, its
target pipeline against the production one, and the control's published anchors.

`fe_boot.boot` is the reference for the clean bootstrap and `band_h2_combo_v1.screen.boot_oos_market`
for the pooled OOS bootstrap; both are bit-identical checks (max |diff| == 0), not tolerances.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from research.batch_screen import api, run, stages, windows
from research.batch_screen import boot as B
from research.fast_engine import fe_data, fe_signals

BAND_H2 = api.ROOT / "research/band_h2_combo_v1/RESULTS.json"
PUBLISHED_TOL = 5e-7  # RESULTS.json stores round(x, 6)


@pytest.fixture(scope="module")
def bh():
    return json.loads(BAND_H2.read_text())


def test_clean_bootstrap_driver_is_bit_identical_to_the_engine():
    checks = run.boot_parity(n_paths=30)
    assert checks
    for name, cell in checks.items():
        assert cell["max_abs_diff"] == 0.0, name


def test_oos_pooled_driver_is_bit_identical_to_the_old_code():
    checks = run.oos_pooled_parity(per_market=20)
    assert checks
    for name, cell in checks.items():
        assert cell["max_abs_diff"] == 0.0, name


def test_control_targets_are_the_published_r2_series():
    """`k_control`'s targets must be bit-identical to `fe_data.main_inputs`' R2 wants."""
    days, p, _i0, _i1 = windows.main_panel(0.0)
    arm = api.load_arm("k_control", "K")
    t = api.call_targets(arm["mod"], p, arm)
    wants, leg = t.wants, t.leg
    _d, o, c, w_e2, w_r2, _e2 = fe_data.main_inputs(0.0)
    assert leg == "2x"
    assert np.array_equal(wants[0], w_r2)
    assert np.array_equal(p.legs["2x"][0][0], o) and np.array_equal(p.legs["2x"][1][0], c)


def test_r2_control_differs_from_k_only_in_the_fill_rule():
    k = api.load_arm("k_control", "K")
    r2 = api.load_arm("r2_control", "R2")
    assert k["rule"] == "K" and r2["rule"] == "C0"
    assert k["params"] == r2["params"] == {}
    days, p, i0, i1 = windows.main_panel(0.0)
    env = windows.try_env(days, i0, i1)
    wk = api.call_targets(k["mod"], p, k).wants
    wr = api.call_targets(r2["mod"], p, r2).wants
    assert np.array_equal(wk, wr)
    rk = stages.run_one(k, p, env, i0, i1, "pre_tax", 2)
    rr = stages.run_one(r2, p, env, i0, i1, "pre_tax", 2)
    assert rk["strategy_trades"] != rr["strategy_trades"]  # the rule reaches the engine
    assert rk["cagr_usd_liquidated"] > rr["cagr_usd_liquidated"]


def test_vol_shape_p15_matches_its_published_sizing():
    """The p = 1.5 plugin's targets must be bit-identical to `vol_shape_v1.shapes.apply_shape`."""
    from research.vol_shape_v1 import shapes

    days, p, _i0, _i1 = windows.main_panel(0.0)
    arm = api.load_arm("vol_shape_p15", "p15")
    wants = api.call_targets(arm["mod"], p, arm).wants
    e2 = fe_signals.e2_up(p.price)
    comps = fe_signals.early_components(p.price)
    on_eff, factor = fe_signals.r2_state(p.price, e2, comps)
    want = shapes.apply_shape(on_eff, factor, p.vol, arm["params"]["p"], arm["params"]["c"])
    assert np.array_equal(wants, want)
    # ... and (1.0, 0.35) is production's own sizing rule, bit for bit
    assert np.array_equal(shapes.apply_shape(on_eff, factor, p.vol, 1.0, 0.35),
                          fe_signals.sized_wants(on_eff, factor, p.vol))
    assert arm["params"]["c"] == 0.24593188954326456


def test_control_main_window_matches_the_published_k_cells(bh):
    arm = api.load_arm("k_control", "K")
    for md, spread in (("base", 0.0), ("calibrated", 0.0075)):
        days, p, i0, i1 = windows.main_panel(spread)
        env = windows.try_env(days, i0, i1)
        for m in (1, 2):
            r = stages.run_one(arm, p, env, i0, i1, "pre_tax", m)
            got = stages._cells(r, days, i0, i1)
            want = bh["pre_tax_main"][md][f"m{m}"]["K"]
            assert abs(got["cagr"] - want["cagr"]) <= PUBLISHED_TOL
            assert abs(got["max_dd"] - want["max_dd"]) <= PUBLISHED_TOL
            assert abs(got["turnover_annual"] - want["turnover_annual"]) <= PUBLISHED_TOL


def test_control_after_tax_windows_match_the_published_k_cells(bh):
    arm = api.load_arm("k_control", "K")
    days, p, _i0, _i1 = windows.main_panel(0.0)
    for wn, (a, b) in {"2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
                       "2010_01_to_end": ("2010-01-01", "2026-09-29"),
                       "2017_01_to_end": ("2017-01-03", "2026-09-29")}.items():
        i0, i1 = stages.fe_study.window_indices(days, a, b)
        rec = stages.after_tax_cells(arm, p, days, i0, i1, "none")
        want = bh["after_tax_windows"]["100k"][wn]["K"]["after_tax_cagr"]
        assert abs(rec["after_tax_cagr"] - want) <= PUBLISHED_TOL, wn


def test_control_oos_and_instrument_stages_match_the_published_k_cells(bh):
    arm = api.load_arm("k_control", "K")
    oos = stages.stage2_oos(arm, "none")
    for key in ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"):
        assert abs(oos[key]["cagr"] - bh["oos"][key]["K"]["cagr"]) <= PUBLISHED_TOL, key
    pre, after = stages.stage2_instruments(arm, "none")
    published = bh["instruments"]
    for key in pre:
        assert abs(pre[key]["cagr"] - published[key]["K"]["cagr"]) <= PUBLISHED_TOL, key
    e1 = bh["E1_after_tax_etfs_rindex"]
    for key in after:
        assert abs(after[key]["after_tax_cagr"] - e1[key]["K"]["after_tax_cagr"]) <= PUBLISHED_TOL, key


def test_control_rolling_starts_match_the_published_k_cells(bh):
    arm = api.load_arm("k_control", "K")
    rolling = stages.stage2_rolling(arm, "none")
    for yr in ("2001", "2005", "2011", "2016", "2021"):
        assert abs(rolling[yr]["after_tax_cagr"]
                   - bh["E2_rolling_starts"][yr]["K"]["after_tax_cagr"]) <= PUBLISHED_TOL, yr


def test_control_stage1_vectors_match_the_published_clean_bootstrap(bh):
    """The framework's B1/B2 path vectors, against the engine's `fe_boot.boot` (bit-identical)."""
    from research.fast_engine import fe_boot

    arm = api.load_arm("k_control", "K")
    mine = B.boot([arm], seed=B.SEED, block=B.BLOCK, n_pre=50, n_tax=50, capitals=("100k",),
                  modes=("progressive", "flat"), quiet=True)
    theirs = fe_boot.boot(seed=B.SEED, block=B.BLOCK, arms=("C0", "K"), n_pre=50, n_tax=50,
                          capitals=("100k",), modes=("progressive", "flat"), anchor_e2=False,
                          deltas=("C0",), keep_paths=True, quiet=True)
    for md in ("progressive", "flat"):
        assert np.array_equal(mine["tax"]["100k"][md][arm["id"]],
                              theirs["paths"]["tax"]["100k"][md]["K"])
    stats = B.delta_stats(np.zeros(50))
    assert stats["median_delta_pp"] == 0.0 and stats["share_gt_0"] == 0.0 and stats["p_mean"] == 1.0


def test_delta_stats_p_value_is_the_share_of_non_positive_means():
    rng = np.random.default_rng(20261016)
    d = rng.normal(0.5, 1.0, 400)
    s = B.delta_stats(d)
    assert s["median_delta_pp"] == pytest.approx(np.median(d) * 100)
    assert 0.0 <= s["p_mean"] <= 1.0 and s["p_mean"] < 0.01
    assert B.delta_stats(-d)["p_mean"] > 0.99
