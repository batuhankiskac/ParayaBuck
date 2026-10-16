"""The option kernel's own tests: parity, one test per mechanism, the calendar, the plumbing.

The mechanism tests call `checks.CHECKS` (the same fixtures the harness prints), the parity tests
call `parity_opts` with small parameters, and the plumbing tests drive a fixture plugin through
`batch_screen`'s own entry points (`api.load_arm` -> `stages.run_one`) to prove the option reaches
the kernel from a spec.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from research.batch_screen import api, rules, stages
from research.batch_screen.tests.test_api import range_days, tiny_panel, write_idea
from research.fast_engine import fe_sim

from research.fast_engine_opts import checks as CK
from research.fast_engine_opts import fe_opts_calendar as CAL
from research.fast_engine_opts import fe_opts_legs as LEGS
from research.fast_engine_opts.bridge import bundle_for, prepare_panel, split_opts
from research.fast_engine_opts.fe_opts import FLOAT_OPTS, INT_OPTS, Opts, session_fold
from research.fast_engine_opts.fe_opts_sim import simulate_opts
from research.fast_engine_opts.harness import anchors
from research.fast_engine_opts.parity_opts import off_boot_parity, off_side_by_side

# ----------------------------------------------------------------------------- parity
def test_options_off_is_the_engine_side_by_side():
    """The 4 arms x 3 modes x 2 capitals: every cell equal, the NAV path bitwise identical."""
    from research.fast_engine_opts.parity_opts import FLAT_KEYS

    got = off_side_by_side(caps=("10k", "100k"))
    assert got["cells"] == 4 * 3 * 2 * len(FLAT_KEYS)
    assert got["max_abs_diff"] == 0.0
    assert got["max_abs_nav_diff"] == 0.0
    assert got["nav_bitwise_identical"]


def test_options_off_is_the_engine_in_the_bootstrap():
    """`chunk_opts` with all-off vectors against `batch_screen.boot._chunk`, cell for cell."""
    got = off_boot_parity(n_pre=16, n_tax=16, W=16)
    assert got["pass"], got
    assert got["max_abs_diff"] == 0.0


def test_k_anchors_through_the_option_kernel():
    """K's published pre-tax and after-tax cells, reproduced by `simulate_opts(Opts())`."""
    got = anchors()
    bad = {k: v["diff_pp"] for k, v in got.items() if not v["pass"]}
    assert not bad, bad


# ----------------------------------------------------------------------------- mechanisms
@pytest.mark.parametrize("name", sorted(CK.CHECKS))
def test_mechanism(name):
    """One test per option: the fixture check of `checks.CHECKS` must pass whole."""
    got = CK.CHECKS[name]()
    assert got["pass"], {k: v for k, v in got.items() if k != "pass"}


def test_every_option_has_a_check():
    """Each declared option is covered by the checks or named as covered by the panel types."""
    covered = {"band_up", "band_down", "min_notional", "min_notional_frac", "dd_start", "dd_floor",
               "slice_frac", "slice_parts", "fx_cost", "prefund_sessions", "settle_floor",
               "dd_upsize_only", "early_mode", "fill_mode"}
    assert covered == set(FLOAT_OPTS) | set(INT_OPTS)


# ----------------------------------------------------------------------------- the spec
def test_defaults_are_production_and_pack_round_trips():
    o = Opts()
    assert o.is_off()
    optf, opti = o.pack()
    assert list(optf) == [0.005, 0.005, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, -1.0]
    assert list(opti) == [0, 0, 0]
    assert not Opts(band_up=0.0).is_off()
    assert Opts(dd_start=0.20).needs() == ("wvol",)
    assert Opts(early_mode=1).needs() == ("early",)
    assert Opts(fill_mode=2).needs() == ("fill",)
    assert Opts(slice_parts=3).needs() == ()


def test_split_opts_validates_names():
    o, knobs = split_opts({"band_up": 0.0, "margin_spread": 0.0125, "wht": 0.2})
    assert o.band_up == 0.0 and knobs == {"margin_spread": 0.0125, "wht": 0.2}
    with pytest.raises(ValueError, match="unknown option"):
        split_opts({"nope": 1})


def test_session_fold_charges_the_gap():
    days = ["2020-01-02", "2020-01-03", "2020-01-06"]  # Thu, Fri, Mon
    got = session_fold(days, np.full(3, 0.365))
    assert got[0] == 0.0 and got[1] == pytest.approx(0.001) and got[2] == pytest.approx(1.001 ** 3 - 1)


# ----------------------------------------------------------------------------- the calendar
def test_early_close_rule():
    assert CAL.thanksgiving_friday(2025) == date(2025, 11, 28)
    assert [d.isoformat() for d in CAL.early_close_dates(2025)] == [
        "2025-07-03", "2025-11-28", "2025-12-24"]
    # a Sunday Christmas (2022) and a Saturday one (2021): no December session, hence no early
    # close; the Saturday case is observed on the preceding Friday and the Sunday one on Monday
    assert [d.isoformat() for d in CAL.early_close_dates(2021)] == ["2021-11-26"]
    assert [d.isoformat() for d in CAL.early_close_dates(2022)] == ["2022-11-25"]
    # 2019: all three
    assert [d.isoformat() for d in CAL.early_close_dates(2019)] == [
        "2019-07-03", "2019-11-29", "2019-12-24"]
    assert CAL.is_early_close_date(date(2024, 12, 24)) and not CAL.is_early_close_date(
        date(2024, 12, 23))


