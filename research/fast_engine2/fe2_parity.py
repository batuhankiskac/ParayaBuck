"""fe2_parity -- the two-leg kernel's parity cells against `research/fast_engine`.

Parity (a): with leg B identically zero (`opens`, `closes`, `wants` all zero) the two-leg kernel
must reproduce `fe_sim.simulate` **bit for bit** -- `max_abs_diff == 0.0`, on the whole NAV path,
on every reported scalar (including the nested per-year dicts) and on every bootstrap path. That
is the contract `fe2_sim.sim_one == fe2_sim.sim2|leg B = 0`; the checks here assert it on the real
main window (pre-tax, progressive and flat, m = 1/2/4 and both financing models), on the three
after-tax windows at three capitals, with a non-zero idle-cash schedule, and on the clean block
bootstrap.

`engine_parity()` additionally runs the engine's own `python -m research.fast_engine.parity
--quick` (the required PASS) plus the engine's published K anchors through `fe_study`.

Parity (b) -- the two-instrument after-tax case against `riskoff_after_tax_v1`'s engine -- lives
in `research/two_leg_kernel_v1/parity_b.py`: it needs that study's reference implementation, which
belongs with the study, not with the engine.
"""

from __future__ import annotations

import subprocess
import sys

import numpy as np

from research.fast_engine import fe_boot, fe_data, fe_sim, fe_study
from research.fast_engine.fe_config import FIN, TAX_CAPITALS, TAX_WINDOWS

from . import fe2_boot, fe2_sim

ARMS = ("C0", "A1u", "H2", "K")
MODES = ("pre_tax", "progressive", "flat")
ZERO = None  # filled per window length


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


def _zeros(n):
    return np.zeros(n, dtype=np.float64)


def main_window(ms=(1, 2, 4), arms=ARMS, modes=MODES):
    """Pre-tax/after-tax main window, base and calibrated, every rule and mode."""
    out = {}
    for fin, model in ((0.0, "base"), (fe_study.FIN["calibrated"], "calibrated")):
        days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(fin)
        fx, ufe = fe_study.try_market(days)
        i0, i1 = fe_study.START, len(days) - 1
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        z = _zeros(len(days))
        for arm in arms:
            for mode in modes:
                for m in ms:
                    a = fe_sim.simulate(o, c, w_r2, env, 100_000.0, i0, i1, arm=arm, mode=mode, m=m)
                    b = fe2_sim.simulate2(o, c, w_r2, z, z, z, env, 100_000.0, i0, i1,
                                          arm=arm, mode=mode, m=m)
                    nav = float(np.max(np.abs(a["_nav"] - b["_nav"])))
                    scal, where = _maxdiff(a, b)
                    key = f"{model}_{arm}_{mode}_m{m}"
                    out[key] = {"nav_max_abs_diff": nav, "scalar_max_abs_diff": scal,
                                "scalar_worst_key": where,
                                "pass": bool(nav == 0.0 and scal == 0.0)}
    return out


def after_tax_windows(capitals=("10k", "100k", "1M"), arms=("C0", "K"),
                      modes=("progressive", "flat")):
    """The three published after-tax windows at several capitals (regime + YI-UFE + instalments)."""
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
                    rb = fe2_sim.simulate2(o, c, w_r2, z, z, z, env, capital, i0, i1, arm=arm,
                                           mode=mode, m=2)
                    nav = float(np.max(np.abs(ra["_nav"] - rb["_nav"])))
                    scal, where = _maxdiff(ra, rb)
                    out[f"{wn}_{cap}_{arm}_{mode}"] = {
                        "nav_max_abs_diff": nav, "scalar_max_abs_diff": scal,
                        "scalar_worst_key": where, "pass": bool(nav == 0.0 and scal == 0.0),
                    }
    return out


def cash_yield_cell():
    """A non-zero idle-cash schedule (the `cash_yield` knob) with leg B at zero."""
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
        rb = fe2_sim.simulate2(o, c, w_r2, z, z, z, env, 100_000.0, i0, i1, arm=arm,
                               mode="progressive", m=2)
        nav = float(np.max(np.abs(ra["_nav"] - rb["_nav"])))
        scal, where = _maxdiff(ra, rb)
        out[f"{arm}_cash_on"] = {"cagr": ra["cagr_usd_liquidated"], "nav_max_abs_diff": nav,
                                 "scalar_max_abs_diff": scal, "scalar_worst_key": where,
                                 "pass": bool(nav == 0.0 and scal == 0.0)}
    return out


