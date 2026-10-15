#!/usr/bin/env python3
"""cash_yield_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Idea I5 of docs/research/idea_survey_v4.md: idle cash earns a broker sweep rate instead of zero.
Question: does crediting DTB3 - 0.50 pp (and - 1.00 pp) on the account's uninvested cash -- the
2 % cap buffer plus 0.98 - w -- survive the Turkish tax on interest (menkul sermaye iradi), and
is it worth more after tax than the zero-interest production rule K? The measured precedent is
pre-tax only and under the OLD control (`tbill_sweep_v1`'s report arm A_ALL: +0.68 pp vs E2,
bootstrap median +0.857 pp on 100 % of paths); the ETF form of the same idea was closed after
tax by `r2_tbill_combo_v1`.

  K    production rule (the control): E2's 2-of-3 QQQ vote + R2's 0.5 x vt early re-entry leg,
       vt = min(1, 0.35/vol20), cap 0.98, 0.5 pp resize band, plus the deployed execution layer
       A1u (gain-scaled downsize band 0.5 / 3 / 8 pp at g_frac <= 1 % / <= 5 % / > 5 %, USD FIFO
       gain fraction) + H2 (a December downsize defers to January). Idle cash earns ZERO.
  Z1   K + idle cash earns max(0, DTB3 - 0.50 pp)/yr; the interest is declared as MSI at the
       progressive tariff, no indexation, no declaration threshold, same March/July instalments.
  Z2   as Z1 with the 22,000 x scale(year) TRY declaration bar of GVK 86/1-c applied to the
       year's TRY interest (at or below it: nothing declared; above it: the WHOLE amount).
  Z3   as Z1 with the pessimistic spread DTB3 - 1.00 pp.
  Z1x  REPORT ONLY (not an arm, not gated): Z1 with the interest never taxed -- the upper bound
       if a mali musavir reads the declaration bar as sheltering the amounts modelled here.

Yield mechanics (pre-registered, Alpaca's own documented Bank Sweep mechanics): accrual on the
session's ENDING uninvested cash balance at the one-session-lagged rate rate[i-1]/252 (the same
lag the financing leg uses), credited and compounded on the last session of each calendar month,
plus a forced final credit at the window's last session. The pending (uncredited) accrual is not
NAV until it is credited.

Stages (pre-registered before any non-control arm was simulated on real data):
  pre      pre-tax main window, base/calibrated x m = 1/2/4 plus the R-USD row.
  AT       after tax at $100k (gate capital) and $30k/$300k/$10k/$1M on the three windows.
  B12      clean bootstrap, seed 20261011, block 20, 1,000 paths, $100k (+$1M report-only),
           B1-recent and B2-flat, plus 5,000 pre-tax paths (G5's leg).
  E1       after tax on the 21 quality-passing ETFs under R-INDEX, $100k, calibrated, m = 2.
  E2       rolling January starts 2001..2021, after tax at $100k, base, m = 2 (report only).
  E3       fresh after-tax bootstraps, seed 20261015, blocks 10 and 40, 1,000 paths (report only).
  E5       report only: $10k-$1M after-tax windows, the m = 4 cost stress, decision traces.

Gates (pre-registered; ewma_band_v1's G1-G9 against the control K, plus the after-tax pair):
  G1  m = 2, base and calibrated: CAGR >= K, or Calmar >= 1.1 x K and CAGR >= K - 0.5 pp;
  G2  both financings: max DD <= K + 1.0 pp;
  G3  both: 2017+ CAGR >= K - 1.0 pp;
  G4  CAGR above K in >= 2 of the 3 sub-periods P1 2000-08 / P2 2009-16 / P3 2017-end;
  G5  both: block-20 (seed 20261011, 5,000 pre-tax paths) bootstrap median of arm - K >= 0;
  G6  G1 at m = 1, both financings;
  G7  six OOS windows: median d CAGR >= 0; in >= 5 of 6 d CAGR >= -0.5 pp and d DD <= +3 pp;
      d DD never > +8 pp;
  G8  pooled OOS bootstrap over M1-M5 (2,000 paths each): median d >= 0;
  G9  turnover <= 2 x K;
  AT  after tax at $100k: arm - K >= 0 in ALL THREE windows;
  B1B2 clean bootstrap at $100k, B1-recent (progressive) and B2-flat: median d >= 0 AND
      share > 0 >= 55 %.
Selection (pre-registered): among Z1/Z2/Z3, the one with the highest pre-tax base m = 2 CAGR
among those clearing every gate above (ties: after-tax 2000-window CAGR). If none clears, K
stands. E1/E2/E3 and the OOS per-window rows are reported, not decisive.

--harness reproduces the control K's published cells cell-by-cell (K_ANCHORS.json, copied
mechanically from research/band_h2_combo_v1/RESULTS.json), the production-R2 / pure-E2 anchor
series (fast_reentry_v1, vt_level_tax_v1, e2_integration_v1), C0 == `simulate_band`, and the
report arm A_ALL of tbill_sweep_v1 (the pre-tax cross-check of the yield code path).

--- inherited description (band_h2_combo_v1, whose screen this file is a copy of) ---

This file is research/band_h2_combo_v1/screen.py with (a) the arm set, the gate block and the
per-arm reporting row renamed for this study (control = K, candidates = Z1/Z2/Z3, report arm Z1x),
(b) the cash-yield accrual/credit and its three tax modes added to `engine_run` and `tax_engine`,
and (c) the reference-reproduction block retargeted at K's published cells. Everything else --
the signal construction, the target series, the execution layer (A1u's gain-scaled band and H2's
December window), the tax engine, the stages, the bootstrap draws, the seeds and the OOS/
instrument loaders -- is the parent's code unchanged, which is what lets `--harness` assert K's
published cells and the parent campaigns' anchor series.

Modes
  --smoke    synthetic random-walk prices only, no market data: the rule-gain probes (TRY and USD
             paths) vs the engine's own sell gain, C0 == the reference `simulate_band`, H2 ==
             `simulate_band_overlay` for the tail-10 arm, finiteness, after-tax <= pre-tax, that
             the band and the deferral fire, and (new) that the yield legs are inert at rate 0,
             that Z1x >= Z1 >= K on the same path, that the Z1/Z2/Z3 interest-tax modes do what
             they say, and that the month-end crediting is exact.
  --harness  reproduction on real data: K's published cells against K_ANCHORS.json; production
             R2's pre-tax (15.4766 / 14.8961 %) and after-tax ($100k 10.160 / 14.706 / 22.886 %)
             anchors; the pure-E2 series vs e2_integration_v1; C0 engine == `simulate_band`;
             B1/B2 control medians vs tax_harvest_v2; and the yield code path against
             tbill_sweep_v1's archived report arm A_ALL (pre-tax main window, on the E2 target
             series: 15.35 % CAGR, bootstrap median +0.857 pp).
  --run      harness (deep), then the stages above, then the gates. Writes RESULTS.json,
             SUMMARY.txt.

One engine serves every stage: `engine_run` is a stateful session loop over the Turkish
after-tax account (research/cash_yield_v1/tax_engine.py, the parent's copy plus
CashYieldAccount/CashYieldFlatAccount) with production K's execution layer. The rule's decision
state (the indexed rule gain and its year accumulator) is computed independently of the regime,
so the pre-tax and after-tax runs follow the same trade path. With arm "K" (and the yield legs
off) the engine is exactly research/band_h2_combo_v1's arm "K" -- asserted in --harness.

Engine helpers (synthetic 2x, SMA/TSM/DON, vol, vt, metrics) come from
research/trend_robustness_v2/screen.py (its main() is never run); the production signal
construction, `simulate_band` and the OOS/instrument loaders are the copies used by
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
import os
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

# ---- the arm rule (pre-registered; see PREREGISTRATION.md sections 3 and 4)
# Control K is production `equity_leveraged_trend_v1` 1.2.0 exactly: E2's vote + R2's early leg,
# vt = min(1, 0.35/vol20), cap 0.98, 0.5 pp band, plus A1u's gain-scaled downsize band and H2's
# December deferral. Z1/Z2/Z3 add idle-cash interest at the pre-registered spread and tax mode;
# Z1x is the report-only untaxed upper bound and never appears in a gate.
ARMS = ("K", "Z1", "Z2", "Z3")
REPORT_ARMS = ("Z1x",)
ARMS_ALL = ARMS + REPORT_ARMS
CONTROL = "K"
BASELINE = "K"          # the arm every candidate has to beat (the gates in section 5)
CANDIDATES = ("Z1", "Z2", "Z3")
ALPACA_SPREAD = 0.005   # DTB3 - 0.50 pp (the pre-registered sweep spread of Z1/Z2)
Z3_SPREAD = 0.010       # DTB3 - 1.00 pp (Z3, the pessimistic-spread plateau check)
_RULE_K = {"band": True, "usd_rule": True, "defer": True, "yield": None}
RULE = {
    "K": dict(_RULE_K),
    "Z1": {**_RULE_K, "yield": {"spread": ALPACA_SPREAD, "tax": "none"}},
    "Z2": {**_RULE_K, "yield": {"spread": ALPACA_SPREAD, "tax": "threshold"}},
    "Z3": {**_RULE_K, "yield": {"spread": Z3_SPREAD, "tax": "none"}},
    "Z1x": {**_RULE_K, "yield": {"spread": ALPACA_SPREAD, "tax": "untaxed"}},
}
# reference labels, never arms of this study: production R2 (band off, no deferral), the pure-E2
# series, and the two devices alone (used by --smoke's code-path identities only). All four are
# harness-only; none appears in ARMS, in a stage or in a gate.
RULE["R2"] = {"band": False, "usd_rule": False, "defer": False, "yield": None}
RULE["E2"] = {"band": False, "usd_rule": False, "defer": False, "yield": None}
RULE["A1u"] = {"band": True, "usd_rule": True, "defer": False, "yield": None}
RULE["H2"] = {"band": False, "usd_rule": False, "defer": True, "yield": None}
# the archived report arm A_ALL of tbill_sweep_v1 (pure-E2 targets + idle cash at DTB3 - 0.5 pp):
# the pre-tax cross-check of this study's yield code path, run in --harness only.
RULE["E2y"] = {"band": False, "usd_rule": False, "defer": False,
               "yield": {"spread": ALPACA_SPREAD, "tax": "none"}}
B_UP = C0_BAND                                   # entries / exits / up-resizes keep 0.5 pp
GAIN_TIERS = ((0.01, 0.005), (0.05, 0.03), (math.inf, 0.08))  # g_frac ceiling -> B_down
DEC_TAIL = 10                                    # H2: the last 10 December sessions
YEAR_END = "12"                                  # month prefix of the December sessions

# ---- after-tax stage
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITALS = {"10k": 10_000.0, "30k": 30_000.0, "100k": 100_000.0, "300k": 300_000.0, "1M": 1_000_000.0}
GATE_CAPITAL = "100k"
# The pre-tax runs of the main window and of the clean bootstrap use the gate capital (the
# pre-tax *return* statistics are scale-free; the band and the deferral are scale-free too).
PRE_CAPITAL = TAX_CAPITALS[GATE_CAPITAL]
TAX_BOOT_PATHS = 1_000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_L = 253 + 10 * 252  # 2,773 sessions (253 warm-up + 2,520 measured)
TAX_BOOT_EXT = 3
TAX_BOOT_I0 = TAX_BOOT_EXT + 253
PRE_BOOT_PATHS = 5_000  # the pre-tax leg of the clean bootstrap (E4)

# ---- gates (pre-registered; see PREREGISTRATION.md section 5). ewma_band_v1's G1-G9 against
# the control K, plus the common protocol's after-tax pair.
G1_MARGIN_PP = -0.5          # G1 alternative: Calmar >= G1_CALMAR_MULT x K and CAGR >= K + this
G1_CALMAR_MULT = 1.1
G2_MAX_DD_PP = 1.0           # max DD <= K + this (base and calibrated, m = 2)
G3_MIN_2017_PP = -1.0        # 2017+ CAGR >= K + this (base and calibrated)
G4_MIN_PERIODS = 2           # CAGR above K in >= this many of P1/P2/P3
G5_MIN_MEDIAN_PP = 0.0       # pre-tax clean bootstrap (seed 20261011, block 20): median >= 0
G6_SAME_AS_G1 = True         # G1 re-checked at m = 1
G7_MEDIAN_MIN_PP = 0.0       # six OOS windows: median d CAGR >= this
G7_MAX_WINDOWS_BAD = 1       # in >= 5 of 6 windows d CAGR >= G7_MIN_CAGR_PP and d DD <= +3 pp
G7_MIN_CAGR_PP = -0.5
G7_MAX_DD_PP = 3.0
G7_HARD_DD_PP = 8.0          # d DD never above this in any window
G8_MIN_MEDIAN_PP = 0.0       # pooled OOS bootstrap median d >= 0
G9_MAX_TURNOVER_MULT = 2.0   # turnover <= this x K
AT_MIN_DELTA_PP = 0.0        # after tax at $100k: arm - K >= 0 in all three windows
B_MIN_MEDIAN_PP = 0.0        # clean bootstrap: median (arm - K) >= 0 ...
B_MIN_SHARE = 0.55           # ... and share > 0 >= this (B1-recent and B2-flat)
# ---- reporting stages E1-E3 (the parent campaigns' stages; reported, not decisive)
E1_MIN_SHARE = 0.60            # share of the 21 ETFs with after-tax delta > 0
E1_RINDEX_START = "2000-03-09"  # the TRY calendar's start; E1 start = max(ETF warm-up, this)
E2_START_YEARS = tuple(range(2001, 2022))   # 21 January starts, 2001..2021
E2_MIN_SHARE = 0.70            # parent thresholds, for the reporting block only
E3_SEED = 20261015
E3_BLOCKS = (10, 40)
E3_PATHS = 1_000
E3_MIN_SHARE = 0.55
# ---- published reference evidence
KREF = HERE / "K_ANCHORS.json"   # the control K's published cells (from band_h2_combo_v1)

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
REF_E2 = {  # e2_integration_v1 DEVIATION.json, E2_prod_price_h0 (cagr, max_dd, turnover)
    ("base", 1): (0.1494912807457318, 0.4924710732832198, 5.968939533490137),
    ("base", 2): (0.14674909060373587, 0.4956428339322504, 5.968939533490137),
    ("calibrated", 1): (0.14377164501317918, 0.49333140001226616, 5.968913400107268),
    ("calibrated", 2): (0.14104311139315073, 0.49649783199459774, 5.968913400107268),
}
# production R2 anchors. REF_R2 = research/fast_reentry_v1/RESULTS.json, main_window; TAX_ANCHORS
# = research/vt_level_tax_v1 + r2_tbill_combo_v1 at $100k. The study's control is asserted against
# these (the task's R2 anchors: 15.48 / 14.90 % CAGR, 46.76 % max DD; after tax 10.16 / 14.71 /
# 22.89 %). REF_E2 / TAX_ANCHORS["E2"] are the pure-E2 reference series, not the control.
REF_R2 = {  # (cagr, max_dd, turnover)
    ("base", 1): (0.157538, 0.464143, 5.991522),
    ("base", 2): (0.154766, 0.467584, 5.991522),
    ("calibrated", 1): (0.151719, 0.465364, 5.991496),
    ("calibrated", 2): (0.148961, 0.468797, 5.991496),
}
TAX_ANCHORS = {  # series -> window -> (pre_tax cagr, progressive cagr, progressive max_dd), $100k
    "E2": {
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
BOOT_ANCHOR = {"progressive": {"E2": 0.03079, "R2": 0.035155},
               "flat": {"E2": 0.035201, "R2": 0.039678}}


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
    """Target weights [P, T]. "R2" and "C0" both mean production R2 (E2 + the early re-entry
    path) -- the run's control *is* R2; "E2" is the anchor label for the pure-E2 reference
    series. Any other label raises, so a typo can never silently fall through to the E2 path
    (the gain_band_v1 bug, `arm_signals`' old `else` branch)."""
    if arm not in ("R2", "C0", "E2"):
        raise ValueError(f"unknown arm label {arm!r}")
    if arm == "E2":
        on_eff = np.atleast_2d(np.asarray(e2_on, dtype=bool)).copy()
        factor = np.ones_like(on_eff, dtype=float)
    else:
        on_eff, factor = r2_state(price, e2_on, comps)
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


def december_tail(days, i0, i1, tail):
    """Session indices in the last `tail` December sessions of each calendar year in [i0, i1]
    (tax_harvest_v2/screen.py, verbatim copy; `tail <= 0` -> empty set)."""
    if tail <= 0:
        return set()
    by_year = defaultdict(list)
    for i in range(i0, i1 + 1):
        if days[i][5:7] == YEAR_END:
            by_year[days[i][:4]].append(i)
    out = set()
    for idxs in by_year.values():
        out.update(idxs[-tail:])
    return out


def simulate_band_overlay(opens, closes, wants, cost, start, band, dec_tail=frozenset()):
    """`simulate_band` plus the H2 deferral (tax_harvest_v2/screen.py, verbatim copy), for the
    --smoke identity check: a downward resize (0 < want < held weight at the fill's open) whose
    fill session is in `dec_tail` is not executed. An empty `dec_tail` is `simulate_band` bit for
    bit, and a non-empty one is the engine's arm "H2" (pre-tax, no tax-funding sales)."""
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
        if i in dec_tail:
            trade = trade & ~((want > 1e-12) & (want < w))
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


def rule_sale_usd(i, price, units, fee, lots):
    """(net rule gain, proceeds) in USD of selling `units` at (session i, price), FIFO,
    read-only: the A1u decision state. USD cost basis, no FX, no YI-UFE indexation, no
    per-lot floor; the caller floors the resulting fraction at 0 (`gain_band` treats any
    g_frac <= 1 % identically, so the floor is behavioural no-op -- PREREGISTRATION 4.1).
    The lot book is `BaseAccount`'s: lot = [units, usd_price_with_fee, fx_at_buy, month]."""
    if units <= 0 or price <= 0 or not lots:
        return 0.0, 0.0
    per_unit = (units * price - fee) / units
    proceeds = units * per_unit
    left, gain, k = units, 0.0, 0
    n = len(lots)
    while left > 1e-15 and k < n:
        lot = lots[k]
        take = min(left, lot[0])
        gain += take * per_unit - take * lot[1]
        left -= take
        k += 1
    return gain, proceeds


# ----------------------------------------------------------------------------- the engine
def engine_run(capital, i0, i1, days, mkt, oq, cq, wq, e2on, arm, mode, m=M, rule_index=True,
               trace=False, rate=None):
    """One arm through the Turkish after-tax account, session by session.

    mode in {"pre_tax", "progressive", "flat"}; arm in ARMS (+ the harness-only reference labels
    "R2"/"E2"). Every arm shares the target series `wq` (production R2's); only the fill decision
    and the idle-cash yield differ: entries, exits to zero and up-resizes always fill (0.5 pp
    band), while a discretionary downward resize (`tgt > 0`, `tgt < w`) is subject to the arm's
    band (A1u's gain tiers) and/or its December window (H2: postponed when the fill session is
    one of the last DEC_TAIL December sessions). A downsize between two positive targets is a
    downward resize whatever its size, so the rule also covers the transfer between R2's full
    weight and its half-weight early-re-entry leg. `rule_index` selects the R-INDEX (True) or
    R-USD (False) instantiation of the decision state. With arm "K" this is exactly
    research/band_h2_combo_v1's arm "K".

    Idle-cash yield (arms Z1/Z2/Z3/Z1x): `rate` is the annual decimal rate series aligned to
    `days` (the same series the financing leg consumes); the accrual for session i uses
    `rate[i - 1]` (one session's lag, as `trv2.synthetic_2x` and the archived report arm
    A_ALL do). When `rate is None` and the arm has a yield spec, DTB3 is looked up on `days`."""
    days = list(days)
    if not isinstance(i0, int):
        i0, i1 = bisect.bisect_left(days, i0), bisect.bisect_right(days, i1) - 1
    oq, cq, wq = (np.asarray(x, dtype=float).flatten() for x in (oq, cq, wq))
    e2on = np.asarray(e2on, dtype=bool).flatten()
    cost = COST_PER_SIDE * m
    spec = RULE[arm]
    yspec = spec.get("yield")
    if yspec is not None and rate is None:
        rate = dtb3_on(days)
    if rate is not None:
        rate = np.asarray(rate, dtype=float).flatten()
    cashbox = {"v": float(capital)}
    if mode == "flat":
        qa = (te.CashYieldFlatAccount(mkt, 0.0, cashbox, tax=yspec["tax"]) if yspec is not None
              else te.FlatAccount(mkt, 0.0, cashbox))
        pays = None
    else:
        rg = te.Regime("progressive", "tariff") if mode == "progressive" else te.Regime("pre_tax", "none")
        qa = (te.CashYieldAccount(mkt, rg, 0.0, cashbox, tax=yspec["tax"]) if yspec is not None
              else te.BaseAccount(mkt, rg, 0.0, cashbox))
        pays = te.instalments(days, i0, i1)
    cashbox["v"] = float(capital)

    # the month-end credit sessions (Alpaca: realized on the last business day of the month)
    credit_sessions = set()
    if yspec is not None:
        for k in range(i0, i1 + 1):
            if k == i1 or days[k][:7] != days[k + 1][:7]:
                credit_sessions.add(k)
        spread = yspec["spread"]

    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = q_sells = tax_sales = postponed = 0
    turnover = 0.0
    rule_year = defaultdict(float)
    post_by_year = defaultdict(int)
    blocked_band = deferred = 0
    defer_by_year = defaultdict(int)
    dec_tail = december_tail(days, i0, i1, DEC_TAIL) if spec["defer"] else set()
    prev_tgt = None
    dec = [] if trace else None

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

        # ---- the arm: H2's December window first, then A1u's gain-scaled band, on a
        #      discretionary downward resize (exits to zero and up-resizes always fill)
        blocked = None
        if trade and tgt > 1e-12:
            if spec["defer"] and i in dec_tail and tgt < w:
                trade, blocked = False, "deferred"
            elif du < -1e-12 and spec["band"]:
                if spec["usd_rule"]:
                    gain, proceeds = rule_sale_usd(i, po, -du, fee_total, qa.lots)
                    g_frac = max(0.0, gain) / proceeds if proceeds > 0 else 0.0
                else:
                    gain, proceeds = rule_sale(mkt, i, po, -du, fee_total, qa.lots, rule_index)
                    g_frac = gain / proceeds if proceeds > 0 else 0.0
                if drift <= gain_band(g_frac):
                    trade, blocked = False, "band"

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
                deferred += 1
                defer_by_year[year_i] += 1

        if trace:
            dec.append(0 if not trade else (2 if du < -1e-12 else 1))
        # ---- idle-cash yield: accrue on this session's ENDING cash, credit at month end
        if yspec is not None:
            qa.accrue(i, rate[i - 1] - spread if i > 0 else 0.0)
            if i in credit_sessions:
                qa.credit(i)
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
    mtm = nav[-1]   # i1 is a month-end credit session, so the last accrual is already in nav
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
        "deferred": int(deferred),
        "postponements_by_year": {int(k): int(v) for k, v in sorted(post_by_year.items())},
        "deferrals_by_year": {int(k): int(v) for k, v in sorted(defer_by_year.items())},
        "fees_usd": float(qa.fees), "index_relief_try": float(qa.index_relief_try),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.gain_by_reason.items())},
        "yield_usd_total": float(sum(qa.int_usd_by_year.values()) if yspec is not None else 0.0),
        "yield_try_by_year": ({int(k): float(v) for k, v in sorted(qa.year_int.items())}
                              if yspec is not None else {}),
        "yield_usd_by_year": ({int(k): float(v) for k, v in sorted(qa.int_usd_by_year.items())}
                              if yspec is not None else {}),
        "interest_tax_try_by_year": ({int(k): float(v) for k, v in sorted(qa.extra_tax_try.items())}
                                     if yspec is not None else {}),
        "interest_tax_try_total": float(sum(qa.extra_tax_try.values()) if yspec is not None else 0.0),
        "accrual_sessions": int(qa.accrual_sessions if yspec is not None else 0),
        "_nav": nav,
        "_dec": np.array(dec, dtype=np.int8) if trace else None,
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
        "cagr_liquidated": float(res["cagr_usd_liquidated"]),
        "max_dd": float(trv2.max_dd(nav.reshape(1, -1))[0]),
        "turnover_annual": float(res["turnover_total"] / years),
        "trades": int(res["strategy_trades"]), "sells_per_year": float(res["qld_sells"] / years),
        "postponements": int(res["postponements"]), "blocked_band": int(res["blocked_band"]),
        "deferred": int(res["deferred"]), "years": float(years),
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
    w_e2 = arm_signals(price, e2, vol_instr, "E2", comps)[0][0]   # fidelity anchor only
    w_r2 = arm_signals(price, e2, vol_instr, "R2", comps)[0][0]   # production R2 = the control
    return days, o, c, w_e2, w_r2, e2


