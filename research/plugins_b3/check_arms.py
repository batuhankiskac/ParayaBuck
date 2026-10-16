"""plugins_b3 -- unit checks for the nine plugin modules: nothing here simulates a strategy, reads
a result file or computes a return, NAV or gate.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_b3.check_arms

Four families of check:

1. **Helper parity.** Every recursive/rolling helper of `research/plugins_b3/helpers.py`
   (`ema`, `wilder_atr`, `trailing_sum`, `trailing_std_full`, `trailing_median_full`, `lag`) and
   every recursion inside a plugin (`garch_sigma`, `sticky_vol`, `sticky_target`) is compared
   element-wise with a naive per-path Python loop written from the same definition in this file.
2. **Neutral-knob identities.** An arm whose knob is set to its neutral value must reproduce the
   K control's target series bit-for-bit (`ib11` clip 1.0/1.0, `ib19` tilt 1.0, `ib32` band 0.0,
   `ib23` multiplier 1.0, and `ib23` A/B on a panel with no copper/gold history, where the
   "unavailable -> production" convention says they *are* production); `r2_state_with_entry` with
   the production gate must equal `fe_signals.r2_state`.
3. **Per-arm reconstruction.** Each arm's target series is rebuilt from the arm text with the
   naive helpers and compared with the module's `targets` output on the real main window.
4. **Availability and refusal.** `NotApplicable` exactly on the windows the idea bank does not
   evaluate (IB03: GSPC_1957_1999; IB15: M1-M5; IB19: M3-M5; IB23: the six OOS windows), and
   `NeedsKernel` for every IB27/IB38 arm.
"""

from __future__ import annotations

import sys

import numpy as np

from research.batch_screen import api, smoke, windows
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers

OOS_KEYS = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")

