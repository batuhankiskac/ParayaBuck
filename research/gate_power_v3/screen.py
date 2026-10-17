"""gate_power_v3.screen -- the third-pass audit of the batch screen's power.

    python -m research.gate_power_v3.screen --harness   engine parity --quick, K's anchors through
                                                        the copy, the K-control record bit-for-bit,
                                                        every synthetic leg's own price path
    python -m research.gate_power_v3.screen --run       positive controls + synthetic power +
                                                        deflation + DSR audit + tally, one RESULTS
    python -m research.gate_power_v3.screen --tally     the record tally only

Everything is driven by `research.fast_engine` through the copied `research.batch_screen` modules
(only `api.py`'s synthetic-leg construction is changed, see its `SYNTH_LEGS`); no kernel is added
and no per-day/per-path Python loop is written for simulation, tax or bootstrap.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from research.fast_engine.fe_config import GATE_CAPITAL

from . import api, boot as B, deflation, engine, stages, windows
from . import dsr_audit, tally

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE / "results"
ANCHOR_BAND_H2 = ROOT / "research/band_h2_combo_v1/RESULTS.json"
BS_CONTROL = ROOT / "research/batch_screen/results/anchors_v1/_control.json"
ANCHOR_TOL_PP = 0.01

N_TRIALS = 303          # the campaign's multiple-testing count (distinct registered trials)
CONTROLS = {"K": "k_control/K", "R2": "r2_control/R2", "A1u": "a1u_control/A1u",
            "E2": "e2_control/E2"}
PC = [("pc_r2_vs_e2", "r2_control", "R2", "E2"),
      ("pc_a1u_vs_r2", "a1u_control", "A1u", "R2"),
      ("pc_k_vs_a1u", "k_control", "K", "A1u"),
      ("pc_k_vs_r2", "k_control", "K", "R2")]
SYNTH_BATCH = "gp3_synth"


def rnd(x, nd=6):
    if isinstance(x, bool) or x is None or isinstance(x, (str, int)):
        return x
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: rnd(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [rnd(v, nd) for v in x]
    if isinstance(x, np.ndarray):
        return [rnd(float(v), nd) for v in x.ravel()]
    if isinstance(x, np.floating):
        return round(float(x), nd)
    if isinstance(x, np.integer):
        return int(x)
    return x


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rnd(obj), sort_keys=True, indent=1) + "\n")


# ----------------------------------------------------------------------- controls (cached)
def _control_file(tag: str) -> Path:
    return RESULTS / f"_control_{tag}.pkl"


def get_control(tag: str, quiet=True) -> dict:
    """The control's record and raw vectors, cached **exactly** (pickle; float64).

    The cache must not round the raw per-path CAGRs: `dump()`'s 6-dp JSON would perturb the
    control's cells by ~1e-6 *fraction* and a Δ of exactly 0 (a K clone) would come out as
    +6e-6 pp -- which is exactly the `b1_delta_pp`/after-tax Δ the campaign's own K-clone arms
    carry (`research/batch_screen/results/.../IC58__A.json`), i.e. that signature is the
    framework's rounded `_control.json`, not a real difference. A rounded copy is still written
    next to the cache for inspection.
    """
    import pickle

    p = _control_file(tag)
    if not p.exists():
        ctrl = engine.make_control(CONTROLS[tag], quiet=quiet)
        with open(p, "wb") as fh:
            pickle.dump(ctrl, fh)
        dump(p.with_suffix(".json"), ctrl)
    with open(p, "rb") as fh:
        return pickle.load(fh)


def _vec_np(ctrl):
    """The control's raw vectors as numpy (`pre`/`tax` per-model arrays, `oos_pooled` one array)."""
    v = ctrl["vec"]
    return {"pre": {k: np.asarray(x, dtype=float) for k, x in v["pre"].items()},
            "tax": {k: np.asarray(x, dtype=float) for k, x in v["tax"].items()},
            "oos_pooled": np.asarray(v["oos_pooled"], dtype=float)}


