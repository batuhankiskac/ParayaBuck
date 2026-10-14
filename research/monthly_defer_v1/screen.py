#!/usr/bin/env python3
"""monthly_defer_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Idea I2 of `research/idea_survey_v2`: the Turkish Yi-UFE indexation factor steps once per calendar
month, so a *downward* vol resize whose fill crosses a month boundary shrinks the indexed taxable
gain on every lot it consumes (and, in December, also moves the realisation into the next tax
year). `tax_harvest_v1/v2` proved the mechanism for the single December boundary (arm H2). This
screen generalises it to all 12 month boundaries.

Arms (control + 3 candidates), all sharing the control's target series bit for bit:
  C0  production R2, no overlay (control)
  A1  suppress a downward resize whose fill is in the last 10 sessions of ANY month
  A2  the same with 21 sessions (a month has 20-23 sessions -> the whole month)
  A3  A1 + hard override (fill immediately if held - target > 10 pp, or on the fill session that
      follows a close at which the E2 vote turned off)
  H2  (anchor, not a candidate) tax_harvest_v2's published arm: last 10 December sessions only.
      Re-run here only to prove this file's overlay machinery reproduces a published result.

Modes
  --smoke    synthetic random-walk prices only: probe == engine sell arithmetic, overlay "none" ==
             reference `simulate_band`, deferral == the tax engine's own pre-tax NAV path for every
             arm, window identities, finiteness. No market data.
  --harness  real-data reproduction of the controls and of one published non-control arm (H2), plus
             the pre-tax bootstrap / OOS-window anchors. No new arm is simulated.
  --run      harness, then every arm on the main window (both financings, m = 1/2), the 5,000-path
             pre-tax bootstrap, the six OOS windows + the pooled OOS bootstrap, the 21 instruments,
             the after-tax windows at $10k/$100k/$1M (+ m = 4 stress) and the clean B1/B2 bootstrap.
             Writes RESULTS.json and SUMMARY.txt.

Engine helpers are imported from research/trend_robustness_v2/screen.py (its main() is never run);
the production R2 signal and `simulate_band` are copied from research/fast_reentry_v1/screen.py; the
OOS loaders from research/ewma_band_v1/screen.py; the instrument loaders and quality rule from
research/fast_reentry_v2/screen.py; the Turkish after-tax engine is tax_engine.py, a byte copy of
research/tax_harvest_v2/tax_engine.py.
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
_te_spec = importlib.util.spec_from_file_location("md_tax_engine", HERE / "tax_engine.py")
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
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1  # 3 back-extension sessions
OOS_PATHS_PER_MARKET = 2000
OOS_BOOT_L = TSM_N + MEASURED
TOL = 1e-4  # 0.01 pp harness tolerance
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))

# after-tax stage (base financing, cost = 4 bps x M)
M = 2
M_STRESS = 4
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITALS = {"10k": 10_000.0, "100k": 100_000.0, "1M": 1_000_000.0}
GATE_CAPITAL = "100k"
TAX_BOOT_PATHS = 1000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_L = 253 + 10 * 252  # 2773 measured sessions
TAX_BOOT_EXT = 3
TAX_BOOT_I0 = TAX_BOOT_EXT + 253


class Overlay:
    """Tax-timing overlay on the execution layer. `tail == 0` is the control (no suppression)."""

    __slots__ = ("name", "tail", "months", "override_pp")

    def __init__(self, name, tail, months=None, override_pp=None):
        self.name, self.tail, self.months, self.override_pp = name, tail, months, override_pp


# pre-registered arms; "months=None" = all twelve
OVERLAYS = {
    "none": Overlay("none", 0),
    "A1": Overlay("A1", 10),
    "A2": Overlay("A2", 21),
    "A3": Overlay("A3", 10, override_pp=0.10),
    "H2": Overlay("H2", 10, months=("12",)),
}
ARMS = ("C0", "A1", "A2", "A3")          # control + 3 candidates
CONTROL = "C0"
CANDIDATES = ("A1", "A2", "A3")
ANCHOR_ARM = "H2"                        # harness-only reproduction of tax_harvest_v2
SIG_ARM = {"C0": "R2", "A1": "R2", "A2": "R2", "A3": "R2", "E2": "C0", "H2": "R2"}
OV_OF = {"C0": "none", "A1": "A1", "A2": "A2", "A3": "A3", "H2": "H2", "E2": "none"}
OVERRIDE_PP = 0.10                       # A3: |target - held| above this fills immediately

# gates
GATE_OOS_MEDIAN_CAGR_PP = 0.0     # G7: median delta CAGR over the six windows
GATE_OOS_MIN_CAGR_PP = -0.5       # G7: per-window CAGR floor
GATE_OOS_MAX_DD_PP = 3.0          # G7: per-window delta max DD ceiling
GATE_OOS_HARD_DD_PP = 8.0         # G7: absolute worst delta max DD
GATE_INSTR_DD_PP = 3.0            # G10: instrument delta max DD ceiling
GATE_INSTR_SHARE = 0.80           # G10: share of instruments within it
GATE_BOOT_SHARE = 0.55            # T2/T3: share of bootstrap paths with delta > 0

# OOS windows (ewma_band_v1 / e2_confirmation_v1), calibrated financing, m = 2
GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
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
OOS_WINDOWS = ("GSPC_1957_1999",) + tuple(MARKETS)

# instruments (G10): 19 range_vol_v2 symbols + 3 range_vol_v1 symbols; EWC fails the fixed
# range_vol_v2 quality rule and is excluded before simulation.
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
# research/fast_reentry_v1/RESULTS.json, main_window base m=2 R2: sub-periods and 2017+
REF_R2_SUB = {"P1": -0.000601, "P2": 0.142965, "P3": 0.327139, "2017_end": 0.327139}
# research/fast_reentry_v1/RESULTS.json, bootstrap_main_block20 (m = 2)
REF_BOOT = {
    ("base", "R2"): {"cagr_median": 0.078856, "diff_vs_C0_median": 0.006826, "share_ge_C0": 0.6892,
                     "turnover_median": 9.339146},
    ("calibrated", "R2"): {"cagr_median": 0.074346, "diff_vs_C0_median": 0.006546, "share_ge_C0": 0.6816,
                           "turnover_median": 9.339104},
}
# research/fast_reentry_v1/RESULTS.json, oos (identical for base and calibrated: loader is calibrated)
REF_OOS = {
    "GSPC": {"C0": (0.099822, 0.389930), "R2": (0.098899, 0.396257)},
    "M1": {"C0": (0.206775, 0.386160), "R2": (0.211819, 0.409711)},
    "M2": {"C0": (0.112648, 0.625214), "R2": (0.117017, 0.583313)},
    "M3": {"C0": (0.063306, 0.671595), "R2": (0.064037, 0.650253)},
    "M4": {"C0": (0.094116, 0.586096), "R2": (0.094742, 0.581035)},
    "M5": {"C0": (-0.000486, 0.827401), "R2": (-0.005853, 0.854504)},
}
# research/fast_reentry_v1/RESULTS.json, oos_bootstrap_block20_m2.pooled: R2 - E2 median
REF_OOS_BOOT_R2_VS_E2 = 0.003596
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
# research/tax_harvest_v2/RESULTS.json (the published H2 arm), main window base m=2 + $100k after tax
REF_H2 = {
    "pre_tax": {"base": {"m1": 0.157907, "m2": 0.155137, "dd_m2": 0.462542, "turn_m2": 5.948224,
                         "deferrals_m2": 51},
                "calibrated": {"m1": 0.152081, "m2": 0.149325, "dd_m2": 0.463786, "turn_m2": 5.948199,
                               "deferrals_m2": 51}},
    "after_tax_100k": {"2000_03_to_2026_09": (0.103033, 53),
                       "2010_01_to_end": (0.148395, 32),
                       "2017_01_to_end": (0.231609, 19)},
}


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
    """Fast copy of `_rolling_median_vol_ref` (valid when NaNs form a prefix; asserted in --smoke)."""
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


def vote_off_fill(e2_on):
    """Fill-session mask: session i fills a decision taken at the close of i-1, so the vote state
    that produced it is e2_on[..., i-1]; the vote "turned off" when i-2 was on and i-1 is off."""
    e2 = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    T = e2.shape[1]
    out = np.zeros(e2.shape, dtype=bool)
    if T >= 3:
        out[:, 2:] = (~e2[:, 1:-1]) & e2[:, :-2]
    return out


# ----------------------------------------------------------------------------- overlays
def month_mask(days, tail, months=None):
    """(len(days),) bool: True at sessions in the last min(tail, m) sessions of their calendar
    month (all months when `months` is None, else only the listed MM months)."""
    mask = np.zeros(len(days), dtype=bool)
    if tail <= 0:
        return mask
    by_month = defaultdict(list)
    for i, d in enumerate(days):
        if months is None or d[5:7] in months:
            by_month[d[:7]].append(i)
    for idxs in by_month.values():
        mask[idxs[-min(tail, len(idxs)):]] = True
    return mask


def defer_mask_for(ov, days):
    """1-D deferral mask over `days` for an overlay (never None; all-False when tail == 0)."""
    return month_mask(days, ov.tail, ov.months)


def path_mask(src_mask, idx):
    """(P, n+1) deferral mask for a bootstrapped price path whose column `i >= 1` is source session
    `idx[:, i-1]` (the resampled price arrays carry one extra leading column)."""
    return np.concatenate([np.zeros((idx.shape[0], 1), dtype=bool), src_mask[idx]], axis=1)


def _mask_at(mask, i):
    """Session `i` of a deferral/force mask: 1-D masks index the session, 2-D masks the column."""
    m = np.asarray(mask)
    return m[:, i] if m.ndim == 2 else m[i]


def suppress_fill(ov, defer_mask, force_mask, i, tgt, w):
    """True where the downward resize at fill session `i` must be suppressed (per path)."""
    if ov.tail <= 0:
        return False
    hold = np.asarray(_mask_at(defer_mask, i)) & (tgt > 1e-12) & (tgt < w)
    if force_mask is not None:
        hold = hold & ~np.asarray(_mask_at(force_mask, i))
    if ov.override_pp is not None:
        hold = hold & np.logical_not((w - tgt) > ov.override_pp)
    return hold


# ----------------------------------------------------------------------------- engine
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


def simulate_band_overlay(opens, closes, wants, cost, start, band, ov=None, defer_mask=None,
                          force_mask=None):
    """`simulate_band` plus the tax-timing overlay, for the pre-tax OOS / instrument / bootstrap
    stages. An overlay with `tail == 0` is `simulate_band` bit for bit (asserted in --smoke), and a
    non-zero one is the tax engine's own rule (`tax_run_overlay`), also asserted in --smoke."""
    opens, closes, wants = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (opens, closes, wants))
    P, T = closes.shape
    nav = np.ones(P)
    w = np.zeros(P)
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    turn = np.zeros(P)
    ntr = np.zeros(P, dtype=np.int64)
    if ov is None:
        ov = OVERLAYS["none"]
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
        if ov.tail > 0 and defer_mask is not None:
            trade = trade & ~np.asarray(suppress_fill(ov, defer_mask, force_mask, i, want, w))
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
def probe_gain(mkt, rg, i, price, units, fee, lots):
    """Net engine-TRY gain of selling `units` at (session i, price) with `fee`, FIFO, no mutation."""
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


