"""repro -- one IC37/C B1 path and its paired K path, decomposed.

    python -m research.rw_boot_audit_v1.repro

The published draw is a deterministic function of `(seed, block, path index)`
(`fe_boot.draw_positions` consumes the rng one path at a time, so a one-path call returns path 0 of
the 1,000-path draw). This module rebuilds that path 0 for `IC37/C` on its own reduced-window pool
and, on the *same* chunk, the framework control `k_control/K`, and decomposes the recorded Δ:

1. the *pairing*: the arm's and the control's pool, start/length, capital and TRY calendar
   (`Windows.boot_pool` + `_control_w20030103.json`), and the equality of their drawn sessions;
2. leg A: the market path the two arms share (the control's leg-A panel is the arm's leg-A panel,
   so a zeroed leg B must reproduce K bit for bit);
3. leg B: the IEF path the kernel reads, column-by-column, against the drawn sessions' **own**
   returns `IEF[idx]/IEF[idx-1]` -- the calendar-spanning columns the audit is about;
4. the pre-tax NAVs and the progressive tax rows at $100k, and whether a NAV path can reach <= 0.

The output is `repro.json` (read-only; nothing here writes to the framework).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from research.batch_screen import api
from research.batch_screen import boot as B
from research.batch_screen import extra as X
from research.batch_screen import run as RUN
from research.batch_screen import windows
from research.fast_engine import fe_sim
from research.fast_engine.fe_config import BLOCK, FIN, SEED, TAX_CAPITALS
from research.fast_engine2 import fe2_sim

HERE = Path(__file__).resolve().parent
IDEA, ARM = "IC37", "C"
CONTROL = "k_control/K"
#: the two-leg arms whose leg B is an extra-data key (the affected class), and a panel-leg control
EXTRA_KEY_ARMS = {"IC37/C": "ief", "IC31/A": "lqd", "ib25_RW/A_RW": "shy", "IB26_2L/A_2L": "ief"}


def _load(idea_arm: str):
    idea, arm = idea_arm.split("/")
    return api.load_arm(idea, arm)


def chunk_path(idea_arm: str, path: int = 0, pool=None, n_paths: int = 1) -> dict:
    """One published chunk of an arm's window: the panels, the draw and the env."""
    arm = _load(idea_arm)
    win = windows.arm_window(arm)
    if pool is None:
        pool = windows.boot_pool(windows.bars()[0], win)
    bw = B.BootWindow("none", pool)
    pos = bw.positions(np.random.default_rng(SEED), BLOCK, path + n_paths)[path : path + n_paths]
    panels = B.boot_panels(bw, pos, ("2x",), (FIN["base"], FIN["calibrated"]), cache={})
    return {"arm": arm, "win": win, "pool": pool, "bw": bw, "pos": pos, "panels": panels}


def _ief_paths(pan, pos, key="ief"):
    """`(raw, own_built)` leg-B level paths of one chunk, `(P, T)`.

    `raw` is what `MarketData.leg_series` returns today (`extra(key)`): the drawn source session's
    own value per column. `own_built` is the level compounded from each drawn session's own
    close-to-close return -- `boot_panels`' own convention for the market leg.
    """
    pool_days = windows.bars()[0]
    series = X.resolve_series(key, pan.extra_window or pan.window)
    pool = X.values_on(series, pool_days)
    idx = np.asarray(pan.extra_index, dtype=np.int64)[:, 1:]     # (P, T-1) drawn source sessions
    own = pool[idx] / pool[idx - 1]
    lvl = np.concatenate([np.ones((idx.shape[0], 1)), np.cumprod(own, axis=1)], axis=1)
    return np.asarray(pan.extra(key), dtype=np.float64), lvl, own, idx, pool


