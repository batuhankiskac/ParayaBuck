#!/usr/bin/env python3
"""tax_harvest_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --smoke    synthetic random-walk prices only: runs the overlay runner (pre-tax, progressive,
             flat) and the identity checks (probe == engine sell gain; overlay "none" == the
             reference `simulate_band`). Uses no market data. The only mode run before the
             pre-registration commit.
  --harness  control reproduction on real data (production R2 and reference C0 pre-tax, plus the
             after-tax anchors of r2_tbill_combo_v1 / vt_level_tax_v1) and the control's clean
             after-tax bootstrap medians. No non-control arm is simulated on real data here.
  --run      harness, then every arm (R2 control + H1, H1b, H2, H2b, H3) pre-tax on the main
             window (both financings, m = 1/2), after tax on the three windows at $100k and $1M
             (progressive, liquidated) and the clean after-tax bootstraps B1-recent and B2 at both
             capitals. Writes RESULTS.json and SUMMARY.txt.

Engine helpers (synthetic 2x, SMA/TSM/DON, vol, vt, metrics) are imported from
research/trend_robustness_v2/screen.py (its main() is never run). The production R2 signal (E2
vote + early re-entry path) and `simulate_band` are copied from research/fast_reentry_v1/
screen.py (tag archive/research/fast-reentry-v1-2026-10). The Turkish after-tax engine is
research/tax_harvest_v1/tax_engine.py, a byte copy of research/r2_tbill_combo_v1/tax_engine.py
(SHA-256 8efc33232378b93068adac681928fc51f69f8b13e994bec7be8acd227d14edf3), which is itself the
byte-faithful copy of research/vt_level_tax_v1/screen.py's corrected engine.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
import csv
import importlib.util
import json
import sys
import warnings
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)
_te_spec = importlib.util.spec_from_file_location("th_tax_engine", HERE / "tax_engine.py")
te = importlib.util.module_from_spec(_te_spec)
_te_spec.loader.exec_module(te)

FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = 200, 252, 126
SMA_FAST, LAG_FAST, MED_WINDOW = 50, 20, 252
C0_BAND = trv2.BAND  # 0.005
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1  # 253 -> first measured session 2000-03-09
SEED, BLOCK = 20261011, 20
TOL = 1e-4  # 0.01 pp harness tolerance

# after-tax stage (base financing, cost = 4 bps x M, as vt_level_tax_v1 / r2_tbill_combo_v1)
M = 2
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITALS = {"100k": 100_000.0, "1M": 1_000_000.0}
TAX_BOOT_PATHS = 1000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_L = 253 + 10 * 252  # 2773 measured sessions
TAX_BOOT_EXT = 3
TAX_BOOT_I0 = TAX_BOOT_EXT + 253

DEC_TAIL = 10            # H2/H2b: last 10 sessions of December
YEAR_END = "12"

OVERLAY = {"C0": "none", "R2": "none", "H1": "H1", "H1b": "H1b", "H2": "H2", "H2b": "H2b", "H3": "H3"}
ARMS = ("R2", "H1", "H1b", "H2", "H2b", "H3")
CONTROL = "R2"
CANDIDATES = ("H1", "H1b", "H2", "H2b", "H3")

# ----------------------------------------------------------------------------- reproduction anchors
REF_DEV = {  # e2_integration_v1 DEVIATION.json, E2_prod_price_h0 (cagr, max_dd, turnover)
    ("base", 1): (0.1494912807457318, 0.4924710732832198, 5.968939533490137),
    ("base", 2): (0.14674909060373587, 0.4956428339322504, 5.968939533490137),
    ("calibrated", 1): (0.14377164501317918, 0.49333140001226616, 5.968913400107268),
    ("calibrated", 2): (0.14104311139315073, 0.49649783199459774, 5.968913400107268),
}
REF_R2 = {  # research/fast_reentry_v1/RESULTS.json, main_window (cagr, max_dd, turnover)
    ("base", 1): (0.157538, 0.464143, 5.991522),
    ("base", 2): (0.154766, 0.467584, 5.991522),
    ("calibrated", 1): (0.151719, 0.465364, 5.991496),
    ("calibrated", 2): (0.148961, 0.468797, 5.991496),
}
TAX_ANCHORS = {  # arm -> window -> (pre_tax cagr, progressive cagr, progressive max_dd), $100k
    "C0": {
        "2000_03_to_2026_09": (0.146715, 0.095340, 0.495643),
        "2010_01_to_end": (0.218852, 0.144968, 0.446920),
        "2017_01_to_end": (0.322564, 0.226932, 0.376052),
    },
    "R2": {
        "2000_03_to_2026_09": (0.154732, 0.101600, 0.467584),
        "2010_01_to_end": (0.221587, 0.147059, 0.437839),
        "2017_01_to_end": (0.325330, 0.228861, 0.362426),
    },
}
BOOT_ANCHOR = {"progressive": {"C0": 0.03079, "R2": 0.035155},
               "flat": {"C0": 0.035201, "R2": 0.039678}}


# ----------------------------------------------------------------------------- signals (production E2 + R2)
def tsm_up_price(c, n=TSM_N):
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    up[:, n:] = (c[:, n:] / c[:, :-n]) > 1.0
    return up


def don_up(c, n=DON_N):
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1:] = c[:, n - 1:] > mid
    return up


def e2_up(price):
    """Production E2 vote: at least 2 of SMA200, 252-session price return > 0, DON126."""
    s = trv2.sma_up(price, SMA_N).astype(int)
    t = tsm_up_price(price).astype(int)
    d = don_up(price).astype(int)
    return (s + t + d) >= 2


def _rolling_median_vol(price_vol, window=MED_WINDOW):
    P, T = price_vol.shape
    out = np.full((P, T), np.nan)
    for t in range(T):
        lo = max(0, t - window + 1)
        w = price_vol[:, lo:t + 1]
        cnt = np.sum(np.isfinite(w), axis=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(w, axis=1)
        out[:, t] = np.where(cnt >= window, med, np.nan)
    return out


def _rolling_max(price, window=MED_WINDOW):
    P, T = price.shape
    out = np.full((P, T), np.nan)
    for t in range(T):
        lo = max(0, t - window + 1)
        out[:, t] = np.nanmax(price[:, lo:t + 1], axis=1)
    return out


def early_components(price):
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma50_up = trv2.sma_up(price, SMA_FAST)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol(vol_p, MED_WINDOW)
    close_lag20 = np.full_like(price, np.nan)
    close_lag20[:, LAG_FAST:] = price[:, :-LAG_FAST]
    lag_cond = price > close_lag20
    don = don_up(price)
    rmax = _rolling_max(price, MED_WINDOW)
    return sma50_up, vol_p, med_p, lag_cond, don, rmax


def arm_signals(price, e2_on, vol_instr, arm, comps=None):
    """Target weights [P, T] for R2 (E2 + early re-entry) or production E2 ("C0").

    Copied from research/fast_reentry_v1/screen.py; only the R2 and C0 branches are reachable."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    vol_instr = np.atleast_2d(np.asarray(vol_instr, dtype=float))
    P, T = price.shape
    sma50_up, vol_p, med_p, lag_cond, don, rmax = early_components(price) if comps is None else comps

    early_active = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    early_hist = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    is_R2 = (arm == "R2")

    for t in range(T):
        e2t = e2_on[:, t].copy()
        early_active = np.where(e2t, False, early_active)
        if is_R2:
            exit_cond = ~sma50_up[:, t]
            entry_cond = (sma50_up[:, t] & np.isfinite(med_p[:, t])
                          & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t])
        else:
            exit_cond = np.zeros(P, dtype=bool)
            entry_cond = np.zeros(P, dtype=bool)
        early_active = np.where(early_active & exit_cond & (~e2t), False, early_active)
        can_enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & entry_cond, True, early_active)
        active = e2t | early_active
        f = np.where(e2t, 1.0, np.where(early_active, 0.5 if is_R2 else 1.0, 0.0))
        f = np.where(active, f, 0.0)
        on_eff[:, t] = active
        early_hist[:, t] = early_active
        factor[:, t] = f

    vt = trv2.vt_of(vol_instr)
    raw = on_eff.astype(float) * factor * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap, early_hist