TOL = 0.0  # bit-for-bit where the reference is the same arithmetic, else a small epsilon
EPS = 1e-12

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{(' -- ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def eq(name: str, got, want, tol=TOL) -> None:
    """Element-wise comparison; `tol` is absolute (the cumsum helpers differ from a window sum
    only by floating-point summation order, ~1e-11 on a price-scale series)."""
    a, b = np.asarray(got, dtype=float), np.asarray(want, dtype=float)
    d = float(np.nanmax(np.abs(a - b))) if a.shape == b.shape else np.inf
    check(name, a.shape == b.shape and d <= tol, f"max|diff|={d:.3g}")


# ----------------------------------------------------------------------------- naive references
def naive_ema(a, n):
    P, T = a.shape
    out = np.full_like(a, np.nan)
    alpha = 2.0 / (n + 1.0)
    for p in range(P):
        state, acc, cnt = np.nan, 0.0, 0
        for t in range(T):
            v = a[p, t]
            if np.isfinite(v):
                acc += v
                cnt += 1
                if np.isnan(state) and cnt >= n:
                    state = acc / n
                elif np.isfinite(state):
                    state = alpha * v + (1.0 - alpha) * state
            out[p, t] = state
    return out


def naive_atr(high, low, close, n):
    P, T = high.shape
    out = np.full_like(high, np.nan)
    for p in range(P):
        state, acc, cnt = np.nan, 0.0, 0
        for t in range(T):
            if t == 0 or not np.isfinite(close[p, t - 1]):
                tr = np.nan
            else:
                tr = max(high[p, t] - low[p, t], abs(high[p, t] - close[p, t - 1]),
                         abs(low[p, t] - close[p, t - 1]))
            if np.isfinite(tr):
                acc += tr
                cnt += 1
                if np.isnan(state) and cnt >= n:
                    state = acc / n
                elif np.isfinite(state):
                    state = ((n - 1.0) * state + tr) / n
            out[p, t] = state
    return out


def naive_trailing(a, n, kind):
    P, T = a.shape
    out = np.full_like(a, np.nan)
    for p in range(P):
        for t in range(n - 1, T):
            w = a[p, t - n + 1 : t + 1]
            fin = np.isfinite(w)
            if not fin.all():
                continue
            if kind == "sum":
                out[p, t] = w.sum()
            elif kind == "std":
                out[p, t] = w.std(ddof=1)
            elif kind == "median":
                out[p, t] = np.median(w)
            else:  # pragma: no cover
                raise ValueError(kind)
    return out


def naive_garch(close, omega, alpha, beta, seed_n):
    P, T = close.shape
    out = np.full_like(close, np.nan)
    for p in range(P):
        r = [close[p, t] / close[p, t - 1] - 1.0 for t in range(1, T)]
        head = np.array(r[:seed_n])
        state = head.var(ddof=1) if len(head) == seed_n else np.nan
        out[p, seed_n] = np.sqrt(252.0 * state)
        for t in range(seed_n + 1, T):
            state = omega + alpha * r[t - 1] ** 2 + beta * state
            out[p, t] = np.sqrt(252.0 * state)
    return out


def naive_sticky(a, band):
    P, T = a.shape
    out = np.full_like(a, np.nan)
    for p in range(P):
        accepted = np.nan
        for t in range(T):
            v = a[p, t]
            if np.isfinite(v):
                if np.isnan(accepted) or abs(v / accepted - 1.0) > band:
                    accepted = v
            out[p, t] = accepted
    return out


def naive_sticky_target(a, band):
    P, T = a.shape
    out = np.empty_like(a)
    for p in range(P):
        accepted = a[p, 0]
        out[p, 0] = accepted
        for t in range(1, T):
            if abs(a[p, t] - accepted) > band:
                accepted = a[p, t]
            out[p, t] = accepted
    return out


# ----------------------------------------------------------------------------- panels and control
def spec(idea: str, arm: str, **over) -> dict:
    mod = api.load_idea(idea)
    s = dict(mod.ARMS[arm])
    s.update(over)
    return {k: v for k, v in s.items() if k not in ("rule", "leg", "cash_yield")}


def run(idea: str, arm: str, data, **over):
    return api.load_idea(idea).targets(data, spec(idea, arm, **over))


def k_wants(data):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)


def pre_futures_panel(n=1000, seed=5):
    """A synthetic panel dated before the copper/gold futures history (1995), so the extra series
    the IB23 arms read have no value anywhere in it."""
    from datetime import date, timedelta

    rng = np.random.default_rng(seed)
    days, d = [], date(1995, 1, 3)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    price = 100.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.012, (1, n)), axis=1)
    zeros = np.zeros((1, n))
    rate = np.full((1, n), 0.04)
    return api.panel(days, price=price, div=zeros, rate=rate, o2=price * 0.998, c2=price,
                     d2=zeros, window="pre_futures")


def channels(data, ema_n, atr_n, k):
    """The naive Keltner *level* (the plugin compares the close against it)."""
    hi, lo = helpers.relative_high_low(data)
    return naive_ema(data.price, ema_n) + k * naive_atr(hi, lo, data.price, atr_n)


def naive_wants(data, e2, factor=None, comps=None):
    comps = fe_signals.early_components(data.price) if comps is None else comps
    if factor is None:
        on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    return fe_signals.sized_wants(e2, factor, data.vol)


def naive_r2_with_entry(price, e2, entry_up):
    """`fe_signals.r2_state` transcribed again, with the entry gate as an argument."""
    sma50_up, vol_p, med_p, lag_cond = fe_signals.early_components(price)
    P, T = price.shape
    early = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T))
    for t in range(T):
        e2t = e2[:, t]
        early = np.where(e2t, False, early)
        entry = entry_up[:, t] & np.isfinite(med_p[:, t]) & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t]
        early = np.where(early & (~sma50_up[:, t]) & (~e2t), False, early)
        can = (~early) & (~e2t) & np.isfinite(med_p[:, t])
        early = np.where(can & entry, True, early)
        on_eff[:, t] = e2t | early
        factor[:, t] = np.where(e2t, 1.0, np.where(early, 0.5, 0.0))
    return on_eff, factor


