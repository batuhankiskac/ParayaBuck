"""two_leg_kernel_v1 -- parity runner for the two-leg kernel (code + parity deliverable).

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.two_leg_kernel_v1.run --harness
        every parity cell: the engine's own `parity --quick`, the two-leg kernel against the
        engine with leg B at zero (`fe2_parity`), the fixed riskoff after-tax case
        (`parity_b`), and the framework's routing checks -- plus the published K anchors.
        Writes HARNESS.json and prints PASS/FAIL.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.two_leg_kernel_v1.run --run
        the same cells, the framework's full harness (`research.batch_screen.run --harness`,
        73 checks), the speed measurements and the wall clock -> RESULTS.json (rounded and
        sorted, byte-identical on a rerun) + SUMMARY.txt.

No arm performance is computed: this is the kernel's parity, not a screen. The kernel package is
`research/fast_engine2/`; this directory holds the parity (b) reference engine and this runner.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from research.batch_screen import run as framework_run
from research.fast_engine import fe_boot, fe_data, fe_signals, fe_sim, fe_study
from research.fast_engine.fe_config import EXP_Q
from research.fast_engine2 import fe2_boot, fe2_parity, fe2_sim
from research.two_leg_kernel_v1 import parity_b

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "RESULTS.json"
HARNESS = HERE / "HARNESS.json"
SUMMARY = HERE / "SUMMARY.txt"
TIMING = HERE / "TIMING.json"   # wall clocks: machine-dependent, kept out of RESULTS.json


def rnd(x, nd=6):
    """`research.batch_screen.run.rnd`: round every float leaf, so a rerun is byte-identical."""
    return framework_run.rnd(x, nd)


def dump(path: Path, obj) -> None:
    path.write_text(json.dumps(rnd(obj), sort_keys=True, indent=1) + "\n")


# ----------------------------------------------------------------------------- parity table
def engine_parity_run():
    """`python -m research.fast_engine.parity --quick` (the required PASS)."""
    return fe2_parity.engine_parity(quick=True)


def parity_cells(engine_parity_result=None, boot_paths=300):
    """Every parity cell, grouped, with the max |diff| of each group."""
    fe2 = fe2_parity.run(n_boot_paths=boot_paths, quiet=True,
                         engine_parity_result=engine_parity_result)
    pb = parity_b.run(assert_pass=True)
    return fe2, pb


def cell_table(fe2, pb):
    """One row per parity group: what it compares, its cells, its worst diff, its verdict."""
    def worst(group):
        d = [c.get("max_abs_diff", c.get("scalar_max_abs_diff", c.get("diff_pp", 0.0)))
             for c in group.values() if isinstance(c, dict)]
        return max([abs(float(x)) for x in d if x is not None] + [0.0])

    rows = [
        {"check": "engine parity (research/fast_engine.parity --quick)",
         "cells": 1, "max_abs_diff": 0.0 if fe2["engine_parity_quick"]["pass"] else None,
         "pass": bool(fe2["engine_parity_quick"]["pass"])},
        {"check": "two-leg vs engine, leg B == 0: main window (4 rules x 3 modes x m=1/2/4, "
                  "base+calibrated)",
         "cells": len(fe2["main_window"]), "max_abs_diff": worst(fe2["main_window"]),
         "pass": all(c["pass"] for c in fe2["main_window"].values())},
        {"check": "two-leg vs engine, leg B == 0: after-tax windows (3 windows x 3 capitals x "
                  "2 rules x progressive/flat)",
         "cells": len(fe2["after_tax_windows"]), "max_abs_diff": worst(fe2["after_tax_windows"]),
         "pass": all(c["pass"] for c in fe2["after_tax_windows"].values())},
        {"check": "two-leg vs engine, leg B == 0: idle-cash yield on",
         "cells": sum(1 for c in fe2["cash_yield"].values() if isinstance(c, dict)), "max_abs_diff": worst(fe2["cash_yield"]),
         "pass": all(c["pass"] for c in fe2["cash_yield"].values()
                     if isinstance(c, dict) and "pass" in c)},
        {"check": "two-leg vs engine, leg B == 0: clean bootstrap (per path, pre-tax + after-tax)",
         "cells": sum(1 for c in fe2["bootstrap"].values() if isinstance(c, dict)), "max_abs_diff": worst(fe2["bootstrap"]),
         "pass": all(c["pass"] for c in fe2["bootstrap"].values()
                     if isinstance(c, dict) and "pass" in c)},
        {"check": "published K anchors through the two-leg kernel (band_h2_combo_v1 RESULTS)",
         "cells": len(fe2["engine_anchors"]), "max_abs_diff": worst(fe2["engine_anchors"]),
         "pass": all(c["pass"] for c in fe2["engine_anchors"].values())},
    ]
    for name, cell in pb["parity"].items():
        rows.append({"check": f"riskoff_after_tax_v1 engine on the fixed two-leg case ({name})",
                     "cells": len(cell["cells"]) + len(cell["tax_by_year"]) + 1,
                     "max_abs_diff": cell["nav_max_abs_diff"], "pass": bool(cell["pass"])})
    n = pb["divergence"]["numbers"]
    rows.append({"check": "same case with both legs realising in one tax year (reported, not "
                          "asserted: the pooled-vs-per-account convention)",
                 "cells": 2, "max_abs_diff": None, "pass": True,
                 "cagr_diff_pp": n["cagr_diff_pp"],
                 "ref_tax_try": n["ref_tax_total_try"], "kernel_tax_try": n["kernel_tax_total_try"]})
    return rows


# ----------------------------------------------------------------------------- speed
def speed_measurements(repeats=3):
    """Wall clock: one main-window path, and a 256-path bootstrap, one leg vs two legs."""
    days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    i0, i1 = fe_study.START, len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
    z = np.zeros(len(days))
    # a real two-leg arm: the production weight split 50/50 over the 2x and 1x legs
    y = fe_data.load()[2]
    q = fe_data.load()[1]["QQQ"]
    o1, c1 = fe_signals.synthetic_2x(q[0], q[1], q[2], y, EXP_Q)  # 2x, cost in the price
    o1x, c1x = _total_return_1x(q[0], q[1], q[2])
    out = {}

    def timed(fn, n=repeats):
        best = None
        for _ in range(n):
            t = time.perf_counter()
            r = fn()
            dt = time.perf_counter() - t
            best = dt if best is None else min(best, dt)
        return best, r

    t1, r1 = timed(lambda: fe_sim.simulate(o, c, w_r2, env, 100_000.0, i0, i1, arm="K",
                                           mode="progressive", m=2))
    t2, r2 = timed(lambda: fe2_sim.simulate2(o, c, w_r2 * 0.5, o1x, c1x, w_r2 * 0.5, env,
                                             100_000.0, i0, i1, arm="K", mode="progressive", m=2))
    t3, r3 = timed(lambda: fe2_sim.simulate2(o, c, w_r2, z, z, z, env, 100_000.0, i0, i1,
                                             arm="K", mode="progressive", m=2))
    out["one_main_window_path_s_one_leg"] = t1
    out["one_main_window_path_s_two_leg"] = t2
    out["one_main_window_paths_two_leg_leg_b_zero"] = t3
    out["one_path_overhead_pp_cagr"] = (r2["cagr_usd_liquidated"] - r1["cagr_usd_liquidated"]) * 100
    out["leg_a_trades"], out["leg_b_trades"] = r2["_legs"]["A"]["trades"], r2["_legs"]["B"]["trades"]

    ctrl = [{"id": "K", "rule": "K", "wants": fe2_boot.leg_a_only}]
    split = [{"id": "K", "rule": "K", "wants": _split_wants}]
    n = 256
    t4, _ = timed(lambda: fe2_boot.boot2(ctrl, fe2_boot.chunk_series(), n_pre=n, n_tax=n,
                                         capitals=("100k",), modes=("progressive",), quiet=True),
                  n=1)
    t5, _ = timed(lambda: fe2_boot.boot2(split, _split_series(), n_pre=n, n_tax=n,
                                         capitals=("100k",), modes=("progressive",), quiet=True),
                  n=1)
    out["bootstrap_256_paths_s_one_leg"] = t4
    out["bootstrap_256_paths_s_two_leg"] = t5
    return out


def _total_return_1x(q_open, q_close, q_div):
    """`batch_screen.api.total_return_1x` (kept local so the speed cell needs no framework)."""
    prev = q_close[:-1]
    r_cc = (q_close[1:] + q_div[1:]) / prev - 1
    r_on = (q_open[1:] + q_div[1:]) / prev - 1
    close = np.concatenate([[1.0], np.cumprod(1 + r_cc)])
    opn = np.concatenate([[1.0], close[:-1] * (1 + r_on)])
    return opn, close


def _split_wants(ctx):
    """Half the engine's own leg-A targets, and the same half again on leg B (the 1x panel)."""
    w = np.asarray(ctx["w"], dtype=np.float64)
    return np.ascontiguousarray(np.stack([w * 0.5, w * 0.5], axis=-2))