def decompose(c: dict) -> dict:
    pan = c["panels"][("2x", FIN["base"])]
    bw = c["bw"]
    raw, lvl, own, idx, _pool = _ief_paths(pan, c["pos"], c["arm"].get("leg_b") or "ief")
    seen = raw[:, 1:] / raw[:, :-1]
    spanning = ~np.isclose(seen, own, rtol=1e-12, atol=1e-15)
    fixed_seen = lvl[:, 1:] / lvl[:, :-1]
    return {
        "pool": [int(c["pool"][0]), int(c["pool"][1])],
        "win": c["win"].as_dict(),
        "boot_i0_i1": [int(bw.i0), int(bw.i1)],
        "boot_years": float(bw.yrs),
        "chunk_columns": int(pan.price.shape[1]),
        "n_drawn_sessions": int(idx.shape[1]),
        "leg_b_key": c["arm"].get("leg_b"),
        "leg_b_spanning_columns": int(spanning.sum()),
        "leg_b_columns": int(spanning.size),
        "leg_b_max_abs_return_minus_1_as_read": float(np.abs(seen - 1).max()),
        "leg_b_max_abs_return_minus_1_own": float(np.abs(own - 1).max()),
        "leg_b_max_abs_diff_vs_own_fixed": float(np.abs(fixed_seen - own).max()),
        "leg_b_rel_diff_raw_vs_own_max": float(np.abs(seen / own - 1).max()),
        "leg_b_first_spanning_column": (int(np.argmax(spanning)) + 1) if spanning.any() else None,
        "leg_b_level_end_as_read": float(raw[0, -1]),
        "leg_b_level_end_own_built": float(lvl[0, -1]),
        "leg_b_own_log_return_window": float(np.log(own[0, bw.i0 - 1 : bw.i1]).sum()),
        "leg_b_read_log_return_window": float(np.log(seen[0, bw.i0 - 1 : bw.i1]).sum()),
    }


def simulate_both(c: dict) -> dict:
    """The arm (two-leg) and the control (one-leg) on the *same* chunk, pre-tax and after tax."""
    pan = c["panels"][("2x", FIN["base"])]
    arm = c["arm"]
    ctrl = RUN.control_spec()
    bw = c["bw"]
    env = bw.env
    t = api.call_targets(arm["mod"], pan, arm)
    tk = api.call_targets(ctrl["mod"], pan, ctrl)
    oa, ca = pan.legs["2x"]
    ob, cb = pan.leg_series(arm.get("leg_b") or "ief")
    zeros = np.zeros_like(t.wants_b)

    def pre(o_b, c_b, w_b):
        return fe2_sim.simulate2(oa, ca, t.wants[0], o_b, c_b, w_b, env, 1.0, bw.i0, bw.i1,
                                 arm="K", mode="pre_tax", m=2, rule_index=True)

    def prog(o_b, c_b, w_b):
        return fe2_sim.simulate2(oa, ca, t.wants[0], o_b, c_b, w_b, env, TAX_CAPITALS["100k"],
                                 bw.i0, bw.i1, arm="K", mode="progressive", m=2, rule_index=True)

    r_k = fe_sim.simulate(oa, ca, tk.wants[0], env, 1.0, bw.i0, bw.i1, arm="K", mode="pre_tax", m=2)
    r_zero = pre(zeros, zeros, zeros)
    r_arm = pre(ob, cb, t.wants_b[0])
    r_arm_prog = prog(ob, cb, t.wants_b[0])
    r_k_prog = fe_sim.simulate(oa, ca, tk.wants[0], env, TAX_CAPITALS["100k"], bw.i0, bw.i1,
                               arm="K", mode="progressive", m=2)

    def cagr(nav):
        k = bw.i1 - bw.i0 + 1
        return float((nav[k] / nav[0]) ** (252 / k) - 1)

    return {
        "leg_a_targets_identical": bool(np.array_equal(t.wants[0], tk.wants[0])),
        "zero_leg_b_is_the_control_pretax": bool(np.array_equal(r_zero["_nav"], r_k["_nav"])
                                                 and r_zero["cagr_usd_liquidated"]
                                                 == r_k["cagr_usd_liquidated"]),
        "pretax_cagr_control": cagr(r_k["_nav"]),
        "pretax_cagr_arm": cagr(r_arm["_nav"]),
        "pretax_cagr_delta_pp": (cagr(r_arm["_nav"]) - cagr(r_k["_nav"])) * 100,
        "pretax_nav_min_arm": float(np.nanmin(r_arm["_nav"])),
        "pretax_nav_min_control": float(np.nanmin(r_k["_nav"])),
        "leg_b_trades": int(r_arm["_legs"]["B"]["trades"]),
        "leg_b_turnover": float(r_arm["_legs"]["B"]["turnover_total"]),
        "prog_cagr_control": float(r_k_prog["cagr_usd_liquidated"]),
        "prog_cagr_arm": float(r_arm_prog["cagr_usd_liquidated"]),
        "prog_cagr_delta_pp": (float(r_arm_prog["cagr_usd_liquidated"])
                               - float(r_k_prog["cagr_usd_liquidated"])) * 100,
        "prog_tax_control_try": float(r_k_prog["tax_total_try"]),
        "prog_tax_arm_try": float(r_arm_prog["tax_total_try"]),
        "prog_nav_min_arm": float(np.nanmin(r_arm_prog["_nav"])),
        "prog_nav_n_le_0_arm": int((r_arm_prog["_nav"] <= 0).sum()),
        "prog_terminal_arm_usd": float(r_arm_prog["terminal_usd_liquidated"]),
        "leg_b_realised_gain_try": float(sum(r_arm_prog["_legs"]["B"]
                                            ["realised_gain_try_by_reason"].values())),
        "leg_a_realised_gain_try": float(sum(r_arm_prog["_legs"]["A"]
                                            ["realised_gain_try_by_reason"].values())),
    }


