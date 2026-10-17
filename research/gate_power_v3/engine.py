"""gate_power_v3.engine -- the stage-1 / stage-2 orchestration of the third-pass audit.

This is the framework's `batch_screen.run.control_record` / `run._arm_task` without the registry,
the process pool or the result files: it returns plain dicts, so the audit's `screen.py` can drive
**any** control (production K, R2, A1u, E2) and any candidate through exactly the same stages,
gates and block bootstrap the campaign used. Nothing here simulates: every number comes from
`research.fast_engine` through the copied `stages`/`boot` modules, and the copied modules are the
campaign's own code (only `api.py`'s synthetic-leg construction differs, see its `SYNTH_LEGS`).

`build_record` returns `(rec, vec)`: `rec` has the cells the gates read, and `vec` the raw per-path
CAGRs (pre base/calibrated, after-tax progressive/flat + the fresh cells + the pooled OOS vector).
`run_candidate` pairs a candidate against a control record and produces the framework's verdict.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine.fe_config import GATE_CAPITAL

from . import boot as B
from . import stages, windows
from .api import NotApplicable


def control_arm(control_id="k_control/K"):
    """The control's resolved arm from its plugin id (`idea/arm`)."""
    idea, arm = control_id.split("/")
    from . import api

    return api.load_arm(idea, arm)


def build_record(arm, cash_yield="none", win=None, stage2=True, quiet=True, label=None):
    """`stages.stage1` + the clean bootstrap + (if `stage2`) the whole holdout, as `(rec, vec)`.

    The record's `boot` cells are the arm's **own level** statistics (the control's Δ-vs-itself is
    applied by `run_candidate`); `vec` carries the raw per-path CAGRs the Δ and the deflation use.
    """
    win = win or windows.arm_window(arm)
    rec = stages.stage1(arm, cash_yield, win=win)
    b12 = stages.stage1_vectors(arm, cash_yield, quiet=quiet, label=label or arm["id"], win=win)
    vec = {"pre": {md: b12["pre"][md][arm["id"]] for md in b12["pre"]},
           "tax": {md: b12["tax"][GATE_CAPITAL][md][arm["id"]]
                   for md in b12["tax"][GATE_CAPITAL]}}
    rec["boot"] = {"pre": {}, "tax": {}}
    for md in ("base", "calibrated"):
        rec["boot"]["pre"][md] = B.delta_stats(vec["pre"][md])
    for md in ("progressive", "flat"):
        rec["boot"]["tax"][md] = B.delta_stats(vec["tax"][md])
    rec["after_tax"].update({k: v for k, v in
                             stages.stage2_main_windows(arm, cash_yield, win=win).items()
                             if k not in rec["after_tax"]})
    if stage2:
        rec["oos"] = stages.stage2_oos(arm, cash_yield)
        rec["etf_pre"], rec["etf_after_tax"] = stages.stage2_instruments(arm, cash_yield)
        rec["rolling"] = stages.stage2_rolling(arm, cash_yield, win=win)
        fresh = stages.stage2_fresh(arm, cash_yield, label=arm["id"], win=win)
        rec["fresh"] = {blk: {"tax": {md: list(fresh[blk]["tax"][GATE_CAPITAL][md][arm["id"]])
                                      for md in fresh[blk]["tax"][GATE_CAPITAL]}}
                        for blk in fresh}
        vec["fresh"] = {blk: {md: list(fresh[blk]["tax"][GATE_CAPITAL][md][arm["id"]])
                              for md in fresh[blk]["tax"][GATE_CAPITAL]} for blk in fresh}
        pooled = stages.stage2_oos_pooled(arm, cash_yield)
        rec["oos_pooled"] = B.delta_stats(pooled["pooled"][arm["id"]])
        vec["oos_pooled"] = pooled["pooled"][arm["id"]]
    rec["win"] = win.as_dict()
    return rec, vec


