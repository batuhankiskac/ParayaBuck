"""fe2_opts_parity -- the options kernel's parity cells and its positive controls.

Two kinds of cell:

* **P1/P2/P4, option off -> identity.** `fe2_opts.simulate2(knobs=None)` must reproduce
  `fe2_sim.simulate2` cell by cell (`max_abs_diff == 0.0`, `_nav` and the nested dicts included)
  on the real main window, and `fe2_opts.boot_chunk2g` with every knob off must reproduce
  `fe2_boot._boot_chunk2` bit for bit on the bootstrap (`out_pre` and `out_at`). P4 repeats the
  bootstrap with the off values passed *explicitly* (`score_mode=0` and 0/0/0/0), which guards
  against the option path depending on how the knobs arrived.
* **P3, positive controls.** One cell per option asserts that the option *changes* the numbers and
  that the change has the specified structure (a wider band blocks at least as many downsizes,
  the router moves turnover from one leg to the other within the cap, the netting bucket is
  non-empty and empty in pre-tax mode, the YTD gate suppresses it).

The two-leg arm both use is deliberately lopsided but genuinely two-legged: leg A is the engine's
own synthetic-2x reconstruction with the R2 target series (what `fe_data.main_inputs` and
`fe_boot.chunk_legs` provide), leg B is the same window's **1x** instrument (QQQ's raw
open/close) at a **fixed 0.2 weight of the R2 target computed on that 1x price** -- so the legs
are different instruments with different gain fractions and leg B has real turnover and real
downsize sessions of its own (on the main window: leg A 159.6 / leg B 33.2 of cumulative turnover
weight, 265 and 110 downsize fills at arm C0).

`main()` runs `--quick` (small bootstrap) or `--full` and prints the one-line verdict.
"""

from __future__ import annotations

import sys
import time

import numpy as np

from research.fast_engine import fe_boot, fe_data, fe_signals, fe_sim, fe_study
from research.fast_engine import fe_tax
from research.fast_engine.fe_config import BLOCK, TAX_BOOT_EXT, TAX_BOOT_L, TAX_CAPITALS

from . import fe2_boot, fe2_opts, fe2_sim

CAPITAL = 100_000.0
BOOT_ARMS = ({"id": "K", "rule": "K"}, {"id": "C0", "rule": "C0"})
LEG_B_WEIGHT = 0.2

# ----------------------------------------------------------------------------- comparison
def _maxdiff(a, b, path=""):
    """The largest absolute difference between two result dicts, `_`-prefixed keys included."""
    if isinstance(a, dict) or isinstance(b, dict):
        a = {} if a is None else a
        b = {} if b is None else b
        worst, where = 0.0, ""
        for k in sorted(set(a) | set(b), key=str):
            d, w = _maxdiff(a.get(k), b.get(k), f"{path}.{k}")
            if d > worst:
                worst, where = d, w
        return worst, where
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                return float("inf"), f"{path}.len({len(a)}!={len(b)})"
            if not a or all(isinstance(x, str) for x in a):
                return (0.0, "") if list(a) == list(b) else (float("inf"), path)
        a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        aa, bb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
        if aa.shape != bb.shape:
            return float("inf"), f"{path}.shape({aa.shape}!={bb.shape})"
        if not aa.size:
            return 0.0, ""
        d = np.abs(aa - bb)
        k = int(np.argmax(d))
        return float(d.ravel()[k]), f"{path}[{k}]"
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None or isinstance(a, str):
        return (0.0, "") if a == b else (float("inf"), path)
    d = abs(float(a) - float(b))
    return d, (path if d else "")

# ----------------------------------------------------------------------------- the two-leg arm
_MAIN = {}
_MAIN_CACHE = {}

def main_run(knobs=None, arm="K", mode="pre_tax", m=2, fin=0.0, trace=False):
    """One two-leg run of the main window (cached per full argument tuple)."""
    key = (knobs is None, tuple(sorted((knobs or {}).items())), arm, mode, m, fin, trace)
    if key not in _MAIN_CACHE:
        days, env, la, lb, i0, i1 = _main_env(fin)
        _MAIN_CACHE[key] = fe2_opts.simulate2(*la, *lb, env, CAPITAL, i0, i1, arm=arm, mode=mode,
                                              m=m, trace=trace, knobs=knobs)
    return _MAIN_CACHE[key]

