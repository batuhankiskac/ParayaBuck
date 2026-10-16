"""harness -- the single entry point: engine parity, option parity, K's anchors, the checks.

    python -m research.fast_engine_opts.harness          # everything
    python -m research.fast_engine_opts.harness --quick   # skips the 200-path bootstrap cell

It runs, in order,

  1. `python -m research.fast_engine.parity --quick` (the engine's own mandatory check; its
     PARITY.json is restored byte for byte afterwards),
  2. the option kernel against the engine with every option off (`parity_opts`),
  3. K's published cells -- pre-tax base/calibrated and the three after-tax windows -- *through*
     `simulate_opts`, plus R2 as the reference row,
  4. one check per option (`checks`).

Exit code 0 only if every group passes. The wall clock of the run is printed last (a study that
builds on this package reports it).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from research.batch_screen import api
from research.fast_engine import fe_data, fe_sim, fe_study
from research.fast_engine.fe_config import FIN, TAX_CAPITALS, TAX_WINDOWS

from . import checks as CK
from .fe_opts import Opts
from .fe_opts_sim import simulate_opts
from .parity_opts import off_boot_parity, off_side_by_side

HERE = Path(__file__).resolve().parent
ANCHOR_TOL_PP = 0.01


def engine_parity_quick() -> dict:
    """`python -m research.fast_engine.parity --quick`, with the engine's own PARITY.json restored."""
    out = api.ROOT / "research/fast_engine/PARITY.json"
    before = out.read_bytes() if out.exists() else None
    proc = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"],
                          cwd=api.ROOT, capture_output=True, text=True)
    restored = False
    if before is not None and out.read_bytes() != before:
        out.write_bytes(before)
        restored = True
    tail = (proc.stdout.strip().splitlines() or [proc.stderr[-200:]])[-1]
    # the engine prints its own wall clock in that line; HARNESS_OPTS.json must not carry it
    tail = re.sub(r"in [0-9.]+s", "in <t>s", tail)
    return {"pass": bool(proc.returncode == 0 and "parity PASS" in proc.stdout),
            "tail": tail, "parity_json_restored": restored}


def _cell(got, want, tol_pp=ANCHOR_TOL_PP) -> dict:
    diff_pp = (float(got) - float(want)) * 100
    return {"reference": float(want), "reproduced": float(got), "diff_pp": diff_pp,
            "pass": bool(abs(diff_pp) <= tol_pp)}


def anchors(arms=("K", "C0")) -> dict:
    """K's published main-window and after-tax cells, computed through `simulate_opts`.

    The reference is `research/band_h2_combo_v1/RESULTS.json` (the same file the framework's own
    harness anchors against). `C0` is the R2 reference row. The after-tax windows run on the *base*
    financing inputs, as `fe_study.after_tax_windows` does; only the pre-tax rows vary with the
    financing model.
    """
    pub = json.loads((api.ROOT / "research/band_h2_combo_v1/RESULTS.json").read_text())
    days, o, c, _we, w_r2, _e = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    out = {}
    for md, spread in FIN.items():
        d_, o_, c_, _we2, wr_, _e2 = fe_data.main_inputs(spread)
        env = fe_sim.RunEnv(d_, fx, ufe, fe_study.START, T)
        years = fe_sim.calendar_years(d_[fe_study.START - 1], d_[T])
        for arm in arms:
            r = simulate_opts(o_, c_, wr_, env, TAX_CAPITALS["100k"], fe_study.START, T,
                              arm=arm, mode="pre_tax", m=2, opts=Opts())
            want = pub["pre_tax_main"][md]["m2"][arm]
            cagr = (r["_nav"][-1] / r["_nav"][0]) ** (1 / years) - 1
            dd = float(fe_sim.max_dd(r["_nav"].reshape(1, -1))[0])
            out[f"{arm}_pre_{md}_cagr"] = _cell(cagr, want["cagr"])
            out[f"{arm}_pre_{md}_max_dd"] = _cell(dd, want["max_dd"])
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env2 = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        for arm in arms:
            rq = simulate_opts(o, c, w_r2, env2, TAX_CAPITALS["100k"], i0, i1, arm=arm,
                               mode="progressive", m=2, opts=Opts())
            out[f"{arm}_after_tax_{wn}"] = _cell(
                rq["cagr_usd_liquidated"],
                pub["after_tax_windows"]["100k"][wn][arm]["after_tax_cagr"])
    return out


def mechanism_checks() -> dict:
    """One check per option, on the synthetic fixtures of `checks` (each has its own `pass`)."""
    return {name: fn() for name, fn in CK.CHECKS.items()}


def _flatten(groups) -> dict:
    """Every `pass` the harness reports, group-level ones included (so a group cannot hide)."""
    flat = {}
    for gname, g in groups.items():
        if isinstance(g, dict) and "pass" in g:
            flat[gname] = bool(g["pass"])
        for k, v in (g.items() if isinstance(g, dict) else []):
            if isinstance(v, dict) and "pass" in v:
                flat[f"{gname}.{k}"] = bool(v["pass"])
    return flat


def harness(full=False) -> dict:
    """Every group's result. `wall_s` is measured and *not* stored: the artifact must be
    byte-identical across runs (the wall clock is printed by `main`)."""
    t0 = time.time()
    groups = {
        "engine_parity_quick": engine_parity_quick(),
        "options_off_side_by_side": off_side_by_side(),
        "options_off_bootstrap": off_boot_parity(n_pre=(200 if full else 32),
                                                 n_tax=(200 if full else 32),
                                                 W=(200 if full else 32)),
        "anchors": anchors(),
        "mechanisms": mechanism_checks(),
    }
    flat = _flatten(groups)
    return {"pass": all(flat.values()), "checks": flat, "groups": groups,
            "wall_s": time.time() - t0, "full": bool(full)}


def _stored(res) -> dict:
    """The deterministic part of a harness result (what goes into HARNESS_OPTS.json)."""
    return {k: v for k, v in res.items() if k != "wall_s"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    full = "--full" in argv
    res = harness(full=full)
    for gname, g in res["groups"].items():
        flat = _flatten({gname: g})
        bad = [k for k, v in flat.items() if not v]
        print(f"{gname:28s} {'PASS' if not bad else 'FAIL'}  {len(flat) - len(bad)}/{len(flat)}")
        for k in bad:
            print(f"    FAILED {k}")
    (HERE / "HARNESS_OPTS.json").write_text(
        json.dumps(_stored(res), indent=1, sort_keys=True, default=float) + "\n")
    print(f"\nHARNESS {'PASS' if res['pass'] else 'FAIL'}   wall {res['wall_s']:.1f} s"
          f"   (HARNESS_OPTS.json written)")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
