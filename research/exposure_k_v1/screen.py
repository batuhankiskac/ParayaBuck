#!/usr/bin/env python3
"""exposure_k_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Idea I1 of docs/research/idea_survey_v4.md: the after-tax exposure frontier under K. Control =
production K (research/band_h2_combo_v1's arm "K" = E2 + R2 early re-entry + A1u band + H2
deferral); the single change in every candidate arm is the vol-target level (`vt`).

  K   production K, exactly: vt = min(1, 0.35/vol20) on synthetic QLD*, cap 0.98, and the
      execution layer A1u (gain-scaled downsize band 0.5 / 3 / 8 pp at a USD FIFO gain fraction
      <= 1 % / <= 5 % / > 5 %) + H2 (a non-exit downward resize whose fill session is one of the
      last 10 December sessions is postponed). The control; its cells must reproduce
      research/band_h2_combo_v1/RESULTS.json's "K" cells (deep harness).
  R2  reference row only: production R2 (band off, no deferral, vt = 0.35) -- band_h2_combo_v1's
      control. Never a candidate.
  F1  vt = 0.375 (a 1/14 increase), everything else K.        (pre-registered, survey I1)
  F2  vt = 0.40  -- reproduces vt_level_tax_v1's cell under the new control.
  F3  vt = 0.45  -- top of the measured grid; the frontier's endpoint.

Stages (pre-registered before any non-control arm was simulated on real data):
  pre      pre-tax main window, base/calibrated x m = 1/2/4 plus the R-USD row.
  AT       after tax at $100k (gate capital) and $30k/$300k/$10k/$1M on the three windows.
  B12      clean bootstrap, seed 20261011, block 20, 1,000 paths, $100k (+$1M report-only),
           B1-recent and B2-flat, 5,000 pre-tax paths (the tail-gate and G5/E4 leg).
  E1       after tax on the 21 quality-passing ETFs under R-INDEX, $100k, calibrated, m = 2.
  E2       rolling January starts 2001..2021, after tax at $100k, base, m = 2.
  E3       fresh after-tax bootstraps, seed 20261015, blocks 10 and 40, 1,000 paths, $100k.
  E4       the pre-tax leg of B12 (seed 20261011, base and calibrated) + main-window max DD.
  E5       report only: $10k-$1M after-tax windows, the m = 4 cost stress, decision traces.
  OOS      the six ewma_band_v1 windows, pre-tax, calibrated, m = 2 (G7/G8).

Gates (pre-registered; see PREREGISTRATION.md section 5). Versus K (the control): G1-G9 as
ewma_band_v1 defines them with G9 = turnover <= 2 x K, the leverage_frontier_v1 tail gate
P(DD > 70 %) <= K's + 5 pp, after tax at $100k in all three windows, and the clean bootstrap
B1-recent/B2 at $100k with median Delta >= 0 and share > 0 >= 55 %.

Selection (pre-registered, survey I1 -- this rule DECIDES): the highest after-tax CAGR at $100k
among F1-F3 whose pre-tax max DD <= K + 1.0 pp (base and calibrated, m = 2) and whose G1-G9 all
pass; if none, close the exposure axis with the frontier table and do not re-run it. The common
protocol's stricter after-tax/bootstrap gate is reported per arm and its verdict stated; where
the two disagree, the survey rule decides.

Modes
  --smoke    synthetic random-walk prices only, no market data.
  --harness  reproduction on real data: K's published anchors (pre-tax 15.7649 / 15.1785 % CAGR,
             max DD 46.670 / 46.795 %, turnover 5.4561, m = 1 16.0173 %; after tax at $100k
             10.753 / 15.235 / 23.972 %), the R2 reference row, the pure-E2 anchor series, the
             R2 arm == `simulate_band`; then the deep part -- every K cell of
             research/band_h2_combo_v1/RESULTS.json that this run produces.
  --run      harness (deep), then the stages, the reference comparison, the gates and the
             selection rule. Writes RESULTS.json, SUMMARY.txt.

One engine serves every stage: `engine_run` is a stateful session loop over the Turkish after-tax
account (research/exposure_k_v1/tax_engine.py, a byte copy of
research/band_h2_combo_v1/tax_engine.py = research/gain_band_v3/tax_engine.py) with production
R2's execution layer plus the arm's fill rule. Every arm label except the candidates shares R2's
target series; the candidates' target series differ from K's only in the vol-target constant, so
each arm is simulated on its own `wq`. Engine helpers (synthetic 2x, SMA/TSM/DON, vol, vt,
metrics) come from research/trend_robustness_v2/screen.py (its main() is never run).
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

# ---- the arm rule (pre-registered; see PREREGISTRATION.md sections 3 and 4)
# The control is production K = A1u + H2 (band_h2_combo_v1's arm "K", verbatim). The candidates
# change exactly one parameter: the vol-target level VT. R2 is the protocol's reference row.
ARMS = ("K", "R2", "F1", "F2", "F3")
ARMS_ALL = ARMS
CONTROL = "K"
BASELINE = "R2"         # reference row only; never a candidate
CANDIDATES = ("F1", "F2", "F3")
BANDED = {"band": True, "usd_rule": True, "defer": True}    # production K's execution layer
PLAIN = {"band": False, "usd_rule": False, "defer": False}  # production R2's execution layer
RULE = {
    "K": dict(BANDED),
    "R2": dict(PLAIN),
    "F1": dict(BANDED), "F2": dict(BANDED), "F3": dict(BANDED),
}
RULE["E2"] = dict(PLAIN)  # alias: the harness's pure-E2 fidelity anchor series (band off)
VT = {"K": 0.35, "R2": 0.35, "F1": 0.375, "F2": 0.40, "F3": 0.45}  # the only changed parameter
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
E5_CAPITALS = ("10k", "30k", "300k", "1M")   # report-only
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

# ---- gates (pre-registered; see PREREGISTRATION.md section 5). Every gate is versus K (C0).
G1_MIN_CAGR_PP = 0.0          # m = 2, both financings: CAGR >= K, or Calmar >= 1.1 x K with
G1_CALMAR_MULT = 1.1          # ... CAGR >= K - 0.5 pp
G1_RELAX_PP = -0.5
G2_MAX_DD_PP = 1.0            # max DD <= K + 1.0 pp (base and calibrated) -- the survey's budget
G3_MIN_CAGR_PP = -1.0         # 2017+ CAGR >= K - 1.0 pp (base and calibrated)
G4_MIN_SUBPERIODS = 2         # CAGR above K in >= 2 of P1 2000-08 / P2 2009-16 / P3 2017+
G5_MIN_MEDIAN_PP = 0.0        # block-20 pre-tax bootstrap median (arm - K) >= 0 (base and cal.)
G7_MIN_SHARE = 5 / 6          # >= 5 of the 6 OOS windows with dCAGR >= -0.5 pp and dDD <= +3 pp
G7_MIN_DCAGR_PP = -0.5
G7_MAX_DDD_PP = 3.0
G7_MAX_DDD_ANY_PP = 8.0       # dDD never above this, in any window
G8_MIN_MEDIAN_PP = 0.0        # pooled OOS bootstrap median (arm - K) >= 0
G9_MAX_TURNOVER_MULT = 2.0    # turnover <= 2 x K
TAIL_MAX_DD = 0.70            # leverage_frontier_v1's tail gate
TAIL_MAX_EXTRA_PP = 5.0       # P(DD > 70 %) <= K's + 5 pp
TAIL_MAX_DD_LEVEL = 0.65      # its second leg: max DD <= 65 % (implied by G2 here)
# after-tax gates (this study's, plus the common protocol's)
AT_MIN_DELTA_PP = 0.0         # after tax at $100k: arm - K >= 0 in all three windows
BOOT_MIN_MEDIAN_PP = 0.0      # clean bootstrap B1-recent and B2 at $100k: median Delta >= 0
BOOT_PROTOCOL_SHARE = 0.55    # ... common protocol's share > 0 floor
BOOT_SURVEY_SHARE = 0.50      # ... the survey's share > 0 floor
# selection (pre-registered, survey I1): highest after-tax CAGR at $100k among arms with
# pre-tax max DD <= K + 1.0 pp and G1-G9 all pass.
SELECT_DD_BUDGET_PP = 1.0
# ---- v3 reporting stages E1-E4 (reused; report-only for the candidates vs K)
E1_MIN_SHARE = 0.60            # share of the 21 ETFs with after-tax delta > 0
E1_RINDEX_START = "2000-03-09"  # the TRY calendar's start; E1 start = max(ETF warm-up, this)
E2_START_YEARS = tuple(range(2001, 2022))   # 21 January starts, 2001..2021
E2_MIN_SHARE = 0.70            # parent thresholds, for the "vs C0" reporting block only
E3_SEED = 20261015
E3_BLOCKS = (10, 40)
E3_PATHS = 1_000
E3_MIN_SHARE = 0.55
E4_MIN_MEDIAN_PP = -0.10
E4_MAX_DD_PP = 1.0
# ---- published reference evidence
# The control of this study is production K, published by research/band_h2_combo_v1. Its stored
# RESULTS.json is the reproduction target of the deep harness: every K cell this run produces is
# compared against it cell-by-cell.
KREF = ROOT / "research/band_h2_combo_v1/RESULTS.json"
KREF_SUMMARY = ROOT / "research/band_h2_combo_v1/SUMMARY.txt"

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
# production R2 anchors (the reference row). REF_R2 = research/fast_reentry_v1/RESULTS.json,
# main_window; TAX_ANCHORS = research/vt_level_tax_v1 + r2_tbill_combo_v1 at $100k. The *control*
# of this study is production K, asserted against REF_K / TAX_ANCHORS["K"] (band_h2_combo_v1's
# published cells: 15.7649 / 15.1785 % CAGR, max DD 46.670 / 46.795 %, turnover 5.4561, m = 1
# 16.0173 %; after tax at $100k 10.753 / 15.235 / 23.972 %). REF_E2 / TAX_ANCHORS["E2"] are the
# pure-E2 reference series, not the control.
REF_R2 = {  # (cagr, max_dd, turnover)
    ("base", 1): (0.157538, 0.464143, 5.991522),
    ("base", 2): (0.154766, 0.467584, 5.991522),
    ("calibrated", 1): (0.151719, 0.465364, 5.991496),
    ("calibrated", 2): (0.148961, 0.468797, 5.991496),
}
REF_K = {  # research/band_h2_combo_v1/RESULTS.json, arm "K" (cagr, max_dd, turnover)
    ("base", 1): (0.160173, 0.463529, 5.456781),
    ("base", 2): (0.157649, 0.466698, 5.456136),
    ("calibrated", 1): (0.154303, 0.464786, 5.456216),
    ("calibrated", 2): (0.151785, 0.467948, 5.456216),
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
    "K": {
        "2000_03_to_2026_09": (0.157615, 0.107533, 0.466698),
        "2010_01_to_end": (0.223435, 0.152354, 0.446272),
        "2017_01_to_end": (0.330215, 0.239720, 0.349774),
    },
}
BOOT_ANCHOR = {"progressive": {"E2": 0.03079, "R2": 0.035155, "K": 0.038577},
               "flat": {"E2": 0.035201, "R2": 0.039678, "K": 0.040376}}
BOOT_PRE_ANCHOR = {"base": 0.077761, "calibrated": 0.073137}  # K's pre-tax leg medians


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


def vt_of(vol, target):
    """vt(vol) = min(1, target/vol), the local copy of trv2.vt_of with the *arm's* target
    (trv2's module-level VOL_TARGET is 0.35 and is never used here). vol <= 0 -> 1.0."""
    v = np.asarray(vol)
    return np.where(v > 0, np.minimum(1.0, target / np.where(v > 0, v, 1.0)), 1.0)


def sized_wants(on_eff, factor, vol_instr, target):
    """`fast_reentry_v1.arm_signals`' sizing: min(1, target/vol20) x the path factor, cap 0.98."""
    vt = vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)), target)
    raw = np.atleast_2d(np.asarray(on_eff, dtype=float)) * np.atleast_2d(factor) * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def arm_signals(price, e2_on, vol_instr, arm, comps=None):
    """Target weights [P, T] of one arm. "K" and "R2" share R2's target series (they differ only
    in the fill rule and, for the candidates, in VT); "E2" is the anchor label for the pure-E2
    reference series. Any other label raises, so a typo can never silently fall through to the
    E2 path (the gain_band_v1 bug)."""
    if arm not in RULE or arm not in VT:
        raise ValueError(f"unknown arm label {arm!r}")
    target = VT[arm]
    if arm == "E2":
        on_eff = np.atleast_2d(np.asarray(e2_on, dtype=bool)).copy()
        factor = np.ones_like(on_eff, dtype=float)
    else:
        on_eff, factor = r2_state(price, e2_on, comps)
    return sized_wants(on_eff, factor, vol_instr, target), None


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
               trace=False):
    """One arm through the Turkish after-tax account, session by session.

    mode in {"pre_tax", "progressive", "flat"}; arm in ARMS. Every arm shares the target series
    `wq` (production R2's, for every arm label including "C0"); only the fill decision differs:
    entries, exits to zero and up-resizes always fill (0.5 pp band), while a discretionary
    downward resize (`tgt > 0`, `tgt < w`) is subject to the arm's band (A1u's gain tiers)
    and/or its December window (H2: postponed when the fill session is one of the last
    DEC_TAIL December sessions). A downsize
    between two positive targets is a downward resize whatever its size, so the rule also covers
    the transfer between R2's full weight and its half-weight early-re-entry leg. `rule_index`
    selects the R-INDEX (True) or R-USD (False) instantiation of the decision state. With arm
    "C0" this is exactly research/tax_harvest_v2/screen.py:tax_run_overlay(overlay="none")."""
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
        "deferred": int(deferred),
        "postponements_by_year": {int(k): int(v) for k, v in sorted(post_by_year.items())},
        "deferrals_by_year": {int(k): int(v) for k, v in sorted(defer_by_year.items())},
        "fees_usd": float(qa.fees), "index_relief_try": float(qa.index_relief_try),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.gain_by_reason.items())},
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
    w_e2, W = targets_of(price, e2, vol_instr, ARMS, comps)  # W[arm] = that arm's target series
    return days, o, c, w_e2, W, e2