# ----------------------------------------------------------------------- batches
def run_batch(name, specs, quiet=True):
    """`specs` = list of `(idea, arm, control_tag)`; writes one record per candidate."""
    ctrls = {}
    for _i, _a, ctag in specs:
        if ctag not in ctrls:
            ctrls[ctag] = {"tag": ctag, "arm": CONTROLS[ctag],
                           "record": None, "vec": None}
            c = get_control(ctag, quiet=quiet)
            ctrls[ctag]["record"], ctrls[ctag]["vec"] = c["record"], _vec_np(c)
    batch_dir = RESULTS / name
    batch_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for idea, arm_id, ctag in specs:
        t0 = time.time()
        arm = api.load_arm(idea, arm_id)
        rec = engine.run_candidate(arm, ctrls[ctag], stage2=True, quiet=quiet)
        rec["wall_s"] = time.time() - t0
        dump(batch_dir / f"{idea}__{arm_id}.json", {k: v for k, v in rec.items()
                                                    if k != "wall_s"})
        print(f"  {rec['arm']:28s} {rec['verdict']:20s} "
              f"AT2000 {rec['stage1_gates']['after_tax_2000_delta_pp']:+.4f} "
              f"({rec['wall_s']:.1f}s)", flush=True)
        out.append(rec)
    return out


def synth_specs():
    mod = api.load_idea("gp3_synth")
    return [("gp3_synth", a, "K") for a in sorted(mod.ARMS)]


# ----------------------------------------------------------------------- deflation
def load_arm_deltas(batch=None):
    out = {}
    for p in sorted(RESULTS.glob("*/*__*.json")):
        if batch is not None and p.parent.name != batch:
            continue
        r = json.loads(p.read_text())
        v = r.get("b1_delta_pp") or []
        if v and not r.get("control") and np.isfinite(np.asarray(v, dtype=float)).all():
            out[r["arm"]] = np.asarray(v, dtype=float)
    return out


def deflate_batch(name, quiet=True):
    """The framework's three tests on a batch's Δ vectors, plus the DSR audit and RW-only."""
    vecs = load_arm_deltas(name)
    rec = deflation.deflate(vecs, N_TRIALS, alpha=0.05)
    rec["batch"] = name
    rec["dsr_audit"] = dsr_audit.analyse(vecs, N_TRIALS)
    rec["rw_only_declared"] = sorted(rec["rw_stepdown"]["rejected"])
    # stage-2 pass set of this batch
    s2 = {}
    for p in sorted((RESULTS / name).glob("*__*.json")):
        r = json.loads(p.read_text())
        s2[r["arm"]] = bool(r.get("stage2_pass"))
    rec["stage2_pass"] = sorted(a for a, v in s2.items() if v)
    rec["declared_framework"] = sorted(a for a in rec["declared"] if s2.get(a))
    rec["declared_null_dsr"] = sorted(a for a in rec["dsr_audit"]["pass_null"] if s2.get(a))
    rec["declared_rw_only"] = sorted(a for a in rec["rw_only_declared"] if s2.get(a))
    dump(RESULTS / f"DEFLATION_{name}.json", rec)
    return rec


def _stage2_of(globs):
    s2 = {}
    for p in globs:
        r = json.loads(p.read_text())
        s2[r["arm"]] = bool(r.get("stage2_pass"))
    return s2


def _derive(rec, s2):
    """The three declaration rules of one pool: framework DSR, corrected DSR, RW only."""
    rec["stage2_pass"] = sorted(a for a, v in s2.items() if v)
    rec["declared_framework"] = sorted(a for a in rec.get("declared", []) if s2.get(a))
    rec["declared_null_dsr"] = sorted(a for a in rec["dsr_audit"]["pass_null"] if s2.get(a))
    rec["declared_rw_only"] = sorted(
        a for a in rec.get("rw_stepdown", {}).get("rejected", []) if s2.get(a))
    return rec