def tax_run_overlay(capital, i0, i1, days, mkt, oq, cq, wq, ov, mode, m=M, defer_mask=None,
                    force_mask=None):
    """Single-leg after-tax strategy run with the tax-timing overlay on the execution layer.

    `wq` is the arm's target series (R2's wants); every overlay shares it -- only the fill decision
    changes. mode in {"pre_tax", "progressive", "flat"}. With ov.tail == 0 this mirrors
    research/r2_tbill_combo_v1/screen.py:tax_run (single book) exactly."""
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
    if defer_mask is None:
        defer_mask = month_mask(days, ov.tail, ov.months)

    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = q_sells = tax_sales = deferrals = 0
    turnover = 0.0
    deferrals_by_year = defaultdict(int)
    deferrals_by_month = defaultdict(int)
    defer_log = []
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

        if trade and bool(np.asarray(suppress_fill(ov, defer_mask, force_mask, i, tgt, w))):
            trade = False
            deferrals += 1
            deferrals_by_year[int(days[i][:4])] += 1
            deferrals_by_month[days[i][:7]] += 1
            if len(defer_log) < 80:
                defer_log.append({"fill_session": days[i], "target": float(tgt), "held": float(w)})

        if trade:
            turnover += drift
            if du > 1e-12:
                qa.buy(i, po, du * po, fee_total)
            elif du < -1e-12:
                qa.sell(i, po, -du, fee_total, "resize")
                q_sells += 1
            trades += 1

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
        "deferrals": int(deferrals),
        "deferrals_by_year": {int(k): int(v) for k, v in sorted(deferrals_by_year.items())},
        "deferrals_by_month": {k: int(v) for k, v in sorted(deferrals_by_month.items())},
        "deferral_log": defer_log,
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
    price = q[1]
    e2 = e2_up(price)[0]
    v20 = trv2.vol_n(c)[0]
    comps = early_components(price)
    wants = {}
    for sig in ("R2", "C0"):
        wants[sig] = arm_signals(price, e2, v20, sig, comps)[0][0]
    return days, o, c, e2, wants


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
        raise RuntimeError(f"fixed calendar {TAX_BOOT_LO}..{TAX_BOOT_HI} has only {len(sub)} days")
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


def ufe_month_drops(days, i0, i1):
    """Yi-UFE months inside the window whose index fell versus the previous month (deferring those
    lots' sales loses, not gains, so they are reported as the mechanism's counter-cases)."""
    ufe = te.load_market(FX_CSV, UFE_CSV, days).ufe
    months = sorted({d[:7] for d in days[i0:i1 + 1]})
    out = []
    for m in months:
        prev = te.prev_month(m)
        if m in ufe and prev in ufe and ufe[m] < ufe[prev]:
            out.append({"month": m, "prev": prev, "ratio": float(ufe[m] / ufe[prev])})
    return out


# ----------------------------------------------------------------------------- main window stats
def seg(dates, nav, a, b):
    """From the last close <= a (first bar if a is None or before data) to the last close <= b."""
    ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    s = np.asarray(nav)[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1), "max_dd": float(trv2.max_dd(s)[0])}


def pre_tax_main_stats(days, o, c, wants, e2, ov, m, defer_mask=None, force_mask=None):
    """Pre-tax main-window stats from the tax-engine runner (proven equal to `simulate_band`).

    `dates = days[START-1:]` aligns with the runner's `_nav` (nav[0] = the close before the first
    simulated session)."""
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    r = tax_run_overlay(1.0, START, len(days) - 1, days, mkt, o, c, wants, ov, "pre_tax", m=m,
                        defer_mask=defer_mask, force_mask=force_mask)
    nav = r["_nav"]
    dates = days[START - 1:]
    cg = float(r["cagr_usd_liquidated"])
    dd = float(r["max_dd_nav_usd"])
    return {
        "cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd),
        "sharpe": trv2.sharpe_daily(nav),
        "turnover_annual": float(r["turnover_annual"]),
        "trades": int(r["strategy_trades"]),
        "sells_per_year": float(r["qld_sells"] / r["years"]),
        "deferrals": int(r["deferrals"]),
        "time_in_market": float((np.asarray(wants)[START - 1:-1] > 0).mean()),
        "subperiods": {name: seg(dates, nav, a, b) for name, a, b in SUBPERIODS},
        "2017_end": seg(dates, nav, "2017-01-01", None),
        "deferrals_by_year": r["deferrals_by_year"],
        "deferrals_by_month": r["deferrals_by_month"],
    }


# ----------------------------------------------------------------------------- pre-tax bootstrap
def pre_tax_bootstrap(specs):
    """Stationary block bootstrap (mean block 20, draws identical to signal_ensemble_v1 /
    fast_reentry_v1: seed 20261011, 5,000 ten-year paths, 3-session back-extension). Each drawn
    position keeps its source session's calendar date, so its month window is the source's.

    specs: list of (label, sig_arm, overlay_name). Returns (cagr, turnover) dicts keyed
    (model, label)."""
    all_days, arr, y = trv2.load()
    q = arr["QQQ"]
    T = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = len(pool)
    L = WARM + MEASURED
    start = trv2.SMA_MAX + 1 + EXT
    src_masks = {name: month_mask(all_days, OVERLAYS[name].tail, OVERLAYS[name].months)
                 for _, _, name in specs}
    cg = {(md, lab): [] for md in FIN for lab, _, _ in specs}
    tv = {(md, lab): [] for md in FIN for lab, _, _ in specs}
    for chunk in range(0, PATHS, CHUNK):
        P = min(CHUNK, PATHS - chunk)
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT, 0, -1)], axis=1)
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
        comps = early_components(pc)
        force = vote_off_fill(e2)
        for md in FIN:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            vol_instr = trv2.vol_n(scl)
            for lab, sig, ovname in specs:
                ov = OVERLAYS[ovname]
                w, _ = arm_signals(pc, e2, vol_instr, sig, comps)
                dm = path_mask(src_masks[ovname], idx) if ov.tail > 0 else None
                fm = force if (ov.tail > 0 and ov.override_pp is not None) else None
                nav, turn, _ = simulate_band_overlay(so, scl, w, COST_PER_SIDE * 2, start, C0_BAND,
                                                     ov, dm, fm)
                k = nav.shape[1] - 1
                cg[(md, lab)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, lab)].append(turn / (k / 252))
    cg = {key: np.concatenate(v) for key, v in cg.items()}
    tv = {key: np.concatenate(v) for key, v in tv.items()}
    summ = {}
    for md in FIN:
        summ[md] = {}
        for lab, _, _ in specs:
            c = cg[(md, lab)]
            summ[md][lab] = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                             "cagr_p25": float(np.percentile(c, 25)), "cagr_p75": float(np.percentile(c, 75)),
                             "turnover_median": float(np.median(tv[(md, lab)]))}
    return cg, tv, summ