def targets_of(price, e2, vol_instr, arms=ARMS, comps=None):
    """(pure-E2 anchor series [P, T], {arm: [P, T]}) for one price series; one state loop feeds
    every arm. Arms that differ only in the fill rule (K vs R2) share a bit-identical target
    series; the candidates' series are built from the same (on_eff, factor, vol20) triple and
    differ in the VT constant alone. Index 0 is the harness fidelity anchor."""
    comps = early_components(price) if comps is None else comps
    on_eff, factor = r2_state(price, e2, comps)
    e2_arr = np.atleast_2d(np.asarray(e2, dtype=bool))
    v = np.atleast_2d(np.asarray(vol_instr, dtype=float))
    w_e2 = sized_wants(e2_arr, np.ones_like(factor), v, VT["K"])[0]
    cache, out = {}, {}
    for arm in arms:
        if arm == "E2":
            out[arm] = w_e2
            continue
        t = VT[arm]
        if t not in cache:
            cache[t] = sized_wants(on_eff, factor, v, t)[0]
        out[arm] = cache[t]
    return w_e2, out


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
    """One OOS window: own E2 + own R2 early path, own 2x leg (calibrated, m = 2), R-USD rule.
    Every arm runs its own target series (the candidates differ from K in VT only)."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)[0]
    comps = early_components(c)
    _w_e2, W = targets_of(c, e2, trv2.vol_n(sc)[0], arms, comps)
    mkt = usd_market(days)
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        r = engine_run(1.0, start, len(days) - 1, days, mkt, so, sc, W[arm], e2, arm, "pre_tax",
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
    """Own E2 + own R2 early path on the instrument's adjusted close, own 2x leg (R-USD rule)."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)[0]
    comps = early_components(c)
    _w_e2, W = targets_of(c, e2, trv2.vol_n(sc)[0], arms, comps)
    mkt = usd_market(days)
    res = {}
    for arm in arms:
        r = engine_run(1.0, start, len(days) - 1, days, mkt, so, sc, W[arm], e2, arm, "pre_tax",
                       m=M, rule_index=False)
        st = window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


# ----------------------------------------------------------------------------- bootstrap E1 (fixed 2015-09..2026-09 calendar)
def boot(seed=SEED, block=BLOCK, arms=ARMS_ALL, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
         capitals=("100k", "1M"), modes=("progressive", "flat"), anchor_e2=True,
         deltas=CANDIDATES, baselines=(CONTROL,), label="B12", tail_dd=True):
    """Block bootstrap on the fixed 2015-09 -> 2026-09 calendar with its realised USD/TRY and
    Yİ-ÜFE; market returns (QQQ overnight/intraday/dividend + DTB3) resampled exactly as
    tax_asym_v2 / vt_level_tax_v1 / tax_harvest_v2 / band_h2_combo_v1. Pre-tax at `n_pre` paths
    under both financing models; after-tax `modes` at the first `n_tax` paths.

    The path draws depend only on (seed, block, n_paths) and the path index, so the cells of any
    arm are unchanged by the presence of other arms -- that is what makes the K cells here
    directly comparable with band_h2_combo_v1's stored ones. `tail_dd` also records each path's
    max drawdown, for the leverage_frontier_v1 tail gate: P(DD > 70 %) on the 10-year measured
    window (2,520 sessions), pre-tax base and after-tax at each capital."""
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
    dd_pre = {(md, a): [] for md in FIN for a in arms} if tail_dd else {}
    at = {cl: {mode: {a: [] for a in arms} for mode in modes} for cl in capitals}
    dd_at = {cl: {mode: {a: [] for a in arms} for mode in modes} for cl in capitals} if tail_dd else {}
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
        # one state loop per path; the sizing differs per arm (VT) and per financing model only
        w_e2, W = targets_of(pc, e2, trv2.vol_n(sc)[0], arms, comps)
        _w_e2c, Wc = targets_of(pc, e2, trv2.vol_n(sc_c)[0], arms, comps)
        legs = {"base": (so[0], sc[0], W), "calibrated": (so_c[0], sc_c[0], Wc)}
        if pidx < n_pre:
            for md, (o_, c_, want) in legs.items():
                for arm in arms:
                    r = engine_run(PRE_CAPITAL, i0, i1, fixed_dates, mkt, o_, c_, want[arm], e2,
                                   arm, "pre_tax", m=M)
                    k = len(r["_nav"]) - 1
                    pre[(md, arm)].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
                    if tail_dd:
                        dd_pre[(md, arm)].append(r["max_dd_nav_usd"])
        if pidx < n_tax:
            o_, c_, want = legs["base"]
            for cap_label in capitals:
                for mode in modes:
                    for arm in arms:
                        r = engine_run(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, o_, c_,
                                       want[arm], e2, arm, mode, m=M)
                        if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                            raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode} {cap_label}")
                        at[cap_label][mode][arm].append(r["cagr_usd_liquidated"])
                        if tail_dd:
                            dd_at[cap_label][mode][arm].append(r["max_dd_nav_usd"])
                    if anchor_e2:
                        # anchor label only: pure E2 targets under R2's (band-off) fill rule
                        r = engine_run(TAX_CAPITALS[GATE_CAPITAL], i0, i1, fixed_dates, mkt, o_, c_,
                                       w_e2, e2, "R2", mode, m=M)
                        anchor_e2_out[mode].append(r["cagr_usd_liquidated"])
        if (pidx + 1) % 500 == 0:
            print(f"  {label} path {pidx + 1}/{n_paths}", flush=True)

    out = {"n_paths_pre": n_pre, "n_paths_tax": n_tax, "seed": seed, "block": block,
           "arms": list(arms), "calendar_start": fixed_dates[0], "calendar_end": fixed_dates[-1],
           "tail_gate": {"threshold": TAIL_MAX_DD, "measured_sessions": i1 - i0 + 1},
           "pre": {}, "tax": {}}
    for md in (FIN if n_pre > 0 else ()):   # no pre-tax paths -> no pre-tax block
        base = np.array(pre[(md, CONTROL)])
        out["pre"][md] = {}
        for arm in arms:
            c = np.array(pre[(md, arm)])
            d = c - base
            out["pre"][md][arm] = {
                "cagr_median": float(np.median(c)), "diff_vs_C0_median_pp": float(np.median(d) * 100),
                "diff_vs_C0_mean_pp": float(np.mean(d) * 100), "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100), "p95_delta_pp": float(np.percentile(d, 95) * 100)}
            if tail_dd:
                dd = np.array(dd_pre[(md, arm)])
                out["pre"][md][arm]["median_max_dd"] = float(np.median(dd))
                out["pre"][md][arm]["p_dd_gt_70"] = float(np.mean(dd > TAIL_MAX_DD))
    for cl in capitals:
        out["tax"][cl] = {}
        for mode in modes:
            e = {"n_paths": n_tax, "capital_usd": TAX_CAPITALS[cl]}
            for arm in arms:
                e[arm] = {"cagr_median": float(np.median(at[cl][mode][arm]))}
                if tail_dd:
                    dd = np.array(dd_at[cl][mode][arm])
                    e[arm]["median_max_dd"] = float(np.median(dd))
                    e[arm]["p_dd_gt_70"] = float(np.mean(dd > TAIL_MAX_DD))
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
                    if tail_dd:
                        dd = np.array(dd_at[cl][mode][arm]) - np.array(dd_at[cl][mode][ctrl])
                        e[f"{arm}_vs_{ctrl}"]["median_delta_max_dd_pp"] = float(np.median(dd) * 100)
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
        _w_e2, W = targets_of(cc, e2, vol_instr, arms)
        for arm in arms:
            want = W[arm]   # that arm's own target series (candidates differ in VT only)
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
    global DEC_TAIL, GAIN_TIERS
    n = 1600
    o, price, y = synthetic_bars(n, 7)
    days = _trading_days(n)
    so, sc = trv2.synthetic_2x(o, price, np.zeros(n), y, EXP_Q)
    e2 = e2_up(price)[0]
    v20 = trv2.vol_n(sc)[0]
    w_e2, W = targets_of(price, e2, v20, ARMS, early_components(price))
    w_k, w_r2 = W["K"], W["R2"]
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

    # 1c. identity checks for this study's arm set.
    # (i) R2's layer (band off, no deferral) == the reference `simulate_band`.
    # (ii) With the gain tiers neutralised (B_down = 0.5 pp = the base band, so the A1u band can
    #      never block) K reduces to the H2 overlay: with DEC_TAIL = 0 it must equal
    #      `simulate_band` and with DEC_TAIL = 10 the reference `simulate_band_overlay`, on a
    #      target that oscillates between two positive weights so the December window sees real
    #      non-exit downward resizes.
    # (iii) With the real tiers, K's band and deferral both fire and K's NAV differs from R2's.
    ref_none, _, _ = simulate_band(so[None, :], sc[None, :], w_r2[None, :], COST_PER_SIDE * 2,
                                   START, C0_BAND)
    r2e = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_r2, e2, "R2", "pre_tax")
    checks["R2_equals_reference_simulate_band"] = bool(np.max(np.abs(r2e["_nav"] - ref_none[0])) < 1e-9)
    w_osc = np.where((np.arange(n) // 60) % 2 == 0, 0.98, 0.49)
    dt10 = december_tail(days, START, n - 1, DEC_TAIL)
    ref_osc_plain, _, _ = simulate_band(so[None, :], sc[None, :], w_osc[None, :], COST_PER_SIDE * 2,
                                        START, C0_BAND)
    ref_osc_defer, _, _ = simulate_band_overlay(so[None, :], sc[None, :], w_osc[None, :],
                                                COST_PER_SIDE * 2, START, C0_BAND, dec_tail=dt10)
    keep_tiers, GAIN_TIERS = GAIN_TIERS, ((math.inf, C0_BAND),)
    keep_tail, DEC_TAIL = DEC_TAIL, 0
    k_inert_plain = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_osc, e2, "K", "pre_tax")
    DEC_TAIL = keep_tail
    k_inert_defer = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_osc, e2, "K", "pre_tax")
    GAIN_TIERS = keep_tiers
    checks["K_neutral_band_empty_window_equals_simulate_band"] = bool(
        np.max(np.abs(k_inert_plain["_nav"] - ref_osc_plain[0])) < 1e-9)
    checks["K_neutral_band_equals_reference_overlay_tail10"] = bool(
        np.max(np.abs(k_inert_defer["_nav"] - ref_osc_defer[0])) < 1e-9 and len(dt10) > 0)
    checks["K_neutral_band_defers_only_in_December"] = bool(
        k_inert_defer["deferred"] > 0 and k_inert_defer["deferred"] == k_inert_defer["postponements"]
        and k_inert_defer["blocked_band"] == 0 and len(k_inert_defer["deferrals_by_year"]) > 0)
    k_sig = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_k, e2, "K", "pre_tax")
    f2_sig = engine_run(1.0, START, n - 1, days, mkt, so, sc, W["F2"], e2, "F2", "pre_tax")
    checks["K_nav_differs_from_R2"] = bool(np.max(np.abs(k_sig["_nav"] - r2e["_nav"])) > 1e-6)
    checks["candidate_target_series_move_the_rule"] = bool(
        np.max(np.abs(k_sig["_nav"] - f2_sig["_nav"])) > 1e-6
        and not np.array_equal(w_k, W["F2"]))

    # 2. fast rolling vol median == the loop reference
    pv = trv2.vol_n(100 * np.cumprod(1 + rng.normal(0, 0.01, 1400)))
    checks["rolling_median_fast_equals_ref"] = bool(np.array_equal(
        _rolling_median_vol(pv), _rolling_median_vol_ref(pv), equal_nan=True))

    # 2b. the split state/sizing helpers == the reference arm_signals, the vt copy == trv2's at
    # 0.35, and R2/K share a target series while the candidates differ from both
    w_ref = arm_signals(price, e2, v20, "R2")[0][0]
    on_eff, factor = r2_state(price, e2, early_components(price))
    checks["r2_state_sizing_matches_arm_signals"] = bool(
        np.array_equal(w_ref, sized_wants(on_eff, factor, v20, VT["R2"])[0]))
    checks["targets_of_matches_arm_signals"] = bool(
        np.array_equal(W["R2"], w_ref) and np.array_equal(W["K"], w_ref))
    checks["local_vt_of_035_equals_trv2"] = bool(
        np.allclose(vt_of(v20, 0.35), trv2.vt_of(v20), equal_nan=True))
    checks["candidate_vt_raises_mean_weight_in_trend"] = bool(
        np.mean(W["F1"]) > np.mean(W["K"]) and np.mean(W["F2"]) > np.mean(W["F1"])
        and np.mean(W["F3"]) > np.mean(W["F2"]))
    checks["pure_E2_series_differs_from_R2"] = bool(
        not np.array_equal(w_e2, w_ref))

    # 3. the R2 arm == the reference simulate_band (base, m = 2)
    ref_nav, _, _ = simulate_band(so[None, :], sc[None, :], w_r2, COST_PER_SIDE * 2, START, C0_BAND)
    checks["r2_engine_equals_simulate_band"] = bool(np.max(np.abs(r2e["_nav"] - ref_nav[0])) < 1e-9)

    # 4. the mechanism fires: a sustained synthetic uptrend must block at least one downsize
    r = np.full(1500, 0.0012)
    price_up = 100.0 * np.cumprod(1 + r)
    o_up = np.concatenate([[price_up[0]], price_up[:-1]])
    so_u, sc_u = trv2.synthetic_2x(o_up, price_up, np.zeros(1500), np.full(1500, 0.01), EXP_Q)
    days_u = _trading_days(1500)
    ufe_u = {f"{yr}-{mo:02d}": float(1000 + 60 * yr + 8 * mo) for yr in range(2000, 2030) for mo in range(1, 13)}
    mkt_u = te.Market(days_u, np.full(1500, 30.0), ufe_u)
    e2_u = e2_up(price_up)[0]
    _wc_u, W_u = targets_of(price_up, e2_u, trv2.vol_n(sc_u)[0], ARMS)
    blocked = {}
    for arm in ARMS:
        rr = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, W_u[arm],
                        e2_u, arm, "pre_tax")
        blocked[arm] = {"band": rr["blocked_band"], "deferred": rr["deferred"],
                        "postponed": rr["postponements"]}
    checks["band_fires"] = bool(blocked["K"]["band"] > 0 and blocked["F2"]["band"] > 0)
    checks["deferral_fires"] = bool(blocked["K"]["deferred"] > 0)
    checks["R2_blocks_nothing"] = bool(blocked["R2"]["postponed"] == 0)
    checks["postponements_decompose"] = bool(all(
        blocked[a]["postponed"] == blocked[a]["band"] + blocked[a]["deferred"] for a in ARMS))

    # 4b. the tax modes bite in a trending series: progressive <= pre-tax, and flat < pre-tax
    flat_bites = prog_bites = False
    for arm in ARMS:
        want_u = W_u[arm]
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

    # 4c. the deferral must be scoped to December: with the real tiers, every postponement the
    # K arm records outside the empty-window run is a December one, and the counters decompose.
    dt10u = december_tail(days_u, START, 1499, DEC_TAIL)
    keep_tail, DEC_TAIL = DEC_TAIL, 0
    k_u_nod = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, W_u["K"], e2_u, "K", "pre_tax")
    DEC_TAIL = keep_tail
    k_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, W_u["K"], e2_u, "K", "pre_tax")
    checks["K_empty_window_defers_nothing"] = bool(
        k_u_nod["deferred"] == 0 and k_u_nod["postponements"] == k_u_nod["blocked_band"])
    checks["K_deferral_scoped_to_December"] = bool(
        len(dt10u) > 0 and k_u["deferred"] > 0
        and k_u["postponements"] >= k_u_nod["postponements"]
        and k_u["postponements"] == k_u["blocked_band"] + k_u["deferred"])

    # 5. finite, positive, and after tax <= pre-tax
    for arm in ARMS:
        want = W[arm]
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