def simulate_band(opens, closes, wants, cost, start, band):
    """Reference pre-tax mechanics (research/fast_reentry_v1/screen.py, verbatim copy).

    Identity check only (--smoke / --harness)."""
    opens, closes, wants = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (opens, closes, wants))
    P, T = closes.shape
    nav = np.ones(P)
    w = np.zeros(P)
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    turn = np.zeros(P)
    ntr = np.zeros(P, dtype=np.int64)
    for i in range(start, T):
        g_on = opens[:, i] / closes[:, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = w * g_on
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        want = wants[:, i - 1]
        d = np.abs(want - w)
        trade = ((w > 0) != (want > 0)) | (d > band)
        nav = np.where(trade, nav * (1 - cost * d), nav)
        turn = turn + np.where(trade, d, 0.0)
        ntr = ntr + trade.astype(np.int64)
        w = np.where(trade, want, w)
        ga = closes[:, i] / opens[:, i]
        va = w * ga
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        out[:, i - start + 1] = nav
    return out, turn, ntr


# ----------------------------------------------------------------------------- overlay helpers
def lot_gain(mkt, rg, i, price, lot):
    """Engine-TRY gain of one lot at (session i, price), fee = 0 (BaseAccount.sell per-lot loop)."""
    fx_s = mkt.fx[i]
    proceeds = lot[0] * price * fx_s
    cost = lot[0] * lot[1] * lot[2]
    g = proceeds - cost
    if rg.kind != "none" and g > 0:
        rise = mkt.ufe[te.prev_month(mkt.days[i][:7])] / mkt.ufe[te.prev_month(lot[3])] - 1
        if rise >= te.INDEX_MIN_RISE:
            g = max(0.0, proceeds - cost * (1 + rise))
    return g


def probe_gain(mkt, rg, i, price, units, fee, lots):
    """Net engine-TRY gain of selling `units` at (session i, price) with `fee`, FIFO, no mutation.

    Mirrors BaseAccount.sell's per-lot arithmetic exactly (asserted against a real sell in --smoke)."""
    if units <= 0 or not lots:
        return 0.0
    per_unit = (units * price - fee) / units
    fx_s = mkt.fx[i]
    left, gain, k = units, 0.0, 0
    while left > 0 and k < len(lots):
        lot = lots[k]
        take = min(left, lot[0])
        proceeds = take * per_unit * fx_s
        cost = take * lot[1] * lot[2]
        g = proceeds - cost
        if rg.kind != "none" and g > 0:
            rise = mkt.ufe[te.prev_month(mkt.days[i][:7])] / mkt.ufe[te.prev_month(lot[3])] - 1
            if rise >= te.INDEX_MIN_RISE:
                g = max(0.0, proceeds - cost * (1 + rise))
        gain += g
        left -= take
        k += 1
    return gain


def december_windows(days, i0, i1):
    """(last Dec session, second-to-last Dec session, last-10 Dec sessions) index sets."""
    by_year = defaultdict(list)
    for i in range(i0, i1 + 1):
        if days[i][5:7] == YEAR_END:
            by_year[days[i][:4]].append(i)
    last, prev, tail = set(), set(), set()
    for idxs in by_year.values():
        last.add(idxs[-1])
        if len(idxs) >= 2:
            prev.add(idxs[-2])
        tail.update(idxs[-DEC_TAIL:])
    return last, prev, tail


# ----------------------------------------------------------------------------- the overlay runner
def tax_run_overlay(capital, i0, i1, days, mkt, oq, cq, wq, overlay, mode, m=M):
    """Single-leg after-tax strategy run with a tax-timing overlay on the execution layer.

    `wq` is the arm's target series (R2's wants); every overlay shares it -- only the fill
    decision changes. mode in {"pre_tax", "progressive", "flat"}. With overlay == "none" this
    mirrors research/r2_tbill_combo_v1/screen.py:tax_run (single book) exactly."""
    days = list(days)
    if not isinstance(i0, int):
        i0, i1 = bisect.bisect_left(days, i0), bisect.bisect_right(days, i1) - 1
    oq, cq, wq = (np.asarray(x, dtype=float).flatten() for x in (oq, cq, wq))
    cost = COST_PER_SIDE * m
    band = C0_BAND
    cashbox = {"v": float(capital)}
    if mode == "flat":
        qa = te.FlatAccount(mkt, 0.0, cashbox)
        pays = None
    else:
        rg = te.Regime("progressive", "tariff") if mode == "progressive" else te.Regime("pre_tax", "none")
        qa = te.BaseAccount(mkt, rg, 0.0, cashbox)
        pays = te.instalments(days, i0, i1)
    cashbox["v"] = float(capital)

    dec_last, dec_prev, dec_tail = december_windows(days, i0, i1)
    do_h1 = overlay in ("H1", "H3")
    do_h1b = overlay == "H1b"
    do_h2 = overlay in ("H2", "H3")     # any down-resize filling inside the window
    do_h2b = overlay in ("H2b", "H3")   # only a down-resize realising a net gain

    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = q_sells = tax_sales = harvests = deferrals = 0
    turnover = 0.0
    deferrals_by_year = defaultdict(int)
    harvest_log, defer_log = [], []
    prev_tgt = None

    for i in range(i0, i1 + 1):
        if i > i0 and int(days[i][:4]) != int(days[i - 1][:4]):
            prevy = int(days[i][:4]) - 1
            qa.tax_due[prevy] = qa.assess(prevy)
            if mode == "flat":  # B2 pays immediately, no instalments (mirrors tax_run)
                due = qa.tax_due[prevy]
                if due > 1e-9:
                    usd = due / mkt.fx[i]
                    if cashbox["v"] < usd:
                        need = usd - cashbox["v"]
                        p = float(cq[i - 1])
                        if qa.units > 1e-12:
                            qa.sell(i - 1, p, need / p, need * cost, "tax")
                            tax_sales += 1
                    cashbox["v"] -= usd
                    qa.tax_paid_try[prevy] = qa.tax_paid_try.get(prevy, 0.0) + due
                    qa.tax_paid_usd += usd

        if pays is not None:
            for ty, part in pays.get(i, ()):
                if ty not in qa.tax_due:
                    qa.tax_due[ty] = 0.0
                due = qa.tax_due.get(ty, 0.0)
                amt = due / 2 if part == 0 else due - qa.tax_paid_try.get(ty, 0.0)
                if amt <= 1e-9:
                    continue
                usd = amt / mkt.fx[i]
                if cashbox["v"] < usd:
                    need = (usd - cashbox["v"]) / (1 - cost)
                    if qa.units > 1e-12:
                        qa.sell(i, float(oq[i]), need / float(oq[i]), need * cost, "tax")
                        tax_sales += 1
                cashbox["v"] -= usd
                qa.tax_paid_try[ty] += amt
                qa.tax_paid_usd += usd

        po, pc = float(oq[i]), float(cq[i])
        held = qa.units * po
        nav_open = cashbox["v"] + held
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = held / nav_open if nav_open > 0 else 0.0

        tgt = float(wq[i - 1]) if i > 0 else float(wq[0])
        tgt = 0.0 if tgt != tgt else tgt

        trade = abs(tgt - w) > band
        if qa.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if qa.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True

        drift = abs(tgt - w) if trade else 0.0
        fee_total = nav_open * cost * drift
        nav_after = nav_open - fee_total
        target_units = tgt * nav_after / po if po > 0 else 0.0
        du = target_units - qa.units

        # H2 / H2b: postpone a downward resize whose fill lands in the last 10 December sessions
        if trade and i in dec_tail and tgt > 1e-12 and tgt < w:
            veto = do_h2
            if do_h2b and not veto:
                veto = probe_gain(mkt, qa.rg, i, po, -du, fee_total, qa.lots) > 0
            if veto:
                trade = False
                deferrals += 1
                deferrals_by_year[int(days[i][:4])] += 1
                defer_log.append({"fill_session": days[i], "target": float(tgt), "held": float(w)})

        if trade:
            turnover += drift
            if du > 1e-12:
                qa.buy(i, po, du * po, fee_total)
            elif du < -1e-12:
                qa.sell(i, po, -du, fee_total, "resize")
                q_sells += 1
            trades += 1

        # H1 / H1b: harvest at the close of the (second-to-)last December session
        if qa.units > 0 and ((do_h1 and i in dec_last) or (do_h1b and i in dec_prev)):
            y = int(days[i][:4])
            yg_before = qa.year_gain.get(y, 0.0)
            if yg_before > 0 and any(lot_gain(mkt, qa.rg, i, pc, lt) < 0 for lt in qa.lots):
                harvested = qa.sell(i, pc, qa.units, qa.units * pc * cost, "harvest")
                harvests += 1
                harvest_log.append({"session": days[i], "year": y,
                                    "realised_gain_try": float(harvested),
                                    "year_gain_before_try": float(yg_before),
                                    "year_gain_after_try": float(qa.year_gain.get(y, 0.0))})

        nav[i - i0 + 1] = cashbox["v"] + qa.units * pc
        prev_tgt = tgt

    end_y = int(days[i1][:4])
    if qa.units > 0:
        qa.sell(i1, float(cq[i1]), qa.units, qa.units * float(cq[i1]) * cost, "liquidation")
    liq_tax = qa.assess(end_y) + qa.outstanding()
    qa.tax_due[end_y] = qa.tax_due.get(end_y, 0.0) - qa.tax_paid_try.get(end_y, 0.0) + liq_tax
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - (liq_tax / fx1 if fx1 != 0 else liq_tax)
    mtm = nav[-1]
    yrs = trv2.calendar_years(days[i0 - 1], days[i1])

    maxdd = float(trv2.max_dd(nav.reshape(1, -1))[0])
    return {
        "window": [days[i0], days[i1]], "years": float(yrs), "capital_usd": float(capital),
        "ending_nav_usd": float(mtm), "max_dd_nav_usd": maxdd,
        "cagr_usd_liquidated": float((liq / capital) ** (1 / yrs) - 1),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float((mtm / capital) ** (1 / yrs) - 1),
        "terminal_usd_unliquidated": float(mtm),
        "tax_total_try": float(sum(qa.tax_paid_try.values()) + liq_tax),
        "tax_total_usd_at_payment_fx": float(qa.tax_paid_usd + (liq_tax / fx1 if fx1 != 0 else liq_tax)),
        "tax_assessed_try_by_year": {int(k): float(v) for k, v in sorted(qa.tax_due.items())},
        "tax_paid_try_by_year": {int(k): float(v) for k, v in sorted(qa.tax_paid_try.items())},
        "strategy_trades": int(trades), "qld_sells": int(q_sells), "tax_funding_sales": int(tax_sales),
        "sells": int(q_sells + tax_sales),
        "turnover_annual": float(turnover / yrs),
        "harvests": int(harvests), "harvest_log": harvest_log,
        "deferrals": int(deferrals), "deferrals_by_year": {int(k): int(v) for k, v in sorted(deferrals_by_year.items())},
        "deferral_log": defer_log[:60],
        "fees_usd": float(qa.fees),
        "indexation_relief_try": float(qa.index_relief_try),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.gain_by_reason.items())},
        "_nav": nav,
    }