def wants_of(price, e2, vol_instr, comps=None):
    """(production-E2 wants [P, T], production-R2 wants [P, T]) for one price series; one state
    loop for the pair. Index 0 is the harness fidelity anchor; index 1 is the study's control
    and the base of every arm's target series."""
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


_RATE_CACHE = {}


def dtb3_on(dates):
    """The DTB3 series (decimal) on `dates`, ffilled, with the pre-1954 TB3MS fallback -- the
    same rate series every market loader in this file builds. Cached per date-tuple."""
    key = tuple(dates)
    if key not in _RATE_CACHE:
        d3, tb = load_rates()
        _RATE_CACHE[key] = rate_on(list(dates), d3, tb)
    return _RATE_CACHE[key]


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
    """One OOS window: own E2 + own R2 early path, own 2x leg (calibrated, m = 2), R-USD rule.
    Every arm label, control included, is driven by production R2's target series."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)[0]
    comps = early_components(c)
    _w_e2, w_r2 = wants_of(c, e2, trv2.vol_n(sc)[0], comps)
    mkt = usd_market(days)
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        r = engine_run(1.0, start, len(days) - 1, days, mkt, so, sc, w_r2, e2, arm, "pre_tax",
                       m=M, rule_index=False)
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
    """Own E2 + own R2 early path on the instrument's adjusted close, own 2x leg (R-USD rule).
    Every arm label, control included, is driven by production R2's target series."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)[0]
    comps = early_components(c)
    _w_e2, w_r2 = wants_of(c, e2, trv2.vol_n(sc)[0], comps)
    mkt = usd_market(days)
    res = {}
    for arm in arms:
        r = engine_run(1.0, start, len(days) - 1, days, mkt, so, sc, w_r2, e2, arm, "pre_tax",
                       m=M, rule_index=False)
        st = window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