def harness():
    """Control reproduction on real data (no candidate arm).

    The study's control is production **K**, so the label "K" (K's execution layer, K's target
    series) is asserted against the numbers `research/band_h2_combo_v1` published for K: pre-tax
    CAGR 16.0173 / 15.7649 % at m = 1/2 base and 15.4303 / 15.1785 % calibrated with max DD
    46.353 / 46.670 % and turnover 5.4561, and after tax at $100k 10.553 / 15.235 / 23.972 %
    (liquidated) with max DD 46.670 / 44.627 / 34.977 %. The reference row "R2" is asserted
    against the R2 anchors of `fast_reentry_v1` / `vt_level_tax_v1` (15.7538 / 15.4766 %, DD
    46.414 / 46.758 %, after tax 10.160 / 14.706 / 22.886 %), and the pure-E2 anchor series
    against `e2_integration_v1` under its own label. Two identity checks carry the mechanics: the
    R2 arm's NAV path equals the reference `simulate_band` (band off, no deferral), and K's differs
    from both R2's and the pure-E2 series' while its band/deferral counters fire."""
    checks, bad = {}, 0
    days, o, c, w_e2, W, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1

    def cmp(name, got, ref, tol):
        nonlocal bad
        checks[name] = {"reference": float(ref), "reproduced": float(got), "diff": float(got - ref)}
        if abs(got - ref) > tol:
            bad += 1

    # ---- pre-tax anchors (nav-based CAGR, max DD, turnover), base and calibrated at m = 1/2
    for md, spread in FIN.items():
        d_, o_, c_, we_, W_, e_ = main_inputs(spread)
        for m in (1, 2):
            for label, ref in (("K", REF_K), ("R2", REF_R2), ("E2", REF_E2)):
                r = engine_run(1.0, START, len(d_) - 1, d_, mkt, o_, c_,
                               (we_ if label == "E2" else W_[label]), e_, label, "pre_tax", m=m)
                for key, val in (("cagr_usd_unliquidated", ref[(md, m)][0]),
                                 ("max_dd_nav_usd", ref[(md, m)][1]),
                                 ("turnover_annual", ref[(md, m)][2])):
                    cmp(f"{label}_{md}_m{m}_{key}", r[key], val, TOL)
    # ---- after-tax anchors at $100k (liquidated), K / R2 / E2
    tax = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        tax[wn] = {}
        for label, series in (("K", "K"), ("R2", "R2"), ("E2", "E2")):
            rp = engine_run(100_000.0, a, b, days, mkt, o, c,
                            (w_e2 if label == "E2" else W[label]), e2, label, "pre_tax")
            rq = engine_run(100_000.0, a, b, days, mkt, o, c,
                            (w_e2 if label == "E2" else W[label]), e2, label, "progressive")
            ref = TAX_ANCHORS[series][wn]
            tax[wn][label] = {"pre_tax": {"reference": ref[0], "reproduced": rp["cagr_usd_liquidated"],
                                          "diff_pp": (rp["cagr_usd_liquidated"] - ref[0]) * 100},
                              "progressive": {"reference": ref[1], "reproduced": rq["cagr_usd_liquidated"],
                                              "diff_pp": (rq["cagr_usd_liquidated"] - ref[1]) * 100},
                              "progressive_max_dd": {"reference": ref[2], "reproduced": rq["max_dd_nav_usd"],
                                                     "diff_pp": (rq["max_dd_nav_usd"] - ref[2]) * 100},
                              "tax_usd": rq["tax_total_usd_at_payment_fx"],
                              "sells_per_year": rq["sells"] / rq["years"]}
            for v in tax[wn][label].values():
                if isinstance(v, dict) and abs(v["diff_pp"]) > 0.005:
                    bad += 1
    # ---- identity checks
    ref_nav, _, _ = simulate_band(o[None, :], c[None, :], W["R2"][None, :], COST_PER_SIDE * 2,
                                  START, C0_BAND)
    r2_run = engine_run(1.0, START, T, days, mkt, o, c, W["R2"], e2, "R2", "pre_tax")
    k_run = engine_run(1.0, START, T, days, mkt, o, c, W["K"], e2, "K", "pre_tax")
    e2_run = engine_run(1.0, START, T, days, mkt, o, c, w_e2, e2, "E2", "pre_tax")
    checks["r2_engine_equals_simulate_band"] = {
        "max_abs_nav_diff": float(np.max(np.abs(r2_run["_nav"] - ref_nav[0])))}
    if checks["r2_engine_equals_simulate_band"]["max_abs_nav_diff"] > 1e-9:
        bad += 1
    checks["K_nav_differs_from_R2"] = {"max_abs_nav_diff": float(np.max(np.abs(k_run["_nav"] - r2_run["_nav"])))}
    checks["K_nav_differs_from_pure_E2"] = {"max_abs_nav_diff": float(np.max(np.abs(k_run["_nav"] - e2_run["_nav"])))}
    if not checks["K_nav_differs_from_R2"]["max_abs_nav_diff"] > 0.01:
        bad += 1
    checks["K_band_and_deferral_fire"] = {"blocked_band": int(k_run["blocked_band"]),
                                          "deferred": int(k_run["deferred"]),
                                          "postponements": int(k_run["postponements"])}
    if not (k_run["blocked_band"] > 0 and k_run["deferred"] > 0
            and r2_run["postponements"] == 0):
        bad += 1
    # ---- the candidates must actually differ from K on the target series
    diff = {}
    for arm in CANDIDATES:
        diff[arm] = {"vt": VT[arm],
                     "max_abs_diff_vs_K": float(np.max(np.abs(W[arm] - W["K"]))),
                     "sessions_differs": int(np.sum(W[arm] != W["K"])),
                     "mean_weight": float(np.mean(W[arm])), "mean_weight_K": float(np.mean(W["K"]))}
        if not (diff[arm]["sessions_differs"] > 0 and diff[arm]["max_abs_diff_vs_K"] > 1e-9):
            bad += 1
    checks["candidate_targets_differ_from_K"] = diff
    return {"reproduction_checks": checks, "n_outside_tolerance": bad,
            "after_tax_anchors": tax, "pass": bool(bad == 0)}