# ----------------------------------------------------------------------------- the four families
def helper_parity(pan: dict) -> None:
    rng = np.random.default_rng(11)
    px = 100.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.012, (3, 400)), axis=1)
    hi = px * (1.0 + np.abs(rng.normal(0.0, 0.004, px.shape)))
    lo = px * (1.0 - np.abs(rng.normal(0.0, 0.004, px.shape)))
    vol = fe_signals.vol_n(px)
    eq("helpers.ema vs naive", helpers.ema(px, 20), naive_ema(px, 20))
    eq("helpers.wilder_atr vs naive", helpers.wilder_atr(hi, lo, px, 20), naive_atr(hi, lo, px, 20))
    eq("helpers.trailing_sum vs naive", helpers.trailing_sum(px, 60),
       naive_trailing(px, 60, "sum"), tol=1e-8)
    eq("helpers.trailing_std_full vs naive", helpers.trailing_std_full(vol, 60),
       naive_trailing(vol, 60, "std"), tol=1e-12)
    gap = px.copy()
    gap[:, :100] = np.nan  # a leading gap must leave the affected windows NaN, then recover
    eq("helpers.trailing_sum recovers after a leading gap",
       np.isfinite(helpers.trailing_sum(gap, 60)), np.isfinite(naive_trailing(gap, 60, "sum")))
    eq("helpers.trailing_median_full vs naive", helpers.trailing_median_full(vol, 60),
       naive_trailing(vol, 60, "median"))
    eq("helpers.lag vs shift", helpers.lag(px, 20)[:, 20:], px[:, :-20])
    eq("helpers.session_year (real window)", helpers.session_year(pan["main"]), pan["main"].year)
    eq("helpers.session_year (chunk pads column 0)", helpers.session_year(pan["boot"])[1:],
       pan["boot"].year)

    import research.ideas.ib07 as ib07
    import research.ideas.ib32 as ib32

    ga = spec("ib07", "A")
    eq("ib07.garch_sigma vs naive",
       ib07.garch_sigma(px, ga["omega"], ga["alpha"], ga["beta"], ga["seed_n"]),
       naive_garch(px, ga["omega"], ga["alpha"], ga["beta"], ga["seed_n"]))
    eq("ib32.sticky_vol(0.20) vs naive", ib32.sticky_vol(vol, 0.20), naive_sticky(vol, 0.20))
    eq("ib32.sticky_vol(0.0) == vol", ib32.sticky_vol(vol, 0.0), vol)


def identities(pan: dict) -> None:
    main = pan["main"]
    k = k_wants(main)
    eq("ib11 neutral clip == K", run("ib11", "A", main, lo=1.0, hi=1.0), k)
    eq("ib11 C neutral clip == K", run("ib11", "C", main, lo=1.0, hi=1.0), k)
    eq("ib19 tilt 1.0 == K", run("ib19", "A", main, tilt=1.0), k)
    eq("ib23 C mult 1.0 == K", run("ib23", "C", main, mult=1.0), k)
    pre = pre_futures_panel()
    for arm in ("A", "B", "C"):
        try:
            run("ib23", arm, pre)
        except api.NotApplicable:
            check(f"ib23 {arm} NotApplicable on a panel with no copper/gold", True)
        else:
            check(f"ib23 {arm} NotApplicable on a panel with no copper/gold", False,
                  "returned weights")
    for arm in ("A", "B"):
        band = dict(A=0.20, B=0.35)[arm]
        eq(f"ib32 {arm} band 0.0 == K", run("ib32", arm, main, band=0.0), k)
        narrow = run("ib32", arm, main, band=band)
        check(f"ib32 {arm} differs from K", not np.array_equal(narrow, k),
              f"mean|diff|={float(np.mean(np.abs(narrow - k))):.4g}")
    eq("ib32 C band 0.0 == K", run("ib32", "C", main, band=0.0), k)
    ib32 = api.load_idea("ib32")
    narrow = ib32.sticky_vol(main.vol, 0.20)
    wide = ib32.sticky_vol(main.vol, 0.35)
    def changes(s):
        return int((np.diff(s[0]) != 0).sum())

    check("ib32 narrower band moves at least as often", changes(narrow) >= changes(wide),
          f"{changes(narrow)} >= {changes(wide)}")

    comps = fe_signals.early_components(main.price)
    prod = fe_signals.r2_state(main.price, fe_signals.e2_up(main.price), comps)
    mine = helpers.r2_state_with_entry(main.price, fe_signals.e2_up(main.price), comps[0], comps)
    eq("r2_state_with_entry(production gate) == fe_signals.r2_state factor", mine[1], prod[1])
    check("r2_state_with_entry(production gate) == fe_signals.r2_state on_eff",
          np.array_equal(mine[0], prod[0]))
    s = fe_signals.sma_up(main.price, SMA_N).astype(np.int64)
    t = fe_signals.tsm_up_price(main.price).astype(np.int64)
    d = fe_signals.don_up(main.price, DON_N).astype(np.int64)
    never = np.zeros_like(main.price, dtype=bool)
    check("vote_3_of_4 without the extra vote == production E2",
          np.array_equal(helpers.vote_3_of_4(s, t, d, never, never), fe_signals.e2_up(main.price)))


