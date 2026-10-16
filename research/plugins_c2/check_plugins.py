"""plugins_c2 -- mechanical checks of the seven plugins (no return, NAV, CAGR or gate is computed).

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_c2.check_plugins

* OOS applicability of every idea equals the bank's pre-registered evaluable set (IC07: see its
  docstring for the bank's inconsistency; IC17: nothing, the FRED history is truncated);
* IC02's 12-1 vote, IC07's pivots / last-two scan against brute-force loops on synthetic paths;
* IC12's weekly state against a brute-force loop and its as-of read;
* IC12 B / IC22 A / IC27 A / IC48 A equal production K off-state and the declared multiple on it;
* IC27's real-window spread equals the level-ratio construction, and its bootstrap-chunk HYG
  returns equal the drawn source sessions' returns.
"""

from __future__ import annotations

import sys

import numpy as np

from research.batch_screen import api, extra, smoke, windows
from research.ideas import IC02, IC07, IC12, IC17, IC22, IC27, IC48, k_control
from research.plugins_c2 import native

OOS = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")
EXPECTED = {
    "IC02": list(OOS),
    "IC07": ["M1", "M3", "M4", "M5"],
    "IC12": ["GSPC_1957_1999", "M1", "M3", "M4", "M5"],
    "IC17": [],
    "IC22": ["M3", "M4", "M5"],
    "IC27": ["M3", "M4", "M5"],
    "IC48": list(OOS),
}
MODS = {"IC02": IC02, "IC07": IC07, "IC12": IC12, "IC17": IC17, "IC22": IC22, "IC27": IC27,
        "IC48": IC48}


def check(name, ok, fails):
    print(f"  {'ok ' if ok else 'FAIL'} {name}")
    if not ok:
        fails.append(name)


def main() -> int:
    fails: list = []
    panels = {w: windows.oos_panel(w)[1] for w in OOS}
    for idea, mod in MODS.items():
        for arm, spec in mod.ARMS.items():
            got = []
            for w, p in panels.items():
                try:
                    mod.targets(p, spec)
                    got.append(w)
                except api.NotApplicable:
                    pass
            check(f"{idea}/{arm} OOS applicable = {EXPECTED[idea]}", got == EXPECTED[idea], fails)

    rng = np.random.default_rng(20261016)
    x = np.cumsum(rng.normal(size=(2, 400)), axis=1) + 60.0
    up = IC02.skip_momentum_up(x, 252, 21)
    bf = np.zeros_like(up)
    for t in range(252, 400):
        bf[:, t] = x[:, t - 21] > x[:, t - 252]
    check("IC02 12-1 vote = loop", bool((up == bf).all()), fails)

    k = 13
    piv = IC07._extremes(x, k, True)
    bfp = np.zeros_like(piv)
    for p in range(2):
        for i in range(k, 400 - k):
            bfp[p, i] = x[p, i] > max(np.r_[x[p, i - k:i], x[p, i + 1:i + k + 1]])
    check("IC07 pivot highs = loop", bool((piv == bfp).all()), fails)
    m1, m2 = IC07._last_two(IC07._events(piv, k))
    ok = True
    for p in range(2):
        for t in range(400):
            conf = [i for i in range(400) if bfp[p, i] and i + k <= t]
            ok &= m1[p, t] == (conf[-1] if conf else -1)
            ok &= m2[p, t] == (conf[-2] if len(conf) > 1 else -1)
    check("IC07 last two confirmed pivots = loop", bool(ok), fails)

    _d, main, _i0, _i1 = windows.main_panel(0.0)
    kw = k_control.targets(main, k_control.ARMS["K"])
    dates, v = native.observations(main, "nfci")
    st, _calm = IC12.weekly_states(dates, v, IC12.ARMS["A"])
    xl = np.r_[np.nan, v[:-1]]
    ok = True
    for i in range(0, v.size, 7):
        med = np.median(xl[i - 155:i + 1]) if i >= 156 else np.nan
        e = np.nan if not (np.isfinite(med) and i >= 5) else float(xl[i] > med and xl[i] > xl[i - 4])
        ok &= (np.isnan(e) and np.isnan(st[i])) or e == st[i]
    check("IC12 weekly state = loop", bool(ok), fails)
    lab = native.session_labels(main)[0]
    s = native.asof(dates, st, lab)
    ok = True
    for j in range(0, lab.size, 211):
        prior = np.nonzero(dates <= lab[j])[0]
        e = st[prior[-1]] if prior.size else np.nan
        ok &= (np.isnan(e) and np.isnan(s[j])) or e == s[j]
    check("IC12 as-of read = loop", bool(ok), fails)

    def off_on(mod, arm, state, mult):
        w = mod.targets(main, mod.ARMS[arm])
        on = state
        return bool(np.allclose(w[~on], kw[~on]) and np.allclose(w[on], np.minimum(mult * kw[on], 0.98)))

    on12 = np.nan_to_num(native.asof(dates, st, native.session_labels(main))) > 0.5
    check("IC12/B = K off-state, 0.5 K on it", off_on(IC12, "B", on12, 0.5), fails)
    dd, dv = native.observations(main, "dtwexbgs")
    s22, _ = IC22.dollar_states(dv, IC22.ARMS["A"])
    on22 = np.nan_to_num(native.asof(dd, s22, native.session_labels(main))) > 0.5
    check("IC22/A = K off-state, 0.5 K on it", off_on(IC22, "A", on22, 0.5), fails)
    spr = IC27.spread(main, 126)
    on27 = np.nan_to_num(spr, nan=0.0) < -0.03
    check("IC27/A = K off-state, 0.75 K on it", off_on(IC27, "A", on27, 0.75), fails)
    dd20 = IC48.drawdown(main.price, 20)
    w48 = IC48.targets(main, IC48.ARMS["A"])
    mult = np.where(np.isfinite(dd20), np.clip(1 - dd20 / 0.10, 0.5, 1.0), 1.0)
    check("IC48/A = K x clip(1 - dd20/0.10, 0.5, 1)", bool(np.allclose(w48, kw * mult)), fails)

    hyg = main.extra("hyg", ffill=True)
    r = np.full_like(hyg, np.nan)
    r[:, 126:] = hyg[:, 126:] / hyg[:, :-126] - 1
    rp = np.full_like(hyg, np.nan)
    rp[:, 126:] = main.price[:, 126:] / main.price[:, :-126] - 1
    check("IC27 real-window spread = level ratio",
          bool(np.allclose(np.nan_to_num(r - rp, nan=9.0), np.nan_to_num(spr, nan=9.0))), fails)
    b = smoke.real_panels()["boot"]
    lr = IC27.hyg_log_returns(b)
    f = extra.forward_fill(extra.values_on("HYG", list(b.extra_days)).reshape(1, -1))[0]
    ok = True
    for p in range(b.P):
        for t in range(1, b.T, 53):
            src = b.extra_index[p, t]
            e = np.log(f[src] / f[src - 1]) if src > 0 else np.nan
            ok &= (np.isnan(e) and np.isnan(lr[p, t])) or bool(np.isclose(e, lr[p, t]))
    check("IC27 chunk HYG returns = drawn source sessions'", bool(ok), fails)

    print(f"{'PASS' if not fails else 'FAIL'} ({len(fails)} failed)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
