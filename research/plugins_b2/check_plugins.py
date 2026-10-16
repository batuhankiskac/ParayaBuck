"""research/plugins_b2/check_plugins.py -- mechanical unit checks for this branch's plugins.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_b2.check_plugins

What it does NOT do: no simulation, no NAV, no CAGR, no return, no gate, no registry write.  It
calls each arm's `targets` on the real panels -- main, the OOS windows, the ETF stage and *both*
bootstrap-chunk families (the OOS draw of `boot.oos_pooled`, which the framework's smoke runner
does not build) -- and re-derives every transform independently (own TR/Q, own mask, own state
loop where practical) and compares.  A wrong implementation that still returns an array of the
right shape fails here.

The only numbers printed are counts and distributions of target weights and vol estimates.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

import numpy as np

from research.batch_screen import api, boot as B, windows
from research.batch_screen.boot import OOS_BOOT_L, _oos_market_panels
from research.fast_engine import fe_boot, fe_data, fe_signals
from research.fast_engine.fe_config import BLOCK, SEED, SMA_N, TAX_BOOT_EXT, TAX_BOOT_L

P_BOOT = 4
FAILED = []


# ----------------------------------------------------------------------------- panels
def _pos_oos(rng, P, npool):
    """The OOS draw of `boot.oos_pooled` (positions only; the engine is never run)."""
    pos = np.empty((P, OOS_BOOT_L), dtype=np.int64)
    pos[:, 0] = rng.integers(0, npool, P)
    jump = rng.random((P, OOS_BOOT_L)) < 1 / BLOCK
    fresh = rng.integers(0, npool, (P, OOS_BOOT_L))
    for t in range(1, OOS_BOOT_L):
        pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
    return pos


def panels():
    out = {"main": windows.main_panel(0.0)[1], "etf:XLK": windows.instrument_panel("XLK")[1]}
    for key in ("M1", "M2", "M3", "M4", "M5", "GSPC_1957_1999"):
        out[f"oos:{key}"] = windows.oos_panel(key)[1]
    win = B.BootWindow("none")
    pos = fe_boot.draw_positions(np.random.default_rng(SEED), BLOCK, P_BOOT, TAX_BOOT_L,
                                 win.npool, TAX_BOOT_EXT)
    out["boot:main"] = B.boot_panels(win, pos, ("2x",), (0.0,))[("2x", 0.0)]
    rng = np.random.default_rng(SEED)
    for key in ("M1", "M2", "M4"):
        days, o, c, y, start = fe_data.load_market(key)
        out[f"boot:{key}"] = _oos_market_panels(
            days, o, c, y, start, _pos_oos(rng, P_BOOT, len(days) - start), ("2x",), {}, key)["2x"]
    return out


def call(idea, arm, p):
    """`targets` of one arm on one panel, or the `(marker, reason)` tuple."""
    mod = api.load_idea(idea)
    try:
        return api.call_targets(mod, p, api.load_arm(idea, arm))[0]
    except api.NotApplicable as exc:
        return ("n/a", exc.reason)
    except api.NeedsKernel as exc:
        return ("needs_kernel", str(exc))


def production_wants(p):
    """The control's target series, for the "unchanged elsewhere" comparisons."""
    e2 = fe_signals.e2_up(p.price)
    return fe_signals.sized_wants(*fe_signals.r2_state(p.price, e2,
                                                       fe_signals.early_components(p.price)), p.vol)


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def same(a, b, tol=1e-12):
    return bool(np.allclose(a, b, rtol=0, atol=tol, equal_nan=True))


def hl_ratios(p):
    """Independent high/low read: the panel's own (ETFs) or the registered ratios, in levels."""
    if p.high is not None:
        return p.high, p.low
    return p.extra("hl_high") * p.price, p.extra("hl_low") * p.price


def own_trc(p, high, low):
    """Independent TR/close: previous close from the panel's own price path."""
    prev = np.concatenate([p.price[:, :1], p.price[:, :-1]], axis=1)
    tr = np.maximum(np.maximum(high - low, np.abs(high - prev)), np.abs(low - prev))
    return tr / p.price