# ----------------------------------------------------------------------------- bootstrap E1 (fixed 2015-09..2026-09 calendar)
def boot(seed=SEED, block=BLOCK, arms=ARMS_ALL, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
         capitals=("100k", "1M"), modes=("progressive", "flat"), anchor_e2=True,
         deltas=CANDIDATES, baselines=(CONTROL,), label="E1", pre_baselines=(CONTROL,)):
    """Block bootstrap on the fixed 2015-09 -> 2026-09 calendar with its realised USD/TRY and
    Yİ-ÜFE; market returns (QQQ overnight/intraday/dividend + DTB3) resampled exactly as
    tax_asym_v2 / vt_level_tax_v1 / tax_harvest_v2. Pre-tax at `n_pre` paths under both financing
    models; after-tax `modes` at the first `n_tax` paths.

    v3: `seed` and `block` are parameters (v2's G5/B1/B2 = seed 20261011 block 20; the fresh E3
    bootstraps = seed 20261015, blocks 10 and 40), and the arm set is explicit (the v2 reference
    block needs A3 too). The path draws depend only on (seed, block, n_paths), so
    boot(20261011, 20, ...) reproduces v2's cells for the arms v2 ran."""
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
    rng = np.random.default_rng(seed)
    pool = np.arange(1, Tsrc)
    npool = len(pool)
    L, EXT_ = TAX_BOOT_L, TAX_BOOT_EXT
    i0, i1 = TAX_BOOT_I0, len(fixed_dates) - 1
    pre = {(md, a): [] for md in FIN for a in arms}
    at = {cl: {mode: {a: [] for a in arms} for mode in modes} for cl in capitals}
    anchor_e2_out = {"progressive": [], "flat": []}   # pure-E2 targets, $100k (fidelity anchor)
    n_paths = max(n_pre, n_tax)
    for pidx in range(n_paths):
        pos = np.empty((1, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, 1)
        jump = rng.random((1, L)) < 1 / block
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
        # every arm label (the control "C0" included) is driven by production R2's target
        # series; only the fill rule differs, so C0 here is production R2 exactly.
        if pidx < n_pre:
            for md, (o_, c_, want) in legs.items():
                for arm in arms:
                    r = engine_run(PRE_CAPITAL, i0, i1, fixed_dates, mkt, o_, c_, want, e2,
                                   arm, "pre_tax", m=M, rate=yy[0])
                    k = len(r["_nav"]) - 1
                    pre[(md, arm)].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
        if pidx < n_tax:
            o_, c_, want = legs["base"]
            for cap_label in capitals:
                for mode in modes:
                    for arm in arms:
                        r = engine_run(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, o_, c_,
                                       want, e2, arm, mode, m=M, rate=yy[0])
                        if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                            raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode} {cap_label}")
                        at[cap_label][mode][arm].append(r["cagr_usd_liquidated"])
                    if anchor_e2:
                        # anchor label only: pure E2 targets (the reference bootstrap's control)
                        r = engine_run(TAX_CAPITALS[GATE_CAPITAL], i0, i1, fixed_dates, mkt, o_, c_,
                                       w_e2, e2, "E2", mode, m=M, rate=yy[0])
                        anchor_e2_out[mode].append(r["cagr_usd_liquidated"])
        if (pidx + 1) % 500 == 0:
            print(f"  {label} path {pidx + 1}/{n_paths}", flush=True)

    out = {"n_paths_pre": n_pre, "n_paths_tax": n_tax, "seed": seed, "block": block,
           "arms": list(arms), "calendar_start": fixed_dates[0], "calendar_end": fixed_dates[-1],
           "pre": {}, "tax": {}}
    for md in (FIN if n_pre > 0 else ()):   # no pre-tax paths -> no pre-tax block
        base = np.array(pre[(md, CONTROL)])
        out["pre"][md] = {}
        for arm in arms:
            c = np.array(pre[(md, arm)])
            d = c - base
            out["pre"][md][arm] = {
                "cagr_median": float(np.median(c)),
                f"diff_vs_{CONTROL}_median_pp": float(np.median(d) * 100),
                f"diff_vs_{CONTROL}_mean_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100), "p95_delta_pp": float(np.percentile(d, 95) * 100)}
            for ctrl in pre_baselines:       # report-only extra comparisons (e.g. the A_ALL check)
                if ctrl == CONTROL or ctrl not in arms or arm == ctrl:
                    continue
                b2 = np.array(pre[(md, ctrl)])
                d2 = c - b2
                out["pre"][md][f"{arm}_vs_{ctrl}"] = {
                    "median_delta_pp": float(np.median(d2) * 100),
                    "mean_delta_pp": float(np.mean(d2) * 100),
                    "share_gt_0": float(np.mean(d2 > 0)),
                    "p05_delta_pp": float(np.percentile(d2, 5) * 100),
                    "p95_delta_pp": float(np.percentile(d2, 95) * 100)}
    for cl in capitals:
        out["tax"][cl] = {}
        for mode in modes:
            e = {"n_paths": n_tax, "capital_usd": TAX_CAPITALS[cl]}
            for arm in arms:
                e[arm] = {"cagr_median": float(np.median(at[cl][mode][arm]))}
            for ctrl in baselines:
                base = np.array(at[cl][mode][ctrl])
                for arm in deltas:
                    if arm == ctrl:
                        continue
                    d = np.array(at[cl][mode][arm]) - base
                    e[f"{arm}_vs_{ctrl}"] = {
                        "median_delta_cagr_pp": float(np.median(d) * 100),
                        "mean_delta_cagr_pp": float(np.mean(d) * 100),
                        "share_gt_0": float(np.mean(d > 0)),
                        "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                        "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100)}
            out["tax"][cl][mode] = e
    out["anchor_e2_100k"] = {mode: ({"cagr_median": float(np.median(anchor_e2_out[mode]))}
                                    if anchor_e2_out[mode] else {})
                             for mode in modes}
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
        _w_e2, w_r2 = wants_of(cc, e2, vol_instr)
        for arm in arms:
            want = w_r2   # production R2 target series for every label, the control included
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
    global DEC_TAIL
    n = 1600
    o, price, y = synthetic_bars(n, 7)
    days = _trading_days(n)
    so, sc = trv2.synthetic_2x(o, price, np.zeros(n), y, EXP_Q)
    e2 = e2_up(price)[0]
    v20 = trv2.vol_n(sc)[0]
    w_e2, w_r2 = (w[0] for w in wants_of(price, e2, v20, early_components(price)))
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

    # 1b. the A1u probe (USD FIFO, no FX, no indexation) == the engine's own sell gain on a
    # usd_market in "pre_tax" mode (regime kind "none": no indexation branch, fx = 1)
    mk_usd = usd_market(days)
    ok = True
    for _ in range(300):
        acc = te.BaseAccount(mk_usd, te.Regime("pre_tax", "none"), 0.0, {"v": 1e6})
        ibuy = int(rng.integers(1, n - 2))
        for _ in range(int(rng.integers(1, 5))):
            acc.buy(ibuy, float(sc[ibuy]), float(rng.uniform(0.5, 10)) * float(sc[ibuy]), 0.0)
        i = int(rng.integers(ibuy, n))
        units = acc.units * float(rng.uniform(0.1, 1.0))
        fee = units * float(sc[i]) * 0.0008
        probe, proceeds = rule_sale_usd(i, float(sc[i]), units, fee, list(acc.lots))
        real = acc.sell(i, float(sc[i]), units, fee, "resize")
        if abs(probe - real) > 1e-9 * max(1.0, abs(real)):
            ok = False
        if abs(proceeds - units * (float(sc[i]) - fee / units)) > 1e-9 * max(1.0, proceeds):
            ok = False
    checks["usd_probe_equals_engine_sell"] = bool(ok)

    # 1c. H2 == the reference H2 simulator (tail 10), and K's composition adds exactly the
    # December deferral to A1u and nothing else (K with an empty window == A1u).
    dt10 = december_tail(days, START, n - 1, DEC_TAIL)
    ref_h2, _, _ = simulate_band_overlay(so[None, :], sc[None, :], w_r2[None, :], COST_PER_SIDE * 2,
                                         START, C0_BAND, dec_tail=dt10)
    ref_h2_none, _, _ = simulate_band_overlay(so[None, :], sc[None, :], w_r2[None, :],
                                              COST_PER_SIDE * 2, START, C0_BAND)
    h2e = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_r2, e2, "H2", "pre_tax")
    checks["H2_equals_reference_overlay_tail10"] = bool(
        np.max(np.abs(h2e["_nav"] - ref_h2[0])) < 1e-9)
    checks["overlay_empty_tail_equals_simulate_band"] = bool(
        np.max(np.abs(ref_h2_none[0] - simulate_band(so[None, :], sc[None, :], w_r2[None, :],
                                                     COST_PER_SIDE * 2, START, C0_BAND)[0][0])) < 1e-15)
    a1u_e = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_r2, e2, "A1u", "pre_tax")
    keep_tail, DEC_TAIL = DEC_TAIL, 0
    k0 = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_r2, e2, "K", "pre_tax")
    DEC_TAIL = keep_tail
    checks["K_with_empty_window_equals_A1u"] = bool(np.array_equal(k0["_nav"], a1u_e["_nav"]))

    # 2. fast rolling vol median == the loop reference
    pv = trv2.vol_n(100 * np.cumprod(1 + rng.normal(0, 0.01, 1400)))
    checks["rolling_median_fast_equals_ref"] = bool(np.array_equal(
        _rolling_median_vol(pv), _rolling_median_vol_ref(pv), equal_nan=True))

    # 2b. the split state/sizing helpers == the reference arm_signals (R2 want series), and the
    # R2 alias label is the *same* target series as the control's
    w_ref = arm_signals(price, e2, v20, "R2")[0][0]
    on_eff, factor = r2_state(price, e2, early_components(price))
    checks["r2_state_sizing_matches_arm_signals"] = bool(
        np.array_equal(w_ref, sized_wants(on_eff, factor, v20)[0]))
    checks["wants_of_matches_arm_signals"] = bool(
        np.array_equal(wants_of(price, e2, v20)[1][0], w_ref))
    checks["R2_label_is_the_R2_target_series"] = bool(
        np.array_equal(arm_signals(price, e2, v20, "R2")[0][0], w_ref)
        and np.array_equal(w_r2, w_ref))
    checks["E2_anchor_differs_from_R2_series"] = bool(
        not np.array_equal(arm_signals(price, e2, v20, "E2")[0][0], w_ref))

    # 3. the band-off engine == the reference simulate_band (base, m = 2)
    ref_nav, _, _ = simulate_band(so[None, :], sc[None, :], w_r2, COST_PER_SIDE * 2, START, C0_BAND)
    r2e = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_r2, e2, "R2", "pre_tax")
    checks["r2_engine_equals_simulate_band"] = bool(np.max(np.abs(r2e["_nav"] - ref_nav[0])) < 1e-9)
    checks["K_nav_differs_from_R2"] = bool(
        np.max(np.abs(engine_run(1.0, START, n - 1, days, mkt, so, sc, w_r2, e2, "K", "pre_tax")["_nav"]
                     - ref_nav[0])) > 1e-6)

    # 4. the mechanism fires: a sustained synthetic uptrend must block at least one downsize
    r = np.full(1500, 0.0012)
    price_up = 100.0 * np.cumprod(1 + r)
    o_up = np.concatenate([[price_up[0]], price_up[:-1]])
    so_u, sc_u = trv2.synthetic_2x(o_up, price_up, np.zeros(1500), np.full(1500, 0.01), EXP_Q)
    days_u = _trading_days(1500)
    ufe_u = {f"{yr}-{mo:02d}": float(1000 + 60 * yr + 8 * mo) for yr in range(2000, 2030) for mo in range(1, 13)}
    mkt_u = te.Market(days_u, np.full(1500, 30.0), ufe_u)
    e2_u = e2_up(price_up)[0]
    _wc_u, wr_u = wants_of(price_up, e2_u, trv2.vol_n(sc_u)[0])
    blocked = {}
    for arm in ARMS + ("R2",):
        rr = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u,
                        e2_u, arm, "pre_tax")
        blocked[arm] = {"band": rr["blocked_band"], "deferred": rr["deferred"],
                        "postponed": rr["postponements"]}
    checks["band_fires"] = bool(blocked["K"]["band"] > 0 or blocked["Z1"]["band"] > 0)
    checks["deferral_fires"] = bool(blocked["K"]["deferred"] > 0 and blocked["Z1"]["deferred"] > 0)
    checks["R2_blocks_nothing"] = bool(blocked["R2"]["postponed"] == 0)
    checks["postponements_decompose"] = bool(all(
        blocked[a]["postponed"] == blocked[a]["band"] + blocked[a]["deferred"] for a in ARMS))

    # 4b. the tax modes bite in a trending series: progressive <= pre-tax, and flat < pre-tax
    flat_bites = prog_bites = False
    for arm in ARMS:
        want_u = wr_u   # every arm label is driven by the same (production R2) target series
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

    # 4d. the yield legs, on the same synthetic history (rate y = 1 %/yr, days_u's UFE/scale)
    r_zero = np.zeros(1500)
    nav_k = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u, e2_u, "K", "pre_tax")["_nav"]
    z1_zero = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u, e2_u, "Z1", "pre_tax",
                         rate=r_zero)
    checks["yield_inert_at_zero_rate"] = bool(np.array_equal(nav_k, z1_zero["_nav"]))
    runs_u = {a: engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u, e2_u, a, "progressive",
                            rate=np.full(1500, 0.06)) for a in ("K", "Z1", "Z2", "Z3", "Z1x")}
    areas = {a: float(runs_u[a]["_nav"].sum()) for a in runs_u}
    end = {a: float(runs_u[a]["_nav"][-1]) for a in runs_u}
    checks["yield_arm_ordering"] = bool(
        end["Z1x"] > end["Z1"] > end["K"]
        and end["Z1"] > end["Z3"] > end["K"]
        and areas["Z1x"] > areas["Z1"] > areas["K"]
        and runs_u["Z1"]["yield_usd_total"] > 0
        and runs_u["Z2"]["yield_usd_total"] > 0)
    checks["yield_earns_something"] = bool(
        runs_u["Z1"]["_nav"][-1] > runs_u["K"]["_nav"][-1] + 1e-6
        and runs_u["Z1"]["tax_total_usd_at_payment_fx"] > 0)
    checks["interest_tax_positive_when_declared"] = bool(
        runs_u["Z1"]["interest_tax_try_total"] > 0
        and runs_u["Z3"]["interest_tax_try_total"] > 0
        and runs_u["Z2"]["interest_tax_try_total"] >= 0
        and runs_u["Z1x"]["interest_tax_try_total"] == 0)
    # 4e. the two interest-tax modes do exactly what they say: on a pure-cash account with
    # fx 30 and scale ~1, the 22,000 x scale TRY bar is ~21,800 TRY of interest. 0.1 %/yr on
    # $1,000 earns ~30 TRY/yr (bar not met -> Z2 == Z1x); 100 %/yr on $100,000 puts even the
    # 7-session stub year (the array's first 253 sessions are warm-up) at ~83,000 TRY
    # (bar met in every year -> the whole amount is declared, Z2 == Z1)
    zeros_w = np.zeros(1500)
    tiny = engine_run(1000.0, START, 1499, days_u, mkt_u, so_u, sc_u, zeros_w, e2_u, "Z2",
                      "progressive", rate=np.full(1500, 0.001 + ALPACA_SPREAD))
    tiny_x = engine_run(1000.0, START, 1499, days_u, mkt_u, so_u, sc_u, zeros_w, e2_u, "Z1x",
                        "progressive", rate=np.full(1500, 0.001 + ALPACA_SPREAD))
    huge = engine_run(100_000.0, START, 1499, days_u, mkt_u, so_u, sc_u, zeros_w, e2_u, "Z2",
                      "progressive", rate=np.full(1500, 1.0 + ALPACA_SPREAD))
    huge_1 = engine_run(100_000.0, START, 1499, days_u, mkt_u, so_u, sc_u, zeros_w, e2_u, "Z1",
                        "progressive", rate=np.full(1500, 1.0 + ALPACA_SPREAD))
    checks["threshold_mode_cliff"] = bool(
        np.array_equal(tiny["_nav"], tiny_x["_nav"]) and tiny["interest_tax_try_total"] == 0.0
        and np.allclose(huge["_nav"], huge_1["_nav"], rtol=0, atol=1e-9)
        and huge["interest_tax_try_total"] > 0.0)
    # 4f. exact closed form for a pure-cash account: daily accrual at r/252, compounded at each
    # month end. Daily compounding would compound inside the month and must give a different NAV.
    r_cash, cap0 = 0.05, 1000.0
    cash_run = engine_run(cap0, START, 1499, days_u, mkt_u, so_u, sc_u, zeros_w, e2_u, "Z1",
                          "pre_tax", rate=np.full(1500, r_cash + ALPACA_SPREAD))
    daily_rate = r_cash / 252.0
    exp_m = 1.0
    month_days = 0
    for i in range(START, 1499 + 1):
        month_days += 1
        if i == 1499 or days_u[i][:7] != days_u[i + 1][:7]:
            exp_m *= (1.0 + daily_rate * month_days)
            month_days = 0
    exp_d = (1.0 + daily_rate) ** (1499 + 1 - START)
    got = float(cash_run["_nav"][-1])
    checks["yield_monthly_compounding_closed_form"] = bool(
        abs(got - cap0 * exp_m) < 1e-9 * cap0
        and abs(got - cap0 * exp_d) > 1e-6 * cap0
        and cash_run["accrual_sessions"] == 1499 + 1 - START)
    res["yield"] = {"zero_rate_nav_equal": bool(np.array_equal(nav_k, z1_zero["_nav"])),
                    "progressive_nav_end": {a: float(runs_u[a]["_nav"][-1]) for a in runs_u},
                    "area_under_nav": areas,
                    "yield_usd_total": {a: float(runs_u[a]["yield_usd_total"]) for a in runs_u},
                    "interest_tax_try_total": {a: float(runs_u[a]["interest_tax_try_total"]) for a in runs_u},
                    "tiny_interest_try": float(sum(tiny["yield_try_by_year"].values())),
                    "huge_interest_try": float(sum(huge["yield_try_by_year"].values())),
                    "closed_form_multiple": cap0 * exp_m, "engine_multiple": got,
                    "daily_compounding_multiple": cap0 * exp_d}

    # 4c. the H2 mechanism on the same uptrend series but with the target *oscillating between
    # two positive weights* (0.98 / 0.49), so that December sessions see genuine non-exit
    # downward resizes: the engine's H2 arm must equal the reference overlay simulator with the
    # tail-10 December window, the deferral must fire, and it must never touch an exit to zero
    # (H2 has no band, so every postponement is a December deferral).
    w_osc = np.where((np.arange(1500) // 60) % 2 == 0, 0.98, 0.49)
    dt10u = december_tail(days_u, START, 1499, DEC_TAIL)
    ref_u, _, _ = simulate_band_overlay(so_u[None, :], sc_u[None, :], w_osc[None, :],
                                        COST_PER_SIDE * 2, START, C0_BAND, dec_tail=dt10u)
    h2_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_osc, e2_u, "H2", "pre_tax")
    k_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_osc, e2_u, "K", "pre_tax")
    checks["H2_equals_reference_overlay_oscillating_target"] = bool(
        np.max(np.abs(h2_u["_nav"] - ref_u[0])) < 1e-9 and len(dt10u) > 0)
    checks["deferral_fires"] = bool(h2_u["deferred"] > 0 and k_u["deferred"] > 0)
    checks["H2_defers_only_in_December"] = bool(
        h2_u["deferred"] == h2_u["postponements"] and h2_u["blocked_band"] == 0
        and len(h2_u["deferrals_by_year"]) > 0)

    # 5. finite, positive, and after tax <= pre-tax
    for arm in ARMS:
        want = w_r2
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


