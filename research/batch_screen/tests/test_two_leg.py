"""The two-leg kernel's framework integration: the plugin contract, the routing, availability.

The engine-level parity cells (leg B == 0 bit-for-bit against `research/fast_engine`, and the
`riskoff_after_tax_v1` reference case) live in `research/fast_engine2/fe2_parity.py` and
`research/two_leg_kernel_v1/parity_b.py`; these tests cover what the *framework* adds: the
`(wants_a, wants_b, leg_b)` contract, `leg_series`/`leg_b_usable`, the `stages.run_one` routing,
and that the bootstrap draws the same paths for a two-leg arm and the one-leg control.
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
from research.fast_engine import fe_boot, fe_study

HERE = Path(__file__).resolve().parent
PROBE = HERE.parent / "probes" / "two_leg.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("probe_two_leg_t", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def probe_arm(probe, arm_id):
    arm = api.resolve_arm(probe, arm_id)
    arm.update({"id": f"two_leg/{arm_id}", "idea": "two_leg", "arm_id": arm_id, "mod": probe})
    return arm


# ----------------------------------------------------------------------------- the contract
def test_two_leg_return_value_requires_the_spec_declaration(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    p = tiny_panel()
    body = ("import numpy as np\nARMS = {'a': {%s}}\n\n\n"
            "def targets(data, spec):\n"
            "    w = np.zeros((data.P, data.T))\n"
            "    return w, w, spec.get('leg_b', '1x')\n")
    write_idea(tmp_path, "two", body % "'leg_b': '1x'")
    arm = api.resolve_arm(api.load_idea("two"), "a")
    assert arm["two_leg"] and arm["leg_b"] == "1x"
    t = api.call_targets(api.load_idea("two"), p, dict(arm, params={"leg_b": "1x"}))
    assert t.two_leg and t.leg_b == "1x" and t.wants_b.shape == (p.P, p.T)
    # a second leg without the spec declaration is refused
    write_idea(tmp_path, "nodecl", body % "")
    with pytest.raises(api.PluginError, match="no leg_b"):
        api.call_targets(api.load_idea("nodecl"), p, {"params": {}, "leg": "2x"})
    # a declared leg_b with a single-leg return is refused
    write_idea(tmp_path, "missing", "import numpy as np\nARMS = {'a': {'leg_b': '1x'}}\n\n\n"
                                    "def targets(data, spec):\n"
                                    "    return np.zeros((data.P, data.T))\n")
    with pytest.raises(api.PluginError, match="single-leg"):
        api.call_targets(api.load_idea("missing"), p, {"params": {"leg_b": "1x"}, "leg": "2x"})
    # a returned leg_b that disagrees with the spec is refused
    write_idea(tmp_path, "mismatch", body % "'leg_b': 'shy'")
    with pytest.raises(api.PluginError, match="declares"):
        api.call_targets(api.load_idea("mismatch"), p,
                         {"params": {"leg_b": "1x"}, "leg_b": "shy", "leg": "2x"})
    # leg B may not be leg A's own instrument (one FIFO book per instrument)
    write_idea(tmp_path, "same", body % "'leg': '2x', 'leg_b': '2x'")
    with pytest.raises(api.PluginError, match="own instrument"):
        api.resolve_arm(api.load_idea("same"), "a")
    # an unknown leg_b key is refused at resolve time
    write_idea(tmp_path, "bad", "import numpy as np\nARMS = {'a': {'leg_b': 'nope'}}\n\n\n"
                                "def targets(data, spec):\n"
                                "    w = np.zeros((data.P, data.T))\n"
                                "    return w, w, 'nope'\n")
    with pytest.raises(api.PluginError, match="unknown leg_b"):
        api.resolve_arm(api.load_idea("bad"), "a")


def test_two_leg_nan_weights_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    p = tiny_panel()
    write_idea(tmp_path, "nanb", "import numpy as np\nARMS = {'a': {'leg_b': '1x'}}\n\n\n"
                                 "def targets(data, spec):\n"
                                 "    w = np.zeros((data.P, data.T))\n"
                                 "    return w, np.full_like(w, np.nan), '1x'\n")
    with pytest.raises(api.PluginError, match="non-finite leg-B"):
        api.call_targets(api.load_idea("nanb"), p, {"params": {"leg_b": "1x"}, "leg": "2x"})


def test_leg_series_and_availability(probe):
    days, p, i0, i1 = windows.main_panel(0.0)
    o1, c1 = p.leg_series("1x")
    assert np.array_equal(o1, p.legs["1x"][0]) and np.array_equal(c1, p.legs["1x"][1])
    og, cg = p.leg_series("gld")
    assert og is cg  # an extra series is its own open and close
    assert np.isnan(og[0, 0]) and np.isfinite(og[0, -1])
    assert p.leg_b_usable("1x", i0, i1)[0]
    ok, why = p.leg_b_usable("gld", i0, i1)
    assert not ok and days[i0 - 1] in why and "2004-11-18" in why


# ----------------------------------------------------------------------------- the routing
def test_two_leg_arm_with_an_empty_leg_b_is_the_control(probe):
    """`leg A` production, `leg B` a zero target on the panel's 1x leg: the control, bit for bit."""
    days, p, i0, i1 = windows.main_panel(0.0)
    env = windows.try_env(days, i0, i1)
    ctrl = api.load_arm("k_control", "K")
    arm = probe_arm(probe, "zeros")
    for mode, capital in (("pre_tax", 1.0), ("progressive", 100_000.0)):
        rc = stages.run_one(ctrl, p, env, i0, i1, mode, 2, capital)
        rz = stages.run_one(arm, p, env, i0, i1, mode, 2, capital)
        assert np.array_equal(rc["_nav"], rz["_nav"])
        assert rc["cagr_usd_liquidated"] == rz["cagr_usd_liquidated"]
        assert rc["turnover_total"] == rz["turnover_total"]
        assert rz["_legs"]["B"]["trades"] == 0 and rz["_legs"]["B"]["units_end"] == 0.0