def pairing(c: dict) -> dict:
    """The arm's chunk geometry vs the cached reduced-window control's own record."""
    ctrl_file = RUN.control_file(RUN.RESULTS / "bank_v3", c["win"])
    out = {"control_file": ctrl_file.name, "exists": bool(ctrl_file.exists())}
    if not ctrl_file.exists():
        return out
    ctrl = json.loads(ctrl_file.read_text())
    arm_pool = list(c["pool"])
    out.update({
        "control_win": ctrl.get("win"),
        "control_params": ctrl.get("params"),
        "control_pool_matches_arm_pool": (ctrl["record"].get("win", {}).get("tag") == c["win"].tag),
        "control_vectors_n_pre": len((ctrl["vectors"]["pre"]["base"])),
        "control_vectors_n_tax": len(ctrl["vectors"]["tax"]["progressive"]),
        "arm_pool": arm_pool,
    })
    return out


def recorded_path0() -> dict:
    """The published bank_v3 cells: the arm's B1 path 0 and the control's own path 0."""
    arm = json.loads((RUN.RESULTS / "bank_v3" / "IC37__C.json").read_text())
    ctrl = json.loads((RUN.RESULTS / "bank_v3" / "_control_w20030103.json").read_text())
    a = np.asarray(arm["b1_delta_pp"], dtype=np.float64)
    cp = np.asarray(ctrl["vectors"]["tax"]["progressive"], dtype=np.float64)
    return {
        "arm_b1_n": int(a.size),
        "arm_b1_median_pp": float(np.median(a)),
        "arm_b1_path0_delta_pp": float(a[0]),
        "control_tax_progressive_path0": float(cp[0]),
        "control_pool_record": ctrl["params"],
        "arm_b1_min_pp": float(np.nanmin(a)),
        "arm_b1_max_pp": float(np.nanmax(a)),
        "arm_b1_n_nonfinite": int((~np.isfinite(a)).sum()),
        "note_nonfinite": "ic37c_confirm_v1 reported a path whose after-tax NAV went <= 0",
    }


def run(path: int = 0) -> dict:
    c = chunk_path("IC37/C", path=path)
    return {
        "study": "rw_boot_audit_v1",
        "arm": "IC37/C",
        "path": int(path),
        "recorded": recorded_path0(),
        "pairing": pairing(c),
        "decomposition": decompose(c),
        "simulation": simulate_both(c),
    }


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m research.rw_boot_audit_v1.repro")
    ap.add_argument("--path", type=int, default=0)
    a = ap.parse_args(argv)
    res = run(a.path)
    (HERE / "repro.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=float) + "\n")
    print(json.dumps(res, indent=1, sort_keys=True, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
