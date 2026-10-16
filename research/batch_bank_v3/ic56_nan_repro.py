"""Minimal repro of the IC56 NaN bootstrap deltas (bank_v3, 2026-10-16).

Diagnosis (see docs/research/batch_bank_v3.md): the `wht` option's chunk path
(`research/fast_engine_opts/fe_opts_legs.chunk_raw_panel` -> `chunk_triple`) rebuilds the traded
leg from the *raw price* triple `[close(idx0-1), close(idx_t), ...]` of the drawn sessions and
hands it to `fe_signals.synthetic_2x`, whose daily-reset 2x recurrence reads the return between
consecutive *columns*. A bootstrap chunk is stitched from ~20-session blocks with a jump between
blocks (`fe_boot.draw_positions`), so column t and t-1 at a block boundary are two unrelated
sessions: the raw-price reconstruction spans the calendar gap there, while the framework's own
`batch_screen.boot.boot_panels` builds each drawn session's *own* return and skips the gap. For a
2x leg one such spanning return below -50% makes the gross factor `1 + 2r - cost` non-positive and
the cumprod path flips sign, so the leg's price goes to zero/negative and the per-path CAGRs
`(nav[k]/nav[0]) ** (1/yrs) - 1` become NaN.

Run: `/Users/batuhankiskac/.venvs/pb-research/bin/python -m research.batch_bank_v3.ic56_nan_repro`
(from the worktree root). Read-only: no results file and no framework file is written.
"""

from __future__ import annotations

import json

import numpy as np

from research.batch_screen import api
from research.batch_screen import boot as B
from research.batch_screen import stages
from research.batch_screen.rules import CONTROL_ARM
from research.fast_engine import fe_config as C
from research.fast_engine import fe_data
from research.fast_engine_opts import fe_opts_legs as LEGS

PATHS = 2
N = 16  # small bootstrap: enough to see the NaN, seconds to run


def part1_the_mechanism() -> dict:
    """The rebuilt chunk leg against the framework's own chunk leg, per drawn block jump."""
    win = B.BootWindow()
    rng = np.random.default_rng(C.SEED)
    pos = win.positions(rng, C.BLOCK, PATHS)
    pan = B.boot_panels(win, pos, ("2x",), (0.0,))[("2x", 0.0)]
    fw = np.asarray(pan.legs["2x"][1])
    price = np.asarray(pan.price)
    rebuilt = np.asarray(LEGS.chunk_raw_panel(pan, pos, wht=0.20).legs["2x"][1])

    # a drawn index is a jump when it is not the drawn session right after the previous one
    idx = pos + 1
    _o, arr, _y = fe_data.load()
    c = np.asarray(arr["QQQ"][1])
    # the rebuilt arrays' own returns, by column: column 0 is the base, column j >= 1 is the drawn
    # session drawn j-1, so the return into column j >= 2 is close[j-1] / close[j-2] over *drawn*
    # sessions -- spanning the calendar gap whenever the draw jumped there.
    r = np.concatenate([(c[idx[:, :1]] / c[idx[:, :1] - 1] - 1),
                        (c[idx[:, 1:]] / c[idx[:, :-1]] - 1)], axis=1)  # (P, L) -> columns 1..L
    jump = idx[:, 1:] != idx[:, :-1] + 1  # (P, L-1) -> the step into rebuilt columns 2..L
    kill = np.zeros_like(jump)
    kill[:, :] = jump & (1 + 2 * r[:, 1:] <= 0)  # a step whose 2x gross factor is <= 0
    rows = []
    for p in range(PATHS):
        js = np.flatnonzero(jump[p]) + 2  # rebuilt column that spans the jump
        ks = np.flatnonzero(kill[p]) + 2  # rebuilt column whose 2x factor is <= 0
        zero = np.flatnonzero(rebuilt[p] <= 0)
        rows.append({
            "path": p, "n_jumps": int(js.size), "first_jump_column": int(js[0]),
            "n_kill_columns": int(ks.size), "first_kill_column": int(ks[0]) if ks.size else None,
            "first_le_0_column_rebuilt": int(zero[0]) if zero.size else None,
            "n_columns_le_0_framework": int((fw[p] <= 0).sum()),
            "n_columns_le_0_rebuilt": int((rebuilt[p] <= 0).sum()),
            "framework_min": float(fw[p].min()),
            "rebuilt_min": float(rebuilt[p].min()),
        })

    # the arithmetic at the first jump of path 0: the spanning raw-price return and its 2x factor
    p0, js = next((p, np.flatnonzero(jump[p]) + 2) for p in range(PATHS) if jump[p].any())
    col = int(js[0])
    own = float(price[p0, col] / price[p0, col - 1] - 1)
    return {
        "chunk_length": int(pos.shape[1]),
        "jumps_per_path": rows,
        "first_jump_example": {
            "path": p0, "rebuilt_column": col,
            "drawn_session_prev": int(idx[p0, col - 2]), "drawn_session": int(idx[p0, col - 1]),
            "spanning_raw_return": float(r[p0, col - 1]),
            "reconstructed_2x_factor": float(1 + 2 * r[p0, col - 1] - C.EXP_Q / 252),
            "framework_own_return_at_that_column": own,
        },
    }