def reconstructions(pan: dict) -> None:
    main = pan["main"]
    price = main.price
    comps = fe_signals.early_components(price)
    s = fe_signals.sma_up(price, SMA_N).astype(np.int64)
    t = fe_signals.tsm_up_price(price).astype(np.int64)
    d = fe_signals.don_up(price, DON_N).astype(np.int64)

    # --- IB03: the naive channel re-derives each arm's vote
    chan = channels(main, 20, 20, 1.0)
    chan_up = price > chan
    want_a = fe_signals.sized_wants(*fe_signals.r2_state(price, (s + t + chan_up) >= 2, comps),
                                    main.vol)
    eq("ib03 A vs naive Keltner vote", run("ib03", "A", main), want_a)
    chan50_up = price > channels(main, 50, 20, 0.5)
    want_b = fe_signals.sized_wants(*fe_signals.r2_state(price, (t + d + chan50_up) >= 2, comps),
                                    main.vol)
    eq("ib03 B vs naive Keltner vote", run("ib03", "B", main), want_b)
    e2 = fe_signals.e2_up(price)
    on_eff, factor = naive_r2_with_entry(price, e2, chan_up)
    eq("ib03 C vs naive Keltner entry gate",
       run("ib03", "C", main), fe_signals.sized_wants(on_eff, factor, main.vol))
    check("ib03 C differs from K (the gate bites)", not np.array_equal(
        run("ib03", "C", main), k_wants(main)))

    # --- IB07: sigma from the naive recursion
    for arm, mode in (("A", "garch"), ("B", "max"), ("C", "mix")):
        sp = spec("ib07", arm)
        sigma = naive_garch(main.close, sp["omega"], sp["alpha"], sp["beta"], sp["seed_n"])
        vol_in = {"garch": sigma, "max": np.maximum(main.vol, sigma),
                  "mix": 0.5 * main.vol + 0.5 * sigma}[mode]
        want = fe_signals.sized_wants(*fe_signals.r2_state(price, e2, comps), vol_in)
        eq(f"ib07 {arm} vs naive GARCH", run("ib07", arm, main), want)

    # --- IB11: semi-deviations from a naive trailing sum
    r = main.close[:, 1:] / main.close[:, :-1] - 1.0
    for arm, n, scope in (("A", 60, "all"), ("B", 252, "all"), ("C", 60, "early")):
        pad = np.full(main.close.shape, np.nan)
        pad[:, 1:] = r
        up = np.sqrt(2.0 * 252.0 / n * naive_trailing(pad * pad * (pad > 0), n, "sum"))
        dn = np.sqrt(2.0 * 252.0 / n * naive_trailing(pad * pad * (pad < 0), n, "sum"))
        tilt = np.clip(np.divide(up, dn, out=np.ones_like(up), where=(dn > 0) & np.isfinite(dn)),
                       0.70, 1.00)
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
        base = fe_signals.sized_wants(on_eff, factor, main.vol)
        want = base * (np.where(factor == 0.5, tilt, 1.0) if scope == "early" else tilt)
        want = np.where(np.isfinite(tilt), want, base)
        eq(f"ib11 {arm} vs naive semi-deviations", run("ib11", arm, main),
           np.minimum(want, 0.98), tol=1e-8)
        check(f"ib11 {arm} differs from K somewhere", not np.array_equal(run("ib11", arm, main),
                                                                       base),
              f"mean|diff|={float(np.mean(np.abs(run('ib11', arm, main) - base))):.4g}")

    # --- IB15: arm A halves on a negative VRP, from the naive inputs
    vix = main.extra("vix", ffill=True)
    vrp = (vix / 100.0) ** 2 - fe_signals.vol_n(price) ** 2
    ok = np.isfinite(vrp)
    base = fe_signals.sized_wants(*fe_signals.r2_state(price, e2, comps), main.vol)
    eq("ib15 A vs naive VRP halving",
       run("ib15", "A", main), base * np.where(ok & (vrp < 0), 0.5, 1.0))
    med = naive_trailing(vrp, 252, "median")
    sd = naive_trailing(vrp, 252, "std")
    z = np.where(sd > 0, (vrp - med) / np.where(sd > 0, sd, 1.0), np.nan)
    good = np.isfinite(z)
    mult = np.where(good, np.clip(1.0 + 0.5 * np.where(good, z, 0.0), 0.7, 1.1), 1.0)
    eq("ib15 B vs naive z tilt", run("ib15", "B", main),
       np.minimum(base * mult, 0.98), tol=1e-6)
    check("ib15 B never exceeds the production cap",
          float(np.max(run("ib15", "B", main))) <= 0.98 + 1e-12)
    vote = (ok & (vrp > 0)).astype(np.int64)
    e2v = helpers.vote_3_of_4(s, t, d, vote, ok)
    want_c = fe_signals.sized_wants(*fe_signals.r2_state(price, e2v, comps), main.vol)
    eq("ib15 C vs naive 3-of-4 vote", run("ib15", "C", main), want_c)

    # --- IB19: the multiplier from the calendar year alone
    year = helpers.session_year(main)
    for arm, cut in (("A", (year % 4 == 1) | (year % 4 == 2)), ("B", year % 4 == 0),
                     ("C", (year % 4 == 3) | (year % 4 == 0))):
        eq(f"ib19 {arm} vs naive term-year multiplier",
           run("ib19", arm, main), k_wants(main) * np.where(cut, 0.85, 1.0))
    check("ib19 A/B/C are not identical",
          not (np.array_equal(run("ib19", "A", main), run("ib19", "B", main))
               or np.array_equal(run("ib19", "A", main), run("ib19", "C", main))))

    # --- IB23: the ratio's own return and the entry gate, from the two extra series
    ratio = main.extra("copper") / main.extra("gold")
    rr126 = ratio / helpers.lag(ratio, 126) - 1.0
    ok126 = np.isfinite(rr126)
    entry = comps[0] & (~ok126 | (rr126 >= 0))
    on_b, fac_b = naive_r2_with_entry(price, e2, entry)
    eq("ib23 B vs naive entry gate", run("ib23", "B", main),
       fe_signals.sized_wants(on_b, fac_b, main.vol))
    rr = ratio / helpers.lag(ratio, 252) - 1.0
    okr = np.isfinite(rr)
    e2r = helpers.vote_3_of_4(s, t, d, (okr & (rr > 0)).astype(np.int64), okr)
    eq("ib23 A vs naive copper/gold vote",
       run("ib23", "A", main), fe_signals.sized_wants(*fe_signals.r2_state(price, e2r, comps),
                                                      main.vol))
    below = np.isfinite(helpers.trailing_median_full(ratio, 252)) & (
        ratio < helpers.trailing_median_full(ratio, 252))
    eq("ib23 C vs naive median test",
       run("ib23", "C", main), base * np.where(below, 0.75, 1.0))

    # --- IB32: the two recursions against the naive loops
    for arm, band in (("A", 0.20), ("B", 0.35)):
        eq(f"ib32 {arm} vs naive sticky vol",
           run("ib32", arm, main),
           fe_signals.sized_wants(*fe_signals.r2_state(price, e2, comps),
                                  naive_sticky(main.vol, band)))
    eq("ib32 C vs naive sticky target", run("ib32", "C", main), naive_sticky_target(base, 0.05))


