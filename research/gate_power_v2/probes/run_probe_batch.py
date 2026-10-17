"""probes/run_probe_batch.py -- run the availability probes through the real batch runner.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.gate_power_v2.probes.run_probe_batch

Isolation: the probe modules (and a copy of the control) are staged in a temporary idea directory,
the trial ledger is a temporary file and the results a temporary directory, so **nothing** of the
real `research/batch_screen/REGISTRY.jsonl` or `results/` is touched and no trial is registered.
The run is a full stage 1 + stage 2 (the control record included), i.e. minutes, not the fast
smoke.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from .. import api, registry
from .. import run as R

HERE = Path(__file__).resolve().parent
DEFAULT_IDEAS = ("yield_veto", "coverage_gate")


def stage_ideas(tmp: Path, ideas) -> Path:
    """A temporary idea directory: the probes plus the real control module (same bytes)."""
    ideas_dir = tmp / "ideas"
    ideas_dir.mkdir(parents=True, exist_ok=True)
    for idea in list(ideas) + [R.CONTROL_ARM.split("/")[0]]:
        src = HERE / f"{idea}.py"
        if not src.exists():
            src = api.ROOT / "research" / "ideas" / f"{idea}.py"
        shutil.copy(src, ideas_dir / f"{idea}.py")
    return ideas_dir


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.gate_power_v2.probes.run_probe_batch")
    ap.add_argument("--ideas", default=",".join(DEFAULT_IDEAS))
    ap.add_argument("--batch", default="probe_availability")
    ap.add_argument("--keep", action="store_true", help="keep the temporary directory")
    args = ap.parse_args(argv)
    ideas = [s for s in args.ideas.split(",") if s]

    tmp = Path(tempfile.mkdtemp(prefix="batch_screen_probe_"))
    api.IDEAS_DIR = stage_ideas(tmp, ideas)
    registry.REGISTRY = tmp / "REGISTRY.jsonl"
    R.RESULTS = tmp / "results"
    t0 = time.time()
    try:
        res = R.run_batch(args.batch, ideas, "none", workers=1, stage2=True, allow_dirty=True)
        print(f"probe batch wall {time.time() - t0:.1f}s")
        print((R.RESULTS / args.batch / "SCOREBOARD.md").read_text())
        for p in sorted((R.RESULTS / args.batch).glob("*__*.json")):
            r = json.loads(p.read_text())
            av = r.get("availability") or {}
            print(f"{r['arm']}: verdict={r['verdict']} "
                  f"oos_applicable={av.get('n_oos_applicable')} "
                  f"etf_applicable={av.get('n_etf_applicable')} "
                  f"g7_need={((r.get('gates') or {}).get('G7_detail') or {}).get('n_within_required')} "
                  f"g8_markets={((r.get('gates') or {}).get('G8_detail') or {}).get('markets')}")
        print(f"deflation declared: {(res['deflation'] or {}).get('declared')}")
        print(f"isolated results in {R.RESULTS}")
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
