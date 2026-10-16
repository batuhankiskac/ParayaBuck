"""opts_nan_fix_v1 -- the engine entry point for the chunk-leg NaN fix and its re-screen.

    python -m research.opts_nan_fix_v1.screen --harness   # parity --quick, K anchors, chunk-leg
                                                          # stats, the before/after parity diff
    python -m research.opts_nan_fix_v1.screen --parity    # (re)diff the current bootstrap archive
                                                          # against the committed pre-fix baseline
    python -m research.opts_nan_fix_v1.screen --run       # the re-screen batch `opts_fix_v1`
    python -m research.opts_nan_fix_v1.screen --summary   # RESULTS.json + SUMMARY.txt

Everything runs on the mandatory Numba engine `research/fast_engine` (`fe_sim`/`fe_study`) and the
framework `research/batch_screen`; the one code change is in the engine copy
`research/fast_engine_opts/fe_opts_legs.chunk_triple` (see `PREREGISTRATION.md` and
`docs/research/opts_nan_fix_v1.md`). No target-weight array is built here: the arms are the
registered IC56/IC40/IC31/IC35 specs, screened unchanged by the framework's own pipeline.
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

from research.batch_screen import api, run as RUN
from research.fast_engine import fe_data, fe_sim, fe_study
from research.fast_engine.fe_config import FIN, TAX_CAPITALS, TAX_WINDOWS
from research.opts_nan_fix_v1 import parity_cells as PC

HERE = Path(__file__).resolve().parent
PY = sys.executable
BATCH = "opts_fix_v1"
IDEAS = "IC56,IC40,IC31,IC35"
ANCHOR_TOL_PP = 0.01
PUB = api.ROOT / "research/band_h2_combo_v1/RESULTS.json"


# ----------------------------------------------------------------------------- engine checks
def engine_parity_quick() -> dict:
    """`python -m research.fast_engine.parity --quick`, with the engine's PARITY.json restored."""
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
    """K's (and R2's) published cells through `fe_sim.simulate` -- the engine, not the option copy.

    Pre-tax base/calibrated CAGR and max DD on the main window (`m = 2`), and the after-tax $100k
    progressive CAGR on the 2000/2010/2017 windows, against `band_h2_combo_v1/RESULTS.json`.
    """
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


def parity_diff() -> dict:
    """Dump the current bootstrap archive and diff it against the committed pre-fix baseline."""
    base = HERE / "PRE_FIX_BASELINE.npz"
    with tempfile.TemporaryDirectory() as td:
        cur = Path(td) / "after.npz"
        PC.dump(cur)
        return PC.compare(base, cur, HERE / "PARITY_FIX.json")


def harness() -> dict:
    t0 = time.time()
    groups = {"engine_parity_quick": engine_parity_quick(),
              "engine_anchors": engine_anchors(),
              "chunk_leg_stats": PC._leg_cells(),
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


# ----------------------------------------------------------------------------- the re-screen
def run_batch() -> dict:
    """The re-screen batch and its deflation, in the foreground (the framework's own pipeline)."""
    t0 = time.time()
    cmds = [
        [PY, "-m", "research.batch_screen.run", "--batch", BATCH, "--ideas", IDEAS,
         "--workers", "4"],
        [PY, "-m", "research.batch_screen.run", "--deflate", "--batch", BATCH],
    ]
    out = {"commands": [" ".join(c) for c in cmds], "wall_s": None, "batch_log": []}
    for c in cmds:
        proc = subprocess.run(c, cwd=api.ROOT, capture_output=True, text=True)
        out["batch_log"].append({"cmd": " ".join(c), "returncode": proc.returncode,
                                 "stdout_tail": proc.stdout[-4000:], "stderr_tail": proc.stderr[-2000:]})
        print(proc.stdout[-4000:], flush=True)
        if proc.returncode != 0:
            print(proc.stderr[-2000:], flush=True)
            raise SystemExit(f"command failed: {' '.join(c)}")
    out["wall_s"] = time.time() - t0
    return out


def _record(path) -> dict:
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text())


def _arm_cells(batch: str) -> dict:
    out = {}
    for p in sorted((RUN.RESULTS / batch).glob("*__*.json")):
        r = json.loads(p.read_text())
        if r.get("control"):
            continue
        v = np.asarray(r.get("b1_delta_pp") or [], dtype=np.float64)
        gates = r.get("stage1_gates") or {}
        out[r["arm"]] = {
            "spec_hash": (r.get("hashes") or {}).get("spec"),
            "after_tax_2000_delta_pp": gates.get("after_tax_2000_delta_pp"),
            "b1_median_delta_pp": gates.get("b1_median_delta_pp"),
            "b1_non_finite": int((~np.isfinite(v)).sum()),
            "b1_n": int(v.size),
            "max_dd_delta_pp": gates.get("max_dd_delta_pp"),
            "turnover_ratio": gates.get("turnover_ratio"),
            "stage1_pass": r.get("stage1_pass"),
            "verdict": r.get("verdict"),
            "status": r.get("status"),
        }
    return out


