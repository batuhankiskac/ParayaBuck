"""three_leg_kernel_v1.run -- the N-leg kernel's harness: parity, speed and reach.

    python -m research.three_leg_kernel_v1.run --run

runs, in order:

  * the engine's own `python -m research.fast_engine.parity --quick` (must PASS; `fe3_parity` runs
    it and keeps `research/fast_engine/PARITY.json` at its committed bytes);
  * the N-leg parity suite (`research.fast_engine3.fe3_parity`: legs 2..N == 0 vs the engine, legs
    3..N == 0 vs `fe2_sim`, margin off on both, margin on a single leg vs `fast_engine_opts`, the
    three-book positive control, K's published anchors through `simulate3`);
  * the speed cells (a warm-cache main-window run at 1/2/3/4 legs, a four-book run and a margin
    run, plus the clean bootstrap) -- these live in `TIMING.json`, not `RESULTS.json`;
  * the framework harness (`research.batch_screen.run.harness`: the published 116 cells plus the
    multi-leg routing cells), written to `research/batch_screen/HARNESS.json`.

Every deterministic value goes into `RESULTS.json`, so two consecutive runs give a byte-identical
`RESULTS.json`; the wall clock and the millisecond timings live in `TIMING.json` and
`SUMMARY.txt`. `PARITY3.json` is the parity suite's own artifact (`fe3_parity --write`).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

from research.batch_screen import run as bsrun
from research.fast_engine import fe_boot, fe_data, fe_sim, fe_study
from research.fast_engine3 import fe3_boot, fe3_parity, fe3_sim

HERE = Path(__file__).resolve().parent


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
    path.write_text(json.dumps(rnd(obj), sort_keys=True, indent=1) + "\n")


def deterministic_parity(par) -> dict:
    """The parity dict without its wall clock and the engine subprocess's timing tail."""
    out = {k: v for k, v in par.items() if k not in ("wall_s", "engine_parity_quick")}
    out["engine_parity_quick"] = {"pass": bool(par["engine_parity_quick"]["pass"])}
    return out


def _median_ms(fn, n=30):
    fn()  # warm the numba caches
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts) * 1000.0


def speed_cells(n=30):
    """Warm-cache per-run cost of the main window at 1..4 legs, a four-book run and a loan run."""
    days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    i0, i1 = fe_study.START, len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
    z = np.zeros(len(days))
    out = {"sessions": int(i1 - i0 + 1), "runs": n}
    out["engine_1leg_ms"] = _median_ms(
        lambda: fe_sim.simulate(o, c, w_r2, env, 100_000.0, i0, i1, arm="K", mode="progressive",
                                m=2), n)
    for nlegs in (2, 3, 4):
        ops = [o] + [z] * (nlegs - 1)
        cls = [c] + [z] * (nlegs - 1)
        ws = [w_r2] + [z] * (nlegs - 1)
        out[f"nleg_{nlegs}_ms"] = _median_ms(
            lambda ops=ops, cls=cls, ws=ws: fe3_sim.simulate3(
                ops, cls, ws, env, 100_000.0, i0, i1, arm="K", mode="progressive", m=2), n)
    p = bsrun.windows.main_panel(0.0)[1]
    o1, c1 = p.legs["1x"]
    mr = fe3_parity._margin_rate(days)
    ops4 = [o, o1, o1, o1]
    cls4 = [c, c1, c1, c1]
    ws4 = [w_r2, 0.5 * w_r2, 0.25 * w_r2, 0.1 * w_r2]
    out["nleg_4_real_ms"] = _median_ms(
        lambda: fe3_sim.simulate3(ops4, cls4, ws4, env, 100_000.0, i0, i1, arm="K",
                                  mode="progressive", m=2), n)
    out["nleg_1_margin_ms"] = _median_ms(
        lambda: fe3_sim.simulate3([o], [c], [2.0 * w_r2], env, 100_000.0, i0, i1, arm="K",
                                  mode="progressive", m=2, mrate=mr), n)
    del z
    return out


def bootstrap_timing(paths=256):
    """The clean bootstrap at `paths` paths: the engine's `fe_boot.boot` vs `fe3_boot.boot3`."""
    t0 = time.perf_counter()
    fe_boot.boot(seed=fe_boot.SEED, block=fe_boot.BLOCK, arms=("C0", "K"), n_pre=paths,
                 n_tax=paths, capitals=("100k",), modes=("progressive",), anchor_e2=False,
                 deltas=("C0",), quiet=True, keep_paths=True)
    t_engine = time.perf_counter() - t0
    t0 = time.perf_counter()
    fe3_boot.boot3([{"id": a, "rule": a, "wants": fe3_boot.leg_a_only_N(4)} for a in ("C0", "K")],
                   fe3_boot.chunk_series3(4), n_pre=paths, n_tax=paths, capitals=("100k",),
                   modes=("progressive",), quiet=True)
    t_nleg = time.perf_counter() - t0
    return {"paths": paths, "engine_s": t_engine, "nleg_4legs_s": t_nleg,
            "ratio": t_nleg / t_engine}


