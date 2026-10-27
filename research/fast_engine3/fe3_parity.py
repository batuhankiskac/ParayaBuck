"""fe3_parity -- the N-leg kernel's parity cells.

Three identities, each on the NAV path *and* on every reported scalar (`max_abs_diff == 0.0`):

  (a) **N-leg with legs 2..N identically zero** vs `research/fast_engine.fe_sim.simulate`
      (pre-tax, progressive, flat; m = 1/2/4; base and calibrated; the after-tax windows at three
      capitals; a non-zero idle-cash schedule; the clean block bootstrap);
  (b) **N-leg with legs 3..N identically zero** vs `research/fast_engine2.fe2_sim.simulate2`, both
      with leg B at zero (the K anchors) and with a real leg B (`2x` + `1x`);
  (c) **margin off** is both of the above (the `mrate` array is empty), and **margin on a single
      leg** reproduces `research.fast_engine_opts.fe_opts_sim.simulate_opts`' margin cells;
  (d) **the gain-aware downsize router** (`route_cap`/`route_score`, `fe2_opts`'s `route_cap`
      generalised to N legs) with `route_score = indexed` reproduces
      `research.fast_engine2.fe2_opts.simulate2(knobs={"route_cap_pp": ...})` bit for bit on two
      legs, its `route_cap = 0` off-state is the published kernel, and a two-book-of-one-instrument
      run keeps two separate FIFO books.

`engine_parity()` additionally runs the engine's own `python -m research.fast_engine.parity
--quick` (the required PASS) and the engine's published K anchors through `fe_study`.
"""

from __future__ import annotations

import subprocess
import sys

import numpy as np

from research.fast_engine import fe_boot, fe_data, fe_sim, fe_study
from research.fast_engine.fe_config import FIN, TAX_CAPITALS, TAX_WINDOWS
from research.fast_engine_opts import fe_opts_sim
from research.fast_engine_opts.fe_opts import session_fold

from . import fe3_boot, fe3_sim

ARMS = ("C0", "A1u", "H2", "K")
MODES = ("pre_tax", "progressive", "flat")
NRT = fe3_sim.N_LEGS  # 5
MARGIN_SPREAD = 0.015


def _maxdiff(a, b, path=""):
    """The largest absolute difference between two `simulate` dicts, with the offending path."""
    worst, where = 0.0, ""
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if isinstance(k, str) and k.startswith("_"):
                continue
            d, w = _maxdiff(a.get(k), b.get(k), f"{path}.{k}")
            if d > worst:
                worst, where = d, w
    elif isinstance(a, (list, tuple, np.ndarray)):
        if isinstance(a, (list, tuple)) and (a and isinstance(a[0], str)):
            return (0.0, "") if list(a) == list(b) else (float("inf"), path)
        aa, bb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
        if aa.shape != bb.shape:
            return float("inf"), f"{path}.shape({aa.shape}!={bb.shape})"
        if aa.size:
            k = int(np.argmax(np.abs(aa - bb)))
            worst, where = float(np.abs(aa - bb).ravel()[k]), f"{path}[{k}]"
    elif isinstance(a, bool) or a is None:
        worst = 0.0 if a == b else float("inf")
        where = path if worst else ""
    elif isinstance(a, (int, float, np.integer, np.floating)):
        worst = abs(float(a) - float(b))
        where = path if worst else ""
    return worst, where


def _cell(a, b):
    nav = float(np.max(np.abs(a["_nav"] - b["_nav"])))
    scal, where = _maxdiff(a, b)
    return {"nav_max_abs_diff": nav, "scalar_max_abs_diff": scal, "scalar_worst_key": where,
            "pass": bool(nav == 0.0 and scal == 0.0)}


def _zeros(n):
    return np.zeros(n, dtype=np.float64)


def _pad4(rows, n=NRT):
    """`n` rows of the given 1-D series padded with zeros to `NRT` legs."""
    z = _zeros(rows[0].shape[0])
    return [*rows, *([z] * (n - len(rows)))]


def _leg_panels():
    """The main window's `2x`/`1x` legs as `(open, close)` 1-D pairs."""
    from research.batch_screen import windows

    p = windows.main_panel(0.0)[1]
    o2, c2 = p.legs["2x"]
    o1, c1 = p.legs["1x"]
    return (o2[0], c2[0]), (o1[0], c1[0])