def _main_env(fin=0.0):
    if fin not in _MAIN:
        days, oa, ca, _e2, wa, _vote = fe_data.main_inputs(fin)
        _d2, arr, _y = fe_data.load()
        assert days == _d2 and len(days) == len(oa)
        # leg B: the same window's 1x instrument (raw QQQ open/close), sized as a fixed weight of
        # the R2 target computed on that 1x price series
        ob = np.ascontiguousarray(np.asarray(arr["QQQ"][0], dtype=np.float64))
        cb = np.ascontiguousarray(np.asarray(arr["QQQ"][1], dtype=np.float64))
        e2b = fe_signals.e2_up(cb)
        onb, facb = fe_signals.r2_state(cb, e2b, fe_signals.early_components(cb))
        wb = LEG_B_WEIGHT * fe_signals.sized_wants(onb, facb, fe_signals.vol_n(cb))
        fx, ufe = fe_study.try_market(days)
        i0, i1 = fe_study.START, len(days) - 1
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        _MAIN[fin] = (days, env, (oa, ca, wa), (ob, cb, wb), i0, i1)
    return _MAIN[fin]

# ----------------------------------------------------------------------------- P1: identity
def p1_identity(fins=((0.0, "base"), (fe_study.FIN["calibrated"], "calibrated")),
                modes=("pre_tax", "progressive", "flat"), ms=(1, 2, 4)):
    """Option off, one run per (model, mode, m): `fe2_opts` == `fe2_sim` in every cell."""
    out = {}
    for fin, model in fins:
        days, env, la, lb, i0, i1 = _main_env(fin)
        for mode in modes:
            for m in ms:
                theirs = fe2_sim.simulate2(*la, *lb, env, CAPITAL, i0, i1, arm="K", mode=mode, m=m)
                mine = fe2_opts.simulate2(*la, *lb, env, CAPITAL, i0, i1, arm="K", mode=mode, m=m,
                                          knobs=None)
                nav = float(np.max(np.abs(theirs["_nav"] - mine["_nav"])))
                scal, where = _maxdiff(theirs, mine)
                cell = {"nav_max_abs_diff": nav, "max_abs_diff": scal, "worst_key": where,
                        "pass": bool(nav == 0.0 and scal == 0.0)}
                assert cell["pass"], f"P1 {model} {mode} m{m}: {cell}"
                out[f"p1_off_identity.{model}_{mode}_m{m}"] = cell
    return out

# ----------------------------------------------------------------------------- P2/P4: bootstrap
def _chunk_inputs(n_paths, arms=BOOT_ARMS):
    """The bootstrap's two-leg chunk: leg A from `fe_boot.chunk_legs`, leg B the same window's 1x.

    The draws are `fe_boot`'s own (`draw_positions` with the published seed, block and length), so
    the panels are exactly the ones `fe2_boot.boot2` would hand the kernel.
    """
    win = fe_boot.BootWindow()
    rng = np.random.default_rng(fe_boot.SEED)
    pos = fe_boot.draw_positions(rng, BLOCK, n_paths, TAX_BOOT_L, win.npool, TAX_BOOT_EXT)
    legs, _e2, _we = fe_boot.chunk_legs(win.q, win.y, pos)
    # the same alignment `chunk_legs` uses: its panels carry one leading column, so column i of a
    # panel is the price of reconstruction step i-1
    idx = pos + 1
    o1 = np.asarray(win.q[0][idx], dtype=np.float64)
    c1 = np.asarray(win.q[1][idx], dtype=np.float64)
    ob1 = np.ascontiguousarray(np.concatenate([o1[:, :1], o1], axis=1))
    cb1 = np.ascontiguousarray(np.concatenate([c1[:, :1], c1], axis=1))
    e2b = fe_signals.e2_up(cb1)
    onb, facb = fe_signals.r2_state(cb1, e2b, fe_signals.early_components(cb1))
    wb1 = LEG_B_WEIGHT * fe_signals.sized_wants(onb, facb, fe_signals.vol_n(cb1))
    n = len(arms)
    panels = {}
    for md in ("base", "calibrated"):
        oa, ca, wa = legs[md]
        p2 = np.ascontiguousarray(np.stack([wa, wb1], axis=1))
        panels[md] = (np.ascontiguousarray(np.stack([oa, ob1], axis=1)),
                      np.ascontiguousarray(np.stack([ca, cb1], axis=1)),
                      np.ascontiguousarray(np.stack([p2] * n)))
    env = win.env
    return {
        "win": win,
        "panels": panels,
        "env": env,
        "n_arms": n,
        "n_paths": n_paths,
        "cost": fe_sim.COST_PER_SIDE * fe_sim.DEFAULT_M,
        "cap_pre": TAX_CAPITALS["100k"],
        "cap_at": np.array([TAX_CAPITALS["100k"]], dtype=np.float64),
        "mode_ids": np.array([fe_tax.MODE_PROGRESSIVE, fe_tax.MODE_FLAT], dtype=np.int64),
        "band": np.array([fe_sim.RULE[a["rule"]]["band"] for a in arms], dtype=np.bool_),
        "usd_rule": np.array([fe_sim.RULE[a["rule"]]["usd_rule"] for a in arms], dtype=np.bool_),
        "defer": np.array([fe_sim.RULE[a["rule"]]["defer"] for a in arms], dtype=np.bool_),
    }