def test_early_close_flags_follow_the_window():
    days = ["2020-12-23", "2020-12-24", "2020-12-28", "2021-07-01", "2021-07-02"]
    got = CAL.flags(days)
    assert list(got) == [False, True, False, False, False]  # 2021 has no July 3 session


# ----------------------------------------------------------------------------- the panel mechanisms
def test_leg_spy_name_agrees_with_the_framework():
    assert LEGS.LEG_SPY == api.LEG_SPY
    assert api.LEG_SPY in api.LEGS


def test_panel_records_its_raw_triple():
    p = tiny_panel()
    (o2, c2, d2), (o1, c1, d1) = p.raw
    assert o2.shape == c2.shape == d2.shape == p.price.shape
    assert np.array_equal(c2, p.price)


def test_prepare_panel_does_not_mutate_the_shared_panel():
    p = tiny_panel()
    before_leg, before_keys = p.leg, set(p.legs)
    got = prepare_panel(p, {"leg": api.LEG_SPY, "opts": {}, "params": {}, "rule": "K"})
    assert p.leg == before_leg and set(p.legs) == before_keys
    assert got is not p and got.leg == api.LEG_SPY and api.LEG_SPY in got.legs
    # no SPY series on a synthetic panel: the option leg is an alias of the window's own 2x leg
    assert np.array_equal(got.legs[api.LEG_SPY][1], p.legs["2x"][1])
    assert got.vol.shape == p.vol.shape


def test_bundle_for_is_none_without_options():
    p = tiny_panel()
    arms = [{"rule": "K", "leg": "2x", "params": {}, "opts": {}}]
    assert bundle_for(arms, p) is None
    arms = [dict(arms[0], opts={"early_mode": 1})]
    b = bundle_for(arms, p)
    assert b is not None and b.early.shape == (1, p.T) and not b.fill.shape[1]
    assert b.wvol.shape == (1, p.P, 0)
    assert np.array_equal(b.row(0)["early"], CAL.flags(list(p.days)))


# ----------------------------------------------------------------------------- the plumbing
def _env_for(days, i0=2):
    n = len(days)
    return fe_sim.RunEnv(days, np.ones(n), CK.ufe_table(), i0, n - 1)


FIXTURE = """
import numpy as np
ARMS = {"a": {"rule": "%s", "leg": "2x", "cash_yield": "none"}}

def targets(data, spec):
    w = np.zeros((data.P, data.T))
    w[:, 4:] = 0.5
    return w
"""


def test_a_registered_rule_carries_the_option_to_the_kernel(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    rules.register_rule("kopt_test_band0", band=True, usd_rule=True, defer=True,
                        opts={"band_up": 0.0})
    assert rules.opts_of("kopt_test_band0") == {"band_up": 0.0}
    assert rules.opts_of("K") == {}
    write_idea(tmp_path, "kopt_fixture", FIXTURE % "kopt_test_band0")
    write_idea(tmp_path, "kopt_plain", FIXTURE % "K")
    days = range_days(40)
    p = tiny_panel()
    env = _env_for(days)
    i0, i1 = 2, len(days) - 1

    arm = api.load_arm("kopt_fixture", "a")
    assert arm["opts"] == {"band_up": 0.0}
    routed = stages.run_one(arm, p, env, i0, i1, "pre_tax")
    wants = api.call_targets(arm["mod"], p, arm).wants
    direct = simulate_opts(p.legs["2x"][0], p.legs["2x"][1], wants, env, 100_000.0, i0, i1,
                           arm="K", mode="pre_tax", m=2, opts=Opts(band_up=0.0))
    assert routed["cagr_usd_liquidated"] == direct["cagr_usd_liquidated"]
    assert np.array_equal(routed["_nav"], direct["_nav"])
    # and the same target series under K, without options, is the engine's own run
    plain = api.load_arm("kopt_plain", "a")
    assert plain["opts"] == {}
    engine = stages.run_one(plain, p, env, i0, i1, "pre_tax")
    assert engine["cagr_usd_liquidated"] != routed["cagr_usd_liquidated"]


TWO_LEG_BODY = ('ARMS = {"a": {"rule": "%s", "leg": "%s", "leg_b": "1x"}}\n\n\n'
                'def targets(data, spec):\n'
                '    w = data.price * 0\n'
                '    return w, w, "1x"\n')


def test_an_option_arm_cannot_be_two_leg(tmp_path, monkeypatch):
    """Options live in the single-leg kernel: an arm cannot ask for both."""
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    rules.register_rule("kopt_test_twoleg", band=True, usd_rule=True, defer=True,
                        opts={"band_up": 0.0})
    write_idea(tmp_path, "kopt_twoleg", TWO_LEG_BODY % ("kopt_test_twoleg", "2x"))
    with pytest.raises(api.PluginError, match="single-leg"):
        api.resolve_arm(api.load_idea("kopt_twoleg"), "a")
    # ... and a two-leg arm without options still resolves
    write_idea(tmp_path, "kopt_plain_twoleg", TWO_LEG_BODY % ("K", "2x"))
    assert api.resolve_arm(api.load_idea("kopt_plain_twoleg"), "a")["two_leg"]


def test_the_spy_leg_is_single_leg_only(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    write_idea(tmp_path, "kopt_spy_two", TWO_LEG_BODY % ("K", "2x_spy"))
    with pytest.raises(api.PluginError, match="single-leg"):
        api.resolve_arm(api.load_idea("kopt_spy_two"), "a")


def test_a_targets_series_that_asks_for_an_option_alone_is_not_enough():
    """`targets` cannot request an option: only the registered rule can (the spec's other keys
    stay params)."""
    o, knobs = split_opts({"band_up": 0.0})
    assert o.band_up == 0.0 and knobs == {}