def main_window(ms=(1, 2, 4), arms=ARMS, modes=MODES):
    """Main window: the engine identity (a) and the two-leg identity (b), every rule and mode."""
    from research.fast_engine2 import fe2_sim

    out = {}
    (oa, ca), (ob, cb) = _leg_panels()
    for fin, model in ((0.0, "base"), (FIN["calibrated"], "calibrated")):
        days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(fin)
        fx, ufe = fe_study.try_market(days)
        i0, i1 = fe_study.START, len(days) - 1
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        for arm in arms:
            for mode in modes:
                for m in ms:
                    a = fe_sim.simulate(o, c, w_r2, env, 100_000.0, i0, i1, arm=arm, mode=mode, m=m)
                    b = fe3_sim.simulate3(_pad4([o]), _pad4([c]), _pad4([w_r2]), env, 100_000.0,
                                          i0, i1, arm=arm, mode=mode, m=m)
                    out[f"engine_{model}_{arm}_{mode}_m{m}"] = _cell(a, b)
        # (b) legs 3..N at zero, leg B the real 1x leg and a half-weight target
        wb = 0.5 * w_r2
        for arm in arms:
            for mode in modes:
                a = fe2_sim.simulate2(oa, ca, w_r2, ob, cb, wb, env, 100_000.0, i0, i1, arm=arm,
                                      mode=mode, m=2)
                b = fe3_sim.simulate3(_pad4([oa, ob]), _pad4([ca, cb]), _pad4([w_r2, wb]), env,
                                      100_000.0, i0, i1, arm=arm, mode=mode, m=2)
                cell = _cell(a, b)
                cell["legs_A_equal"] = bool(a["_legs"]["A"] == b["_legs"]["A"])
                cell["legs_B_equal"] = bool(a["_legs"]["B"] == b["_legs"]["B"])
                cell["pass"] = bool(cell["pass"] and cell["legs_A_equal"] and cell["legs_B_equal"])
                out[f"twoleg_{model}_{arm}_{mode}"] = cell
    return out


def after_tax_windows(capitals=("10k", "100k", "1M"), arms=("C0", "K"),
                      modes=("progressive", "flat")):
    """The three published after-tax windows at several capitals (regime + YI-UFE + instalments)."""
    from research.fast_engine2 import fe2_sim

    out = {}
    days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    z = _zeros(len(days))
    for wn, (a0, b0) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a0, b0)
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        for cap in capitals:
            for arm in arms:
                for mode in modes:
                    capital = TAX_CAPITALS[cap]
                    ra = fe_sim.simulate(o, c, w_r2, env, capital, i0, i1, arm=arm, mode=mode, m=2)
                    rb = fe3_sim.simulate3(_pad4([o]), _pad4([c]), _pad4([w_r2]), env, capital, i0,
                                           i1, arm=arm, mode=mode, m=2)
                    out[f"engine_{wn}_{cap}_{arm}_{mode}"] = _cell(ra, rb)
                    # the same window, two legs at zero vs fe2_sim (identity (b))
                    rc = fe2_sim.simulate2(o, c, w_r2, z, z, z, env, capital, i0, i1, arm=arm,
                                           mode=mode, m=2)
                    out[f"twoleg_{wn}_{cap}_{arm}_{mode}"] = _cell(rc, rb)
    return out


def cash_yield_cell():
    """A non-zero idle-cash schedule (the `cash_yield` knob) with legs 2..N at zero."""
    from research.batch_screen.rules import cash_rates

    days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    i0, i1 = fe_study.START, len(days) - 1
    rate = cash_rates(list(days))
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1, cash_rate=rate)
    z = _zeros(len(days))
    out = {"schedule_positive": {"value": float(rate.max()), "pass": bool(rate.max() > 0)}}
    for arm in ("A1u", "K"):
        ra = fe_sim.simulate(o, c, w_r2, env, 100_000.0, i0, i1, arm=arm, mode="progressive", m=2)
        rb = fe3_sim.simulate3(_pad4([o]), _pad4([c]), _pad4([w_r2]), env, 100_000.0, i0, i1,
                               arm=arm, mode="progressive", m=2)
        out[f"{arm}_cash_on"] = {"cagr": ra["cagr_usd_liquidated"], **_cell(ra, rb)}
    return out