def _chunk(p, mode="off", knobs_arrays=None):
    """Run one chunk: "ref" = `fe2_boot._boot_chunk2`, "off"/"knobs" = the options kernel.

    Returns `(out_pre, out_at)`.
    """
    ob, cb, wb = p["panels"]["base"]
    oc, cc, wc = p["panels"]["calibrated"]
    env = p["env"]
    n, W = p["n_arms"], p["n_paths"]
    args = (ob, cb, wb, oc, cc, wc, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y,
            env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, p["cap_pre"], p["cap_at"],
            p["win"].i0, p["win"].i1, p["cost"], p["win"].yrs, env.Y0, env.M_lo, p["band"],
            p["usd_rule"], p["defer"], True, p["mode_ids"], env.cash_rate, 0, W, W)
    out_pre = np.zeros((2, n, W), dtype=np.float64)
    out_at = np.zeros((1, 2, n, W), dtype=np.float64)
    if mode == "ref":
        fe2_boot._boot_chunk2(*args, fe2_boot.DBG0, out_pre, out_at)
    elif mode == "off":
        fe2_opts.boot_chunk2g_off(*args, fe2_boot.DBG0, out_pre, out_at)
    else:
        fe2_opts.boot_chunk2g(*args, *knobs_arrays, fe2_boot.DBG0, out_pre, out_at)
    return out_pre, out_at

def p2_identity(n_paths):
    """Option off on the bootstrap: `boot_chunk2g` == `_boot_chunk2`, bit for bit."""
    p = _chunk_inputs(n_paths)
    rp, ra = _chunk(p, "ref")
    op, oa = _chunk(p, "off")
    dp = float(np.max(np.abs(rp - op)))
    da = float(np.max(np.abs(ra - oa)))
    cell = {"paths": n_paths, "arms": [a["id"] for a in BOOT_ARMS],
            "max_abs_diff_pre": dp, "max_abs_diff_tax": da,
            "pass": bool(dp == 0.0 and da == 0.0)}
    assert cell["pass"], f"P2 bootstrap off identity: {cell}"
    return {"p2_off_identity_bootstrap": cell}

def p4_explicit_off(n_paths):
    """The off values passed explicitly (not via the wrapper) must be the same identity."""
    p = _chunk_inputs(n_paths)
    rp, ra = _chunk(p, "ref")
    zeros_i = np.zeros(p["n_arms"], dtype=np.int64)
    zeros_f = np.zeros(p["n_arms"], dtype=np.float64)
    zeros_b = np.zeros(p["n_arms"], dtype=np.bool_)
    op, oa = _chunk(p, "knobs", (zeros_i, zeros_f, zeros_f, zeros_f, zeros_f, zeros_b))
    dp = float(np.max(np.abs(rp - op)))
    da = float(np.max(np.abs(ra - oa)))
    cell = {"paths": n_paths, "score_mode": 0, "band4": 0.0, "route_cap": 0.0, "net_frac": 0.0,
            "net_min_ytd": 0.0, "max_abs_diff_pre": dp, "max_abs_diff_tax": da,
            "pass": bool(dp == 0.0 and da == 0.0)}
    assert cell["pass"], f"P4 explicit off identity: {cell}"
    return {"p4_explicit_off_bootstrap": cell}