def _split_series():
    """A `boot2` series provider whose leg B is the 1x leg of the same window's triple."""
    win = fe_boot.BootWindow()

    def provider(pos, W):
        import numpy as np  # local: this runs inside the bootstrap loop

        from research.batch_screen import api

        legs, _e2, _w = fe_boot.chunk_legs(win.q, win.y, pos)
        ob, cb, w_b = legs["base"]
        oc, cc, w_c = legs["calibrated"]
        # the 1x leg of the same chunk: the raw QQQ triple of the drawn sessions
        idx = pos + 1
        o1, c1 = api.total_return_1x(win.q[0][idx], win.q[1][idx], win.q[2][idx])
        T = ob.shape[1]
        one = np.ones((W, 1))
        o1 = np.concatenate([one, o1], axis=1)[:, :T]
        c1 = np.concatenate([one, c1], axis=1)[:, :T]
        return {
            "base": (np.ascontiguousarray(np.stack([ob, o1], axis=1)),
                     np.ascontiguousarray(np.stack([cb, c1], axis=1)), {"w": w_b}),
            "calibrated": (np.ascontiguousarray(np.stack([oc, o1], axis=1)),
                           np.ascontiguousarray(np.stack([cc, c1], axis=1)), {"w": w_c}),
        }

    return provider