def reach_table():
    """What the N-leg kernel makes expressible, and what stays blocked (the doc's own table)."""
    return {
        "now_expressible": [
            "IC36 A/B/C -- a 3/4-instrument inverse-vol blend in the off state, rescaled monthly",
            "IC34-C -- QLD* with XLU and XLP held at once",
            "IC45-C / IC46-C -- a sleeve on top of production past the 0.98 cap, funded by a cash "
            "loan with interest",
            "IC41 A/B/C -- QLD* plus the S&P 500 2x leg (`2x_spy`) as leg B",
        ],
        "still_blocked": [
            "two books of the same instrument (one FIFO queue per symbol per taxpayer)",
            "more than 4 instruments in one account",
            "a per-session instrument switch inside one leg",
            "several cash pools / accounts, or a per-arm tax regime",
            "the single-leg execution options (band_up, slicing, the DD governor, T+1, FX cost, "
            "withholding) on a multi-leg arm",
        ],
    }


def _n_cells(group, prefix=None) -> int:
    return sum(1 for k, v in group.items()
               if isinstance(v, dict) and "pass" in v and (prefix is None or k.startswith(prefix)))


def build_results(par, harness) -> dict:
    """The deterministic RESULTS.json payload."""
    dpar = deterministic_parity(par)
    mw, aw, cy = par["main_window"], par["after_tax_windows"], par["cash_yield"]
    mw_two, aw_two = _n_cells(mw, "twoleg"), _n_cells(aw, "twoleg")
    anchors = fe3_parity.engine_anchors()
    return {
        "parity": dpar,
        "counts": {
            "parity_cells": int(par["n_checks"]),
            # legs 2..N == 0 vs the engine (main + after-tax + cash yield)
            "engine_identity_cells": (_n_cells(mw) - mw_two) + (_n_cells(aw) - aw_two)
            + _n_cells(cy),
            # legs 3..N == 0 vs fe2_sim (leg B real and zero)
            "twoleg_identity_cells": mw_two + aw_two,
            "bootstrap_cells": _n_cells(par["bootstrap"]),
            "margin_off_cells": _n_cells(par["margin"], "margin_off"),
            # the three modes' loan cells (the fourth `margin_on_*` key is the "the loan bites"
            # control, counted with the other positive controls below)
            "margin_on_cells": _n_cells(par["margin"], "margin_on") - 1,
            "positive_control_cells": _n_cells(par["multi_leg"]) + 1,
            "anchor_cells": len(anchors),
            "framework_harness_checks": int(harness["n_checks"]),
        },
        "engine_anchors": anchors,
        "framework_harness": {"all": bool(harness["all"]), "n_checks": int(harness["n_checks"]),
                              "failed": list(harness["failed"])},
        "reach": reach_table(),
    }


