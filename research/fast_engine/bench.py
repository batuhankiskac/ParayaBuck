"""bench.py -- wall-clock before/after of the research engine.

Three measurements, each run twice: once on the old pure-Python code
(`research/band_h2_combo_v1/screen.py` + `tax_engine.py`) and once on this package.

  harness    the study's control reproduction: the R2/E2 pre-tax anchors at m = 1/2 under both
             financing models, the three after-tax windows at $100k, and the clean bootstrap at
             1,000 pre-tax / 1,000 after-tax paths (`screen.harness(run_boot=True)`)
  boot1000   one full after-tax bootstrap: 1,000 paths, $100k, progressive, 4 arms -- i.e. the
             published E3 run, seed 20261015 block 10
  study      the whole study-sized run: pre-tax main (base/calibrated x m = 1/2/4 + R-USD), the
             after-tax windows at all five capitals, the m = 4 cost stress, the six OOS windows,
             the 21 ETFs, E1, E2, E3, E5 and the clean bootstrap (5,000 pre-tax / 1,000
             after-tax paths)

The two sides are run in separate processes so neither inherits the other's caches, and the
JIT compile time is measured separately with a cold `NUMBA_CACHE_DIR`.

Usage:  python -m research.fast_engine.bench [--only harness|boot1000|study|jit] [--old] [--new]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "BENCH.json"
PY = sys.executable


def _old():
    import importlib.util

    spec = importlib.util.spec_from_file_location("old_screen", ROOT / "research/band_h2_combo_v1/screen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------------------- the three jobs
def old_harness():
    return _old().harness(run_boot=True, deep=False)


def old_boot1000():
    scr = _old()
    return scr.boot(
        seed=scr.E3_SEED,
        block=10,
        arms=scr.ARMS,
        n_pre=0,
        n_tax=1000,
        capitals=("100k",),
        modes=("progressive",),
        anchor_e2=False,
        deltas=scr.CANDIDATES,
        baselines=(scr.CONTROL, scr.BASELINE),
        label="bench",
    )


def old_study():
    scr = _old()
    scr.pre_tax_main(scr.ARMS)
    scr.after_tax_windows(tuple(scr.TAX_CAPITALS), scr.ARMS)
    scr.cost_stress(scr.ARMS)
    scr.oos_stages(scr.ARMS)
    scr.instrument_stages_usd(scr.ARMS)
    scr.stage_e1(scr.ARMS)
    scr.stage_e2(scr.ARMS)
    scr.stage_e3(scr.ARMS)
    scr.stage_e5_decisions()
    return scr.boot(
        seed=scr.SEED,
        block=scr.BLOCK,
        arms=scr.ARMS,
        n_pre=scr.PRE_BOOT_PATHS,
        n_tax=scr.TAX_BOOT_PATHS,
        capitals=("100k", "1M"),
        modes=("progressive", "flat"),
        deltas=scr.CANDIDATES,
        baselines=(scr.CONTROL, scr.BASELINE),
        label="clean",
    )


def new_harness():
    """The same work as `old_harness`, on this package."""
    from . import fe_boot, fe_config, fe_data, fe_sim, fe_study

    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    for md, spread in fe_config.FIN.items():
        d_, o_, c_, we_, wr_, e_ = fe_data.main_inputs(spread)
        env = fe_sim.RunEnv(d_, fx, ufe, fe_config.START, T)
        for wants in (we_, wr_):
            for m in (1, 2):
                fe_sim.simulate(o_, c_, wants, env, 100_000.0, fe_config.START, T, arm="C0", mode="pre_tax", m=m)
    for wn, (a, b) in fe_config.TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        for wants in (w_e2, w_r2):
            fe_sim.simulate(o, c, wants, env, 100_000.0, i0, i1, arm="C0", mode="pre_tax")
            fe_sim.simulate(o, c, wants, env, 100_000.0, i0, i1, arm="C0", mode="progressive")
    return fe_boot.boot(
        seed=fe_config.SEED,
        block=fe_config.BLOCK,
        arms=fe_sim.ARMS,
        n_pre=1000,
        n_tax=1000,
        capitals=("100k",),
        modes=("progressive", "flat"),
        deltas=fe_config.CANDIDATES,
        baselines=(fe_sim.CONTROL,),
        label="bench",
        quiet=True,
    )


def new_boot1000():
    from . import fe_boot, fe_config, fe_sim

    return fe_boot.boot(
        seed=fe_config.E3_SEED,
        block=10,
        arms=fe_sim.ARMS,
        n_pre=0,
        n_tax=1000,
        capitals=("100k",),
        modes=("progressive",),
        anchor_e2=False,
        deltas=fe_config.CANDIDATES,
        baselines=(fe_sim.CONTROL, fe_sim.BASELINE),
        label="bench",
        quiet=True,
    )


def new_study():
    from . import fe_boot, fe_config, fe_sim, fe_study

    fe_study.pre_tax_main()
    fe_study.after_tax_windows(tuple(fe_config.TAX_CAPITALS))
    fe_study.cost_stress()
    fe_study.oos_stages()
    fe_study.instrument_stages_usd()
    fe_study.stage_e1()
    fe_study.stage_e2()
    window = fe_boot.BootWindow()
    fe_study.stage_e3(window=window)
    fe_study.stage_e5_decisions()
    return fe_boot.boot(
        seed=fe_config.SEED,
        block=fe_config.BLOCK,
        arms=fe_sim.ARMS,
        n_pre=fe_config.PRE_BOOT_PATHS,
        n_tax=fe_config.TAX_BOOT_PATHS,
        capitals=("100k", "1M"),
        modes=("progressive", "flat"),
        deltas=fe_config.CANDIDATES,
        baselines=(fe_sim.CONTROL, fe_sim.BASELINE),
        label="clean",
        window=window,
        quiet=True,
    )


JOBS = {
    "harness": (old_harness, new_harness),
    "boot1000": (old_boot1000, new_boot1000),
    "study": (old_study, new_study),
}


def timed(fn):
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def jit_compile_time():
    """Cold-cache: the cost of compiling every kernel, in a fresh process with an empty cache."""
    code = (
        "import time; t=time.perf_counter()\n"
        + "from research.fast_engine import fe_boot\n"
        + "fe_boot.boot(n_pre=2, n_tax=2, capitals=('100k',), modes=('progressive','flat'),"
        " chunk=2, quiet=True)\nprint(time.perf_counter()-t)\n"
    )
    cache = tempfile.mkdtemp(prefix="fast_engine_numba_")
    env = dict(os.environ, NUMBA_CACHE_DIR=cache)
    subprocess.run(
        [sys.executable, "-c", "import shutil,os;shutil.rmtree(os.environ['NUMBA_CACHE_DIR'],ignore_errors=True)"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    cold = float(out.stdout.strip().splitlines()[-1])
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    warm = float(out.stdout.strip().splitlines()[-1])
    return {"cold_s": cold, "warm_s": warm}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", choices=sorted(JOBS) + ["jit"])
    ap.add_argument("--old", action="store_true", help="run only the Python side")
    ap.add_argument("--new", action="store_true", help="run only the engine side")
    args = ap.parse_args()
    both = not (args.old or args.new)
    result = json.loads(OUT.read_text()) if OUT.exists() else {}
    result.setdefault("jobs", {})
    for name, (fn_old, fn_new) in JOBS.items():
        if args.only not in (None, name):
            continue
        e = result["jobs"].setdefault(name, {})
        if both or args.old:
            e["old_s"] = timed(fn_old)
            print(f"{name}: old {e['old_s']:.1f}s", flush=True)
        if both or args.new:
            e["new_s"] = timed(fn_new)
            print(f"{name}: new {e['new_s']:.1f}s", flush=True)
        if "old_s" in e and "new_s" in e:
            e["speedup"] = e["old_s"] / e["new_s"]
            print(f"{name}: speedup {e['speedup']:.1f}x", flush=True)
    if args.only in (None, "jit"):
        result["jit"] = jit_compile_time()
        print(f"jit: cold {result['jit']['cold_s']:.2f}s warm {result['jit']['warm_s']:.2f}s", flush=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
