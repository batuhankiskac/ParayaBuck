"""The reduced-window option (`spec["start"]`, README section 2a).

The *rules* on synthetic input plus two real-data cells (the default pool is the published draw,
and a restricted pool makes a late-starting leg B usable). The end-to-end proof -- a real batch
with a reduced-window arm, its own `_control_<tag>.json` and the paired Δ -- is
`probes/run_probe_batch.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from research.gate_power_v2 import api, run, stages, windows
from research.gate_power_v2 import boot as B
from research.gate_power_v2.tests.test_api import write_idea
from research.fast_engine.fe_config import START

HERE = Path(__file__).resolve().parent
PROBE = HERE.parent / "probes" / "two_leg.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("probe_two_leg_w", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def probe_arm(probe, arm_id):
    arm = api.resolve_arm(probe, arm_id)
    arm.update({"id": f"two_leg/{arm_id}", "idea": "two_leg", "arm_id": arm_id, "mod": probe})
    return arm


# ----------------------------------------------------------------------------- the rules
def test_no_start_is_the_published_window():
    """Opt-in: an arm that declares nothing is untouched, `leg_b` or not."""
    days = windows.bars()[0]
    for arm in ({"leg": "2x"}, {"leg": "2x", "leg_b": "gld"}, {"leg": "2x", "inputs": ["ief"]}):
        win = windows.arm_window(arm, days)
        assert win is windows.DEFAULT_WINDOW and not win.reduced and win.i0 == START
    assert windows.boot_pool(days, windows.DEFAULT_WINDOW) == (0, len(days) - 2)


def test_auto_is_the_last_input_plus_one_session():
    days = windows.bars()[0]
    win = windows.arm_window({"start": "auto", "leg_b": "gld", "inputs": ["ief"]}, days)
    j_gld = windows.input_start_index(days, "gld")
    j_ief = windows.input_start_index(days, "ief")
    assert win.i0 == max(j_gld, j_ief) + 1 and win.inputs == ("ief", "gld")
    assert win.tag == f"w{days[win.i0].replace('-', '')}" and win.reduced
    assert win.as_dict() == {"start": win.i0, "tag": win.tag, "declared": "auto",
                             "inputs": ["ief", "gld"]}


def test_an_auto_input_with_no_data_is_not_applicable():
    # GLD starts 2004-11-18: a panel that ends in 1999 has no value for it at all
    with pytest.raises(api.NotApplicable, match="has no value"):
        windows.arm_window({"start": "auto", "inputs": ["gld"]}, ["1999-01-04", "1999-01-05"])
    # a declared window past the end of the panel is refused rather than indexed
    with pytest.raises(api.NotApplicable, match="past the end"):
        windows.arm_window({"start": "auto", "inputs": ["vix"]}, ["1999-01-04", "1999-01-05"])


def test_explicit_date_and_the_warmup_floor():
    days = windows.bars()[0]
    win = windows.arm_window({"start": "2015-01-02"}, days)
    assert days[win.i0] >= "2015-01-02" and win.reduced
    assert win.tag == f"w{days[win.i0].replace('-', '')}" and win.declared == "2015-01-02"
    # a declared date before the published warm-up start cannot move the window earlier
    early = windows.arm_window({"start": "1999-03-11"}, days)
    assert early.i0 == START and early.tag == "default" and early.declared == "1999-03-11"


def test_a_window_shorter_than_ten_years_is_not_applicable():
    days = windows.bars()[0]
    with pytest.raises(api.NotApplicable, match="less than"):
        windows.arm_window({"start": "2017-06-01"}, days)
    # ten years is inside: 2016-09-29 -> 2026-09-29 is the line
    win = windows.arm_window({"start": "2016-09-29"}, days)
    assert win.reduced


def test_start_and_inputs_are_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    head = "import numpy as np\nARMS = {{'a': {{'start': {start}}}}}\n\n\n"
    tail = "def targets(data, spec):\n    return np.zeros((data.P, data.T))\n"
    write_idea(tmp_path, "badstart", (head + tail).format(start="'yesterday'"))
    with pytest.raises(api.PluginError, match="YYYY-MM-DD"):
        api.resolve_arm(api.load_idea("badstart"), "a")
    write_idea(tmp_path, "badinput",
               (head.replace("'start': {start}", "'start': 'auto', 'inputs': {start}") + tail)
               .format(start="['nope']"))
    with pytest.raises(api.PluginError, match="unknown input"):
        api.resolve_arm(api.load_idea("badinput"), "a")
    # the declaration reaches `params` (the registry pins the whole spec)
    write_idea(tmp_path, "okstart",
               (head.replace("'start': {start}", "'start': '2015-01-02', 'inputs': {start}") + tail)
               .format(start="['ief']"))
    arm = api.load_arm("okstart", "a")
    assert arm["start"] == "2015-01-02" and arm["inputs"] == ("ief",)
    assert arm["params"]["start"] == "2015-01-02" and arm["params"]["inputs"] == ["ief"]


# ----------------------------------------------------------------------------- the stages
def test_after_tax_windows_are_clipped_not_dropped():
    days = windows.bars()[0]
    win = windows.arm_window({"start": "2004-11-19"}, days)
    at = windows.after_tax_windows(days, win)
    assert set(at) == set(("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end"))
    assert at["2000_03_to_2026_09"][0] == win.i0                       # clipped up
    assert at["2010_01_to_end"][0] == windows.rolling_start_index(days, 2010)   # untouched
    assert at["2017_01_to_end"][0] == windows.rolling_start_index(days, 2017)
    for k, (j0, j1) in at.items():
        assert j0 >= win.i0 and j1 == len(days) - 1


def test_rolling_starts_before_the_window_are_dropped():
    days = windows.bars()[0]
    win = windows.arm_window({"start": "2004-11-19"}, days)
    starts = windows.rolling_starts(days, win)
    assert sorted(starts, key=int) == [str(y) for y in range(2005, 2022)]
    assert all(starts[y] == windows.rolling_start_index(days, int(y)) for y in starts)


def test_stage1_records_the_window_and_clips_the_after_tax_windows():
    days = windows.bars()[0]
    arm = api.load_arm("k_control", "K")
    win = windows.Window(i0=windows.rolling_start_index(days, 2015), tag="w2015",
                         declared="2015-01-02", inputs=())
    rec = stages.stage1(arm, "none", win=win)
    assert rec["window"]["start"] == days[win.i0] and rec["win"]["tag"] == "w2015"
    assert rec["win"]["declared"] == "2015-01-02" and rec["win"]["inputs"] == []
    at = windows.after_tax_windows(days, win)
    for k, (j0, j1) in at.items():
        assert rec["after_tax"][k]["window"] == [days[j0], days[j1]]
    assert rec["after_tax"]["2000_03_to_2026_09"]["window"][0] == days[win.i0]     # clipped up
    assert rec["after_tax"]["2010_01_to_end"]["window"][0] == days[win.i0]         # clipped up
    assert rec["after_tax"]["2017_01_to_end"]["window"][0] == "2017-01-03"         # after the window
    assert rec["main"]["base"]["m2"]["cagr"] > 0
    # the 2015 window covers P2/P3 only (`seg` cannot split a window it starts inside)
    assert rec["main"]["base"]["m2"]["subperiods_not_covered"] == ["P1"]
    assert set(rec["main"]["base"]["m2"]["subperiods"]) == {"P2", "P3"}
    # the window really moved: the published one starts 2000-03-09
    assert (rec["window"]["start"], rec["window"]["end"]) == (days[win.i0], days[-1])
    assert days[windows.DEFAULT_WINDOW.i0] == "2000-03-09" and win.i0 > windows.DEFAULT_WINDOW.i0


# ----------------------------------------------------------------------------- the bootstrap
def test_the_bootstrap_pool_is_the_window_and_the_default_pool_is_published():
    days = windows.bars()[0]
    arm = api.load_arm("k_control", "K")
    win = windows.arm_window({"start": "2004-11-19"}, days)
    pool = windows.boot_pool(days, win)
    assert pool == (win.i0 - 1, len(days) - 2)
    bw = B.BootWindow("none", pool)
    assert bw.npool == 6931 and bw.npool_draw == pool[1] - pool[0] + 1 and bw.pool_lo == pool[0]
    pos = bw.positions(np.random.default_rng(20261011), 20, 8)
    assert pos.shape == (8, 2773 + 3) and pos.min() >= pool[0] and pos.max() <= pool[1]
    bad_pool = (len(days) - 2, len(days) - 1)
    with pytest.raises(ValueError, match="outside the return pool"):
        B.BootWindow("none", bad_pool)
    # the published pool is what `pool=None` draws, bit for bit
    a = B.boot([arm], n_pre=8, n_tax=8, pool=None, quiet=True)
    b = B.boot([arm], n_pre=8, n_tax=8, pool=windows.boot_pool(days, windows.DEFAULT_WINDOW),
               quiet=True)
    for md in ("base", "calibrated"):
        assert np.array_equal(a["pre"][md][arm["id"]], b["pre"][md][arm["id"]])
    for md in ("progressive", "flat"):
        assert np.array_equal(a["tax"]["100k"][md][arm["id"]], b["tax"]["100k"][md][arm["id"]])
    assert b["params"]["pool"] == [0, len(days) - 2] and a["params"]["pool"] is None


def test_a_restricted_pool_makes_a_late_leg_b_usable(probe):
    """The IC31-IC37 unlock: GLD has no price before 2004-11-18, so only a reduced window works."""
    days = windows.bars()[0]
    win = windows.arm_window(probe_arm(probe, "gld_offstate"), days)
    bw = B.BootWindow("none", windows.boot_pool(days, win))
    pos = bw.positions(np.random.default_rng(20261011), 20, 4)
    p = B.boot_panels(bw, pos, ("2x",), (0.0,))[("2x", 0.0)]
    assert p.leg_b_usable("gld", bw.i0, bw.i1)[0]
    pub = B.BootWindow("none")
    p0 = B.boot_panels(pub, pub.positions(np.random.default_rng(20261011), 20, 4), ("2x",),
                       (0.0,))[("2x", 0.0)]
    ok, why = p0.leg_b_usable("gld", pub.i0, pub.i1)
    assert not ok and "gld" in why


def test_control_file_and_batch_windows():
    days = windows.bars()[0]
    win = windows.arm_window({"start": "2004-11-19"}, days)
    assert run.control_file(Path("."), windows.DEFAULT_WINDOW).name == "_control.json"
    assert run.control_file(Path("."), win).name == f"_control_{win.tag}.json"
    arms = [api.load_arm("k_control", "K"),
            {"start": "auto", "leg_b": "gld", "inputs": (), "leg": "2x"}]
    wins = run.batch_windows(arms)
    assert sorted(wins) == ["default", win.tag]
    assert wins[win.tag].i0 == win.i0
    # an arm whose declared window is not evaluable is left out (its own record says so)
    assert sorted(run.batch_windows([{"start": "auto", "inputs": [], "leg": "2x"},
                                     {"start": "2024-01-01", "leg": "2x"}])) == ["default"]


def test_a_smoke_of_a_reduced_arm_reports_its_window(tmp_path, monkeypatch):
    from research.gate_power_v2 import smoke

    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    write_idea(tmp_path, "red", ("import numpy as np\nARMS = {'a': {'start': '2015-01-02'}}\n\n\n"
                                 "def targets(data, spec):\n"
                                 "    return np.zeros((data.P, data.T))\n"))
    r = smoke.smoke_arm("red", "a")
    assert r["status"] == "ok" and r["win"]["tag"] == windows.win_tag(
        windows.bars()[0], windows.rolling_start_index(windows.bars()[0], 2015))
    assert r["panels"]["main"]["status"] == "ok" and r["panels"]["boot"]["status"] == "ok"
    assert "window" in smoke.format_arm(r)
    # the panels are cached per (leg, window tag)
    assert smoke.smoke_arm("red", "a") is not r
    assert smoke.smoke_panels(api.DEFAULT_LEG, windows.DEFAULT_WINDOW) is smoke.smoke_panels()