def dsr_pass():
    """The DSR question over the pools: each PC batch, the synthetic batch, all this study's arms,
    and the campaign's own arms (the DSR the campaign actually ran)."""
    out = {}
    for name in [p[0] for p in PC] + [SYNTH_BATCH]:
        if (RESULTS / name).exists():
            out[name] = deflate_batch(name)
    # every arm on disk (pc + synth), with the derived declaration rules
    allv = load_arm_deltas(None)
    aa = deflation.deflate(allv, N_TRIALS, alpha=0.05)
    aa["dsr_audit"] = dsr_audit.analyse(allv, N_TRIALS)
    aa["batch"] = "all_arms"
    _derive(aa, _stage2_of(sorted(RESULTS.glob("*/*__*.json"))))
    dump(RESULTS / "DEFLATION_all.json", aa)
    out["all_arms"] = aa
    # the campaign's own arms alone: the DSR the campaign actually ran (B1 Δ vectors from the
    # committed records), with the declarations filtered by the records' stage-2 verdicts.
    camp = tally.load_campaign_deltas()
    ca = {"batch": "campaign_batch_screen", "dsr_audit": dsr_audit.analyse(camp, N_TRIALS),
          "rw_stepdown": deflation.rw_stepdown(camp), "declared": []}
    dump(RESULTS / "DSR_campaign.json", ca["dsr_audit"] | {"rw_stepdown": ca["rw_stepdown"]})
    _derive(ca, {r["arm"]: bool(r.get("stage2_pass"))
                 for r in tally.load_latest_records()[0]})
    out["campaign"] = ca
    dump(RESULTS / "DSR.json", out)
    return out


# ----------------------------------------------------------------------- harness
def _anchor(got, want, tol_pp=ANCHOR_TOL_PP):
    d = (float(got) - float(want)) * 100
    return {"reference": float(want), "reproduced": float(got), "diff_pp": d,
            "pass": bool(abs(d) <= tol_pp)}