def part2_jump_free_isolation() -> dict:
    """The same rebuild on a drawn walk with no jump and no pool wrap: framework == rebuild.

    This is the isolation step: the reconstruction is correct on a contiguous run, so the block
    jumps (not the withholding arithmetic) are what break it.
    """
    win = B.BootWindow()
    L = 2776  # one full chunk, drawn as a contiguous run inside the pool
    pos = np.arange(1000, 1000 + L, dtype=np.int64)[None, :].repeat(2, axis=0)
    pan = B.boot_panels(win, pos, ("2x",), (0.0,))[("2x", 0.0)]
    fw = np.asarray(pan.legs["2x"][1])
    rb = np.asarray(LEGS.chunk_raw_panel(pan, pos, wht=0.0).legs["2x"][1])
    return {"n_jumps": int((pos[:, 1:] != pos[:, :-1] + 1).sum()),
            "n_columns_le_0_framework": int((fw <= 0).sum()),
            "n_columns_le_0_rebuilt": int((rb <= 0).sum()),
            "max_abs_diff": float(np.max(np.abs(rb - fw))),
            "max_rel_diff": float(np.max(np.abs(rb - fw) / fw))}


def part3_ic40_same_path() -> dict:
    """`leg = "2x_spy"` (IC40) uses the same reconstruction for its option leg.

    `bridge.prepare_panel` calls `chunk_triple(pos, "SPY")` whenever an arm holds the `2x_spy` leg
    inside a bootstrap, so the leg goes through `_leg_of` -> `synthetic_2x` on drawn raw prices with
    the same spanning columns.
    """
    win = B.BootWindow()
    rng = np.random.default_rng(C.SEED)
    pos = win.positions(rng, C.BLOCK, PATHS)
    pan = B.boot_panels(win, pos, ("2x",), (0.0,))[("2x", 0.0)]
    o, c, d = LEGS.chunk_triple(pos, "SPY")
    leg = np.asarray(LEGS._leg_of(o, c, d, pan.meta.get("fin", 0.0), C.EXP_Q)[1])
    return {"n_columns_le_0_spy_leg": int((leg <= 0).sum()), "n_columns": int(leg.size),
            "min_spy_leg": float(leg.min()), "n_columns_le_0_framework_qqq_leg":
            int((np.asarray(pan.legs["2x"][1]) <= 0).sum())}


def part4_end_to_end() -> dict:
    """The arm's own bootstrap through the framework's stage-1 driver, against its control."""
    arm = api.load_arm("IC56", "A")
    ctrl = api.load_arm(*CONTROL_ARM.split("/"))
    v = stages.stage1_vectors(arm, n_pre=N, n_tax=N, quiet=True)
    k = stages.stage1_vectors(ctrl, n_pre=N, n_tax=N, quiet=True)
    out = {}
    for md in ("progressive", "flat"):
        a = np.asarray(v["tax"]["100k"][md][arm["id"]], dtype=np.float64)
        b = np.asarray(k["tax"]["100k"][md][CONTROL_ARM], dtype=np.float64)
        out[f"tax_{md}"] = {
            "arm_median_cagr": float(np.nanmedian(a)),
            "control_median_cagr": float(np.nanmedian(b)),
            "delta_nan": int(np.isnan(a - b).sum()), "n": int(a.size),
            "arm_min_cagr": float(np.nanmin(a)), "arm_max_cagr": float(np.nanmax(a)),
        }
    for md in ("base", "calibrated"):
        a = np.asarray(v["pre"][md][arm["id"]], dtype=np.float64)
        b = np.asarray(k["pre"][md][CONTROL_ARM], dtype=np.float64)
        out[f"pre_{md}"] = {"delta_nan": int(np.isnan(a - b).sum()), "n": int(a.size),
                            "arm_median_cagr": float(np.nanmedian(a))}
    return out


def main() -> None:
    out = {"part1_the_mechanism": part1_the_mechanism(),
           "part2_jump_free_isolation": part2_jump_free_isolation(),
           "part3_ic40_same_path": part3_ic40_same_path(),
           "part4_end_to_end": part4_end_to_end()}
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