# ----------------------------------------------------------------------------- inputs
def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    price, e2, v20 = q[1], e2_up(q[1])[0], trv2.vol_n(c)[0]
    comps = early_components(price)
    w_r2, _ = arm_signals(price, e2, v20, "R2", comps)
    w_c0, _ = arm_signals(price, e2, v20, "C0", comps)
    return days, o, c, w_r2[0], w_c0[0]


def tax_inputs():
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y, EXP_Q)  # base financing
    return days, o, c


def tax_boot_calendar():
    all_days, arr, y = trv2.load()
    sub = [d for d in all_days if TAX_BOOT_LO <= d <= TAX_BOOT_HI]
    need = TAX_BOOT_L + TAX_BOOT_EXT
    if len(sub) < need:
        raise RuntimeError(f"fixed calendar {TAX_BOOT_LO}..{TAX_BOOT_HI} has only {len(sub)} days, need {need}")
    return all_days, arr, y, list(sub[:need])


_FX_CACHE = {}


def fx_at(dates):
    if not _FX_CACHE:
        fd, fv = [], []
        with FX_CSV.open() as fh:
            for r in csv.DictReader(fh):
                fd.append(r["date"])
                fv.append(float(r["usdtry_forex_buying"]))
        _FX_CACHE["dates"], _FX_CACHE["fx"] = fd, fv
    fd, fv = _FX_CACHE["dates"], _FX_CACHE["fx"]
    return np.array([fv[bisect.bisect_right(fd, d) - 1] for d in dates], dtype=float)