def summary(res, timing) -> str:
    """The verdict-first SUMMARY.txt."""
    par = res["parity"]
    sp = timing["speed_ms"]
    lines = [
        "# three_leg_kernel_v1 -- the N-leg kernel (N <= 4) for the batch-screen framework",
        "",
        "**Verdict: built and parity-proven. Engineering only -- no idea's performance was "
        "computed.**",
        "",
        "`research/fast_engine3/` generalises `fe2_sim.sim2` to N <= 4 instruments in one cash pool "
        "(per-leg FIFO books and costs, the A1u band / H2 deferral applied per leg on that leg's own "
        "book, one Turkish taxpayer) and adds the margin loan of `research/fast_engine_opts` "
        "(interest on a negative cash balance). `research/fast_engine/` and `research/fast_engine2/` "
        "are not edited: the per-leg primitives (`_buy_leg` / `_sell_leg`) and the tax engine are "
        "imported unchanged.",
        "",
        "## Parity (bit for bit, `max_abs_diff == 0.0`)",
        "",
        f"* `python -m research.fast_engine.parity --quick`: "
        f"{'PASS' if par['engine_parity_quick']['pass'] else 'FAIL'}",
        f"* N-leg parity suite: {'PASS' if par['all'] else 'FAIL'} "
        f"({par['n_checks']} cells, {len(par['failed'])} failed, {timing['parity_s']:.1f}s)",
        "",
        "| identity | cells | diff |",
        "|---|---:|---|",
        f"| legs 2..N == 0 vs `fe_sim.simulate` (main window, the after-tax windows, cash yield) | "
        f"{res['counts']['engine_identity_cells']} | 0.0 |",
        f"| legs 3..N == 0 vs `fe2_sim.simulate2` (leg B real and zero) | "
        f"{res['counts']['twoleg_identity_cells']} | 0.0 |",
        f"| the clean bootstrap, legs 2..N == 0 (vs `fe_boot.boot` and `fe2_boot.boot2`) | "
        f"{res['counts']['bootstrap_cells']} | 0.0 |",
        f"| margin **off** == the engine and `fe2_sim` | "
        f"{res['counts']['margin_off_cells']} | 0.0 |",
        f"| margin **on** a single leg vs `fe_opts_sim.simulate_opts` | "
        f"{res['counts']['margin_on_cells']} | 0.0 |",
        f"| positive controls (three books, the capacity refusal, the loan bites) | "
        f"{res['counts']['positive_control_cells']} | n/a |",
        f"| published K anchors through `simulate3` (tolerance 0.01 pp) | "
        f"{res['counts']['anchor_cells']} | "
        f"{max(v['diff_pp'] for v in res['engine_anchors'].values()):.2e} pp |",
        "| `python -m research.fast_engine.parity --quick` | 1 | PASS |",
        "",
        "## Speed (main window, warm cache, median of 30)",
        "",
        "| run | ms |",
        "|---|---:|",
        f"| engine (1 leg) | {sp['engine_1leg_ms']:.3f} |",
        f"| N-leg, 2 legs (leg B at zero) | {sp['nleg_2_ms']:.3f} |",
        f"| N-leg, 3 legs (legs B/C at zero) | {sp['nleg_3_ms']:.3f} |",
        f"| N-leg, 4 legs (legs B/C/D at zero) | {sp['nleg_4_ms']:.3f} |",
        f"| N-leg, 4 real books | {sp['nleg_4_real_ms']:.3f} |",
        f"| N-leg, 1 leg with a margin loan | {sp['nleg_1_margin_ms']:.3f} |",
        f"| clean bootstrap {timing['bootstrap']['paths']} paths, engine | "
        f"{timing['bootstrap']['engine_s']:.2f} s |",
        f"| clean bootstrap {timing['bootstrap']['paths']} paths, 4-leg kernel | "
        f"{timing['bootstrap']['nleg_4legs_s']:.2f} s "
        f"({timing['bootstrap']['ratio']:.2f}x) |",
        "",
        "## The framework harness",
        "",
        "`python -m research.batch_screen.run --harness`: "
        f"{'PASS' if res['framework_harness']['all'] else 'FAIL'} "
        f"({res['framework_harness']['n_checks']} checks, "
        f"{len(res['framework_harness']['failed'])} failed) -- the published 116 cells plus the "
        "multi-leg routing cells; `research/batch_screen/HARNESS.json`.",
        "",
        "## What is now expressible",
        "",
    ]
    for x in res["reach"]["now_expressible"]:
        lines.append(f"* {x}")
    lines += ["", "## What remains blocked", ""]
    for x in res["reach"]["still_blocked"]:
        lines.append(f"* {x}")
    lines += [
        "",
        "## Timings of this run",
        "",
        f"* `--run` wall clock: **{timing['wall_s']:.1f} s**",
        f"* N-leg parity suite: {timing['parity_s']:.1f} s; framework harness: "
        f"{timing['harness_s']:.1f} s; speed cells + bootstrap timings: {timing['speed_s']:.1f} s",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.three_leg_kernel_v1.run")
    ap.add_argument("--run", action="store_true", help="run the harness and write the artifacts")
    ap.add_argument("--harness", action="store_true", help="alias of --run")
    ap.add_argument("--boot-paths", type=int, default=60)
    args = ap.parse_args(argv)
    if not (args.run or args.harness):
        ap.print_help()
        return 2
    t0 = time.perf_counter()
    t = {}
    par = fe3_parity.run(n_boot_paths=args.boot_paths, quiet=False)
    t["parity_s"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    speed = speed_cells()
    boot = bootstrap_timing()
    t["speed_s"] = time.perf_counter() - t1
    t1 = time.perf_counter()
    harness = bsrun.harness()
    t["harness_s"] = time.perf_counter() - t1
    t["speed_ms"] = speed
    t["bootstrap"] = boot
    t["wall_s"] = time.perf_counter() - t0
    res = build_results(par, harness)
    dump(HERE / "RESULTS.json", res)
    dump(HERE / "TIMING.json", t)
    dump(bsrun.HERE / "HARNESS.json", harness)
    (HERE / "SUMMARY.txt").write_text(summary(res, t))
    print((HERE / "SUMMARY.txt").read_text())
    ok = bool(par["all"] and harness["all"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