def bootstrap(n_paths=300, arms=("C0", "K")):
    """`fe_boot.boot` and `fe2_boot.boot2` vs `fe3_boot.boot3` (legs 2..N / 3..N at zero)."""
    from research.fast_engine2 import fe2_boot

    theirs = fe_boot.boot(seed=fe_boot.SEED, block=fe_boot.BLOCK, arms=arms, n_pre=n_paths,
                          n_tax=n_paths, capitals=("100k",), modes=("progressive", "flat"),
                          anchor_e2=False, deltas=("C0",), quiet=True, keep_paths=True)
    mine = fe3_boot.boot3([{"id": a, "rule": a, "wants": fe3_boot.leg_a_only_N(NRT)}
                           for a in arms],
                          fe3_boot.chunk_series3(NRT), n_pre=n_paths, n_tax=n_paths,
                          capitals=("100k",), modes=("progressive", "flat"), quiet=True)
    # the two-leg kernel with leg B at zero, on the same drawn paths
    mine2 = fe2_boot.boot2([{"id": a, "rule": a, "wants": fe2_boot.leg_a_only} for a in arms],
                           fe2_boot.chunk_series(), seed=fe_boot.SEED, block=fe_boot.BLOCK,
                           n_pre=n_paths, n_tax=n_paths, capitals=("100k",),
                           modes=("progressive", "flat"), quiet=True)
    out = {"n_paths": n_paths}
    for a in arms:
        for md in ("base", "calibrated"):
            d = float(np.max(np.abs(mine["pre"][md][a] - theirs["paths"]["pre"][(md, a)])))
            out[f"engine_{a}_pre_{md}"] = {"max_abs_diff": d, "pass": bool(d == 0.0)}
            d2 = float(np.max(np.abs(mine["pre"][md][a] - mine2["pre"][md][a])))
            out[f"twoleg_{a}_pre_{md}"] = {"max_abs_diff": d2, "pass": bool(d2 == 0.0)}
        for mode in ("progressive", "flat"):
            d = float(np.max(np.abs(mine["tax"]["100k"][mode][a]
                                    - theirs["paths"]["tax"]["100k"][mode][a])))
            out[f"engine_{a}_tax_{mode}"] = {"max_abs_diff": d, "pass": bool(d == 0.0)}
            d2 = float(np.max(np.abs(mine["tax"]["100k"][mode][a]
                                     - mine2["tax"]["100k"][mode][a])))
            out[f"twoleg_{a}_tax_{mode}"] = {"max_abs_diff": d2, "pass": bool(d2 == 0.0)}
    return out


def _margin_rate(days):
    """`SESSION_FOLD(DTB3 + spread)` of the main window, the bridge's own margin schedule."""
    _d, _arr, y = fe_data.load()
    ann = np.asarray(y, dtype=np.float64) + MARGIN_SPREAD
    assert ann.shape[0] == len(days)
    return session_fold(list(days), ann)


def _strip_opts(d):
    """A result dict without `opts_stats` (the option kernel reports nine keys, the N-leg one)."""
    return {k: v for k, v in d.items() if k != "opts_stats"}