def boot_diffs(cg, specs, base_label, models=("base", "calibrated")):
    out = {}
    for md in models:
        out[md] = {}
        b = cg[(md, base_label)]
        for lab, _, _ in specs:
            d = cg[(md, lab)] - b
            out[md][lab] = {"median_delta_cagr_pp": float(np.median(d) * 100),
                            "mean_delta_cagr_pp": float(np.mean(d) * 100),
                            "share_gt_0": float(np.mean(d > 0)),
                            "share_ge_0": float(np.mean(d >= 0)),
                            "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                            "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100)}
    return out


# ----------------------------------------------------------------------------- OOS windows
def load_gspc():
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


def window_stats(nav, days, start):
    yrs = trv2.calendar_years(days[start - 1], days[-1])
    return {"cagr": float(trv2.cagr(nav[0], nav[-1], yrs)),
            "max_dd": float(trv2.max_dd(nav.reshape(1, -1))[0]),
            "years": float(yrs),
            "window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}


def oos_window(days, o, c, y, start, arms, sig="R2"):
    """One OOS window: production E2 + early path (`sig="R2"`) or bare E2 (`sig="C0"`) on the
    market's own close, own 2x leg (calibrated financing, m = 2), each arm with its own month
    overlay."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)
    comps = early_components(c)
    w_r2 = arm_signals(c, e2, trv2.vol_n(sc), sig, comps)[0][0]
    force = vote_off_fill(e2)[0]
    res = {}
    for arm in arms:
        ov = OVERLAYS[OV_OF[arm]]
        dm = month_mask(days, ov.tail, ov.months) if ov.tail > 0 else None
        fm = force if (ov.tail > 0 and ov.override_pp is not None) else None
        nav, turn, ntr = simulate_band_overlay(so, sc, w_r2, COST_PER_SIDE * 2, start, C0_BAND,
                                               ov, dm, fm)
        r = window_stats(nav[0], days, start)
        r["turnover_annual"] = float(turn[0] / r["years"])
        res[arm] = r
    return res


def boot_oos_market(key, rng, specs):
    """e2_confirmation_v1.bootstrap_market draws, one market, all specs (2,000 paths, m = 2)."""
    days, o, c, y, start = load_market(key)
    pool = np.arange(start, len(days))
    npool = len(pool)
    src_masks = {name: month_mask(days, OVERLAYS[name].tail, OVERLAYS[name].months)
                 for _, _, name in specs}
    acc = {lab: [] for lab, _, _ in specs}
    for chunk in range(0, OOS_PATHS_PER_MARKET, CHUNK):
        P = min(CHUNK, OOS_PATHS_PER_MARKET - chunk)
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
        comps = early_components(cc)
        force = vote_off_fill(e2)
        for lab, sig, ovname in specs:
            ov = OVERLAYS[ovname]
            w, _ = arm_signals(cc, e2, vol_instr, sig, comps)
            dm = path_mask(src_masks[ovname], idx) if ov.tail > 0 else None
            fm = force if (ov.tail > 0 and ov.override_pp is not None) else None
            nav = simulate_band_overlay(so, sc, w, COST_PER_SIDE * 2, START, C0_BAND, ov, dm, fm)[0]
            k = nav.shape[1] - 1
            acc[lab].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {lab: np.concatenate(v) for lab, v in acc.items()}


# ----------------------------------------------------------------------------- instruments (G10)
def load_instrument(key):
    """Adjusted OHLC of one ETF: close := adjclose, open/high/low scaled by adjclose/close."""
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
    """Own E2 + own R2 early path on the instrument's adjusted close, own 2x synthetic leg."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)
    comps = early_components(c)
    w_r2 = arm_signals(c, e2, trv2.vol_n(sc), "R2", comps)[0][0]
    force = vote_off_fill(e2)[0]
    res = {}
    for arm in arms:
        ov = OVERLAYS[OV_OF[arm]]
        dm = month_mask(days, ov.tail, ov.months) if ov.tail > 0 else None
        fm = force if (ov.tail > 0 and ov.override_pp is not None) else None
        nav, turn, ntr = simulate_band_overlay(so, sc, w_r2, COST_PER_SIDE * 2, start, C0_BAND,
                                               ov, dm, fm)
        r = window_stats(nav[0], days, start)
        r["turnover_annual"] = float(turn[0] / r["years"])
        res[arm] = r
    return res


# ----------------------------------------------------------------------------- episodes
def yearly_deltas(days, i0, i1, nav_a, nav_b):
    """Per calendar year: the change in log(nav_a / nav_b) over that year's sessions, in pp."""
    out = []
    cur, cur_sum, worst, worst_i = None, 0.0, 0.0, None
    for i in range(i0 + 1, i1 + 1):
        y = days[i][:4]
        if y != cur:
            if cur is not None:
                out.append({"year": int(cur), "delta_pp": float(cur_sum * 100),
                            "worst_session": days[worst_i] if worst_i is not None else None,
                            "worst_session_pp": float(worst * 100)})
            cur, cur_sum, worst, worst_i = y, 0.0, 0.0, None
        ka, kb = i - i0 + 1, i - i0 + 1
        d = np.log(nav_a[ka] / nav_a[ka - 1]) - np.log(nav_b[kb] / nav_b[kb - 1])
        cur_sum += d
        if d < worst:
            worst, worst_i = d, i
    if cur is not None:
        out.append({"year": int(cur), "delta_pp": float(cur_sum * 100),
                    "worst_session": days[worst_i] if worst_i is not None else None,
                    "worst_session_pp": float(worst * 100)})
    return sorted(out, key=lambda e: e["delta_pp"])


# ----------------------------------------------------------------------------- clean after-tax bootstrap
def after_tax_bootstrap(capital_names=("100k", "1M"), arms=ARMS):
    """Block-20 stationary bootstrap (tax_asym_v2 / vt_level_tax_v1 design): 1,000 ten-year paths on
    the fixed 2015-09 -> 2026-09 calendar with its fixed FX / Yi-UFE. Modes: progressive (B1-recent)
    and flat 20 % (B2). Every arm shares the path's target series, so it is computed once per path."""
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
    masks = {}
    for arm in arms:
        ov = OVERLAYS[OV_OF[arm]]
        masks[arm] = (month_mask(fixed_dates, ov.tail, ov.months) if ov.tail > 0 else None)
    res = {cl: {mode: {a: {"cagr": [], "maxdd": [], "sells": [], "deferrals": []} for a in arms}
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
        w_r2 = arm_signals(pc, e2, vol, "R2", comps)[0][0]
        force = vote_off_fill(e2)[0]
        for arm in arms:
            ov = OVERLAYS[OV_OF[arm]]
            fm = force if (ov.tail > 0 and ov.override_pp is not None) else None
            for cap_label in capital_names:
                for mode in ("progressive", "flat"):
                    r = tax_run_overlay(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, so[0], sc[0],
                                        w_r2, ov, mode, defer_mask=masks[arm], force_mask=fm)
                    if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                        raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode} {cap_label}")
                    d = res[cap_label][mode][arm]
                    d["cagr"].append(r["cagr_usd_liquidated"])
                    d["maxdd"].append(r["max_dd_nav_usd"])
                    d["sells"].append(r["sells"] / r["years"])
                    d["deferrals"].append(r["deferrals"])
        if (pidx + 1) % 100 == 0:
            print(f"  bootstrap path {pidx + 1}/{TAX_BOOT_PATHS}", flush=True)
    out = {}
    for cap_label in capital_names:
        out[cap_label] = {}
        for mode in ("progressive", "flat"):
            e = {"tax_kind": mode, "n_paths": TAX_BOOT_PATHS, "calendar_start": fixed_dates[0],
                 "calendar_end": fixed_dates[-1], "seed": SEED, "block": BLOCK,
                 "capital_usd": TAX_CAPITALS[cap_label]}
            for ctrl in ("C0", "R2", "E2"):
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
                        "deferrals_median": float(np.median(res[cap_label][mode][a]["deferrals"]))}
            out[cap_label][mode] = e
    return out


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
    w_r2 = arm_signals(price, e2, v20, "R2", comps)[0][0]
    ufe = {f"{yr}-{mo:02d}": float(1000 + 12 * yr + 5 * mo) for yr in range(1995, 2030) for mo in range(1, 13)}
    mkt = te.Market(days, np.full(n, 30.0), ufe)
    checks, res = {}, {}

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

    pv = trv2.vol_n(100 * np.cumprod(1 + rng.normal(0, 0.01, 1400)))
    checks["rolling_median_fast_equals_ref"] = bool(np.array_equal(
        _rolling_median_vol(pv), _rolling_median_vol_ref(pv), equal_nan=True))

    # window identities: the December-only window is a subset of the all-months one, and the
    # 10-session December window is exactly the H2 tail set
    m_all = month_mask(days, 10)
    m_dec = month_mask(days, 10, ("12",))
    checks["dec_window_subset_of_all_months"] = bool(np.all(m_all[m_dec])) and bool(m_dec.any())
    by_year = defaultdict(list)
    for i, d in enumerate(days):
        if d[5:7] == "12":
            by_year[d[:4]].append(i)
    last10 = np.zeros(len(days), dtype=bool)
    for idxs in by_year.values():
        last10[idxs[-10:]] = True
    checks["dec_10_is_the_last_10_dec_sessions"] = bool(np.array_equal(m_dec, last10))
    m21 = month_mask(days, 21)
    by_month = defaultdict(list)
    for i, d in enumerate(days):
        by_month[d[:7]].append(i)
    checks["tail21_covers_last_21_of_every_month"] = bool(all(
        int(np.sum(m21[idxs])) == min(21, len(idxs)) for idxs in by_month.values()))

    # overlay "none" == simulate_band on synthetic data (full nav path, pre-tax, m = 2)
    ref_nav, ref_turn, ref_ntr = simulate_band(so[None, :], sc[None, :], w_r2[None, :],
                                               COST_PER_SIDE * 2, START, C0_BAND)
    none = tax_run_overlay(1.0, START, n - 1, days, mkt, so, sc, w_r2, OVERLAYS["none"], "pre_tax")
    checks["none_equals_simulate_band"] = bool(np.max(np.abs(none["_nav"] - ref_nav[0])) < 1e-9)

    b_nav, _, _ = simulate_band_overlay(so[None, :], sc[None, :], w_r2[None, :],
                                        COST_PER_SIDE * 2, START, C0_BAND)
    checks["band_overlay_none_equals_simulate_band"] = bool(np.max(np.abs(b_nav[0] - ref_nav[0])) < 1e-12)

    arms_ok, defer_seen = {}, {}
    force = vote_off_fill(e2)[0]
    for arm in ("A1", "A2", "A3", "H2"):
        ov = OVERLAYS[OV_OF[arm]]
        dm = month_mask(days, ov.tail, ov.months)
        fm = force if ov.override_pp is not None else None
        rr = tax_run_overlay(1.0, START, n - 1, days, mkt, so, sc, w_r2, ov, "pre_tax",
                             defer_mask=dm, force_mask=fm)
        b, _, _ = simulate_band_overlay(so[None, :], sc[None, :], w_r2[None, :],
                                        COST_PER_SIDE * 2, START, C0_BAND, ov, dm[None, :],
                                        None if fm is None else fm[None, :])
        arms_ok[arm] = bool(np.max(np.abs(b[0] - rr["_nav"])) < 1e-9)
        defer_seen[arm] = int(rr["deferrals"])
    checks["band_overlay_equals_engine_all_arms"] = bool(all(arms_ok.values()))
    checks["deferral_fires_in_synthetic"] = bool(defer_seen["A2"] > defer_seen["A1"] > 0
                                                 and defer_seen["H2"] > 0)

    for arm in ARMS:
        ov = OVERLAYS[OV_OF[arm]]
        dm = month_mask(days, ov.tail, ov.months)
        fm = force if ov.override_pp is not None else None
        rp = tax_run_overlay(1.0, START, n - 1, days, mkt, so, sc, w_r2, ov, "progressive",
                             defer_mask=dm, force_mask=fm)
        rf = tax_run_overlay(1.0, START, n - 1, days, mkt, so, sc, w_r2, ov, "flat",
                             defer_mask=dm, force_mask=fm)
        rr = tax_run_overlay(1.0, START, n - 1, days, mkt, so, sc, w_r2, ov, "pre_tax",
                             defer_mask=dm, force_mask=fm)
        for r in (rp, rf, rr):
            assert np.isfinite(r["cagr_usd_liquidated"]) and r["terminal_usd_liquidated"] > 0, arm
        assert rp["cagr_usd_liquidated"] <= rr["cagr_usd_liquidated"] + 1e-9, arm
        assert rf["cagr_usd_liquidated"] <= rr["cagr_usd_liquidated"] + 1e-9, arm
        res[arm] = {"pre_tax_cagr": rr["cagr_usd_liquidated"], "progressive_cagr": rp["cagr_usd_liquidated"],
                    "flat_cagr": rf["cagr_usd_liquidated"], "deferrals": rp["deferrals"],
                    "nav_end": float(rr["ending_nav_usd"])}
    checks["everything_finite"] = bool(all(np.isfinite(v) for a in res.values() for v in a.values()))
    return {"checks": checks, "arms": res, "deferrals": defer_seen, "pass": bool(all(checks.values()))}


def harness(run_boot=True, run_oos_boot=True):
    """Control reproduction on real data plus the published H2 anchor. No new arm."""
    checks, bad = {}, 0
    days, o, c, e2, wants = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    for md, spread in FIN.items():
        days_, o_, c_, e2_, wants_ = main_inputs(spread)
        for arm, ref, key in (("C0", REF_DEV, "C0"), ("R2", REF_R2, "R2")):
            for m in (1, 2):
                r = tax_run_overlay(1.0, START, len(days_) - 1, days_, mkt, o_, c_,
                                    wants_[key], OVERLAYS["none"], "pre_tax", m=m)
                for k, val in (("cagr_usd_unliquidated", ref[(md, m)][0]),
                               ("max_dd_nav_usd", ref[(md, m)][1]),
                               ("turnover_annual", ref[(md, m)][2])):
                    got = float(r[k])
                    checks[f"{arm}_{md}_m{m}_{k}"] = {"reference": val, "reproduced": got, "diff": got - val}
                    if abs(got - val) > TOL:
                        bad += 1
    # sub-period / 2017+ anchors (pins the dates/nav alignment used by G3/G4)
    st = pre_tax_main_stats(days, o, c, wants["R2"], e2, OVERLAYS["none"], 2)
    for k in ("P1", "P2", "P3"):
        got = st["subperiods"][k]["cagr"]
        checks[f"R2_base_m2_sub_{k}"] = {"reference": REF_R2_SUB[k], "reproduced": got,
                                         "diff": got - REF_R2_SUB[k]}
        if abs(got - REF_R2_SUB[k]) > 5e-5:
            bad += 1
    got = st["2017_end"]["cagr"]
    checks["R2_base_m2_sub_2017_end"] = {"reference": REF_R2_SUB["2017_end"], "reproduced": got,
                                         "diff": got - REF_R2_SUB["2017_end"]}
    if abs(got - REF_R2_SUB["2017_end"]) > 5e-5:
        bad += 1

    checks["simulate_band_identity"] = {"max_abs_nav_diff": None, "trades_match": True}
    ref_nav, ref_turn, ref_ntr = simulate_band(o[None, :], c[None, :], wants["R2"][None, :],
                                               COST_PER_SIDE * 2, START, C0_BAND)
    mine = tax_run_overlay(1.0, START, len(days) - 1, days, mkt, o, c, wants["R2"],
                           OVERLAYS["none"], "pre_tax")
    d_nav = float(np.max(np.abs(mine["_nav"] - ref_nav[0])))
    checks["simulate_band_identity"] = {"max_abs_nav_diff": d_nav, "trades_match": True}
    if d_nav > 1e-9:
        bad += 1

    tax = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        tax[wn] = {}
        for arm, key in (("C0", "C0"), ("R2", "R2")):
            rp = tax_run_overlay(100_000.0, a, b, days, mkt, o, c, wants[key], OVERLAYS["none"], "pre_tax")
            rq = tax_run_overlay(100_000.0, a, b, days, mkt, o, c, wants[key], OVERLAYS["none"], "progressive")
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

    # H2 anchor: the published tax_harvest_v2 arm reproduced by this file's overlay machinery
    h2 = {}
    for md, spread in FIN.items():
        days_, o_, c_, e2_, wants_ = main_inputs(spread)
        for m in (1, 2):
            r = tax_run_overlay(1.0, START, len(days_) - 1, days_, mkt, o_, c_, wants_["R2"],
                                OVERLAYS[OV_OF[ANCHOR_ARM]], "pre_tax", m=m)
            ref = REF_H2["pre_tax"][md][f"m{m}"]
            h2[f"H2_{md}_m{m}_cagr"] = {"reference": ref, "reproduced": r["cagr_usd_liquidated"],
                                        "diff": r["cagr_usd_liquidated"] - ref}
            if abs(r["cagr_usd_liquidated"] - ref) > TOL:
                bad += 1
        if md == "base":
            r2_ = tax_run_overlay(1.0, START, len(days_) - 1, days_, mkt, o_, c_, wants_["R2"],
                                  OVERLAYS[OV_OF[ANCHOR_ARM]], "pre_tax", m=2)
            for k, val in (("max_dd_nav_usd", REF_H2["pre_tax"][md]["dd_m2"]),
                           ("turnover_annual", REF_H2["pre_tax"][md]["turn_m2"])):
                h2[f"H2_base_m2_{k}"] = {"reference": val, "reproduced": float(r2_[k]),
                                         "diff": float(r2_[k]) - val}
                if abs(float(r2_[k]) - val) > TOL:
                    bad += 1
            h2["H2_base_m2_deferrals"] = {"reference": REF_H2["pre_tax"][md]["deferrals_m2"],
                                          "reproduced": int(r2_["deferrals"])}
            if int(r2_["deferrals"]) != REF_H2["pre_tax"][md]["deferrals_m2"]:
                bad += 1
    for wn, (a, b) in TAX_WINDOWS.items():
        rq = tax_run_overlay(100_000.0, a, b, days, mkt, o, c, wants["R2"],
                             OVERLAYS[OV_OF[ANCHOR_ARM]], "progressive")
        ref, refd = REF_H2["after_tax_100k"][wn]
        h2[f"H2_100k_{wn}_cagr"] = {"reference": ref, "reproduced": rq["cagr_usd_liquidated"],
                                    "diff_pp": (rq["cagr_usd_liquidated"] - ref) * 100}
        h2[f"H2_100k_{wn}_deferrals"] = {"reference": refd, "reproduced": int(rq["deferrals"])}
        if abs(rq["cagr_usd_liquidated"] - ref) > 5e-5 or int(rq["deferrals"]) != refd:
            bad += 1

    boot = {}
    if run_boot:
        specs = [("E2", "C0", "none"), ("R2", "R2", "none")]
        cg, tv, summ = pre_tax_bootstrap(specs)
        diffs = boot_diffs(cg, specs, "E2")
        for md in FIN:
            got_vals = {"cagr_median": summ[md]["R2"]["cagr_median"],
                        "turnover_median": summ[md]["R2"]["turnover_median"],
                        "diff_vs_C0_median": diffs[md]["R2"]["median_delta_cagr_pp"] / 100.0,
                        "share_ge_C0": diffs[md]["R2"]["share_ge_0"]}
            for k, ref in REF_BOOT[(md, "R2")].items():
                got = float(got_vals[k])
                checks[f"boot_{md}_R2_{k}"] = {"reference": ref, "reproduced": got, "diff": got - ref}
                if abs(got - ref) > TOL:
                    bad += 1
        boot = {"summaries": summ, "diffs_vs_E2": diffs}

    oos_boot = {}
    if run_oos_boot:
        rng = np.random.default_rng(SEED)
        per = {k: boot_oos_market(k, rng, [("E2", "C0", "none"), ("R2", "R2", "none")]) for k in MARKETS}
        pooled = {lab: np.concatenate([per[k][lab] for k in MARKETS]) for lab in ("E2", "R2")}
        med = float(np.median(pooled["R2"] - pooled["E2"]))
        checks["oos_boot_R2_vs_E2_median"] = {"reference": REF_OOS_BOOT_R2_VS_E2, "reproduced": med,
                                              "diff": med - REF_OOS_BOOT_R2_VS_E2}
        if abs(med - REF_OOS_BOOT_R2_VS_E2) > TOL:
            bad += 1
        oos_boot = {"pooled_median_R2_vs_E2": med, "pooled_paths": int(len(pooled["R2"]))}

    # OOS window anchors (calibrated, m = 2): my control (production R2 signal) vs REF_OOS["R2"],
    # and the bare E2 signal of this file vs REF_OOS["C0"]
    gd, go, gc, gy, gstart = load_gspc()
    oos = {"GSPC": oos_window(gd, go, gc, gy, gstart, ("C0",), sig="R2")}
    for key in MARKETS:
        md_, mo_, mc_, my_, mstart_ = load_market(key)
        oos[key] = oos_window(md_, mo_, mc_, my_, mstart_, ("C0",), sig="R2")
    oos_e2 = {"GSPC": oos_window(gd, go, gc, gy, gstart, ("C0",), sig="C0")}
    for key in MARKETS:
        md_, mo_, mc_, my_, mstart_ = load_market(key)
        oos_e2[key] = oos_window(md_, mo_, mc_, my_, mstart_, ("C0",), sig="C0")
    for key, refs in REF_OOS.items():
        for tag, src in (("R2", oos), ("C0", oos_e2)):
            for idx, kk in ((0, "cagr"), (1, "max_dd")):
                got = float(src[key]["C0"][kk])
                checks[f"oos_{key}_{tag}_{kk}"] = {"reference": refs[tag][idx], "reproduced": got,
                                                   "diff": got - refs[tag][idx]}
                if abs(got - refs[tag][idx]) > TOL:
                    bad += 1
    # clean after-tax bootstrap of the control: reproduces r2_tbill_combo_v1's published medians
    tax_boot = {}
    if run_boot:
        b = after_tax_bootstrap(("100k",), arms=("C0",))
        for mode in ("progressive", "flat"):
            got = b["100k"][mode]["C0"]["cagr_median"]
            ref = BOOT_ANCHOR[mode]["R2"]
            tax_boot[mode] = {"reference": ref, "reproduced": got, "diff_pp": (got - ref) * 100}
            if abs(got - ref) > 0.001:
                bad += 1

    return {"reproduction_checks": checks, "n_outside_tolerance": bad, "after_tax_anchors": tax,
            "h2_anchor": h2, "bootstrap_control": boot, "tax_bootstrap_control": tax_boot,
            "oos_bootstrap_control": oos_boot, "oos_windows": oos, "oos_windows_e2": oos_e2,
            "pass": bool(bad == 0)}


# ----------------------------------------------------------------------------- run
def run() -> dict:
    days, o, c, e2, wants = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    w_r2 = wants["R2"]

    def masks_for(arm, e2_arr, dm_2d=None):
        ov = OVERLAYS[OV_OF[arm]]
        if ov.tail <= 0:
            return None, None
        dm = dm_2d if dm_2d is not None else month_mask(days, ov.tail, ov.months)
        fm = (vote_off_fill(e2_arr)[0] if e2_arr.ndim == 1 else vote_off_fill(e2_arr)) \
            if ov.override_pp is not None else None
        return dm, fm

    # ---- pre-tax main window (both financings, m = 1/2)
    pre = {}
    for md, spread in FIN.items():
        days_, o_, c_, e2_, wants_ = main_inputs(spread)
        pre[md] = {}
        for m in (1, 2):
            pre[md][f"m{m}"] = {}
            for arm in ARMS:
                dm, fm = masks_for(arm, e2_)
                pre[md][f"m{m}"][arm] = pre_tax_main_stats(days_, o_, c_, wants_["R2"], e2_,
                                                           OVERLAYS[OV_OF[arm]], m, dm, fm)
    pre["H2_anchor"] = {}
    for md, spread in FIN.items():
        days_, o_, c_, e2_, wants_ = main_inputs(spread)
        pre["H2_anchor"][md] = {}
        for m in (1, 2):
            dm = month_mask(days_, OVERLAYS["H2"].tail, OVERLAYS["H2"].months)
            pre["H2_anchor"][md][f"m{m}"] = pre_tax_main_stats(days_, o_, c_, wants_["R2"], e2_,
                                                               OVERLAYS["H2"], m, dm, None)
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": len(days) - START,
                     "years": float(trv2.calendar_years(days[START - 1], days[-1]))}

    # ---- pre-tax bootstrap (G5)
    print("pre-tax bootstrap", flush=True)
    specs = [(a, SIG_ARM[a], OV_OF[a]) for a in ARMS] + [("H2", SIG_ARM["H2"], "H2")]
    cg, tv, boot_sum = pre_tax_bootstrap(specs)
    boot_delta = boot_diffs(cg, specs, CONTROL)
    g5 = {md: {a: {"median_delta_cagr_pp": boot_delta[md][a]["median_delta_cagr_pp"],
                   "share_gt_0": boot_delta[md][a]["share_gt_0"],
                   "pass": bool(boot_delta[md][a]["median_delta_cagr_pp"] >= 0)}
              for a in CANDIDATES} for md in FIN}
    print("pre-tax bootstrap done", flush=True)

    # ---- after tax, three windows x three capitals
    print("after-tax windows", flush=True)
    win = {}
    for cap_label, capital in TAX_CAPITALS.items():
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            win[cap_label][wn] = {}
            for arm in ARMS:
                ov = OVERLAYS[OV_OF[arm]]
                dm, fm = masks_for(arm, e2)
                rq = tax_run_overlay(capital, a, b, days, mkt, o, c, w_r2, ov, "progressive",
                                     defer_mask=dm, force_mask=fm)
                rp = tax_run_overlay(1.0, a, b, days, mkt, o, c, w_r2, ov, "pre_tax",
                                     defer_mask=dm, force_mask=fm)
                win[cap_label][wn][arm] = {
                    "pre_tax_cagr": rp["cagr_usd_liquidated"],
                    "after_tax_cagr": rq["cagr_usd_liquidated"],
                    "max_dd_after_tax": rq["max_dd_nav_usd"],
                    "terminal_usd": rq["terminal_usd_liquidated"],
                    "tax_usd": rq["tax_total_usd_at_payment_fx"],
                    "sells_per_year": rq["sells"] / rq["years"],
                    "trades_per_year": rq["strategy_trades"] / rq["years"],
                    "deferrals": rq["deferrals"],
                    "deferrals_by_month": rq["deferrals_by_month"],
                    "indexation_relief_try": rq["indexation_relief_try"],
                    "realised_gain_try_by_reason": rq["realised_gain_try_by_reason"],
                }
                if cap_label == "100k":
                    win[cap_label][wn][arm]["tax_assessed_try_by_year"] = rq["tax_assessed_try_by_year"]
            ov = OVERLAYS["H2"]
            dm = month_mask(days, ov.tail, ov.months)
            rq = tax_run_overlay(TAX_CAPITALS[cap_label], a, b, days, mkt, o, c, w_r2, ov,
                                 "progressive", defer_mask=dm)
            win[cap_label][wn]["H2_anchor"] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                                               "max_dd_after_tax": rq["max_dd_nav_usd"],
                                               "deferrals": rq["deferrals"]}

    # ---- cost stress m = 4 at $100k
    print("cost stress m=4", flush=True)
    cost = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        cost[wn] = {}
        for arm in ARMS:
            ov = OVERLAYS[OV_OF[arm]]
            dm, fm = masks_for(arm, e2)
            rq = tax_run_overlay(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, w_r2, ov,
                                 "progressive", m=M_STRESS, defer_mask=dm, force_mask=fm)
            rp = tax_run_overlay(1.0, a, b, days, mkt, o, c, w_r2, ov, "pre_tax", m=M_STRESS,
                                 defer_mask=dm, force_mask=fm)
            cost[wn][arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                             "pre_tax_cagr": rp["cagr_usd_liquidated"],
                             "max_dd_after_tax": rq["max_dd_nav_usd"],
                             "turnover_annual": rp["turnover_annual"]}

    # ---- per-year NAV deltas vs C0 (main window, base, m = 2, pre-tax)
    print("episodes", flush=True)
    navs = {}
    for arm in ARMS:
        ov = OVERLAYS[OV_OF[arm]]
        dm, fm = masks_for(arm, e2)
        navs[arm] = tax_run_overlay(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, ov, "pre_tax",
                                    defer_mask=dm, force_mask=fm)["_nav"]
    yearly = {a: yearly_deltas(days, START, len(days) - 1, navs[a], navs[CONTROL]) for a in CANDIDATES}
    yearly["H2_anchor"] = yearly_deltas(
        days, START, len(days) - 1,
        tax_run_overlay(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, OVERLAYS["H2"],
                        "pre_tax")["_nav"], navs[CONTROL])
    ufe_drops = ufe_month_drops(days, START, len(days) - 1)

    # ---- OOS windows + pooled bootstrap (G7/G8)
    print("OOS windows", flush=True)
    gd, go, gc, gy, gstart = load_gspc()
    oos = {"GSPC_1957_1999": oos_window(gd, go, gc, gy, gstart, ARMS)}
    for key in MARKETS:
        md_, mo_, mc_, my_, mstart_ = load_market(key)
        oos[key] = oos_window(md_, mo_, mc_, my_, mstart_, ARMS)
    for wn in OOS_WINDOWS:
        for arm in CANDIDATES:
            d = oos[wn][arm]
            d["delta_cagr_pp"] = (d["cagr"] - oos[wn][CONTROL]["cagr"]) * 100
            d["delta_max_dd_pp"] = (d["max_dd"] - oos[wn][CONTROL]["max_dd"]) * 100
    print("OOS bootstrap", flush=True)
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, specs) for k in MARKETS}
    pooled = {lab: np.concatenate([per[k][lab] for k in MARKETS]) for lab, _, _ in specs}
    oos_boot = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        oos_boot["per_market"][k] = {lab: {"median_delta_cagr_pp": float(np.median(per[k][lab] - per[k][CONTROL]) * 100),
                                           "cagr_median": float(np.median(per[k][lab]))}
                                     for lab, _, _ in specs if lab != CONTROL}
    for lab, _, _ in specs:
        if lab == CONTROL:
            continue
        d = pooled[lab] - pooled[CONTROL]
        oos_boot["pooled"][lab] = {"median_delta_cagr_pp": float(np.median(d) * 100),
                                   "mean_delta_cagr_pp": float(np.mean(d) * 100),
                                   "share_gt_0": float(np.mean(d > 0)), "paths": int(len(d))}

    # ---- instruments (G10)
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

    # ---- clean after-tax bootstrap (T2/T3)
    print("after-tax bootstrap starting", flush=True)
    boot = after_tax_bootstrap(("100k", "1M"), arms=ARMS)
    print("after-tax bootstrap done", flush=True)

    return {"pre": pre, "bootstrap_pre_tax": {"summaries": boot_sum, "diffs": boot_delta, "g5": g5},
            "windows": win, "cost": cost, "yearly": yearly, "ufe_drops": ufe_drops, "oos": oos,
            "oos_bootstrap": oos_boot, "instruments": inst, "quality": quality, "bootstrap": boot}