def test_two_leg_arm_reaches_fast_engine2(probe):
    """The split arm really holds two instruments: leg B trades and realises its own gains."""
    days, p, i0, i1 = windows.main_panel(0.0)
    env = windows.try_env(days, i0, i1)
    arm = probe_arm(probe, "split")
    r = stages.run_one(arm, p, env, i0, i1, "progressive", 2, 100_000.0)
    a, b = r["_legs"]["A"], r["_legs"]["B"]
    assert a["trades"] > 0 and b["trades"] > 0
    assert a["turnover_total"] > 0 and b["turnover_total"] > 0
    assert sum(b["realised_gain_try_by_reason"].values()) != 0.0
    # `sells` = strategy sells + tax-funding sales (the engine's definition), each split per leg
    assert r["qld_sells"] == a["sells"] + b["sells"]
    assert r["tax_funding_sales"] == a["tax_funding_sales"] + b["tax_funding_sales"]
    assert r["sells"] == r["qld_sells"] + r["tax_funding_sales"]
    assert r["strategy_trades"] == a["trades"] + b["trades"]
    assert a["fees_usd"] > 0 and b["fees_usd"] > 0
    # every fee belongs to one leg's fill (funding sales included), up to float summation order
    assert r["fees_usd"] == pytest.approx(a["fees_usd"] + b["fees_usd"], rel=1e-12)


def test_two_leg_stage1_is_not_applicable_without_leg_b_history(probe):
    arm = probe_arm(probe, "gld_sleeve")
    with pytest.raises(api.NotApplicable, match="gld"):
        stages.stage1(arm, "none")


def test_two_leg_oos_stage_records_the_leg_b_availability(probe):
    arm = probe_arm(probe, "gld_sleeve")
    rec = stages.stage2_oos(arm, "none")
    assert all(v.get("applicable") is False for v in rec.values())
    assert all("gld" in v["reason"] for v in rec.values())
    ok, bad = stages.oos_pooled_markets(arm, "none")
    assert not ok and len(bad) == len(stages.OOS_POOLED_MARKETS)


def test_two_leg_bootstrap_shares_the_control_paths(probe):
    """A two-leg arm in the same call must not disturb the one-leg arms' draw."""
    ctrl = api.load_arm("k_control", "K")
    split = probe_arm(probe, "split")
    both = B.boot([ctrl, split], n_pre=4, n_tax=4, capitals=("100k",),
                  modes=("progressive",), quiet=True)
    alone = B.boot([ctrl], n_pre=4, n_tax=4, capitals=("100k",),
                   modes=("progressive",), quiet=True)
    for md in ("base", "calibrated"):
        assert np.array_equal(both["pre"][md][ctrl["id"]], alone["pre"][md][ctrl["id"]])
    assert np.array_equal(both["tax"]["100k"]["progressive"][ctrl["id"]],
                          alone["tax"]["100k"]["progressive"][ctrl["id"]])
    theirs = fe_boot.boot(seed=fe_boot.SEED, block=fe_boot.BLOCK, arms=("C0", "K"), n_pre=4, n_tax=4,
                          capitals=("100k",), modes=("progressive",), anchor_e2=False,
                          deltas=("C0",), quiet=True, keep_paths=True)
    assert np.array_equal(both["pre"]["base"][ctrl["id"]],
                          theirs["paths"]["pre"][("base", "K")])
    # the two-leg arm's own paths are finite and differ from the control's
    v = both["tax"]["100k"]["progressive"][split["id"]]
    assert np.isfinite(v).all() and not np.array_equal(v, both["tax"]["100k"]["progressive"][ctrl["id"]])


def test_two_leg_after_tax_cells_and_models(probe):
    """The after-tax stages work for a two-leg arm (progressive and flat, YI-UFE and instalments)."""
    days, p, i0, i1 = windows.main_panel(0.0)
    arm = probe_arm(probe, "split")
    a, b = stages.TAX_WINDOWS["2000_03_to_2026_09"]
    j0, j1 = fe_study.window_indices(days, a, b)
    cell = stages.after_tax_cells(arm, p, days, j0, j1, "none")
    assert cell["pre_tax_cagr"] > 0 and cell["after_tax_cagr"] > 0
    assert cell["sells_per_year"] > 0 and np.isfinite(cell["tax_usd"])


# ----------------------------------------------------------------------------- the harness
def test_harness_two_leg_checks_pass():
    checks = run.two_leg_checks()
    flat = {k: v["pass"] for k, v in checks.items() if isinstance(v, dict) and "pass" in v}
    assert flat and all(flat.values()), json.dumps(checks, default=str)[:800]