def own_roll_mean(a, n):
    out = np.full_like(a, np.nan)
    for t in range(n - 1, a.shape[1]):
        win = a[:, t - n + 1 : t + 1]
        out[:, t] = np.where(np.isfinite(win).all(axis=1), win.mean(axis=1), np.nan)
    return out


# ----------------------------------------------------------------------------- per idea
def checks_ib02(P):
    p = P["main"]
    tsm = fe_signals.tsm_up_price(p.price)
    don = fe_signals.don_up(p.price)
    sma = fe_signals.sma_up(p.price, SMA_N)
    for arm, mw, of in (("A", 200, 3), ("B", 100, 3), ("C", 200, 4)):
        if arm == "B":  # 100 < SMA_N, so the median window is the only difference
            med = np.full_like(p.price, np.nan)
            med[:, mw - 1 :] = np.median(
                np.lib.stride_tricks.sliding_window_view(p.price, mw, axis=1), axis=-1)
        else:
            med = fe_signals.rolling_median_vol(p.price, mw)
        up = p.price > med
        if of == 4:
            vote = (sma.astype(int) + up.astype(int) + tsm.astype(int) + don.astype(int)) >= 3
        else:
            vote = (up.astype(int) + tsm.astype(int) + don.astype(int)) >= 2
        exp = fe_signals.sized_wants(*fe_signals.r2_state(p.price, vote,
                                                          fe_signals.early_components(p.price)),
                                     p.vol)
        w = call("IB02", arm, p)
        check(f"IB02/{arm} target = the vote's state machine (independently re-derived)",
              same(w, exp))
        check(f"IB02/{arm} vote differs from production's on some session",
              int((vote != fe_signals.e2_up(p.price)).sum()) > 0,
              f"{int((vote != fe_signals.e2_up(p.price)).sum())} sessions")
        check(f"IB02/{arm} applies to every window incl. the chunks and the ETF stage",
              all(isinstance(call("IB02", arm, P[k]), np.ndarray)
                  for k in ("oos:M2", "etf:XLK", "boot:main", "boot:M1")))


def checks_ib06(P):
    p = P["main"]
    high, low = hl_ratios(p)
    trc = own_trc(p, high, low)
    atr = own_roll_mean(trc, 20)
    comps = fe_signals.early_components(p.price)
    e2 = fe_signals.e2_up(p.price)
    prod_on = fe_signals.r2_state(p.price, e2, comps)[0]
    mod = api.load_idea("IB06")

    gate_a = trc > 1.5 * atr
    gate_b = ((np.lib.stride_tricks.sliding_window_view(high, 7, axis=1).max(axis=-1)
               - np.lib.stride_tricks.sliding_window_view(low, 7, axis=1).min(axis=-1))
              / p.price[:, 6:]) < 0.5 * atr[:, 6:]
    for arm, gate in (("A", gate_a), ("B", gate_b)):
        g = np.zeros_like(p.price, dtype=bool)
        g[:, (0 if arm == "A" else 6):] = gate if arm == "A" else gate
        on, factor = mod.early_state(p.price, e2, comps, gate=g)
        exp = fe_signals.sized_wants(on, factor, p.vol)
        check(f"IB06/{arm} target = the gated state machine (independently re-derived)",
              same(call("IB06", arm, p), exp))
        check(f"IB06/{arm} the gate only removes early entries",
              bool((prod_on | ~on).all()) and int((prod_on & ~on).sum()) > 0,
              f"{int((prod_on & ~on).sum())} gated-out on-sessions")
    peak = np.lib.stride_tricks.sliding_window_view(p.price, 20, axis=1).max(axis=-1)
    fire = np.zeros_like(p.price, dtype=bool)
    fire[:, 19:] = ((p.price[:, 19:] / peak - 1.0) < -2.5 * atr[:, 19:])
    exp = production_wants(p) * ~fire
    check("IB06/C target = production zeroed on the chandelier (independently re-derived)",
          same(call("IB06", "C", p), exp))
    check("IB06/C fires on some sessions", int(fire.sum()) > 0, f"{int(fire.sum())} sessions")
    check("IB06 TR >= the intraday range and > 0",
          bool((trc >= (high - low) / p.price - 1e-15).all()) and float(np.nanmin(trc)) > 0)
    check("IB06 applicable on main/M1-M5/ETF/chunks, refused on GSPC_1957_1999",
          all(isinstance(call("IB06", "A", P[k]), np.ndarray)
              for k in ("oos:M1", "oos:M2", "oos:M3", "oos:M4", "oos:M5", "etf:XLK", "boot:main",
                        "boot:M2"))
          and call("IB06", "A", P["oos:GSPC_1957_1999"])[0] == "n/a")


