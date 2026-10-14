#!/usr/bin/env python3
"""gain_band_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

I3 of research/idea_survey_v2: replace production R2's constant 0.5 pp resize band, on
**downward resizes only**, with (A1) a band that grows with the embedded indexed taxable
TRY gain of the lots the sale would consume, and (A2/A3) a band plus a bracket budget that
postpones discretionary downward resizes while the year's realised TRY gain is above the
year's deflated 27 %-bracket ceiling. Control = production R2.

Modes
  --smoke    synthetic random-walk prices only, no market data: the rule-gain probe vs the
             engine's own sell gain, C0 == the reference `simulate_band`, finiteness and
             after-tax <= pre-tax, and that the band/budget actually fire. The only mode run
             before the pre-registration commit.
  --harness  control reproduction on real data: R2/C0 pre-tax at m = 1/2 x base/calibrated
             vs fast_reentry_v1 / e2_integration_v1, the after-tax anchors at $100k on the
             three windows vs vt_level_tax_v1, the control's B1/B2 bootstrap medians vs
             tax_harvest_v2, and the C0 engine == `simulate_band` identity. No non-control
             arm is simulated on real data here.
  --run      harness, then every arm (C0 + A1/A2/A3) on every stage: pre-tax main window
             (base/calibrated, m = 1/2, m = 4 reported), the R-USD main-window row, the
             after-tax windows at $10k/$100k/$1M (progressive, liquidated) plus m = 4,
             the six OOS windows, the 21 instruments, the b

ootstraps E1 (G5/B1/B2) and E2
             (G8), then the gates and the selection rule. Writes RESULTS.json, SUMMARY.txt.

One engine serves every stage: `engine_run` is a stateful session loop over the Turkish
after-tax account (research/gain_band_v1/tax_engine.py, a byte copy of
research/tax_harvest_v2/tax_engine.py = the r2_tbill_combo_v1 / vt_level_tax_v1 corrected
engine) with production R2's execution layer plus the arm's fill rule. The rule's decision
state (the indexed rule gain and its year accumulator) is computed independently of the
regime, so the pre-tax and after-tax runs follow the same trade path. With arm "C0" the
engine is exactly tax_harvest_v2's tax_run_overlay(overlay="none") -- asserted in
--harness.

Engine helpers (synthetic 2x, SMA/TSM/DON, vol, vt, metrics) come from
research/trend_robustness_v2/screen.py (its main() is never run); the production R2 signal,
`simulate_band` and the OOS/instrument loaders are the copies used by
research/tax_harvest_v2/screen.py (itself from fast_reentry_v1 / ewma_band_v1 /
fast_reentry_v2, i.e. e2_confirmation_v1's and range_vol_v2's designs).
"""

from __future__ import annotations

import bisect
from collections import defaultdict
import csv
import importlib.util
import json
import math
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
_te_spec = importlib.util.spec_from_file_location("gb_tax_engine", HERE / "tax_engine.py")
te = importlib.util.module_from_spec(_te_spec)
_te_spec.loader.exec_module(te)

FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
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
M = 2
M_STRESS = 4
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))

# ---- the arm rule (pre-registered; see PREREGISTRATION.md sections 4 and 4.1)
ARMS = ("C0", "A1", "A2", "A3")
CONTROL = "C0"
CANDIDATES = ("A1", "A2", "A3")
NEIGHBOURS = {"A1": ("A2",), "A2": ("A1", "A3"), "A3": ("A2",)}
RULE = {
    "C0": {"band": False, "budget": False, "override": None},
    "A1": {"band": True, "budget": False, "override": None},
    "A2": {"band": True, "budget": True, "override": 0.15},
    "A3": {"band": True, "budget": True, "override": 0.10},
}
RULE["R2"] = RULE["C0"]  # alias: the reference-anchor harness runs label production R2 explicitly
B_UP = C0_BAND                                   # entries / exits / up-resizes keep 0.5 pp
GAIN_TIERS = ((0.01, 0.005), (0.05, 0.03), (math.inf, 0.08))  # g_frac ceiling -> B_down
BUDGET_CEILING_TRY = 1_000_000.0                 # x scale(year) = the year's 35 % bracket floor

# ---- after-tax stage
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITALS = {"10k": 10_000.0, "100k": 100_000.0, "1M": 1_000_000.0}
GATE_CAPITAL = "100k"
# The pre-tax runs of the main window and of bootstrap E1 use the gate capital, so that the
# bracket-budget state is evaluated at the same account scale as the after-tax gate runs (the
# pre-tax *return* statistics are scale-free). See PREREGISTRATION.md section 4.1 item 7.
PRE_CAPITAL = TAX_CAPITALS[GATE_CAPITAL]
TAX_BOOT_PATHS = 1_000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_L = 253 + 10 * 252  # 2,773 sessions (253 warm-up + 2,520 measured)
TAX_BOOT_EXT = 3
TAX_BOOT_I0 = TAX_BOOT_EXT + 253
PRE_BOOT_PATHS = 5_000  # G5 (pre-tax, both financings)

# ---- gates (pre-registered)
GATE_INSTR_MAX_DD_PP = 3.0
GATE_INSTR_MIN_SHARE = 0.80
GATE_OOS_MAX_DD_PP = 3.0
GATE_OOS_NEVER_DD_PP = 8.0
GATE_OOS_CAGR_PP = -0.50
BOOT_SHARE_MIN = 0.55
BOOT_MEDIAN_MIN_PP = 0.0

# ---- OOS windows (ewma_band_v1 / e2_confirmation_v1), calibrated, m = 2, R-USD rule
OOS_GSPC_FIRST, OOS_GSPC_LAST = "1957-01-02", "1999-12-31"
EXTRA_FIN = 0.0075
DTB3_FIRST = "1954-01-04"
MARKETS = {
    "M1": ("^IXIC", "1971-01-01", "1975-01-02", "1999-12-31"),
    "M2": ("^GSPC", "1934-01-01", None, "1956-12-31"),
    "M3": ("^N225", "1900-01-01", None, "2026-09-29"),
    "M4": ("^GDAXI", "1900-01-01", None, "2026-09-29"),
    "M5": ("^FTSE", "1900-01-01", None, "2026-09-29"),
}
OOS_WINDOWS = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")
OOS_PATHS_PER_MARKET = 2_000
OOS_BOOT_L = TSM_N + trv2.MEASURED  # 2,772

# ---- instruments (21 quality-passing ETFs; EWC excluded by range_vol_v2's fixed rule)
INSTRUMENT_FILES = {
    "XLB": "range_vol_v2_xlb.csv", "XLE": "range_vol_v2_xle.csv", "XLF": "range_vol_v2_xlf.csv",
    "XLI": "range_vol_v2_xli.csv", "XLK": "range_vol_v2_xlk.csv", "XLP": "range_vol_v2_xlp.csv",
    "XLU": "range_vol_v2_xlu.csv", "XLV": "range_vol_v2_xlv.csv", "XLY": "range_vol_v2_xly.csv",
    "EWJ": "range_vol_v2_ewj.csv", "EWG": "range_vol_v2_ewg.csv", "EWU": "range_vol_v2_ewu.csv",
    "EWC": "range_vol_v2_ewc.csv", "EWA": "range_vol_v2_ewa.csv", "EWH": "range_vol_v2_ewh.csv",
    "EWS": "range_vol_v2_ews.csv", "EWL": "range_vol_v2_ewl.csv", "EFA": "range_vol_v2_efa.csv",
    "EEM": "range_vol_v2_eem.csv", "SPY": "range_vol_v1_spy.csv", "DIA": "range_vol_v1_dia.csv",
    "IWM": "range_vol_v1_iwm.csv",
}
INSTRUMENTS = tuple(sorted(k for k in INSTRUMENT_FILES if k != "EWC"))
EXCLUDED_INSTRUMENTS = ("EWC",)
QUALITY_MAX_BAD_FRAC = 0.05

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


def _rolling_median_vol_ref(price_vol, window=MED_WINDOW):
    """v1's reference trailing rolling median of the vol series; NaN until the window is full."""
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


def _rolling_median_vol(price_vol, window=MED_WINDOW, block=32):
    """`_rolling_median_vol_ref` via sliding windows (same values; asserted in --smoke)."""
    P, T = price_vol.shape
    out = np.full((P, T), np.nan)
    if T < window:
        return out
    for s in range(0, P, block):
        e = min(P, s + block)
        blk = price_vol[s:e]
        fin = np.isfinite(blk)
        first = np.argmax(fin, axis=1)
        for r in range(e - s):
            if np.any(np.diff(fin[r].astype(np.int8)) < 0):
                raise ValueError("interior NaN in the vol series; use _rolling_median_vol_ref")
        j0 = int(first.min())
        if T - j0 < window:
            continue
        out[s:e, j0 + window - 1:] = np.median(sliding_window_view(blk[:, j0:], window, axis=1), axis=2)
    return out