def harness(run_boot=True, deep=False):
    """Control reproduction on real data (no candidate arm).

    Three anchor blocks must pass before any number is read:
      (1) the control K's own published cells, cell-by-cell against K_ANCHORS.json (the
          band_h2_combo_v1 campaign's RESULTS.json for its arm "K", i.e. production
          15.7649 / 15.1785 % CAGR at m = 2, max DD 46.6698 / 46.7948 %, turnover 5.4561,
          after tax at $100k 10.7533 / 15.2354 / 23.9720 %);
      (2) production R2's anchors under its own "R2" label (fast_reentry_v1's pre-tax CAGR / max
          DD / turnover at m = 1/2 x base/calibrated; vt_level_tax_v1's after-tax $100k
          10.16 / 14.71 / 22.89 %) and the pure-E2 series under "E2" (e2_integration_v1), plus
          the band-off engine == `simulate_band` and the B1/B2 control medians vs
          tax_harvest_v2;
      (3) the report arm A_ALL of tbill_sweep_v1 -- pure-E2 targets with idle cash at
          DTB3 - 0.5 pp, pre-tax -- as the cross-check of the *yield* code path (its published
          main-window 15.35 % CAGR / 45.7 % max DD; its published bootstrap median +0.857 pp on
          100 % of paths). A monthly-crediting convention differs from that arm's daily
          compounding by < 0.02 pp, so the cross-check carries a 0.05 pp tolerance."""
    checks, bad = {}, 0
    days, o, c, w_e2, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_ = main_inputs(spread)
        for label, wants, ref in (("E2", we_, REF_E2), ("R2", wr_, REF_R2)):
            for m in (1, 2):
                r = engine_run(1.0, START, len(d_) - 1, d_, mkt, o_, c_, wants, e_, label, "pre_tax", m=m)
                for key, val in (("cagr_usd_unliquidated", ref[(md, m)][0]),
                                 ("max_dd_nav_usd", ref[(md, m)][1]),
                                 ("turnover_annual", ref[(md, m)][2])):
                    got = float(r[key])
                    checks[f"{label}_{md}_m{m}_{key}"] = {"reference": val, "reproduced": got, "diff": got - val}
                    if abs(got - val) > TOL:
                        bad += 1
    # ---- explicit R2 anchor block (the run's control label "C0" only)
    r2_anchors = {
        "reference": {"pre_tax": "fast_reentry_v1/RESULTS.json main_window (R2): 15.48 / 14.90 % CAGR, "
                                 "46.76 % max DD",
                      "after_tax": "vt_level_tax_v1 + r2_tbill_combo_v1 at $100k (R2): 10.16 / 14.71 / "
                                   "22.89 % CAGR, max DD 46.76 / 43.78 / 36.24 %"},
        "pre_tax": {f"{md}_m{m}": {
            "reference_cagr": REF_R2[(md, m)][0],
            "reproduced_cagr": checks[f"R2_{md}_m{m}_cagr_usd_unliquidated"]["reproduced"],
            "reference_max_dd": REF_R2[(md, m)][1],
            "reproduced_max_dd": checks[f"R2_{md}_m{m}_max_dd_nav_usd"]["reproduced"],
            "reference_turnover": REF_R2[(md, m)][2],
            "reproduced_turnover": checks[f"R2_{md}_m{m}_turnover_annual"]["reproduced"]}
            for md in FIN for m in (1, 2)},
        "after_tax_100k": {},
    }
    ref_nav, _, _ = simulate_band(o[None, :], c[None, :], w_r2[None, :], COST_PER_SIDE * 2, START, C0_BAND)
    r2_nav = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, "R2", "pre_tax")
    d_nav = float(np.max(np.abs(r2_nav["_nav"] - ref_nav[0])))
    checks["r2_engine_equals_simulate_band"] = {"max_abs_nav_diff": d_nav}
    if d_nav > 1e-9:
        bad += 1
    e2_run = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_e2, e2, "E2", "pre_tax")
    checks["R2_label_is_not_the_E2_anchor_series"] = {
        "max_abs_nav_diff_vs_E2": float(np.max(np.abs(r2_nav["_nav"] - e2_run["_nav"]))),
        "cagr_R2_label": r2_nav["cagr_usd_unliquidated"],
        "cagr_E2_anchor": e2_run["cagr_usd_unliquidated"]}
    if not checks["R2_label_is_not_the_E2_anchor_series"]["max_abs_nav_diff_vs_E2"] > 0.01:
        bad += 1
    k_nav = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, "K", "pre_tax")
    checks["K_nav_differs_from_R2"] = {"max_abs_nav_diff": float(np.max(np.abs(k_nav["_nav"] - r2_nav["_nav"])))}
    if not checks["K_nav_differs_from_R2"]["max_abs_nav_diff"] > 0.01:
        bad += 1
    # ---- production-E2 targets with the Z1 yield == tbill_sweep_v1's archived report arm A_ALL
    e2y = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_e2, e2, "E2y", "pre_tax")
    aall = {"cagr": float(e2y["cagr_usd_unliquidated"]), "max_dd": float(e2y["max_dd_nav_usd"]),
            "reference_cagr": 0.1535, "reference_max_dd": 0.457,
            "diff_pp": (float(e2y["cagr_usd_unliquidated"]) - 0.1535) * 100,
            "tolerance_pp": 0.05}
    aall["pass"] = bool(abs(aall["diff_pp"]) <= aall["tolerance_pp"])
    if not aall["pass"]:
        bad += 1
    checks["aall_pre_tax_cross_check"] = aall
    tax = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        tax[wn] = {}
        for label, wants, series in (("E2", w_e2, "E2"), ("R2", w_r2, "R2")):
            rp = engine_run(100_000.0, a, b, days, mkt, o, c, wants, e2, label, "pre_tax")
            rq = engine_run(100_000.0, a, b, days, mkt, o, c, wants, e2, label, "progressive")
            ref = TAX_ANCHORS[series][wn]
            tax[wn][label] = {"pre_tax": {"reference": ref[0], "reproduced": rp["cagr_usd_liquidated"],
                                          "diff_pp": (rp["cagr_usd_liquidated"] - ref[0]) * 100},
                              "progressive": {"reference": ref[1], "reproduced": rq["cagr_usd_liquidated"],
                                              "diff_pp": (rq["cagr_usd_liquidated"] - ref[1]) * 100},
                              "progressive_max_dd": {"reference": ref[2], "reproduced": rq["max_dd_nav_usd"],
                                                     "diff_pp": (rq["max_dd_nav_usd"] - ref[2]) * 100}}
            for v in tax[wn][label].values():
                if abs(v["diff_pp"]) > 0.005:
                    bad += 1
        r2_anchors["after_tax_100k"][wn] = {
            "reference": list(TAX_ANCHORS["R2"][wn]),
            "reproduced": [tax[wn]["R2"]["pre_tax"]["reproduced"],
                           tax[wn]["R2"]["progressive"]["reproduced"],
                           tax[wn]["R2"]["progressive_max_dd"]["reproduced"]],
            "pass": bool(all(abs(v["diff_pp"]) <= 0.005 for v in tax[wn]["R2"].values()))}
    r2_anchors["pass"] = bool(
        all(abs(checks[f"R2_{md}_m{m}_{k}"]["diff"]) <= TOL for md in FIN for m in (1, 2)
            for k in ("cagr_usd_unliquidated", "max_dd_nav_usd", "turnover_annual"))
        and checks["r2_engine_equals_simulate_band"]["max_abs_nav_diff"] <= 1e-9
        and all(r2_anchors["after_tax_100k"][wn]["pass"] for wn in TAX_WINDOWS))
    if not r2_anchors["pass"]:
        bad += 1
    # ---- K's own published cells (the control anchors of this study)
    _pre, _pre_usd = pre_tax_main(ARMS)
    k_anchors = k_anchor_checks({"pre_tax_main": _pre, "pre_tax_main_r_usd": _pre_usd,
                                 "after_tax_windows": after_tax_windows(tuple(TAX_CAPITALS), ARMS_ALL),
                                 "cost_stress_m4": cost_stress(ARMS)})
    if not k_anchors["pass"]:
        bad += 1
    boot_control = {}
    if run_boot:
        b = boot(n_pre=TAX_BOOT_PATHS, n_tax=TAX_BOOT_PATHS, capitals=("100k",),
                 arms=ARMS_ALL + ("R2", "E2y"), anchor_e2=True, pre_baselines=(CONTROL, "E2"))
        for mode in ("progressive", "flat"):
            # the boot drives every arm label from the R2 target series; the "R2" label is
            # production R2 (BOOT_ANCHOR["R2"], tax_harvest_v2's medians) and "E2"/"E2y" carry the
            # pure-E2 series (BOOT_ANCHOR["E2"] and tbill_sweep_v1's archived A_ALL).
            boot_control[mode] = {"R2": b["tax"]["100k"][mode]["R2"]["cagr_median"],
                                  "E2": b["anchor_e2_100k"][mode]["cagr_median"]}
            for label, ref_key in (("R2", "R2"), ("E2", "E2")):
                if abs(boot_control[mode][label] - BOOT_ANCHOR[mode][ref_key]) > 0.0010:
                    bad += 1
        boot_control["pre_base"] = {a: b["pre"]["base"][a]["cagr_median"] for a in b["arms"]}
        boot_control["pre_cal"] = {a: b["pre"]["calibrated"][a]["cagr_median"] for a in b["arms"]}
        aall_b = b["pre"]["base"].get("E2y_vs_E2", {})
        boot_control["aall_bootstrap_cross_check"] = {
            "median_delta_pp": aall_b.get("median_delta_pp"), "reference_pp": 0.857,
            "share_gt_0": aall_b.get("share_gt_0"), "reference_share": 1.0,
            "diff_pp": (aall_b.get("median_delta_pp") - 0.857) if aall_b else None,
            "tolerance_pp": 0.05}
        if aall_b and abs(aall_b.get("median_delta_pp") - 0.857) > 0.05:
            bad += 1
    out = {"reproduction_checks": checks, "n_outside_tolerance": bad, "after_tax_anchors": tax,
           "r2_anchors": r2_anchors, "k_anchors": k_anchors, "bootstrap_control": boot_control,
           "pass": bool(bad == 0)}
    if deep:
        out["deep"] = harness_deep()
        if not out["deep"]["pass"]:
            out["pass"] = False
    return out