def checks_ib10(P):
    p = P["main"]
    high, low = hl_ratios(p)
    atr_ratio = own_roll_mean(own_trc(p, high, low), 20)
    sigma = 2.0 * 0.63 * float(np.sqrt(252.0)) * atr_ratio
    check("IB10 sigma_atr sits on the production vol input's scale (the gearing reading)",
          float(np.nanmean(sigma)) > 0.30 and float(np.nanmean(sigma)) < 0.80,
          f"mean sigma {np.nanmean(sigma):.3f}, mean data.vol {np.nanmean(p.vol):.3f}, "
          f"mean vt(production) {np.nanmean(fe_signals.vt_of(p.vol)):.3f}")
    sysm = np.where(sigma > 0, np.minimum(1.0, 0.35 / np.where(sigma > 0, sigma, 1.0)), 1.0)
    vt = {"A": sysm,
          "B": fe_signals.vt_of(0.5 * sigma + 0.5 * p.vol),
          "C": fe_signals.vt_of(np.maximum(sigma, p.vol))}
    check("IB10/A is not degenerate (mean vt below 1, i.e. it re-sizes)",
          float(np.nanmean(vt["A"])) < 0.999, f"mean vt {np.nanmean(vt['A']):.3f}")
    on, factor = fe_signals.r2_state(p.price, fe_signals.e2_up(p.price),
                                     fe_signals.early_components(p.price))
    for arm in ("A", "B", "C"):
        raw = on * factor * vt[arm]
        exp = np.where(raw > 0.98, 0.98, raw)
        check(f"IB10/{arm} target = production state machine with the ATR vt (re-derived)",
              same(call("IB10", arm, p), exp))
    check("IB10/C is not identical to production's sizing",
          not same(call("IB10", "C", p), production_wants(p), tol=0) and
          int((np.abs(vt["C"] - fe_signals.vt_of(p.vol)) > 1e-9).sum()) > 0,
          f"{int((np.abs(vt['C'] - fe_signals.vt_of(p.vol)) > 1e-9).sum())} sessions sized "
          "differently")
    check("IB10 applicable on main/M1-M5/ETF/chunks, refused on GSPC_1957_1999",
          all(isinstance(call("IB10", "C", P[k]), np.ndarray)
              for k in ("oos:M1", "oos:M2", "etf:XLK", "boot:main", "boot:M1"))
          and call("IB10", "C", P["oos:GSPC_1957_1999"])[0] == "n/a")


def month_lag_ref(s, mk):
    """Independent (loop) reference of the 1-month lag (row 0's month keys for comparison)."""
    row = mk[0]
    out = np.full_like(s, np.nan)
    for j in range(s.shape[1]):
        prev = [k for k in range(s.shape[1]) if row[k] <= row[j] - 1]
        if prev:
            out[:, j] = s[:, prev[-1]]
    return out