def early_components(price):
    """The four path-independent early-entry conditions: SMA50, price vol20, its trailing
    252-session median, and 20-session momentum. `fast_reentry_v1`'s tuple also carried the
    Donchian vote and the 252-session running max; neither is used by the R2 or E2 branch of
    `r2_state`/`arm_signals`, so they are not computed here."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma50_up = trv2.sma_up(price, SMA_FAST)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol(vol_p, MED_WINDOW)
    close_lag20 = np.full_like(price, np.nan)
    close_lag20[:, LAG_FAST:] = price[:, :-LAG_FAST]
    lag_cond = price > close_lag20
    return sma50_up, vol_p, med_p, lag_cond


def r2_state(price, e2_on, comps=None):
    """(on_eff, factor) of production R2's path: the E2 vote plus the half-weight early
    re-entry leg. Model-independent -- the entry/exit conditions use the *price* series only,
    so one call serves both financing models. `fast_reentry_v1.arm_signals`' R2 branch,
    separated from the sizing so the session loop runs once per path."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    P, T = price.shape
    sma50_up, vol_p, med_p, lag_cond = early_components(price) if comps is None else comps
    early_active = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    for t in range(T):
        e2t = e2_on[:, t]
        early_active = np.where(e2t, False, early_active)
        exit_cond = ~sma50_up[:, t]
        entry_cond = (sma50_up[:, t] & np.isfinite(med_p[:, t])
                      & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t])
        early_active = np.where(early_active & exit_cond & (~e2t), False, early_active)
        can_enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & entry_cond, True, early_active)
        on_eff[:, t] = e2t | early_active
        factor[:, t] = np.where(e2t, 1.0, np.where(early_active, 0.5, 0.0))
    return on_eff, factor


def sized_wants(on_eff, factor, vol_instr):
    """`fast_reentry_v1.arm_signals`' sizing: min(1, 0.35/vol20) x the path factor, total cap 0.98."""
    vt = trv2.vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)))
    raw = np.atleast_2d(np.asarray(on_eff, dtype=float)) * np.atleast_2d(factor) * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def arm_signals(price, e2_on, vol_instr, arm, comps=None):
    """Target weights [P, T] for R2 (E2 + early re-entry) or production E2 ("C0")."""
    if arm == "R2":
        on_eff, factor = r2_state(price, e2_on, comps)
    else:
        on_eff = np.atleast_2d(np.asarray(e2_on, dtype=bool)).copy()
        factor = np.ones_like(on_eff, dtype=float)
    return sized_wants(on_eff, factor, vol_instr), None


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


# ----------------------------------------------------------------------------- the arm's decision state
def gain_band(g_frac):
    """B_down(g_frac): 0.5 pp <= 1 %, 3 pp <= 5 %, 8 pp above (pre-registered tiers)."""
    for hi, band in GAIN_TIERS:
        if g_frac <= hi:
            return band
    return GAIN_TIERS[-1][1]


def rule_sale(mkt, i, price, units, fee, lots, index=True):
    """(net rule gain, proceeds) in TRY of selling `units` at (session i, price), FIFO, read-only.

    The same per-lot arithmetic as `tax_engine.BaseAccount.sell` (asserted against a real sell
    in --smoke), except that the indexation branch is driven by the `index` flag instead of the
    tax regime, so the decision state is identical in the pre-tax and after-tax runs."""
    if units <= 0 or price <= 0 or not lots:
        return 0.0, 0.0
    per_unit = (units * price - fee) / units
    fx_s = mkt.fx[i]
    proceeds = units * per_unit * fx_s
    month = mkt.days[i][:7]
    left, gain, k = units, 0.0, 0
    n = len(lots)
    while left > 1e-15 and k < n:
        lot = lots[k]
        take = min(left, lot[0])
        p = take * per_unit * fx_s
        cost = take * lot[1] * lot[2]
        g = p - cost
        if index and g > 0:
            rise = mkt.ufe[te.prev_month(month)] / mkt.ufe[te.prev_month(lot[3])] - 1
            if rise >= te.INDEX_MIN_RISE:
                g = max(0.0, p - cost * (1 + rise))
        gain += g
        left -= take
        k += 1
    return gain, proceeds


# ----------------------------------------------------------------------------- the engine
def engine_run(capital, i0, i1, days, mkt, oq, cq, wq, e2on, arm, mode, m=M, rule_index=True):
    """One arm through the Turkish after-tax account, session by session.

    mode in {"pre_tax", "progressive", "flat"}; arm in ARMS. Every arm shares the target series
    `wq` (production R2's, or E2's for "C0"); only the fill decision differs: entries, exits to
    zero and up-resizes always fill (0.5 pp band), while a discretionary downward resize
    (`tgt > 0`, `tgt < w`) is subject to the arm's band and budget. `rule_index` selects the
    R-INDEX (True) or R-USD (False) instantiation of the decision state. With arm "C0" this is
    exactly research/tax_harvest_v2/screen.py:tax_run_overlay(overlay="none")."""
    days = list(days)
    if not isinstance(i0, int):
        i0, i1 = bisect.bisect_left(days, i0), bisect.bisect_right(days, i1) - 1
    oq, cq, wq = (np.asarray(x, dtype=float).flatten() for x in (oq, cq, wq))
    e2on = np.asarray(e2on, dtype=bool).flatten()
    cost = COST_PER_SIDE * m
    spec = RULE[arm]
    cashbox = {"v": float(capital)}
    if mode == "flat":
        qa = te.FlatAccount(mkt, 0.0, cashbox)
        pays = None
    else:
        rg = te.Regime("progressive", "tariff") if mode == "progressive" else te.Regime("pre_tax", "none")
        qa = te.BaseAccount(mkt, rg, 0.0, cashbox)
        pays = te.instalments(days, i0, i1)
    cashbox["v"] = float(capital)

    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = q_sells = tax_sales = postponed = 0
    turnover = 0.0
    rule_year = defaultdict(float)
    post_by_year = defaultdict(int)
    blocked_band = blocked_budget = 0
    prev_tgt = None

    for i in range(i0, i1 + 1):
        year_i = int(days[i][:4])
        # ---- year boundary: assess the previous year (verbatim)
        if i > i0 and year_i != int(days[i - 1][:4]):
            prevy = year_i - 1
            qa.tax_due[prevy] = qa.assess(prevy)
            if mode == "flat":  # B2 pays immediately, no instalments
                due = qa.tax_due[prevy]
                if due > 1e-9:
                    usd = due / mkt.fx[i]
                    if cashbox["v"] < usd:
                        need = usd - cashbox["v"]
                        p = float(cq[i - 1])
                        if qa.units > 1e-12:
                            g, _ = rule_sale(mkt, i - 1, p, need / p, need * cost, qa.lots, rule_index)
                            rule_year[year_i - 1] += g
                            qa.sell(i - 1, p, need / p, need * cost, "tax")
                            tax_sales += 1
                    cashbox["v"] -= usd
                    qa.tax_paid_try[prevy] = qa.tax_paid_try.get(prevy, 0.0) + due
                    qa.tax_paid_usd += usd

        # ---- tax instalments (funded by selling)
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
                        g, _ = rule_sale(mkt, i, float(oq[i]), need / float(oq[i]), need * cost,
                                         qa.lots, rule_index)
                        rule_year[year_i] += g
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

        trade = abs(tgt - w) > C0_BAND
        if qa.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if qa.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True

        drift = abs(tgt - w) if trade else 0.0
        fee_total = nav_open * cost * drift
        nav_after = nav_open - fee_total
        target_units = tgt * nav_after / po if po > 0 else 0.0
        du = target_units - qa.units

        # ---- the arm: gain-scaled band and bracket budget on a discretionary downward resize
        blocked = None
        if trade and tgt > 1e-12 and du < -1e-12:
            if spec["band"]:
                gain, proceeds = rule_sale(mkt, i, po, -du, fee_total, qa.lots, rule_index)
                g_frac = gain / proceeds if proceeds > 0 else 0.0
                if drift <= gain_band(g_frac):
                    trade, blocked = False, "band"
            if trade and spec["budget"]:
                if rule_year[year_i] >= BUDGET_CEILING_TRY * mkt.scale(year_i):
                    e2_off = (i >= 2) and (not bool(e2on[i - 1])) and bool(e2on[i - 2])
                    if not (drift > spec["override"] or e2_off):
                        trade, blocked = False, "budget"

        if trade:
            turnover += drift
            if du > 1e-12:
                qa.buy(i, po, du * po, fee_total)
            elif du < -1e-12:
                g, _ = rule_sale(mkt, i, po, -du, fee_total, qa.lots, rule_index)
                rule_year[year_i] += g
                qa.sell(i, po, -du, fee_total, "resize")
                q_sells += 1
            trades += 1
        elif blocked is not None:
            postponed += 1
            post_by_year[year_i] += 1
            if blocked == "band":
                blocked_band += 1
            else:
                blocked_budget += 1

        nav[i - i0 + 1] = cashbox["v"] + qa.units * pc
        prev_tgt = tgt

    # ---- liquidation at the window end (verbatim)
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
        "strategy_trades": int(trades), "qld_sells": int(q_sells), "tax_funding_sales": int(tax_sales),
        "sells": int(q_sells + tax_sales),
        "turnover_total": float(turnover), "turnover_annual": float(turnover / yrs),
        "postponements": int(postponed), "blocked_band": int(blocked_band),
        "blocked_budget": int(blocked_budget),
        "postponements_by_year": {int(k): int(v) for k, v in sorted(post_by_year.items())},
        "fees_usd": float(qa.fees), "index_relief_try": float(qa.index_relief_try),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.gain_by_reason.items())},
        "_nav": nav,
    }


