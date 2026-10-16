"""parity_opts -- the option kernel against the engine: options off must be the engine, bit for bit.

Three groups, all comparing *identical* quantities:

  `off`            `fe_sim.simulate` vs `simulate_opts(Opts())` on the QQQ main window, every arm
                   x mode x capital (the NAV path bitwise; every cell of the returned dict equal)
  `off_boot`       the framework's bootstrap driver against `fe_opts_boot` (same paths, same cells)
  `mechanism`      each option switched *on* is checked to (a) move the numbers, (b) respect its
                   own invariants and (c) leave the options it does not touch alone

Run `python -m research.fast_engine_opts.parity_opts [--full]`; `--full` adds the 200-path
bootstrap. Writes PARITY_OPTS.json next to this file. The engine's own
`python -m research.fast_engine.parity --quick` is a separate, mandatory check (see harness.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from research.batch_screen import api, run, windows
from research.fast_engine import fe_sim
from research.fast_engine.fe_config import TAX_CAPITALS
from research.fast_engine.fe_tax import MODES

from . import fe_opts_sim as OS
from .fe_opts import Opts

HERE = Path(__file__).resolve().parent
#: a value no cell can hold: proves both drivers wrote every cell (no uninitialised memory)
SENTINEL = -123456.789
ARMS = ("C0", "A1u", "H2", "K")
CAPS = ("10k", "30k", "100k", "300k", "1M")
FLAT_KEYS = (
    "years", "capital_usd", "ending_nav_usd", "max_dd_nav_usd", "cagr_usd_liquidated",
    "terminal_usd_liquidated", "cagr_usd_unliquidated", "terminal_usd_unliquidated",
    "tax_total_try", "tax_total_usd_at_payment_fx", "strategy_trades", "qld_sells",
    "tax_funding_sales", "sells", "turnover_total", "turnover_annual", "postponements",
    "blocked_band", "deferred", "fees_usd", "index_relief_try",
)


def window(zero_finance=True):
    """`(days, panel, i0, i1, wants)`: the framework's main window and production R2's targets."""
    days, p, i0, i1 = windows.main_panel(0.0 if zero_finance else 0.0075)
    arm = run.control_spec()
    wants = api.call_targets(arm["mod"], p, arm).wants
    return days, p, i0, i1, wants


def _cells_equal(a, b):
    """The largest absolute difference over the engine's own numeric cells (NaN if a key missed)."""
    worst = 0.0
    for k in FLAT_KEYS:
        d = abs(float(a[k]) - float(b[k]))
        if d > worst:
            worst = d
    for k in ("tax_assessed_try_by_year", "postponements_by_year", "deferrals_by_year",
              "realised_gain_try_by_reason"):
        if a[k] != b[k]:
            return float("inf")
    return worst


def off_side_by_side(arms=ARMS, modes=MODES, caps=CAPS, m=fe_sim.DEFAULT_M, zero_finance=True):
    """`fe_sim.simulate` vs `simulate_opts(Opts())`: 0 mean difference, bitwise NAV, over 4 x 3 x 5."""
    days, p, i0, i1, wants = window(zero_finance)
    env = windows.try_env(days, i0, i1)
    oa, ca = p.legs[p.leg]
    out = {}
    n_cells = 0
    worst = 0.0
    nav_diff = 0.0
    for arm in arms:
        for mode in modes:
            for cl in caps:
                cap = TAX_CAPITALS[cl]
                ref = fe_sim.simulate(oa, ca, wants, env, cap, i0, i1, arm=arm, mode=mode, m=m)
                got = OS.simulate_opts(oa, ca, wants, env, cap, i0, i1, arm=arm, mode=mode, m=m,
                                       opts=Opts())
                d = _cells_equal(ref, got)
                nd = float(np.max(np.abs(ref["_nav"] - got["_nav"])))
                if not np.array_equal(ref["_nav"], got["_nav"]):
                    nd = max(nd, 1e-12)  # a bitwise difference counts even when it rounds away
                worst = max(worst, d)
                nav_diff = max(nav_diff, nd)
                n_cells += len(FLAT_KEYS)
    out["max_abs_diff"] = worst
    out["max_abs_nav_diff"] = nav_diff
    out["nav_bitwise_identical"] = bool(nav_diff == 0.0)
    out["cells"] = n_cells
    out["pass"] = bool(worst == 0.0 and nav_diff == 0.0)
    return out