def boot_anchor_checks(boot_ref, checks=None, bad=0):
    """K's, R2's and the pure-E2 series' clean-bootstrap medians against the published anchors
    (K's from band_h2_combo_v1, R2's/E2's from tax_harvest_v2 / vt_level_tax_v1). Cheap: reads the
    deep run's own bootstrap instead of recomputing one."""
    out, nbad = {}, int(bad)
    for mode in ("progressive", "flat"):
        for label, key in ((CONTROL, "K"), (BASELINE, "R2"), ("E2", "E2")):
            got = (boot_ref["anchor_e2_100k"][mode]["cagr_median"] if label == "E2"
                   else boot_ref["tax"]["100k"][mode][label]["cagr_median"])
            ref = BOOT_ANCHOR[mode][key]
            out[f"{mode}_{label}"] = {"reference": ref, "reproduced": float(got),
                                      "diff_pp": (float(got) - ref) * 100}
            if abs(float(got) - ref) > 0.0010:
                nbad += 1
    for md in FIN:
        got = boot_ref["pre"][md][CONTROL]["cagr_median"]
        ref = BOOT_PRE_ANCHOR[md]
        out[f"pre_{md}_K"] = {"reference": ref, "reproduced": float(got), "diff_pp": (float(got) - ref) * 100}
        if abs(float(got) - ref) > 0.0010:
            nbad += 1
    return out, nbad
# ----------------------------------------------------------------------------- the run
def pre_tax_main(arms):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD main-window row."""
    days, o, c, w_e2, W, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    usd_mkt = usd_market(days)
    pre, pre_usd = {}, {}
    for md, spread in FIN.items():
        d_, o_, c_, we_, W_, e_ = main_inputs(spread)
        pre[md], pre_usd[md] = {}, {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in arms:
                r = engine_run(PRE_CAPITAL, START, T, d_, mkt, o_, c_, W_[arm], e_, arm, "pre_tax", m=m)
                pre[md][f"m{m}"][arm] = pre_stats(d_, START, T, r)
        for arm in arms:
            r = engine_run(PRE_CAPITAL, START, T, d_, usd_mkt, o_, c_, W_[arm], e_, arm, "pre_tax", m=M,
                           rule_index=False)
            pre_usd[md][arm] = pre_stats(d_, START, T, r, real_days=d_)
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": T - START + 1,
                     "years": float(trv2.calendar_years(days[START - 1], days[-1]))}
    return pre, pre_usd


def after_tax_windows(capitals, arms):
    """After tax at the given capitals (progressive 2026 tariff, liquidated, base financing,
    m = 2) on the three TAX_WINDOWS. Each arm runs its own target series."""
    days, o, c, w_e2, W, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    win = {}
    for cap_label in capitals:
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            win[cap_label][wn] = {}
            for arm in arms:
                rq = engine_run(TAX_CAPITALS[cap_label], a, b, days, mkt, o, c, W[arm], e2, arm, "progressive")
                # the pre-tax leg of the after-tax windows runs at capital 1.0 (band_h2's
                # convention, kept identical so the reference check reproduces its cells)
                rp = engine_run(1.0, a, b, days, mkt, o, c, W[arm], e2, arm, "pre_tax")
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
                }
    return win


def cost_stress(arms):
    """m = 4 cost stress, after tax at $100k on the three windows."""
    days, o, c, w_e2, W, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    cost = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        cost[wn] = {}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, W[arm], e2, arm,
                            "progressive", m=M_STRESS)
            # the pre-tax leg of the cost stress runs at capital 1.0 (band_h2's convention)
            rp = engine_run(1.0, a, b, days, mkt, o, c, W[arm], e2, arm, "pre_tax", m=M_STRESS)
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
        out["pooled"][a] = {"diff_vs_C0_median_pp": float(np.median(d) * 100),
                            "diff_vs_C0_mean_pp": float(np.mean(d) * 100),
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
        _w_e2, W = targets_of(ic, e2, trv2.vol_n(sc)[0], arms, early_components(ic))
        mkt = te.load_market(FX_CSV, UFE_CSV, idays)
        rec = {"window": {"start": idays[start], "end": idays[-1], "sessions": len(idays) - start}}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], start, len(idays) - 1, idays, mkt, so, sc,
                            W[arm], e2, arm, "progressive")
            rp = engine_run(PRE_CAPITAL, start, len(idays) - 1, idays, mkt, so, sc, W[arm], e2, arm, "pre_tax")
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
    days, o, c, w_e2, W, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    out = {}
    for yr in E2_START_YEARS:
        i0 = bisect.bisect_left(days, f"{yr}-01-01")
        rec = {"start": days[i0], "index": int(i0)}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], i0, T, days, mkt, o, c, W[arm], e2, arm, "progressive")
            rp = engine_run(PRE_CAPITAL, i0, T, days, mkt, o, c, W[arm], e2, arm, "pre_tax")
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
    for block in E3_BLOCKS:
        for mode in ("progressive", "flat"):
            b = boot(seed=E3_SEED, block=block, arms=arms, n_pre=0, n_tax=E3_PATHS,
                     capitals=(GATE_CAPITAL,), modes=(mode,), anchor_e2=False,
                     deltas=CANDIDATES + (CONTROL,), baselines=(CONTROL, BASELINE),
                     label=f"E3 block {block} {mode}")
            out[f"block{block}_{mode}"] = b["tax"][GATE_CAPITAL][mode]
    return out


def stage_e5_decisions():
    """E5 (report only): how often the arms' fill decisions differ on the main path
    (base financing, m = 2, $100k, R-INDEX, pre-tax so no tax-funding sales interfere).
    Decision code: 0 = no trade, 1 = up/entry/exit trade, 2 = downward resize."""
    days, o, c, w_e2, W, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    traces = {}
    for arm in ARMS:
        traces[arm] = engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, W[arm], e2, arm, "pre_tax",
                              trace=True)
    out = {"window": {"start": days[START], "end": days[-1], "sessions": int(len(traces[CONTROL]["_dec"]))},
           "sells": {}, "postponements": {}}
    for arm in ARMS:
        r = traces[arm]
        out["sells"][arm] = int(np.sum(r["_dec"] == 2))
        out["postponements"][arm] = {"band": int(r["blocked_band"]), "deferred": int(r["deferred"])}
    for a, b in (("F1", CONTROL), ("F2", CONTROL), ("F3", CONTROL), ("R2", CONTROL),
                 ("F2", "F1"), ("F3", "F2")):
        da, db = traces[a]["_dec"], traces[b]["_dec"]
        out[f"{a}_vs_{b}"] = {
            "sessions_decision_differs": int(np.sum(da != db)),
            "share_decision_differs": float(np.mean(da != db)),
            f"{a}_sells_{b}_holds": int(np.sum((da == 2) & (db != 2))),
            f"{a}_holds_{b}_sells": int(np.sum((da != 2) & (db == 2)))}
    return out


# ----------------------------------------------------------------------------- reproduction checks
def _flatten_numeric(d, prefix=""):
    """Every numeric leaf of a nested dict, path-labelled (bools and strings skipped)."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flatten_numeric(v, f"{prefix}{k}."))
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        else:
            out[f"{prefix}{k}"] = float(v)
    return out


def _compare_subtree(label, mine, theirs, cells, only_mine, only_theirs):
    """Compare every numeric leaf present in both subtrees; record one-sided keys."""
    fm, ft = _flatten_numeric(mine), _flatten_numeric(theirs)
    for k in sorted(set(fm) | set(ft)):
        if k not in fm:
            only_theirs.append(f"{label}.{k}")
        elif k not in ft:
            only_mine.append(f"{label}.{k}")
        else:
            cells.append((f"{label}.{k}", fm[k], ft[k]))