def harness_deep():
    """The control's published cells on the pre-tax/window/cost stages and the clean bootstrap
    (seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths) that `run()` reuses as
    `boot_ref`. The reported reference series (R2, E2, E2y) ride the same bootstrap call."""
    pre, pre_usd = pre_tax_main(ARMS)
    win = after_tax_windows(tuple(TAX_CAPITALS), ARMS_ALL)
    cost = cost_stress(ARMS)
    print(f"  deep harness: clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)",
          flush=True)
    boot_arms = ARMS_ALL + ("R2", "E2y")
    boot_ref = boot(seed=SEED, block=BLOCK, arms=boot_arms, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
                    capitals=("100k", "1M"), modes=("progressive", "flat"),
                    deltas=("K",) + CANDIDATES + REPORT_ARMS, baselines=(CONTROL, "R2"),
                    pre_baselines=(CONTROL, "E2", "R2"), label="clean")
    ref = k_anchor_checks({"pre_tax_main": pre, "pre_tax_main_r_usd": pre_usd,
                           "after_tax_windows": win, "cost_stress_m4": cost,
                           "bootstrap_clean_seed20261011": boot_ref})
    ref["boot_ref"] = boot_ref
    return ref


# ----------------------------------------------------------------------------- the control's published cells
def k_anchor_checks(sections):
    """Every cell of K_ANCHORS.json against this run's own output (dotted-path lookup), the
    cell-by-cell reproduction of the control's published numbers. `sections` maps the published
    section names to the run's internal objects."""
    anchors = json.loads(KREF.read_text())
    cells = anchors["cells"]
    tol = float(anchors.get("tolerance", TOL))
    n_cells = n_cmp = 0
    worst, mismatches, missing = 0.0, [], []
    for path, ref in cells.items():
        n_cells += 1
        head, rest = path.split(".", 1)
        node = sections.get(head)
        if node is None:
            missing.append(path)
            continue
        try:
            for part in rest.split("."):
                node = node[part] if not isinstance(node, list) else node[int(part)]
            got = float(node)
        except (KeyError, IndexError, TypeError, ValueError):
            missing.append(path)
            continue
        n_cmp += 1
        d = abs(got - ref)
        worst = max(worst, d)
        if d > tol:
            mismatches.append({"cell": path, "mine": got, "reference": ref, "diff": d})
    return {"source": anchors.get("source_file"), "source_sha256": anchors.get("source_sha256"),
            "tolerance": tol, "n_cells": n_cells, "n_compared": n_cmp,
            "n_missing_sections": len(missing), "missing": missing[:40],
            "n_mismatch": len(mismatches), "max_abs_diff": worst, "mismatches": mismatches[:40],
            "pass": bool(n_cmp > 100 and not mismatches)}


# ----------------------------------------------------------------------------- the run
def pre_tax_main(arms):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD main-window row."""
    days, o, c, w_e2, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    usd_mkt = usd_market(days)
    pre, pre_usd = {}, {}
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_ = main_inputs(spread)
        W = {arm: wr_ for arm in arms}
        pre[md], pre_usd[md] = {}, {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in arms:
                r = engine_run(PRE_CAPITAL, START, T, d_, mkt, o_, c_, W[arm], e_, arm, "pre_tax", m=m)
                pre[md][f"m{m}"][arm] = pre_stats(d_, START, T, r)
        for arm in arms:
            r = engine_run(PRE_CAPITAL, START, T, d_, usd_mkt, o_, c_, W[arm], e_, arm, "pre_tax", m=M,
                           rule_index=False)
            pre_usd[md][arm] = pre_stats(d_, START, T, r, real_days=d_)
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": T - START + 1,
                     "years": float(trv2.calendar_years(days[START - 1], days[-1]))}
    return pre, pre_usd


def after_tax_windows(capitals, arms):
    """After tax at the given capitals (progressive 2026 tariff, liquidated, base financing,
    m = 2) on the three TAX_WINDOWS. Every arm label runs production R2's target series."""
    days, o, c, w_e2, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    WANTS = {arm: w_r2 for arm in arms}
    win = {}
    for cap_label in capitals:
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            win[cap_label][wn] = {}
            for arm in arms:
                rq = engine_run(TAX_CAPITALS[cap_label], a, b, days, mkt, o, c, WANTS[arm], e2, arm, "progressive")
                # v2's convention for this reporting row: the pre-tax leg of the after-tax
                # windows runs at capital 1.0 (so A2/A3's budget is inert here). Kept identical
                # to v2, which is what the reference check verifies; it is not a v3 gate cell.
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
                    "deferred": rq["deferred"],
                    "postponements_by_year": rq["postponements_by_year"],
                    "deferrals_by_year": rq["deferrals_by_year"],
                    "index_relief_try": rq["index_relief_try"],
                    "yield_usd_total": rq["yield_usd_total"],
                    "yield_try_total": float(sum(rq["yield_try_by_year"].values())),
                    "yield_try_by_year": rq["yield_try_by_year"],
                    "interest_tax_try_total": rq["interest_tax_try_total"],
                }
    return win


def cost_stress(arms):
    """m = 4 cost stress, after tax at $100k on the three windows."""
    days, o, c, w_e2, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    cost = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        cost[wn] = {}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, w_r2, e2, arm,
                            "progressive", m=M_STRESS)
            # v2's convention (see after_tax_windows): the pre-tax leg of the cost stress runs
            # at capital 1.0, so the reference check reproduces v2 exactly.
            rp = engine_run(1.0, a, b, days, mkt, o, c, w_r2, e2, arm, "pre_tax", m=M_STRESS)
            cost[wn][arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                             "pre_tax_cagr": rp["cagr_usd_liquidated"],
                             "max_dd_after_tax": rq["max_dd_nav_usd"],
                             "turnover_annual": rp["turnover_annual"],
                             "postponements": rq["postponements"]}
    return cost


def oos_stages(arms):
    """Six OOS windows (^GSPC 1957-1999 + M1-M5), pre-tax, calibrated, m = 2, R-USD rule."""
    oos = {}
    gd, go, gc, gy, gstart = load_gspc()
    oos["GSPC_1957_1999"] = oos_window(gd, go, gc, gy, gstart, arms)
    for key in ("M1", "M2", "M3", "M4", "M5"):
        md_, mo_, mc_, my_, mstart_ = load_market(key)
        oos[key] = oos_window(md_, mo_, mc_, my_, mstart_, arms)
    for wn in OOS_WINDOWS:
        for arm in arms:
            if arm == CONTROL:
                continue
            oos[wn][arm]["delta_cagr_pp"] = (oos[wn][arm]["cagr"] - oos[wn][CONTROL]["cagr"]) * 100
            oos[wn][arm]["delta_max_dd_pp"] = (oos[wn][arm]["max_dd"] - oos[wn][CONTROL]["max_dd"]) * 100
    return oos


def instrument_stages_usd(arms):
    """The 21 quality-passing ETFs, pre-tax, calibrated, m = 2, R-USD rule (+ the quality record)."""
    inst, quality = {}, {}
    for key in sorted(INSTRUMENT_FILES):
        idays, io, ih, il, ic, iy = load_instrument(key)
        frac, ok = quality_ok(ih, il, io, ic)
        quality[key] = {"bad_frac": float(frac), "pass": bool(ok), "sessions": len(idays)}
        if key in EXCLUDED_INSTRUMENTS:
            continue
        r = instrument_window(idays, io, ic, iy, arms)
        for arm in arms:
            if arm == CONTROL:
                continue
            r[arm]["delta_cagr_pp"] = (r[arm]["cagr"] - r[CONTROL]["cagr"]) * 100
            r[arm]["delta_max_dd_pp"] = (r[arm]["max_dd"] - r[CONTROL]["max_dd"]) * 100
        inst[key] = r
    return inst, quality


def oos_bootstrap(arms):
    """Pooled OOS bootstrap E2 (G8 reference): block-20 over M1-M5, rng seed SEED, arms in order."""
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, arms) for k in MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in arms}
    out = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        out["per_market"][k] = {a: {"median_diff_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)}
                                for a in arms}
    for a in arms:
        d = pooled[a] - pooled[CONTROL]
        out["pooled"][a] = {f"diff_vs_{CONTROL}_median_pp": float(np.median(d) * 100),
                            f"diff_vs_{CONTROL}_mean_pp": float(np.mean(d) * 100),
                            "share_gt_0": float(np.mean(d > 0)), "paths": int(len(d))}
    return out


# ----------------------------------------------------------------------------- v3 stages (new evidence)
def stage_e1(arms):
    """E1: after tax on the 21 quality-passing ETFs under R-INDEX (real USD/TRY + YI-UFE), own E2
    + own R2 early path, own synthetic 2x leg (calibrated, m = 2), $100k, liquidated at the ETF
    window end; start = max(ETF warm-up, 2000-03-09)."""
    out = {}
    for key in INSTRUMENTS:
        idays, io, ih, il, ic, iy = load_instrument(key)
        start = max(START, bisect.bisect_left(idays, E1_RINDEX_START))
        so, sc = trv2.synthetic_2x(io, ic, np.zeros_like(ic), iy + EXTRA_FIN, EXP_Q)
        e2 = e2_up(ic)[0]
        _w_e2, w_r2 = wants_of(ic, e2, trv2.vol_n(sc)[0], early_components(ic))
        mkt = te.load_market(FX_CSV, UFE_CSV, idays)
        rec = {"window": {"start": idays[start], "end": idays[-1], "sessions": len(idays) - start}}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], start, len(idays) - 1, idays, mkt, so, sc,
                            w_r2, e2, arm, "progressive")
            rp = engine_run(PRE_CAPITAL, start, len(idays) - 1, idays, mkt, so, sc, w_r2, e2, arm, "pre_tax")
            rec[arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"], "pre_tax_cagr": rp["cagr_usd_liquidated"],
                        "max_dd_after_tax": rq["max_dd_nav_usd"], "terminal_usd": rq["terminal_usd_liquidated"],
                        "tax_usd": rq["tax_total_usd_at_payment_fx"], "years": float(rq["years"]),
                        "postponements": rq["postponements"], "blocked_band": rq["blocked_band"],
                        "deferred": rq["deferred"], "sells_per_year": rq["sells"] / rq["years"]}
        for arm in arms:
            if arm == CONTROL:
                continue
            rec[arm]["delta_after_tax_pp"] = (rec[arm]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
            rec[arm]["delta_pre_tax_pp"] = (rec[arm]["pre_tax_cagr"] - rec[CONTROL]["pre_tax_cagr"]) * 100
            rec[arm]["delta_max_dd_after_tax_pp"] = (rec[arm]["max_dd_after_tax"] - rec[CONTROL]["max_dd_after_tax"]) * 100
        out[key] = rec
    return out


def stage_e2(arms):
    """E2: rolling January starts 2001..2021 on the QQQ main series; after tax at $100k, base
    financing, m = 2, liquidated 2026-09-29."""
    days, o, c, w_e2, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    out = {}
    for yr in E2_START_YEARS:
        i0 = bisect.bisect_left(days, f"{yr}-01-01")
        rec = {"start": days[i0], "index": int(i0)}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], i0, T, days, mkt, o, c, w_r2, e2, arm, "progressive")
            rp = engine_run(PRE_CAPITAL, i0, T, days, mkt, o, c, w_r2, e2, arm, "pre_tax")
            rec[arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"], "pre_tax_cagr": rp["cagr_usd_liquidated"],
                        "max_dd_after_tax": rq["max_dd_nav_usd"], "terminal_usd": rq["terminal_usd_liquidated"],
                        "tax_usd": rq["tax_total_usd_at_payment_fx"], "years": float(rq["years"]),
                        "sells_per_year": rq["sells"] / rq["years"]}
        for arm in arms:
            if arm == CONTROL:
                continue
            rec[arm]["delta_after_tax_pp"] = (rec[arm]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
        out[str(yr)] = rec
    return out


def stage_e3(arms):
    """E3: fresh after-tax bootstraps -- seed 20261015, blocks 10 and 40, 1,000 paths each,
    $100k, B1-recent (progressive) and B2-flat. Median delta and share > 0 per run."""
    out = {}
    arms = tuple(dict.fromkeys(tuple(arms) + ARMS_ALL + ("R2",)))
    for block in E3_BLOCKS:
        for mode in ("progressive", "flat"):
            b = boot(seed=E3_SEED, block=block, arms=arms, n_pre=0, n_tax=E3_PATHS,
                     capitals=(GATE_CAPITAL,), modes=(mode,), anchor_e2=False,
                     deltas=("K",) + CANDIDATES + REPORT_ARMS, baselines=(CONTROL, "R2"),
                     label=f"E3 block {block} {mode}")
            out[f"block{block}_{mode}"] = b["tax"][GATE_CAPITAL][mode]
    return out


def stage_e5_decisions():
    """E5 (report only): how often the arms' fill decisions differ on the main path
    (base financing, m = 2, $100k, R-INDEX, pre-tax so no tax-funding sales interfere).
    Decision code: 0 = no trade, 1 = up/entry/exit trade, 2 = downward resize."""
    days, o, c, w_e2, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    traces = {}
    for arm in ARMS:
        traces[arm] = engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, w_r2, e2, arm, "pre_tax",
                              trace=True)
    out = {"window": {"start": days[START], "end": days[-1], "sessions": int(len(traces[CONTROL]["_dec"]))},
           "sells": {}, "postponements": {}}
    for arm in ARMS:
        r = traces[arm]
        out["sells"][arm] = int(np.sum(r["_dec"] == 2))
        out["postponements"][arm] = {"band": int(r["blocked_band"]), "deferred": int(r["deferred"])}
    for a, b in (("Z1", CONTROL), ("Z2", CONTROL), ("Z3", CONTROL), ("Z2", "Z1"), ("Z3", "Z1")):
        da, db = traces[a]["_dec"], traces[b]["_dec"]
        out[f"{a}_vs_{b}"] = {
            "sessions_decision_differs": int(np.sum(da != db)),
            "share_decision_differs": float(np.mean(da != db)),
            f"{a}_sells_{b}_holds": int(np.sum((da == 2) & (db != 2))),
            f"{a}_holds_{b}_sells": int(np.sum((da != 2) & (db == 2)))}
    return out


# ----------------------------------------------------------------------------- reproduction checks
def reference_checks(res):
    """Every cell of K_ANCHORS.json that this run produces, compared cell by cell.

    The anchors are the control K's published cells (research/band_h2_combo_v1/RESULTS.json, the
    campaign that shipped K); the map below renames this run's internal section names to the
    published ones. The reference series (R2, E2, E2y) ride the same stages with the same seeds,
    so a mismatch is a bug in this study."""
    sections = {
        "pre_tax_main": res["pre"], "pre_tax_main_r_usd": res["pre_usd"],
        "after_tax_windows": res["windows"], "cost_stress_m4": res["cost"],
        "oos": res["oos"], "instruments": res["instruments"],
        "bootstrap_clean_seed20261011": res["boot_ref"],
        "e1": res["e1"], "e2": res["e2"], "e3": res["e3"],
        "e5": {"K": {"sells": res["e5"]["decisions"]["sells"]["K"],
                     "postponements": res["e5"]["decisions"]["postponements"]["K"]}},
    }
    return k_anchor_checks(sections)