def bootstrap(n_paths=300, arms=("C0", "K")):
    """`fe_boot.boot` vs `fe2_boot.boot2` with leg B at zero: every pre-tax and after-tax path."""
    theirs = fe_boot.boot(seed=fe_boot.SEED, block=fe_boot.BLOCK, arms=arms, n_pre=n_paths,
                          n_tax=n_paths, capitals=("100k",), modes=("progressive", "flat"),
                          anchor_e2=False, deltas=("C0",), quiet=True, keep_paths=True)
    mine = fe2_boot.boot2(
        [{"id": a, "rule": a, "wants": fe2_boot.leg_a_only} for a in arms],
        fe2_boot.chunk_series(), seed=fe_boot.SEED, block=fe_boot.BLOCK, n_pre=n_paths,
        n_tax=n_paths, capitals=("100k",), modes=("progressive", "flat"), quiet=True,
    )
    out = {"n_paths": n_paths}
    for a in arms:
        for md in ("base", "calibrated"):
            d = float(np.max(np.abs(mine["pre"][md][a] - theirs["paths"]["pre"][(md, a)])))
            out[f"{a}_pre_{md}"] = {"max_abs_diff": d, "pass": bool(d == 0.0)}
        for mode in ("progressive", "flat"):
            d = float(np.max(np.abs(mine["tax"]["100k"][mode][a]
                                    - theirs["paths"]["tax"]["100k"][mode][a])))
            out[f"{a}_tax_{mode}"] = {"max_abs_diff": d, "pass": bool(d == 0.0)}
    return out


def engine_anchors(tol_pp=0.01):
    """The published K cells, reproduced through the two-leg kernel with leg B = 0.

    `research/band_h2_combo_v1/RESULTS.json` is the reference (the same file the framework's
    harness uses for the control anchors); every cell must match within `tol_pp` pp.
    """
    import json
    import pathlib

    ref = json.loads((pathlib.Path(__file__).resolve().parents[2]
                      / "research/band_h2_combo_v1/RESULTS.json").read_text())
    days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    i0, i1 = fe_study.START, len(days) - 1
    out = {}

    def cell(label, got, want):
        d = (float(got) - float(want)) * 100
        return {"reference": float(want), "reproduced": float(got), "diff_pp": d,
                "pass": bool(abs(d) <= tol_pp)}

    for model, fin in (("base", 0.0), ("calibrated", FIN["calibrated"])):
        d2, o2, c2, _e, w2, _v = fe_data.main_inputs(fin)
        envc = fe_sim.RunEnv(d2, fx, ufe, i0, i1)
        z2 = _zeros(len(d2))
        r = fe2_sim.simulate2(o2, c2, w2, z2, z2, z2, envc, 100_000.0, i0, i1, arm="K",
                              mode="pre_tax", m=2)
        st = fe_study.pre_stats(d2, i0, i1, r)
        refc = ref["pre_tax_main"][model]["m2"]["K"]
        out[f"K_pre_{model}_m2_cagr"] = cell("", st["cagr"], refc["cagr"])
        out[f"K_pre_{model}_m2_max_dd"] = cell("", st["max_dd"], refc["max_dd"])
        out[f"K_pre_{model}_m2_turnover"] = cell("", st["turnover_annual"], refc["turnover_annual"])
    z = _zeros(len(days))
    for wn in ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end"):
        a0, b0 = TAX_WINDOWS[wn]
        j0, j1 = fe_study.window_indices(days, a0, b0)
        env2 = fe_sim.RunEnv(days, fx, ufe, j0, j1)
        r = fe2_sim.simulate2(o, c, w_r2, z, z, z, env2, 100_000.0, j0, j1, arm="K",
                              mode="progressive", m=2)
        out[f"K_after_tax_100k_{wn}"] = cell(
            "", r["cagr_usd_liquidated"],
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
    """Every parity (a) cell plus the engine's own parity run.

    `engine_parity_result` lets a caller that has already run `python -m research.fast_engine.parity
    --quick` (the framework's harness does) hand the result in instead of paying for it twice.
    """
    import time

    t0 = time.time()
    checks = {
        "main_window": main_window(),
        "after_tax_windows": after_tax_windows(),
        "cash_yield": cash_yield_cell(),
        "bootstrap": bootstrap(n_paths=n_boot_paths),
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
    checks["all"] = all(flat.values())
    checks["n_checks"] = len(flat)
    checks["failed"] = sorted(k for k, v in flat.items() if not v)
    checks["wall_s"] = time.time() - t0
    if not quiet:
        for k in checks["failed"]:
            print(f"  FAIL {k}", flush=True)
        print(f"fe2 parity {'PASS' if checks['all'] else 'FAIL'} "
              f"({checks['n_checks']} cells, {len(checks['failed'])} failed, "
              f"{checks['wall_s']:.1f}s)", flush=True)
    return checks


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m research.fast_engine2.fe2_parity")
    ap.add_argument("--boot-paths", type=int, default=300)
    args = ap.parse_args(argv)
    checks = run(n_boot_paths=args.boot_paths)
    return 0 if checks["all"] else 1


if __name__ == "__main__":
    sys.exit(main())
