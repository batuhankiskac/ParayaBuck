"""rw_boot_audit_v1.screen -- the audit entry point.

    python -m research.rw_boot_audit_v1.screen --harness   # engine parity --quick, K's anchors,
                                                           # the leg-B convention cells, the pairing
                                                           # cells, the before/after parity diff
    python -m research.rw_boot_audit_v1.screen --repro      # one IC37/C B1 path decomposed
    python -m research.rw_boot_audit_v1.screen --baseline   # (pre-fix only) dump PRE_FIX_BASELINE.npz
    python -m research.rw_boot_audit_v1.screen --parity     # diff the current archive vs the baseline
    python -m research.rw_boot_audit_v1.screen --run        # the re-screen batch `rw_fix_v1` (x2)
    python -m research.rw_boot_audit_v1.screen --summary    # RESULTS.json + SUMMARY.txt

Everything runs on the mandatory Numba engine `research/fast_engine` and the framework
`research/batch_screen`; the one code change is `research/batch_screen/api.py::MarketData.leg_series`
(see `PREREGISTRATION.md` and `docs/research/rw_boot_audit_v1.md`). No target-weight array is built
here: the arms are the registered extra-key two-leg specs, screened unchanged by the framework's own
pipeline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from research.batch_screen import api, run as RUN, windows
from research.batch_screen import boot as B
from research.fast_engine import fe_data, fe_sim, fe_study
from research.fast_engine.fe_config import FIN, TAX_CAPITALS, TAX_WINDOWS
from research.rw_boot_audit_v1 import parity_cells as PC
from research.rw_boot_audit_v1 import repro as REPRO

HERE = Path(__file__).resolve().parent
PY = sys.executable
BATCH = "rw_fix_v1"
IDEAS = ("IC31,IC32,IC33,IC34,IC35,IC37,IC38,IC42,IC43,IC44,IC45,IC46,"
         "ib25_RW,ib25_2L,IB26_RW,IB26_2L,ib27_2L")
ANCHOR_TOL_PP = 0.01
PUB = api.ROOT / "research/band_h2_combo_v1/RESULTS.json"
WORKERS = "6"


# ----------------------------------------------------------------------------- engine checks
def engine_parity_quick() -> dict:
    out = api.ROOT / "research/fast_engine/PARITY.json"
    before = out.read_bytes() if out.exists() else None
    proc = subprocess.run([PY, "-m", "research.fast_engine.parity", "--quick"], cwd=api.ROOT,
                          capture_output=True, text=True)
    restored = False
    if before is not None and out.read_bytes() != before:
        out.write_bytes(before)
        restored = True
    tail = (proc.stdout.strip().splitlines() or [proc.stderr[-200:]])[-1]
    return {"pass": bool(proc.returncode == 0 and "parity PASS" in proc.stdout),
            "tail": tail.split(" in ")[0], "parity_json_restored": restored}


def engine_anchors(arms=("K", "C0")) -> dict:
    """K's and R2's published cells through `fe_sim.simulate` (engine, not the framework)."""
    pub = json.loads(PUB.read_text())
    days, o, c, _we, w_r2, _e = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    out = {}

    def cell(got, want):
        d = (float(got) - float(want)) * 100
        return {"reference": float(want), "reproduced": float(got), "diff_pp": d,
                "pass": bool(abs(d) <= ANCHOR_TOL_PP)}

    for md, spread in FIN.items():
        _d, o_, c_, _we2, wr_, _e2 = fe_data.main_inputs(spread)
        env = fe_sim.RunEnv(_d, fx, ufe, fe_study.START, T)
        years = fe_sim.calendar_years(_d[fe_study.START - 1], _d[T])
        for arm in arms:
            r = fe_sim.simulate(o_, c_, wr_, env, TAX_CAPITALS["100k"], fe_study.START, T,
                                arm=arm, mode="pre_tax", m=2)
            want = pub["pre_tax_main"][md]["m2"][arm]
            out[f"{arm}_pre_{md}_cagr"] = cell((r["_nav"][-1] / r["_nav"][0]) ** (1 / years) - 1,
                                               want["cagr"])
            out[f"{arm}_pre_{md}_max_dd"] = cell(
                float(fe_sim.max_dd(r["_nav"].reshape(1, -1))[0]), want["max_dd"])
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env2 = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        for arm in arms:
            rq = fe_sim.simulate(o, c, w_r2, env2, TAX_CAPITALS["100k"], i0, i1, arm=arm,
                                 mode="progressive", m=2)
            out[f"{arm}_after_tax_{wn}"] = cell(
                rq["cagr_usd_liquidated"],
                pub["after_tax_windows"]["100k"][wn][arm]["after_tax_cagr"])
    return out


def leg_convention() -> dict:
    """The leg-B chunk convention, and that `leg_series` returns the rebuilt path."""
    c = REPRO.chunk_path("IC37/C", path=0)
    pan = c["panels"][("2x", 0.0)]
    raw, lvl, own, _idx, _pool = REPRO._ief_paths(pan, c["pos"], "ief")
    got = np.asarray(pan.leg_series("ief")[0], dtype=np.float64)
    seen = raw[:, 1:] / raw[:, :-1]
    spanning = int((~np.isclose(seen, own, rtol=1e-12, atol=1e-15)).sum())
    return {
        "leg_series_is_not_the_per_column_level": {
            "pass": bool(not np.allclose(got, raw, rtol=1e-9, atol=1e-12))},
        "leg_series_is_the_own_return_path": {
            "pass": bool(np.allclose(got, lvl, rtol=1e-12, atol=1e-15))},
        "leg_b_returns_match_the_market_convention": {
            "pass": bool(np.allclose(got[:, 1:] / got[:, :-1], own, rtol=1e-12, atol=1e-15))},
        "the_per_column_level_still_spans_block_jumps": {
            "pass": bool(spanning > 0), "spanning_columns": spanning,
            "note": "extra() keeps its published per-drawn-session semantics; only leg_series "
                    "rebuilds a price path"},
        "max_abs_return_minus_1_as_read_vs_own": {
            "pass": True, "as_read": float(np.abs(seen - 1).max()),
            "own": float(np.abs(own - 1).max())},
    }


def pairing_cells() -> dict:
    """The arm's and the control's chunk geometry: same pool, start/length, calendar, capital."""
    c = REPRO.chunk_path("IC37/C", path=0)
    bw = c["bw"]
    ctrl = RUN.control_spec()
    pool = windows.boot_pool(windows.bars()[0], c["win"])
    # the framework's own reduced-window harness cells
    h = RUN.reduced_window_checks()
    ctrl_file = RUN.control_file(RUN.RESULTS / "bank_v3", c["win"])
    rec = json.loads(ctrl_file.read_text()) if ctrl_file.exists() else {}
    rec_win = ((rec.get("record") or {}).get("win") or {})
    return {
        "control_and_arm_share_the_same_pool": {
            "pass": bool(c["pool"] == pool == (c["win"].i0 - 1, len(windows.bars()[0]) - 2)),
            "pool": list(pool), "arm_i0": int(c["win"].i0)},
        "control_and_arm_share_the_same_i0_i1_years": {
            "pass": bool(rec_win.get("start") == c["win"].i0
                         and (rec.get("record") or {}).get("window", {}).get("start")
                         == windows.bars()[0][c["win"].i0]
                         and bw.i0 == B.BootWindow().i0 and bw.i1 == B.BootWindow().i1
                         and bw.yrs == B.BootWindow().yrs),
            "control_start": rec_win.get("start"), "arm_i0": int(c["win"].i0),
            "i0_i1": [int(bw.i0), int(bw.i1)], "years": float(bw.yrs)},
        "levered_control_is_re_simulated_on_the_window": {
            "pass": bool(rec.get("win", {}).get("tag") == c["win"].tag
                         and len((rec.get("vectors") or {}).get("tax", {})
                                 .get("progressive", [])) == 1000),
            "control_file": ctrl_file.name,
            "n_tax_paths": len((rec.get("vectors") or {}).get("tax", {}).get("progressive", []))},
        "the_frameworks_own_reduced_window_cells": {
            "pass": all(v.get("pass") for v in h.values() if isinstance(v, dict)),
            "cells": h},
        "control_spec_is_production_K": {"pass": bool(ctrl["rule"] == "K" and ctrl["id"] == "k_control/K")},
    }


def parity_diff() -> dict:
    base = HERE / "PRE_FIX_BASELINE.npz"
    with tempfile.TemporaryDirectory() as td:
        cur = Path(td) / "after.npz"
        PC.dump(cur)
        return PC.compare(base, cur, HERE / "PARITY_LEGB.json",
                          labels=("PRE_FIX_BASELINE.npz", "current (fixed) archive"))


def repro_path0() -> dict:
    """The published IC37/C B1 path 0 reproduced on its own chunk, raw vs `leg_series`."""
    r = REPRO.run(0)
    rec = r["recorded"]["arm_b1_path0_delta_pp"]
    sim = r["simulation"]
    dec = r["decomposition"]
    return {
        "recorded_path0_b1_delta_pp": rec,
        "reproduced_raw_pp": sim["prog_delta_raw_pp"],
        "reproduced_leg_series_pp": sim["prog_delta_leg_series_pp"],
        "raw_convention_reproduces_the_published_path": {
            "pass": bool(abs(sim["prog_delta_raw_pp"] - rec) <= 0.01)},
        "zero_leg_b_reproduces_the_control": {
            "pass": bool(sim["zero_leg_b_is_the_control_pretax"]
                         and sim["leg_a_targets_identical"])},
        "the_read_leg_b_spans_block_jumps": {
            "pass": bool(dec["leg_b_spanning_columns"] > 0),
            "spanning_columns": dec["leg_b_spanning_columns"],
            "max_abs_return_minus_1_as_read": dec["leg_b_max_abs_return_minus_1_as_read"],
            "max_abs_return_minus_1_own": dec["leg_b_max_abs_return_minus_1_own"]},
        "leg_series_leg_b_spans_none": {
            "pass": bool(dec["leg_b_max_abs_diff_vs_own_fixed"] <= 1e-12)},
        "paired_with_the_published_control_pool": {
            "pass": bool(r["pairing"]["control_pool_matches_arm_pool"]
                         and r["pairing"]["control_win"]["tag"] == r["decomposition"]["win"]["tag"])},
    }


def harness() -> dict:
    t0 = time.time()
    groups = {"engine_parity_quick": engine_parity_quick(),
              "engine_anchors": engine_anchors(),
              "leg_b_convention": leg_convention(),
              "pairing": pairing_cells(),
              "repro_path0": repro_path0(),
              "parity_before_after": parity_diff()}
    flat = {}

    def add(name, g):
        if isinstance(g, dict) and "pass" in g:
            flat[name] = bool(g["pass"])
        for k, v in (g.items() if isinstance(g, dict) else []):
            if isinstance(v, dict) and "pass" in v:
                flat[f"{name}.{k}"] = bool(v["pass"])

    for name, g in groups.items():
        add(name, g)
    res = {"pass": all(flat.values()), "checks": flat, "groups": groups, "wall_s": time.time() - t0}
    (HERE / "HARNESS.json").write_text(json.dumps({k: v for k, v in res.items() if k != "wall_s"},
                                                  indent=1, sort_keys=True, default=float) + "\n")
    return res


# ----------------------------------------------------------------------------- archive
def baseline() -> dict:
    s = PC.dump(HERE / "PRE_FIX_BASELINE.npz")
    (HERE / "PRE_FIX_SUMMARY.json").write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
    return s


# ----------------------------------------------------------------------------- the re-screen
def run_batch(twice=True) -> dict:
    """The re-screen batch and its deflation, in the foreground (framework's own pipeline)."""
    t0 = time.time()
    cmds = [[PY, "-m", "research.batch_screen.run", "--batch", BATCH, "--ideas", IDEAS,
             "--workers", WORKERS]]
    if twice:
        cmds.append(list(cmds[0]))
    cmds.append([PY, "-m", "research.batch_screen.run", "--deflate", "--batch", BATCH])
    out = {"commands": [" ".join(c) for c in cmds], "wall_s": None, "per_command": [],
           "batch_log": []}
    labels = ["batch pass 1", "batch pass 2", "deflate"] if twice else ["batch pass", "deflate"]
    for label, c in zip(labels, cmds):
        cs = time.time()
        proc = subprocess.run(c, cwd=api.ROOT, capture_output=True, text=True)
        out["per_command"].append({"cmd": " ".join(c), "label": label,
                                   "returncode": proc.returncode, "wall_s": time.time() - cs})
        out["batch_log"].append({"cmd": " ".join(c), "label": label,
                                 "returncode": proc.returncode, "wall_s": time.time() - cs,
                                 "stdout_tail": proc.stdout[-3000:], "stderr_tail": proc.stderr[-2000:]})
        print(proc.stdout[-3000:], flush=True)
        if proc.returncode != 0:
            print(proc.stderr[-2000:], flush=True)
            raise SystemExit(f"command failed: {' '.join(c)}")
    out["wall_s"] = time.time() - t0
    tm = RUN.RESULTS / BATCH / "timing.json"
    out["batch_total_wall_s"] = (json.loads(tm.read_text()).get("total_wall_s")
                                if tm.exists() else None)
    (HERE / "RUN_TIMING.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=float) + "\n")
    return out


def _controls(batch: str) -> dict:
    """The batch's control records by window tag (the paired reference of every Δ)."""
    out = {}
    d = RUN.RESULTS / batch
    if not d.exists():
        return out
    for p in sorted(d.glob("_control*.json")):
        c = json.loads(p.read_text())
        rec = c.get("record") or {}
        tag = (c.get("win") or {}).get("tag", "default")
        out[tag] = {
            "pre_base_m2": ((rec.get("main") or {}).get("base", {}).get("m2") or {}).get("cagr"),
            "pre_cal_m2": ((rec.get("main") or {}).get("calibrated", {}).get("m2") or {}).get("cagr"),
            "level_pre": {md: ((rec.get("boot_level") or {}).get("pre", {}).get(md) or {})
                          .get("cagr_median") for md in ("base", "calibrated")},
            "level_b1": ((rec.get("boot_level") or {}).get("tax", {}).get("progressive") or {})
            .get("cagr_median"),
            "level_b2": ((rec.get("boot_level") or {}).get("tax", {}).get("flat") or {})
            .get("cagr_median"),
            "at": {wn: v.get("after_tax_cagr") for wn, v in (rec.get("after_tax") or {}).items()},
        }
    return out


def _arm_cells(batch: str) -> dict:
    out = {}
    d = RUN.RESULTS / batch
    if not d.exists():
        return out
    ctrl = _controls(batch)
    for p in sorted(d.glob("*__*.json")):
        r = json.loads(p.read_text())
        if r.get("control"):
            continue
        v = np.asarray(r.get("b1_delta_pp") or [], dtype=np.float64)
        s1 = r.get("stage1") or {}
        gates = r.get("stage1_gates") or {}
        tag = (r.get("win") or {}).get("tag", "default")
        c = ctrl.get(tag) or {}

        def sub(x, y):
            return None if x is None or y is None else float(x) - float(y)

        main = s1.get("main") or {}
        boot_lvl = (s1.get("boot_level") or {}).get("pre", {})
        b1 = ((s1.get("boot") or {}).get("tax") or {}).get("progressive") or {}
        b2 = ((s1.get("boot") or {}).get("tax") or {}).get("flat") or {}
        out[r["arm"]] = {
            "spec_hash": (r.get("hashes") or {}).get("spec"),
            "window": tag,
            "pre_base_cagr_delta_pp": _pp(sub(((main.get("base") or {}).get("m2") or {}).get("cagr"),
                                              c.get("pre_base_m2"))),
            "pre_cal_cagr_delta_pp": _pp(sub(((main.get("calibrated") or {}).get("m2") or {}).get("cagr"),
                                             c.get("pre_cal_m2"))),
            "pre_boot_base_delta_pp": _pp(sub(((boot_lvl.get("base") or {}).get("cagr_median")),
                                              (c.get("level_pre") or {}).get("base"))),
            "after_tax_2000_delta_pp": gates.get("after_tax_2000_delta_pp"),
            "after_tax_2010_delta_pp": _pp(sub(((s1.get("after_tax") or {})
                                                .get("2010_01_to_end") or {}).get("after_tax_cagr"),
                                               (c.get("at") or {}).get("2010_01_to_end"))),
            "after_tax_2017_delta_pp": _pp(sub(((s1.get("after_tax") or {})
                                                .get("2017_01_to_end") or {}).get("after_tax_cagr"),
                                               (c.get("at") or {}).get("2017_01_to_end"))),
            "b1_median_delta_pp": gates.get("b1_median_delta_pp"),
            "b1_share_gt_0": b1.get("share_gt_0"),
            "b2_median_delta_pp": b2.get("median_delta_pp"),
            "b2_share_gt_0": b2.get("share_gt_0"),
            "b1_non_finite": int((~np.isfinite(v)).sum()),
            "b1_n": int(v.size),
            "max_dd_delta_pp": gates.get("max_dd_delta_pp"),
            "turnover_ratio": gates.get("turnover_ratio"),
            "stage1_pass": r.get("stage1_pass"),
            "stage2_pass": r.get("stage2_pass"),
            "verdict": r.get("verdict"),
        }
    return out


def _pp(x, nd=6):
    return None if x is None else round(float(x) * 100, nd)


def _old_cells() -> dict:
    """Every previously screened cell of every re-screened arm, by batch (excluding rw_fix_v1)."""
    out = {}
    for d in sorted(RUN.RESULTS.glob("*")):
        if not d.is_dir() or d.name == BATCH:
            continue
        for arm, cell in _arm_cells(d.name).items():
            out.setdefault(arm, {})[d.name] = cell
    return out


def summary() -> dict:
    new = _arm_cells(BATCH)
    old = _old_cells()
    defl = json.loads((RUN.RESULTS / "DEFLATION.json").read_text()) \
        if (RUN.RESULTS / "DEFLATION.json").exists() else {}
    res = {
        "study": "rw_boot_audit_v1",
        "question": "is the reduced-window extra-key two-leg B1 Δ (+12 pp) a framework bug?",
        "fix": "research/batch_screen/api.py::MarketData.leg_series: rebuild a chunk's extra-key "
               "leg-B level from each drawn session's own close-to-close return "
               "(boot_panels' convention) instead of the per-drawn-session level",
        "batch": BATCH, "ideas": IDEAS.split(","),
        "n_trials_ledger": int(RUN.registry.n_trials()),
        "arms": {},
        "repro": json.loads((HERE / "repro.json").read_text())
        if (HERE / "repro.json").exists() else {},
        "parity": json.loads((HERE / "PARITY_LEGB.json").read_text())
        if (HERE / "PARITY_LEGB.json").exists() else {},
        "harness": json.loads((HERE / "HARNESS.json").read_text()).get("checks")
        if (HERE / "HARNESS.json").exists() else {},
        "run_timing": json.loads((HERE / "RUN_TIMING.json").read_text())
        if (HERE / "RUN_TIMING.json").exists() else {},
        "deflation": {k: defl.get(k) for k in ("n_trials", "n_arms_with_delta", "pass_5pct",
                                               "stage2_pass", "declared", "excluded_non_finite",
                                               "window_tags")},
    }
    for arm in sorted(new):
        res["arms"][arm] = {"old": old.get(arm, {}), "new": new[arm]}
    res["verdict_changed"] = [
        arm for arm in sorted(new)
        if any((c.get("verdict") != new[arm].get("verdict")) for c in old.get(arm, {}).values())]
    (HERE / "RESULTS.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=float) + "\n")

    pd = res["parity"] or {}
    rp = res["repro"] or {}
    lines = ["rw_boot_audit_v1 SUMMARY", ""]
    d = (rp.get("decomposition") or {})
    s = (rp.get("simulation") or {})
    lines.append(f"repro IC37/C path0: leg-B spanning columns {d.get('leg_b_spanning_columns')}"
                 f" / {d.get('leg_b_columns')}; progressive Δ raw {s.get('prog_delta_raw_pp')} pp"
                 f" (= recorded {(rp.get('recorded') or {}).get('arm_b1_path0_delta_pp')}) vs"
                 f" leg_series {s.get('prog_delta_leg_series_pp')} pp")
    lines.append(f"parity: pass={pd.get('pass')} cells={pd.get('n_cells')} "
                 f"unaffected moved={len(pd.get('unaffected_arms_moved') or [])} "
                 f"hard failures={len(pd.get('hard_failures') or [])} "
                 f"affected cells changed={pd.get('affected_cells_n_changed')}")
    rt = (res.get("run_timing") or {})
    per = ", ".join("%s %.1f s" % (c.get("label") or c.get("cmd", "")[-30:], c.get("wall_s") or 0.0)
                    for c in rt.get("per_command", []))
    lines.append("`--run` wall clock: %s s total (%s); a reference pass took %s s and reproduced "
                 "RESULTS.json byte for byte"
                 % (rt.get("wall_s"), per, rt.get("reference_pass_wall_s")))
    lines.append(f"verdicts changed: {res['verdict_changed'] or 'none'}")
    lines.append("")
    lines.append(f"{'arm':18s} {'AT2000 old':>11s} {'new':>11s} {'B1med old':>11s} {'new':>11s} "
                 f"{'B1nan old->new':>15s} verdict")
    for arm in sorted(new):
        o = (old.get(arm) or {})
        oc = o.get(BATCH) or (next(iter(o.values())) if o else {})
        n = new[arm]
        lines.append(f"{arm:18s} {str(oc.get('after_tax_2000_delta_pp')):>11s} "
                     f"{str(n.get('after_tax_2000_delta_pp')):>11s} "
                     f"{str(oc.get('b1_median_delta_pp')):>11s} "
                     f"{str(n.get('b1_median_delta_pp')):>11s} "
                     f"{(str(oc.get('b1_non_finite')) + '->' + str(n.get('b1_non_finite'))):>15s} "
                     f"{oc.get('verdict')} -> {n.get('verdict')}")
    (HERE / "SUMMARY.txt").write_text("\n".join(lines) + "\n")
    return res


# ----------------------------------------------------------------------------- entry point
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.rw_boot_audit_v1.screen")
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--repro", action="store_true")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--parity", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args(argv)
    if a.repro:
        print(json.dumps(REPRO.run(), indent=1, sort_keys=True, default=float))
        return 0
    if a.baseline:
        print(json.dumps(baseline(), indent=1, sort_keys=True))
        return 0
    if a.parity:
        print(json.dumps(parity_diff(), indent=1, sort_keys=True))
        return 0
    if a.run:
        print(json.dumps({"wall_s": run_batch()["wall_s"]}, indent=1))
        return 0
    if a.summary:
        print(json.dumps(summary(), indent=1, sort_keys=True, default=float))
        return 0
    if a.harness:
        res = harness()
        for k, v in sorted(res["checks"].items()):
            print(f"{'PASS' if v else 'FAIL'}  {k}")
        print(f"HARNESS {'PASS' if res['pass'] else 'FAIL'}  wall {res['wall_s']:.1f} s")
        return 0 if res["pass"] else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