def p4_knobs_pretax(n_paths):
    """`knobs_pre=False` keeps the pre-tax block off; the tax block always uses the arm's knobs."""
    p = _chunk_inputs(n_paths)
    kn = {"band_score": "indexed", "band_step4_pp": 12.0, "route_cap_pp": 10.0,
          "loss_net_frac": 0.25, "loss_net_min_ytd_try": 100_000.0}
    a_false = fe2_opts.knob_arrays([{"fill_knobs": kn} for _ in range(p["n_arms"])])
    a_true = fe2_opts.knob_arrays([{"fill_knobs": kn, "fill_knobs_pretax": True}
                                   for _ in range(p["n_arms"])])
    z = np.zeros(p["n_arms"], dtype=np.float64)
    zeros = (np.zeros(p["n_arms"], dtype=np.int64), z, z, z, z,
             np.zeros(p["n_arms"], dtype=np.bool_))
    pre_off, at_off = _chunk(p, "knobs", zeros)
    pre_f, at_f = _chunk(p, "knobs", a_false)
    pre_t, at_t = _chunk(p, "knobs", a_true)
    dpf = float(np.max(np.abs(pre_f - pre_off)))
    dpt = float(np.max(np.abs(pre_t - pre_off)))
    dpp = float(np.max(np.abs(pre_t - pre_f)))
    dat = float(np.max(np.abs(at_f - at_t)))
    dao = float(np.max(np.abs(at_f - at_off)))
    cell = {"paths": n_paths, "knobs": kn,
            "skip_pre_identical_to_off": dpf, "apply_pre_differs_from_off": dpt,
            "apply_vs_skip_pre": dpp, "tax_same_both_ways": dat,
            "tax_differs_from_off": dao,
            "pass": bool(not a_false[5].any() and a_true[5].all() and dpf == 0.0 and dpt > 0.0
                         and dpp > 0.0 and dat == 0.0 and dao > 0.0)}
    assert cell["pass"], f"P4 knobs_pre: {cell}"
    return {"p4_knobs_pretax": cell}

def p4_knob_validation():
    """The knob contract: `None`/`{}` off, and unknown keys / out-of-range values raise."""
    off = fe2_opts.knob_values(None)
    empty = fe2_opts.knob_values({})
    bad = {"unknown_key": None, "band_score": None, "band_step4_pp": None, "route_cap_pp": None,
           "loss_net_frac": None, "loss_net_min_ytd_try": None}
    for name, knobs in (("unknown_key", {"nope": 1}),
                        ("band_score", {"band_score": "banana"}),
                        ("band_step4_pp", {"band_step4_pp": -1.0}),
                        ("route_cap_pp", {"route_cap_pp": float("inf")}),
                        ("loss_net_frac", {"loss_net_frac": 1.5}),
                        ("loss_net_min_ytd_try", {"loss_net_min_ytd_try": -3.0})):
        try:
            fe2_opts.knob_values(knobs)
        except ValueError as e:
            bad[name] = str(e)
    valid = fe2_opts.knob_values({"band_score": "max", "band_step4_pp": 12.0,
                                  "route_cap_pp": 10.0, "loss_net_frac": 0.25,
                                  "loss_net_min_ytd_try": 100000.0})
    ok = bool(off == empty == (0, 0.0, 0.0, 0.0, 0.0) and all(bad.values())
              and valid == (2, 0.12, 0.10, 0.25, 100000.0))
    cell = {"knobs_off": list(off), "valid": list(valid), "raised": bad, "pass": ok}
    assert cell["pass"], f"P4 knob validation: {cell}"
    return {"p4_knob_validation": cell}