def margin_cells():
    """A single leg over the cap: `simulate3(mrate=...)` vs `simulate_opts(mrate=...)`.

    `wants = min(1, 0.35/vol) * 2` pushes the account past its own cap so a real cash loan exists
    (the IC45-C/IC46-C situation). Margin *off* must be the engine bit for bit, and margin *on*
    must move the NAV and reproduce `fe_opts_sim.simulate_opts`' `margin_usd` to the last bit.
    """
    days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    i0, i1 = fe_study.START, len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
    z = _zeros(len(days))
    mr = _margin_rate(days)
    w_big = np.minimum(2.0 * w_r2, 2.0)
    out = {}
    for mode in ("progressive", "flat", "pre_tax"):
        off_a = fe_opts_sim.simulate_opts(o, c, w_big, env, 100_000.0, i0, i1, arm="K", mode=mode,
                                          m=2)
        off_b = fe3_sim.simulate3(_pad4([o]), _pad4([c]), _pad4([w_big]), env, 100_000.0, i0, i1,
                                  arm="K", mode=mode, m=2)
        out[f"margin_off_{mode}"] = _cell(off_a, off_b)
        on_a = fe_opts_sim.simulate_opts(o, c, w_big, env, 100_000.0, i0, i1, arm="K", mode=mode,
                                         m=2, mrate=mr)
        on_b = fe3_sim.simulate3(_pad4([o]), _pad4([c]), _pad4([w_big]), env, 100_000.0, i0, i1,
                                 arm="K", mode=mode, m=2, mrate=mr)
        cell = _cell(_strip_opts(on_a), _strip_opts(on_b))
        cell["margin_usd_engine"] = float(on_a["opts_stats"]["margin_usd"])
        cell["margin_usd_nleg"] = float(on_b["opts_stats"]["margin_usd"])
        cell["margin_usd_equal"] = bool(cell["margin_usd_engine"] == cell["margin_usd_nleg"])
        cell["loan_is_nonzero"] = bool(cell["margin_usd_engine"] > 0)
        cell["pass"] = bool(cell["pass"] and cell["margin_usd_equal"] and cell["loan_is_nonzero"])
        out[f"margin_on_{mode}"] = cell
    # the loan really bites: margin on moves the after-tax NAV away from margin off
    off = fe3_sim.simulate3(_pad4([o]), _pad4([c]), _pad4([w_big]), env, 100_000.0, i0, i1,
                            arm="K", mode="progressive", m=2)
    on = fe3_sim.simulate3(_pad4([o]), _pad4([c]), _pad4([w_big]), env, 100_000.0, i0, i1,
                           arm="K", mode="progressive", m=2, mrate=mr)
    out["margin_on_moves_the_nav"] = {
        "cagr_off": off["cagr_usd_liquidated"], "cagr_on": on["cagr_usd_liquidated"],
        "pass": bool(not np.array_equal(off["_nav"], on["_nav"])
                     and on["cagr_usd_liquidated"] < off["cagr_usd_liquidated"])}
    return out


def multi_leg_holds_three_books():
    """A three-leg run really holds three books, and legs 3..N at zero add nothing."""
    from research.batch_screen import windows

    days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    i0, i1 = fe_study.START, len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
    z = _zeros(len(days))
    p = windows.main_panel(0.0)[1]
    o1, c1 = p.legs["1x"]
    wb = 0.5 * w_r2
    wc = 0.25 * w_r2
    r = fe3_sim.simulate3(_pad4([o, o1, o1]), _pad4([c, c1, c1]), _pad4([w_r2, wb, wc]), env,
                          100_000.0, i0, i1, arm="K", mode="progressive", m=2)
    legs = r["_legs"]
    return {
        "three_books_trade": {
            "trades": {k: legs[k]["trades"] for k in ("A", "B", "C", "D")},
            "turnover": {k: legs[k]["turnover_total"] for k in ("A", "B", "C", "D")},
            "pass": bool(all(legs[k]["trades"] > 0 and legs[k]["turnover_total"] > 0
                             for k in ("A", "B", "C"))
                         and legs["D"]["trades"] == 0 and legs["D"]["turnover_total"] == 0.0
                         and r["strategy_trades"] == sum(legs[k]["trades"] for k in "ABCD")),
        },
        "n_legs_are_capped": {
            "pass": bool(_raises(lambda: fe3_sim.simulate3(
                _pad4([o, o1, o1, o1, o1, o1], NRT + 1), _pad4([c, c1, c1, c1, c1, c1], NRT + 1),
                _pad4([w_r2, wb, wc, wc, wc, wc], NRT + 1), env, 100_000.0, i0, i1, arm="K",
                mode="pre_tax", m=2))),
            "n_max": NRT,
        },
        "five_books_trade": {
            "pass": bool(_five_books_trade(o, c, o1, c1, w_r2, wb, wc, env, i0, i1)),
            "n_max": NRT,
        },
    }


def _five_books_trade(o, c, o1, c1, w_r2, wb, wc, env, i0, i1):
    """The capacity extension: a five-leg run really holds five books (and a sixth is refused)."""
    z = _zeros(len(o))
    wd = 0.10 * w_r2
    r = fe3_sim.simulate3([o, o1, o, o1, o], [c, c1, c, c1, c], [w_r2, wb, wc, wd, wd], env,
                          100_000.0, i0, i1, arm="K", mode="progressive", m=2)
    legs = r["_legs"]
    ok = bool(all(legs[k]["trades"] > 0 and legs[k]["turnover_total"] > 0 for k in "ABCDE")
              and len(legs) == 5
              and r["strategy_trades"] == sum(legs[k]["trades"] for k in "ABCDE"))
    return bool(ok and _raises(lambda: fe3_sim.simulate3(
        _pad4([o, o1, o, o1, o, z], NRT + 1), _pad4([c, c1, c, c1, c, z], NRT + 1),
        _pad4([w_r2, wb, wc, wd, wd, z], NRT + 1), env, 100_000.0, i0, i1, arm="K",
        mode="pre_tax", m=2)))


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


