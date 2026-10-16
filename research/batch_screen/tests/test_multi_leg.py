"""The N-leg kernel's framework integration: the contract, the routing, the loan, the option leg.

The engine-level parity cells (legs 2..N == 0 vs the engine, legs 3..N == 0 vs `fe2_sim`, margin on
a single leg vs `fast_engine_opts`) live in `research/fast_engine3/fe3_parity.py`; these tests cover
what the *framework* adds: the `"legs": [...]` declaration, the N-leg return forms, the
`stages.run_one` / `boot.boot` routing, the margin schedule, and that the bootstrap draws the same
paths for a multi-leg arm and the one-leg control.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from research.batch_screen import api, stages, windows
from research.batch_screen import boot as B
from research.batch_screen import run
from research.batch_screen.tests.test_api import tiny_panel, write_idea

HERE = Path(__file__).resolve().parent
PROBE = HERE.parent / "probes" / "multi_leg.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("probe_multi_leg_t", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def probe_arm(probe, arm_id):
    arm = api.resolve_arm(probe, arm_id)
    arm.update({"id": f"multi_leg/{arm_id}", "idea": "multi_leg", "arm_id": arm_id, "mod": probe})
    return arm


# ----------------------------------------------------------------------------- the contract
def test_max_legs_matches_the_kernel():
    from research.fast_engine3 import fe3_sim

    assert api.MAX_LEGS == fe3_sim.N_LEGS == 4


def test_the_legs_declaration_is_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)

    def mod(extra):
        write_idea(tmp_path, "m", "import numpy as np\nARMS = {'a': {%s}}\n\n\n"
                                  "def targets(data, spec):\n    return np.zeros((data.P, data.T))\n"
                                  % extra)
        return api.load_idea("m")

    with pytest.raises(api.PluginError, match="2..4"):
        api.resolve_arm(mod("'legs': ['2x']"), "a")
    with pytest.raises(api.PluginError, match="2..4"):
        api.resolve_arm(mod("'legs': ['2x', '1x', '2x', '1x', '2x']"), "a")
    with pytest.raises(api.PluginError, match="twice"):
        api.resolve_arm(mod("'legs': ['2x', '2x']"), "a")
    with pytest.raises(api.PluginError, match="unknown leg"):
        api.resolve_arm(mod("'legs': ['2x', 'nope']"), "a")
    with pytest.raises(api.PluginError, match="panel leg"):
        api.resolve_arm(mod("'legs': ['gld', '2x']"), "a")
    with pytest.raises(api.PluginError, match="disagrees"):
        api.resolve_arm(mod("'legs': ['2x', '1x'], 'leg': '1x'"), "a")
    with pytest.raises(api.PluginError, match="disagrees"):
        api.resolve_arm(mod("'legs': ['2x', '1x'], 'leg_b': '2x'"), "a")
    # a loan needs a multi-leg arm
    with pytest.raises(api.PluginError, match="multi-leg arm"):
        api.resolve_arm(mod("'margin_spread': 0.01"), "a")
    with pytest.raises(api.PluginError, match="finite"):
        api.resolve_arm(mod("'legs': ['2x', '1x'], 'margin_spread': float('nan')"), "a")
    # a multi-leg arm may not carry a single-leg kernel option
    from research.batch_screen import rules

    rules.register_rule("t_band0", band=True, usd_rule=True, defer=True, opts={"band_up": 0.0})
    with pytest.raises(api.PluginError, match="multi-leg kernel"):
        api.resolve_arm(mod("'legs': ['2x', '1x'], 'rule': 't_band0'"), "a")
    # a valid declaration resolves with the legs tuple
    arm = api.resolve_arm(mod("'legs': ['2x', 'gld', 'shy'], 'margin_spread': 0.015"), "a")
    assert arm["legs"] == ("2x", "gld", "shy") and arm["n_legs"] == 3 and arm["multi_leg"]
    assert arm["margin_spread"] == 0.015 and arm["opts"] == {"margin_spread": 0.015}
    assert arm["params"]["margin_spread"] == 0.015


def test_n_leg_return_forms(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    p = tiny_panel()
    head = "import numpy as np\nARMS = {'a': {'legs': ['2x', '1x']}}\n\n\ndef targets(data, spec):\n"
    arm = {"params": {"legs": ["2x", "1x"]}, "legs": ("2x", "1x"), "leg": "2x",
           "leg_b": "1x"}

    def load(body):
        write_idea(tmp_path, "n", head + body)
        return api.load_idea("n")

    # a bare sequence of one weight panel per declared leg
    m = load("    w = np.zeros((data.P, data.T))\n    return [w, w]\n")
    t = api.call_targets(m, p, arm)
    assert t.n_legs == 2 and not t.multi_leg and t.leg == "2x" and t.leg_b == "1x"
    # the (weights, legs) tuple, with the legs in the declared order
    m = load("    w = np.zeros((data.P, data.T))\n    return [w, w], ['1x', '2x']\n")
    t = api.call_targets(m, p, arm)
    assert [k for k, _w in t.leg_wants()] == ["2x", "1x"]
    # the dict form
    m = load("    w = np.zeros((data.P, data.T))\n    return {'1x': w, '2x': w}\n")
    t = api.call_targets(m, p, arm)
    assert [k for k, _w in t.leg_wants()] == ["2x", "1x"]
    # a leg the spec does not declare, and a duplicate
    m = load("    w = np.zeros((data.P, data.T))\n    return [w, w], ['2x', 'shy']\n")
    with pytest.raises(api.PluginError, match="spec declares"):
        api.call_targets(m, p, arm)
    m = load("    w = np.zeros((data.P, data.T))\n    return [w, w], ['2x', '2x']\n")
    with pytest.raises(api.PluginError, match="twice"):
        api.call_targets(m, p, arm)
    # non-finite and wrong-shape panels
    m = load("    w = np.zeros((data.P, data.T))\n    return [w, np.full_like(w, np.nan)]\n")
    with pytest.raises(api.PluginError, match="non-finite"):
        api.call_targets(m, p, arm)
    m = load("    w = np.zeros((data.P, data.T))\n    return [w, w[:, :-1]]\n")
    with pytest.raises(api.PluginError, match="panel shape"):
        api.call_targets(m, p, arm)
    # an N-leg return without a spec declaration is refused (the dict form always reaches it)
    m = load("    w = np.zeros((data.P, data.T))\n    return {'1x': w, '2x': w, 'shy': w}\n")
    with pytest.raises(api.PluginError, match="spec declares"):
        api.call_targets(m, p, {"params": {}, "leg": "2x"})
    # a one-key dict is not an N-leg return
    m = load("    w = np.zeros((data.P, data.T))\n    return {'2x': w}\n")
    with pytest.raises(api.PluginError, match="at least two"):
        api.call_targets(m, p, {"params": {}, "leg": "2x"})


# ----------------------------------------------------------------------------- the routing
def test_three_leg_empty_books_are_the_control(probe):
    """Leg A production, legs B/C a zero target: the K control, bit for bit."""
    days, p, i0, i1 = windows.main_panel(0.0)
    env = windows.try_env(days, i0, i1)
    ctrl = api.load_arm("k_control", "K")
    arm = probe_arm(probe, "zeros")
    assert len(arm["legs"]) == 3
    for mode, capital in (("pre_tax", 1.0), ("progressive", 100_000.0)):
        rc = stages.run_one(ctrl, p, env, i0, i1, mode, 2, capital)
        rz = stages.run_one(arm, p, env, i0, i1, mode, 2, capital)
        assert np.array_equal(rc["_nav"], rz["_nav"])
        assert rc["cagr_usd_liquidated"] == rz["cagr_usd_liquidated"]
        assert rc["turnover_total"] == rz["turnover_total"]
        assert rc["tax_total_try"] == rz["tax_total_try"]
        assert rz["_legs"]["B"]["trades"] == 0 and rz["_legs"]["C"]["trades"] == 0
        assert rz["_legs"]["B"]["units_end"] == 0.0 and rz["_legs"]["C"]["units_end"] == 0.0


def test_margin_arm_charges_interest(probe):
    """The loan is real, it is charged on the negative balance, and it lowers the NAV."""
    days, p, i0, i1 = windows.main_panel(0.0)
    env = windows.try_env(days, i0, i1)
    arm = probe_arm(probe, "margin_qqq")
    assert arm["margin_spread"] == 0.015
    on = stages.run_one(arm, p, env, i0, i1, "progressive", 2, 100_000.0)
    off = stages.run_one(dict(arm, opts={}, margin_spread=None), p, env, i0, i1, "progressive", 2,
                         100_000.0)
    assert on["opts_stats"]["margin_usd"] > 0
    assert "opts_stats" not in off
    assert not np.array_equal(on["_nav"], off["_nav"])
    assert on["cagr_usd_liquidated"] < off["cagr_usd_liquidated"]
    # a loan at the window's own bill rate (spread 0) is still charged, and is cheaper
    bill = stages.run_one(dict(arm, margin_spread=0.0), p, env, i0, i1, "progressive", 2, 100_000.0)
    assert bill["opts_stats"]["margin_usd"] > 0
    assert on["opts_stats"]["margin_usd"] > bill["opts_stats"]["margin_usd"]
    assert bill["cagr_usd_liquidated"] > on["cagr_usd_liquidated"]


def test_sleeve_over_the_cap_is_funded_by_the_loan(probe):
    days, p, i0, i1 = windows.main_panel(0.0)
    env = windows.try_env(days, i0, i1)
    arm = probe_arm(probe, "sleeve_margin")
    r = stages.run_one(arm, p, env, i0, i1, "progressive", 2, 100_000.0)
    assert r["_legs"]["A"]["trades"] > 0 and r["_legs"]["B"]["trades"] > 0
    assert r["opts_stats"]["margin_usd"] > 0
    assert r["fees_usd"] == pytest.approx(r["_legs"]["A"]["fees_usd"] + r["_legs"]["B"]["fees_usd"],
                                          rel=1e-12)


def test_the_2x_spy_leg_is_a_real_second_book(probe):
    days, p, i0, i1 = windows.main_panel(0.0)
    env = windows.try_env(days, i0, i1)
    arm = probe_arm(probe, "spy_pair")
    assert arm["legs"] == ("2x", "2x_spy")
    r = stages.run_one(arm, p, env, i0, i1, "progressive", 2, 100_000.0)
    a, b = r["_legs"]["A"], r["_legs"]["B"]
    assert a["trades"] > 0 and b["trades"] > 0 and b["turnover_total"] > 0
    assert sum(b["realised_gain_try_by_reason"].values()) != 0.0
    assert r["strategy_trades"] == a["trades"] + b["trades"]


def test_a_four_leg_blend_runs_on_its_reduced_window(probe):
    arm = probe_arm(probe, "offstate_blend")
    days = windows.bars()[0]
    win = windows.arm_window(arm)
    assert win.reduced and win.inputs == ("gld", "ief", "shy")
    assert win.i0 == max(windows.input_start_index(days, k) for k in ("gld", "ief", "shy")) + 1
    rec = stages.stage1(arm, "none", win=win)
    assert rec["window"]["start"] == str(days[win.i0])
    assert np.isfinite(rec["main"]["base"]["m2"]["cagr"])
    assert np.isfinite(rec["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"])


def test_a_two_leg_arm_without_a_loan_stays_on_fe2(probe):
    """The minimal routing point: no loan and two legs -> the published two-leg kernel."""
    from research.fast_engine3 import bridge3

    arm = probe_arm(probe, "zeros")
    assert bridge3.needs_kernel3(arm)                    # 3 legs
    assert not bridge3.needs_kernel3(dict(arm, legs=("2x", "1x"), n_legs=2))
    assert not bridge3.needs_kernel3(dict(arm, legs=("2x",), n_legs=1))
    assert bridge3.needs_kernel3(dict(arm, legs=("2x", "1x"), n_legs=2, margin_spread=0.01))
    assert bridge3.needs_kernel3(dict(arm, legs=("2x", "2x_spy"), n_legs=2))


def test_the_n_leg_bootstrap_keeps_the_control_paths(probe):
    ctrl = api.load_arm("k_control", "K")
    arm = probe_arm(probe, "zeros")
    both = B.boot([ctrl, arm], n_pre=8, n_tax=8, capitals=("100k",), modes=("progressive",),
                  quiet=True)
    alone = B.boot([ctrl], n_pre=8, n_tax=8, capitals=("100k",), modes=("progressive",),
                   quiet=True)
    for md in ("base", "calibrated"):
        assert np.array_equal(both["pre"][md][ctrl["id"]], alone["pre"][md][ctrl["id"]])
    assert np.array_equal(both["tax"]["100k"]["progressive"][ctrl["id"]],
                          alone["tax"]["100k"]["progressive"][ctrl["id"]])
    # the empty-book arm is the control, path for path
    assert np.array_equal(both["tax"]["100k"]["progressive"][arm["id"]],
                          both["tax"]["100k"]["progressive"][ctrl["id"]])
    # the margin arm bootstraps, on the control's own paths
    marg = probe_arm(probe, "margin_qqq")
    mb = B.boot([ctrl, marg], n_pre=8, n_tax=8, capitals=("100k",), modes=("progressive",),
                quiet=True)
    v = mb["tax"]["100k"]["progressive"][marg["id"]]
    assert np.isfinite(v).all()
    assert np.array_equal(mb["tax"]["100k"]["progressive"][ctrl["id"]],
                          alone["tax"]["100k"]["progressive"][ctrl["id"]])
    assert not np.array_equal(v, mb["tax"]["100k"]["progressive"][ctrl["id"]])


def test_the_n_leg_blend_runs_a_clean_bootstrap(probe):
    arm = probe_arm(probe, "offstate_blend")
    win = windows.arm_window(arm)
    pool = windows.boot_pool(windows.bars()[0], win)
    b = B.boot([arm], n_pre=4, n_tax=4, capitals=("100k",), modes=("progressive",), quiet=True,
               pool=pool)
    assert np.isfinite(b["pre"]["base"][arm["id"]]).all()
    assert np.isfinite(b["tax"]["100k"]["progressive"][arm["id"]]).all()
    assert b["params"]["pool"] == list(pool)


# ----------------------------------------------------------------------------- the harness
def test_harness_multi_leg_checks_pass():
    checks = run.multi_leg_checks()
    flat = {k: v["pass"] for k, v in checks.items() if isinstance(v, dict) and "pass" in v}
    assert flat and all(flat.values()), json.dumps(checks, default=str)[:1200]


def test_multi_leg_kernel_parity_pass():
    checks = run.multi_leg_kernel_parity(n_boot_paths=20)
    assert checks["all"]["pass"], json.dumps(checks["all"], default=str)
    assert checks["main_window"]["pass"] and checks["bootstrap"]["pass"]
    assert checks["margin"]["pass"] and checks["engine_anchors"]["pass"]