# ----------------------------------------------------------------------------- gates (this study)
def _arm_row(arm, pre, win, boot_ref):
    """One arm's headline row (pre-tax, after-tax, bootstrap, yield details); the control gets
    one too, so the summary table can print it."""
    g5 = {md: boot_ref["pre"][md][arm][f"diff_vs_{CONTROL}_median_pp"] for md in FIN}
    bb = {}
    for mode in ("progressive", "flat"):
        cell = (boot_ref["tax"][GATE_CAPITAL][mode].get(f"{arm}_vs_{CONTROL}")
                if arm != CONTROL else None)
        bb[mode] = {"median_delta_pp": cell["median_delta_cagr_pp"] if cell else 0.0,
                    "share_gt_0": cell["share_gt_0"] if cell else 0.0}
    dc = ({wn: win[wn][arm]["after_tax_cagr"] - win[wn][CONTROL]["after_tax_cagr"] for wn in TAX_WINDOWS}
          if arm != CONTROL else {wn: 0.0 for wn in TAX_WINDOWS})
    rx = win["2000_03_to_2026_09"][arm]
    return {
        "pre_tax_cagr_base_m2": pre["base"]["m2"][arm]["cagr"],
        "pre_tax_cagr_cal_m2": pre["calibrated"]["m2"][arm]["cagr"],
        "pre_tax_cagr_base_m1": pre["base"]["m1"][arm]["cagr"],
        "pre_tax_cagr_delta_pp_base": (pre["base"]["m2"][arm]["cagr"] - pre["base"]["m2"][CONTROL]["cagr"]) * 100,
        "pre_tax_cagr_delta_pp_cal": (pre["calibrated"]["m2"][arm]["cagr"] - pre["calibrated"]["m2"][CONTROL]["cagr"]) * 100,
        "pre_tax_max_dd_base": pre["base"]["m2"][arm]["max_dd"],
        "pre_tax_max_dd_cal": pre["calibrated"]["m2"][arm]["max_dd"],
        "pre_tax_max_dd_delta_pp_base": (pre["base"]["m2"][arm]["max_dd"] - pre["base"]["m2"][CONTROL]["max_dd"]) * 100,
        "pre_tax_max_dd_delta_pp_cal": (pre["calibrated"]["m2"][arm]["max_dd"] - pre["calibrated"]["m2"][CONTROL]["max_dd"]) * 100,
        "turnover_annual": pre["base"]["m2"][arm]["turnover_annual"],
        "sells_per_year": pre["base"]["m2"][arm]["sells_per_year"],
        "turnover_ratio": pre["base"]["m2"][arm]["turnover_annual"] / pre["base"]["m2"][CONTROL]["turnover_annual"],
        "after_tax_100k": {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                                "delta_vs_K_pp": dc[wn] * 100,
                                "tax_usd": win[wn][arm]["tax_usd"],
                                "max_dd_after_tax": win[wn][arm]["max_dd_after_tax"]}
                           for wn in TAX_WINDOWS},
        "after_tax_unliquidated_100k": {wn: win[wn][arm]["after_tax_cagr_unliquidated"]
                                        for wn in TAX_WINDOWS},
        "yield_usd_total_2000_window": rx.get("yield_usd_total"),
        "yield_try_total_2000_window": rx.get("yield_try_total"),
        "interest_tax_try_total_2000_window": rx.get("interest_tax_try_total"),
        "yield_try_by_year": rx.get("yield_try_by_year"),
        "oos_median_delta_pp": None,   # filled by the caller through the OOS stage
        "boot_pre_median_delta_pp": g5,
        "boot_after_tax_median_delta_pp": {mode: bb[mode]["median_delta_pp"] for mode in bb},
        "boot_after_tax_share_gt_0": {mode: bb[mode]["share_gt_0"] for mode in bb},
    }


def gates_cash(res):
    """ewma_band_v1's G1-G9 against the control K, plus the common protocol's after-tax pair
    (AT: arm - K >= 0 after tax at $100k in all three windows; B1B2: clean-bootstrap median >= 0
    and share > 0 >= 55 % under B1-recent and B2-flat). The parent campaigns' E1/E2/E3 stages are
    reported next to them but do not decide. See PREREGISTRATION.md section 5."""
    pre = res["pre"]
    win = res["windows"][GATE_CAPITAL]
    boot_ref, oos = res["boot_ref"], res["oos"]
    g = {}
    for arm in CANDIDATES:
        r = {}

        def g_return(md, m):
            a, c = pre[md][m][arm], pre[md][m][CONTROL]
            d = (a["cagr"] - c["cagr"]) * 100
            cal = a["calmar"] >= G1_CALMAR_MULT * c["calmar"]
            return bool(d >= 0 or (cal and d >= G1_MARGIN_PP)), d, cal

        g1 = {md: g_return(md, "m2") for md in FIN}
        r["G1_detail"] = {md: {"cagr_delta_pp": g1[md][1], "calmar_arm": pre[md]["m2"][arm]["calmar"],
                               "calmar_K": pre[md]["m2"][CONTROL]["calmar"],
                               "calmar_pass": g1[md][2]} for md in FIN}
        r["G1"] = bool(all(v[0] for v in g1.values()))
        g6 = {md: g_return(md, "m1") for md in FIN}
        r["G6_detail"] = {md: {"cagr_delta_pp": g6[md][1], "calmar_pass": g6[md][2]} for md in FIN}
        r["G6"] = bool(all(v[0] for v in g6.values()))
        dd = {md: (pre[md]["m2"][arm]["max_dd"] - pre[md]["m2"][CONTROL]["max_dd"]) * 100 for md in FIN}
        r["G2_detail"] = {"max_dd_delta_pp": dd, "allowed_pp": G2_MAX_DD_PP}
        r["G2"] = bool(all(v <= G2_MAX_DD_PP + 1e-9 for v in dd.values()))
        y17 = {md: (pre[md]["m2"][arm]["2017_end"]["cagr"]
                    - pre[md]["m2"][CONTROL]["2017_end"]["cagr"]) * 100 for md in FIN}
        r["G3_detail"] = {"cagr_2017_delta_pp": y17, "min_pp": G3_MIN_2017_PP}
        r["G3"] = bool(all(v >= G3_MIN_2017_PP for v in y17.values()))
        sub = {k: (pre["base"]["m2"][arm]["subperiods"][k]["cagr"]
                   - pre["base"]["m2"][CONTROL]["subperiods"][k]["cagr"]) * 100
               for k in ("P1", "P2", "P3")}
        n_pos = int(sum(1 for v in sub.values() if v > 0))
        r["G4_detail"] = {"deltas_pp": sub, "n_above": n_pos, "required": G4_MIN_PERIODS}
        r["G4"] = bool(n_pos >= G4_MIN_PERIODS)
        g5 = {md: boot_ref["pre"][md][arm][f"diff_vs_{CONTROL}_median_pp"] for md in FIN}
        r["G5_detail"] = {"median_delta_pp": g5,
                          "share_gt_0": {md: boot_ref["pre"][md][arm]["share_gt_0"] for md in FIN},
                          "min_pp": G5_MIN_MEDIAN_PP}
        r["G5"] = bool(all(v >= G5_MIN_MEDIAN_PP for v in g5.values()))
        dc = {wn: oos[wn][arm]["delta_cagr_pp"] for wn in OOS_WINDOWS}
        dd7 = {wn: oos[wn][arm]["delta_max_dd_pp"] for wn in OOS_WINDOWS}
        ok7 = int(sum(1 for wn in OOS_WINDOWS if dc[wn] >= G7_MIN_CAGR_PP and dd7[wn] <= G7_MAX_DD_PP))
        r["G7_detail"] = {"delta_cagr_pp": dc, "delta_max_dd_pp": dd7,
                          "median_cagr_pp": float(np.median(list(dc.values()))), "n_ok": ok7,
                          "worst_dd_pp": float(max(dd7.values())),
                          "need_ok": len(OOS_WINDOWS) - G7_MAX_WINDOWS_BAD, "hard_dd_pp": G7_HARD_DD_PP}
        r["G7"] = bool(np.median(list(dc.values())) >= G7_MEDIAN_MIN_PP
                       and ok7 >= len(OOS_WINDOWS) - G7_MAX_WINDOWS_BAD
                       and max(dd7.values()) <= G7_HARD_DD_PP)
        g8 = res["oos_boot"]["pooled"][arm][f"diff_vs_{CONTROL}_median_pp"]
        r["G8_detail"] = {"pooled_median_delta_pp": g8, "share_gt_0": res["oos_boot"]["pooled"][arm]["share_gt_0"],
                          "min_pp": G8_MIN_MEDIAN_PP, "paths": res["oos_boot"]["pooled"][arm]["paths"]}
        r["G8"] = bool(g8 >= G8_MIN_MEDIAN_PP)
        turn = pre["base"]["m2"][arm]["turnover_annual"] / pre["base"]["m2"][CONTROL]["turnover_annual"]
        r["G9_detail"] = {"turnover_arm": pre["base"]["m2"][arm]["turnover_annual"],
                          "turnover_K": pre["base"]["m2"][CONTROL]["turnover_annual"],
                          "ratio": turn, "max_ratio": G9_MAX_TURNOVER_MULT}
        r["G9"] = bool(turn <= G9_MAX_TURNOVER_MULT + 1e-9)
        # ---- after-tax pair
        at = {wn: (win[wn][arm]["after_tax_cagr"] - win[wn][CONTROL]["after_tax_cagr"]) * 100
              for wn in TAX_WINDOWS}
        r["AT_detail"] = {"deltas_pp": at, "required_pp": AT_MIN_DELTA_PP}
        r["AT"] = bool(all(v >= AT_MIN_DELTA_PP for v in at.values()))
        bb = {}
        for mode in ("progressive", "flat"):
            cell = boot_ref["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{CONTROL}"]
            bb[mode] = {"median_delta_pp": cell["median_delta_cagr_pp"],
                        "mean_delta_pp": cell["mean_delta_cagr_pp"],
                        "share_gt_0": cell["share_gt_0"],
                        "p05_pp": cell["p05_delta_cagr_pp"], "p95_pp": cell["p95_delta_cagr_pp"],
                        "pass": bool(cell["median_delta_cagr_pp"] >= B_MIN_MEDIAN_PP
                                     and cell["share_gt_0"] >= B_MIN_SHARE)}
        r["B1B2_detail"] = {"cells": bb, "required_median_pp": B_MIN_MEDIAN_PP,
                            "required_share_gt_0": B_MIN_SHARE}
        r["B1B2"] = bool(all(v["pass"] for v in bb.values()))
        r["gate_list"] = {k: r[k] for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "AT", "B1B2")}
        r["all_pass"] = bool(all(r["gate_list"].values()))
        # ---- headline row for the summary
        r["row"] = _arm_row(arm, pre, win, boot_ref)
        r["row"]["oos_median_delta_pp"] = float(np.median(list(dc.values())))
        # ---- reporting: the parent campaigns' E1/E2/E3 (not decisive)
        ds = [res["e1"][k][arm]["delta_after_tax_pp"] for k in INSTRUMENTS]
        n_p = int(sum(1 for d in ds if d > 0))
        r["E1_report"] = {"n_instruments": len(ds), "median_delta_pp": float(np.median(ds)),
                          "share_gt_0": float(n_p / len(ds)), "n_gt_0": n_p,
                          "min_delta_pp": float(min(ds)), "max_delta_pp": float(max(ds)),
                          "median_pre_tax_delta_pp": float(np.median(
                              [res["e1"][k][arm]["delta_pre_tax_pp"] for k in INSTRUMENTS])),
                          "pass_at_60pct": bool(np.median(ds) > 0 and n_p / len(ds) >= E1_MIN_SHARE)}
        ds2 = [res["e2"][str(y)][arm]["delta_after_tax_pp"] for y in E2_START_YEARS]
        n2 = int(sum(1 for d in ds2 if d > 0))
        r["E2_report"] = {"n_starts": len(ds2), "median_delta_pp": float(np.median(ds2)),
                          "share_gt_0": float(n2 / len(ds2)), "n_gt_0": n2,
                          "min_delta_pp": float(min(ds2)), "max_delta_pp": float(max(ds2)),
                          "pass_at_70pct": bool(n2 / len(ds2) >= E2_MIN_SHARE and np.median(ds2) > 0)}
        det, ok = {}, True
        for key in sorted(res["e3"]):
            cell = res["e3"][key][f"{arm}_vs_{CONTROL}"]
            good = bool(cell["median_delta_cagr_pp"] >= 0.0 and cell["share_gt_0"] >= E3_MIN_SHARE)
            det[key] = {"median_delta_pp": cell["median_delta_cagr_pp"],
                        "mean_delta_pp": cell["mean_delta_cagr_pp"],
                        "share_gt_0": cell["share_gt_0"], "pass": good}
            ok = ok and good
        r["E3_report"] = {"runs": det, "pass_at_55pct": bool(ok)}
        g[arm] = r
    # ---- reporting row for the two reference series (never gates)
    ref_rows = {}
    for label in ("R2",):
        ref_rows[label] = {
            "note": "reference row, not an arm of this study; anchored in --harness against "
                    "fast_reentry_v1 / vt_level_tax_v1 / tax_harvest_v2",
            "pre_tax_main_anchor": {"base_m2_cagr": REF_R2[("base", 2)][0],
                                    "cal_m2_cagr": REF_R2[("calibrated", 2)][0]},
            "after_tax_100k_anchor": {wn: TAX_ANCHORS["R2"][wn][1] for wn in TAX_WINDOWS}}
    # ---- the control's own headline row (it is the reference, so it carries no gates)
    g[CONTROL] = {"row": _arm_row(CONTROL, pre, win, boot_ref), "gate_list": {}, "all_pass": True,
                  "failing_gates": [], "E1_report": {}, "E2_report": {}, "E3_report": {"runs": {}}}
    g[CONTROL]["row"]["oos_median_delta_pp"] = 0.0
    # ---- selection
    passing = [a for a in CANDIDATES if g[a]["all_pass"]]
    if passing:
        best = max(passing, key=lambda a: (pre["base"]["m2"][a]["cagr"],
                                           win["2000_03_to_2026_09"][a]["after_tax_cagr"]))
    else:
        best = None
    fails = {a: [k for k, v in g[a]["gate_list"].items() if not v] for a in CANDIDATES}
    reason = ("ADOPT " + best + ": highest pre-tax base m = 2 CAGR among the candidates clearing "
              "every gate" if best else
              "no candidate clears every gate -> K stands unchanged")
    defs = {
        "G1": f"m = 2, base and calibrated: CAGR >= K, or Calmar >= {G1_CALMAR_MULT} x K and CAGR >= K + {G1_MARGIN_PP} pp",
        "G2": f"max DD <= K + {G2_MAX_DD_PP} pp (base and calibrated, m = 2)",
        "G3": f"2017+ CAGR >= K + {G3_MIN_2017_PP} pp (base and calibrated, m = 2)",
        "G4": f"CAGR above K in >= {G4_MIN_PERIODS} of the 3 sub-periods P1/P2/P3 (base, m = 2)",
        "G5": f"pre-tax clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} paths): median "
              f"(arm - K) >= {G5_MIN_MEDIAN_PP} pp in both financings",
        "G6": "G1 re-checked at m = 1, base and calibrated",
        "G7": f"six OOS windows: median d CAGR >= {G7_MEDIAN_MIN_PP} pp; >= {len(OOS_WINDOWS) - G7_MAX_WINDOWS_BAD} of 6 "
              f"with d CAGR >= {G7_MIN_CAGR_PP} pp and d DD <= +{G7_MAX_DD_PP} pp; d DD never > +{G7_HARD_DD_PP} pp",
        "G8": f"pooled OOS bootstrap over M1-M5 (2,000 paths each): median (arm - K) >= {G8_MIN_MEDIAN_PP} pp",
        "G9": f"turnover <= {G9_MAX_TURNOVER_MULT} x K",
        "AT": f"after tax at $100k: arm - K >= {AT_MIN_DELTA_PP} pp in all three windows",
        "B1B2": f"clean bootstrap at $100k, B1-recent (progressive) and B2-flat: median (arm - K) >= "
                f"{B_MIN_MEDIAN_PP} pp AND share > 0 >= {B_MIN_SHARE:.0%}",
        "select": "the candidate with the highest pre-tax base m = 2 CAGR among those clearing every gate; "
                  "if none, K stands",
    }
    return {"arms": g, "reference_rows": ref_rows, "adopted": best, "reason": reason,
            "failing_gates": fails, "gate": defs}