# ----------------------------------------------------------------------------- the router
def _two_leg_router_env():
    """The two-leg arm the option-copy parity uses: leg A `fe_data.main_inputs`, leg B the 1x.

    Imported from `fe2_opts_parity` so the router cell and the fe2_opts `p3_route_cap` cell run on
    exactly the same two-leg setup (the same leg A R2 target and the same fixed 0.2-weight 1x leg
    B), which is what makes the bit-for-bit comparison meaningful.
    """
    from research.fast_engine2 import fe2_opts_parity

    days, env, la, lb, i0, i1 = fe2_opts_parity._main_env(0.0)
    return days, env, la, lb, i0, i1


def router_cells():
    """The gain-aware downsize router: indexed == `fe2_opts` bit for bit, and its controls.

    * `router_indexed_eq_fe2opts_*` -- `simulate3(route_cap=0.10, route_score=1)` reproduces
      `fe2_opts.simulate2(knobs={"route_cap_pp": 10.0})` in the whole NAV path and every scalar,
      including the two per-leg `_legs` rows (the required two-leg parity).
    * `router_off_is_the_current_kernel` -- with the cap at 0 the score is ignored and the run is
      the published two-leg kernel (`fe2_sim.simulate2`), bit for bit.
    * `router_changes_the_split` -- the router moves turnover from one leg to the other, bounded by
      the cap (the `fe2_opts` `p3_route_cap` structure).
    * `router_usd_vs_indexed` -- the `usd` score is a genuinely different setting.
    * `two_books_of_one_instrument_*` -- two legs sharing one price series run with separate FIFO
      books, and the router really separates them.
    """
    from research.fast_engine2 import fe2_opts_parity, fe2_sim

    days, env, la, lb, i0, i1 = _two_leg_router_env()
    oa, ca, wa = la
    ob, cb, wb = lb
    out = {}
    for arm in ("C0", "K"):
        for mode in ("pre_tax", "progressive"):
            theirs = fe2_opts_parity.main_run(arm=arm, mode=mode, m=2,
                                              knobs={"route_cap_pp": 10.0})
            mine = fe3_sim.simulate3([oa, ob], [ca, cb], [wa, wb], env, 100_000.0, i0, i1,
                                     arm=arm, mode=mode, m=2, route_cap=0.10, route_score=1)
            cell = _cell(theirs, mine)
            cell["legs_A_equal"] = bool(theirs["_legs"]["A"] == mine["_legs"]["A"])
            cell["legs_B_equal"] = bool(theirs["_legs"]["B"] == mine["_legs"]["B"])
            cell["pass"] = bool(cell["pass"] and cell["legs_A_equal"] and cell["legs_B_equal"])
            out[f"router_indexed_eq_fe2opts_{arm}_{mode}"] = cell
    # the score is ignored when the router is off, and the run is the two-leg kernel exactly
    plain = fe2_sim.simulate2(oa, ca, wa, ob, cb, wb, env, 100_000.0, i0, i1, arm="K",
                              mode="progressive", m=2)
    off_i = fe3_sim.simulate3([oa, ob], [ca, cb], [wa, wb], env, 100_000.0, i0, i1, arm="K",
                              mode="progressive", m=2, route_cap=0.0, route_score=1)
    off_u = fe3_sim.simulate3([oa, ob], [ca, cb], [wa, wb], env, 100_000.0, i0, i1, arm="K",
                              mode="progressive", m=2, route_cap=0.0, route_score=0)
    out["router_off_is_the_current_kernel"] = {
        "nav_engine_max_abs_diff": float(np.max(np.abs(plain["_nav"] - off_i["_nav"]))),
        "nav_score0_vs_score1": float(np.max(np.abs(off_i["_nav"] - off_u["_nav"]))),
        "pass": bool(_cell(plain, off_i)["pass"] and _cell(off_i, off_u)["pass"])}
    # the cap bounds the move: turnover shifts between the legs, total within 2 x cap x sessions
    off = fe3_sim.simulate3([oa, ob], [ca, cb], [wa, wb], env, 100_000.0, i0, i1, arm="C0",
                            mode="pre_tax", m=2)
    on = fe3_sim.simulate3([oa, ob], [ca, cb], [wa, wb], env, 100_000.0, i0, i1, arm="C0",
                           mode="pre_tax", m=2, route_cap=0.10, route_score=1)
    nsess = i1 - i0 + 1
    ta0, tb0 = off["_legs"]["A"]["turnover_total"], off["_legs"]["B"]["turnover_total"]
    ta1, tb1 = on["_legs"]["A"]["turnover_total"], on["_legs"]["B"]["turnover_total"]
    da, db = ta1 - ta0, tb1 - tb0
    out["router_changes_the_split"] = {
        "turnover_delta": {"A": da, "B": db}, "cap_bound_total_weight": 2.0 * 0.10 * nsess,
        "nav_max_abs_diff": float(np.max(np.abs(on["_nav"] - off["_nav"]))),
        "pass": bool(da * db < 0.0 and abs(da) <= ta0 and abs(db) <= tb0
                     and abs((ta1 + tb1) - (ta0 + tb0)) <= 2.0 * 0.10 * nsess
                     and float(np.max(np.abs(on["_nav"] - off["_nav"]))) > 0.0)}
    usd = fe3_sim.simulate3([oa, ob], [ca, cb], [wa, wb], env, 100_000.0, i0, i1, arm="K",
                            mode="progressive", m=2, route_cap=0.10, route_score=0)
    idx = fe3_sim.simulate3([oa, ob], [ca, cb], [wa, wb], env, 100_000.0, i0, i1, arm="K",
                            mode="progressive", m=2, route_cap=0.10, route_score=1)
    out["router_usd_vs_indexed"] = {
        "nav_max_abs_diff": float(np.max(np.abs(usd["_nav"] - idx["_nav"]))),
        "pass": bool(not np.array_equal(usd["_nav"], idx["_nav"])
                     and usd["cagr_usd_liquidated"] != idx["cagr_usd_liquidated"])}
    # two books of one instrument: the same series in both legs, separate FIFO books
    w = 0.49 * wa
    obb = fe3_sim.simulate3([oa, oa], [ca, ca], [w, w], env, 100_000.0, i0, i1, arm="K",
                            mode="progressive", m=2)
    obb_on = fe3_sim.simulate3([oa, oa], [ca, ca], [w, w], env, 100_000.0, i0, i1, arm="K",
                               mode="progressive", m=2, route_cap=0.10, route_score=1)
    a1, b1 = obb["_legs"]["A"], obb["_legs"]["B"]
    a2, b2 = obb_on["_legs"]["A"], obb_on["_legs"]["B"]
    out["two_books_of_one_instrument"] = {
        "books_trade": {"A": a1["trades"], "B": b1["trades"]},
        "route_moves_between_same_series": {"A": a2["turnover_total"] - a1["turnover_total"],
                                            "B": b2["turnover_total"] - b1["turnover_total"]},
        "pass": bool(a1["trades"] > 0 and b1["trades"] > 0 and a1["turnover_total"] > 0
                     and b1["turnover_total"] > 0
                     and not np.array_equal(obb["_nav"], obb_on["_nav"]))}
    return out


