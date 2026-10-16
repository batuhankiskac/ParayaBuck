"""research/framework_v2/screen.py -- the framework_v2 engineering screen.

    PYTHONPATH=. $R -m research.framework_v2.screen --harness            # every check, printed
    PYTHONPATH=. $R -m research.framework_v2.screen --run [--tests]      # + RESULTS.json

This study adds *framework mechanisms*, not an idea: the deliverable is code, tests and parity
(`PREREGISTRATION.md`, `docs/research/framework_v2.md`), so the screen is the framework's own
check set -- `research.batch_screen.run.harness`, which runs `research.fast_engine.parity
--quick` and the two-leg parities, plus the trial ledger's re-verification against git. No idea is
simulated and no performance number is computed, which is why there is no arm table here.

`RESULTS.json` is deterministic (checks, counts, verdicts; every container sorted and rounded by
`run.dump`); the wall clock lives in `TIMING.json` and `SUMMARY.txt`. `--tests` also runs
`research/batch_screen/tests/run.sh` and records its measured pass count.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

from research.batch_screen import registry
from research.batch_screen import run as R

NAME = "framework_v2"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TESTS = ROOT / "research" / "batch_screen" / "tests" / "run.sh"


def run(tests=False) -> dict:
    """The framework's harness plus the ledger check; `tests` also runs the pytest suite."""
    t0 = time.time()
    ver = registry.verify_ledger()
    checks = R.harness()
    out = {
        "name": NAME,
        "kind": "engineering (framework mechanisms; no idea, no performance claim)",
        "harness": checks,
        "n_checks": int(checks["n_checks"]),
        "engine_parity_quick": checks["engine_parity_quick"],
        "registry": {"ok": bool(ver["ok"]), "lines": int(ver["lines"]), "arms": int(ver["arms"]),
                     "n_trials": int(ver["n_trials"]), "problems": ver["problems"]},
        "tests": run_tests() if tests else {"command": f"sh {TESTS.relative_to(ROOT)}",
                                            "ran": False},
    }
    out["all"] = bool(checks["all"] and ver["ok"]
                      and (not tests or out["tests"]["failed"] == 0))
    out["wall_s"] = time.time() - t0
    print(f"harness {'PASS' if checks['all'] else 'FAIL'} ({checks['n_checks']} checks, "
          f"{len(checks['failed'])} failed)")
    print(f"ledger {'PASS' if ver['ok'] else 'FAIL'} ({ver['lines']} lines, N = "
          f"{ver['n_trials']})")
    if tests:
        print(f"tests  {'PASS' if out['tests']['failed'] == 0 else 'FAIL'} "
              f"({out['tests']['passed']} passed, {out['tests']['failed']} failed)")
    print(f"{NAME} {'PASS' if out['all'] else 'FAIL'} in {out['wall_s']:.1f}s")
    return out


def run_tests() -> dict:
    """`sh research/batch_screen/tests/run.sh -q`, with the measured pass/fail counts."""
    p = subprocess.run(["sh", str(TESTS), "-q"], cwd=ROOT, capture_output=True, text=True)
    tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
    m = re.search(r"(\d+) passed", tail[0])
    f = re.search(r"(\d+) failed", tail[0])
    return {"command": f"sh {TESTS.relative_to(ROOT)} -q", "ran": True,
            "passed": int(m.group(1)) if m else 0,
            "failed": int(f.group(1)) if f else (0 if m else 1),
            "exit": int(p.returncode), "tail": _stable(tail[0][:200])}


#: the cells that cannot be byte-stable across runs: every wall clock, and the engine parity's
#: one-line tail (`parity PASS in 11.7s -> PARITY.json`). The framework's convention is that only
#: `timing.json` is non-deterministic, so `RESULTS.json` carries neither.
_TIMING_RE = re.compile(r" in [\d.]+s")


def _stable(obj):
    """The check set with the timing cells removed (and the parity tail normalised)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "wall_s":
                continue
            out[k] = _stable(_TIMING_RE.sub("", v) if k == "tail" and isinstance(v, str) else v)
        return out
    if isinstance(obj, list):
        return [_stable(v) for v in obj]
    return obj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.framework_v2.screen")
    ap.add_argument("--harness", action="store_true", help="run every check and print them")
    ap.add_argument("--run", action="store_true", help="write RESULTS.json / TIMING.json")
    ap.add_argument("--tests", action="store_true", help="also run the pytest suite")
    args = ap.parse_args(argv)
    out = run(tests=args.tests)
    if args.run:
        R.dump(HERE / "RESULTS.json", _stable({k: v for k, v in out.items() if k != "wall_s"}))
        R.dump(HERE / "TIMING.json", {"name": NAME, "wall_s": out["wall_s"],
                                      "tests": out.get("tests", {}).get("ran", False)})
        print(f"results in {HERE / 'RESULTS.json'}")
    for k in out["harness"]["failed"]:
        print(f"  FAIL {k}", flush=True)
    return 0 if out["all"] else 1


if __name__ == "__main__":
    sys.exit(main())