def p4_knob_arrays():
    """`knob_arrays` mirrors `knob_values` per arm (`fill_knobs` / `fill_knobs_pretax`)."""
    arms = [{"fill_knobs": {"band_score": "indexed", "route_cap_pp": 10.0}},
            {},
            {"fill_knobs": {"loss_net_frac": 0.25, "loss_net_min_ytd_try": 1e5},
             "fill_knobs_pretax": True}]
    sm, b4, rc, nf, nm, pre = fe2_opts.knob_arrays(arms)
    ok = bool(list(sm) == [1, 0, 0] and list(b4) == [0.0, 0.0, 0.0]
              and list(rc) == [0.1, 0.0, 0.0] and list(nf) == [0.0, 0.0, 0.25]
              and list(nm) == [0.0, 0.0, 1e5] and list(pre) == [False, False, True])
    cell = {"score_modes": [int(v) for v in sm], "band4s": [float(v) for v in b4],
            "route_caps": [float(v) for v in rc], "net_fracs": [float(v) for v in nf],
            "net_min_ytds": [float(v) for v in nm], "knobs_pre": [bool(v) for v in pre],
            "pass": ok}
    assert cell["pass"], f"P4 knob arrays: {cell}"
    return {"p4_knob_arrays": cell}

# ----------------------------------------------------------------------------- P3: controls
def _leg(r, name):
    return r["_legs"][name]

def p3_band_score():
    """`band_score` indexed/max: the fraction that keys the band changes, and so do the blocks."""
    off = main_run(arm="K", mode="pre_tax", m=2)
    idx = main_run(arm="K", mode="pre_tax", m=2, knobs={"band_score": "indexed"})
    mx = main_run(arm="K", mode="pre_tax", m=2, knobs={"band_score": "max"})
    blk = {"off": int(off["blocked_band"]), "indexed": int(idx["blocked_band"]),
           "max": int(mx["blocked_band"])}
    dnav = float(np.max(np.abs(idx["_nav"] - off["_nav"])))
    dmx = float(np.max(np.abs(mx["_nav"] - off["_nav"])))
    di = float(np.max(np.abs(idx["_nav"] - mx["_nav"])))
    distinct = len({blk["off"], blk["indexed"], blk["max"]}) >= 2
    cell = {"blocked_band": blk, "blocked_band_delta_indexed": blk["indexed"] - blk["off"],
            "blocked_band_delta_max": blk["max"] - blk["off"],
            "nav_max_abs_diff_indexed": dnav, "nav_max_abs_diff_max": dmx,
            "nav_max_abs_diff_indexed_vs_max": di,
            "cagr": {k: round(v["cagr_usd_liquidated"], 8)
                     for k, v in (("off", off), ("indexed", idx), ("max", mx))},
            "pass": bool(distinct and dnav > 0.0 and dmx > 0.0 and di > 0.0)}
    assert cell["pass"], f"P3 band_score: {cell}"
    return {"p3_band_score": cell}

def p3_band_step4():
    """`band_step4_pp=12`: a wider fourth step blocks at least as many downsizes as off."""
    off = main_run(arm="K", mode="pre_tax", m=2)
    on = main_run(arm="K", mode="pre_tax", m=2, knobs={"band_step4_pp": 12.0})
    dnav = float(np.max(np.abs(on["_nav"] - off["_nav"])))
    cell = {"band_step4_pp": 12.0, "blocked_band_off": int(off["blocked_band"]),
            "blocked_band_on": int(on["blocked_band"]),
            "blocked_band_delta": int(on["blocked_band"] - off["blocked_band"]),
            "deferred_off": int(off["deferred"]), "deferred_on": int(on["deferred"]),
            "cagr_off": round(off["cagr_usd_liquidated"], 8),
            "cagr_on": round(on["cagr_usd_liquidated"], 8), "nav_max_abs_diff": dnav,
            "pass": bool(on["blocked_band"] >= off["blocked_band"] and dnav > 0.0
                         and on["blocked_band"] > off["blocked_band"])}
    assert cell["pass"], f"P3 band_step4: {cell}"
    return {"p3_band_step4": cell}