def run_candidate(arm, ctrl, cash_yield="none", stage2=True, quiet=True):
    """A candidate against a control record, exactly as the framework's `_arm_task` decides it.

    `ctrl` is a `build_record` result of the control arm. Returns a JSON-ready record with
    `stage1`, `stage1_gates`, `stage1_pass`, `stage2`, `gates`, `stage2_pass`, `verdict`,
    `b1_delta_pp` and the paired Δ cells. A stage-1 fail is recorded and stage 2 is skipped (unless
    `stage2` is False, in which case a survivor is recorded `not_run_stage2`).
    """
    win = windows.arm_window(arm)
    crec = ctrl["record"]
    rec = stages.stage1(arm, cash_yield, win=win)
    b12 = stages.stage1_vectors(arm, cash_yield, quiet=quiet, label=arm["id"], win=win)
    rec["boot"] = {"pre": {}, "tax": {}}
    delta_vec = {}
    for md in ("base", "calibrated"):
        v = b12["pre"][md][arm["id"]]
        d = v - np.asarray(ctrl["vec"]["pre"][md], dtype=np.float64)
        rec["boot"]["pre"][md] = B.delta_stats(d)
        delta_vec[f"pre_{md}"] = d
    for md in ("progressive", "flat"):
        v = b12["tax"][GATE_CAPITAL][md][arm["id"]]
        d = v - np.asarray(ctrl["vec"]["tax"][md], dtype=np.float64)
        rec["boot"]["tax"][md] = B.delta_stats(d)
        delta_vec[f"tax_{md}"] = d
    rec["after_tax"].update({k: v for k, v in
                             stages.stage2_main_windows(arm, cash_yield, win=win).items()
                             if k not in rec["after_tax"]})
    s1_pass, s1_gates = stages.stage1_pass(rec, crec)
    out = {
        "arm": arm["id"], "idea": arm["idea"], "arm_id": arm["arm_id"], "spec": arm["spec"],
        "rule": arm["rule"], "leg": arm["leg"], "cash_yield": cash_yield, "win": win.as_dict(),
        "stage1_gates": s1_gates, "stage1_pass": s1_pass,
        "b1_delta_pp": [float(v) * 100 for v in delta_vec["tax_progressive"]],
    }
    if s1_pass and stage2:
        s2 = {"after_tax": dict(rec["after_tax"])}
        s2["oos"] = stages.stage2_oos(arm, cash_yield)
        s2["etf_pre"], s2["etf_after_tax"] = stages.stage2_instruments(arm, cash_yield)
        s2["rolling"] = stages.stage2_rolling(arm, cash_yield, win=win)
        fresh = stages.stage2_fresh(arm, cash_yield, label=arm["id"], win=win)
        s2["fresh"] = {blk: {"tax": {md: list(fresh[blk]["tax"][GATE_CAPITAL][md][arm["id"]])
                                     for md in fresh[blk]["tax"][GATE_CAPITAL]}}
                       for blk in fresh}
        markets, na = stages.oos_pooled_markets(arm, cash_yield)
        if markets:
            d, note = stages.oos_pooled_delta(arm, ctrl["vec"]["oos_pooled"], control_arm(
                ctrl["arm"]), cash_yield, markets)
            cell = B.delta_stats(d)
            if len(markets) != len(stages.OOS_POOLED_MARKETS):
                cell["markets"] = note["markets"]
                cell["markets_not_applicable"] = na
        else:
            cell = {"not_evaluable": "no OOS market's pooled bootstrap could be drawn",
                    "markets_not_applicable": na}
        s2["oos_pooled"] = cell
        joined = dict(rec)
        joined.update({"oos": s2["oos"], "etf_pre": s2["etf_pre"],
                       "etf_after_tax": s2["etf_after_tax"], "rolling": s2["rolling"],
                       "fresh": s2["fresh"], "oos_pooled": s2["oos_pooled"]})
        out["stage2"] = s2
        out["gates"] = stages.gates(joined, crec)
        out["stage2_pass"] = bool(out["gates"]["pass"])
        if out["gates"].get("holdout_insufficient"):
            out["availability"] = out["gates"]["holdout_insufficient"]
    out["stage1"] = rec
    out["verdict"] = ("reject_stage1" if not s1_pass else
                      ("not_run_stage2" if not stage2 else
                       ("holdout_insufficient" if (out.get("gates") or {}).get("holdout_insufficient")
                        else ("stage2_pass" if out.get("stage2_pass") else "reject_stage2"))))
    return out


def make_control(control_id="k_control/K", cash_yield="none", stage2=True, quiet=True):
    """`build_record` of the control arm, wrapped as `{"arm", "record", "vec"}`."""
    arm = control_arm(control_id)
    rec, vec = build_record(arm, cash_yield, stage2=stage2, quiet=quiet, label="control")
    return {"arm": control_id, "record": rec, "vec": vec}


__all__ = ["build_record", "control_arm", "make_control", "run_candidate"]