def summary() -> dict:
    """The study's RESULTS.json: old (bank_v3) vs new (opts_fix_v1) Stage-1 cells per arm."""
    new = _arm_cells(BATCH)
    old = _arm_cells("bank_v3")
    defl = _record(RUN.RESULTS / "DEFLATION.json")
    res = {
        "study": "opts_nan_fix_v1",
        "question": "does repairing the option-leg chunk reconstruction change any verdict?",
        "fix": "research/fast_engine_opts/fe_opts_legs.chunk_triple: step each drawn session from "
               "its own previous close (boot_panels' convention) instead of concatenating raw prices",
        "batch": BATCH, "ideas": IDEAS.split(","),
        "n_trials_ledger": int(RUN.registry.n_trials()),
        "arms": {},
        "parity": _record(HERE / "PARITY_FIX.json"),
        "harness": _record(HERE / "HARNESS.json").get("checks"),
        "deflation": {k: defl.get(k) for k in ("n_trials", "n_arms_with_delta", "pass_5pct",
                                               "stage2_pass", "declared", "excluded_non_finite")},
    }
    for arm in sorted(set(new) | set(old)):
        res["arms"][arm] = {"old_bank_v3": old.get(arm), "new_opts_fix_v1": new.get(arm)}
    # the comparison that matters is the 12 re-run arms (the four ideas), not every bank_v3 arm
    re_run = sorted(a for a in new if a.split("/")[0] in IDEAS.split(","))
    res["re_run_arms"] = re_run
    res["verdict_changed"] = [a for a in re_run
                              if (res["arms"][a]["old_bank_v3"] or {}).get("verdict")
                              != (res["arms"][a]["new_opts_fix_v1"] or {}).get("verdict")]
    # the two-leg ideas (IC31/IC35) must be bit-identical cell for cell -- they never reach the fix
    res["unchanged_arms"] = [
        a for a in re_run if res["arms"][a]["old_bank_v3"] == res["arms"][a]["new_opts_fix_v1"]]
    (HERE / "RESULTS.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=float) + "\n")

    pd = res["parity"] or {}
    lines = [f"opts_nan_fix_v1 SUMMARY", ""]
    lines.append(f"parity: pass={pd.get('pass')} cells={pd.get('n_cells')} "
                 f"non-option bit-identical={not pd.get('non_option_not_bit_identical')} "
                 f"hard_failures={len(pd.get('hard_failures') or [])}")
    lines.append(f"verdicts changed: {res['verdict_changed'] or 'none'}")
    lines.append(f"arms bit-identical to bank_v3: {res['unchanged_arms']}")
    lines.append("")
    lines.append(f"{'arm':10s} {'AT2000 old':>11s} {'new':>11s} {'B1med old':>10s} {'new':>10s} "
                 f"{'B1nan old->new':>15s} {'verdict'}")
    for arm in re_run:
        o, n = res["arms"][arm]["old_bank_v3"] or {}, res["arms"][arm]["new_opts_fix_v1"] or {}
        lines.append(f"{arm:10s} {str(o.get('after_tax_2000_delta_pp')):>11s} "
                     f"{str(n.get('after_tax_2000_delta_pp')):>11s} "
                     f"{str(o.get('b1_median_delta_pp')):>10s} {str(n.get('b1_median_delta_pp')):>10s} "
                     f"{(str(o.get('b1_non_finite')) + '->' + str(n.get('b1_non_finite'))):>15s} "
                     f"{o.get('verdict')} -> {n.get('verdict')}")
    (HERE / "SUMMARY.txt").write_text("\n".join(lines) + "\n")
    return res


# ----------------------------------------------------------------------------- entry point
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.opts_nan_fix_v1.screen")
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--parity", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args(argv)
    if a.parity:
        print(json.dumps(parity_diff(), indent=1, sort_keys=True))
        return 0
    if a.run:
        r = run_batch()
        print(json.dumps({"wall_s": r["wall_s"]}, indent=1))
        return 0
    if a.summary:
        print(json.dumps(summary(), indent=1, sort_keys=True, default=float))
        return 0
    if a.harness:
        res = harness()
        for k, v in sorted(res["checks"].items()):
            print(f"{'PASS' if v else 'FAIL'}  {k}")
        print(f"HARNESS {'PASS' if res['pass'] else 'FAIL'}  wall {res['wall_s']:.1f} s")
        for g in ("chunk_leg_stats",):
            print(g, json.dumps(res["groups"][g], sort_keys=True))
        return 0 if res["pass"] else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