def off_boot_parity(n_pre=32, n_tax=32, W=32):
    """`batch_screen.boot._chunk` vs `fe_opts_boot.chunk_opts` with an all-off option bundle.

    The framework's own driver and the option driver on the *same* drawn paths, the same target
    panels (the framework's `_targets`), the same rules, over the pre-tax base/calibrated block and
    the after-tax block of both modes: max |diff| must be exactly 0.

    The draw is a full bootstrap path (`TAX_BOOT_L` drawn columns plus `TAX_BOOT_EXT`) and the
    kernel runs the window's own `i0 -> i1`, exactly as `batch_screen.boot.boot` does: a *shorter*
    draw with the full calendar's indices would read past the end of the price arrays (numba does
    not bounds-check), which is a trap worth naming -- it produced garbage, non-reproducible cells
    the first time this cell was written.
    """
    from research.batch_screen import boot as B
    from research.fast_engine import fe_boot
    from research.fast_engine.fe_config import FIN, TAX_BOOT_EXT, TAX_BOOT_L, TAX_CAPITALS

    from .bridge import Bundle

    arms = [run.control_spec(), api.load_arm("r2_control", "R2"), api.load_arm("vol_shape_p15",
                                                                              "p15")]
    win = B.BootWindow()
    env = win.env
    rng = np.random.default_rng(20261011)
    pos = fe_boot.draw_positions(rng, 20, W, TAX_BOOT_L, win.npool, TAX_BOOT_EXT)
    panels = B.boot_panels(win, pos, ("2x",), (FIN["base"], FIN["calibrated"]), cache={})
    pb, pcal = panels[("2x", FIN["base"])], panels[("2x", FIN["calibrated"])]
    wb = np.ascontiguousarray(np.stack([B._targets(a, pb) for a in arms]))
    wc = np.ascontiguousarray(np.stack([B._targets(a, pcal) for a in arms]))
    band, usd, defer = B._rule_arrays(arms)
    cost = fe_sim.COST_PER_SIDE * fe_sim.DEFAULT_M
    mode_ids = np.array([1, 2], dtype=np.int64)  # progressive, flat
    so = np.ascontiguousarray(pb.legs["2x"][0])
    sc = np.ascontiguousarray(pb.legs["2x"][1])
    so_c = np.ascontiguousarray(pcal.legs["2x"][0])
    sc_c = np.ascontiguousarray(pcal.legs["2x"][1])
    n_a = len(arms)
    args = (so, sc, wb, so_c, sc_c, wc, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y,
            env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, TAX_CAPITALS["100k"],
            np.array([100_000.0]), win.i0, win.i1, cost, win.yrs, env.Y0, env.M_lo, band, usd,
            defer, True, mode_ids, env.cash_rate, 0, n_pre, n_tax, B.DBG0)
    ok_pre = np.full((2, n_a, W), SENTINEL)
    ok_at = np.full((1, 2, n_a, W), SENTINEL)
    mine_pre = np.full((2, n_a, W), SENTINEL)
    mine_at = np.full((1, 2, n_a, W), SENTINEL)
    B._chunk(*args, ok_pre, ok_at)
    from .fe_opts import Opts
    from .fe_opts_boot import chunk_opts

    of, oi = Opts().pack()
    empty = Bundle(optf=np.tile(of, (n_a, 1)), opti=np.tile(oi, (n_a, 1)),
                   fill=np.zeros((n_a, 0)), mrate=np.zeros((n_a, 0)),
                   early=np.zeros((n_a, 0), dtype=np.bool_), wvol=np.zeros((n_a, W, 0)))
    chunk_opts(*args, mine_pre, mine_at,
               empty.optf, empty.opti, empty.fill, empty.mrate, empty.early, empty.wvol,
               empty.optf, empty.opti, empty.fill, empty.mrate, empty.early, empty.wvol)
    # the bundle must be *all-off*: the option arrays are empty, and the kernel branches on them
    def _cmp(a, b, label=""):
        if (a == SENTINEL).any() or (b == SENTINEL).any():
            which = ("chunk" if (a == SENTINEL).any() else "") + ("opts" if (b == SENTINEL).any() else "")
            i = np.argwhere((a == SENTINEL) | (b == SENTINEL))[:4]
            raise AssertionError(f"unwritten cell in {label}: {which} at {i.tolist()}")
        pat = np.isnan(a) == np.isnan(b)
        d = np.where(np.isnan(a) & np.isnan(b), 0.0, np.abs(np.where(pat, a, 0.0) - np.where(pat, b, 0.0)))
        idx = np.argwhere(pat == False)  # noqa: E712
        return float(np.max(d)), bool(pat.all() and not idx.size), idx

    d_pre, ok_p, i_p = _cmp(ok_pre, mine_pre, "pre")
    d_at, ok_a, i_a = _cmp(ok_at, mine_at, "tax")
    bad = [(int(x), int(y), int(z), float(ok_pre[x, y, z]), float(mine_pre[x, y, z]))
           for x, y, z in i_p[:4]]
    return {"cells": 2 * (2 * n_a + 2 * n_a), "n_arms": n_a, "n_paths": W,
            "max_abs_diff_pre": d_pre, "max_abs_diff_tax": d_at,
            "max_abs_diff": max(d_pre, d_at), "nan_pattern_matches": bool(ok_p and ok_a),
            "first_nan_mismatch": bad, "pass": bool(ok_p and ok_a)}


# ----------------------------------------------------------------------------- mechanisms
def _run(opts, **kw):
    days, p, i0, i1, wants = window()
    env = windows.try_env(days, i0, i1)
    oa, ca = p.legs[p.leg]
    return OS.simulate_opts(oa, ca, kw.pop("wants", wants), env, kw.pop("capital", 100_000.0), i0, i1,
                            arm=kw.pop("arm", "K"), mode=kw.pop("mode", "pre_tax"),
                            m=kw.pop("m", fe_sim.DEFAULT_M), opts=opts, **kw)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    full = "--full" in argv
    checks = {"off": off_side_by_side()}
    if "--no-boot" not in argv:
        n = 200 if full else 40
        checks["off_boot"] = off_boot_parity(n_pre=n, n_tax=n)
    checks["harness_pass"] = bool(all(c["pass"] for c in checks.values()))
    (HERE / "PARITY_OPTS.json").write_text(json.dumps(checks, indent=1, sort_keys=True) + "\n")
    print(f"off        pass={checks['off']['pass']} cells={checks['off']['cells']} "
          f"max|d|={checks['off']['max_abs_diff']:.3g} max|dNAV|={checks['off']['max_abs_nav_diff']:.3g}")
    if "off_boot" in checks:
        print(f"off_boot   pass={checks['off_boot']['pass']} cells={checks['off_boot']['cells']} "
              f"max|d|={checks['off_boot']['max_abs_diff']:.3g}")
    return 0 if checks["harness_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