def p3_route_cap():
    """`route_cap_pp=10`: units move between the legs, bounded by the cap."""
    route_cap = 0.10
    off = main_run(arm="C0", mode="pre_tax", m=2)
    on = main_run(arm="C0", mode="pre_tax", m=2, knobs={"route_cap_pp": 10.0})
    days, env, _la, _lb, i0, i1 = _main_env(0.0)
    nsess = i1 - i0 + 1
    ta_off, tb_off = _leg(off, "A")["turnover_total"], _leg(off, "B")["turnover_total"]
    ta_on, tb_on = _leg(on, "A")["turnover_total"], _leg(on, "B")["turnover_total"]
    da, db = ta_on - ta_off, tb_on - tb_off
    total_off = off["turnover_total"]
    total_on = on["turnover_total"]
    cell = {"route_cap_pp": 10.0, "sessions": nsess,
            "turnover_off": {"A": ta_off, "B": tb_off, "total": total_off},
            "turnover_on": {"A": ta_on, "B": tb_on, "total": total_on},
            "turnover_delta": {"A": da, "B": db, "total": total_on - total_off},
            "cap_bound_total_weight": 2.0 * route_cap * nsess,
            "nav_max_abs_diff": float(np.max(np.abs(on["_nav"] - off["_nav"]))),
            "pass": bool(da * db < 0.0                                                    # moves legs
                         and abs(da) <= ta_off and abs(db) <= tb_off                      # not bigger than off
                         and abs(total_on - total_off) <= 2.0 * route_cap * nsess         # the cap bounds it
                         and float(np.max(np.abs(on["_nav"] - off["_nav"]))) > 0.0)}
    assert cell["pass"], f"P3 route_cap: {cell}"
    return {"p3_route_cap": cell}

def p3_loss_net():
    """`loss_net_frac=0.25` (progressive): the net bucket fills, the tax moves, pre-tax none."""
    frac = 0.25
    off = main_run(arm="C0", mode="progressive", m=2)
    on = main_run(arm="C0", mode="progressive", m=2, knobs={"loss_net_frac": frac})
    pre_off = main_run(arm="C0", mode="pre_tax", m=2)
    pre_on = main_run(arm="C0", mode="pre_tax", m=2, knobs={"loss_net_frac": frac})
    _, where = _maxdiff(pre_off, pre_on)
    net_by_leg = {n: _leg(on, n)["realised_gain_try_by_reason"].get("net")
                  for n in ("A", "B")}
    n_events = int(on["sells"] - off["sells"])
    cell = {"loss_net_frac": frac, "net_events": n_events,
            "net_gain_by_leg": net_by_leg,
            "net_gain_total": on["realised_gain_try_by_reason"].get("net"),
            "tax_off": off["tax_total_try"], "tax_on": on["tax_total_try"],
            "tax_delta": on["tax_total_try"] - off["tax_total_try"],
            "cagr_off": round(off["cagr_usd_liquidated"], 8),
            "cagr_on": round(on["cagr_usd_liquidated"], 8),
            "nav_max_abs_diff": float(np.max(np.abs(on["_nav"] - off["_nav"]))),
            "pretax_max_abs_diff": _maxdiff(pre_off, pre_on)[0],
            "pretax_worst_key": where,
            "pass": bool(n_events > 0 and any(v is not None for v in net_by_leg.values())
                         and (on["tax_total_try"] != off["tax_total_try"]
                              or on["realised_gain_try_by_reason"]
                              != off["realised_gain_try_by_reason"])
                         and float(np.max(np.abs(on["_nav"] - off["_nav"]))) > 0.0
                         and _maxdiff(pre_off, pre_on)[0] == 0.0)}
    assert cell["pass"], f"P3 loss_net: {cell}"
    return {"p3_loss_net": cell}