def checks_ib14(P):
    p = P["main"]
    mod = api.load_idea("IB14")
    s = np.array([[10.0, 10.0, 20.0, 20.0, 30.0, 30.0]])
    mk = np.array([[2021 * 12 + 1, 2021 * 12 + 1, 2021 * 12 + 2, 2021 * 12 + 2, 2021 * 12 + 3,
                    2021 * 12 + 3]])
    check("IB14 month_lag = the value in force one month earlier (two month blocks)",
          same(mod.month_lag(s, mk), np.array([[np.nan, np.nan, 10.0, 10.0, 20.0, 20.0]])),
          str(mod.month_lag(s, mk).tolist()))
    check("IB14 month_lag matches the loop reference on the real M2 calendar",
          same(mod.month_lag(_spread(P["oos:M2"]), _month_keys(P["oos:M2"])),
               month_lag_ref(_spread(P["oos:M2"]), _month_keys(P["oos:M2"]))))
    raw = p.extra("baa10y", ffill=True)
    chg = raw - np.concatenate([np.full((1, 60), np.nan), raw[:, :-60]], axis=1)
    stressed = np.where(np.isfinite(chg), chg > 0.30, False)
    day = np.asarray(p.days)
    i08 = (day >= "2008-10-01") & (day <= "2008-12-31")
    check("IB14 the 60-session daily change and its 2008 widening reproduce independently",
          int(stressed.sum()) > 0 and float(np.nanmax(chg[0][i08])) > 0.30,
          f"{int(stressed.sum())} veto sessions; max 2008H2 change "
          f"{np.nanmax(chg[0][i08]):.3f} pp")
    for arm, thr, mult in (("A", 0.30, None), ("B", 0.15, 0.5)):
        st = np.where(np.isfinite(chg), chg > thr, False)
        exp = production_wants(p) * (0.5 if mult else 1.0) if mult else production_wants(p) * ~st
        if mult:
            exp = production_wants(p) * np.where(st, mult, 1.0)
        check(f"IB14/{arm} action reproduces independently", same(call("IB14", arm, p), exp))
    credit = np.where(np.isfinite(chg), ~stressed, True)
    vote = (fe_signals.sma_up(p.price, SMA_N).astype(int) + fe_signals.tsm_up_price(p.price).astype(int)
            + fe_signals.don_up(p.price).astype(int) + credit.astype(int)) >= 3
    exp = fe_signals.sized_wants(*fe_signals.r2_state(p.price, vote,
                                                      fe_signals.early_components(p.price)), p.vol)
    check("IB14/C 3-of-4 vote reproduces independently", same(call("IB14", "C", p), exp))
    kinds = {k: mod.credit_series(P[k], {"lookback": 60})[1] for k in ("main", "oos:M2", "etf:XLK")}
    check("IB14 series selection: daily on main/ETF, monthly proxy on the US OOS windows",
          kinds["main"].startswith("daily") and kinds["etf:XLK"].startswith("daily")
          and kinds["oos:M2"].startswith("monthly"), str(kinds))
    check("IB14 applicable on M1/M2 and both chunk families, refused on M3-M5",
          all(isinstance(call("IB14", "A", P[k]), np.ndarray)
              for k in ("oos:M1", "oos:M2", "boot:main", "boot:M2", "etf:XLK"))
          and all(call("IB14", "A", P[k])[0] == "n/a" for k in ("oos:M3",) + ())
          and call("IB14", "A", P["oos:M4"])[0] == "n/a")


def _spread(p):
    """The monthly BAA-AAA spread of a window, forward-filled (the IB14 proxy series)."""
    return p.extra("baa_m", ffill=True) - p.extra("aaa_m", ffill=True)


