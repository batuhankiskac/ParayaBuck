"""research/closeout_v1/run_batch.py -- the study's screen entry point, batch `closeout_v1`.

Both plugin modules of this batch are **new files** that contain only this study's arms
(`research/ideas/IB35_K.py`: `C_K`; `research/ideas/IB24_2L.py`: `A_2L`, `B_2L`, `C_2L`), so the
framework's own `run.plan` over them registers and screens exactly those four arms -- no pinned old
arm is re-run and nothing is rebound (unlike `batch_bank_v4b`, whose modules hold pinned arms too).

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.closeout_v1.run_batch --check
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.closeout_v1.run_batch --workers 6
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.closeout_v1.run_batch --hash

`--check` runs the plugin-level properties this study asserts about itself (the state machine on a
synthetic vote series, the arms' routing, the 3 pp band's behaviour on real data against K's own
band, the sleeve's turnover and the monotonicity of the three states), with no gate and no ledger
write for the properties themselves. `--hash` prints the SHA-256 of the batch's `RESULTS.json` and
`SCOREBOARD.md` (the byte-identity check of the second run). The batch itself keeps the framework's
two stages, gates, scoreboard and deflation untouched; `--deflate` and `--verify-registry` are the
framework's own CLI (`python -m research.batch_screen.run`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

import research.batch_screen.run as R
from research.batch_screen import api, rules, stages, windows
from research.batch_screen.rules import CONTROL_ARM

BATCH = "closeout_v1"
IDEAS = ("IB35_K", "IB24_2L")
RESULTS = Path("research/batch_screen/results") / BATCH


def sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check() -> int:
    """The plugins' own properties: state machine, routing, the band's behaviour, the sleeve."""
    from research.fast_engine import fe_signals
    from research.fast_engine.fe_config import BAND
    from research.ideas import IB24_2L

    out: dict = {}

    # (1) the hysteresis state machine, on a hand-built vote series (the arms' own frozen logic)
    v = np.array([[1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 1, 1]], dtype=bool)
    states = {k: IB24_2L.sleeve_state(v, s) for k, s in IB24_2L.ARMS.items()}
    out["state_machine"] = {
        "A_2L": states["A_2L"].astype(int).ravel().tolist(),
        "B_2L": states["B_2L"].astype(int).ravel().tolist(),
        "C_2L": states["C_2L"].astype(int).ravel().tolist(),
        "pass": bool(
            # A: on exactly at the 5th consecutive off session, off again on re-entry
            states["A_2L"].ravel().tolist() == [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
            # B: a 5-session pause never reaches 10
            and states["B_2L"].sum() == 0
            # C: on from the first off session, off only at the 5th consecutive on session
            and states["C_2L"].ravel().tolist()
            == [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1]
        ),
    }

    # (2) every screened arm resolves and routes as the batch assumes
    routed, refused = {}, {}
    for idea in IDEAS:
        mod = api.load_idea(idea)
        for arm_id in mod.ARMS:
            key = f"{idea}/{arm_id}"
            try:
                arm = api.load_arm(idea, arm_id)
            except api.NeedsKernel as exc:
                refused[key] = str(exc)[:80]
                continue
            routed[key] = {
                "rule": arm["rule"],
                "rule_booleans": rules.rule_of(arm["rule"]),
                "rule_opts": rules.opts_of(arm["rule"]),
                "legs": list(arm["legs"]),
                "leg_b": arm["leg_b"],
                "window": windows.arm_window(arm).tag,
            }
    out["routed"] = routed
    out["refused"] = refused
    out["routing_pass"] = bool(
        not refused
        and routed["IB35_K/C_K"]["rule_opts"] == {"band_up": 0.03, "band_down": 0.03}
        and routed["IB35_K/C_K"]["rule_booleans"]
        == {"band": False, "usd_rule": False, "defer": False}
        and all(routed[f"IB24_2L/{a}"]["legs"] == ["2x", "bilstar"]
                for a in ("A_2L", "B_2L", "C_2L"))
        and all(routed[k]["window"] == "default" for k in routed)
    )

    # (3) the 3 pp band really behaves as a wide *plain* band on real data, and the engine's own
    #     band constant is untouched. The reference is the *plain-band* control R2 (C0: the same
    #     target series with the 0.5 pp band), which is the family the arm belongs to: a wider
    #     plain band must trade fewer times and turn over less. K is not that reference (A1u/H2
    #     gate its downsizes), so K's row is reported for information only.
    days, p, i0, i1 = windows.main_panel(0.0)
    env = windows.try_env(days, i0, i1, "none")
    r0 = stages.run_one(api.load_arm("r2_control", "R2"), p, env, i0, i1, "pre_tax", 2, 1.0)
    rk = stages.run_one(api.load_arm("k_control", "K"), p, env, i0, i1, "pre_tax", 2, 1.0)
    r3 = stages.run_one(api.load_arm("IB35_K", "C_K"), p, env, i0, i1, "pre_tax", 2, 1.0)
    out["band_3pp"] = {
        "engine_C0_BAND": float(BAND),
        "trades": {"R2_plain_0.5pp": int(r0["strategy_trades"]),
                   "K": int(rk["strategy_trades"]), "3pp": int(r3["strategy_trades"])},
        "qld_sells": {"R2_plain_0.5pp": int(r0["qld_sells"]), "K": int(rk["qld_sells"]),
                      "3pp": int(r3["qld_sells"])},
        "turnover_total": {"R2_plain_0.5pp": float(r0["turnover_total"]),
                           "K": float(rk["turnover_total"]), "3pp": float(r3["turnover_total"])},
        "pass": bool(BAND == 0.005 and r3["strategy_trades"] < r0["strategy_trades"]
                     and r3["turnover_total"] < r0["turnover_total"]
                     and r3["strategy_trades"] < rk["strategy_trades"]),
    }

    # (4) the sleeve really trades, and the three states nest as the arms claim
    vote = fe_signals.e2_up(p.price)
    shares = {"vote_off": float((~vote).mean())}
    for key, spec in IB24_2L.ARMS.items():
        shares[key] = float(IB24_2L.sleeve_state(vote, spec).mean())
    runs = {}
    for arm_id in IB24_2L.ARMS:
        r = stages.run_one(api.load_arm("IB24_2L", arm_id), p, env, i0, i1, "pre_tax", 2, 1.0)
        runs[arm_id] = {
            "leg_a_trades": int(r["_legs"]["A"]["trades"]),
            "leg_b_trades": int(r["_legs"]["B"]["trades"]),
            "leg_b_turnover_total": float(r["_legs"]["B"]["turnover_total"]),
            "leg_b_units_end": float(r["_legs"]["B"]["units_end"]),
        }
    out["sleeve"] = {"state_shares": shares, "runs": runs}
    out["sleeve_pass"] = bool(
        shares["vote_off"] < shares["C_2L"] and shares["A_2L"] <= shares["vote_off"]
        and shares["B_2L"] <= shares["A_2L"]
        and all(v["leg_a_trades"] > 0 and v["leg_b_trades"] > 0
                and v["leg_b_turnover_total"] > 0 for v in runs.values())
    )

    out["pass"] = bool(out["state_machine"]["pass"] and out["routing_pass"]
                       and out["band_3pp"]["pass"] and out["sleeve_pass"])
    print(json.dumps(out, indent=1, sort_keys=True, default=float))
    print(f"check {'PASS' if out['pass'] else 'FAIL'}", flush=True)
    return 0 if out["pass"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.closeout_v1.run_batch")
    ap.add_argument("--batch", default=BATCH)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--check", action="store_true", help="the plugins' own property checks only")
    ap.add_argument("--hash", action="store_true", help="sha256 of the batch's results files")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    if args.hash:
        for name in ("RESULTS.json", "SCOREBOARD.md"):
            q = RESULTS / name
            print(f"{name}: {sha256(q) if q.exists() else 'missing'}")
        return 0
    arms = [f"{i}/{a}" for i in IDEAS for a in api.load_idea(i).ARMS]
    print(f"batch {args.batch}: screening {arms} (control = {CONTROL_ARM})", flush=True)
    res = R.run_batch(args.batch, list(IDEAS), cash_yield="none", workers=args.workers,
                      stage2=True, allow_dirty=False, quiet=False)
    print(f"total wall {res['wall_s']:.1f}s", flush=True)
    (Path("research") / BATCH / "RUN_TIMING.json").write_text(json.dumps(
        {"batch": args.batch, "study": BATCH, "workers": args.workers,
         "batch_total_wall_s": float(res["wall_s"]),
         "arm_wall_s": {k: float(v) for k, v in sorted(res["timing"].items())}},
        indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