def reference_checks(res, full=True):
    """Reproduce production K's published cells with this study's code.

    The control of this study *is* band_h2_combo_v1's arm "K", so every numeric leaf of every K
    cell that campaign published -- pre-tax main window (m = 1/2/4, base/calibrated), the R-USD
    row, the after-tax windows at all five capitals, the m = 4 cost stress, the seed-20261011
    clean bootstrap (pre-tax and after-tax, $100k/$1M), the six OOS windows, the 21 R-USD
    instruments, E1 (after-tax 21 ETFs, R-INDEX) and E2 (rolling starts) -- is compared against
    `research/band_h2_combo_v1/RESULTS.json`. R2's cells (that campaign's control "C0") are
    compared the same way, mapped label-for-label: this study's "R2" is its "C0".

    Two name maps are needed, and both are stated rather than hidden:
      * the *control*: this study's CONTROL is "K" (that campaign's candidate label), so its
        `*_vs_C0` bootstrap cells (deltas versus R2) are compared against this run's `*_vs_R2`;
      * the cleaner bootstrap stores a few extra max-drawdown leaves here (the tail gate); they
        are reported as keys only in this run, never as mismatches.

    `full=False` compares only the cheap cells (pre-tax main, after-tax windows at the gate
    capital, cost stress, bootstrap) -- the deep part of `--harness`; `full=True` adds the
    E1/E2/E3/OOS/instrument stages, which exist only after `run()` has produced them."""
    ref = json.loads(KREF.read_text())
    cells, only_mine, only_theirs, sections = [], [], [], []
    br = res["boot_ref"]
    bref = ref["bootstrap_clean_seed20261011"]
    arm_map = {CONTROL: "K", BASELINE: "C0"}   # this run's label -> band_h2_combo_v1's label

    def add_(box, label, mine, theirs):
        box.append((label, float(mine), float(theirs)))

    def both(label, mine, theirs):
        for a, b in arm_map.items():
            if a in mine and b in theirs:
                _compare_subtree(f"{label}.{a}", mine[a], theirs[b], cells, only_mine, only_theirs)

    sections.append("pre_tax_main")
    for md in FIN:
        for m in ("m1", "m2", "m4"):
            both(f"pre_tax_main.{md}.{m}", res["pre"][md][m], ref["pre_tax_main"][md][m])
        both(f"pre_usd.{md}", res["pre_usd"][md], ref["pre_tax_main_r_usd"][md])
    sections.append("after_tax_windows")
    for cl in TAX_CAPITALS:
        if cl not in res["windows"]:
            continue   # full=False (the deep harness) runs the gate capital only
        for wn in TAX_WINDOWS:
            both(f"after_tax.{cl}.{wn}", res["windows"][cl][wn], ref["after_tax_windows"][cl][wn])
    sections.append("cost_stress_m4")
    for wn in TAX_WINDOWS:
        both(f"cost.{wn}", res["cost"][wn], ref["cost_stress_m4"][wn])
    sections.append("clean_bootstrap")
    for cl in ("100k", "1M"):
        for mode in ("progressive", "flat"):
            m_, t_ = br["tax"][cl][mode], bref["tax"][cl][mode]
            for a, b in arm_map.items():
                _compare_subtree(f"boot.tax.{cl}.{mode}.{a}", m_[a], t_[b], cells, only_mine, only_theirs)
            # this run's CONTROL is "K", that campaign's is "R2" (= this run's BASELINE), so that
            # campaign's `K_vs_C0` cell is this run's `K_vs_R2`.
            _compare_subtree(f"boot.tax.{cl}.{mode}.K_vs_R2", m_["K_vs_R2"], t_["K_vs_C0"],
                             cells, only_mine, only_theirs)
    for md in FIN:
        m_, t_ = br["pre"][md], bref["pre"][md]
        for a, b in arm_map.items():
            add_(cells, f"boot.pre.{md}.{a}.cagr_median", m_[a]["cagr_median"], t_[b]["cagr_median"])
    for mode in ("progressive", "flat"):
        add_(cells, f"boot.anchor_e2.{mode}.cagr_median", br["anchor_e2_100k"][mode]["cagr_median"],
             bref["anchor_e2_100k"][mode]["cagr_median"])
    if full:
        sections.append("e1_e2_e3_oos_instruments")
        for key in INSTRUMENTS:
            both(f"e1.{key}", res["e1"][key], ref["E1_after_tax_etfs_rindex"][key])
        for yr in E2_START_YEARS:
            both(f"e2.{yr}", res["e2"][str(yr)], ref["E2_rolling_starts"][str(yr)])
        for key in sorted(res["e3"]):
            _compare_subtree(f"e3.{key}.K_vs_R2", res["e3"][key]["K_vs_R2"],
                             ref["E3_fresh_bootstraps"][key]["K_vs_C0"],
                             cells, only_mine, only_theirs)
        for wn in OOS_WINDOWS:
            both(f"oos.{wn}", res["oos"][wn], ref["oos"][wn])
        for key in res["instruments"]:
            both(f"instruments.{key}", res["instruments"][key], ref["instruments"][key])

    bad = [(l, m, t, abs(m - t)) for l, m, t in cells if abs(m - t) > 2e-6]
    return {"n_cells": len(cells), "n_mismatch": len(bad), "tolerance": 2e-6,
            "full": bool(full), "sections": sections,
            "reference_file": "research/band_h2_combo_v1/RESULTS.json (arm K, and C0 for the R2 row)",
            "max_abs_diff": float(max((abs(m - t) for _, m, t in cells), default=0.0)),
            "n_keys_only_in_mine": len(only_mine), "n_keys_only_in_reference": len(only_theirs),
            "keys_only_in_mine": only_mine[:20], "keys_only_in_reference": only_theirs[:20],
            "mismatches": [{"cell": l, "mine": m, "reference": t, "diff": d} for l, m, t, d in bad][:40],
            "pass": bool(not bad)}
# ----------------------------------------------------------------------------- gates (this study)
def _cagr(arm_stats):
    return arm_stats["cagr"]