# ----------------------------------------------------------------------------- stats
def seg(dates, nav, a, b):
    """From the last close <= a (first bar if a is None or before data) to the last close <= b."""
    ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1), "max_dd": float(trv2.max_dd(s)[0])}


def pre_stats(days, i0, i1, res, real_days=None):
    """Pre-tax stats of one engine run (NAV-path convention of fast_reentry_v1)."""
    d = list(real_days if real_days is not None else days)
    dates = d[i0 - 1:i1 + 1]
    nav = res["_nav"]
    years = trv2.calendar_years(dates[0], dates[-1])
    st = {
        "cagr": float((nav[-1] / nav[0]) ** (1 / years) - 1),
        "max_dd": float(trv2.max_dd(nav.reshape(1, -1))[0]),
        "turnover_annual": float(res["turnover_total"] / years),
        "trades": int(res["strategy_trades"]), "sells_per_year": float(res["qld_sells"] / years),
        "postponements": int(res["postponements"]), "blocked_band": int(res["blocked_band"]),
        "blocked_budget": int(res["blocked_budget"]), "years": float(years),
    }
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["2017_end"] = seg(dates, nav, "2016-12-31", None)
    st["subperiods"] = {n: seg(dates, nav, a, b) for n, a, b in SUBPERIODS}
    return st


def window_stats(nav, days, start):
    yrs = trv2.calendar_years(days[start - 1], days[-1])
    return {"cagr": float(trv2.cagr(nav[0], nav[-1], yrs)),
            "max_dd": float(trv2.max_dd(nav.reshape(1, -1))[0]), "years": float(yrs),
            "window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}


# ----------------------------------------------------------------------------- inputs
def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    price, e2, vol_instr = q[1], e2_up(q[1])[0], trv2.vol_n(c)[0]
    comps = early_components(price)
    w_c0 = arm_signals(price, e2, vol_instr, "C0", comps)[0][0]
    w_r2 = arm_signals(price, e2, vol_instr, "R2", comps)[0][0]
    return days, o, c, w_c0, w_r2, e2


def wants_of(price, e2, vol_instr, comps=None):
    """(E2 wants [P, T], R2 wants [P, T]) for one price series; one state loop for the pair."""
    comps = early_components(price) if comps is None else comps
    on_eff, factor = r2_state(price, e2, comps)
    e2_arr = np.atleast_2d(np.asarray(e2, dtype=bool))
    return (sized_wants(e2_arr, np.ones_like(factor), vol_instr),
            sized_wants(on_eff, factor, vol_instr))


def usd_market(days):
    """R-USD instantiation market: fx = 1 and a flat Yİ-ÜFE (no indexation, scale = 1)."""
    days = list(days)
    years = sorted({int(d[:4]) for d in days})
    ufe = {f"{y}-{mo:02d}": 1.0 for y in range(min(years[0], 2025) - 1, max(years[-1], 2026) + 2)
           for mo in range(1, 13)}
    return te.Market(days, np.ones(len(days)), ufe)


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


# ----------------------------------------------------------------------------- OOS windows (ewma_band_v1)
def load_gspc():
    """signal_ensemble_v1.load_gspc: ^GSPC from the first DTB3 date to 1999-12-31, opens := previous close."""
    rows = []
    with GSPC.open() as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["close"])))
    tb = {}
    with DTB3.open() as fh:
        for r in csv.DictReader(fh):
            tb[r["observation_date"]] = r["DTB3"]
    first_tb = min(tb)
    rows = [r for r in rows if first_tb <= r[0] <= OOS_GSPC_LAST]
    days = [r[0] for r in rows]
    c = np.array([r[1] for r in rows])
    o = np.concatenate([[c[0]], c[:-1]])
    y, last = [], None
    dset = set(days)
    for d in sorted(set(tb) | set(days)):
        v = tb.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        if d in dset:
            y.append(last)
    start = next(i for i, d in enumerate(days) if d >= OOS_GSPC_FIRST)
    assert start >= START
    return days, o, c, np.array(y), start


def load_rates():
    d3, tb = [], []
    with DTB3.open() as fh:
        for r in csv.DictReader(fh):
            if r["DTB3"] not in (".", ""):
                d3.append((r["observation_date"], float(r["DTB3"]) / 100))
    with TB3MS.open() as fh:
        for r in csv.DictReader(fh):
            if r["TB3MS"] not in (".", ""):
                tb.append((r["observation_date"], float(r["TB3MS"]) / 100))
    return d3, tb


def rate_on(dates, d3, tb):
    out = []
    i3 = it = 0
    last3 = lastt = None
    for d in dates:
        while i3 < len(d3) and d3[i3][0] <= d:
            last3 = d3[i3][1]
            i3 += 1
        while it < len(tb) and tb[it][0] <= d:
            lastt = tb[it][1]
            it += 1
        if d >= DTB3_FIRST:
            out.append(last3)
        else:
            if lastt is None:
                raise RuntimeError(f"no T-bill rate on {d}")
            out.append(lastt)
    return np.array(out, dtype=float)


def placeholder_open(o, h, lo, c):
    return (o is None or h is None or lo is None or o <= 0 or (o == h == lo == c)
            or o > h * (1 + 1e-9) or o < lo * (1 - 1e-9))


def load_market(key):
    """e2_confirmation_v1.load_market (pre-registered open rule)."""
    sym, first, meas_from, last = MARKETS[key]
    rows = []
    with INDEX.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == sym and first <= r["date"] <= last:
                f = lambda x: None if x == "" else float(x)
                rows.append((r["date"], f(r["open"]), f(r["high"]), f(r["low"]), float(r["close"])))
    days = [r[0] for r in rows]
    c = np.array([r[4] for r in rows])
    o = np.empty_like(c)
    for k, (d, op, h, lo, cl) in enumerate(rows):
        o[k] = cl if k == 0 else (c[k - 1] if placeholder_open(op, h, lo, cl) else op)
    d3, tb = load_rates()
    y = rate_on(days, d3, tb)
    start = START if meas_from is None else next(i for i, d in enumerate(days) if d >= meas_from)
    assert start >= START
    return days, o, c, y, start


def oos_window(days, o, c, y, start, arms):
    """One OOS window: own E2 + own R2 early path, own 2x leg (calibrated, m = 2), R-USD rule."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)[0]
    comps = early_components(c)
    w_c0, w_r2 = wants_of(c, e2, trv2.vol_n(sc)[0], comps)
    mkt = usd_market(days)
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        r = engine_run(1.0, start, len(days) - 1, days, mkt, so, sc,
                       w_c0 if arm == "C0" else w_r2, e2, arm, "pre_tax", m=M, rule_index=False)
        st = window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


# ----------------------------------------------------------------------------- instruments (gate F3)
def load_instrument(key):
    """Adjusted OHLC of one ETF: close := adjclose, open/high/low scaled by adjclose/close; DTB3 ffilled."""
    rows = []
    with (ROOT / "data/historical" / INSTRUMENT_FILES[key]).open() as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                         float(r["close"]), float(r["adjclose"])))
    dates = [r[0] for r in rows]
    o = np.empty(len(rows)); h = np.empty(len(rows)); l = np.empty(len(rows)); c = np.empty(len(rows))
    for k, (d, op, hi, lo, cl, adj) in enumerate(rows):
        f = adj / cl
        o[k] = op * f; h[k] = hi * f; l[k] = lo * f; c[k] = adj
    d3, tb = load_rates()
    y = rate_on(dates, d3, tb)
    return dates, o, h, l, c, y


def quality_ok(high, low, opn, close):
    """range_vol_v2's fixed data-quality rule: > 5 % of bars with high == low or an invalid open."""
    bad = 0
    for hi, lo, op, cl in zip(high, low, opn, close):
        if hi is None or lo is None or hi == lo:
            bad += 1
            continue
        if op is None or op <= 0 or op < lo * (1 - 1e-9) or op > hi * (1 + 1e-9):
            bad += 1
    frac = bad / max(1, len(high))
    return frac, bool(frac <= QUALITY_MAX_BAD_FRAC)