def harness():
    t0 = time.time()
    checks = {}
    parity_out = ROOT / "research/fast_engine/PARITY.json"
    before = parity_out.read_bytes() if parity_out.exists() else None
    par = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"],
                         cwd=ROOT, capture_output=True, text=True)
    if before is not None and parity_out.read_bytes() != before:
        parity_out.write_bytes(before)
    checks["engine_parity_quick"] = {
        "pass": bool(par.returncode == 0 and "parity PASS" in par.stdout),
        "tail": (par.stdout.strip().splitlines() or [""])[-1]}

    # K's anchors through the *copy* (not the campaign's harness): pre-tax and after-tax
    bh = json.loads(ANCHOR_BAND_H2.read_text())
    ctrl = get_control("K")
    rec = ctrl["record"]
    an = {}
    for md in ("base", "calibrated"):
        for m in (1, 2):
            an[f"K_pre_{md}_m{m}"] = _anchor(rec["main"][md][f"m{m}"]["cagr"],
                                             bh["pre_tax_main"][md][f"m{m}"]["K"]["cagr"])
        an[f"K_maxdd_{md}"] = _anchor(rec["main"][md]["m2"]["max_dd"],
                                      bh["pre_tax_main"][md]["m2"]["K"]["max_dd"])
        an[f"K_turnover_{md}"] = _anchor(rec["main"][md]["m2"]["turnover_annual"],
                                         bh["pre_tax_main"][md]["m2"]["K"]["turnover_annual"])
    for wn in ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end"):
        an[f"K_after_tax_{wn}"] = _anchor(
            rec["after_tax"][wn]["after_tax_cagr"],
            bh["after_tax_windows"]["100k"][wn]["K"]["after_tax_cagr"])
    an["K_all_pass"] = {"pass": bool(all(v["pass"] for v in an.values()))}
    checks["K_anchors"] = an

    # the published K-control record bit-for-bit (same framework, same default window)
    pub = json.loads(BS_CONTROL.read_text())
    same_main = rnd(rec["main"]) == rnd(pub["record"]["main"])
    same_at = rnd(rec["after_tax"]) == rnd(pub["record"]["after_tax"])
    # The published `_control.json` is a 6-dp-rounded serialization, so the raw vector cannot be
    # *exactly* equal; the identity is that this study's exact control rounds to the published
    # record's cells and to its stored vectors at the published 6-dp precision (the residual
    # |diff| <= 5e-7 *is* the rounding, and it is the source of the campaign's "+6e-6 pp" K clones).
    v_mine = np.asarray(ctrl["vec"]["tax"]["progressive"], dtype=float)
    v_pub = np.asarray(pub["vectors"]["tax"]["progressive"], dtype=float)
    maxdiff = float(np.max(np.abs(v_mine - v_pub)))
    checks["K_control_bit_identical"] = {
        "main": same_main, "after_tax": same_at,
        "b1_vector_max_abs_diff": f"{maxdiff:.3e}",   # a string: `dump` rounds floats to 6 dp
        "b1_vector_equal_at_published_6dp": bool(v_mine.shape == v_pub.shape
                                                 and np.array_equal(np.round(v_mine, 6), v_pub)),
        "pass": bool(same_main and same_at and v_mine.shape == v_pub.shape
                     and np.array_equal(np.round(v_mine, 6), v_pub) and maxdiff <= 1e-6)}

    # every synthetic leg's own price path: the per-arm drift/noise check (the v2 defect)
    checks["synth_legs"] = synth_leg_checks()
    failed = [f"{g}.{k}" for g, grp in checks.items() if isinstance(grp, dict)
              for k, v in grp.items() if isinstance(v, dict) and "pass" in v and not v["pass"]]
    checks["all"] = bool(all(v["pass"] for grp in checks.values() if isinstance(grp, dict)
                             for k, v in grp.items()
                             if isinstance(v, dict) and "pass" in v))
    checks["failed"] = failed
    checks["wall_s"] = time.time() - t0
    dump(HERE / "HARNESS.json", checks)
    return checks


def synth_leg_checks():
    """One panel, every synthetic leg: does the leg do exactly what its name says?

    `2x` is production K's leg. For an `"all"` drift the log leg/2x ratio must rise by `d/252` per
    session (so the total log ratio ≈ `d/100 × years`); a zero drift must be the `2x` leg exactly;
    the noise legs' log-ratio increments must have mean ≈ 0, sd ≈ sigma, and be exact negatives of
    each other. This is the cell that would have caught v2's batch-level drift (where `syn_null0`
    was a copy of `syn_edge`).
    """
    days, p0, i0, i1 = windows.main_panel(0.0, leg="2x")
    base = np.asarray(p0.legs["2x"][1][0], dtype=float)
    out = {}
    for name, (mode, drift, nsign, nsigma, nseed) in sorted(api.SYNTH_LEGS.items()):
        _d, p, _a, _b = windows.main_panel(0.0, leg=name)
        leg = np.asarray(p.legs[name][1][0], dtype=float)
        if mode == "all" and drift == 0.0:
            out[name] = {"pass": bool(np.array_equal(leg, base)), "kind": "zero drift == 2x"}
            continue
        if mode == "noise":
            inc = np.diff(np.log(leg)) - np.diff(np.log(base))
            out[name] = {"pass": bool(abs(inc.mean()) < 5e-4 and abs(inc.std() - nsigma) < 5e-4),
                         "kind": "noise", "inc_mean": float(inc.mean()),
                         "inc_sd": float(inc.std()), "want_sd": float(nsigma)}
            continue
        inc = np.diff(np.log(leg)) - np.diff(np.log(base))
        # the invested/pre2013 legs drift on a subset: the *per-drifted-session* increment is
        # log(1 + d/252), the exact log of the multiplicative factor the builder applies
        nz = inc[np.abs(inc) > 0.5 * np.log1p(drift / 100.0 / 252.0)]
        per = float(nz.mean()) if nz.size else 0.0
        want = float(np.log1p(drift / 100.0 / 252.0))
        out[name] = {"pass": bool(nz.size > 0 and abs(per - want) < 1e-12),
                     "kind": mode, "n_drifted_sessions": int(nz.size),
                     "per_session_log_inc": per, "want": want}
    # mirrored noise is exact
    _d, dp, _, _ = windows.main_panel(0.0, leg="syn_np_0500")
    _d, dm, _, _ = windows.main_panel(0.0, leg="syn_nm_0500")
    lp = np.log(np.asarray(dp.legs["syn_np_0500"][1][0], dtype=float))
    lm = np.log(np.asarray(dm.legs["syn_nm_0500"][1][0], dtype=float))
    out["noise_mirrored"] = {"pass": bool(np.allclose(lp - np.log(base), -(lm - np.log(base)),
                                                      atol=1e-12)), "kind": "mirror"}
    return out