def gates_exposure(res):
    """Every pre-registered gate of this study, versus K (the control), for each candidate F*.

    G1-G9 are ewma_band_v1's gates with C0 -> K and its G9 floor (turnover <= 2 x K, the common
    protocol's). The tail gate is leverage_frontier_v1's G3 (P(DD > 70 %) <= K's + 5 pp) computed
    on the same 10-year block-20 bootstrap, pre-tax base and after-tax at $100k/$1M. The
    after-tax and clean-bootstrap cells are the common protocol's; the survey's selection rule
    uses only G1-G9 plus the max-DD budget. The decision rule is pre-registered in section 5."""
    pre, boot_ref = res["pre"], res["boot_ref"]
    win = res["windows"][GATE_CAPITAL]
    oos, instruments, e1, e2 = res["oos"], res["instruments"], res["e1"], res["e2"]
    oos_boot, boot_tail = res["oos_boot"], res["boot_ref"]
    g = {}
    for arm in CANDIDATES:
        r = {}
        base, cal = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        kbase, kcal = pre["base"]["m2"][CONTROL], pre["calibrated"]["m2"][CONTROL]
        # ---- G1 / G6: return
        def g1_of(a, ctrl, mm):
            cagr_ok = a["cagr"] >= ctrl["cagr"] + G1_MIN_CAGR_PP / 100
            calmar_ok = (a["calmar"] >= G1_CALMAR_MULT * ctrl["calmar"]
                         and a["cagr"] >= ctrl["cagr"] + G1_RELAX_PP / 100)
            return bool(cagr_ok or calmar_ok), {"cagr": a["cagr"], "cagr_k": ctrl["cagr"],
                                                "delta_pp": (a["cagr"] - ctrl["cagr"]) * 100,
                                                "calmar": a["calmar"], "calmar_k": ctrl["calmar"],
                                                "calmar_relax": G1_CALMAR_MULT,
                                                "pass_by_cagr": bool(cagr_ok), "m": mm}
        b1, d1 = g1_of(base, kbase, 2)
        c1, dc1 = g1_of(cal, kcal, 2)
        r["G1"] = bool(b1 and c1)
        r["G1_detail"] = {"base": d1, "calibrated": dc1}
        b6, d6 = g1_of(pre["base"]["m1"][arm], pre["base"]["m1"][CONTROL], 1)
        c6, dc6 = g1_of(pre["calibrated"]["m1"][arm], pre["calibrated"]["m1"][CONTROL], 1)
        r["G6"] = bool(b6 and c6)
        r["G6_detail"] = {"base": d6, "calibrated": dc6}
        # ---- G2: drawdown <= K + 1.0 pp (the survey's budget, both financings)
        dd = {"base": (base["max_dd"] - kbase["max_dd"]) * 100,
              "calibrated": (cal["max_dd"] - kcal["max_dd"]) * 100}
        r["G2_detail"] = {"max_dd": {"base": base["max_dd"], "calibrated": cal["max_dd"]},
                          "max_dd_k": {"base": kbase["max_dd"], "calibrated": kcal["max_dd"]},
                          "delta_pp": dd, "allowed_pp": G2_MAX_DD_PP,
                          "survey_budget_pp": SELECT_DD_BUDGET_PP}
        r["G2"] = bool(dd["base"] <= G2_MAX_DD_PP + 1e-9 and dd["calibrated"] <= G2_MAX_DD_PP + 1e-9)
        # ---- G3: 2017+ CAGR >= K - 1.0 pp (both)
        g3 = {"base": (base["2017_end"]["cagr"] - kbase["2017_end"]["cagr"]) * 100,
              "calibrated": (cal["2017_end"]["cagr"] - kcal["2017_end"]["cagr"]) * 100}
        r["G3_detail"] = {"delta_pp": g3, "min_pp": G3_MIN_CAGR_PP,
                          "cagr": {"base": base["2017_end"]["cagr"], "calibrated": cal["2017_end"]["cagr"]},
                          "cagr_k": {"base": kbase["2017_end"]["cagr"], "calibrated": kcal["2017_end"]["cagr"]}}
        r["G3"] = bool(all(v >= G3_MIN_CAGR_PP - 1e-9 for v in g3.values()))
        # ---- G4: above K in >= 2 of the 3 sub-periods (base)
        sub = {p: (base["subperiods"][p]["cagr"] - kbase["subperiods"][p]["cagr"]) * 100
               for p in ("P1", "P2", "P3")}
        r["G4_detail"] = {"delta_pp": sub, "n_above": int(sum(1 for v in sub.values() if v > 0)),
                          "required": G4_MIN_SUBPERIODS}
        r["G4"] = bool(sum(1 for v in sub.values() if v > 0) >= G4_MIN_SUBPERIODS)
        # ---- G5: pre-tax block-20 bootstrap median (arm - K) >= 0 (base and calibrated)
        g5 = {md: boot_ref["pre"][md][arm]["diff_vs_C0_median_pp"] for md in FIN}
        g5s = {md: boot_ref["pre"][md][arm]["share_gt_0"] for md in FIN}
        r["G5_detail"] = {"median_delta_pp": g5, "share_gt_0": g5s, "required_pp": G5_MIN_MEDIAN_PP,
                          "median_cagr": {md: boot_ref["pre"][md][arm]["cagr_median"] for md in FIN},
                          "median_cagr_k": {md: boot_ref["pre"][md][CONTROL]["cagr_median"] for md in FIN}}
        r["G5"] = bool(all(v >= G5_MIN_MEDIAN_PP - 1e-9 for v in g5.values()))
        # ---- G7: the six OOS windows
        dc = {wn: oos[wn][arm]["delta_cagr_pp"] for wn in OOS_WINDOWS}
        ddw = {wn: oos[wn][arm]["delta_max_dd_pp"] for wn in OOS_WINDOWS}
        within = sum(1 for wn in OOS_WINDOWS
                     if dc[wn] >= G7_MIN_DCAGR_PP and ddw[wn] <= G7_MAX_DDD_PP)
        r["G7_detail"] = {"delta_cagr_pp": dc, "delta_max_dd_pp": ddw,
                          "median_delta_cagr_pp": float(np.median(list(dc.values()))),
                          "n_within_limits": within, "required_within": int(round(G7_MIN_SHARE * 6)),
                          "max_delta_max_dd_pp": float(max(ddw.values())),
                          "cagr_k": {wn: oos[wn][CONTROL]["cagr"] for wn in OOS_WINDOWS}}
        r["G7"] = bool(np.median(list(dc.values())) >= 0.0
                       and within >= int(round(G7_MIN_SHARE * 6))
                       and max(ddw.values()) <= G7_MAX_DDD_ANY_PP)
        # ---- G8: pooled OOS bootstrap median (arm - K) >= 0
        r["G8_detail"] = dict(oos_boot["pooled"][arm])
        r["G8"] = bool(oos_boot["pooled"][arm]["diff_vs_C0_median_pp"] >= G8_MIN_MEDIAN_PP - 1e-9)
        # ---- G9: turnover <= 2 x K
        turn = base["turnover_annual"] / kbase["turnover_annual"]
        r["G9_detail"] = {"turnover": base["turnover_annual"], "turnover_k": kbase["turnover_annual"],
                          "ratio": turn, "max_ratio": G9_MAX_TURNOVER_MULT,
                          "sells_per_year": base["sells_per_year"],
                          "sells_per_year_k": kbase["sells_per_year"]}
        r["G9"] = bool(turn <= G9_MAX_TURNOVER_MULT + 1e-9)
        r["G1_G9_detail"] = {k: r[k] for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9")}
        r["G1_G9"] = bool(all(r[k] for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9")))
        # ---- after tax at $100k: delta in all three windows (common protocol; survey needs the
        #      level for ranking only)
        at = {wn: (win[wn][arm]["after_tax_cagr"] - win[wn][CONTROL]["after_tax_cagr"]) * 100
              for wn in TAX_WINDOWS}
        r["AT_detail"] = {"delta_pp": at, "after_tax_cagr": {wn: win[wn][arm]["after_tax_cagr"] for wn in TAX_WINDOWS},
                          "after_tax_cagr_k": {wn: win[wn][CONTROL]["after_tax_cagr"] for wn in TAX_WINDOWS},
                          "tax_usd": {wn: win[wn][arm]["tax_usd"] for wn in TAX_WINDOWS},
                          "tax_usd_k": {wn: win[wn][CONTROL]["tax_usd"] for wn in TAX_WINDOWS},
                          "max_dd_after_tax": {wn: win[wn][arm]["max_dd_after_tax"] for wn in TAX_WINDOWS},
                          "sells_per_year": {wn: win[wn][arm]["sells_per_year"] for wn in TAX_WINDOWS},
                          "sells_per_year_k": {wn: win[wn][CONTROL]["sells_per_year"] for wn in TAX_WINDOWS},
                          "required_pp": AT_MIN_DELTA_PP}
        r["AT_all_windows"] = bool(all(v >= AT_MIN_DELTA_PP - 1e-9 for v in at.values()))
        r["AT_min_delta_pp"] = float(min(at.values()))
        # ---- clean bootstrap B1-recent / B2 at $100k (protocol: median >= 0, share >= 55 %)
        cells = {}
        for mode in ("progressive", "flat"):
            c = boot_ref["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{CONTROL}"]
            cells["B1_recent" if mode == "progressive" else "B2_flat"] = {
                "median_delta_pp": c["median_delta_cagr_pp"], "mean_delta_pp": c["mean_delta_cagr_pp"],
                "share_gt_0": c["share_gt_0"], "p05_pp": c["p05_delta_cagr_pp"],
                "p95_pp": c["p95_delta_cagr_pp"],
                "median_delta_max_dd_pp": c.get("median_delta_max_dd_pp"),
                "protocol_pass": bool(c["median_delta_cagr_pp"] >= BOOT_MIN_MEDIAN_PP - 1e-9
                                      and c["share_gt_0"] >= BOOT_PROTOCOL_SHARE),
                "survey_pass": bool(c["median_delta_cagr_pp"] >= BOOT_MIN_MEDIAN_PP - 1e-9
                                    and c["share_gt_0"] >= BOOT_SURVEY_SHARE)}
        r["BOOT_detail"] = {"cells": cells, "protocol_median_pp": BOOT_MIN_MEDIAN_PP,
                            "protocol_share": BOOT_PROTOCOL_SHARE, "survey_share": BOOT_SURVEY_SHARE}
        r["BOOT_protocol"] = bool(all(v["protocol_pass"] for v in cells.values()))
        r["BOOT_survey"] = bool(all(v["survey_pass"] for v in cells.values()))
        # ---- tail gate: P(DD > 70 %) <= K's + 5 pp, and the 65 % level leg
        tail = {}
        for md in FIN:
            a_, k_ = boot_tail["pre"][md][arm], boot_tail["pre"][md][CONTROL]
            tail[f"pre_{md}"] = {"p_dd_gt_70": a_["p_dd_gt_70"], "p_dd_gt_70_k": k_["p_dd_gt_70"],
                                 "delta_pp": (a_["p_dd_gt_70"] - k_["p_dd_gt_70"]) * 100,
                                 "median_max_dd": a_["median_max_dd"], "median_max_dd_k": k_["median_max_dd"]}
        for cl in ("100k", "1M"):
            for mode in ("progressive", "flat"):
                a_ = boot_tail["tax"][cl][mode][arm]
                k_ = boot_tail["tax"][cl][mode][CONTROL]
                tail[f"at_{cl}_{mode}"] = {"p_dd_gt_70": a_["p_dd_gt_70"], "p_dd_gt_70_k": k_["p_dd_gt_70"],
                                           "delta_pp": (a_["p_dd_gt_70"] - k_["p_dd_gt_70"]) * 100,
                                           "median_max_dd": a_["median_max_dd"],
                                           "median_max_dd_k": k_["median_max_dd"]}
        r["TAIL_detail"] = {"cells": tail, "threshold": TAIL_MAX_DD, "allowed_extra_pp": TAIL_MAX_EXTRA_PP,
                            "max_dd_level": TAIL_MAX_DD_LEVEL,
                            "paths_pre": boot_tail["n_paths_pre"], "paths_tax": boot_tail["n_paths_tax"]}
        r["TAIL_pre_base"] = bool(tail["pre_base"]["p_dd_gt_70"] <= tail["pre_base"]["p_dd_gt_70_k"]
                                  + TAIL_MAX_EXTRA_PP / 100 + 1e-9)
        r["TAIL_pre_calibrated"] = bool(tail["pre_calibrated"]["p_dd_gt_70"]
                                        <= tail["pre_calibrated"]["p_dd_gt_70_k"] + TAIL_MAX_EXTRA_PP / 100 + 1e-9)
        r["TAIL_at_100k"] = bool(tail["at_100k_progressive"]["p_dd_gt_70"]
                                 <= tail["at_100k_progressive"]["p_dd_gt_70_k"] + TAIL_MAX_EXTRA_PP / 100 + 1e-9)
        r["TAIL_at_1M"] = bool(tail["at_1M_progressive"]["p_dd_gt_70"]
                               <= tail["at_1M_progressive"]["p_dd_gt_70_k"] + TAIL_MAX_EXTRA_PP / 100 + 1e-9)
        r["TAIL_level_65"] = bool(max(v["median_max_dd"] for v in tail.values()) <= TAIL_MAX_DD_LEVEL)
        # ---- the survey's selection legs
        r["survey_dd_ok"] = bool(dd["base"] <= SELECT_DD_BUDGET_PP + 1e-9
                                 and dd["calibrated"] <= SELECT_DD_BUDGET_PP + 1e-9)
        r["survey_eligible"] = bool(r["G1_G9"] and r["survey_dd_ok"])
        r["protocol_eligible"] = bool(r["G1_G9"] and r["AT_all_windows"] and r["BOOT_protocol"])
        # ---- reporting blocks (parent campaign's stages, vs K)
        r["vs_K"] = {
            "pre_tax_cagr_base_m2": base["cagr"], "pre_tax_cagr_cal_m2": cal["cagr"],
            "pre_tax_cagr_delta_pp_base": (base["cagr"] - kbase["cagr"]) * 100,
            "pre_tax_cagr_delta_pp_cal": (cal["cagr"] - kcal["cagr"]) * 100,
            "pre_tax_max_dd_base": base["max_dd"], "pre_tax_max_dd_cal": cal["max_dd"],
            "pre_tax_max_dd_delta_pp_base": dd["base"], "pre_tax_max_dd_delta_pp_cal": dd["calibrated"],
            "pre_tax_m1_cagr_base": pre["base"]["m1"][arm]["cagr"],
            "pre_tax_m1_cagr_cal": pre["calibrated"]["m1"][arm]["cagr"],
            "turnover_annual": base["turnover_annual"],
            "turnover_ratio_vs_K": turn,
            "sells_per_year": base["sells_per_year"],
            "after_tax_100k": {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                                    "delta_pp": at[wn]} for wn in TAX_WINDOWS},
            "oos_median_delta_pp": r["G7_detail"]["median_delta_cagr_pp"],
            "boot_median_delta_pp": {k: v["median_delta_pp"] for k, v in cells.items()},
            "boot_share_gt_0": {k: v["share_gt_0"] for k, v in cells.items()},
        }
        ds = [e1[k][arm]["delta_after_tax_pp"] for k in INSTRUMENTS]
        n_pos = int(sum(1 for d in ds if d > 0))
        r["E1_detail"] = {"n_instruments": len(ds), "median_delta_pp": float(np.median(ds)),
                          "share_gt_0": float(n_pos / len(ds)), "n_gt_0": n_pos,
                          "min_delta_pp": float(min(ds)), "max_delta_pp": float(max(ds)),
                          "median_pre_tax_delta_pp": float(np.median(
                              [e1[k][arm]["delta_pre_tax_pp"] for k in INSTRUMENTS])),
                          "required_share": E1_MIN_SHARE}
        r["E1"] = bool(np.median(ds) > 0 and n_pos / len(ds) >= E1_MIN_SHARE)
        ds2 = [e2[str(y)][arm]["delta_after_tax_pp"] for y in E2_START_YEARS]
        n2 = int(sum(1 for d in ds2 if d > 0))
        r["E2_detail"] = {"n_starts": len(ds2), "median_delta_pp": float(np.median(ds2)),
                          "share_gt_0": float(n2 / len(ds2)), "n_gt_0": n2,
                          "min_delta_pp": float(min(ds2)), "max_delta_pp": float(max(ds2)),
                          "deltas_pp": {str(y): e2[str(y)][arm]["delta_after_tax_pp"] for y in E2_START_YEARS},
                          "required_share": E2_MIN_SHARE}
        r["E2"] = bool(n2 / len(ds2) >= E2_MIN_SHARE and np.median(ds2) > 0)
        r["instruments_detail"] = {"median_delta_cagr_pp": float(np.median(
            [instruments[k][arm]["delta_cagr_pp"] for k in INSTRUMENTS])),
            "median_delta_max_dd_pp": float(np.median(
                [instruments[k][arm]["delta_max_dd_pp"] for k in INSTRUMENTS])),
            "worst_delta_max_dd_pp": float(max(instruments[k][arm]["delta_max_dd_pp"] for k in INSTRUMENTS))}
        g[arm] = r

    # ---- selection (pre-registered)
    ranked = sorted(CANDIDATES, key=lambda a: -g[a]["vs_K"]["after_tax_100k"]["2000_03_to_2026_09"]["after_tax_cagr"])
    survey_sel = next((a for a in ranked if g[a]["survey_eligible"]), None)
    protocol_sel = next((a for a in ranked if g[a]["protocol_eligible"]), None)
    frontier = [{"arm": a,
                 "vt": VT[a],
                 "after_tax_cagr_100k_2000": g[a]["vs_K"]["after_tax_100k"]["2000_03_to_2026_09"]["after_tax_cagr"],
                 "after_tax_cagr_100k_2010": g[a]["vs_K"]["after_tax_100k"]["2010_01_to_end"]["after_tax_cagr"],
                 "after_tax_cagr_100k_2017": g[a]["vs_K"]["after_tax_100k"]["2017_01_to_end"]["after_tax_cagr"],
                 "pre_tax_max_dd_base": g[a]["vs_K"]["pre_tax_max_dd_base"],
                 "pre_tax_max_dd_cal": g[a]["vs_K"]["pre_tax_max_dd_cal"],
                 "pre_tax_cagr_base": g[a]["vs_K"]["pre_tax_cagr_base_m2"],
                 "p_dd_gt_70_pre_base": g[a]["TAIL_detail"]["cells"]["pre_base"]["p_dd_gt_70"],
                 "p_dd_gt_70_at_100k": g[a]["TAIL_detail"]["cells"]["at_100k_progressive"]["p_dd_gt_70"],
                 "survey_eligible": g[a]["survey_eligible"],
                 "protocol_eligible": g[a]["protocol_eligible"]
                 } for a in ARMS if a != CONTROL]
    for a in g:
        g[a]["survey_run"] = bool(g[a]["survey_eligible"])
        g[a]["protocol_run"] = bool(g[a]["protocol_eligible"])
    return {"arms": g, "control": CONTROL,
            "frontier": frontier,
            "ranking_by_after_tax_100k_2000": ranked,
            "survey_rule": {
                "selected": survey_sel,
                "rule": ("highest after-tax CAGR at $100k on 2000-03 -> 2026-09 among F1-F3 whose "
                         f"pre-tax max DD <= K + {SELECT_DD_BUDGET_PP} pp (base and calibrated, m = 2) "
                         "and whose G1-G9 all pass; none -> close the exposure axis with the frontier table"),
                "detail": {a: {"survey_eligible": g[a]["survey_eligible"],
                               "G1_G9": g[a]["G1_G9"], "G1_G9_detail": g[a]["G1_G9_detail"],
                               "survey_dd_ok": g[a]["survey_dd_ok"],
                               "after_tax_cagr_100k_2000": g[a]["vs_K"]["after_tax_100k"]["2000_03_to_2026_09"]["after_tax_cagr"]}
                           for a in CANDIDATES}},
            "protocol_rule": {
                "selected": protocol_sel,
                "rule": ("highest after-tax CAGR at $100k on 2000-03 -> 2026-09 among F1-F3 that pass "
                         "G1-G9, beat K after tax at $100k in all three windows, and have B1-recent and "
                         "B2-flat median Delta >= 0 with share > 0 >= 55 %"),
                "detail": {a: {"protocol_eligible": g[a]["protocol_eligible"],
                               "AT_all_windows": g[a]["AT_all_windows"], "AT_min_delta_pp": g[a]["AT_min_delta_pp"],
                               "BOOT_protocol": g[a]["BOOT_protocol"],
                               "G1_G9": g[a]["G1_G9"]} for a in CANDIDATES}},
            "decides": "survey_rule",
            "gate": {
                "G1": f"m = 2, base and calibrated: CAGR >= K, or Calmar >= {G1_CALMAR_MULT} x K with CAGR >= K + {G1_RELAX_PP} pp",
                "G2": f"max DD <= K + {G2_MAX_DD_PP} pp (base and calibrated, m = 2)",
                "G3": f"2017+ CAGR >= K + {G3_MIN_CAGR_PP} pp (base and calibrated)",
                "G4": f"CAGR above K in >= {G4_MIN_SUBPERIODS} of P1/P2/P3 (base)",
                "G5": f"block-20 pre-tax bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} paths): median (arm - K) >= {G5_MIN_MEDIAN_PP} pp, base and calibrated",
                "G6": "G1 at m = 1 (base and calibrated)",
                "G7": f"6 OOS windows (calibrated, m = 2): median dCAGR >= 0, >= 5 of 6 with dCAGR >= {G7_MIN_DCAGR_PP} pp and dDD <= +{G7_MAX_DDD_PP} pp, dDD never above +{G7_MAX_DDD_ANY_PP} pp",
                "G8": f"pooled OOS bootstrap (M1-M5, {OOS_PATHS_PER_MARKET} paths each): median (arm - K) >= {G8_MIN_MEDIAN_PP} pp",
                "G9": f"turnover <= {G9_MAX_TURNOVER_MULT} x K",
                "TAIL": (f"P(DD > {TAIL_MAX_DD:.0%}) in the 10-year block-20 bootstrap <= K's + {TAIL_MAX_EXTRA_PP} pp "
                         f"(pre-tax base, after tax at $100k and $1M); median max DD <= {TAIL_MAX_DD_LEVEL:.0%}"),
                "AT": f"after tax at $100k: (arm - K) >= {AT_MIN_DELTA_PP} pp in all three windows",
                "BOOT": (f"clean bootstrap at $100k, B1-recent and B2-flat: median >= {BOOT_MIN_MEDIAN_PP} pp and "
                         f"share > 0 >= {BOOT_PROTOCOL_SHARE:.0%} (protocol; the survey's floor is {BOOT_SURVEY_SHARE:.0%})"),
                "decides": "survey_rule",
            }}
# ----------------------------------------------------------------------------- the run
def run(boot_ref=None) -> dict:
    print(f"pre-tax main window ({len(ARMS)} arms)", flush=True)
    pre, pre_usd = pre_tax_main(ARMS)
    print("after-tax windows (5 capitals)", flush=True)
    win = after_tax_windows(tuple(TAX_CAPITALS), ARMS)
    print("cost stress m = 4", flush=True)
    cost = cost_stress(ARMS)
    print("OOS windows", flush=True)
    oos = oos_stages(ARMS)
    print("pooled OOS bootstrap (G8)", flush=True)
    oos_boot = oos_bootstrap(ARMS)
    print("instruments (R-USD, pre-tax)", flush=True)
    inst, quality = instrument_stages_usd(ARMS)
    if boot_ref is None:
        print(f"clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)", flush=True)
        boot_ref = boot(seed=SEED, block=BLOCK, arms=ARMS, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
                        capitals=("100k", "1M"), modes=("progressive", "flat"),
                        deltas=CANDIDATES + (CONTROL,), baselines=(CONTROL, BASELINE), label="clean")
    print("E1: after-tax 21 ETFs (R-INDEX)", flush=True)
    e1 = stage_e1(ARMS)
    print("E2: rolling January starts", flush=True)
    e2 = stage_e2(ARMS)
    print(f"E3: fresh bootstraps (seed {E3_SEED}, blocks {E3_BLOCKS})", flush=True)
    e3 = stage_e3(ARMS)
    print("E5: decision traces", flush=True)
    e5 = {"decisions": stage_e5_decisions()}
    res = {"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost, "oos": oos,
           "oos_boot": oos_boot, "instruments": inst, "quality": quality, "boot_ref": boot_ref,
           "e1": e1, "e2": e2, "e3": e3, "e5": e5}
    print("reference reproduction checks (every K cell of band_h2_combo_v1)", flush=True)
    res["reference_check"] = reference_checks(res)
    res["boot_anchors"], nbad = boot_anchor_checks(boot_ref)
    res["reference_check"]["boot_anchor_mismatches"] = int(nbad)
    if nbad:
        res["reference_check"]["pass"] = False
    res["gates"] = gates_exposure(res)
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

    hs = harness()
    for k in sorted(hs["reproduction_checks"]):
        v = hs["reproduction_checks"][k]
        if isinstance(v, dict) and "reference" in v:
            print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff']:+.6f}")
        else:
            print(f"harness {k}: {v}")
    for wn, v in hs["after_tax_anchors"].items():
        for arm, x in v.items():
            print(f"tax anchor {wn} {arm}: pre {x['pre_tax']['diff_pp']:+.5f} "
                  f"prog {x['progressive']['diff_pp']:+.5f} dd {x['progressive_max_dd']['diff_pp']:+.5f} pp")
    print(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)")
    if mode == "--harness":
        sys.exit(0 if hs["pass"] else 1)
    if not hs["pass"]:
        print("harness FAIL: not running the stages")
        sys.exit(1)

    res = run()
    g = res["gates"]
    rc = res["reference_check"]
    pre, win, boot_ref = res["pre"], res["windows"], res["boot_ref"]
    oos, e1, e2 = res["oos"], res["e1"], res["e2"]
    ctrl = win[GATE_CAPITAL]
    print(f"deep reference block: {rc['n_cells']} cells ({', '.join(rc['sections'])}), "
          f"{rc['n_mismatch']} mismatches (tol {rc['tolerance']}), max|diff| {rc['max_abs_diff']:.2e}, "
          f"keys only here {rc['n_keys_only_in_mine']} / only in the reference "
          f"{rc['n_keys_only_in_reference']} -> {'PASS' if rc['pass'] else 'FAIL'}")
    for m in rc["mismatches"][:10]:
        print(f"  deep mismatch {m['cell']}: mine {m['mine']:.8f} ref {m['reference']:.8f} "
              f"diff {m['diff']:+.2e}")
    for k in sorted(res["boot_anchors"]):
        v = res["boot_anchors"][k]
        print(f"boot anchor {k}: ref {v['reference']:.6f} repro {v['reproduced']:.6f} diff {v['diff_pp']:+.4f} pp")

    out = {
        "campaign": "exposure_k_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "candidates": list(CANDIDATES),
            "reference_row": BASELINE, "rule": RULE, "vt": VT, "dec_tail": DEC_TAIL,
            "gain_tiers": [[t, b] for t, b in GAIN_TIERS], "b_up": B_UP, "cap": CAP,
            "signals": "production K (equity_leveraged_trend_v1 1.2.0): E2 2-of-3 on price closes "
                       "(SMA200, 252-session price return > 0, close > 126-session max/min midpoint) "
                       "+ early re-entry at 0.5 x vt while E2 off and close > SMA50, vol20 < its "
                       "252-session median and close > close 20 sessions ago; exit early below SMA50",
            "sizing": "vt = min(1, VT/vol20) on synthetic QLD*, cap 0.98; the candidates change VT only",
            "execution": "A1u (USD gain-scaled downsize band 0.5/3/8 pp at g_frac <= 1 %/<= 5 %/> 5 %) "
                         "+ H2 (a non-exit downward resize whose fill session is in the last 10 "
                         "December sessions is postponed); entries, exits to zero and up-resizes "
                         "keep 0.5 pp; the reference row runs the production-R2 layer (no band, no "
                         "deferral)",
            "instrument": "synthetic QLD* daily-reset 2x on QQQ total return, expense 0.95%/yr, "
                          "financing DTB3 + {base 0.0, calibrated 0.75%/yr}",
            "cost_per_side": COST_PER_SIDE, "cost_stress_m": M_STRESS,
            "main_window": pre["window"], "start": START,
            "after_tax": {"engine": "tax_engine.py (byte copy of band_h2_combo_v1/tax_engine.py)",
                          "financing": "base", "m": M, "windows": TAX_WINDOWS,
                          "capitals": TAX_CAPITALS, "gate_capital": GATE_CAPITAL,
                          "clean_bootstrap": {"paths_pre": PRE_BOOT_PATHS, "paths_tax": TAX_BOOT_PATHS,
                                              "seed": SEED, "block": BLOCK,
                                              "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI,
                                              "tail_measured_sessions": boot_ref["tail_gate"]["measured_sessions"]},
                          "fresh_bootstrap": {"paths": E3_PATHS, "seed": E3_SEED, "blocks": list(E3_BLOCKS)}},
            "e1": {"rule": "R-INDEX", "start": E1_RINDEX_START, "financing": "calibrated", "m": M,
                   "capital": GATE_CAPITAL, "n_instruments": len(INSTRUMENTS),
                   "excluded": list(EXCLUDED_INSTRUMENTS)},
            "e2": {"starts": list(E2_START_YEARS), "capital": GATE_CAPITAL},
            "gates": g["gate"],
            "selection": {"survey_selected": g["survey_rule"]["selected"],
                          "protocol_selected": g["protocol_rule"]["selected"],
                          "decides": g["decides"]},
        },
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "reproduction_checks": hs["reproduction_checks"],
                    "after_tax_anchors": hs["after_tax_anchors"]},
        "reference_check": rc,
        "boot_anchors": res["boot_anchors"],
        "pre_tax_main": pre,
        "pre_tax_main_r_usd": res["pre_usd"],
        "after_tax_windows": win,
        "cost_stress_m4": res["cost"],
        "oos": oos,
        "oos_bootstrap": res["oos_boot"],
        "instruments": res["instruments"],
        "quality": res["quality"],
        "bootstrap_clean_seed20261011": boot_ref,
        "E1_after_tax_etfs_rindex": e1,
        "E2_rolling_starts": e2,
        "E3_fresh_bootstraps": res["e3"],
        "E5": res["e5"],
        "gates": g,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = fmt_pct
    w = pre["window"]
    L = [f"exposure_k_v1 | idea I1 of idea_survey_v4: the after-tax exposure frontier under K "
         f"(vt = {VT['F1']}/{VT['F2']}/{VT['F3']} vs K's {VT['K']}) | main window {w['start']} -> "
         f"{w['end']} ({w['sessions']} sessions) | control = production K | arms = K + R2 + F1 + F2 + F3",
         f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)",
         f"deep reference block (K cells of band_h2_combo_v1): {rc['n_cells']} cells "
         f"({', '.join(rc['sections'])}), {rc['n_mismatch']} mismatches (tol {rc['tolerance']}), "
         f"max|diff| {rc['max_abs_diff']:.2e} -> {'PASS' if rc['pass'] else 'FAIL'}",
         f"bootstrap median anchors: {rc['boot_anchor_mismatches']} mismatches",
         f"SELECTION (the survey rule decides): survey = {g['survey_rule']['selected']}, "
         f"common protocol = {g['protocol_rule']['selected']}, deciding rule = {g['decides']}", ""]

    L.append("per arm (pre-tax main window m = 2, R-INDEX; after tax at $100k, base, m = 2, liquidated):")
    L.append("  arm  vt     CAGR base  CAGR cal   maxDD base  maxDD cal   turn/yr | dAT2000  dAT2010  dAT2017 "
             "(pp vs K) | boot d B1/B2 (pp) | OOS med d (pp) | AT maxDD 2000/2010/2017")
    for arm in ARMS:
        a, ac = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        ats = "".join(f"{(ctrl[wn][arm]['after_tax_cagr'] - ctrl[wn][CONTROL]['after_tax_cagr']) * 100:+9.3f}"
                      for wn in TAX_WINDOWS)
        bc = "".join((f"{boot_ref['tax'][GATE_CAPITAL][md][f'{arm}_vs_{CONTROL}']['median_delta_cagr_pp']:+7.3f}"
                      if arm != CONTROL else "      -")
                     for md in ("progressive", "flat"))
        eo = float(np.median([oos[wn][arm].get("delta_cagr_pp", 0.0) for wn in OOS_WINDOWS]))
        dds = "".join(f"{ctrl[wn][arm]['max_dd_after_tax'] * 100:8.2f}" for wn in TAX_WINDOWS)
        L.append(f"  {arm:4s} {VT[arm]:.3f} {p(a['cagr'])} {p(ac['cagr'])} {p(a['max_dd'])} {p(ac['max_dd'])} "
                 f"{a['turnover_annual']:9.2f} |{ats} |{bc} | {eo:+12.3f} |{dds}")
    L.append("")
    L.append("after-tax frontier at $100k (liquidated) -- the selection table:")
    L.append("  arm  vt     AT CAGR 2000  maxDD 2000   AT CAGR 2010  maxDD 2010   AT CAGR 2017  maxDD 2017   "
             "DD budget ok   G1-G9")
    for row in g["frontier"]:
        L.append(f"  {row['arm']:4s} {row['vt']:.3f} {row['after_tax_cagr_100k_2000'] * 100:13.3f} "
                 f"{row['pre_tax_max_dd_base'] * 100:11.2f} {row['after_tax_cagr_100k_2010'] * 100:14.3f} "
                 f"{row['pre_tax_max_dd_cal'] * 100:12.2f} {row['after_tax_cagr_100k_2017'] * 100:14.3f} "
                 f"{'':13s} {'YES' if row['survey_eligible'] else 'no':>5s}   "
                 f"{'PASS' if g['arms'][row['arm']]['G1_G9'] else 'FAIL'}")
    L.append("")
    L.append("per-arm gates versus K:")
    for arm in CANDIDATES:
        r = g["arms"][arm]
        d = r["G1_G9_detail"]
        L.append(f"  {arm} (vt {VT[arm]}): " + " ".join(f"{k}={'P' if d[k] else 'F'}"
                                                        for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9"))
                 + f" | G1-G9 {'PASS' if r['G1_G9'] else 'FAIL'}")
        L.append(f"     G1 dCAGR base {r['G1_detail']['base']['delta_pp']:+.3f}pp / cal "
                 f"{r['G1_detail']['calibrated']['delta_pp']:+.3f}pp; G2 dDD base "
                 f"{r['G2_detail']['delta_pp']['base']:+.2f}pp / cal {r['G2_detail']['delta_pp']['calibrated']:+.2f}pp; "
                 f"G3 dCAGR2017 base {r['G3_detail']['delta_pp']['base']:+.3f} / cal "
                 f"{r['G3_detail']['delta_pp']['calibrated']:+.3f}pp; G4 "
                 f"{r['G4_detail']['n_above']}/3 sub-periods; G5 median base "
                 f"{r['G5_detail']['median_delta_pp']['base']:+.3f} / cal "
                 f"{r['G5_detail']['median_delta_pp']['calibrated']:+.3f}pp; G7 median "
                 f"{r['G7_detail']['median_delta_cagr_pp']:+.3f}pp ({r['G7_detail']['n_within_limits']}/6 within), "
                 f"max dDD {r['G7_detail']['max_delta_max_dd_pp']:+.2f}pp; G8 {r['G8_detail']['diff_vs_C0_median_pp']:+.3f}pp; "
                 f"G9 ratio {r['G9_detail']['ratio']:.3f}")
        c = r["BOOT_detail"]["cells"]
        L.append(f"     AT $100k: " + ", ".join(f"{wn} {r['AT_detail']['delta_pp'][wn]:+.3f}pp" for wn in TAX_WINDOWS)
                 + f" -> {'PASS' if r['AT_all_windows'] else 'FAIL'}; B1-recent median "
                 f"{c['B1_recent']['median_delta_pp']:+.3f}pp share {c['B1_recent']['share_gt_0'] * 100:.1f}%, "
                 f"B2-flat median {c['B2_flat']['median_delta_pp']:+.3f}pp share {c['B2_flat']['share_gt_0'] * 100:.1f}% "
                 f"-> protocol {'PASS' if r['BOOT_protocol'] else 'FAIL'}")
        t = r["TAIL_detail"]["cells"]
        L.append(f"     TAIL P(DD>{TAIL_MAX_DD:.0%}): pre base {t['pre_base']['p_dd_gt_70'] * 100:.1f}% (K "
                 f"{t['pre_base']['p_dd_gt_70_k'] * 100:.1f}%), $100k {t['at_100k_progressive']['p_dd_gt_70'] * 100:.1f}% "
                 f"(K {t['at_100k_progressive']['p_dd_gt_70_k'] * 100:.1f}%), $1M "
                 f"{t['at_1M_progressive']['p_dd_gt_70'] * 100:.1f}% (K {t['at_1M_progressive']['p_dd_gt_70_k'] * 100:.1f}%); "
                 f"median maxDD pre base {t['pre_base']['median_max_dd'] * 100:.1f}% (K "
                 f"{t['pre_base']['median_max_dd_k'] * 100:.1f}%) -> pre base {'PASS' if r['TAIL_pre_base'] else 'FAIL'}, "
                 f"pre cal {'PASS' if r['TAIL_pre_calibrated'] else 'FAIL'}, $100k "
                 f"{'PASS' if r['TAIL_at_100k'] else 'FAIL'}, $1M {'PASS' if r['TAIL_at_1M'] else 'FAIL'}")
        L.append(f"     survey eligibility: G1-G9 {'YES' if r['G1_G9'] else 'no'} and DD budget "
                 f"{'YES' if r['survey_dd_ok'] else 'no'} -> {'ELIGIBLE' if r['survey_eligible'] else 'NOT eligible'}")
        L.append(f"     reporting: E1 21 ETFs median {r['E1_detail']['median_delta_pp']:+.3f}pp "
                 f"({r['E1_detail']['n_gt_0']}/21) -> {'PASS' if r['E1'] else 'FAIL'}; E2 21 rolling starts "
                 f"median {r['E2_detail']['median_delta_pp']:+.3f}pp ({r['E2_detail']['n_gt_0']}/21) -> "
                 f"{'PASS' if r['E2'] else 'FAIL'}; 21 instruments median dCAGR "
                 f"{r['instruments_detail']['median_delta_cagr_pp']:+.3f}pp, worst dDD "
                 f"{r['instruments_detail']['worst_delta_max_dd_pp']:+.2f}pp")
    L.append("")
    L.append("trading activity (main window, m = 2, base, $100k, progressive):")
    for arm in ARMS:
        r = ctrl["2000_03_to_2026_09"][arm]
        L.append(f"  {arm:4s} sells/yr {r['sells_per_year']:6.2f} trades/yr {r['trades_per_year']:6.2f} "
                 f"postponements {r['postponements']:5d} (band {r['blocked_band']:5d}, December "
                 f"{r['deferred']:4d}) tax paid USD {r['tax_usd']:12,.0f} index relief TRY "
                 f"{r['index_relief_try']:15,.0f}")
    L.append("")
    L.append("E5 (report only): fill-decision differences on the main path (pre-tax, $100k, m = 2)")
    dec = res["e5"]["decisions"]
    for pair in ("F1_vs_K", "F2_vs_K", "F3_vs_K", "R2_vs_K", "F2_vs_F1", "F3_vs_F2"):
        a, b = pair.split("_vs_")
        d = dec[pair]
        L.append(f"  {pair:10s}: {d['sessions_decision_differs']:5d} sessions differ "
                 f"({d['share_decision_differs'] * 100:.2f}%); {a} sells where {b} holds "
                 f"{d[f'{a}_sells_{b}_holds']}, {a} holds where {b} sells {d[f'{a}_holds_{b}_sells']}")
    L.append(f"  sells (downward resizes+exits) per arm: "
             + ", ".join(f"{a} {dec['sells'][a]}" for a in ARMS))
    for cl in E5_CAPITALS:
        L.append(f"  after tax at ${cl} (progressive, liquidated, base, m = 2):")
        for wn in TAX_WINDOWS:
            k0 = win[cl][wn][CONTROL]["after_tax_cagr"]
            L.append(f"    {wn:20s} K {p(k0)} | " + " ".join(
                f"{arm} {p(win[cl][wn][arm]['after_tax_cagr'])} "
                f"{(win[cl][wn][arm]['after_tax_cagr'] - k0) * 100:+7.3f}pp" for arm in CANDIDATES))
    L.append("")
    L.append("m = 4 cost stress, after tax at $100k:")
    for wn in TAX_WINDOWS:
        k0 = res["cost"][wn][CONTROL]["after_tax_cagr"]
        L.append(f"  {wn:20s} K {p(k0)} | " + " ".join(
            f"{arm} {p(res['cost'][wn][arm]['after_tax_cagr'])} "
            f"{(res['cost'][wn][arm]['after_tax_cagr'] - k0) * 100:+7.3f}pp" for arm in CANDIDATES))
    L.append("")
    L.append("six OOS windows (pre-tax, calibrated, m = 2, R-USD rule) -- delta vs K:")
    L.append("  window            K CAGR   K maxDD    F1 d     F2 d     F3 d   (CAGR pp)")
    for wn_ in OOS_WINDOWS:
        r = oos[wn_]
        L.append(f"  {wn_:16s} {p(r[CONTROL]['cagr'])}  {p(r[CONTROL]['max_dd'])}  "
                 + " ".join(f"{r[a]['delta_cagr_pp']:+8.3f}" for a in CANDIDATES))
    L.append("")
    L.append("E1 after tax on the 21 quality-passing ETFs (R-INDEX, $100k, calibrated, m = 2, liquidated):")
    L.append("  instrument  window                     K AT      F1 d     F2 d     F3 d")
    for key in INSTRUMENTS:
        r = e1[key]
        L.append(f"  {key:10s}  {r['window']['start']}..{r['window']['end']} {p(r[CONTROL]['after_tax_cagr'])}"
                 + "".join(f"  {r[a]['delta_after_tax_pp']:+7.3f}" for a in CANDIDATES))
    L.append("")
    L.append("E2 rolling January starts (after tax $100k, base, m = 2, to 2026-09-29):")
    L.append("  year   start        K AT       F1 d     F2 d     F3 d")
    for yr in E2_START_YEARS:
        r = e2[str(yr)]
        L.append(f"  {yr}   {r['start']}  {p(r[CONTROL]['after_tax_cagr'])}"
                 + "".join(f" {r[a]['delta_after_tax_pp']:+8.3f}" for a in CANDIDATES))
    L.append("")
    L.append("reference reproduction: " + f"{rc['n_cells']} cells, {rc['n_mismatch']} mismatches, "
             f"max|diff| {rc['max_abs_diff']:.2e} -> {'PASS' if rc['pass'] else 'FAIL'}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