def instrument_window(days, o, c, y, arms, start=START):
    """Own E2 + own R2 early path on the instrument's adjusted close, own 2x leg (R-USD rule)."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)[0]
    comps = early_components(c)
    w_c0, w_r2 = wants_of(c, e2, trv2.vol_n(sc)[0], comps)
    mkt = usd_market(days)
    res = {}
    for arm in arms:
        r = engine_run(1.0, start, len(days) - 1, days, mkt, so, sc,
                       w_c0 if arm == "C0" else w_r2, e2, arm, "pre_tax", m=M, rule_index=False)
        st = window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


# ----------------------------------------------------------------------------- bootstrap E1 (fixed 2015-09..2026-09 calendar)
def boot_e1(n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS, capitals=("100k", "1M")):
    """Block-20 bootstrap on the fixed 2015-09 -> 2026-09 calendar with its realised USD/TRY and
    Yİ-ÜFE; market returns (QQQ overnight/intraday/dividend + DTB3) resampled exactly as
    tax_asym_v2 / vt_level_tax_v1 / tax_harvest_v2. Pre-tax at `n_pre` paths under both financing
    models (gate G5); after-tax progressive (B1) and flat 20 % (B2) at the first `n_tax` paths."""
    all_days, arr, y = trv2.load()
    sub = [d for d in all_days if TAX_BOOT_LO <= d <= TAX_BOOT_HI]
    need = TAX_BOOT_L + TAX_BOOT_EXT
    if len(sub) < need:
        raise RuntimeError(f"fixed calendar {TAX_BOOT_LO}..{TAX_BOOT_HI} has only {len(sub)} days, need {need}")
    fixed_dates = list(sub[:need])
    fixed_fx = fx_at(fixed_dates)
    ufe = te.load_market(FX_CSV, UFE_CSV, all_days).ufe
    mkt = te.Market(fixed_dates, fixed_fx, ufe)
    q = arr["QQQ"]
    Tsrc = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, Tsrc)
    npool = len(pool)
    L, EXT_ = TAX_BOOT_L, TAX_BOOT_EXT
    i0, i1 = TAX_BOOT_I0, len(fixed_dates) - 1
    pre = {(md, a): [] for md in FIN for a in ARMS}
    at = {cl: {mode: {a: [] for a in ARMS} for mode in ("progressive", "flat")} for cl in capitals}
    anchor_e2 = {"progressive": [], "flat": []}   # pure-E2 targets, $100k (engine-fidelity anchor)
    for pidx in range(max(n_pre, n_tax)):
        pos = np.empty((1, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, 1)
        jump = rng.random((1, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (1, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre_cols = np.stack([(pos[:, 0] - k) % npool for k in range(EXT_, 0, -1)], axis=1)
        pos = np.concatenate([pre_cols, pos], axis=1)
        idx = pool[pos]
        yy = y[idx - 1]
        prev = q[1][idx - 1]
        r_on_tr = (q[0][idx] + q[2][idx]) / prev
        r_on_p = q[0][idx] / prev
        r_id = q[1][idx] / q[0][idx]
        one = np.ones((1, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        e2 = e2_up(pc)[0]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        comps = early_components(pc)
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN["base"], EXP_Q)
        so_c, sc_c = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN["calibrated"], EXP_Q)
        # one state loop per path; the sizing differs per financing model only
        on_eff, factor = r2_state(pc, e2, comps)
        w_r2 = sized_wants(on_eff, factor, trv2.vol_n(sc)[0])[0]
        w_r2c = sized_wants(on_eff, factor, trv2.vol_n(sc_c)[0])[0]
        w_e2 = sized_wants(np.atleast_2d(e2), np.ones_like(factor), trv2.vol_n(sc)[0])[0]
        legs = {"base": (so[0], sc[0], w_r2), "calibrated": (so_c[0], sc_c[0], w_r2c)}
        if pidx < n_pre:
            for md, (o_, c_, want) in legs.items():
                for arm in ARMS:
                    r = engine_run(PRE_CAPITAL, i0, i1, fixed_dates, mkt, o_, c_, want, e2,
                                   arm, "pre_tax", m=M)
                    k = len(r["_nav"]) - 1
                    pre[(md, arm)].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
        if pidx < n_tax:
            o_, c_, want = legs["base"]
            for cap_label in capitals:
                for mode in ("progressive", "flat"):
                    for arm in ARMS:
                        r = engine_run(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, o_, c_,
                                       want, e2, arm, mode, m=M)
                        if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                            raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode} {cap_label}")
                        at[cap_label][mode][arm].append(r["cagr_usd_liquidated"])
                    # anchor label only: pure E2 targets (the reference bootstrap's "C0")
                    r = engine_run(TAX_CAPITALS[GATE_CAPITAL], i0, i1, fixed_dates, mkt, o_, c_,
                                   w_e2, e2, "C0", mode, m=M)
                    anchor_e2[mode].append(r["cagr_usd_liquidated"])
        if (pidx + 1) % 500 == 0:
            print(f"  E1 path {pidx + 1}/{max(n_pre, n_tax)}", flush=True)

    out = {"n_paths_pre": n_pre, "n_paths_tax": n_tax, "seed": SEED, "block": BLOCK,
           "calendar_start": fixed_dates[0], "calendar_end": fixed_dates[-1], "pre": {}, "tax": {}}
    for md in FIN:
        base = np.array(pre[(md, CONTROL)])
        out["pre"][md] = {}
        for arm in ARMS:
            c = np.array(pre[(md, arm)])
            d = c - base
            out["pre"][md][arm] = {
                "cagr_median": float(np.median(c)), "diff_vs_C0_median_pp": float(np.median(d) * 100),
                "diff_vs_C0_mean_pp": float(np.mean(d) * 100), "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100), "p95_delta_pp": float(np.percentile(d, 95) * 100)}
    for cl in capitals:
        out["tax"][cl] = {}
        for mode in ("progressive", "flat"):
            e = {"n_paths": n_tax, "capital_usd": TAX_CAPITALS[cl]}
            for arm in ARMS:
                e[arm] = {"cagr_median": float(np.median(at[cl][mode][arm]))}
            for ctrl in (CONTROL,):
                base = np.array(at[cl][mode][ctrl])
                for arm in CANDIDATES:
                    d = np.array(at[cl][mode][arm]) - base
                    e[f"{arm}_vs_{ctrl}"] = {
                        "median_delta_cagr_pp": float(np.median(d) * 100),
                        "mean_delta_cagr_pp": float(np.mean(d) * 100),
                        "share_gt_0": float(np.mean(d > 0)),
                        "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                        "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100)}
            out["tax"][cl][mode] = e
    out["anchor_e2_100k"] = {mode: {"cagr_median": float(np.median(v))} for mode, v in anchor_e2.items()}
    return out


# ----------------------------------------------------------------------------- bootstrap E2 (pooled OOS, M1-M5)
def boot_oos_market(key, rng, arms):
    """fast_reentry_v1.bootstrap_market draws (shared generator, markets in order), R-USD rule."""
    days, o, c, y, start = load_market(key)
    pool = np.arange(start, len(days))
    npool = len(pool)
    mkt = usd_market(days)
    acc = {a: [] for a in arms}
    for chunk in range(0, OOS_PATHS_PER_MARKET, trv2.CHUNK):
        P = min(trv2.CHUNK, OOS_PATHS_PER_MARKET - chunk)
        pos = np.empty((P, OOS_BOOT_L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, OOS_BOOT_L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, OOS_BOOT_L))
        for t in range(1, OOS_BOOT_L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        idx = pool[pos]
        r_on = o[idx] / c[idx - 1]
        r_id = c[idx] / o[idx]
        cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        e2 = e2_up(cc)
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        vol_instr = trv2.vol_n(sc)
        w_c0, w_r2 = wants_of(cc, e2, vol_instr)
        for arm in arms:
            want = w_c0 if arm == "C0" else w_r2
            for p in range(P):
                r = engine_run(1.0, START, cc.shape[1] - 1, days, mkt, so[p], sc[p], want[p], e2[p],
                               arm, "pre_tax", m=M, rule_index=False)
                k = len(r["_nav"]) - 1
                acc[arm].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
    return {a: np.array(v, dtype=float) for a, v in acc.items()}


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
    """Whole rule path on synthetic random-walk prices; no market data touched."""
    n = 1600
    o, price, y = synthetic_bars(n, 7)
    days = _trading_days(n)
    so, sc = trv2.synthetic_2x(o, price, np.zeros(n), y, EXP_Q)
    e2 = e2_up(price)[0]
    v20 = trv2.vol_n(sc)[0]
    w_c0, w_r2 = wants_of(price, e2, v20, early_components(price))
    ufe = {f"{yr}-{mo:02d}": float(1000 + 12 * yr + 5 * mo) for yr in range(1995, 2030) for mo in range(1, 13)}
    mkt = te.Market(days, np.full(n, 30.0), ufe)
    checks, res = {}, {}

    # 1. probe == the engine's own sell gain (progressive mode), on random lot books
    ok = True
    rng = np.random.default_rng(11)
    for _ in range(300):
        acc = te.BaseAccount(mkt, te.Regime("progressive", "tariff"), 0.0, {"v": 1e6})
        ibuy = int(rng.integers(1, n - 2))
        for _ in range(int(rng.integers(1, 5))):
            acc.buy(ibuy, float(sc[ibuy]), float(rng.uniform(0.5, 10)) * float(sc[ibuy]), 0.0)
        i = int(rng.integers(ibuy, n))
        units = acc.units * float(rng.uniform(0.1, 1.0))
        fee = units * float(sc[i]) * 0.0008
        probe, proceeds = rule_sale(mkt, i, float(sc[i]), units, fee, list(acc.lots), True)
        real = acc.sell(i, float(sc[i]), units, fee, "resize")
        if abs(probe - real) > 1e-9 * max(1.0, abs(real)):
            ok = False
        if abs(proceeds - units * (float(sc[i]) - fee / units) * mkt.fx[i]) > 1e-9 * max(1.0, proceeds):
            ok = False
    checks["probe_equals_engine_sell"] = bool(ok)

    # 2. fast rolling vol median == the loop reference
    pv = trv2.vol_n(100 * np.cumprod(1 + rng.normal(0, 0.01, 1400)))
    checks["rolling_median_fast_equals_ref"] = bool(np.array_equal(
        _rolling_median_vol(pv), _rolling_median_vol_ref(pv), equal_nan=True))

    # 2b. the split state/sizing helpers == the reference arm_signals (R2 want series)
    w_ref = arm_signals(price, e2, v20, "R2")[0][0]
    on_eff, factor = r2_state(price, e2, early_components(price))
    checks["r2_state_sizing_matches_arm_signals"] = bool(
        np.array_equal(w_ref, sized_wants(on_eff, factor, v20)[0]))
    checks["wants_of_matches_arm_signals"] = bool(
        np.array_equal(wants_of(price, e2, v20)[1][0], w_ref))

    # 3. C0 engine == the reference simulate_band (base, m = 2)
    ref_nav, _, _ = simulate_band(so[None, :], sc[None, :], w_r2, COST_PER_SIDE * 2, START, C0_BAND)
    c0 = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_r2, e2, "C0", "pre_tax")
    checks["c0_engine_equals_simulate_band"] = bool(np.max(np.abs(c0["_nav"] - ref_nav[0])) < 1e-9)

    # 4. the mechanism fires: a sustained synthetic uptrend must block at least one downsize
    r = np.full(1500, 0.0012)
    price_up = 100.0 * np.cumprod(1 + r)
    o_up = np.concatenate([[price_up[0]], price_up[:-1]])
    so_u, sc_u = trv2.synthetic_2x(o_up, price_up, np.zeros(1500), np.full(1500, 0.01), EXP_Q)
    days_u = _trading_days(1500)
    ufe_u = {f"{yr}-{mo:02d}": float(1000 + 60 * yr + 8 * mo) for yr in range(2000, 2030) for mo in range(1, 13)}
    mkt_u = te.Market(days_u, np.full(1500, 30.0), ufe_u)
    e2_u = e2_up(price_up)[0]
    wc_u, wr_u = wants_of(price_up, e2_u, trv2.vol_n(sc_u)[0])
    blocked = {}
    for arm in ARMS:
        rr = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wc_u if arm == "C0" else wr_u,
                        e2_u, arm, "pre_tax")
        blocked[arm] = {"band": rr["blocked_band"], "budget": rr["blocked_budget"],
                        "postponed": rr["postponements"]}
    checks["mechanism_fires"] = bool(blocked["A1"]["band"] > 0 or blocked["A2"]["band"] > 0
                                     or blocked["A2"]["budget"] > 0)
    checks["C0_blocks_nothing"] = bool(blocked["C0"]["postponed"] == 0)

    # 4b. the tax modes bite in a trending series: progressive <= pre-tax, and flat < pre-tax
    flat_bites = prog_bites = False
    for arm in ARMS:
        want_u = wc_u if arm == "C0" else wr_u
        pre_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, want_u, e2_u, arm, "pre_tax")
        flat_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, want_u, e2_u, arm, "flat")
        prog_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, want_u, e2_u, arm, "progressive")
        if prog_u["cagr_usd_liquidated"] > pre_u["cagr_usd_liquidated"] + 1e-9:
            checks["progressive_le_pre_tax"] = False
        else:
            checks.setdefault("progressive_le_pre_tax", True)
        if prog_u["tax_total_usd_at_payment_fx"] > 0:
            prog_bites = True
        if flat_u["cagr_usd_liquidated"] < pre_u["cagr_usd_liquidated"] - 1e-9:
            flat_bites = True
    checks["progressive_tax_nonzero_in_uptrend"] = bool(prog_bites)
    checks["flat_tax_bites_in_uptrend"] = bool(flat_bites)

    # 4c. the bracket budget is reachable: at a large account scale (fx = 1, flat Yi-UFE) a
    # realized rule gain above the nominal ceiling must postpone a downsize that the band passed.
    mk_u = usd_market(days)
    big = engine_run(1e9, START, n - 1, days, mk_u, so, sc, w_r2, e2, "A2", "pre_tax")
    checks["budget_fires_at_large_capital"] = bool(big["blocked_budget"] > 0)
    checks["A1_has_no_budget"] = bool(
        engine_run(1e9, START, n - 1, days, mk_u, so, sc, w_r2, e2, "A1", "pre_tax")["blocked_budget"] == 0)

    # 5. finite, positive, and after tax <= pre-tax
    for arm in ARMS:
        want = w_c0 if arm == "C0" else w_r2
        rr = engine_run(1.0, START, n - 1, days, mkt, so, sc, want, e2, arm, "pre_tax")
        rp = engine_run(1.0, START, n - 1, days, mkt, so, sc, want, e2, arm, "progressive")
        rf = engine_run(1.0, START, n - 1, days, mkt, so, sc, want, e2, arm, "flat")
        for r_ in (rp, rf, rr):
            if not (np.isfinite(r_["cagr_usd_liquidated"]) and r_["terminal_usd_liquidated"] > 0):
                checks["everything_finite"] = False
                break
        else:
            checks.setdefault("everything_finite", True)
        if rp["cagr_usd_liquidated"] > rr["cagr_usd_liquidated"] + 1e-9:
            checks["everything_finite"] = False
        res[arm] = {"pre_tax_cagr": rr["cagr_usd_liquidated"],
                    "progressive_cagr": rp["cagr_usd_liquidated"],
                    "flat_cagr": rf["cagr_usd_liquidated"],
                    "postponed_pre": rr["postponements"]}
    return {"checks": checks, "arms": res, "mechanism": blocked,
            "pass": bool(all(checks.values()))}


def harness(run_boot=True):
    """Control reproduction on real data (no candidate arm)."""
    checks, bad = {}, 0
    days, o, c, w_c0, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    for md, spread in FIN.items():
        d_, o_, c_, wc_, wr_, e_ = main_inputs(spread)
        for arm, ref, wants in (("C0", REF_DEV, wc_), ("R2", REF_R2, wr_)):
            for m in (1, 2):
                r = engine_run(1.0, START, len(d_) - 1, d_, mkt, o_, c_, wants, e_, arm, "pre_tax", m=m)
                for key, val in (("cagr_usd_unliquidated", ref[(md, m)][0]),
                                 ("max_dd_nav_usd", ref[(md, m)][1]),
                                 ("turnover_annual", ref[(md, m)][2])):
                    got = float(r[key])
                    checks[f"{arm}_{md}_m{m}_{key}"] = {"reference": val, "reproduced": got, "diff": got - val}
                    if abs(got - val) > TOL:
                        bad += 1
    ref_nav, _, _ = simulate_band(o[None, :], c[None, :], w_r2[None, :], COST_PER_SIDE * 2, START, C0_BAND)
    mine = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, "C0", "pre_tax")
    d_nav = float(np.max(np.abs(mine["_nav"] - ref_nav[0])))
    checks["c0_engine_equals_simulate_band"] = {"max_abs_nav_diff": d_nav}
    if d_nav > 1e-9:
        bad += 1
    ours = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, "A1", "pre_tax")
    checks["A1_nav_differs_from_C0"] = {"max_abs_nav_diff": float(np.max(np.abs(ours["_nav"] - mine["_nav"])))}
    tax = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        tax[wn] = {}
        for arm, wants in (("C0", w_c0), ("R2", w_r2)):
            rp = engine_run(100_000.0, a, b, days, mkt, o, c, wants, e2, arm, "pre_tax")
            rq = engine_run(100_000.0, a, b, days, mkt, o, c, wants, e2, arm, "progressive")
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
    boot = {}
    if run_boot:
        b = boot_e1(n_pre=TAX_BOOT_PATHS, n_tax=TAX_BOOT_PATHS, capitals=("100k",))
        for mode in ("progressive", "flat"):
            # this study's control ("C0") *is* production R2 (E2 + the early path); the reference
            # bootstrap's "C0" is pure E2, tracked here by the anchor_e2 label.
            boot[mode] = {"C0": b["tax"]["100k"][mode]["C0"]["cagr_median"],
                          "R2": b["tax"]["100k"][mode]["C0"]["cagr_median"],
                          "E2": b["anchor_e2_100k"][mode]["cagr_median"]}
            for label, ref_key in (("E2", "C0"), ("C0", "R2")):
                if abs(boot[mode][label] - BOOT_ANCHOR[mode][ref_key]) > 0.0010:
                    bad += 1
        boot["pre_base"] = {a: b["pre"]["base"][a]["cagr_median"] for a in ARMS}
        boot["pre_cal"] = {a: b["pre"]["calibrated"][a]["cagr_median"] for a in ARMS}
    return {"reproduction_checks": checks, "n_outside_tolerance": bad, "after_tax_anchors": tax,
            "bootstrap_control": boot, "pass": bool(bad == 0)}


# ----------------------------------------------------------------------------- the run
def run() -> dict:
    days, o, c, w_c0, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    WANTS = {"C0": w_c0, "A1": w_r2, "A2": w_r2, "A3": w_r2}
    usd_mkt = usd_market(days)

    # ---- pre-tax main window (both financings, m = 1/2/4), R-INDEX
    pre, pre_usd = {}, {}
    for md, spread in FIN.items():
        d_, o_, c_, wc_, wr_, e_ = main_inputs(spread)
        W = {"C0": wc_, "A1": wr_, "A2": wr_, "A3": wr_}
        pre[md], pre_usd[md] = {}, {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in ARMS:
                r = engine_run(PRE_CAPITAL, START, T, d_, mkt, o_, c_, W[arm], e_, arm, "pre_tax", m=m)
                pre[md][f"m{m}"][arm] = pre_stats(d_, START, T, r)
        for arm in ARMS:
            r = engine_run(PRE_CAPITAL, START, T, d_, usd_mkt, o_, c_, W[arm], e_, arm, "pre_tax", m=M,
                           rule_index=False)
            pre_usd[md][arm] = pre_stats(d_, START, T, r, real_days=d_)
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": T - START + 1,
                     "years": float(trv2.calendar_years(days[START - 1], days[-1]))}
    print("pre-tax main window done", flush=True)

    # ---- after tax: three windows x three capitals (pre-tax is scale-free), progressive
    print("after-tax windows", flush=True)
    win, cost = {}, {}
    for cap_label, capital in TAX_CAPITALS.items():
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            win[cap_label][wn] = {}
            for arm in ARMS:
                rq = engine_run(capital, a, b, days, mkt, o, c, WANTS[arm], e2, arm, "progressive")
                rp = engine_run(1.0, a, b, days, mkt, o, c, WANTS[arm], e2, arm, "pre_tax")
                win[cap_label][wn][arm] = {
                    "pre_tax_cagr": rp["cagr_usd_liquidated"],
                    "after_tax_cagr": rq["cagr_usd_liquidated"],
                    "after_tax_cagr_unliquidated": rq["cagr_usd_unliquidated"],
                    "max_dd_after_tax": rq["max_dd_nav_usd"],
                    "terminal_usd": rq["terminal_usd_liquidated"],
                    "terminal_usd_unliquidated": rq["terminal_usd_unliquidated"],
                    "tax_usd": rq["tax_total_usd_at_payment_fx"],
                    "sells_per_year": rq["sells"] / rq["years"],
                    "trades_per_year": rq["strategy_trades"] / rq["years"],
                    "postponements": rq["postponements"], "blocked_band": rq["blocked_band"],
                    "blocked_budget": rq["blocked_budget"],
                    "postponements_by_year": rq["postponements_by_year"],
                    "index_relief_try": rq["index_relief_try"],
                }
    for wn, (a, b) in TAX_WINDOWS.items():
        cost[wn] = {}
        for arm in ARMS:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, WANTS[arm], e2, arm,
                            "progressive", m=M_STRESS)
            rp = engine_run(1.0, a, b, days, mkt, o, c, WANTS[arm], e2, arm, "pre_tax", m=M_STRESS)
            cost[wn][arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                             "pre_tax_cagr": rp["cagr_usd_liquidated"],
                             "max_dd_after_tax": rq["max_dd_nav_usd"],
                             "turnover_annual": rp["turnover_annual"],
                             "postponements": rq["postponements"]}
    print("cost stress done", flush=True)

    # ---- OOS windows (six), R-USD, calibrated, m = 2
    print("OOS windows", flush=True)
    oos = {}
    gd, go, gc, gy, gstart = load_gspc()
    oos["GSPC_1957_1999"] = oos_window(gd, go, gc, gy, gstart, ARMS)
    for key in ("M1", "M2", "M3", "M4", "M5"):
        md_, mo_, mc_, my_, mstart_ = load_market(key)
        oos[key] = oos_window(md_, mo_, mc_, my_, mstart_, ARMS)
    for wn in OOS_WINDOWS:
        for arm in CANDIDATES:
            oos[wn][arm]["delta_cagr_pp"] = (oos[wn][arm]["cagr"] - oos[wn][CONTROL]["cagr"]) * 100
            oos[wn][arm]["delta_max_dd_pp"] = (oos[wn][arm]["max_dd"] - oos[wn][CONTROL]["max_dd"]) * 100
    print("OOS done", flush=True)

    # ---- instruments (21 quality-passing ETFs + EWC for the record)
    print("instruments", flush=True)
    inst, quality = {}, {}
    for key in sorted(INSTRUMENT_FILES):
        idays, io, ih, il, ic, iy = load_instrument(key)
        frac, ok = quality_ok(ih, il, io, ic)
        quality[key] = {"bad_frac": float(frac), "pass": bool(ok), "sessions": len(idays)}
        if key in EXCLUDED_INSTRUMENTS:
            continue
        r = instrument_window(idays, io, ic, iy, ARMS)
        for arm in CANDIDATES:
            r[arm]["delta_cagr_pp"] = (r[arm]["cagr"] - r[CONTROL]["cagr"]) * 100
            r[arm]["delta_max_dd_pp"] = (r[arm]["max_dd"] - r[CONTROL]["max_dd"]) * 100
        inst[key] = r
    print("instruments done", flush=True)

    # ---- bootstraps
    print("E1 bootstrap (pre-tax 5,000 + B1/B2 1,000)", flush=True)
    boot = boot_e1()
    print("E2 bootstrap (pooled OOS, M1-M5)", flush=True)
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, ARMS) for k in MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in ARMS}
    oos_boot = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        oos_boot["per_market"][k] = {a: {"median_diff_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)}
                                     for a in ARMS}
    for a in ARMS:
        d = pooled[a] - pooled[CONTROL]
        oos_boot["pooled"][a] = {"diff_vs_C0_median_pp": float(np.median(d) * 100),
                                 "diff_vs_C0_mean_pp": float(np.mean(d) * 100),
                                 "share_gt_0": float(np.mean(d > 0)), "paths": int(len(d))}
    print("bootstraps done", flush=True)
    return {"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost, "oos": oos,
            "instruments": inst, "quality": quality, "bootstrap": boot, "oos_bootstrap": oos_boot}


# ----------------------------------------------------------------------------- gates
def g_return(a, c):
    """CAGR >= C0, or (Calmar >= 1.1 x C0 and CAGR >= C0 - 0.5 pp)."""
    if a["cagr"] >= c["cagr"]:
        return True
    ca, cc = a["calmar"], c["calmar"]
    if ca is None or cc is None:
        return False
    return bool(ca >= 1.10 * cc and a["cagr"] >= c["cagr"] - 0.005)


def gates_and_selection(res):
    pre, win, cost = res["pre"], res["windows"], res["cost"]
    oos, inst, boot = res["oos"], res["instruments"], res["bootstrap"]
    oos_boot = res["oos_bootstrap"]
    ctrl = win[GATE_CAPITAL]
    g = {}
    for arm in CANDIDATES:
        r = {}
        core = {}
        for md in FIN:
            a2, c2 = pre[md]["m2"][arm], pre[md]["m2"][CONTROL]
            a1, c1 = pre[md]["m1"][arm], pre[md]["m1"][CONTROL]
            core[f"G1_{md}"] = g_return(a2, c2)
            core[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
            core[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
            core[f"G5_{md}"] = bool(boot["pre"][md][arm]["diff_vs_C0_median_pp"] >= BOOT_MEDIAN_MIN_PP)
            core[f"G6_{md}"] = g_return(a1, c1)
        a2, c2 = pre["base"]["m2"][arm], pre["base"]["m2"][CONTROL]
        wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
        core["G4"] = bool(len(wins) >= 2)
        core["G4_wins"] = wins
        core["G9"] = bool(a2["turnover_annual"] <= 2.0 * c2["turnover_annual"])
        dc = [oos[wn][arm]["delta_cagr_pp"] for wn in OOS_WINDOWS]
        ddd = [oos[wn][arm]["delta_max_dd_pp"] for wn in OOS_WINDOWS]
        core["G7"] = bool(float(np.median(dc)) >= 0
                          and sum(1 for v in dc if v >= GATE_OOS_CAGR_PP) >= 5
                          and sum(1 for v in ddd if v <= GATE_OOS_MAX_DD_PP + 1e-12) >= 5
                          and max(ddd) <= GATE_OOS_NEVER_DD_PP + 1e-12)
        core["G7_detail"] = {"cagr_diff_pp": {k: oos[k][arm]["delta_cagr_pp"] for k in OOS_WINDOWS},
                             "dd_diff_pp": {k: oos[k][arm]["delta_max_dd_pp"] for k in OOS_WINDOWS},
                             "median_cagr_diff_pp": float(np.median(dc)),
                             "windows_cagr_ge_minus_0_5pp": int(sum(1 for v in dc if v >= GATE_OOS_CAGR_PP)),
                             "windows_dd_within_3pp": int(sum(1 for v in ddd if v <= GATE_OOS_MAX_DD_PP + 1e-12))}
        core["G8"] = bool(oos_boot["pooled"][arm]["diff_vs_C0_median_pp"] >= 0)
        core["G8_pooled_median_pp"] = oos_boot["pooled"][arm]["diff_vs_C0_median_pp"]
        # after tax
        at_cells = {wn: {"arm": ctrl[wn][arm]["after_tax_cagr"], "R2": ctrl[wn][CONTROL]["after_tax_cagr"],
                         "pre_tax_arm": win[GATE_CAPITAL][wn][arm]["pre_tax_cagr"],
                         "pre_tax_R2": win[GATE_CAPITAL][wn][CONTROL]["pre_tax_cagr"],
                         "pass": bool(ctrl[wn][arm]["after_tax_cagr"] > ctrl[wn][CONTROL]["after_tax_cagr"])}
                    for wn in TAX_WINDOWS}
        core["AT"] = bool(all(v["pass"] for v in at_cells.values()))
        core["AT_cells"] = at_cells
        boots = {}
        for label in ("B1_recent", "B2_flat"):
            mode = "progressive" if label == "B1_recent" else "flat"
            v = boot["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{CONTROL}"]
            boots[label] = {"median_delta_pp": v["median_delta_cagr_pp"], "share_gt_0": v["share_gt_0"],
                            "p05_pp": v["p05_delta_cagr_pp"], "p95_pp": v["p95_delta_cagr_pp"],
                            "pass": bool(v["median_delta_cagr_pp"] >= BOOT_MEDIAN_MIN_PP
                                         and v["share_gt_0"] >= BOOT_SHARE_MIN)}
        core["B1"] = boots["B1_recent"]["pass"]
        core["B2"] = boots["B2_flat"]["pass"]
        core["boot"] = boots
        # instruments
        keys = sorted(inst)
        dds = [inst[k][arm]["delta_max_dd_pp"] for k in keys]
        dcs = [inst[k][arm]["delta_cagr_pp"] for k in keys]
        share = float(np.mean([v <= GATE_INSTR_MAX_DD_PP + 1e-12 for v in dds]))
        core["F3"] = bool(share >= GATE_INSTR_MIN_SHARE)
        core["F3_detail"] = {"n_instruments": len(keys), "share_dd_within_3pp": share,
                             "n_dd_within": int(sum(v <= GATE_INSTR_MAX_DD_PP + 1e-12 for v in dds)),
                             "worst_delta_max_dd_pp": float(max(dds)),
                             "worst_instrument": keys[int(np.argmax(dds))],
                             "median_delta_cagr_pp": float(np.median(dcs)),
                             "share_cagr_gt_0": float(np.mean([v > 0 for v in dcs]))}
        # cost stress (report only)
        core["m4_reported"] = {wn: bool(cost[wn][arm]["after_tax_cagr"] > cost[wn][CONTROL]["after_tax_cagr"])
                               for wn in TAX_WINDOWS}
        core["usd_main_reported"] = {"cagr": res["pre_usd"]["base"][arm]["cagr"],
                                     "R2_cagr": res["pre_usd"]["base"][CONTROL]["cagr"],
                                     "delta_pp": (res["pre_usd"]["base"][arm]["cagr"]
                                                  - res["pre_usd"]["base"][CONTROL]["cagr"]) * 100}
        r.update(core)
        r["pass_all"] = bool(all(core[k] for k in
                                 [f"{x}_{md}" for x in ("G1", "G2", "G3", "G5", "G6")
                                  for md in FIN] + ["G4", "G7", "G8", "G9", "AT", "B1", "B2", "F3"]))
        g[arm] = r

    # plateau: the selected arm's neighbours must each pass AT (after tax, all three windows)
    plateau = {a: bool(all(g[nbr]["AT"] for nbr in NEIGHBOURS[a])) for a in CANDIDATES}
    for a in CANDIDATES:
        g[a]["P"] = plateau[a]
        g[a]["P_neighbours"] = {n: g[n]["AT"] for n in NEIGHBOURS[a]}
        g[a]["eligible"] = bool(g[a]["pass_all"] and plateau[a])
    eligible = [a for a in CANDIDATES if g[a]["eligible"]]
    key = lambda a: (ctrl["2000_03_to_2026_09"][a]["after_tax_cagr"],
                     pre["base"]["m2"][a]["cagr"])
    sel = max(eligible, key=key) if eligible else "none"
    selection = {"eligible": eligible, "selected": sel,
                 "rule": "arms passing G1-G9 + AT + B1 + B2 + F3 and their plateau neighbours' AT; "
                         "selected = highest after-tax liquidated CAGR at $100k on the 2000 window"}
    return {"arms": g, "plateau": plateau, "selection": selection}


# ----------------------------------------------------------------------------- main
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
            for arm, ref_key in (("E2", "C0"), ("C0", "R2")):
                b = hs["bootstrap_control"][mode_][arm]
                ref = BOOT_ANCHOR[mode_][ref_key]
                print(f"boot anchor {mode_} {arm} (ref {ref_key}): ref {ref:.6f} repro {b:.6f} "
                      f"diff {(b - ref) * 100:+.3f} pp")
    print(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)")
    if mode == "--harness" or not hs["pass"]:
        sys.exit(0 if hs["pass"] else 1)

    res = run()
    gates = gates_and_selection(res)
    g, sel = gates["arms"], gates["selection"]
    pre, win, boot = res["pre"], res["windows"], res["bootstrap"]
    ctrl = win[GATE_CAPITAL]

    out = {
        "campaign": "gain_band_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "candidates": list(CANDIDATES),
            "neighbours": NEIGHBOURS, "rule": RULE, "gain_tiers": [[t, b] for t, b in GAIN_TIERS],
            "b_up": B_UP, "budget_ceiling_try": BUDGET_CEILING_TRY,
            "signals": "production R2 (equity_leveraged_trend_v1 1.2.0): E2 2-of-3 on price closes "
                       "(SMA200, 252-session price return > 0, close > 126-session max/min midpoint) "
                       "+ early re-entry at 0.5 x vt while E2 off and close > SMA50, vol20 < its "
                       "252-session median and close > close 20 sessions ago; exit early below SMA50",
            "sizing": "vt = min(1, 0.35/vol20) on synthetic QLD*, cap 0.98",
            "instrument": "synthetic QLD* daily-reset 2x on QQQ total return, expense 0.95%/yr, "
                          "financing DTB3 + {base 0.0, calibrated 0.75%/yr}",
            "cost_per_side": COST_PER_SIDE, "cost_stress_m": M_STRESS,
            "main_window": pre["window"], "start": START,
            "after_tax": {"engine": "tax_engine.py (byte copy of tax_harvest_v2/tax_engine.py)",
                          "financing": "base", "m": M, "windows": TAX_WINDOWS,
                          "capitals": TAX_CAPITALS, "gate_capital": GATE_CAPITAL,
                          "bootstrap": {"paths": TAX_BOOT_PATHS, "seed": SEED, "block": BLOCK,
                                        "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI}},
            "pre_bootstrap": {"paths": PRE_BOOT_PATHS, "seed": SEED, "block": BLOCK,
                              "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI},
            "oos": {"windows": list(OOS_WINDOWS), "financing": "calibrated", "m": M, "rule": "R-USD",
                    "markets": MARKETS, "gspc": [OOS_GSPC_FIRST, OOS_GSPC_LAST]},
            "instruments": {"files": INSTRUMENT_FILES, "included": list(INSTRUMENTS),
                            "excluded": list(EXCLUDED_INSTRUMENTS), "rule": "R-USD",
                            "quality_rule": "range_vol_v2: > 5% invalid/flat bars -> excluded"},
            "gates": {"pre": "ewma_band_v1 G1-G9 (G7 = six OOS windows, G8 = pooled M1-M5 bootstrap)",
                      "AT": "after-tax CAGR > R2 in all three windows at $100k",
                      "B1/B2": "median delta >= 0 and share > 0 >= 0.55, $100k",
                      "F3": f"share of the 21 instruments with delta max DD <= +{GATE_INSTR_MAX_DD_PP} pp "
                            f">= {GATE_INSTR_MIN_SHARE:.0%}",
                      "P": "the selected arm's neighbours each pass AT"},
        },
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "reproduction_checks": hs["reproduction_checks"],
                    "after_tax_anchors": hs["after_tax_anchors"],
                    "bootstrap_control_medians": hs["bootstrap_control"]},
        "pre_tax_main": pre,
        "pre_tax_main_r_usd": res["pre_usd"],
        "after_tax_windows": win,
        "cost_stress_m4": res["cost"],
        "oos": res["oos"],
        "instruments": res["instruments"],
        "quality": res["quality"],
        "bootstrap_e1": boot,
        "bootstrap_e2_oos": res["oos_bootstrap"],
        "gates": gates,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = fmt_pct
    w = pre["window"]
    L = [f"gain_band_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
         f"control = production R2 | arms = C0 + A1 (gain-scaled band) + A2 (band + bracket budget, "
         f"15 pp override) + A3 (10 pp override)",
         f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} checks outside tolerance); "
         f"C0 engine == simulate_band max|diff| "
         f"{hs['reproduction_checks']['c0_engine_equals_simulate_band']['max_abs_nav_diff']:.2e}", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ P1 P2 P3 turn/yr | boot E1 median, median(arm-C0) | "
                 f"m=1 CAGR | blocked band/budget")
        for arm in ARMS:
            a = pre[md]["m2"][arm]
            b = boot["pre"][md][arm]
            sp = a["subperiods"]
            L.append(f"  {arm:4s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} {a['turnover_annual']:5.2f} | "
                     f"{p(b['cagr_median'])} {b['diff_vs_C0_median_pp']:+.3f}pp | "
                     f"{p(pre[md]['m1'][arm]['cagr'])} | {a['blocked_band']:5d} {a['blocked_budget']:5d}")
        L.append("")
    L.append("pre-tax main window under the R-USD rule (fx = 1, no indexation; base, m = 2) - reported:")
    for arm in ARMS:
        a = res["pre_usd"]["base"][arm]
        c_ = res["pre_usd"]["base"][CONTROL]
        L.append(f"  {arm:4s} {p(a['cagr'])} {p(a['max_dd'])} turn/yr {a['turnover_annual']:5.2f} "
                 f"delta vs C0 {(a['cagr'] - c_['cagr']) * 100:+7.3f}pp blocked {a['blocked_band']:5d} "
                 f"{a['blocked_budget']:5d}")
    L.append("")
    for cl in ("10k", "100k", "1M"):
        L.append(f"after tax, progressive 2026 tariff, liquidated, ${cl} (base financing, m = 2):")
        L.append("  window                 arm    pre-tax   after-tax  d vs R2   unliquid   maxDD    tax USD     "
                 "sells/yr  blocked b/bud")
        for wn in TAX_WINDOWS:
            c0 = ctrl[wn][CONTROL]["after_tax_cagr"]
            for arm in ARMS:
                r = win[cl][wn][arm]
                L.append(f"  {wn:20s} {arm:4s} {p(r['pre_tax_cagr'])} {p(r['after_tax_cagr'])} "
                         f"{(r['after_tax_cagr'] - c0) * 100:+8.3f}pp {p(r['after_tax_cagr_unliquidated'])} "
                         f"{p(r['max_dd_after_tax'])} {r['tax_usd']:11,.0f} {r['sells_per_year']:9.2f} "
                         f"{r['blocked_band']:5d}/{r['blocked_budget']:5d}")
        L.append("")
    L.append(f"cost stress m = {M_STRESS} (doubled cost), after tax at $100k, base financing:")
    L.append("  window                 arm    pre-tax m4  after-tax m4  d vs R2   maxDD    blocked")
    for wn in TAX_WINDOWS:
        c_ = res["cost"][wn]
        for arm in ARMS:
            r = c_[arm]
            L.append(f"  {wn:20s} {arm:4s} {p(r['pre_tax_cagr'])} {p(r['after_tax_cagr'])} "
                     f"{(r['after_tax_cagr'] - c_[CONTROL]['after_tax_cagr']) * 100:+8.3f}pp "
                     f"{p(r['max_dd_after_tax'])} {r['postponements']:6d}")
    L.append("")
    L.append("OOS windows (^GSPC 1957-1999 + M1-M5), pre-tax, calibrated, m = 2, R-USD rule:")
    L.append("  window              arm    CAGR       maxDD      d CAGR     d maxDD    blocked")
    for wn in OOS_WINDOWS:
        for arm in ARMS:
            r = res["oos"][wn][arm]
            dc = "" if arm == CONTROL else f"{r['delta_cagr_pp']:+8.2f}pp"
            dd = "" if arm == CONTROL else f"{r['delta_max_dd_pp']:+8.2f}pp"
            L.append(f"  {wn:19s} {arm:4s} {p(r['cagr'])} {p(r['max_dd'])} {dc:>11s} {dd:>11s} "
                     f"{r['postponements']:6d}")
    L.append("  G7 per arm: median d CAGR, windows with dCAGR >= -0.5pp, windows with dDD <= +3pp:")
    for arm in CANDIDATES:
        d = g[arm]["G7_detail"]
        L.append(f"    {arm:4s} median {d['median_cagr_diff_pp']:+7.3f}pp "
                 f"{d['windows_cagr_ge_minus_0_5pp']}/6 {d['windows_dd_within_3pp']}/6 -> "
                 f"{'PASS' if g[arm]['G7'] else 'FAIL'}")
    L.append(f"  G8 pooled OOS bootstrap (M1-M5, {OOS_PATHS_PER_MARKET} paths each) median d: "
             + "  ".join(f"{a} {res['oos_bootstrap']['pooled'][a]['diff_vs_C0_median_pp']:+.3f}pp"
                         for a in CANDIDATES))
    L.append("")
    L.append("instruments (gate F3): 21 quality-passing ETFs, own E2+R2, own 2x leg, calibrated, m = 2, R-USD:")
    L.append("  arm    median d CAGR  share dCAGR>0  n dDD<=+3pp  share  worst d maxDD  gate")
    for arm in CANDIDATES:
        f3 = g[arm]["F3_detail"]
        L.append(f"  {arm:4s} {f3['median_delta_cagr_pp']:+12.3f}pp {f3['share_cagr_gt_0'] * 100:12.1f}% "
                 f"{f3['n_dd_within']:10d}/{f3['n_instruments']} {f3['share_dd_within_3pp'] * 100:7.1f}% "
                 f"{f3['worst_delta_max_dd_pp']:+11.2f}pp ({f3['worst_instrument']}) "
                 f"{'PASS' if g[arm]['F3'] else 'FAIL'}")
    L.append(f"  excluded by the range_vol_v2 quality rule: "
             + ", ".join(f"{k} (bad {res['quality'][k]['bad_frac'] * 100:.1f}%)" for k in EXCLUDED_INSTRUMENTS))
    L.append("")
    L.append(f"clean bootstrap E1 (block-20, {PRE_BOOT_PATHS} pre-tax paths / {TAX_BOOT_PATHS} after-tax paths, "
             f"{TAX_BOOT_LO}..{TAX_BOOT_HI}, fixed FX/Yi-UFE):")
    L.append("  mode          arm    median CAGR  median d vs R2   mean d   share>0   p05 / p95")
    for md in FIN:
        for arm in ARMS:
            v = boot["pre"][md][arm]
            L.append(f"  pre-{md:9s} {arm:4s} {p(v['cagr_median'])} {v['diff_vs_C0_median_pp']:+13.3f}pp "
                     f"{v['diff_vs_C0_mean_pp']:+8.3f} {v['share_gt_0'] * 100:8.1f}% "
                     f"{v['p05_delta_pp']:+5.2f}/{v['p95_delta_pp']:+5.2f}")
    for cl in ("100k", "1M"):
        for mode, label in (("progressive", "B1-recent"), ("flat", "B2-flat")):
            e = boot["tax"][cl][mode]
            L.append(f"  {label:12s} C0 median {p(e['C0']['cagr_median'])}")
            for arm in CANDIDATES:
                v = e[f"{arm}_vs_{CONTROL}"]
                L.append(f"  {label:12s} {arm:4s} {p(e[arm]['cagr_median'])} {v['median_delta_cagr_pp']:+13.3f}pp "
                         f"{v['mean_delta_cagr_pp']:+8.3f} {v['share_gt_0'] * 100:8.1f}% "
                         f"{v['p05_delta_cagr_pp']:+5.2f}/{v['p95_delta_cagr_pp']:+5.2f}  (${cl})")
    L.append("")
    L.append("gates (pre-tax G1-G9 vs R2; AT = after-tax > R2 in all three windows at $100k; "
             "B1/B2 = median d >= 0 and share>0 >= 55%; F3 = dDD <= +3pp on >= 80% of the 21 ETFs; "
             "P = the selected arm's neighbours pass AT):")
    keys = [f"{x}_{md}" for x in ("G1", "G2", "G3", "G5", "G6") for md in FIN] + ["G4", "G7", "G8", "G9", "AT", "B1", "B2", "F3"]
    for arm in CANDIDATES:
        q = g[arm]
        L.append(f"  {arm:4s} " + " ".join(f"{k}={'P' if q[k] else 'F'}" for k in keys)
                 + f" P={'P' if q['P'] else 'F'} -> {'PASS' if q['eligible'] else 'FAIL'}")
    L.append("")
    L.append(f"plateau: " + "  ".join(f"{a} neighbours {q} -> {'P' if gates['plateau'][a] else 'F'}"
                                      for a, q in ((a, g[a]['P_neighbours']) for a in CANDIDATES)))
    L.append(f"selected: {sel}  (rule: {sel and gates['selection']['rule']})")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