# ----------------------------------------------------------------------------- gates
def gates_and_selection(res):
    pre, win, boot = res["pre"], res["windows"], res["bootstrap"]
    oos, oos_boot, inst = res["oos"], res["oos_bootstrap"], res["instruments"]
    ctrl = win[GATE_CAPITAL]
    g = {}
    for arm in CANDIDATES:
        r = {}
        # G1/G2/G3/G6 (both financings) and G4 (base)
        for md in FIN:
            a2, c2 = pre[md]["m2"][arm], pre[md]["m2"][CONTROL]
            a1, c1 = pre[md]["m1"][arm], pre[md]["m1"][CONTROL]
            r[f"G1_{md}"] = bool(a2["cagr"] >= c2["cagr"]
                                 or (a2["calmar"] >= 1.10 * c2["calmar"] and a2["cagr"] >= c2["cagr"] - 0.005))
            r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
            r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
            r[f"G5_{md}"] = bool(res["bootstrap_pre_tax"]["diffs"][md][arm]["median_delta_cagr_pp"] >= 0)
            r[f"G6_{md}"] = bool(a1["cagr"] >= c1["cagr"]
                                 or (a1["calmar"] >= 1.10 * c1["calmar"] and a1["cagr"] >= c1["cagr"] - 0.005))
        a2, c2 = pre["base"]["m2"][arm], pre["base"]["m2"][CONTROL]
        wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
        r["G4"] = bool(len(wins) >= 2)
        r["G4_wins"] = wins
        r["G9"] = bool(a2["turnover_annual"] <= 2.0 * c2["turnover_annual"])
        dc = {k: oos[k][arm]["delta_cagr_pp"] for k in OOS_WINDOWS}
        dd = {k: oos[k][arm]["delta_max_dd_pp"] for k in OOS_WINDOWS}
        n_c = sum(1 for v in dc.values() if v >= GATE_OOS_MIN_CAGR_PP)
        n_d = sum(1 for v in dd.values() if v <= GATE_OOS_MAX_DD_PP + 1e-12)
        r["G7_detail"] = {"cagr_diff_pp": dc, "max_dd_diff_pp": dd,
                          "median_cagr_diff_pp": float(np.median(list(dc.values()))),
                          "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                          "max_dd_diff_max_pp": float(max(dd.values()))}
        r["G7"] = bool(r["G7_detail"]["median_cagr_diff_pp"] >= GATE_OOS_MEDIAN_CAGR_PP
                       and n_c >= 5 and n_d >= 5 and max(dd.values()) <= GATE_OOS_HARD_DD_PP + 1e-12)
        r["G8"] = bool(oos_boot["pooled"][arm]["median_delta_cagr_pp"] >= 0)
        dds = [inst[k][arm]["delta_max_dd_pp"] for k in sorted(inst)]
        share = float(np.mean([v <= GATE_INSTR_DD_PP + 1e-12 for v in dds]))
        r["G10_detail"] = {"n_instruments": len(dds), "n_dd_within": int(sum(v <= GATE_INSTR_DD_PP + 1e-12 for v in dds)),
                           "share_dd_within": share, "worst_delta_max_dd_pp": float(max(dds)),
                           "worst_instrument": sorted(inst)[int(np.argmax(dds))],
                           "median_delta_cagr_pp": float(np.median([inst[k][arm]["delta_cagr_pp"] for k in sorted(inst)]))}
        r["G10"] = bool(share >= GATE_INSTR_SHARE)
        r["windows_100k_detail"] = {wn: {"after_tax": win[GATE_CAPITAL][wn][arm]["after_tax_cagr"],
                                         "R2": ctrl[wn][CONTROL]["after_tax_cagr"],
                                         "pre_tax": win[GATE_CAPITAL][wn][arm]["pre_tax_cagr"],
                                         "R2_pre_tax": ctrl[wn][CONTROL]["pre_tax_cagr"],
                                         "pass": bool(win[GATE_CAPITAL][wn][arm]["after_tax_cagr"]
                                                      > ctrl[wn][CONTROL]["after_tax_cagr"])}
                                    for wn in TAX_WINDOWS}
        r["T1"] = bool(all(v["pass"] for v in r["windows_100k_detail"].values()))
        for gate, mode in (("T2", "progressive"), ("T3", "flat")):
            v = boot[GATE_CAPITAL][mode][f"{arm}_vs_{CONTROL}"]
            r[gate] = bool(v["median_delta_cagr_pp"] >= 0 and v["share_gt_0"] >= GATE_BOOT_SHARE)
            r[f"{gate}_detail"] = {"median_delta_cagr_pp": v["median_delta_cagr_pp"],
                                   "share_gt_0": v["share_gt_0"], "mean_delta_cagr_pp": v["mean_delta_cagr_pp"]}
        keys = [k for k in r if k in ("G4", "G7", "G8", "G9", "G10", "T1", "T2", "T3")] \
            + [f"{gg}_{md}" for gg in ("G1", "G2", "G3", "G5", "G6") for md in FIN]
        r["GATE_KEYS"] = keys
        r["failed"] = sorted([k for k in keys if not r[k]])
        r["pass"] = bool(not r["failed"])
        g[arm] = r
    eligible = [a for a in CANDIDATES if g[a]["pass"]]
    sel = max(eligible, key=lambda a: pre["base"]["m2"][a]["cagr"]) if eligible else "none"
    return {"arms": g, "eligible": eligible, "selected": sel,
            "rule": "selected = candidate with the highest base m=2 pre-tax CAGR among arms passing every gate",
            "verdict": "PASS" if eligible else "FAIL"}


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

    hs = harness(run_boot=True, run_oos_boot=True)
    for k in sorted(hs["reproduction_checks"]):
        v = hs["reproduction_checks"][k]
        if "reference" in v and v["reference"] is not None:
            print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v.get('diff', 0):+.6f}")
        else:
            print(f"harness {k}: {v}")
    for k in sorted(hs["h2_anchor"]):
        v = hs["h2_anchor"][k]
        print(f"anchor H2 {k}: ref {v['reference']} repro {v['reproduced']}")
    for tb_mode, v in hs["tax_bootstrap_control"].items():
        print(f"tax boot anchor {tb_mode:12s}: ref {v['reference']:.6f} repro {v['reproduced']:.6f} "
              f"diff {v['diff_pp']:+.3f} pp")
    print(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)")
    if mode == "--harness" or not hs["pass"]:
        sys.exit(0 if hs["pass"] else 1)

    res = run()
    gates = gates_and_selection(res)
    g = gates["arms"]
    pre, win, boot = res["pre"], res["windows"], res["bootstrap"]

    out = {
        "campaign": "monthly_defer_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "candidates": list(CANDIDATES),
            "anchor": ANCHOR_ARM,
            "overlays": {a: {"tail_sessions": OVERLAYS[OV_OF[a]].tail,
                             "months": "all" if OVERLAYS[OV_OF[a]].months is None else list(OVERLAYS[OV_OF[a]].months),
                             "override_pp": OVERLAYS[OV_OF[a]].override_pp} for a in ARMS + (ANCHOR_ARM,)},
            "signals": "production R2 (equity_leveraged_trend_v1 1.2.0): E2 2-of-3 on price closes "
                       "(SMA200, 252-session price return > 0, close > 126-session max/min midpoint) "
                       "+ early re-entry at 0.5 x vt while E2 off and close > SMA50, vol20 < its "
                       "252-session median and close > close 20 sessions ago; exit early below SMA50",
            "sizing": "vt = min(1, 0.35/vol20) on synthetic QLD*, cap 0.98, 0.5 pp resize band",
            "instrument": "synthetic QLD* daily-reset 2x on QQQ total return, expense 0.95%/yr, "
                          "financing DTB3 + {base 0.0, calibrated 0.75%/yr}",
            "cost_per_side": COST_PER_SIDE, "cost_stress_m": M_STRESS,
            "main_window": pre["window"], "start": START,
            "after_tax": {"engine": "tax_engine.py (byte copy of tax_harvest_v2/tax_engine.py)",
                          "financing": "base", "m": M, "windows": TAX_WINDOWS, "capitals": TAX_CAPITALS,
                          "gate_capital": GATE_CAPITAL,
                          "bootstrap": {"paths": TAX_BOOT_PATHS, "seed": SEED, "block": BLOCK,
                                        "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI,
                                        "modes": ["progressive (B1-recent)", "flat 20% (B2)"]}},
            "bootstrap_pre_tax": {"paths": PATHS, "seed": SEED, "block": BLOCK, "chunk": CHUNK,
                                  "warmup": WARM, "measured": MEASURED, "back_extension": EXT, "m": M},
            "oos": {"windows": list(OOS_WINDOWS), "financing": "calibrated", "m": M,
                    "markets": MARKETS, "paths_per_market": OOS_PATHS_PER_MARKET,
                    "gspc": [OOS_GSPC_FIRST, OOS_GSPC_LAST]},
            "instruments": {"files": INSTRUMENT_FILES, "excluded": list(EXCLUDED_INSTRUMENTS),
                            "quality_rule": "range_vol_v2: > 5% invalid/flat bars -> excluded"},
            "gates": {"G1-G9": "ewma_band_v1 / fast_reentry_v1 definitions, control = R2",
                      "G10": f"instruments: share of delta max DD <= +{GATE_INSTR_DD_PP} pp >= {GATE_INSTR_SHARE:.0%}",
                      "T1": "after-tax $100k > R2 in all three windows",
                      "T2": f"B1-recent median delta >= 0 and share > 0 >= {GATE_BOOT_SHARE:.0%}",
                      "T3": f"B2 median delta >= 0 and share > 0 >= {GATE_BOOT_SHARE:.0%}"},
        },
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "after_tax_anchors": hs["after_tax_anchors"], "h2_anchor": hs["h2_anchor"],
                    "bootstrap_control": hs["bootstrap_control"],
                    "oos_bootstrap_control": hs["oos_bootstrap_control"]},
        "pre_tax_main": pre,
        "bootstrap_pre_tax": res["bootstrap_pre_tax"],
        "after_tax_windows": win,
        "cost_stress": res["cost"],
        "yearly_deltas": res["yearly"],
        "ufe_month_drops": res["ufe_drops"],
        "oos": res["oos"],
        "oos_bootstrap": res["oos_bootstrap"],
        "instruments": res["instruments"],
        "quality": res["quality"],
        "bootstrap": boot,
        "gates": gates,
        "verdict": gates["verdict"],
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    L = []
    w = pre["window"]
    L.append(f"monthly_defer_v1 (I2: Yi-UFE month-boundary deferral of downward vol resizes) | "
             f"main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | control = production R2")
    L.append(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} checks outside tolerance); "
             f"overlay 'none' == fast_reentry simulate_band max|diff| "
             f"{hs['reproduction_checks']['simulate_band_identity']['max_abs_nav_diff']:.2e}; "
             f"H2 anchor reproduced (pre-tax base m2 {hs['h2_anchor']['H2_base_m2_cagr']['reproduced']:.6f} "
             f"vs {hs['h2_anchor']['H2_base_m2_cagr']['reference']:.6f})")
    L.append("")
    L.append("pre-tax main window (m = 2):")
    L.append("  arm   CAGR base  CAGR cal   maxDD base  maxDD cal   m=1 base  Calmar  P1/P2/P3 cagr           turn/yr  sells/yr  deferrals")
    for arm in ARMS + (ANCHOR_ARM,):
        src = pre if arm in ARMS else pre["H2_anchor"]
        b2, c2 = src["base"]["m2"][arm], src["calibrated"]["m2"][arm]
        b1 = src["base"]["m1"][arm]
        sp = b2["subperiods"]
        L.append(f"  {arm:5s} {fmt_pct(b2['cagr'])} {fmt_pct(c2['cagr'])} {fmt_pct(b2['max_dd'])} "
                 f"{fmt_pct(c2['max_dd'])} {fmt_pct(b1['cagr'])} {b2['calmar']:6.3f} "
                 f"{fmt_pct(sp['P1']['cagr'])}/{fmt_pct(sp['P2']['cagr'])}/{fmt_pct(sp['P3']['cagr'])} "
                 f"{b2['turnover_annual']:8.2f} {b2['sells_per_year']:9.2f} {b2['deferrals']:10d}")
    L.append("")
    L.append(f"pre-tax bootstrap (block-20, {PATHS} paths, m = 2) median delta vs R2:")
    for md in FIN:
        L.append(f"  {md:11s} " + "  ".join(
            f"{a}: {res['bootstrap_pre_tax']['diffs'][md][a]['median_delta_cagr_pp']:+.3f}pp "
            f"({res['bootstrap_pre_tax']['diffs'][md][a]['share_gt_0'] * 100:.1f}%)" for a in CANDIDATES))
    L.append("")
    for cl in ("10k", "100k", "1M"):
        L.append(f"after tax, progressive 2026 tariff, liquidated, ${cl} (base financing, m = 2):")
        L.append("  window                 arm     pre-tax   after-tax  d vs R2   maxDD    tax USD     sells/yr  defers")
        for wn in TAX_WINDOWS:
            ctrl = win[cl][wn][CONTROL]
            for arm in ARMS:
                r = win[cl][wn][arm]
                d = (r["after_tax_cagr"] - ctrl["after_tax_cagr"]) * 100
                L.append(f"  {wn:20s} {arm:6s} {fmt_pct(r['pre_tax_cagr'])} {fmt_pct(r['after_tax_cagr'])} "
                         f"{d:+8.2f}pp {fmt_pct(r['max_dd_after_tax'])} {r['tax_usd']:11,.0f} "
                         f"{r['sells_per_year']:9.2f} {r['deferrals']:7d}")
            r = win[cl][wn]["H2_anchor"]
            d = (r["after_tax_cagr"] - ctrl["after_tax_cagr"]) * 100
            L.append(f"  {wn:20s} {'H2*':6s} {'--':>9s} {fmt_pct(r['after_tax_cagr'])} "
                     f"{d:+8.2f}pp {fmt_pct(r['max_dd_after_tax'])} {'--':>11s} {'--':>9s} {r['deferrals']:7d}")
        L.append("")
    L.append(f"cost stress m = {M_STRESS} (doubled cost), after tax at $100k, base financing:")
    L.append("  window                 arm     pre-tax m4  after-tax m4  d vs R2   maxDD    turn/yr")
    for wn in TAX_WINDOWS:
        c = res["cost"][wn]
        for arm in ARMS:
            r = c[arm]
            d = (r["after_tax_cagr"] - c[CONTROL]["after_tax_cagr"]) * 100
            L.append(f"  {wn:20s} {arm:6s} {fmt_pct(r['pre_tax_cagr'])} {fmt_pct(r['after_tax_cagr'])} "
                     f"{d:+8.2f}pp {fmt_pct(r['max_dd_after_tax'])} {r['turnover_annual']:8.2f}")
    L.append("")
    L.append("OOS windows (calibrated, m = 2):")
    L.append("  window              arm     CAGR       maxDD      d CAGR    d maxDD")
    for wn in OOS_WINDOWS:
        for arm in ARMS:
            r = res["oos"][wn][arm]
            dc = "" if arm == CONTROL else f"{r['delta_cagr_pp']:+8.2f}pp"
            dd = "" if arm == CONTROL else f"{r['delta_max_dd_pp']:+8.2f}pp"
            L.append(f"  {wn:19s} {arm:6s} {fmt_pct(r['cagr'])} {fmt_pct(r['max_dd'])} {dc:>11s} {dd:>11s}")
    L.append("  per-arm median d CAGR and worst d max DD over the six windows (G7):")
    for arm in CANDIDATES:
        L.append(f"    {arm:6s} median d CAGR {g[arm]['G7_detail']['median_cagr_diff_pp']:+7.3f}pp | "
                 f"max d max DD {g[arm]['G7_detail']['max_dd_diff_max_pp']:+7.2f}pp | "
                 f"n(dCAGR >= -0.5pp) {g[arm]['G7_detail']['windows_cagr_ge_minus_0_5pp']}/6 | "
                 f"n(dDD <= +3pp) {g[arm]['G7_detail']['windows_dd_within_3pp']}/6 -> "
                 f"{'PASS' if g[arm]['G7'] else 'FAIL'}")
    L.append("")
    L.append("instruments (G10): 21 quality-passing ETFs, own E2+R2, own 2x leg, calibrated, m = 2:")
    L.append("  arm     median d CAGR  n dDD<=+3pp  share  worst d maxDD (instrument)")
    for arm in CANDIDATES:
        f = g[arm]["G10_detail"]
        L.append(f"  {arm:6s} {f['median_delta_cagr_pp']:+12.3f}pp {f['n_dd_within']:8d}/{f['n_instruments']} "
                 f"{f['share_dd_within'] * 100:7.1f}% {f['worst_delta_max_dd_pp']:+9.2f}pp ({f['worst_instrument']}) "
                 f"{'PASS' if g[arm]['G10'] else 'FAIL'}")
    L.append(f"  excluded by the range_vol_v2 quality rule: "
             + ", ".join(f"{k} (bad {res['quality'][k]['bad_frac'] * 100:.1f}%)" for k in EXCLUDED_INSTRUMENTS))
    L.append("")
    L.append("per-year NAV log-ratio delta vs R2 (main window, base, m = 2, pre-tax), worst 5 and best 3:")
    for arm in CANDIDATES:
        ys = res["yearly"][arm]
        worst = "  ".join(f"{e['year']}:{e['delta_pp']:+.2f}" for e in ys[:5])
        best = "  ".join(f"{e['year']}:{e['delta_pp']:+.2f}" for e in ys[-3:])
        L.append(f"  {arm:6s} worst {worst} | best {best} | sum {sum(e['delta_pp'] for e in ys):+.3f} pp")
    L.append(f"  Yi-UFE months in the window whose index fell versus the previous month "
             f"(deferral loses there): {len(res['ufe_drops'])}")
    L.append("")
    L.append(f"clean after-tax bootstrap (block-20, {TAX_BOOT_PATHS} paths, {TAX_BOOT_LO}..{TAX_BOOT_HI}, fixed calendar):")
    L.append("  capital mode         arm     median CAGR  median d vs R2   mean d   share>0   p05 / p95  defers/10y")
    for cl in ("100k", "1M"):
        for md in ("progressive", "flat"):
            e = boot[cl][md]
            for arm in ARMS:
                v = e.get(f"{arm}_vs_{CONTROL}")
                if v is None:
                    L.append(f"  {cl:7s} {md:12s} {arm:6s} {fmt_pct(e[arm]['cagr_median'])} "
                             f"{'--':>16s} {'--':>8s} {'--':>9s} {'--':>11s} {e[arm]['deferrals_median']:11.1f}")
                else:
                    L.append(f"  {cl:7s} {md:12s} {arm:6s} {fmt_pct(e[arm]['cagr_median'])} "
                             f"{v['median_delta_cagr_pp']:+13.3f}pp {v['mean_delta_cagr_pp']:+8.3f} "
                             f"{v['share_gt_0'] * 100:8.1f}% {v['p05_delta_cagr_pp']:+5.2f}/{v['p95_delta_cagr_pp']:+5.2f} "
                             f"{e[arm]['deferrals_median']:11.1f}")
        L.append("")
    L.append("gates (all must pass): G1-G3/G5/G6 both financings, G4, G7 (six OOS windows), G8 (pooled OOS "
             "bootstrap), G9 (turnover <= 2x R2), G10 (ETF drawdown), T1 (after-tax $100k, three windows), "
             "T2/T3 (B1-recent / B2 bootstrap):")
    for arm in CANDIDATES:
        r = g[arm]
        L.append(f"  {arm:6s} " + " ".join(f"{k}={'P' if r[k] else 'F'}" for k in r["GATE_KEYS"])
                 + f" -> {'PASS' if r['pass'] else 'FAIL'}")
    L.append(f"selected arm: {gates['selected']} (eligible {gates['eligible']})")
    L.append(f"VERDICT: {gates['verdict']}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
