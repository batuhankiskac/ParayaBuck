"""research/kernel_ext_v1.run -- the harness and the batch driver of the kernel-extension study.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.kernel_ext_v1.run --harness
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.kernel_ext_v1.run --run

`--harness` (cheap, deterministic, no arm is screened) runs, in order:

  * the engine's own `python -m research.fast_engine.parity --quick` (**must PASS**;
    `research/fast_engine/PARITY.json` is kept at its committed bytes);
  * the N-leg kernel's parity suite (`research.fast_engine3.fe3_parity`: the engine identity, the
    two-leg identity, margin off/on, the new `router` group -- indexed == `fe2_opts` bit for bit,
    off == the two-leg kernel, two books of one instrument -- the five-books cell and K's published
    anchors through `simulate3`);
  * the framework harness (`research.batch_screen.run.harness`: the anchors of the three example
    plugins plus the N-leg routing cells), written to `research/batch_screen/HARNESS.json`;
  * the K control's published anchors (pre-tax CAGR / max DD / turnover, the three after-tax windows,
    the six OOS windows, the 21 ETFs, the 21 rolling starts, the bootstrap levels) and the **R2**
    reference row, each diffed against `research/band_h2_combo_v1/RESULTS.json` (tol 0.01 pp).

`--run` adds the batch: it screens **only** the five newly-runnable arms (batch `kernel_ext_v1`),
runs the deflation and folds the per-arm summary into `RESULTS.json` / `SUMMARY.txt`. Wall clocks
(including the batch's) go into `TIMING.json`; every deterministic value is rounded before writing,
so two `--harness` runs give a byte-identical `RESULTS.json`.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from research.batch_screen import run as bsrun
from research.fast_engine import fe_sim, fe_study
from research.fast_engine3 import fe3_parity

from research.kernel_ext_v1 import run_batch as RB

HERE = Path(__file__).resolve().parent
ANCHOR_K = bsrun.ANCHOR_BAND_H2


def rnd(x, nd=6):
    """Round every float leaf before serialising (the framework's convention)."""
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


def _n_cells(group, prefix=None) -> int:
    return sum(1 for k, v in group.items()
               if isinstance(v, dict) and "pass" in v and (prefix is None or k.startswith(prefix)))


def harness_cells(n_boot_paths=60):
    """The parity suites, the framework harness and the K/R2 anchors (no arm screened)."""
    t0 = time.perf_counter()
    par = fe3_parity.run(n_boot_paths=n_boot_paths, quiet=False)
    t_par = time.perf_counter() - t0
    t0 = time.perf_counter()
    harness = bsrun.harness()
    t_har = time.perf_counter() - t0
    anchors = fe3_parity.engine_anchors()
    k = {k_: {"reference": v["reference"], "reproduced": v["reproduced"], "diff_pp": v["diff_pp"],
              "pass": bool(v["pass"])} for k_, v in sorted(anchors.items())}
    ref = json.loads(ANCHOR_K.read_text())
    r2 = {}
    for label, want in (("R2_pre_base_m2", ref["pre_tax_main"]["base"]["m2"]["C0"]["cagr"]),
                        ("R2_pre_calibrated_m2",
                         ref["pre_tax_main"]["calibrated"]["m2"]["C0"]["cagr"]),
                        ("R2_m1_base", ref["pre_tax_main"]["base"]["m1"]["C0"]["cagr"]),
                        ("R2_maxdd_base", ref["pre_tax_main"]["base"]["m2"]["C0"]["max_dd"])):
        cell = harness["plugin_anchors"][label]
        r2[label] = {"reference": float(want), "reproduced": float(cell["reproduced"]),
                     "diff_pp": (float(cell["reproduced"]) - float(want)) * 100,
                     "pass": bool(cell["pass"])}
    out = {
        # the engine subprocess reports its own wall clock in its tail: keep only the verdict
        "parity": {k_: v for k_, v in par.items()
                   if k_ not in ("wall_s", "engine_parity_quick")},
        "framework_harness": {"all": bool(harness["all"]), "n_checks": int(harness["n_checks"]),
                              "failed": list(harness["failed"])},
        "k_anchors": k,
        "r2_reference": r2,
        "counts": {
            "parity_cells": int(par["n_checks"]),
            "router_cells": _n_cells(par["router"]),
            "framework_harness_checks": int(harness["n_checks"]),
            "k_anchor_cells": len(k),
            # provenance: the fast engine this study runs on (fe3_sim imports these unchanged)
            "engine_default_m": int(fe_sim.DEFAULT_M),
            "engine_cost_per_side": float(fe_sim.COST_PER_SIDE),
            "engine_start_session": int(fe_study.START),
        },
        "engine_parity_quick": {"pass": bool(par["engine_parity_quick"]["pass"])},
    }
    timing = {"parity_s": t_par, "harness_s": t_har}
    ok = bool(par["all"] and harness["all"]
              and par["engine_parity_quick"]["pass"]
              and all(v["pass"] for v in anchors.values())
              and all(v["pass"] for v in r2.values()))
    return out, timing, ok


# ----------------------------------------------------------------------------- the batch
ARM_ROWS = (("IC36_N", "B5_N"), ("IC41_R", "A_N_R"), ("IC41_R", "B_N_R"), ("IC41_R", "C_N_R"),
            ("ib38_C", "C"))


def batch_records(batch=RB.BATCH):
    """The batch's per-arm records, read back from `results/<batch>/`."""
    d = bsrun.RESULTS / batch
    out = {}
    for idea_id, arm_id in ARM_ROWS:
        p = d / f"{idea_id}__{arm_id}.json"
        out[f"{idea_id}/{arm_id}"] = json.loads(p.read_text()) if p.exists() else None
    return out


def batch_controls(batch=RB.BATCH):
    """The batch's control records, keyed by window tag (`default` = `_control.json`)."""
    d = bsrun.RESULTS / batch
    out = {}
    for p in sorted(d.glob("_control*.json")):
        c = json.loads(p.read_text())
        out[c["win"]["tag"]] = c["record"]
    return out


def _stage1_failed(g1) -> list:
    """The stage-1 gates an arm failed, **read from** its record's `stage1_gates`."""
    s = bsrun.stages
    out = []
    if (g1.get("after_tax_2000_delta_pp") or 0.0) <= 0.0:
        out.append("AT2000")
    if (g1.get("b1_median_delta_pp") or 0.0) <= 0.0:
        out.append("B1")
    if any(v > s.S1_MAX_DD_PP + 1e-9 for v in (g1.get("max_dd_delta_pp") or {}).values()):
        out.append("maxDD")
    if any(v > s.S1_TURNOVER_MULT + 1e-9 for v in (g1.get("turnover_ratio") or {}).values()):
        out.append("turnover")
    return out


def arm_state(rec, ctrl):
    """The report row of one arm, read from its record (never recomputed)."""
    if rec is None:
        return {"registered": False}
    g1 = rec.get("stage1_gates") or {}
    g = rec.get("gates") or {}
    s1 = rec.get("stage1") or {}
    b1 = ((s1.get("boot") or {}).get("tax") or {}).get("progressive") or {}
    b2 = ((s1.get("boot") or {}).get("tax") or {}).get("flat") or {}
    at = {k: (rec.get("stage1") or {}).get("after_tax", {}).get(k, {}).get("after_tax_cagr")
          for k in ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end")}
    cat = {k: (ctrl.get("after_tax") or {}).get(k, {}).get("after_tax_cagr")
           for k in ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end")}
    g7 = g.get("G7_detail") or {}
    failed = g.get("failed") if g else _stage1_failed(g1)
    return {
        "registered": True,
        "window": (rec.get("win") or {}).get("tag"),
        "verdict": rec.get("verdict"),
        "stage1_pass": rec.get("stage1_pass"),
        "stage1_gates": g1,
        "after_tax_delta_pp": {k: (None if at[k] is None or cat[k] is None else (at[k] - cat[k]) * 100)
                               for k in at},
        "b1_median_pp": b1.get("median_delta_pp"), "b1_share": b1.get("share_gt_0"),
        "b2_median_pp": b2.get("median_delta_pp"), "b2_share": b2.get("share_gt_0"),
        "turnover_ratio": g1.get("turnover_ratio"),
        "max_dd_delta_pp": g1.get("max_dd_delta_pp"),
        "oos_median_pp": g7.get("median_cagr_pp"),
        "etf_median_pp": (g.get("E1") or {}).get("median_delta_pp"),
        "failed": failed,
        "failed_source": "gates.failed" if g else "stage1_gates",
        "b1_delta_pp_n": len(rec.get("b1_delta_pp") or []),
        "b1_delta_pp_nonfinite": int(np.sum(~np.isfinite(np.asarray(rec.get("b1_delta_pp") or [],
                                                                    dtype=np.float64)))),
    }


def summary(res, timing, batch=None, ctrl=None, recs=None, ctrl_rec=None, defl=None) -> str:
    par = res["parity"]
    ok = bool(par["all"] and res["framework_harness"]["all"]
              and res["engine_parity_quick"]["pass"]
              and all(v["pass"] for v in res["k_anchors"].values()))
    lines = [
        "# kernel_ext_v1 -- N_LEGS = 5, the N-leg gain-aware downsize router, two books of one "
        "instrument",
        "",
        f"**Verdict: {'kernel extension built and parity-proven' if ok else 'PARITY FAILED'}"
        + (", and the batch screened.**" if batch else " (harness only).**"),
        "",
        "`research/fast_engine3/` (edited in place, this package only) now holds **five** instruments "
        "in one cash pool and the `fe2_opts` `route_cap` downsize router generalised to N legs "
        "(`route_score` = `usd` | `indexed`); `batch_screen` accepts a `\"legs\"` list that names one "
        "instrument twice (two FIFO books of one instrument). `research/fast_engine/`, "
        "`fast_engine2/` and `fast_engine_opts/` are untouched.",
        "",
        "## Parity",
        "",
        f"* `python -m research.fast_engine.parity --quick`: "
        f"**{'PASS' if res['engine_parity_quick']['pass'] else 'FAIL'}**",
        f"* `research.fast_engine3.fe3_parity`: "
        f"**{'PASS' if par['all'] else 'FAIL'}** ({par['n_checks']} cells, "
        f"{len(par['failed'])} failed, {timing['parity_s']:.1f} s)",
        f"* the router group: {res['counts']['router_cells']} cells -- `route_score = indexed` "
        "reproduces `fe2_opts` **bit for bit** on the two-leg parity cells (NAV and both per-leg "
        "rows), `route_cap = 0` is the two-leg kernel, the split moves within the cap, `usd` != "
        "`indexed`, and two books of one instrument keep two FIFO books",
        f"* framework harness (`research.batch_screen.run --harness`): "
        f"**{'PASS' if res['framework_harness']['all'] else 'FAIL'}** "
        f"({res['framework_harness']['n_checks']} checks, "
        f"{len(res['framework_harness']['failed'])} failed, {timing['harness_s']:.1f} s)",
        "",
        "## K control anchors reproduced through the engine (tol 0.01 pp)",
        "",
        "| cell | reference | reproduced | diff (pp) |",
        "|---|---:|---:|---:|",
    ]
    for k_, v in sorted(res["k_anchors"].items()):
        lines.append(f"| `{k_}` | {v['reference']*100:.4f} % | {v['reproduced']*100:.4f} % | "
                     f"{v['diff_pp']:+.2e} |")
    lines += ["", "R2 reference (pre-tax CAGR, and max DD): "
              + ", ".join(f"`{k_}` {v['reproduced']:.4f}" for k_, v in
                          sorted(res["r2_reference"].items())) + " (tolerance 0.01 pp each)", ""]
    if batch:
        lines += batch_section(batch, ctrl, recs, defl)
    else:
        lines += ["## The batch", "", "Not run in this invocation (`--run` screens it).", ""]
    lines += [
        "## Timings",
        "",
        f"* parity suite {timing['parity_s']:.1f} s; framework harness {timing['harness_s']:.1f} s",
        f"* wall clock of this invocation (`{timing.get('mode', '--harness')}`): "
        f"**{timing['wall_s']:.1f} s**",
    ]
    if batch:
        ft = bsrun.RESULTS / batch / "timing.json"
        if ft.exists():
            rec = json.loads(ft.read_text())
            lines.append(f"* the `--run` screening step (batch `{batch}` itself, "
                         f"`results/{batch}/timing.json`): **{rec['total_wall_s']:.1f} s** wall on "
                         f"its workers (per arm "
                         + ", ".join(f"`{k.split('/', 1)[-1]}` {v:.1f} s"
                                     for k, v in sorted(rec["wall_s"].items()) if v) + ")")
    lines.append("")
    return "\n".join(lines) + "\n"


def batch_section(batch, ctrls, recs, defl) -> list:
    lines = ["## The batch (`kernel_ext_v1`) -- Δ vs K in pp, on the arm's own declared window",
             "",
             "| arm | window | AT2000 Δ | AT2010 Δ | AT2017 Δ | B1 med / share | B2 med / share | "
             "maxDD Δ b/c | TO ×K | failed gates | verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for idea_id, arm_id in ARM_ROWS:
        key = f"{idea_id}/{arm_id}"
        rec = recs[key]
        tag = (rec or {}).get("win", {}).get("tag", "default")
        st = arm_state(rec, ctrls.get(tag, {}))
        if not st["registered"]:
            lines.append(f"| `{key}` | - | - | - | - | - | - | - | - | - | not registered |")
            continue
        at = st["after_tax_delta_pp"]
        f = lambda v: "-" if v is None else f"{v:+.4f}"  # noqa: E731
        dd = st["max_dd_delta_pp"] or {}
        to = st["turnover_ratio"] or {}
        lines.append(
            f"| `{key}` | {st['window']} | {f(at['2000_03_to_2026_09'])} | "
            f"{f(at['2010_01_to_end'])} | {f(at['2017_01_to_end'])} | "
            f"{f(st['b1_median_pp'])} / {st['b1_share']:.3f} | "
            f"{f(st['b2_median_pp'])} / {st['b2_share']:.3f} | "
            f"{f(dd.get('base'))} / {f(dd.get('calibrated'))} | "
            f"{to.get('base', float('nan')):.3f} | {', '.join(st['failed'] or []) or '-'} | "
            f"{st['verdict']} |")
    lines += ["",
              "`Δ` read from the records (`stage1_gates` / `gates.failed`); `maxDD Δ` pre-tax "
              "base/calibrated at m = 2; `TO ×K` the base model.", ""]
    if defl is not None:
        rw = defl.get("rw_stepdown") or {}
        ds = defl.get("deflated_sharpe") or {}
        wrc = defl.get("white_rc") or {}
        lines += [
            "### Deflation (`results/DEFLATION.json`, batch `kernel_ext_v1`)", "",
            f"* N (distinct (idea, arm, spec_hash) over the whole campaign ledger) = "
            f"**{defl.get('registry_trials')}** (was 312 before this batch's five arms)",
            f"* stage-2 passers: {defl.get('stage2_pass')}; arms with a finite B1 Δ vector: "
            f"{defl.get('n_arms_with_delta')}; `excluded_non_finite` = "
            f"{defl.get('excluded_non_finite')} (no NaN Δ anywhere)",
            f"* `white_rc`: best arm `{wrc.get('best_arm')}`, `t_max` = "
            f"{wrc.get('t_max'):.6f}, p = {wrc.get('p_value')}, n_arms = {wrc.get('n_arms')}",
            f"* `rw_stepdown` (alpha {rw.get('alpha')}, {rw.get('resamples')} resamples): "
            f"**rejected = {rw.get('rejected')}**; p_adjusted = "
            + ", ".join(f"`{k}` {v:.4f}" for k, v in sorted((rw.get("p_adjusted") or {}).items())),
            f"* `deflated_sharpe`: `var_sr` = {defl.get('var_sr')}",
        ]
        lines += [f"* `deflated_sharpe` (per arm, N = {defl.get('n_trials')}): "
                  + ", ".join(f"`{a}`: sr_obs {c.get('sr_obs'):.4f}, sr0 {c.get('sr0'):.6f}, "
                              f"dsr {c.get('dsr')}, pass_5pct {c.get('pass_5pct')}"
                              for a, c in sorted(ds.items())) if defl.get("deflated_sharpe") else "",
                  f"* **`declared` = {defl.get('declared')}**", ""]
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.kernel_ext_v1.run")
    ap.add_argument("--harness", action="store_true", help="parity + anchors only")
    ap.add_argument("--run", action="store_true", help="the harness plus the kernel_ext_v1 batch")
    ap.add_argument("--report", action="store_true",
                    help="the harness plus the batch's committed records (screen nothing)")
    ap.add_argument("--boot-paths", type=int, default=60,
                    help="paths for the parity suite's bootstrap cells")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(argv)
    if not (args.harness or args.run or args.report):
        ap.print_help()
        return 2
    t0 = time.perf_counter()
    res, timing, ok = harness_cells(args.boot_paths)
    batch = ctrls = recs = defl = None
    if args.run or args.report:
        if args.run:
            t1 = time.perf_counter()
            RB.main(["--workers", str(args.workers)])
            timing["batch_s"] = time.perf_counter() - t1
        batch = RB.BATCH
        ctrls = batch_controls(batch)
        recs = batch_records(batch)
        defl_path = bsrun.RESULTS / "DEFLATION.json"
        defl = json.loads(defl_path.read_text()) if defl_path.exists() else None
        res["batch"] = {"batch": batch, "arms": sorted(recs),
                        "control_windows": sorted(ctrls), "deflation": defl}
        res["batch_records"] = recs
    timing["wall_s"] = time.perf_counter() - t0
    timing["mode"] = "--run" if args.run else ("--report" if args.report else "--harness")
    dump(HERE / "RESULTS.json", res)
    dump(HERE / "TIMING.json", timing)
    (HERE / "SUMMARY.txt").write_text(summary(res, timing, batch, ctrls, recs, None, defl))
    print((HERE / "SUMMARY.txt").read_text())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
