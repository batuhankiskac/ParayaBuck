"""research/plugins_b1/check_signals.py -- dev checks for the plugin task (no performance metric).

Run from the worktree root:

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_b1.check_signals

It computes **no** return, NAV, CAGR or gate. It only asserts that the signal arithmetic the nine
idea modules add is what it says it is:

1. the vectorised early-entry-gated state machine of `ib01`/`ib05`/`ib21`/`ib34` reproduces
   `fe_signals.r2_state` **bit for bit** with the gate set to all-True (main window, an OOS window
   and a real bootstrap chunk);
2. `ib01`'s 2-of-3 vote with `tsm_up_price` as its third component reproduces `fe_signals.e2_up`;
3. each new statistic matches an independent naive reference on real data: `ib01`'s efficiency
   ratio, `ib34`'s lag-1 autocorrelation (against `numpy.corrcoef`), `ib05`'s rescaled-range Hurst
   exponent, `ib09`'s trailing sample std, `ib30`'s time-under-water, `ib13`'s 60-session change and
   `ib17`'s cycle parity (against a direct reading of the meeting table in `make_fomc_data`);
4. every arm of every plugin returns finite weights of the documented shape/range on the real main
   window, the `M2` OOS window, the `XLK` instrument window and a bootstrap chunk, and the arms
   whose gate is neutral on a panel equal production K's target series there.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from research.batch_screen import api, windows
from research.batch_screen import boot as B
from research.fast_engine import fe_boot, fe_signals
from research.fast_engine.fe_config import (
    BLOCK,
    INSTRUMENTS,
    SEED,
    TAX_BOOT_EXT,
    TAX_BOOT_L,
)
from research.plugins_b1 import make_fomc_data

IDEAS = ("ib01", "ib05", "ib09", "ib13", "ib17", "ib21", "ib25", "ib30", "ib34")
CHECKED = 0


def check(cond, label):
    global CHECKED
    CHECKED += 1
    if not bool(cond):
        raise AssertionError(label)
    print(f"  ok  {label}")


def k_targets(p):
    return api.call_targets(api.load_idea("k_control"), p, api.resolve_arm(api.load_idea("k_control"), "K"))[0]


def panels():
    out = {}
    _d, out["main"], _i0, _i1 = windows.main_panel(0.0)
    _d, out["M2"], _s, _i1 = windows.oos_panel("M2")
    _d, out["XLK"], _h, _lo, _i0, _i1 = windows.instrument_panel("XLK")
    win = B.BootWindow("none")
    pos = fe_boot.draw_positions(np.random.default_rng(SEED), BLOCK, 8, TAX_BOOT_L, win.npool,
                                TAX_BOOT_EXT)
    out["boot"] = B.boot_panels(win, pos, ("2x",), (0.0,))[("2x", 0.0)]
    return out


# ----------------------------------------------------------------------------- naive references
def naive_er(p, n):
    out = np.full_like(p, np.nan)
    chg = np.abs(np.diff(p, axis=1))
    for t in range(n, p.shape[1]):
        den = chg[:, t - n : t].sum(axis=1)
        out[:, t] = np.where(den > 0, np.abs(p[:, t] - p[:, t - n]) / np.where(den > 0, den, 1.0),
                             0.0)
    return out


def naive_hurst(p, n, blocks, sessions):
    r = p[0, 1:] / p[0, :-1] - 1.0
    out = {}
    for t in sessions:
        w = r[t - n : t]
        rs = []
        for nb in blocks:
            vals = []
            for j in range(n // nb):
                blk = w[j * nb : (j + 1) * nb]
                z = np.concatenate([[0.0], np.cumsum(blk - blk.mean())])
                s = blk.std(ddof=1)
                if s > 0:
                    vals.append((z.max() - z.min()) / s)
            rs.append(np.mean(vals) if vals else np.nan)
        lx = np.log(np.asarray(blocks, dtype=float))
        ly = np.log(np.asarray(rs, dtype=float))
        out[t] = float(np.polyfit(lx, ly, 1)[0]) if np.isfinite(ly).all() else np.nan
    return out


def naive_rho1(p, n, sessions):
    r = p[0, 1:] / p[0, :-1] - 1.0
    out = {}
    for t in sessions:
        w = r[t - n : t]
        out[t] = float(np.corrcoef(w[:-1], w[1:])[0, 1])
    return out


def naive_since_high(p):
    last, out = 0, np.zeros(p.shape[1])
    for t in range(p.shape[1]):
        if p[0, t] >= p[0, : t + 1].max():
            last = t
        out[t] = t - last
    return out


# ----------------------------------------------------------------------------- the checks
def main() -> int:
    P = panels()
    mods = {i: api.load_idea(i) for i in IDEAS}

    print("1. the gated state machine vs fe_signals.r2_state (gate all-True)")
    for name, p in P.items():
        e2 = fe_signals.e2_up(p.price)
        comps = fe_signals.early_components(p.price)
        on_ref, fac_ref = fe_signals.r2_state(p.price, e2, comps)
        gate = np.ones_like(e2, dtype=bool)
        for idea in ("ib01", "ib05", "ib21", "ib34"):
            on, fac = mods[idea]._r2_state_gated(p.price, e2, gate)
            check(np.array_equal(on, on_ref) and np.array_equal(fac, fac_ref),
                  f"{idea} state machine == r2_state on {name} ({on.shape})")

    print("2. the 2-of-3 vote with tsm as third == fe_signals.e2_up")
    for name, p in P.items():
        check(np.array_equal(mods["ib01"]._vote_with_third(p.price, fe_signals.tsm_up_price(p.price)),
                             fe_signals.e2_up(p.price)),
              f"ib01 vote == e2_up on {name}")

    print("3. statistics vs naive references (main window)")
    p = P["main"].price
    er = mods["ib01"]._efficiency_ratio(p, 20)
    ref_er = naive_er(p, 20)
    check(np.array_equal(er[:, :20], np.zeros((er.shape[0], 20)))
          and np.array_equal(er[:, 20:], ref_er[:, 20:]),
          "ib01 efficiency ratio (n=20) == naive from session 20, 0 before it")
    rho = mods["ib34"]._rho1(p, 252)
    sessions = [260, 1000, 3000, 5000, 6900]
    ref = naive_rho1(p, 252, sessions)
    check(max(abs(rho[0, t] - ref[t]) for t in sessions) < 1e-12,
          "ib34 lag-1 autocorrelation == numpy.corrcoef")
    hst = mods["ib05"]._hurst(p)
    href = naive_hurst(p, 252, (10, 20, 40, 80), sessions)
    diffs = [abs(hst[0, t] - href[t]) for t in sessions]
    check(max(diffs) < 1e-12, f"ib05 Hurst == naive R/S (max diff {max(diffs):.2e})")
    vol = P["main"].vol
    std = mods["ib09"]._rolling_std(vol, 20)
    for t in (3000, 6900):
        check(abs(std[0, t] - vol[0, t - 19 : t + 1].std(ddof=1)) < 1e-9,
              f"ib09 rolling std(ddof=1, 20) at t={t} == numpy")
    check(not np.isfinite(std[0, 25]) and not np.isfinite(std[0, 38]) and np.isfinite(std[0, 39]),
          "ib09 rolling std is NaN while the window still holds the vol warm-up")
    u = mods["ib30"]._sessions_since_high(p)
    check(np.array_equal(u[0], naive_since_high(p)), "ib30 time-under-water == naive")
    d10 = mods["ib13"]._series(P["main"])
    chg = mods["ib13"]._change(d10, 60)
    check(np.allclose(chg[0, 3000], d10[0, 3000] - d10[0, 2940]), "ib13 60-session change")
    dates, w = make_fomc_data.cycle_index()
    week = P["main"].extra("fomc_week")
    idx = {d: v for d, v in zip(dates, w)}
    days = P["main"].days
    from datetime import date
    anchors = sorted(date.fromisoformat(d) for d in make_fomc_data.MEETINGS)
    ok = True
    for t in (0, 500, 3000, 6900):
        d = date.fromisoformat(str(days[t]))
        expect = (d - max(a for a in anchors if a <= d)).days // 7
        ok &= (week[0, t] == expect == idx[str(days[t])])
    check(ok, "ib17 cycle index == a direct reading of the meeting table")

    print("4. every arm on every real panel: finite weights, documented range")
    for name, p in P.items():
        for idea in IDEAS:
            mod = mods[idea]
            for arm_id in mod.ARMS:
                try:
                    arm = api.resolve_arm(mod, arm_id)
                except api.NeedsKernel:
                    continue
                try:
                    w, leg = api.call_targets(mod, p, arm)
                except api.NotApplicable as exc:
                    print(f"      {idea}/{arm_id} on {name}: n/a ({exc.reason})")
                    continue
                check(np.isfinite(w).all() and leg == arm["leg"] and w.shape == p.price.shape
                      and -1e-12 <= w.min() and w.max() <= 1.25 + 1e-12,
                      f"{idea}/{arm_id} on {name}: finite, range ({w.min():.3f}, {w.max():.3f})")

    print("5. arms whose added mechanism is switched to neutral reproduce K's target series")
    neutral = {
        "ib01": ({"er_n": 20, "er_min": -1.0, "gate": "early"}, 0),
        "ib05": ({"h_min": -1e9, "gate": "early"}, 272),
        "ib09": ({"floor": 1.0, "scope": "all"}, 0),
        "ib13": ({"lag": 60, "soft": np.inf, "mode": "veto"}, 0),
        "ib17": ({"even": 1.0, "odd": 1.0}, 0),
        "ib21": ({"lag": 252, "cut": 1.0, "lead": 1e9, "scope": "all"}, None),
        "ib30": ({"divisor": 1.0, "slope": 0.0, "lo": 1.0, "hi": 1.0}, 0),
        "ib34": ({"n": 252, "rho_min": -2.0, "gate": "early"}, 272),
    }
    for idea, (spec, j0) in neutral.items():
        for name in ("main", "M2", "XLK", "boot"):
            p = P[name]
            mod = mods[idea]
            if j0 is None:  # ib21: the spread exists only after IEF's inception
                try:
                    sp = mod._spread(p, int(spec["lag"]))
                except api.NotApplicable:
                    print(f"      {idea} on {name}: n/a (no IEF in the window)")
                    continue
                cols = np.isfinite(sp).all(axis=0)
                if not cols.any():
                    print(f"      {idea} on {name}: no session with a defined spread")
                    continue
                j0 = int(np.argmax(cols))
            try:
                w = np.atleast_2d(np.asarray(mod.targets(p, spec), dtype=np.float64))
            except api.NotApplicable as exc:
                print(f"      {idea} on {name}: n/a ({exc.reason})")
                continue
            k = k_targets(p)
            check(np.array_equal(w[:, j0:], k[:, j0:]),
                  f"{idea} neutral spec == K on {name} (from session {j0})")

    print("6. every arm on every OOS window and every ETF the holdout stages use (targets only)")
    oos = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")
    etfs = sorted(set(INSTRUMENTS))
    n_ok, n_na = 0, {}
    for kind, keys in (("oos", oos), ("etf", etfs)):
        for key in keys:
            p = (windows.oos_panel(key)[1] if kind == "oos"
                 else windows.instrument_panel(key)[1])
            for idea in IDEAS:
                mod = mods[idea]
                for arm_id in mod.ARMS:
                    try:
                        arm = api.resolve_arm(mod, arm_id)
                    except api.NeedsKernel:
                        continue
                    try:
                        w, leg = api.call_targets(mod, p, arm)
                    except api.NotApplicable as exc:
                        n_na.setdefault(idea, {})[key] = exc.reason
                        continue
                    if not (np.isfinite(w).all() and leg == arm["leg"]
                            and w.shape == p.price.shape):
                        raise AssertionError(f"{idea}/{arm_id} on {kind}:{key}: bad panel")
                    n_ok += 1
    print(f"  ok  {n_ok} arm x window evaluations returned a finite, in-shape target panel")
    globals()["CHECKED"] += 1
    for idea, na in sorted(n_na.items()):
        print(f"      {idea}: NotApplicable on {sorted(na)}")

    print(f"\ncheck_signals: {CHECKED} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