# ----------------------------------------------------------------------------- entry points
def harness(boot_paths=120):
    """Every parity cell (parity (a), parity (b), the framework's routing checks)."""
    t0 = time.time()
    ep = engine_parity_run()
    fe2, pb = parity_cells(engine_parity_result=ep, boot_paths=boot_paths)
    fw = framework_run.two_leg_checks()
    rows = cell_table(fe2, pb)
    cells = {f"fe2.{k}": v for k, v in fe2["main_window"].items()}
    cells.update({f"fe2.{k}": v for k, v in fe2["after_tax_windows"].items()})
    cells.update({f"fe2.{k}": v for k, v in fe2["bootstrap"].items() if isinstance(v, dict)})
    cells.update({f"fe2.{k}": v for k, v in fe2["cash_yield"].items() if isinstance(v, dict)})
    cells.update({f"fe2.{k}": v for k, v in fe2["engine_anchors"].items()})
    for name, cell in pb["parity"].items():
        cells[f"parity_b.{name}"] = {"pass": cell["pass"],
                                     "nav_max_abs_diff": cell["nav_max_abs_diff"]}
    for k, v in fw.items():
        if isinstance(v, dict) and "pass" in v:
            cells[f"framework.{k}"] = v
    flat = {k: bool(v["pass"]) for k, v in cells.items() if isinstance(v, dict) and "pass" in v}
    out = {
        "all": all(flat.values()),
        "n_checks": len(flat),
        "failed": sorted(k for k, v in flat.items() if not v),
        "engine_parity_quick": ep,
        "parity_table": rows,
        "cells": cells,
        "framework_checks": fw,
        "two_leg_kernel": {"numba": True, "legs": 2, "cells_asserted":
                           sum(1 for r in rows if r["pass"])},
        "wall_s": time.time() - t0,
    }
    dump(HARNESS, out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.two_leg_kernel_v1.run")
    ap.add_argument("--harness", action="store_true", help="run the parity cells")
    ap.add_argument("--run", action="store_true",
                    help="parity cells + the framework harness + speed measurements")
    ap.add_argument("--boot-paths", type=int, default=300)
    args = ap.parse_args(argv)
    if args.harness:
        h = harness(boot_paths=args.boot_paths)
        for k in h["failed"]:
            print(f"  FAIL {k}", flush=True)
        print(f"harness {'PASS' if h['all'] else 'FAIL'} ({h['n_checks']} checks, "
              f"{len(h['failed'])} failed, {h['wall_s']:.1f}s)", flush=True)
        return 0 if h["all"] else 1
    if not args.run:
        ap.print_help()
        return 2

    t0 = time.time()
    h = harness(boot_paths=args.boot_paths)
    t_h = time.time() - t0
    t1 = time.time()
    fw = framework_run.harness()
    t_fw = time.time() - t1
    speed = speed_measurements()
    fe2, pb = parity_cells(boot_paths=args.boot_paths)
    results = {
        "task": "two_leg_kernel_v1",
        "verdict": ("two-leg kernel built and parity-proven; no idea's performance computed"
                    if h["all"] and fw["all"] else "PARITY FAILED"),
        "parity_table": cell_table(fe2, pb),
        "parity_cells": {"n": h["n_checks"], "failed": h["failed"]},
        "framework_harness": {"all": bool(fw["all"]), "n_checks": int(fw["n_checks"]),
                              "failed": list(fw["failed"])},
        "framework_checks": fw["two_leg"],
        "speed_trade_counts": {k: speed[k] for k in ("leg_a_trades", "leg_b_trades")},
    }
    timing = {
        "speed_s": {k: v for k, v in speed.items() if k not in ("leg_a_trades", "leg_b_trades")},
        "wall_clock_s": {"parity_cells": t_h, "framework_harness": t_fw,
                         "framework_harness_internal": float(fw["wall_s"]),
                         "total_run": time.time() - t0},
        "environment": {"python": sys.version.split()[0], "machine": platform.machine(),
                        "platform": platform.platform(),
                        "threads": __import__("os").cpu_count()},
    }
    dump(RESULTS, results)
    dump(TIMING, timing)
    results = dict(results, wall_clock_s=timing["wall_clock_s"])
    write_summary(results, h, fw, speed)
    print(SUMMARY.read_text(), flush=True)
    return 0 if (h["all"] and fw["all"]) else 1


def write_summary(results, h, fw, speed):
    rows = results["parity_table"]
    lines = [
        "# two_leg_kernel_v1 -- two-leg kernel + parity (code deliverable, no arm performance)",
        "",
        f"Verdict: **{results['verdict']}**.",
        "",
        "## Design",
        "",
        "* `research/fast_engine2/fe2_sim.py` -- the two-leg session kernel: one cash pool, per-leg",
        "  units and FIFO lot books, the 0.5 pp band + A1u gain band + H2 deferral per leg on that",
        "  leg's own book, per-leg costs, one taxpayer (both legs' realisations in one annual net",
        "  TRY gain), next-open fills, optional idle-cash yield. With leg B identically zero it is",
        "  bit-identical to `fe_sim.sim_one`.",
        "* `research/fast_engine2/fe2_boot.py` -- the two-leg bootstrap (`boot2`, `_boot_chunk2`,",
        "  `_pt2`), on `fe_boot.draw_positions` (the same paths as `fe_boot.boot`).",
        "* `research/fast_engine2/fe2_parity.py` -- parity (a): leg B == 0 against the engine.",
        "* `research/two_leg_kernel_v1/riskoff_ref.py` + `parity_b.py` -- parity (b): the",
        "  `riskoff_after_tax_v1` engine (copied verbatim, tag",
        "  `archive/research/riskoff-after-tax-v1-2026-10`) on a fixed synthetic two-leg case.",
        "* Framework: `api` accepts `(wants_a, wants_b, leg_b)` (`leg_b` = a panel leg or a",
        "  registered extra key), `stages.run_one` routes two-leg arms to `fe2_sim.simulate2`,",
        "  `boot` groups arms by `(leg, leg_b)` and runs them on the same drawn paths, a leg-B",
        "  series without history makes the window `NotApplicable`.",
        "",
        "## Parity",
        "",
        "| check | cells | max abs diff | pass |",
        "|---|---:|---:|---|",
    ]
    for r in rows:
        mad = "-" if r["max_abs_diff"] is None else f"{r['max_abs_diff']:.3e}"
        if "anchors" in r["check"] and r["max_abs_diff"] is not None:
            mad += " pp (vs 6-dp published)"
        lines.append(f"| {r['check']} | {r['cells']} | {mad} | {'PASS' if r['pass'] else 'FAIL'} |")
    n = pb_div = None
    lines += [
        "",
        f"* parity cells (a + b + routing): {h['n_checks']} asserted, {len(h['failed'])} failed.",
        f"* framework harness: {fw['n_checks']} checks, "
        f"{len(fw['failed'])} failed ({fw['wall_s']:.1f}s).",
        "",
        f"## Speed ({__import__('os').cpu_count()} threads, warm numba cache, min of repeats; "
        "TIMING.json)",
        "",
        f"| measurement | ms |",
        f"|---|---:|",
        f"| one main-window path, one leg (engine) | {1e3 * speed['one_main_window_path_s_one_leg']:.2f} |",
        f"| one main-window path, two legs (50/50 2x+1x) | {1e3 * speed['one_main_window_path_s_two_leg']:.2f} |",
        f"| one main-window path, two-leg kernel with leg B = 0 | {1e3 * speed['one_main_window_paths_two_leg_leg_b_zero']:.2f} |",
        f"| clean bootstrap, 256 paths (pre-tax x2 + after-tax), one leg | {1e3 * speed['bootstrap_256_paths_s_one_leg']:.1f} |",
        f"| clean bootstrap, 256 paths (pre-tax x2 + after-tax), two legs | {1e3 * speed['bootstrap_256_paths_s_two_leg']:.1f} |",
        "",
        f"Wall clock of `--run`: {results['wall_clock_s']['total_run']:.1f}s "
        f"(parity cells {results['wall_clock_s']['parity_cells']:.1f}s, framework harness "
        f"{results['wall_clock_s']['framework_harness']:.1f}s).",
        "",
        "## What is now expressible, and what remains NEEDS_KERNEL",
        "",
        "* Expressible with the two-leg kernel (a plugin returns `(wants_a, wants_b, leg_b)`; the",
        "  idea modules themselves were left untouched, since their registered arms are pinned by",
        "  the ledger): **IB25** (SHY sleeve, `leg_b=\"shy\"`), **IB26** (off-state IEF,",
        "  `leg_b=\"ief\"`; its T10Y3M input series is still not registered), **IB27** (beta-matched",
        "  QQQ + QLD blend, `leg_b=\"1x\"`).",
        "* Still NEEDS_KERNEL: **IB38 A** (per-arm tax regime and capital -- batch-level settings,",
        "  not weights), **IB38 B** (two/three accounts, i.e. several cash pools and FIFO books in",
        "  one household), **IB38 C** (routed split: gain-aware routing between two books of the",
        "  same instrument with a route cap, which a target-weight panel cannot express), and a",
        "  per-session *instrument* switch for one leg (a leg's instrument is fixed per arm).",
        "",
        "## Caveats",
        "",
        "* Leg B of an extra-data series is priced by its registered adjusted close, used for both",
        "  the fill and the close (an extra series carries no OHLC); the convention is stated in",
        "  `api.MarketData.leg_series`.",
        "* `riskoff_after_tax_v1` assesses each account separately and trades its non-equity legs",
        "  on a 10 pp band; the kernel pools one taxpayer's annual net gain and uses 0.5 pp per leg.",
        "  Parity (b) is therefore asserted on a case built so those conventions cannot differ, and",
        "  the deliberate counter-example is reported instead of asserted (see the doc).",
    ]
    SUMMARY.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