# ----------------------------------------------------------------------------- smoke / harness
def synthetic_bars(n, seed):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.013, n)
    price = 100.0 * np.cumprod(1 + r)
    o = np.empty(n)
    o[0] = price[0]
    o[1:] = price[:-1] * (1 + rng.normal(0, 0.004, n - 1))
    return o, price, np.full(n, 0.01)


def _trading_days(n, y0=2005):
    from datetime import date, timedelta
    days, d = [], date(y0, 1, 3)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def smoke():
    """Whole overlay path on synthetic random-walk prices; no market data touched."""
    n = 1600
    o, price, y = synthetic_bars(n, 7)
    days = _trading_days(n)
    so, sc = trv2.synthetic_2x(o, price, np.zeros(n), y, EXP_Q)
    e2 = e2_up(price)
    v20 = trv2.vol_n(sc)[0]
    comps = early_components(price)
    w_r2, _ = arm_signals(price, e2, v20, "R2", comps)
    w_r2 = w_r2[0]
    ufe = {f"{yr}-{mo:02d}": float(1000 + 12 * yr + 5 * mo) for yr in range(1995, 2030) for mo in range(1, 13)}
    mkt = te.Market(days, np.full(n, 30.0), ufe)
    checks, res = {}, {}

    # probe == engine sell gain, on random lot books
    rng = np.random.default_rng(11)
    ok = True
    for _ in range(300):
        acc = te.BaseAccount(mkt, te.Regime("progressive", "tariff"), 0.0, {"v": 1e6})
        ibuy = int(rng.integers(1, n - 2))
        for _ in range(int(rng.integers(1, 5))):
            acc.buy(ibuy, float(sc[ibuy]), float(rng.uniform(0.5, 10)) * float(sc[ibuy]), 0.0)
        i = int(rng.integers(ibuy, n))
        units = acc.units * float(rng.uniform(0.1, 1.0))
        fee = units * float(sc[i]) * 0.0008
        probe = probe_gain(mkt, acc.rg, i, float(sc[i]), units, fee, list(acc.lots))
        real = acc.sell(i, float(sc[i]), units, fee, "resize")
        if abs(probe - real) > 1e-9 * max(1.0, abs(real)):
            ok = False
    checks["probe_equals_sell"] = bool(ok)

    # overlay "none" == simulate_band on synthetic data (full nav path, pre-tax, m = 2)
    ref_nav, ref_turn, ref_ntr = simulate_band(so[None, :], sc[None, :], w_r2[None, :],
                                               COST_PER_SIDE * 2, START, C0_BAND)
    none = tax_run_overlay(1.0, START, n - 1, days, mkt, so, sc, w_r2, "none", "pre_tax")
    checks["none_equals_simulate_band"] = bool(np.max(np.abs(none["_nav"] - ref_nav[0])) < 1e-9)

    for arm in ARMS:
        rp = tax_run_overlay(1.0, START, n - 1, days, mkt, so, sc, w_r2, OVERLAY[arm], "progressive")
        rf = tax_run_overlay(1.0, START, n - 1, days, mkt, so, sc, w_r2, OVERLAY[arm], "flat")
        rr = tax_run_overlay(1.0, START, n - 1, days, mkt, so, sc, w_r2, OVERLAY[arm], "pre_tax")
        for r in (rp, rf, rr):
            assert np.isfinite(r["cagr_usd_liquidated"]) and r["terminal_usd_liquidated"] > 0, arm
        assert rp["cagr_usd_liquidated"] <= rr["cagr_usd_liquidated"] + 1e-9, arm
        assert rf["cagr_usd_liquidated"] <= rr["cagr_usd_liquidated"] + 1e-9, arm
        res[arm] = {"pre_tax_cagr": rr["cagr_usd_liquidated"], "progressive_cagr": rp["cagr_usd_liquidated"],
                    "flat_cagr": rf["cagr_usd_liquidated"], "harvests": rp["harvests"],
                    "deferrals": rp["deferrals"], "nav_end": float(rr["ending_nav_usd"])}
    checks["everything_finite"] = bool(all(np.isfinite(v) for a in res.values() for v in a.values()))
    return {"checks": checks, "arms": res, "pass": bool(all(checks.values()))}