def _month_keys(p):
    """Independent month index (year*12+month) per panel column."""
    if p.extra_index is not None:
        d = np.asarray(p.extra_days)[np.asarray(p.extra_index)].astype("datetime64[M]").astype(int)
    else:
        d = np.asarray(p.days).astype("datetime64[M]").astype(int)[None, :].repeat(p.P, 0)
    return (d // 12 + 1970) * 12 + (d % 12 + 1)


def checks_ib18(P):
    p = P["main"]
    day = np.asarray(p.days, dtype="datetime64[D]")
    # independent mask: python's calendar, week = Monday..Friday
    ref = np.zeros(day.size, dtype=bool)
    for i, d in enumerate(day.astype("O")):
        d = d if isinstance(d, date) else date.fromisoformat(str(d))
        mon = d - timedelta(days=d.weekday())
        fri = mon + timedelta(days=4)
        ref[i] = fri.month == d.month and 15 <= fri.day <= 21
    mod = api.load_idea("IB18")
    w = mod.expiration_week(mod.session_calendar(p))[0]
    check("IB18 expiration-week mask matches an independent calendar computation",
          bool(np.array_equal(w, ref)), f"{int(w.sum())} flagged sessions")
    n19 = int(w[day.astype("datetime64[Y]") == np.datetime64("2019", "Y")].sum())
    check("IB18 flags the 12 expiration weeks of a full year (60 sessions less holidays)",
          55 <= n19 <= 60, f"2019: {n19} sessions")
    for arm, fn in (("A", lambda m: np.where(m, 0.75, 1.0)),
                    ("B", lambda m: np.where(m & np.isin(
                        day.astype("datetime64[M]").astype(int) % 12 + 1, (3, 6, 9, 12)), 0.75, 1.0)),
                    ("C", lambda m: np.where(~m, 0.85, 1.0))):
        check(f"IB18/{arm} target = production x the calendar multiplier (re-derived)",
              same(call("IB18", arm, p), production_wants(p) * fn(w)))
    check("IB18 applicable in a chunk, where the mask comes from the drawn sessions",
          isinstance(call("IB18", "A", P["boot:main"]), np.ndarray)
          and isinstance(call("IB18", "A", P["boot:M2"]), np.ndarray)
          and int(call("IB18", "A", P["boot:main"]).shape[1]) == P["boot:main"].T)


def checks_ib22(P):
    p = P["main"]
    mod = api.load_idea("IB22")
    dax, ftse = p.extra("dax"), p.extra("ftse")
    eu = 0.5 * ((dax[:, 1:] / dax[:, :-1] - 1) + (ftse[:, 1:] / ftse[:, :-1] - 1))
    eu = np.concatenate([np.full((1, 1), np.nan), eu], axis=1)
    comps = fe_signals.early_components(p.price)
    e2 = fe_signals.e2_up(p.price)
    prod_on = fe_signals.r2_state(p.price, e2, comps)[0]
    gate = np.where(np.isfinite(eu), eu > 0, True)
    on, factor = mod.early_state(p.price, e2, comps, gate)
    check("IB22/A target = the gated state machine (independently re-derived)",
          same(call("IB22", "A", p), fe_signals.sized_wants(on, factor, p.vol)))
    check("IB22/A gates entries only and is neutral on missing sessions",
          bool((prod_on | ~on).all()) and int((prod_on & ~on).sum()) > 0
          and bool(gate[~np.isfinite(eu)].all()),
          f"{int((prod_on & ~on).sum())} gated-out on-sessions, "
          f"{int((~np.isfinite(eu)).sum())} neutral sessions")
    nik = p.extra("nikkei")
    nr = np.concatenate([np.full((1, 1), np.nan), nik[:, 1:] / nik[:, :-1] - 1], axis=1)
    val = eu + np.concatenate([np.full((1, 1), np.nan), nr[:, :-1]], axis=1)
    gate_b = np.where(np.isfinite(val), val > 0, True)
    onb, fb = mod.early_state(p.price, e2, comps, gate_b)
    check("IB22/B target = EU + the previous Nikkei session, gated (re-derived)",
          same(call("IB22", "B", p), fe_signals.sized_wants(onb, fb, p.vol)))
    cut = np.where(np.isfinite(np.concatenate([np.full((1, 1), np.nan), eu[:, :-1]], axis=1)),
                   np.concatenate([np.full((1, 1), np.nan), eu[:, :-1]], axis=1) < -0.02, False)
    check("IB22/C cuts exactly the session after a -2% European day",
          same(call("IB22", "C", p), production_wants(p) * np.where(cut, 0.75, 1.0))
          and int(cut.sum()) > 0, f"{int(cut.sum())} cut sessions")
    check("IB22 refuses the three declared US windows and applies on M3/M4/M5 + chunks",
          all(call("IB22", "A", P[k])[0] == "n/a"
              for k in ("oos:M1", "oos:M2", "oos:GSPC_1957_1999"))
          and all(isinstance(call("IB22", "A", P[k]), np.ndarray)
                  for k in ("oos:M3", "oos:M4", "oos:M5", "boot:M4", "etf:XLK")))


def checks_ib31(P):
    p = P["main"]
    mod = api.load_idea("IB31")
    comps = fe_signals.early_components(p.price)
    sma50_up, vol_p, med_p, _lag = comps
    e2 = fe_signals.e2_up(p.price)
    prod_on = fe_signals.r2_state(p.price, e2, comps)[0]
    spike = (
        np.isfinite(med_p) & (vol_p > 1.5 * med_p))
    spike2 = np.isfinite(med_p) & (vol_p > 2.0 * med_p)
    check("IB31 the exit sets are the bank's (1.5x / 2.0x / the union)",
          int(spike.sum()) > 0 and int(spike2.sum()) < int(spike.sum()),
          f"{int(spike.sum())} at 1.5x, {int(spike2.sum())} at 2.0x")
    for arm, ex in (("A", spike), ("B", spike2), ("C", (~sma50_up) | spike)):
        on, factor = mod.early_state(p.price, e2, comps, ex)
        check(f"IB31/{arm} target = the state machine with that exit (independently re-derived)",
              same(call("IB31", arm, p), fe_signals.sized_wants(on, factor, p.vol)))
    check("IB31 production exit reproduces fe_signals.r2_state bit for bit",
          same(mod.early_state(p.price, e2, comps, ~sma50_up)[0],
               fe_signals.r2_state(p.price, e2, comps)[0]))
    _on_a, f_a = mod.early_state(p.price, e2, comps, spike)
    _on_p, f_p = fe_signals.r2_state(p.price, e2, comps)
    early_a, early_p = int((f_a == 0.5).sum()), int((f_p == 0.5).sum())
    check("IB31/A holds the early leg far longer than production",
          early_a > 2 * early_p, f"{early_a} vs {early_p} early-leg sessions")
    check("IB31 applicable in the chunks",
          all(isinstance(call("IB31", "C", P[k]), np.ndarray) for k in ("boot:main", "boot:M2")))


def checks_ib35(P):
    p = P["main"]
    mod = api.load_idea("IB35")
    vol, T = p.vol, p.T
    for arm, lo_q, hi_q in (("A", 0.25, 0.75), ("B", 0.10, 0.90)):
        ref = np.full_like(vol, np.nan)
        updates = 0
        for t in range(T):
            lo = np.quantile(vol[0, max(0, t - 251) : t + 1], lo_q) if t >= 251 else np.nan
            hi = np.quantile(vol[0, max(0, t - 251) : t + 1], hi_q) if t >= 251 else np.nan
            v = vol[0, t]
            fresh = 1.0 if not (v > 0) else min(1.0, 0.35 / v)
            if t == 0:
                ref[0, t] = fresh
            elif not np.isfinite(lo) or v < lo or v > hi:
                ref[0, t] = fresh
                updates += 1
            else:
                ref[0, t] = ref[0, t - 1]
        got = mod.sticky_vt(vol, lo_q, hi_q, 252)
        check(f"IB35/{arm} sticky multiplier matches an independent loop reference",
              same(got, ref, tol=1e-12) or same(got, ref, tol=1e-9),
              f"max |diff| {np.nanmax(np.abs(got - ref)):.2e}")
        prod_changes = int((np.abs(np.diff(fe_signals.vt_of(vol), axis=1)) > 0).sum())
        got_changes = int((np.abs(np.diff(got, axis=1)) > 0).sum())
        check(f"IB35/{arm} re-sizes less often than production",
              bool(0 < got_changes <= updates) and got_changes < prod_changes,
              f"{got_changes} multiplier changes from {updates} updates vs production's "
              f"{prod_changes}")
        exp = production_wants(p).copy()
        on, factor = fe_signals.r2_state(p.price, fe_signals.e2_up(p.price),
                                        fe_signals.early_components(p.price))
        raw = on * factor * got
        exp = np.where(raw > 0.98, 0.98, raw)
        check(f"IB35/{arm} target = the state machine x the sticky multiplier (re-derived)",
              same(call("IB35", arm, p), exp))
    check("IB35/C is declared needs_kernel, not approximated",
          call("IB35", "C", p)[0] == "needs_kernel")
    check("IB35 applicable in the chunks",
          all(isinstance(call("IB35", "A", P[k]), np.ndarray) for k in ("boot:main", "boot:M2")))


def checks_ib26(P):
    for arm in ("A", "B", "C"):
        r = call("IB26", arm, P["main"])
        check(f"IB26/{arm} is registered as needs_kernel and never approximated",
              r[0] == "needs_kernel", r[1][:70])


def main(argv=None) -> int:
    P = panels()
    print("panels: " + ", ".join(P))
    for fn in (checks_ib02, checks_ib06, checks_ib10, checks_ib14, checks_ib18, checks_ib22,
               checks_ib31, checks_ib35, checks_ib26):
        print(fn.__name__)
        fn(P)
    print(f"\n{'FAIL' if FAILED else 'PASS'}: {len(FAILED)} failed check(s)"
          + (f" {FAILED}" if FAILED else ""))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