def engine_anchors(tol_pp=0.01):
    """The published K cells, reproduced through the N-leg kernel with legs 2..N = 0."""
    import json
    import pathlib

    ref = json.loads((pathlib.Path(__file__).resolve().parents[2]
                      / "research/band_h2_combo_v1/RESULTS.json").read_text())
    days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    i0, i1 = fe_study.START, len(days) - 1
    out = {}

    def cell(got, want):
        d = (float(got) - float(want)) * 100
        return {"reference": float(want), "reproduced": float(got), "diff_pp": d,
                "pass": bool(abs(d) <= tol_pp)}

    for model, fin in (("base", 0.0), ("calibrated", FIN["calibrated"])):
        d2, o2, c2, _e, w2, _v = fe_data.main_inputs(fin)
        envc = fe_sim.RunEnv(d2, fx, ufe, i0, i1)
        r = fe3_sim.simulate3(_pad4([o2]), _pad4([c2]), _pad4([w2]), envc, 100_000.0, i0, i1,
                              arm="K", mode="pre_tax", m=2)
        st = fe_study.pre_stats(d2, i0, i1, r)
        refc = ref["pre_tax_main"][model]["m2"]["K"]
        out[f"K_pre_{model}_m2_cagr"] = cell(st["cagr"], refc["cagr"])
        out[f"K_pre_{model}_m2_max_dd"] = cell(st["max_dd"], refc["max_dd"])
        out[f"K_pre_{model}_m2_turnover"] = cell(st["turnover_annual"], refc["turnover_annual"])
    for wn in ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end"):
        a0, b0 = TAX_WINDOWS[wn]
        j0, j1 = fe_study.window_indices(days, a0, b0)
        env2 = fe_sim.RunEnv(days, fx, ufe, j0, j1)
        r = fe3_sim.simulate3(_pad4([o]), _pad4([c]), _pad4([w_r2]), env2, 100_000.0, j0, j1,
                              arm="K", mode="progressive", m=2)
        out[f"K_after_tax_100k_{wn}"] = cell(
            r["cagr_usd_liquidated"],
            ref["after_tax_windows"]["100k"][wn]["K"]["after_tax_cagr"])
    return out