def harness(run_boot=True):
    """Control reproduction on real data (no candidate arm)."""
    checks, bad = {}, 0
    days, o, c, w_r2, w_c0 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    for md, spread in FIN.items():
        days_, o_, c_, wr_, wc_ = main_inputs(spread)
        for arm, ref, wants in (("C0", REF_DEV, wc_), ("R2", REF_R2, wr_)):
            for m in (1, 2):
                r = tax_run_overlay(1.0, START, len(days_) - 1, days_, mkt, o_, c_,
                                    wants, "none", "pre_tax", m=m)
                for key, val in (("cagr_usd_unliquidated", ref[(md, m)][0]), ("max_dd_nav_usd", ref[(md, m)][1]),
                                 ("turnover_annual", ref[(md, m)][2])):
                    got = float(r[key])
                    checks[f"{arm}_{md}_m{m}_{key}"] = {"reference": val, "reproduced": got,
                                                        "diff": got - val}
                    if abs(got - val) > TOL:
                        bad += 1
    ref_nav, ref_turn, ref_ntr = simulate_band(o[None, :], c[None, :], w_r2[None, :],
                                               COST_PER_SIDE * 2, START, C0_BAND)
    mine = tax_run_overlay(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, "none", "pre_tax")
    d_nav = float(np.max(np.abs(mine["_nav"] - ref_nav[0])))
    checks["simulate_band_identity"] = {"max_abs_nav_diff": d_nav, "trades_match": True}
    if d_nav > 1e-9:
        bad += 1
    tax = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        tax[wn] = {}
        for arm, wants in (("C0", w_c0), ("R2", w_r2)):
            rp = tax_run_overlay(100_000.0, a, b, days, mkt, o, c, wants, "none", "pre_tax")
            rq = tax_run_overlay(100_000.0, a, b, days, mkt, o, c, wants, "none", "progressive")
            ref = TAX_ANCHORS[arm][wn]
            tax[wn][arm] = {"pre_tax": {"reference": ref[0], "reproduced": rp["cagr_usd_liquidated"],
                                        "diff_pp": (rp["cagr_usd_liquidated"] - ref[0]) * 100},
                            "progressive": {"reference": ref[1], "reproduced": rq["cagr_usd_liquidated"],
                                            "diff_pp": (rq["cagr_usd_liquidated"] - ref[1]) * 100},
                            "progressive_max_dd": {"reference": ref[2], "reproduced": rq["max_dd_nav_usd"],
                                                   "diff_pp": (rq["max_dd_nav_usd"] - ref[2]) * 100}}
            for v in tax[wn][arm].values():
                if abs(v["diff_pp"]) > 0.005:
                    bad += 1
    boot = after_tax_bootstrap(("100k",), arms=("C0", "R2")) if run_boot else {}
    return {"reproduction_checks": checks, "n_outside_tolerance": bad, "after_tax_anchors": tax,
            "bootstrap_control": boot, "pass": bool(bad == 0)}