# ----------------------------------------------------------------------------- the run
def run(boot_ref=None) -> dict:
    print("pre-tax main window (K + Z1/Z2/Z3)", flush=True)
    pre, pre_usd = pre_tax_main(ARMS)
    print("after-tax windows (5 capitals, + report arm Z1x)", flush=True)
    win = after_tax_windows(tuple(TAX_CAPITALS), ARMS_ALL)
    print("cost stress m = 4", flush=True)
    cost = cost_stress(ARMS)
    print("OOS windows", flush=True)
    oos = oos_stages(ARMS)
    print("instruments (R-USD, pre-tax)", flush=True)
    inst, quality = instrument_stages_usd(ARMS)
    print("pooled OOS bootstrap (G8)", flush=True)
    oos_boot = oos_bootstrap(ARMS)
    if boot_ref is None:
        print(f"clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)", flush=True)
        boot_ref = boot(seed=SEED, block=BLOCK, arms=ARMS_ALL + ("R2", "E2y"), n_pre=PRE_BOOT_PATHS,
                        n_tax=TAX_BOOT_PATHS, capitals=("100k", "1M"), modes=("progressive", "flat"),
                        deltas=("K",) + CANDIDATES + REPORT_ARMS, baselines=(CONTROL, "R2"),
                        pre_baselines=(CONTROL, "E2", "R2"), label="clean")
    print("E1: after-tax 21 ETFs (R-INDEX)", flush=True)
    e1 = stage_e1(ARMS)
    print("E2: rolling January starts", flush=True)
    e2 = stage_e2(ARMS)
    print(f"E3: fresh bootstraps (seed {E3_SEED}, blocks {E3_BLOCKS})", flush=True)
    e3 = stage_e3(ARMS)
    print("E5: decision traces", flush=True)
    e5 = {"decisions": stage_e5_decisions()}
    res = {"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost, "oos": oos,
           "instruments": inst, "quality": quality, "oos_boot": oos_boot, "boot_ref": boot_ref,
           "e1": e1, "e2": e2, "e3": e3, "e5": e5}
    print("reference reproduction checks (the control K's published cells)", flush=True)
    res["reference_check"] = reference_checks(res)
    res["gates"] = gates_cash(res)
    return res


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
    if os.environ.get("CASH_YIELD_PROBE"):   # code-path probe: tiny path counts, scratch outputs
        global PRE_BOOT_PATHS, TAX_BOOT_PATHS, E3_PATHS, OOS_PATHS_PER_MARKET, OUT, SUMMARY
        PRE_BOOT_PATHS, TAX_BOOT_PATHS, E3_PATHS, OOS_PATHS_PER_MARKET = 60, 12, 12, 20
        OUT, SUMMARY = Path("/tmp/cash_yield_probe_RESULTS.json"), Path("/tmp/cash_yield_probe_SUMMARY.txt")
        print(f"PROBE MODE: tiny path counts, scratch outputs {OUT}", flush=True)

    hs = harness(run_boot=(mode == "--run"), deep=(mode in ("--harness", "--run")))
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
            for label, ref_key in (("R2", "R2"), ("E2", "E2")):
                b = hs["bootstrap_control"][mode_][label]
                ref = BOOT_ANCHOR[mode_][ref_key]
                print(f"boot anchor {mode_} {label} (ref {ref_key}): ref {ref:.6f} repro {b:.6f} "
                      f"diff {(b - ref) * 100:+.3f} pp")
        ab = hs["bootstrap_control"].get("aall_bootstrap_cross_check") or {}
        if ab.get("median_delta_pp") is not None:
            print(f"A_ALL cross-check (tbill_sweep_v1, pre-tax bootstrap): ref {ab['reference_pp']:.3f} pp "
                  f"repro {ab['median_delta_pp']:.3f} pp diff {ab['diff_pp']:+.3f} pp "
                  f"(share ref {ab['reference_share']:.2f} repro {ab['share_gt_0']:.2f})")
    ra = hs["r2_anchors"]
    for k in sorted(ra["pre_tax"]):
        v = ra["pre_tax"][k]
        print(f"R2 anchor pre_tax {k}: cagr ref {v['reference_cagr'] * 100:.4f}% "
              f"repro {v['reproduced_cagr'] * 100:.4f}% | maxDD ref {v['reference_max_dd'] * 100:.4f}% "
              f"repro {v['reproduced_max_dd'] * 100:.4f}% | turnover ref {v['reference_turnover']:.4f} "
              f"repro {v['reproduced_turnover']:.4f}")
    for wn in TAX_WINDOWS:
        v = ra["after_tax_100k"][wn]
        print(f"R2 anchor after_tax {wn}: ref {[f'{x * 100:.3f}%' for x in v['reference']]} "
              f"repro {[f'{x * 100:.3f}%' for x in v['reproduced']]} -> {'PASS' if v['pass'] else 'FAIL'}")
    print(f"R2 anchor block (production-R2 reference row): {'PASS' if ra['pass'] else 'FAIL'}")
    ka = hs["k_anchors"]
    print(f"K control anchor block: {ka['n_compared']}/{ka['n_cells']} cells compared, "
          f"{ka['n_mismatch']} mismatches (tol {ka['tolerance']}), max|diff| {ka['max_abs_diff']:.2e}, "
          f"missing {ka['n_missing_sections']} -> {'PASS' if ka['pass'] else 'FAIL'}")
    for m in ka["mismatches"][:10]:
        print(f"  K anchor mismatch {m['cell']}: mine {m['mine']:.8f} ref {m['reference']:.8f} "
              f"diff {m['diff']:+.2e}")
    if hs.get("deep"):
        d = hs["deep"]
        print(f"deep control anchor block (pre/windows/cost/bootstrap): {d['n_compared']}/{d['n_cells']} cells, "
              f"{d['n_mismatch']} mismatches, max|diff| {d['max_abs_diff']:.2e}, "
              f"missing {d['n_missing_sections']} -> {'PASS' if d['pass'] else 'FAIL'}")
    print(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)")
    if (mode == "--harness" or not hs["pass"]) and not os.environ.get("CASH_YIELD_PROBE"):
        sys.exit(0 if hs["pass"] else 1)

    res = run(boot_ref=hs["deep"]["boot_ref"])
    g = res["gates"]["arms"]
    rc = res["reference_check"]
    pre, win, e1, e2, e3, boot_ref = res["pre"], res["windows"], res["e1"], res["e2"], res["e3"], res["boot_ref"]
    ctrl = win[GATE_CAPITAL]

    res["smoke"] = smoke()
    out = {
        "campaign": "cash_yield_v1",
        "params": {
            "arms": list(ARMS), "report_arms": list(REPORT_ARMS), "control": CONTROL,
            "candidates": list(CANDIDATES), "rule": RULE, "alpaca_spread": ALPACA_SPREAD,
            "z3_spread": Z3_SPREAD,
            "dec_tail": DEC_TAIL, "gain_tiers": [[t, b] for t, b in GAIN_TIERS], "b_up": B_UP,
            "idea": "idea I5 of docs/research/idea_survey_v4.md: idle cash earns a broker sweep "
                    "rate instead of zero",
            "yield_mechanics": (
                "accrual on the session's ENDING uninvested cash balance at rate[i-1]/252 (one "
                "session's lag, as the financing leg and the archived report arm A_ALL use); "
                "credited (and compounded) on the last session of each calendar month; the pending "
                "accrual is not NAV until credited. Alpaca's own documentation of the FDIC Bank "
                "Sweep: 'Interest accrues daily based on the day's ending swept balance' and it is "
                "'realized (credited) monthly on the last business day of the month', where it "
                "'stays invested within the sweep program to compound into yield'."),
            "yield_tax": {
                "Z1": "declared in full as menkul sermaye iradi at the progressive tariff, no "
                      "indexation, no declaration bar, same March/July instalments",
                "Z2": "as Z1 but with the 22,000 x scale(year) TRY declaration bar of GVK 86/1-c "
                      "against the year's TRY interest: at or below it nothing is declared, above "
                      "it the whole amount is (the bar is not an exemption)",
                "Z3": "as Z1 at DTB3 - 1.00 pp",
                "Z1x": "report-only upper bound: the interest is never taxed",
                "base": "the interest is added to the same year's declared base as the realised "
                        "capital gain, so it pays the tariff's marginal rate; a capital loss "
                        "(already floored at zero) cannot offset it (GVK 88)"},
            "signals": "production K (equity_leveraged_trend_v1 1.2.0): E2 2-of-3 on price closes "
                       "(SMA200, 252-session price return > 0, close > 126-session max/min midpoint) "
                       "+ early re-entry at 0.5 x vt while E2 off and close > SMA50, vol20 < its "
                       "252-session median and close > close 20 sessions ago; exit early below SMA50",
            "sizing": "vt = min(1, 0.35/vol20) on synthetic QLD*, cap 0.98",
            "instrument": "synthetic QLD* daily-reset 2x on QQQ total return, expense 0.95%/yr, "
                          "financing DTB3 + {base 0.0, calibrated 0.75%/yr}",
            "cost_per_side": COST_PER_SIDE, "cost_stress_m": M_STRESS,
            "main_window": pre["window"], "start": START,
            "after_tax": {"engine": "tax_engine.py (band_h2_combo_v1's copy + CashYieldAccount)",
                          "financing": "base", "m": M, "windows": TAX_WINDOWS,
                          "capitals": TAX_CAPITALS, "gate_capital": GATE_CAPITAL,
                          "clean_bootstrap": {"paths_pre": PRE_BOOT_PATHS, "paths_tax": TAX_BOOT_PATHS,
                                              "seed": SEED, "block": BLOCK,
                                              "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI},
                          "fresh_bootstrap": {"paths": E3_PATHS, "seed": E3_SEED, "blocks": list(E3_BLOCKS)}},
            "e1": {"rule": "R-INDEX", "start": E1_RINDEX_START, "financing": "calibrated", "m": M,
                   "capital": GATE_CAPITAL, "n_instruments": len(INSTRUMENTS),
                   "excluded": list(EXCLUDED_INSTRUMENTS)},
            "e2": {"starts": list(E2_START_YEARS), "capital": GATE_CAPITAL},
            "gates": res["gates"]["gate"],
            "adopted": res["gates"]["adopted"], "reason": res["gates"]["reason"],
            "failing_gates": res["gates"]["failing_gates"],
        },
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "reproduction_checks": hs["reproduction_checks"],
                    "r2_anchors": hs["r2_anchors"], "k_anchors": hs["k_anchors"],
                    "after_tax_anchors": hs["after_tax_anchors"],
                    "bootstrap_control_medians": hs["bootstrap_control"],
                    "deep_reference": {k: v for k, v in hs["deep"].items() if k != "boot_ref"}},
        "reference_check": {k: v for k, v in rc.items()},
        "smoke": res["smoke"],
        "pre_tax_main": pre,
        "pre_tax_main_r_usd": res["pre_usd"],
        "after_tax_windows": win,
        "cost_stress_m4": res["cost"],
        "oos": res["oos"],
        "oos_bootstrap_pooled": res["oos_boot"],
        "instruments": res["instruments"],
        "quality": res["quality"],
        "bootstrap_clean_seed20261011": boot_ref,
        "E1_after_tax_etfs_rindex": e1,
        "E2_rolling_starts": e2,
        "E3_fresh_bootstraps": e3,
        "E5": res["e5"],
        "gates": res["gates"],
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = fmt_pct
    w = pre["window"]
    L = [f"cash_yield_v1 | idle cash at DTB3 - {ALPACA_SPREAD * 100:.2f} pp (Z1/Z2), "
         f"DTB3 - {Z3_SPREAD * 100:.2f} pp (Z3), taxed as menkul sermaye iradi | "
         f"main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
         f"control = production K | arms = K + Z1 + Z2 + Z3 (+ report-only Z1x)",
         f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance); "
         f"K control anchors {'PASS' if hs['k_anchors']['pass'] else 'FAIL'} "
         f"({hs['k_anchors']['n_compared']}/{hs['k_anchors']['n_cells']} cells, max|diff| "
         f"{hs['k_anchors']['max_abs_diff']:.2e}); production-R2 reference block "
         f"{'PASS' if hs['r2_anchors']['pass'] else 'FAIL'}; R2 engine == simulate_band max|diff| "
         f"{hs['reproduction_checks']['r2_engine_equals_simulate_band']['max_abs_nav_diff']:.2e}",
         f"A_ALL cross-check (tbill_sweep_v1 pre-tax): "
         f"{hs['reproduction_checks']['aall_pre_tax_cross_check']}",
         f"all-cell control reference block: {rc['n_compared']}/{rc['n_cells']} cells, "
         f"{rc['n_mismatch']} mismatches (tol {rc['tolerance']}), max|diff| {rc['max_abs_diff']:.2e} -> "
         f"{'PASS' if rc['pass'] else 'FAIL'}",
         f"VERDICT: {res['gates']['adopted'] or 'no change (K stands)'} -- {res['gates']['reason']}", ""]

    L.append("per arm (pre-tax main window m = 2, R-INDEX; after tax at $100k, base, m = 2, liquidated):")
    L.append("  arm   CAGR base  CAGR cal   maxDD base  maxDD cal   turn/yr | dAT2000  dAT2010  dAT2017 "
             "(pp vs K) | boot d B1     B2   (pp) | OOS med d | gates")
    for arm in ARMS:
        a, ac = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        row = g[arm]["row"]
        ats = "".join(f"{row['after_tax_100k'][wn]['delta_vs_K_pp']:+9.3f}" for wn in TAX_WINDOWS)
        bc = "".join(f"{row['boot_after_tax_median_delta_pp'][md]:+7.3f}"
                     for md in ("progressive", "flat"))
        fl = "  ".join(f"{x}={'P' if g[arm]['gate_list'][x] else 'F'}"
                       for x in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "AT", "B1B2")
                       ) if arm in CANDIDATES else "  (control: no gates)"
        L.append(f"  {arm:4s} {p(a['cagr'])} {p(ac['cagr'])} {p(a['max_dd'])} {p(ac['max_dd'])} "
                 f"{a['turnover_annual']:9.2f} |{ats} |{bc} | {row['oos_median_delta_pp']:+9.3f} | {fl}")
    rx = win[GATE_CAPITAL]["2000_03_to_2026_09"]
    L.append(f"  R2 reference row (published, not re-run here): pre-tax 15.4766 / 14.8961 %, "
             f"max DD 46.7584 %, after tax at $100k 10.160 / 14.706 / 22.886 %; "
             f"K's own after tax at $100k is {rx[CONTROL]['after_tax_cagr'] * 100:.4f} / "
             f"{win[GATE_CAPITAL]['2010_01_to_end'][CONTROL]['after_tax_cagr'] * 100:.4f} / "
             f"{win[GATE_CAPITAL]['2017_01_to_end'][CONTROL]['after_tax_cagr'] * 100:.4f} %")
    L.append("")
    L.append(f"gross interest credited (main window, base, m = 2, $100k) and the tax it caused:")
    for arm in ARMS:
        r = rx[arm]
        L.append(f"  {arm:4s} gross {r['yield_usd_total']:>12,.0f} USD over {w['years']:.2f} yr "
                 f"({r['yield_usd_total'] / w['years'] / r['terminal_usd'] * 100:.3f} % of ending NAV/yr); "
                 f"TR interest income {r['yield_try_total']:>16,.0f} TRY; tax attributable to the interest "
                 f"{r['interest_tax_try_total']:>14,.0f} TRY")
    L.append("")
    L.append("gates (all against the control K; ewma_band_v1's G1-G9 plus the after-tax pair):")
    for arm in CANDIDATES:
        r = g[arm]
        gl = ", ".join(f"{k}={'PASS' if r['gate_list'][k] else 'FAIL'}" for k in r["gate_list"])
        L.append(f"  {arm}: {gl} -> "
                 + ("ALL PASS" if r["all_pass"] else "FAILS " + ",".join(res["gates"]["failing_gates"][arm])))
        L.append(f"    G1 m2 base/cal CAGR d {r['G1_detail']['base']['cagr_delta_pp']:+.3f}/"
                 f"{r['G1_detail']['calibrated']['cagr_delta_pp']:+.3f} pp "
                 f"(calmar {r['G1_detail']['base']['calmar_arm']:.3f} vs {r['G1_detail']['base']['calmar_K']:.3f}); "
                 f"G6 m1 {r['G6_detail']['base']['cagr_delta_pp']:+.3f}/{r['G6_detail']['calibrated']['cagr_delta_pp']:+.3f} pp")
        L.append(f"    G2 max DD d {r['G2_detail']['max_dd_delta_pp']['base']:+.2f}/"
                 f"{r['G2_detail']['max_dd_delta_pp']['calibrated']:+.2f} pp; "
                 f"G3 2017+ d {r['G3_detail']['cagr_2017_delta_pp']['base']:+.3f}/"
                 f"{r['G3_detail']['cagr_2017_delta_pp']['calibrated']:+.3f} pp; "
                 f"G4 sub-periods {r['G4_detail']['n_above']}/3 "
                 f"({', '.join(f'{k} {v:+.2f}' for k, v in r['G4_detail']['deltas_pp'].items())})")
        L.append(f"    G5 bootstrap median d base/cal {r['G5_detail']['median_delta_pp']['base']:+.3f}/"
                 f"{r['G5_detail']['median_delta_pp']['calibrated']:+.3f} pp "
                 f"(share>0 {r['G5_detail']['share_gt_0']['base'] * 100:.1f}/"
                 f"{r['G5_detail']['share_gt_0']['calibrated'] * 100:.1f} %)")
        L.append(f"    G7 OOS median d {r['G7_detail']['median_cagr_pp']:+.3f} pp, {r['G7_detail']['n_ok']}/6 "
                 f"inside bounds, worst d DD {r['G7_detail']['worst_dd_pp']:+.2f} pp; "
                 f"G8 pooled OOS bootstrap median d {r['G8_detail']['pooled_median_delta_pp']:+.3f} pp "
                 f"({r['G8_detail']['share_gt_0'] * 100:.1f} % > 0, {r['G8_detail']['paths']} paths)")
        L.append(f"    G9 turnover {r['G9_detail']['turnover_arm']:.3f} vs K {r['G9_detail']['turnover_K']:.3f} "
                 f"(ratio {r['G9_detail']['ratio']:.3f}); AT after-tax $100k "
                 + ", ".join(f"{wn} {v:+.3f}pp" for wn, v in r['AT_detail']['deltas_pp'].items()))
        for mode in ("progressive", "flat"):
            c = r["B1B2_detail"]["cells"][mode]
            L.append(f"    B1B2 {mode:12s} median {c['median_delta_pp']:+.3f}pp mean {c['mean_delta_pp']:+.3f}pp "
                     f"share>0 {c['share_gt_0'] * 100:.1f}% p05/p95 {c['p05_pp']:+.2f}/{c['p95_pp']:+.2f} -> "
                     f"{'PASS' if c['pass'] else 'FAIL'}")
        L.append(f"    reported (not decisive): E1 21 ETFs median {r['E1_report']['median_delta_pp']:+.3f}pp "
                 f"share>0 {r['E1_report']['share_gt_0'] * 100:.1f}% ({r['E1_report']['n_gt_0']}/21); "
                 f"E2 rolling starts {r['E2_report']['n_gt_0']}/{r['E2_report']['n_starts']} "
                 f"median {r['E2_report']['median_delta_pp']:+.3f}pp; "
                 f"E3 " + ", ".join(f"{k} {v['median_delta_pp']:+.3f}pp/{v['share_gt_0'] * 100:.0f}%"
                                    for k, v in r["E3_report"]["runs"].items()))
    L.append("")
    L.append(f"postponement counters (main window, m = 2, base, $100k, progressive):")
    for arm in ARMS:
        r = ctrl["2000_03_to_2026_09"][arm]
        L.append(f"  {arm:4s} postponements {r['postponements']:5d} (band {r['blocked_band']:5d}, "
                 f"December-deferred {r['deferred']:4d}) sells/yr {r['sells_per_year']:.2f}")
    L.append("")
    L.append("E1 after tax on the 21 quality-passing ETFs (R-INDEX, $100k, calibrated, m = 2, liquidated) "
             "-- reported, not decisive:")
    L.append("  instrument  window                     K AT     Z1 d      Z2 d      Z3 d")
    for key in INSTRUMENTS:
        r = e1[key]
        L.append(f"  {key:10s}  {r['window']['start']}..{r['window']['end']} {p(r[CONTROL]['after_tax_cagr'])}"
                 f"  {r['Z1']['delta_after_tax_pp']:+7.3f}  {r['Z2']['delta_after_tax_pp']:+7.3f}  "
                 f"{r['Z3']['delta_after_tax_pp']:+7.3f}")
    L.append("")
    L.append("E2 rolling January starts (after tax $100k, base, m = 2, to 2026-09-29) -- reported:")
    L.append("  year   start        K AT      Z1 d       Z2 d       Z3 d")
    for yr in E2_START_YEARS:
        r = e2[str(yr)]
        L.append(f"  {yr}   {r['start']}  {p(r[CONTROL]['after_tax_cagr'])} {r['Z1']['delta_after_tax_pp']:+8.3f} "
                 f"{r['Z2']['delta_after_tax_pp']:+9.3f} {r['Z3']['delta_after_tax_pp']:+9.3f}")
    L.append("")
    L.append(f"E3 fresh after-tax bootstraps (seed {E3_SEED}, {E3_PATHS} paths, $100k, blocks {E3_BLOCKS}) -- reported:")
    L.append("  run                    arm    d vs K   share>0")
    for key in sorted(e3):
        for arm in CANDIDATES:
            v = e3[key][f"{arm}_vs_{CONTROL}"]
            L.append(f"  {key:20s} {arm:4s} {v['median_delta_cagr_pp']:+10.3f} {v['share_gt_0'] * 100:8.1f}%")
    L.append("")
    L.append("E5 (report only): fill-decision differences on the main path (pre-tax, $100k, m = 2)")
    dec = res["e5"]["decisions"]
    for pair in ("Z1_vs_K", "Z2_vs_K", "Z3_vs_K", "Z2_vs_Z1", "Z3_vs_Z1"):
        a, b = pair.split("_vs_")
        d = dec[pair]
        L.append(f"  {pair:12s}: {d['sessions_decision_differs']:5d} sessions differ "
                 f"({d['share_decision_differs'] * 100:.2f}%); {a} sells where {b} holds "
                 f"{d[f'{a}_sells_{b}_holds']}, {a} holds where {b} sells {d[f'{a}_holds_{b}_sells']}")
    L.append(f"  sells (downward resizes+exits) per arm: "
             + ", ".join(f"{a} {dec['sells'][a]}" for a in ARMS))
    for cl in TAX_CAPITALS:
        L.append(f"  after tax at ${cl} (progressive, liquidated, base, m = 2):")
        for wn in TAX_WINDOWS:
            c0 = win[cl][wn][CONTROL]["after_tax_cagr"]
            L.append(f"    {wn:20s} K {p(c0)} | " + " ".join(
                f"{arm} {p(win[cl][wn][arm]['after_tax_cagr'])} "
                f"{(win[cl][wn][arm]['after_tax_cagr'] - c0) * 100:+7.3f}pp"
                for arm in tuple(CANDIDATES) + REPORT_ARMS))
    L.append("")
    L.append("unliquidated (mark-to-market) after tax at $100k -- the view the owner holds:")
    for wn in TAX_WINDOWS:
        c0 = win[GATE_CAPITAL][wn][CONTROL]["after_tax_cagr_unliquidated"]
        L.append(f"  {wn:20s} K {p(c0)} | " + " ".join(
            f"{arm} {p(win[GATE_CAPITAL][wn][arm]['after_tax_cagr_unliquidated'])} "
            f"{(win[GATE_CAPITAL][wn][arm]['after_tax_cagr_unliquidated'] - c0) * 100:+7.3f}pp"
            for arm in tuple(CANDIDATES) + REPORT_ARMS))
    L.append("")
    L.append("m = 4 cost stress, after tax at $100k:")
    for wn in TAX_WINDOWS:
        c0 = res["cost"][wn][CONTROL]["after_tax_cagr"]
        L.append(f"  {wn:20s} K {p(c0)} | " + " ".join(
            f"{arm} {p(res['cost'][wn][arm]['after_tax_cagr'])} "
            f"{(res['cost'][wn][arm]['after_tax_cagr'] - c0) * 100:+7.3f}pp" for arm in CANDIDATES))
    L.append("")
    L.append("six OOS windows (pre-tax, calibrated, m = 2, R-USD rule) -- delta vs K:")
    L.append("  window            K CAGR    K maxDD     Z1 d      Z2 d      Z3 d   (CAGR pp)")
    for wn_ in OOS_WINDOWS:
        r = res["oos"][wn_]
        L.append(f"  {wn_:16s} {p(r[CONTROL]['cagr'])}  {p(r[CONTROL]['max_dd'])}  "
                 f"{r['Z1']['delta_cagr_pp']:+8.3f} {r['Z2']['delta_cagr_pp']:+9.3f} "
                 f"{r['Z3']['delta_cagr_pp']:+9.3f}")
    L.append("")
    L.append("pooled OOS bootstrap over M1-M5 (G8), pre-tax, delta vs K:")
    for arm in CANDIDATES:
        v = res["oos_boot"]["pooled"][arm]
        L.append(f"  {arm:4s} median {v[f'diff_vs_{CONTROL}_median_pp']:+.3f}pp mean "
                 f"{v[f'diff_vs_{CONTROL}_mean_pp']:+.3f}pp share>0 {v['share_gt_0'] * 100:.1f}% "
                 f"({v['paths']} paths)")
    L.append("")
    L.append("control reference reproduction: " + f"{rc['n_compared']}/{rc['n_cells']} cells, "
             f"{rc['n_mismatch']} mismatches, max|diff| {rc['max_abs_diff']:.2e} -> "
             f"{'PASS' if rc['pass'] else 'FAIL'}")
    L.append("smoke (synthetic): " + ("PASS" if res["smoke"]["pass"] else "FAIL")
             + f" -- {sum(1 for v in res['smoke']['checks'].values() if v)}/{len(res['smoke']['checks'])} checks")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
