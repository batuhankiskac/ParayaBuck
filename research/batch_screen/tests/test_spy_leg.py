"""The `2x_spy` option leg's panel plumbing (regression for `research/plugins_d3/BUGS.md` B1).

`api.panel` builds only the window's own `2x`/`1x` legs, so every call site that builds a panel
must ask for `bridge.base_leg(arm["leg"])` and let `bridge.prepare_panel` add the option leg
(`stages.run_one` / `boot.boot` already do). Building a panel with the arm's own `2x_spy` raises
`KeyError` inside `MarketData.__post_init__`, before `targets` is ever reached -- which is why
`IC40`'s three arms were unrunnable.

The arm used is the framework's own `IC40` (`leg = "2x_spy"`), so the test covers the real call
sites: the smoke's panels, `stages.stage1` (the main window) and `stages.stage2_oos` (the OOS
windows, where the option leg degenerates to the window's own `2x`).
"""

from __future__ import annotations

import numpy as np
import pytest

from research.batch_screen import api, smoke, stages, windows
from research.fast_engine_opts import bridge

SPY_RULE = "2x_spy"


def test_the_panel_leg_is_the_option_leg_s_base_leg():
    arm = api.load_arm("IC40", "A")
    assert arm["leg"] == SPY_RULE == api.LEG_SPY
    assert stages.panel_leg(arm) == "2x" == bridge.base_leg(SPY_RULE)
    assert stages.panel_leg(api.load_arm("k_control", "K")) == "2x"
    assert stages.panel_leg(api.load_arm("r2_control", "R2")) == "2x"
    # the bug the call sites must not repeat: the leg does not exist while the panel is built
    with pytest.raises(KeyError):
        windows.main_panel(0.0, leg=SPY_RULE)


def test_the_prepared_panel_offers_the_option_leg_and_runs():
    arm = api.load_arm("IC40", "A")
    days, raw, i0, i1 = windows.main_panel(0.0, leg=stages.panel_leg(arm))
    prep = bridge.prepare_panel(raw, arm)
    assert prep.leg == SPY_RULE and SPY_RULE in prep.legs and prep.P == 1
    assert not np.array_equal(prep.legs[SPY_RULE][1], prep.legs["2x"][1])
    env = windows.try_env(days, i0, i1)
    # `run_one` is what adds the option leg to a panel the framework built for the base leg
    r = stages.run_one(arm, raw, env, i0, i1, "pre_tax", 2)
    assert np.isfinite(r["cagr_usd_liquidated"]) and r["cagr_usd_liquidated"] > 0


def test_stage1_and_the_oos_windows_run_the_option_leg_arm():
    arm = api.load_arm("IC40", "A")
    rec = stages.stage1(arm, "none")
    assert np.isfinite(rec["main"]["base"]["m2"]["cagr"]) and rec["main"]["base"]["m2"]["max_dd"] > 0
    assert sorted(rec["after_tax"]) == ["2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end"]
    oos = stages.stage2_oos(api.load_arm("IC40", "C"))
    assert oos["M1"].get("applicable", True) is not False     # ^GSPC covers M1
    assert oos["M3"]["applicable"] is False
    assert "S&P 500 series" in oos["M3"]["reason"]


def test_smoke_reaches_targets_on_every_panel_kind():
    for arm_id in ("A", "C"):
        r = smoke.smoke_arm("IC40", arm_id)
        assert r["status"] == "ok", (arm_id, r)
        assert r["panels"]["main"]["status"] == "ok"
        for name, cell in r["panels"].items():
            assert cell["status"] == "ok", (arm_id, name, cell)
            assert cell["leg"] == SPY_RULE
            assert np.isfinite([cell["wants_min"], cell["wants_max"]]).all()