def p3_loss_net_min_ytd():
    """The YTD gate fires strictly less often, and high enough it also shrinks the bucket.

    The event count (extra `qld_sells` over the off run) is the direct measure of "fires less
    often". The bucket's signed gain is *not* monotone in the floor at low floors -- the engine
    floors each lot's indexed gain at 0, so a lot harvested as an indexed loss can still book a
    positive nominal gain, and suppressing sessions changes which lots later sessions consume --
    so the second, larger floor (`1e6` TRY) is the one the "bucket smaller or equal" claim is
    asserted on.
    """
    frac, floor, high = 0.25, 100_000.0, 1_000_000.0
    off = main_run(arm="C0", mode="progressive", m=2)
    only = main_run(arm="C0", mode="progressive", m=2, knobs={"loss_net_frac": frac})
    gated = main_run(arm="C0", mode="progressive", m=2,
                     knobs={"loss_net_frac": frac, "loss_net_min_ytd_try": floor})
    peak = main_run(arm="C0", mode="progressive", m=2,
                    knobs={"loss_net_frac": frac, "loss_net_min_ytd_try": high})
    n_off = int(off["sells"])
    n_only = int(only["sells"] - n_off)
    n_gated = int(gated["sells"] - n_off)
    n_peak = int(peak["sells"] - n_off)
    g_only = only["realised_gain_try_by_reason"].get("net")
    g_gated = gated["realised_gain_try_by_reason"].get("net")
    g_peak = peak["realised_gain_try_by_reason"].get("net")
    d_gated = float(np.max(np.abs(gated["_nav"] - only["_nav"])))
    d_peak = float(np.max(np.abs(peak["_nav"] - only["_nav"])))
    cell = {"loss_net_frac": frac, "floors": [floor, high],
            "net_events_ungated": n_only, "net_events_at_100k": n_gated,
            "net_events_at_1M": n_peak,
            "net_gain_ungated": g_only, "net_gain_at_100k": g_gated, "net_gain_at_1M": g_peak,
            "tax_ungated": only["tax_total_try"], "tax_at_100k": gated["tax_total_try"],
            "tax_at_1M": peak["tax_total_try"],
            "nav_max_abs_diff_100k": d_gated, "nav_max_abs_diff_1M": d_peak,
            "pass": bool(0 <= n_gated < n_only and d_gated > 0.0
                         and 0 <= n_peak < n_gated and d_peak > 0.0
                         and (g_only is None or g_peak is None or g_peak <= g_only))}
    assert cell["pass"], f"P3 loss_net_min_ytd: {cell}"
    return {"p3_loss_net_min_ytd": cell}

# ----------------------------------------------------------------------------- the run
CELLS = ("p1", "p2", "p3_band_score", "p3_band_step4", "p3_route_cap", "p3_loss_net",
         "p3_loss_net_min_ytd", "p4")

def run(n_boot_paths=40, quiet=True):
    """Every cell; each carries `pass` plus the numbers it asserts on."""
    t0 = time.time()
    cells = {}
    todo = [("p1_off_identity", p1_identity, ()),
            ("p2_off_identity_bootstrap", p2_identity, (n_boot_paths,)),
            ("p3_band_score", p3_band_score, ()),
            ("p3_band_step4", p3_band_step4, ()),
            ("p3_route_cap", p3_route_cap, ()),
            ("p3_loss_net", p3_loss_net, ()),
            ("p3_loss_net_min_ytd", p3_loss_net_min_ytd, ()),
            ("p4_explicit_off_bootstrap", p4_explicit_off, (n_boot_paths,)),
            ("p4_knobs_pretax", p4_knobs_pretax, (n_boot_paths,)),
            ("p4_knob_validation", p4_knob_validation, ()),
            ("p4_knob_arrays", p4_knob_arrays, ())]
    for group, fn, args in todo:
        try:
            cells.update(fn(*args))
        except AssertionError as e:
            cells[f"{group}.FAILED"] = {"pass": False, "error": str(e)}
    failed = sorted(k for k, v in cells.items() if not v.get("pass"))
    ok = not failed
    out = dict(cells)
    out["all"] = bool(ok)
    out["n_checks"] = len(cells)
    out["failed"] = failed
    out["wall_s"] = time.time() - t0
    if not quiet:
        for k in failed:
            print(f"  FAIL {k}: {cells[k].get('error', cells[k])}", flush=True)
        print(f"fe2_opts parity {'PASS' if ok else 'FAIL'} ({out['n_checks']} checks, "
              f"{len(failed)} failed, {out['wall_s']:.1f}s)", flush=True)
    return out

def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m research.fast_engine2.fe2_opts_parity")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_true", help="small bootstrap (40 paths)")
    g.add_argument("--full", action="store_true", help="large bootstrap (250 paths)")
    ap.add_argument("--boot-paths", type=int, default=None)
    args = ap.parse_args(argv)
    n = args.boot_paths if args.boot_paths is not None else (250 if args.full else 40)
    checks = run(n_boot_paths=n, quiet=False)
    return 0 if checks["all"] else 1

if __name__ == "__main__":
    sys.exit(main())