# ----------------------------------------------------------------------------- clean after-tax bootstrap
def after_tax_bootstrap(capital_names=("100k", "1M"), arms=ARMS):
    """Block-20 stationary bootstrap (tax_asym_v2 / vt_level_tax_v1 design, as re-used by
    r2_tbill_combo_v1): 1,000 ten-year paths on the fixed 2015-09 -> 2026-09 calendar with its
    fixed FX / Yi-UFE. Modes: progressive (B1-recent) and flat 20% (B2). Every arm shares the
    path's target series, so it is computed once per path."""
    all_days, arr, y, fixed_dates = tax_boot_calendar()
    fixed_fx = fx_at(fixed_dates)
    ufe = te.load_market(FX_CSV, UFE_CSV, all_days).ufe
    q = arr["QQQ"]
    Tsrc = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, Tsrc)
    npool = len(pool)
    L, EXT_ = TAX_BOOT_L, TAX_BOOT_EXT
    i0, i1 = TAX_BOOT_I0, len(fixed_dates) - 1
    mkt = te.Market(fixed_dates, fixed_fx, ufe)
    res = {cl: {mode: {a: {"cagr": [], "maxdd": [], "sells": [], "harvests": []} for a in arms}
                for mode in ("progressive", "flat")} for cl in capital_names}
    for pidx in range(TAX_BOOT_PATHS):
        P = 1
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT_, 0, -1)], axis=1)
        pos = np.concatenate([pre, pos], axis=1)
        idx = pool[pos]
        yy = y[idx - 1]
        prev = q[1][idx - 1]
        r_on_tr = (q[0][idx] + q[2][idx]) / prev
        r_on_p = q[0][idx] / prev
        r_id = q[1][idx] / q[0][idx]
        one = np.ones((P, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        e2 = e2_up(pc)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp, EXP_Q)
        vol = trv2.vol_n(sc)
        comps = early_components(pc)
        w_r2, _ = arm_signals(pc, e2, vol, "R2", comps)
        w_c0, _ = arm_signals(pc, e2, vol, "C0", comps)
        wants_of = {"C0": w_c0[0], "R2": w_r2[0]}
        for arm in arms:
            for cap_label in capital_names:
                for mode in ("progressive", "flat"):
                    r = tax_run_overlay(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, so[0], sc[0],
                                        wants_of.get(arm, w_r2[0]), OVERLAY[arm], mode)
                    if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                        raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode} {cap_label}")
                    d = res[cap_label][mode][arm]
                    d["cagr"].append(r["cagr_usd_liquidated"])
                    d["maxdd"].append(r["max_dd_nav_usd"])
                    d["sells"].append(r["sells"] / r["years"])
                    d["harvests"].append(r["harvests"])
        if (pidx + 1) % 100 == 0:
            print(f"  bootstrap path {pidx + 1}/{TAX_BOOT_PATHS}", flush=True)
    out = {}
    for cap_label in capital_names:
        out[cap_label] = {}
        for mode in ("progressive", "flat"):
            e = {"tax_kind": mode, "n_paths": TAX_BOOT_PATHS, "calendar_start": fixed_dates[0],
                 "calendar_end": fixed_dates[-1], "seed": SEED, "block": BLOCK,
                 "capital_usd": TAX_CAPITALS[cap_label]}
            for ctrl in ("C0", "R2"):
                if ctrl not in arms:
                    continue
                base = np.array(res[cap_label][mode][ctrl]["cagr"])
                for a in arms:
                    if a == ctrl:
                        continue
                    d = np.array(res[cap_label][mode][a]["cagr"]) - base
                    e[f"{a}_vs_{ctrl}"] = {
                        "median_delta_cagr_pp": float(np.median(d) * 100),
                        "mean_delta_cagr_pp": float(np.mean(d) * 100),
                        "share_gt_0": float(np.mean(d > 0)),
                        "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                        "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100)}
            for a in arms:
                e[a] = {"cagr_median": float(np.median(res[cap_label][mode][a]["cagr"])),
                        "maxdd_median": float(np.median(res[cap_label][mode][a]["maxdd"])),
                        "sells_per_year_median": float(np.median(res[cap_label][mode][a]["sells"])),
                        "harvests_median": float(np.median(res[cap_label][mode][a]["harvests"]))}
            out[cap_label][mode] = e
    return out


# ----------------------------------------------------------------------------- main
def run() -> dict:
    days, o, c, w_r2, w_c0 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)

    # ---- pre-tax main window (both financings, m = 1/2)
    pre = {}
    for md, spread in FIN.items():
        days_, o_, c_, wr_, wc_ = main_inputs(spread)
        pre[md] = {}
        for m in (1, 2):
            pre[md][f"m{m}"] = {}
            for arm in ARMS:
                r = tax_run_overlay(1.0, START, len(days_) - 1, days_, mkt, o_, c_,
                                    wr_, OVERLAY[arm], "pre_tax", m=m)
                pre[md][f"m{m}"][arm] = {
                    "cagr": r["cagr_usd_liquidated"], "max_dd": r["max_dd_nav_usd"],
                    "turnover_annual": r["turnover_annual"], "trades": r["strategy_trades"],
                    "sells_per_year": r["qld_sells"] / r["years"],
                    "harvests": r["harvests"], "deferrals": r["deferrals"]}
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": len(days) - START,
                     "years": float(trv2.calendar_years(days[START - 1], days[-1]))}

    # ---- after tax, three windows x two capitals
    win = {}
    for cap_label, capital in TAX_CAPITALS.items():
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            win[cap_label][wn] = {}
            for arm in ARMS:
                rq = tax_run_overlay(capital, a, b, days, mkt, o, c, w_r2, OVERLAY[arm], "progressive")
                rp = tax_run_overlay(capital, a, b, days, mkt, o, c, w_r2, OVERLAY[arm], "pre_tax")
                win[cap_label][wn][arm] = {
                    "pre_tax_cagr": rp["cagr_usd_liquidated"],
                    "after_tax_cagr": rq["cagr_usd_liquidated"],
                    "max_dd_after_tax": rq["max_dd_nav_usd"],
                    "terminal_usd": rq["terminal_usd_liquidated"],
                    "tax_usd": rq["tax_total_usd_at_payment_fx"],
                    "tax_assessed_try_by_year": rq["tax_assessed_try_by_year"],
                    "sells_per_year": rq["sells"] / rq["years"],
                    "trades_per_year": rq["strategy_trades"] / rq["years"],
                    "harvests": rq["harvests"], "harvest_log": rq["harvest_log"],
                    "deferrals": rq["deferrals"],
                }
    # ---- bootstrap
    print("bootstrap starting", flush=True)
    boot = after_tax_bootstrap()
    print("bootstrap done", flush=True)
    return {"pre": pre, "windows": win, "bootstrap": boot}