def engine_parity(quick=True):
    """`python -m research.fast_engine.parity [--quick]`, the engine's own parity run."""
    cmd = [sys.executable, "-m", "research.fast_engine.parity"] + (["--quick"] if quick else [])
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    out_path = root / "research/fast_engine/PARITY.json"
    before = out_path.read_bytes() if out_path.exists() else None
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    if before is not None and out_path.read_bytes() != before:
        out_path.write_bytes(before)  # `parity.py` has no --no-write: keep the committed artifact
    tail = (proc.stdout.strip().splitlines() or [proc.stderr[-300:]])[-1]
    return {"pass": bool(proc.returncode == 0 and "parity PASS" in proc.stdout), "tail": tail}


def run(n_boot_paths=300, quiet=False, engine_parity_result=None):
    """Every parity cell plus the engine's own parity run."""
    import time

    t0 = time.time()
    checks = {
        "main_window": main_window(),
        "after_tax_windows": after_tax_windows(),
        "cash_yield": cash_yield_cell(),
        "bootstrap": bootstrap(n_paths=n_boot_paths),
        "margin": margin_cells(),
        "multi_leg": multi_leg_holds_three_books(),
        "router": router_cells(),
        "engine_anchors": engine_anchors(),
        "engine_parity_quick": (engine_parity_result if engine_parity_result is not None
                                else engine_parity(quick=True)),
    }
    flat = {}
    for group, cells in checks.items():
        if isinstance(cells, dict):
            for k, v in cells.items():
                if isinstance(v, dict) and "pass" in v:
                    flat[f"{group}.{k}"] = bool(v["pass"])
    # the engine's own parity run is a required cell: it is not a `{...: {"pass": ...}}` map, so it
    # is added explicitly (the framework's harness checks it separately as well)
    flat["engine_parity_quick"] = bool(checks["engine_parity_quick"]["pass"])
    checks["all"] = all(flat.values())
    checks["n_checks"] = len(flat)
    checks["failed"] = sorted(k for k, v in flat.items() if not v)
    checks["wall_s"] = time.time() - t0
    if not quiet:
        for k in checks["failed"]:
            print(f"  FAIL {k}", flush=True)
        print(f"fe3 parity {'PASS' if checks['all'] else 'FAIL'} "
              f"({checks['n_checks']} cells, {len(checks['failed'])} failed, "
              f"{checks['wall_s']:.1f}s)", flush=True)
    return checks


def main(argv=None) -> int:
    import argparse
    import json
    import pathlib

    ap = argparse.ArgumentParser(prog="python -m research.fast_engine3.fe3_parity")
    ap.add_argument("--boot-paths", type=int, default=300)
    ap.add_argument("--write", action="store_true", help="write PARITY3.json")
    args = ap.parse_args(argv)
    checks = run(n_boot_paths=args.boot_paths)
    if args.write:
        out = pathlib.Path(__file__).resolve().parent / "PARITY3.json"
        out.write_text(json.dumps(checks, sort_keys=True, indent=1, default=float) + "\n")
    return 0 if checks["all"] else 1


if __name__ == "__main__":
    sys.exit(main())