# ----------------------------------------------------------------------- main
def _assemble(dsr, tl, wall_s):
    """`RESULTS.json` is deterministic (no wall clock); the wall clock goes to `RUN_TIMING.json`."""
    res = {"n_trials": N_TRIALS,
           "deflation": {k: {"sr0_framework": v.get("dsr_audit", {}).get("sr0_framework"),
                             "sr0_null": v.get("dsr_audit", {}).get("sr0_null"),
                             "var_total": v.get("dsr_audit", {}).get("var_total"),
                             "var_sr_null": v.get("dsr_audit", {}).get("var_sr_null_mean"),
                             "declared_framework": v.get("declared_framework"),
                             "declared_null_dsr": v.get("declared_null_dsr"),
                             "declared_rw_only": v.get("declared_rw_only")}
                         for k, v in dsr.items()},
           "tally": tl}
    dump(HERE / "RESULTS.json", res)
    dump(HERE / "RUN_TIMING.json", {"wall_s": wall_s, "note": "wall clock of the --run/--dsr pass"})
    return res


def run_pc():
    """Each positive control in its **own** batch dir (its own control, so the Δ stays paired)."""
    for name, idea, arm, ctrl in PC:
        print(f"positive control {name}:", flush=True)
        run_batch(name, [(idea, arm, ctrl)])


def run_all():
    t0 = time.time()
    run_pc()
    print("synthetic power:", flush=True)
    run_batch(SYNTH_BATCH, synth_specs())
    print("DSR / deflation:", flush=True)
    dsr = dsr_pass()
    print("tally:", flush=True)
    tl = tally.main()
    res = _assemble(dsr, tl, time.time() - t0)
    print(f"\nwall {res['wall_s']:.1f}s")
    return res


def run_dsr_only():
    """Re-run the deflation / DSR audit / tally over the records already on disk."""
    t0 = time.time()
    dsr = dsr_pass()
    tl = tally.main()
    res = _assemble(dsr, tl, time.time() - t0)
    print(f"\nwall {res['wall_s']:.1f}s")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.gate_power_v3.screen")
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--pc", action="store_true", help="run the four positive controls")
    ap.add_argument("--dsr", action="store_true",
                    help="deflation/DSR audit + tally over the records already on disk")
    ap.add_argument("--tally", action="store_true")
    args = ap.parse_args(argv)
    if args.harness:
        c = harness()
        for f in c.get("failed", []):
            print("  FAIL", f)
        print(f"harness {'PASS' if c['all'] else 'FAIL'} ({c['wall_s']:.1f}s)")
        return 0 if c["all"] else 1
    if args.tally:
        tally.main()
        return 0
    if args.run:
        run_all()
        return 0
    if args.pc:
        run_pc()
        return 0
    if args.dsr:
        run_dsr_only()
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