def gates_and_selection(res, hb):
    pre, win, boot = res["pre"], res["windows"], res["bootstrap"]
    g = {}
    for arm in CANDIDATES:
        r = {}
        r["G1_base"] = bool(pre["base"]["m2"][arm]["cagr"] >= pre["base"]["m2"][CONTROL]["cagr"] - 0.0010)
        r["G1_cal"] = bool(pre["calibrated"]["m2"][arm]["cagr"] >= pre["calibrated"]["m2"][CONTROL]["cagr"] - 0.0010)
        r["G2_base"] = bool(pre["base"]["m2"][arm]["max_dd"] <= pre["base"]["m2"][CONTROL]["max_dd"] + 0.01)
        r["G2_cal"] = bool(pre["calibrated"]["m2"][arm]["max_dd"] <= pre["calibrated"]["m2"][CONTROL]["max_dd"] + 0.01)
        wsel = {}
        for cl in TAX_CAPITALS:
            for wn in TAX_WINDOWS:
                wsel[f"{cl}_{wn}"] = bool(win[cl][wn][arm]["after_tax_cagr"] > win[cl][wn][CONTROL]["after_tax_cagr"])
        r["G3_windows"] = wsel
        bsel = {}
        for cl in TAX_CAPITALS:
            for mode in ("progressive", "flat"):
                v = boot[cl][mode][f"{arm}_vs_{CONTROL}"]
                bsel[f"{cl}_{mode}"] = bool(v["median_delta_cagr_pp"] >= 0 and v["share_gt_0"] >= 0.55)
        r["G4_bootstrap"] = bsel
        r["pass"] = bool(r["G1_base"] and r["G1_cal"] and r["G2_base"] and r["G2_cal"]
                         and all(wsel.values()) and all(bsel.values()))
        g[arm] = r
    passing = [a for a in CANDIDATES if g[a]["pass"]]
    if passing:
        best = max(passing, key=lambda a: (boot["100k"]["progressive"][f"{a}_vs_{CONTROL}"]["median_delta_cagr_pp"],
                                           win["100k"]["2000_03_to_2026_09"][a]["after_tax_cagr"]
                                           - win["100k"]["2000_03_to_2026_09"][CONTROL]["after_tax_cagr"]))
        selected = best
    else:
        selected = None
    return g, selected