def availability(pan: dict) -> None:
    def na(idea, arm, window, **over):
        _days, data, _i0, _i1 = windows.oos_panel(window)
        try:
            run(idea, arm, data, **over)
        except api.NotApplicable:
            return True
        return False

    for arm in ("A", "B", "C"):
        check(f"ib03 {arm} N/A on GSPC_1957_1999", na("ib03", arm, "GSPC_1957_1999"))
        ok = all(not na("ib03", arm, w) for w in ("M1", "M2", "M3", "M4", "M5"))
        check(f"ib03 {arm} applicable on M1-M5", ok)
        check(f"ib15 {arm} N/A on M1-M5", all(na("ib15", arm, w) for w in
                                              ("M1", "M2", "M3", "M4", "M5")))
        check(f"ib15 {arm} applicable on GSPC_1957_1999", not na("ib15", arm, "GSPC_1957_1999"))
        check(f"ib19 {arm} N/A on the non-US markets",
              all(na("ib19", arm, w) for w in ("M3", "M4", "M5")))
        check(f"ib19 {arm} applicable on the US windows",
              all(not na("ib19", arm, w) for w in ("GSPC_1957_1999", "M1", "M2")))
        check(f"ib23 {arm} N/A on every OOS window",
              all(na("ib23", arm, w) for w in
                  ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")))


def sweep(pan: dict) -> None:
    """Every plugin arm on every window kind the framework uses: the six OOS windows and all 21
    ETF windows, asserting the returned weights are the panel's shape, finite, and in `[0, 0.98]`
    (or `n/a` where the module declares `NotApplicable`). No metric, no simulation."""
    from research.fast_engine.fe_config import INSTRUMENTS

    idle, n_bad = [], 0
    for idea in ("ib03", "ib07", "ib11", "ib15", "ib19", "ib23", "ib32"):
        mod = api.load_idea(idea)
        for arm in mod.ARMS:
            for label, data in (("main", pan["main"]), ("boot", pan["boot"]),
                                ("synthetic", pan["synthetic"])):
                n_bad += not _one(idea, arm, data, f"{idea}/{arm} {label}")
            for key in OOS_KEYS:
                data = windows.oos_panel(key)[1]
                n_bad += not _one(idea, arm, data, f"{idea}/{arm} oos:{key}")
                idle.append(f"{idea}/{arm} oos:{key}")
            for key in INSTRUMENTS:
                data = windows.instrument_panel(key)[1]
                n_bad += not _one(idea, arm, data, f"{idea}/{arm} etf:{key}")
    check("sweep of 6 OOS + 21 ETF windows per arm has no shape/value failure", n_bad == 0,
          f"{n_bad} failures over {len(idle)} OOS + ETF cells")


def _one(idea, arm, data, label) -> bool:
    try:
        w = np.asarray(run(idea, arm, data), dtype=float)
    except api.NotApplicable:
        return True
    ok = (w.shape == data.price.shape and np.isfinite(w).all()
          and float(w.min()) >= 0.0 and float(w.max()) <= 0.98 + 1e-12)
    if not ok:
        print(f"       {label}: shape={w.shape} min={np.nanmin(w)} max={np.nanmax(w)}")
    return ok


def refusals(pan=None) -> None:
    for idea, n in (("ib27", 3), ("ib38", 3)):
        mod = api.load_idea(idea)
        check(f"{idea} declares {n} arms", len(mod.ARMS) == n)
        for arm in mod.ARMS:
            try:
                api.resolve_arm(mod, arm)
            except api.NeedsKernel:
                check(f"{idea}/{arm} refused with NEEDS_KERNEL", True)
            else:
                check(f"{idea}/{arm} refused with NEEDS_KERNEL", False, "resolve_arm accepted it")


def main(argv=None) -> int:
    pan = smoke.smoke_panels()
    for section, fn in (("helper parity", helper_parity), ("neutral-knob identities", identities),
                        ("per-arm reconstruction", reconstructions),
                        ("availability", availability), ("window sweep", sweep),
                        ("kernel refusals", refusals)):
        print(f"{section}:")
        fn(pan)
    print(f"check_arms {'PASS' if not FAILED else 'FAIL'} "
          f"({len(FAILED)} failed: {FAILED})")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