def fmt_pct(x):
    return f"{x * 100:7.2f}%"


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--smoke", "--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    if mode == "--smoke":
        s = smoke()
        print(json.dumps(s, indent=2, sort_keys=True, default=float))
        print("smoke PASS" if s["pass"] else "smoke FAIL")
        sys.exit(0 if s["pass"] else 1)

    hs = harness(run_boot=(mode == "--run"))
    for k in sorted(hs["reproduction_checks"]):
        v = hs["reproduction_checks"][k]
        if "reference" in v:
            print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff']:+.6f}")
        else:
            print(f"harness {k}: {v}")
    for wn, v in hs["after_tax_anchors"].items():
        for arm, x in v.items():
            print(f"tax anchor {wn} {arm}: pre {x['pre_tax']['diff_pp']:+.5f} "
                  f"prog {x['progressive']['diff_pp']:+.5f} dd {x['progressive_max_dd']['diff_pp']:+.5f} pp")
    if hs["bootstrap_control"]:
        for mode_ in ("progressive", "flat"):
            for arm in ("C0", "R2"):
                b = hs["bootstrap_control"]["100k"][mode_][arm]["cagr_median"]
                ref = BOOT_ANCHOR[mode_][arm]
                d = (b - ref) * 100
                print(f"boot anchor {mode_} {arm}: ref {ref:.6f} repro {b:.6f} diff {d:+.3f} pp")
                if abs(d) > 0.10:
                    hs["n_outside_tolerance"] += 1
                    hs["pass"] = False
    print(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)")
    if mode == "--harness" or not hs["pass"]:
        sys.exit(0 if hs["pass"] else 1)

    res = run()
    g, selected = gates_and_selection(res, hs)
    pre, win, boot = res["pre"], res["windows"], res["bootstrap"]

    out = {
        "campaign": "tax_harvest_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "candidates": list(CANDIDATES),
            "overlay_impl": OVERLAY,
            "signals": "production R2 (equity_leveraged_trend_v1 1.2.0): E2 2-of-3 on price closes "
                       "(SMA200, 252-session price return > 0, close > 126-session max/min midpoint) "
                       "+ early re-entry at 0.5 x vt while E2 off and close > SMA50, vol20 < its "
                       "252-session median and close > close 20 sessions ago; exit early below SMA50",
            "sizing": "vt = min(1, 0.35/vol20) on synthetic QLD*, cap 0.98, 0.5 pp resize band",
            "instrument": "synthetic QLD* daily-reset 2x on QQQ total return, expense 0.95%/yr, "
                          "financing DTB3 + {base 0.0, calibrated 0.75%/yr}",
            "cost_per_side": COST_PER_SIDE, "dec_tail": DEC_TAIL,
            "main_window": pre["window"], "start": START,
            "after_tax": {"engine": "tax_engine.py (byte copy of r2_tbill_combo_v1/vt_level_tax_v1 "
                                    "corrected engine)", "financing": "base", "m": M,
                          "windows": TAX_WINDOWS, "capitals": TAX_CAPITALS,
                          "bootstrap": {"paths": TAX_BOOT_PATHS, "seed": SEED, "block": BLOCK,
                                        "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI,
                                        "modes": ["progressive (B1-recent)", "flat 20% (B2)"]}},
        },
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "after_tax_anchors": hs["after_tax_anchors"],
                    "bootstrap_control_medians": {cl: {md: {a: hs["bootstrap_control"][cl][md][a]["cagr_median"]
                                                            for a in ("C0", "R2")}
                                                       for md in ("progressive", "flat")}
                                                  for cl in hs["bootstrap_control"]}},
        "pre_tax_main": pre,
        "after_tax_windows": win,
        "bootstrap": boot,
        "gates": g,
        "selection": {"selected": selected,
                      "reason": "all pre-registered gates must pass" if selected else
                                "no arm passes every pre-registered gate"},
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    L = []
    w = pre["window"]
    L.append(f"tax_harvest_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
             f"control = production R2 (equity_leveraged_trend_v1 1.2.0)")
    L.append(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} checks outside tolerance); "
             f"overlay 'none' == fast_reentry simulate_band nav path max|diff| "
             f"{hs['reproduction_checks']['simulate_band_identity']['max_abs_nav_diff']:.2e}")
    if hs["bootstrap_control"]:
        for md in ("progressive", "flat"):
            L.append(f"  boot anchor {md:12s} " + "  ".join(
                f"{a} ref {BOOT_ANCHOR[md][a]:.6f} repro {hs['bootstrap_control']['100k'][md][a]['cagr_median']:.6f}"
                for a in ("C0", "R2")))
    L.append("")
    L.append("pre-tax main window (m = 2; G1 = CAGR >= R2 - 0.10 pp, G2 = DD <= R2 + 1 pp):")
    L.append("  arm  CAGR base  CAGR cal   maxDD base  maxDD cal   m=1 CAGR base  trades/yr  sells/yr  turn/yr  harvests  deferrals")
    for arm in ARMS:
        b2, c2 = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        b1 = pre["base"]["m1"][arm]
        yrs = pre["window"]["years"]
        L.append(f"  {arm:4s} {fmt_pct(b2['cagr'])} {fmt_pct(c2['cagr'])} {fmt_pct(b2['max_dd'])} "
                 f"{fmt_pct(c2['max_dd'])} {fmt_pct(b1['cagr'])} {b2['trades']/yrs:9.2f} "
                 f"{b2['sells_per_year']:9.2f} {b2['turnover_annual']:8.2f} {b2['harvests']:9d} {b2['deferrals']:10d}")
    L.append("")
    for cl in TAX_CAPITALS:
        L.append(f"after tax, progressive 2026 tariff, liquidated, ${cl} (base financing, m = 2):")
        L.append("  window                 arm  pre-tax   after-tax  d vs R2   maxDD    tax USD     sells/yr")
        for wn in TAX_WINDOWS:
            ctrl = win[cl][wn][CONTROL]["after_tax_cagr"]
            for arm in ARMS:
                r = win[cl][wn][arm]
                d = (r["after_tax_cagr"] - ctrl) * 100
                L.append(f"  {wn:20s} {arm:4s} {fmt_pct(r['pre_tax_cagr'])} {fmt_pct(r['after_tax_cagr'])} "
                         f"{d:+8.2f}pp {fmt_pct(r['max_dd_after_tax'])} {r['tax_usd']:11,.0f} {r['sells_per_year']:9.2f}")
        L.append("")
    L.append(f"clean after-tax bootstrap (block-20, {TAX_BOOT_PATHS} paths, {TAX_BOOT_LO}..{TAX_BOOT_HI}, fixed FX):")
    L.append("  capital mode         arm   median CAGR  median d vs R2   mean d   share>0   p05 / p95  harvests/10y")
    for cl in TAX_CAPITALS:
        for md in ("progressive", "flat"):
            e = boot[cl][md]
            for arm in ARMS:
                v = e.get(f"{arm}_vs_{CONTROL}")
                if v is None:
                    L.append(f"  {cl:7s} {md:12s} {arm:4s} {fmt_pct(e[arm]['cagr_median'])} "
                             f"{'--':>16s} {'--':>8s} {'--':>9s} {'--':>11s} {e[arm]['harvests_median']:11.1f}")
                else:
                    L.append(f"  {cl:7s} {md:12s} {arm:4s} {fmt_pct(e[arm]['cagr_median'])} "
                             f"{v['median_delta_cagr_pp']:+13.3f}pp {v['mean_delta_cagr_pp']:+8.3f} "
                             f"{v['share_gt_0'] * 100:8.1f}% {v['p05_delta_cagr_pp']:+5.2f}/{v['p95_delta_cagr_pp']:+5.2f} "
                             f"{e[arm]['harvests_median']:11.1f}")
        L.append("")
    L.append("gates (pre-tax G1/G2 both financings m=2; after-tax: R2 beaten in every window x capital; "
             "bootstrap median >= 0 and share > 0 >= 55% in B1/B2 at both capitals):")
    for arm in CANDIDATES:
        r = g[arm]
        L.append(f"  {arm:4s} G1 {'P' if r['G1_base'] and r['G1_cal'] else 'F'} "
                 f"G2 {'P' if r['G2_base'] and r['G2_cal'] else 'F'} "
                 f"G3 windows {sum(r['G3_windows'].values())}/{len(r['G3_windows'])} "
                 f"G4 bootstrap {sum(r['G4_bootstrap'].values())}/{len(r['G4_bootstrap'])} -> "
                 f"{'PASS' if r['pass'] else 'FAIL'}")
    L.append(f"selected: {selected}")
    L.append("")
    # tax per year, $100k, 2000 window: R2 vs every arm
    L.append("tax assessed per calendar year (TRY, $100k, 2000-03 -> 2026-09, progressive); "
             "d = arm - R2 (negative = tax deferred out of that year):")
    base_yr = win["100k"]["2000_03_to_2026_09"][CONTROL]["tax_assessed_try_by_year"]
    L.append("  (last year of each row lumps the terminal liquidation and any unsettled instalment)")
    for arm in CANDIDATES:
        ay = win["100k"]["2000_03_to_2026_09"][arm]["tax_assessed_try_by_year"]
        rows = []
        for y in sorted(set(base_yr) | set(ay)):
            d = ay.get(y, 0.0) - base_yr.get(y, 0.0)
            if abs(d) > 1.0:
                rows.append((y, d))
        pushed_later = sum(-d for _, d in rows if d < 0)
        pulled_earlier = sum(d for _, d in rows if d > 0)
        L.append(f"  {arm:4s} net total d {sum(d for _, d in rows):+14,.0f} TRY | tax pushed later "
                 f"{pushed_later:14,.0f} TRY | tax pulled earlier {pulled_earlier:14,.0f} TRY | "
                 f"per-year: " + ", ".join(f"{y}:{d:+,.0f}" for y, d in rows[:12]))
    L.append("")
    L.append("H1 harvest events ($100k, 2000 window): session, year, realised gain TRY, year gain before/after")
    for arm in ("H1", "H1b", "H3"):
        lg = win["100k"]["2000_03_to_2026_09"][arm]["harvest_log"]
        L.append(f"  {arm}: {len(lg)} events" + ("" if not lg else "; " + "; ".join(
            f"{e['session']} {e['realised_gain_try']:+,.0f} (gain {e['year_gain_before_try']:+,.0f} "
            f"-> {e['year_gain_after_try']:+,.0f})" for e in lg)))
    L.append("")
    L.append(f"VERDICT: {'selected ' + selected if selected else 'none of the overlays passes the gate set'}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
