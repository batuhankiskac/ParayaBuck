#!/usr/bin/env python3
"""band_h2_combo_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Combination study. Question: does the USD gain-scaled downsize band (gain_band_v3's A1u, being
implemented now) plus the year-end vol-resize deferral (tax_harvest_v2's H2) beat A1u alone
after Turkish tax, and does the pair stay sound? Arms: C0 (production R2), A1u, H2, K = A1u+H2.

  C0   production R2 (the control; asserted against the R2 anchors in --harness)
  A1u  gain_band_v3's A1u, exactly: gain-scaled band on a downward resize, B_down =
       0.5 / 3 / 8 pp at g_frac <= 1 % / <= 5 % / > 5 %; g_frac = max(0, USD FIFO gain under
       the live lot book) / USD proceeds (no FX, no YI-UFE indexation); entries, exits to zero
       and up-resizes keep 0.5 pp.
  H2   tax_harvest_v2's H2, exactly, on R2's execution layer: a fill that would be a downward
       resize (target > 0 and target < held weight) in one of the last 10 December sessions is
       not executed; exits to zero and up-resizes always fill; the ordinary rule is
       re-evaluated at every later close (a postponed fill is never re-tried).
  K    A1u + H2. Composition (pre-registered): on the session of a pending downward resize,
       (1) H2's December window is tested FIRST -- if the fill session is one of the last 10
       December sessions and target > 0 and target < held weight, the resize is postponed;
       (2) otherwise A1u's band decides. Because A1u can only suppress a resize the H2 window
       would also postpone (its window test is about the session, not the size), the
       "H2 first" and "A1u first" orderings block the same trades; the ordering only changes
       which counter records the postponement. Exits to zero are never postponed by either.

Stages (pre-registered before any non-control arm was simulated on real data):
  pre      pre-tax main window, base/calibrated x m = 1/2/4 plus the R-USD row.
  AT       after tax at $100k (gate capital) and $30k/$300k/$10k/$1M on the three windows.
  B12      clean bootstrap, seed 20261011, block 20, 1,000 paths, $100k (+$1M report-only),
           B1-recent and B2-flat, arms C0/A1u/H2/K, 5,000 pre-tax paths (E4's leg).
  E1       after tax on the 21 quality-passing ETFs under R-INDEX, $100k, calibrated, m = 2.
  E2       rolling January starts 2001..2021, after tax at $100k, base, m = 2.
  E3       fresh after-tax bootstraps, seed 20261015, blocks 10 and 40, 1,000 paths, $100k.
  E4       the pre-tax leg of B12 (seed 20261011, base and calibrated) + main-window max DD.
  E5       report only: $10k-$1M after-tax windows, the m = 4 cost stress, decision traces.
  OOS      the six ewma_band_v1 windows, pre-tax, calibrated, m = 2 (report).

Gates (pre-registered). K replaces A1u iff all of
  K1  after tax at $100k, K - A1u >= 0 in all three windows;
  K2  B1-recent and B2-flat at $100k: median K - A1u >= 0 and share > 0 >= 50 %;
  K3  pre-tax main-window max DD, K <= A1u + 1.0 pp (base and calibrated, m = 2);
  K4  the 21 January rolling starts: K - A1u >= 0 in >= 60 % of starts.
Soundness of the pair against the incumbent (also gated for K):
  S1  pre-tax main-window CAGR, K >= C0 - 0.10 pp (base and calibrated, m = 2);
  S2  pre-tax main-window max DD, K <= C0 + 1.0 pp (base and calibrated, m = 2);
  S3  turnover, K <= 2 x C0 (G9).
E1-E4 for K vs C0 are reported (the parent campaign's stages) but do not gate this study.

--harness reproduces the control's R2 anchors AND, in its deep part, the published cells of
gain_band_v3's A1u (compared cell-by-cell against research/gain_band_v3/RESULTS.json, which is
committed in this worktree) and of tax_harvest_v2's H2 (compared against the anchors copied
from that campaign's RESULTS.json in the tag archive/research/tax-harvest-v2-2026-10).

--- inherited description of the parent study (gain_band_v3) ---

Confirmation study of the gain-scaled downsize band on evidence gain_band_v2 never looked at.
v2 (control = production R2, verified) FAILED on gate G5 alone -- the pre-tax clean bootstrap
required median delta >= 0 and got -0.4 bp (A1) to -4 bp (A2) -- while every after-tax gate
passed. The band is a tax-timing device (taxable FIFO realisations/yr ~31 -> 6-11), so its
pre-tax effect is expected to be ~0. v3 therefore asks whether the after-tax gain is real and
robust on NEW evidence, and whether the pre-tax cost sits inside a small non-inferiority
margin. A2 (the bracket budget) and A3 are not arms of this study; their code is not carried
over. v3's stages E1-E4 (below) are reused here only as the "K vs C0" reporting block.

Modes
  --smoke    synthetic random-walk prices only, no market data: the rule-gain probes (TRY and
             the A1u USD path) vs the engine's own sell gain, C0 == the reference
             `simulate_band`, H2 == `simulate_band_overlay` for the tail-10 arm, K's December
             window == H2's block set, finiteness and after-tax <= pre-tax, and that the band
             and the deferral actually fire.
  --harness  reproduction on real data: the control's pre-tax CAGR/max DD/turnover vs
             fast_reentry_v1 (= production R2 anchors, asserted explicitly), its after-tax
             anchors at $100k vs vt_level_tax_v1 (10.16 / 14.71 / 22.89), its B1/B2 bootstrap
             medians vs tax_harvest_v2, the pure-E2 anchor series vs e2_integration_v1
             (labelled E2), C0 engine == `simulate_band`; then the deep part -- A1u against
             gain_band_v3/RESULTS.json and H2 against tax_harvest_v2's published cells.
  --run      harness (deep), then the stages above (pre/AT/B12/E1-E5/OOS), the reference
             comparison for every A1u cell the parent campaign published, the H2 anchor
             comparison, the K gates and the K-vs-C0 reporting block. Writes RESULTS.json,
             SUMMARY.txt.

One engine serves every stage: `engine_run` is a stateful session loop over the Turkish
after-tax account (research/band_h2_combo_v1/tax_engine.py, a byte copy of
research/gain_band_v3/tax_engine.py = research/tax_harvest_v2/tax_engine.py = the
r2_tbill_combo_v1 / vt_level_tax_v1 corrected
engine) with production R2's execution layer plus the arm's fill rule. The rule's decision
state (the indexed rule gain and its year accumulator) is computed independently of the
regime, so the pre-tax and after-tax runs follow the same trade path. With arm "C0" the
engine is exactly tax_harvest_v2's tax_run_overlay(overlay="none") on the production R2
target series -- asserted in --harness.

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

# ---- the arm rule (pre-registered; see PREREGISTRATION.md sections 3 and 4)
# This study's arms. A1u is gain_band_v3's A1u verbatim (same tiers, same USD gain fraction);
# H2 is tax_harvest_v2's H2 verbatim (postpone a non-exit downward resize whose *fill* session
# is one of the last DEC_TAIL December sessions); K adds H2's December window to A1u's band.
ARMS = ("C0", "A1u", "H2", "K")
ARMS_ALL = ARMS
CONTROL = "C0"
BASELINE = "A1u"        # the arm K has to beat (the adoption gate in PREREGISTRATION section 5)
CANDIDATES = ("A1u", "H2", "K")
RULE = {
    "C0": {"band": False, "usd_rule": False, "defer": False},
    "A1u": {"band": True, "usd_rule": True, "defer": False},
    "H2": {"band": False, "usd_rule": False, "defer": True},
    "K": {"band": True, "usd_rule": True, "defer": True},
}
RULE["R2"] = RULE["C0"]  # alias: the run's control *is* production R2 (band off, no deferral)
RULE["E2"] = RULE["C0"]  # alias: the harness's pure-E2 fidelity anchor series (band off)
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

# ---- gates (pre-registered; see PREREGISTRATION.md section 5)
# K vs A1u (adoption)
K1_MIN_DELTA_PP = 0.0        # after-tax $100k windows: K - A1u >= 0 in all three
K2_MIN_MEDIAN_PP = 0.0       # clean bootstrap (seed 20261011, block 20, $100k): median >= 0
K2_MIN_SHARE = 0.50          # ... and share > 0 >= this
K3_MAX_DD_PP = 1.0           # main-window pre-tax max DD <= A1u + this (base and calibrated)
K4_MIN_SHARE = 0.60          # 21 January rolling starts with K - A1u >= 0
# soundness of K against the incumbent C0
S1_MIN_CAGR_PP = -0.10       # pre-tax main-window CAGR >= C0 - this (base and calibrated)
S2_MAX_DD_PP = 1.0           # pre-tax main-window max DD <= C0 + this (base and calibrated)
S3_MAX_TURNOVER_MULT = 2.0   # turnover <= this x C0 (G9)
# ---- v3 reporting stages E1-E4 (reused; report-only for K vs C0)
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
V3REF = ROOT / "research/gain_band_v3/RESULTS.json"      # A1u's published cells
H2REF = HERE / "H2_ANCHORS.json"                         # H2's published cells (from the tag)

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
         deltas=CANDIDATES, baselines=(CONTROL,), label="E1"):
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
                                   arm, "pre_tax", m=M)
                    k = len(r["_nav"]) - 1
                    pre[(md, arm)].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
        if pidx < n_tax:
            o_, c_, want = legs["base"]
            for cap_label in capitals:
                for mode in modes:
                    for arm in arms:
                        r = engine_run(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, o_, c_,
                                       want, e2, arm, mode, m=M)
                        if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                            raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode} {cap_label}")
                        at[cap_label][mode][arm].append(r["cagr_usd_liquidated"])
                    if anchor_e2:
                        # anchor label only: pure E2 targets (the reference bootstrap's "C0")
                        r = engine_run(TAX_CAPITALS[GATE_CAPITAL], i0, i1, fixed_dates, mkt, o_, c_,
                                       w_e2, e2, "C0", mode, m=M)
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
                "cagr_median": float(np.median(c)), "diff_vs_C0_median_pp": float(np.median(d) * 100),
                "diff_vs_C0_mean_pp": float(np.mean(d) * 100), "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100), "p95_delta_pp": float(np.percentile(d, 95) * 100)}
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
    # control label "C0" is the *same* target series as "R2" (the v1 bug was the opposite)
    w_ref = arm_signals(price, e2, v20, "R2")[0][0]
    on_eff, factor = r2_state(price, e2, early_components(price))
    checks["r2_state_sizing_matches_arm_signals"] = bool(
        np.array_equal(w_ref, sized_wants(on_eff, factor, v20)[0]))
    checks["wants_of_matches_arm_signals"] = bool(
        np.array_equal(wants_of(price, e2, v20)[1][0], w_ref))
    checks["control_label_C0_is_the_R2_target_series"] = bool(
        np.array_equal(arm_signals(price, e2, v20, "C0")[0][0], w_ref)
        and np.array_equal(w_r2, w_ref))
    checks["E2_anchor_differs_from_R2_series"] = bool(
        not np.array_equal(arm_signals(price, e2, v20, "E2")[0][0], w_ref))

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
    _wc_u, wr_u = wants_of(price_up, e2_u, trv2.vol_n(sc_u)[0])
    blocked = {}
    for arm in ARMS:
        rr = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u,
                        e2_u, arm, "pre_tax")
        blocked[arm] = {"band": rr["blocked_band"], "deferred": rr["deferred"],
                        "postponed": rr["postponements"]}
    checks["band_fires"] = bool(blocked["A1u"]["band"] > 0 or blocked["K"]["band"] > 0)
    checks["deferral_fires"] = bool(blocked["H2"]["deferred"] > 0 and blocked["K"]["deferred"] > 0)
    checks["C0_blocks_nothing"] = bool(blocked["C0"]["postponed"] == 0)
    checks["postponements_decompose"] = bool(all(
        blocked[a]["postponed"] == blocked[a]["band"] + blocked[a]["deferred"] for a in ARMS))

    # 4b. the tax modes bite in a trending series: progressive <= pre-tax, and flat < pre-tax
    flat_bites = prog_bites = False
    for arm in ARMS:
        want_u = wr_u   # the control label "C0" is the R2 target series too
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

    The study's control is production R2, so the label "C0" -- the label the run uses for the
    control, its target series = R2's, band off -- is asserted against fast_reentry_v1's R2
    anchors (pre-tax CAGR / max DD / turnover, m = 1/2 x base/calibrated) and against
    vt_level_tax_v1's R2 after-tax anchors at $100k: pre-tax 15.47 %, after tax 10.16 / 14.71 /
    22.89 % CAGR, max DD 46.76 / 43.78 / 36.24 % (`r2_anchors`). The pure-E2 series is asserted
    separately under its own label "E2" (e2_integration_v1 / tax_harvest_v2). Band-off fill rule
    is identical for both; only the target series differs, so these checks fail if the control
    label drifts off the R2 target series again (the gain_band_v1 bug)."""
    checks, bad = {}, 0
    days, o, c, w_e2, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_ = main_inputs(spread)
        for label, wants, ref in (("E2", we_, REF_E2), ("C0", wr_, REF_R2)):
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
            "reproduced_cagr": checks[f"C0_{md}_m{m}_cagr_usd_unliquidated"]["reproduced"],
            "reference_max_dd": REF_R2[(md, m)][1],
            "reproduced_max_dd": checks[f"C0_{md}_m{m}_max_dd_nav_usd"]["reproduced"],
            "reference_turnover": REF_R2[(md, m)][2],
            "reproduced_turnover": checks[f"C0_{md}_m{m}_turnover_annual"]["reproduced"]}
            for md in FIN for m in (1, 2)},
        "after_tax_100k": {},
    }
    ref_nav, _, _ = simulate_band(o[None, :], c[None, :], w_r2[None, :], COST_PER_SIDE * 2, START, C0_BAND)
    mine = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, "C0", "pre_tax")
    d_nav = float(np.max(np.abs(mine["_nav"] - ref_nav[0])))
    checks["c0_engine_equals_simulate_band"] = {"max_abs_nav_diff": d_nav}
    if d_nav > 1e-9:
        bad += 1
    e2_run = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_e2, e2, "E2", "pre_tax")
    checks["control_is_not_the_E2_anchor_series"] = {
        "max_abs_nav_diff_vs_E2": float(np.max(np.abs(mine["_nav"] - e2_run["_nav"]))),
        "cagr_control_R2": mine["cagr_usd_unliquidated"],
        "cagr_E2_anchor": e2_run["cagr_usd_unliquidated"]}
    if not checks["control_is_not_the_E2_anchor_series"]["max_abs_nav_diff_vs_E2"] > 0.01:
        bad += 1
    ours = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, "A1u", "pre_tax")
    checks["A1u_nav_differs_from_C0"] = {"max_abs_nav_diff": float(np.max(np.abs(ours["_nav"] - mine["_nav"])))}
    tax = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        tax[wn] = {}
        for label, wants, series in (("E2", w_e2, "E2"), ("C0", w_r2, "R2")):
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
            "reproduced": [tax[wn]["C0"]["pre_tax"]["reproduced"],
                           tax[wn]["C0"]["progressive"]["reproduced"],
                           tax[wn]["C0"]["progressive_max_dd"]["reproduced"]],
            "pass": bool(all(abs(v["diff_pp"]) <= 0.005 for v in tax[wn]["C0"].values()))}
    r2_anchors["pass"] = bool(
        all(abs(checks[f"C0_{md}_m{m}_{k}"]["diff"]) <= TOL for md in FIN for m in (1, 2)
            for k in ("cagr_usd_unliquidated", "max_dd_nav_usd", "turnover_annual"))
        and checks["c0_engine_equals_simulate_band"]["max_abs_nav_diff"] <= 1e-9
        and all(r2_anchors["after_tax_100k"][wn]["pass"] for wn in TAX_WINDOWS))
    if not r2_anchors["pass"]:
        bad += 1
    boot_control = {}
    if run_boot:
        b = boot(n_pre=TAX_BOOT_PATHS, n_tax=TAX_BOOT_PATHS, capitals=("100k",), arms=ARMS_ALL)
        for mode in ("progressive", "flat"):
            # boot() drives every arm label, "C0" included, from the R2 target series, so its
            # "C0" is production R2 and matches BOOT_ANCHOR["R2"] (tax_harvest_v2's R2 medians);
            # the pure-E2 series is tracked by its own anchor label and matches "E2".
            boot_control[mode] = {"R2": b["tax"]["100k"][mode]["C0"]["cagr_median"],
                                  "E2": b["anchor_e2_100k"][mode]["cagr_median"]}
            for label, ref_key in (("R2", "R2"), ("E2", "E2")):
                if abs(boot_control[mode][label] - BOOT_ANCHOR[mode][ref_key]) > 0.0010:
                    bad += 1
        boot_control["pre_base"] = {a: b["pre"]["base"][a]["cagr_median"] for a in ARMS}
        boot_control["pre_cal"] = {a: b["pre"]["calibrated"][a]["cagr_median"] for a in ARMS}
    out = {"reproduction_checks": checks, "n_outside_tolerance": bad, "after_tax_anchors": tax,
           "r2_anchors": r2_anchors, "bootstrap_control": boot_control, "pass": bool(bad == 0)}
    if deep:
        out["deep"] = harness_deep()
        if not out["deep"]["pass"]:
            out["pass"] = False
    return out


def harness_deep():
    """The study's own arms against the parent campaigns' published cells, plus the clean
    bootstrap (seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths) that `run()`
    reuses as `boot_ref`."""
    pre, pre_usd = pre_tax_main(ARMS)
    win = after_tax_windows((GATE_CAPITAL,), ARMS)
    cost = cost_stress(ARMS)
    print(f"  deep harness: clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)",
          flush=True)
    boot_ref = boot(seed=SEED, block=BLOCK, arms=ARMS, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
                    capitals=("100k", "1M"), modes=("progressive", "flat"), deltas=CANDIDATES,
                    baselines=(CONTROL, BASELINE), label="clean")
    ref = reference_checks({"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost,
                            "boot_ref": boot_ref}, full=False)
    ref["boot_ref"] = boot_ref
    return ref


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
    for block in E3_BLOCKS:
        for mode in ("progressive", "flat"):
            b = boot(seed=E3_SEED, block=block, arms=arms, n_pre=0, n_tax=E3_PATHS,
                     capitals=(GATE_CAPITAL,), modes=(mode,), anchor_e2=False, deltas=CANDIDATES,
                     baselines=(CONTROL, BASELINE), label=f"E3 block {block} {mode}")
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
    for a, b in (("K", BASELINE), ("K", CONTROL), (BASELINE, CONTROL), ("H2", BASELINE), ("H2", CONTROL)):
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
    """Reproduce the parent campaigns' published cells with this study's code.

    (a) A1u: every numeric leaf of every A1u cell that gain_band_v3 published (pre-tax main
        window, after-tax windows at all five capitals, m = 4 cost stress, the seed-20261011
        clean bootstrap, the six OOS windows, the 21 R-USD instruments, E1/E2/E3) is compared
        against research/gain_band_v3/RESULTS.json (both sides store round(x, 6), hence 2e-6).
        The stages run the same code path with the same seeds and the same per-cell engine
        calls, so a mismatch is a bug in this study.
    (b) H2: the cells tax_harvest_v2 published for H2 (pre-tax main window at m = 1/2, the three
        after-tax windows at $100k, the m = 4 cost stress, the seed-20261011 bootstrap at
        $100k/$1M) are compared against H2_ANCHORS.json, copied from that campaign's tagged
        RESULTS.json. Conventions that differ from th2's *reporting* (not its arithmetic):
        th2 published the pre-tax main window under the liquidated convention, so `cagr` is
        compared against `cagr_liquidated` here (v3's `pre_stats.cagr` is nav-based); the
        postponement counter is split into `blocked_band`/`deferred` (their sum is th2's
        `deferrals` for an H2-only arm, whose band is off).

    `full=False` compares only the cheap cells (pre-tax main, after-tax windows at the gate
    capital, cost stress, bootstrap) -- the deep part of `--harness`; `full=True` adds the
    E1/E2/E3/OOS/instrument stages, which exist only after `run()` has produced them."""
    v3 = json.loads(V3REF.read_text())
    h2 = json.loads(H2REF.read_text())
    cells, only_mine, only_theirs, sections = [], [], [], []

    def add(label, mine, theirs):
        cells.append((label, float(mine), float(theirs)))

    b3 = v3["bootstrap_reference_seed20261011"]
    br = res["boot_ref"]
    sections.append("pre_tax_main")
    for md in FIN:
        for m in ("m1", "m2", "m4"):
            _compare_subtree(f"pre_tax_main.{md}.{m}.A1u", res["pre"][md][m]["A1u"],
                             v3["pre_tax_main"][md][m]["A1u"], cells, only_mine, only_theirs)
        for arm in CANDIDATES:
            _compare_subtree(f"pre_usd.{md}.{arm}", res["pre_usd"][md][arm],
                             v3["pre_tax_main_r_usd"][md][arm], cells, only_mine, only_theirs)
    sections.append("after_tax_windows")
    for cl in TAX_CAPITALS:
        for wn in TAX_WINDOWS:
            _compare_subtree(f"after_tax.{cl}.{wn}.A1u", res["windows"][cl][wn]["A1u"],
                             v3["after_tax_windows"][cl][wn]["A1u"], cells, only_mine, only_theirs)
    sections.append("cost_stress_m4")
    for wn in TAX_WINDOWS:
        _compare_subtree(f"cost.{wn}.A1u", res["cost"][wn]["A1u"], v3["cost_stress_m4"][wn]["A1u"],
                         cells, only_mine, only_theirs)
    sections.append("clean_bootstrap")
    for cl in ("100k", "1M"):
        for mode in ("progressive", "flat"):
            for key in ("A1u", "A1u_vs_C0"):
                _compare_subtree(f"boot.tax.{cl}.{mode}.{key}", br["tax"][cl][mode][key],
                                 b3["tax"][cl][mode][key], cells, only_mine, only_theirs)
    for md in FIN:
        _compare_subtree(f"boot.pre.{md}.A1u", br["pre"][md]["A1u"], b3["pre"][md]["A1u"],
                         cells, only_mine, only_theirs)
    for mode in ("progressive", "flat"):
        add(f"boot.anchor_e2.{mode}.cagr_median", br["anchor_e2_100k"][mode]["cagr_median"],
            b3["anchor_e2_100k"][mode]["cagr_median"])
    if full:
        sections.append("e1_e2_e3_oos_instruments")
        for key in INSTRUMENTS:
            _compare_subtree(f"e1.{key}.A1u", res["e1"][key]["A1u"],
                             v3["E1_after_tax_etfs_rindex"][key]["A1u"], cells, only_mine, only_theirs)
        for yr in E2_START_YEARS:
            _compare_subtree(f"e2.{yr}.A1u", res["e2"][str(yr)]["A1u"],
                             v3["E2_rolling_starts"][str(yr)]["A1u"], cells, only_mine, only_theirs)
        for key in sorted(res["e3"]):
            _compare_subtree(f"e3.{key}.A1u_vs_C0", res["e3"][key]["A1u_vs_C0"],
                             v3["E3_fresh_bootstraps"][key]["A1u_vs_C0"], cells, only_mine, only_theirs)
        for wn in OOS_WINDOWS:
            _compare_subtree(f"oos.{wn}.A1u", res["oos"][wn]["A1u"], v3["oos"][wn]["A1u"],
                             cells, only_mine, only_theirs)
        for key in res["instruments"]:
            _compare_subtree(f"instruments.{key}.A1u", res["instruments"][key]["A1u"],
                             v3["instruments"][key]["A1u"], cells, only_mine, only_theirs)

    # ---- H2 against the tagged campaign's published cells
    h2_map = {"cagr": "cagr_liquidated", "max_dd": "max_dd", "turnover_annual": "turnover_annual"}
    for md in FIN:
        for m in ("m1", "m2"):
            a, b = res["pre"][md][m]["H2"], h2["pre_tax_main"][md][m]["H2"]
            for tk, mk in h2_map.items():
                add(f"h2.pre.{md}.{m}.{tk}", a[mk], b[tk])
            add(f"h2.pre.{md}.{m}.deferrals", a["deferred"], b["deferrals"])
    for wn in TAX_WINDOWS:
        a, b = res["windows"][GATE_CAPITAL][wn]["H2"], h2["after_tax_windows_100k"][wn]["H2"]
        for tk in ("pre_tax_cagr", "after_tax_cagr", "max_dd_after_tax"):
            add(f"h2.window100k.{wn}.{tk}", a[tk], b[tk])
        add(f"h2.window100k.{wn}.deferrals", a["deferred"], b["deferrals"])
    for wn in TAX_WINDOWS:
        a, b = res["cost"][wn]["H2"], h2["cost_stress_m4_100k"][wn]["H2"]
        for tk in ("pre_tax_cagr", "after_tax_cagr", "max_dd_after_tax", "turnover_annual"):
            add(f"h2.cost_m4.{wn}.{tk}", a[tk], b[tk])
    for cl in ("100k", "1M"):
        for mode in ("progressive", "flat"):
            a, b = br["tax"][cl][mode], h2["bootstrap_clean_seed20261011"][cl][mode]
            add(f"h2.boot.{cl}.{mode}.cagr_median", a["H2"]["cagr_median"], b["H2_cagr_median"])
            add(f"h2.boot.{cl}.{mode}.R2_cagr_median", a[CONTROL]["cagr_median"], b["R2_cagr_median"])
            for k in ("median_delta_cagr_pp", "mean_delta_cagr_pp", "share_gt_0",
                      "p05_delta_cagr_pp", "p95_delta_cagr_pp"):
                add(f"h2.boot.{cl}.{mode}.H2_vs_R2.{k}", a["H2_vs_C0"][k], b["H2_vs_R2"][k])

    bad = [(l, m, t, abs(m - t)) for l, m, t in cells if abs(m - t) > 2e-6]
    return {"n_cells": len(cells), "n_mismatch": len(bad), "tolerance": 2e-6,
            "full": bool(full), "sections": sections,
            "max_abs_diff": float(max((abs(m - t) for _, m, t in cells), default=0.0)),
            "n_keys_only_in_mine": len(only_mine), "n_keys_only_in_v3": len(only_theirs),
            "keys_only_in_mine": only_mine[:20], "keys_only_in_v3": only_theirs[:20],
            "mismatches": [{"cell": l, "mine": m, "reference": t, "diff": d} for l, m, t, d in bad][:40],
            "pass": bool(not bad)}


# ----------------------------------------------------------------------------- gates (this study)
def gates_combo(res):
    """K's adoption gate against A1u (K1-K4), the pair's soundness gates against C0 (S1-S3),
    and the parent campaign's E1-E4 reported for every arm against C0. See PREREGISTRATION
    section 5."""
    e1, e2, e3, boot_ref = res["e1"], res["e2"], res["e3"], res["boot_ref"]
    pre, win = res["pre"], res["windows"][GATE_CAPITAL]
    g = {}
    for arm in CANDIDATES:
        r = {}
        # ---- reporting block: the parent campaign's E1-E4 for this arm against C0
        ds = [e1[k][arm]["delta_after_tax_pp"] for k in INSTRUMENTS]
        n_pos = int(sum(1 for d in ds if d > 0))
        r["E1_detail"] = {"n_instruments": len(ds), "median_delta_pp": float(np.median(ds)),
                          "share_gt_0": float(n_pos / len(ds)), "n_gt_0": n_pos,
                          "min_delta_pp": float(min(ds)), "max_delta_pp": float(max(ds)),
                          "median_pre_tax_delta_pp": float(np.median(
                              [e1[k][arm]["delta_pre_tax_pp"] for k in INSTRUMENTS]))}
        r["E1"] = bool(np.median(ds) > 0 and n_pos / len(ds) >= E1_MIN_SHARE)
        ds2 = [e2[str(y)][arm]["delta_after_tax_pp"] for y in E2_START_YEARS]
        n2 = int(sum(1 for d in ds2 if d > 0))
        r["E2_detail"] = {"n_starts": len(ds2), "median_delta_pp": float(np.median(ds2)),
                          "share_gt_0": float(n2 / len(ds2)), "n_gt_0": n2,
                          "min_delta_pp": float(min(ds2)), "max_delta_pp": float(max(ds2)),
                          "deltas_pp": {str(y): e2[str(y)][arm]["delta_after_tax_pp"] for y in E2_START_YEARS}}
        r["E2"] = bool(n2 / len(ds2) >= E2_MIN_SHARE and np.median(ds2) > 0)
        det, ok = {}, True
        for key in sorted(e3):
            cell = e3[key][f"{arm}_vs_{CONTROL}"]
            good = bool(cell["median_delta_cagr_pp"] >= 0.0 and cell["share_gt_0"] >= E3_MIN_SHARE)
            det[key] = {"median_delta_pp": cell["median_delta_cagr_pp"],
                        "mean_delta_pp": cell["mean_delta_cagr_pp"],
                        "share_gt_0": cell["share_gt_0"],
                        "p05_pp": cell["p05_delta_cagr_pp"], "p95_pp": cell["p95_delta_cagr_pp"],
                        "pass": good}
            ok = ok and good
        r["E3_detail"] = det
        r["E3"] = bool(ok)
        bm_b = boot_ref["pre"]["base"][arm]["diff_vs_C0_median_pp"]
        bm_c = boot_ref["pre"]["calibrated"][arm]["diff_vs_C0_median_pp"]
        ddb = (pre["base"]["m2"][arm]["max_dd"] - pre["base"]["m2"][CONTROL]["max_dd"]) * 100
        ddc = (pre["calibrated"]["m2"][arm]["max_dd"] - pre["calibrated"]["m2"][CONTROL]["max_dd"]) * 100
        r["E4_detail"] = {"bootstrap_median_pp": {"base": bm_b, "calibrated": bm_c},
                          "bootstrap_share_gt_0": {"base": boot_ref["pre"]["base"][arm]["share_gt_0"],
                                                   "calibrated": boot_ref["pre"]["calibrated"][arm]["share_gt_0"]},
                          "max_dd_delta_pp": {"base": ddb, "calibrated": ddc},
                          "margin_pp": E4_MIN_MEDIAN_PP, "max_dd_pp": E4_MAX_DD_PP}
        r["E4"] = bool(bm_b >= E4_MIN_MEDIAN_PP and bm_c >= E4_MIN_MEDIAN_PP
                       and ddb <= E4_MAX_DD_PP + 1e-9 and ddc <= E4_MAX_DD_PP + 1e-9)
        r["E_report"] = {k: r[k] for k in ("E1", "E2", "E3", "E4")}
        # ---- against C0 (the incumbent), for the record
        r["vs_C0"] = {
            "pre_tax_cagr_base_m2": pre["base"]["m2"][arm]["cagr"],
            "pre_tax_cagr_cal_m2": pre["calibrated"]["m2"][arm]["cagr"],
            "pre_tax_cagr_delta_pp_base": (pre["base"]["m2"][arm]["cagr"] - pre["base"]["m2"][CONTROL]["cagr"]) * 100,
            "pre_tax_cagr_delta_pp_cal": (pre["calibrated"]["m2"][arm]["cagr"] - pre["calibrated"]["m2"][CONTROL]["cagr"]) * 100,
            "pre_tax_max_dd_base": pre["base"]["m2"][arm]["max_dd"],
            "pre_tax_max_dd_cal": pre["calibrated"]["m2"][arm]["max_dd"],
            "pre_tax_max_dd_delta_pp_base": (pre["base"]["m2"][arm]["max_dd"] - pre["base"]["m2"][CONTROL]["max_dd"]) * 100,
            "pre_tax_max_dd_delta_pp_cal": (pre["calibrated"]["m2"][arm]["max_dd"] - pre["calibrated"]["m2"][CONTROL]["max_dd"]) * 100,
            "turnover_annual": pre["base"]["m2"][arm]["turnover_annual"],
            "turnover_ratio_vs_C0": pre["base"]["m2"][arm]["turnover_annual"] / pre["base"]["m2"][CONTROL]["turnover_annual"],
            "after_tax_100k": {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                                    "delta_vs_C0_pp": (win[wn][arm]["after_tax_cagr"]
                                                       - win[wn][CONTROL]["after_tax_cagr"]) * 100}
                               for wn in TAX_WINDOWS},
            "oos_median_delta_pp": float(np.median(
                [res["oos"][wn][arm]["delta_cagr_pp"] for wn in OOS_WINDOWS])),
            "boot_seed20261011_median_delta_pp": {
                mode: {cl: boot_ref["tax"][cl][mode][f"{arm}_vs_C0"]["median_delta_cagr_pp"]
                       for cl in ("100k", "1M")} for mode in ("progressive", "flat")},
            "boot_seed20261011_share_gt_0": {
                mode: {cl: boot_ref["tax"][cl][mode][f"{arm}_vs_C0"]["share_gt_0"]
                       for cl in ("100k", "1M")} for mode in ("progressive", "flat")},
        }
        # ---- against A1u (the arm being implemented now), for the record
        r["vs_A1u"] = {
            "after_tax_100k": {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                                    "delta_vs_A1u_pp": (win[wn][arm]["after_tax_cagr"]
                                                        - win[wn][BASELINE]["after_tax_cagr"]) * 100}
                               for wn in TAX_WINDOWS},
            "pre_tax_cagr_delta_pp_base": (pre["base"]["m2"][arm]["cagr"] - pre["base"]["m2"][BASELINE]["cagr"]) * 100,
            "pre_tax_max_dd_delta_pp_base": (pre["base"]["m2"][arm]["max_dd"] - pre["base"]["m2"][BASELINE]["max_dd"]) * 100,
            "pre_tax_max_dd_delta_pp_cal": (pre["calibrated"]["m2"][arm]["max_dd"] - pre["calibrated"]["m2"][BASELINE]["max_dd"]) * 100,
            "boot_median_delta_pp": {mode: boot_ref["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{BASELINE}"]["median_delta_cagr_pp"]
                                     for mode in ("progressive", "flat")},
            "boot_share_gt_0": {mode: boot_ref["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{BASELINE}"]["share_gt_0"]
                                for mode in ("progressive", "flat")},
        }
        g[arm] = r
    # ---- K's adoption gate (against A1u) and soundness gate (against C0)
    k = g["K"]
    k1 = {wn: (win[wn]["K"]["after_tax_cagr"] - win[wn][BASELINE]["after_tax_cagr"]) * 100
          for wn in TAX_WINDOWS}
    k["K1_detail"] = {"deltas_pp": k1, "min_delta_pp": float(min(k1.values())),
                      "required_pp": K1_MIN_DELTA_PP}
    k["K1"] = bool(all(d >= K1_MIN_DELTA_PP for d in k1.values()))
    k2 = {}
    for mode in ("progressive", "flat"):
        c = boot_ref["tax"][GATE_CAPITAL][mode]["K_vs_A1u"]
        k2[mode] = {"median_delta_pp": c["median_delta_cagr_pp"], "share_gt_0": c["share_gt_0"],
                    "mean_delta_pp": c["mean_delta_cagr_pp"],
                    "p05_pp": c["p05_delta_cagr_pp"], "p95_pp": c["p95_delta_cagr_pp"],
                    "pass": bool(c["median_delta_cagr_pp"] >= K2_MIN_MEDIAN_PP
                                 and c["share_gt_0"] >= K2_MIN_SHARE)}
    k["K2_detail"] = {"cells": k2, "required_median_pp": K2_MIN_MEDIAN_PP,
                      "required_share_gt_0": K2_MIN_SHARE}
    k["K2"] = bool(all(v["pass"] for v in k2.values()))
    ddb = (pre["base"]["m2"]["K"]["max_dd"] - pre["base"]["m2"][BASELINE]["max_dd"]) * 100
    ddc = (pre["calibrated"]["m2"]["K"]["max_dd"] - pre["calibrated"]["m2"][BASELINE]["max_dd"]) * 100
    k["K3_detail"] = {"max_dd_delta_pp": {"base": ddb, "calibrated": ddc},
                      "max_dd_K": {"base": pre["base"]["m2"]["K"]["max_dd"],
                                   "calibrated": pre["calibrated"]["m2"]["K"]["max_dd"]},
                      "max_dd_A1u": {"base": pre["base"]["m2"][BASELINE]["max_dd"],
                                     "calibrated": pre["calibrated"]["m2"][BASELINE]["max_dd"]},
                      "allowed_pp": K3_MAX_DD_PP}
    k["K3"] = bool(ddb <= K3_MAX_DD_PP + 1e-9 and ddc <= K3_MAX_DD_PP + 1e-9)
    k4d = [ (e2[str(y)]["K"]["after_tax_cagr"] - e2[str(y)][BASELINE]["after_tax_cagr"]) * 100
            for y in E2_START_YEARS ]
    n4 = int(sum(1 for d in k4d if d >= 0.0))
    k["K4_detail"] = {"n_starts": len(k4d), "n_ge_0": n4, "share_ge_0": n4 / len(k4d),
                      "median_delta_pp": float(np.median(k4d)),
                      "max_delta_pp": float(max(k4d)), "min_delta_pp": float(min(k4d)),
                      "deltas_pp": {str(y): d for y, d in zip(E2_START_YEARS, k4d)},
                      "required_share": K4_MIN_SHARE}
    k["K4"] = bool(n4 / len(k4d) >= K4_MIN_SHARE)
    s1 = {"base": (pre["base"]["m2"]["K"]["cagr"] - pre["base"]["m2"][CONTROL]["cagr"]) * 100,
          "calibrated": (pre["calibrated"]["m2"]["K"]["cagr"] - pre["calibrated"]["m2"][CONTROL]["cagr"]) * 100}
    s2 = {"base": (pre["base"]["m2"]["K"]["max_dd"] - pre["base"]["m2"][CONTROL]["max_dd"]) * 100,
          "calibrated": (pre["calibrated"]["m2"]["K"]["max_dd"] - pre["calibrated"]["m2"][CONTROL]["max_dd"]) * 100}
    s3 = pre["base"]["m2"]["K"]["turnover_annual"] / pre["base"]["m2"][CONTROL]["turnover_annual"]
    k["soundness"] = {"S1_detail": {"cagr_delta_pp": s1, "min_pp": S1_MIN_CAGR_PP},
                      "S2_detail": {"max_dd_delta_pp": s2, "max_pp": S2_MAX_DD_PP},
                      "S3_detail": {"turnover_ratio": s3, "max_ratio": S3_MAX_TURNOVER_MULT},
                      "S1": bool(s1["base"] >= S1_MIN_CAGR_PP and s1["calibrated"] >= S1_MIN_CAGR_PP),
                      "S2": bool(s2["base"] <= S2_MAX_DD_PP + 1e-9 and s2["calibrated"] <= S2_MAX_DD_PP + 1e-9),
                      "S3": bool(s3 <= S3_MAX_TURNOVER_MULT + 1e-9)}
    k["soundness"]["pass"] = bool(k["soundness"]["S1"] and k["soundness"]["S2"] and k["soundness"]["S3"])
    g["K"]["adopted"] = bool(k["K1"] and k["K2"] and k["K3"] and k["K4"] and k["soundness"]["pass"])
    adopted = "K" if g["K"]["adopted"] else "A1u"
    reason = ("K passes K1-K4 and S1-S3 (H2 adds to the band)"
              if g["K"]["adopted"] else
              "K does not clear every gate: "
              + ", ".join(f"{x}={'PASS' if (k[x] if x in ('K1','K2','K3','K4') else k['soundness'][x]) else 'FAIL'}"
                          for x in ("K1", "K2", "K3", "K4", "S1", "S2", "S3"))
              + " -> A1u alone")
    return {"arms": g, "adopted": adopted, "reason": reason,
            "gate": {"K1": f"after-tax $100k K - A1u >= {K1_MIN_DELTA_PP} pp in all three windows",
                     "K2": f"clean bootstrap (seed {SEED}, block {BLOCK}, $100k) K - A1u median >= "
                           f"{K2_MIN_MEDIAN_PP} pp and share > 0 >= {K2_MIN_SHARE:.0%} (B1-recent and B2-flat)",
                     "K3": f"pre-tax main-window max DD, K <= A1u + {K3_MAX_DD_PP} pp (base and calibrated, m = 2)",
                     "K4": f"21 January rolling starts: K - A1u >= 0 in >= {K4_MIN_SHARE:.0%}",
                     "S1": f"pre-tax main-window CAGR, K >= C0 + {S1_MIN_CAGR_PP} pp (base and calibrated)",
                     "S2": f"pre-tax main-window max DD, K <= C0 + {S2_MAX_DD_PP} pp (base and calibrated)",
                     "S3": f"turnover, K <= {S3_MAX_TURNOVER_MULT} x C0",
                     "adopt": "K iff K1 and K2 and K3 and K4 and S1 and S2 and S3; else A1u alone"}}


# ----------------------------------------------------------------------------- the run
def run(boot_ref=None) -> dict:
    print("pre-tax main window (4 arms)", flush=True)
    pre, pre_usd = pre_tax_main(ARMS)
    print("after-tax windows (5 capitals)", flush=True)
    win = after_tax_windows(tuple(TAX_CAPITALS), ARMS)
    print("cost stress m = 4", flush=True)
    cost = cost_stress(ARMS)
    print("OOS windows", flush=True)
    oos = oos_stages(ARMS)
    print("instruments (R-USD, pre-tax)", flush=True)
    inst, quality = instrument_stages_usd(ARMS)
    if boot_ref is None:
        print(f"clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)", flush=True)
        boot_ref = boot(seed=SEED, block=BLOCK, arms=ARMS, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
                        capitals=("100k", "1M"), modes=("progressive", "flat"), deltas=CANDIDATES,
                        baselines=(CONTROL, BASELINE), label="clean")
    print("E1: after-tax 21 ETFs (R-INDEX)", flush=True)
    e1 = stage_e1(ARMS)
    print("E2: rolling January starts", flush=True)
    e2 = stage_e2(ARMS)
    print(f"E3: fresh bootstraps (seed {E3_SEED}, blocks {E3_BLOCKS})", flush=True)
    e3 = stage_e3(ARMS)
    print("E5: decision traces", flush=True)
    e5 = {"decisions": stage_e5_decisions()}
    res = {"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost, "oos": oos,
           "instruments": inst, "quality": quality, "boot_ref": boot_ref,
           "e1": e1, "e2": e2, "e3": e3, "e5": e5}
    print("reference reproduction checks (A1u vs gain_band_v3, H2 vs tax_harvest_v2)", flush=True)
    res["reference_check"] = reference_checks(res)
    res["gates"] = gates_combo(res)
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
    print(f"R2 anchor block (the control label the run uses): {'PASS' if ra['pass'] else 'FAIL'}")
    if hs.get("deep"):
        d = hs["deep"]
        print(f"deep reference block: {d['n_cells']} cells ({', '.join(d['sections'])}), "
              f"{d['n_mismatch']} mismatches (tol {d['tolerance']}), max|diff| {d['max_abs_diff']:.2e}, "
              f"keys only in mine {d['n_keys_only_in_mine']} / only in v3 {d['n_keys_only_in_v3']} -> "
              f"{'PASS' if d['pass'] else 'FAIL'}")
        for m in d["mismatches"][:10]:
            print(f"  deep mismatch {m['cell']}: mine {m['mine']:.8f} ref {m['reference']:.8f} "
                  f"diff {m['diff']:+.2e}")
    print(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)")
    if mode == "--harness" or not hs["pass"]:
        sys.exit(0 if hs["pass"] else 1)

    res = run(boot_ref=hs["deep"]["boot_ref"])
    g = res["gates"]["arms"]
    rc = res["reference_check"]
    pre, win, e1, e2, e3, boot_ref = res["pre"], res["windows"], res["e1"], res["e2"], res["e3"], res["boot_ref"]
    ctrl = win[GATE_CAPITAL]

    out = {
        "campaign": "band_h2_combo_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "candidates": list(CANDIDATES),
            "baseline_for_adoption": BASELINE, "rule": RULE, "dec_tail": DEC_TAIL,
            "gain_tiers": [[t, b] for t, b in GAIN_TIERS], "b_up": B_UP,
            "composition": ("K = A1u + H2. On a pending discretionary downward resize the H2 "
                            "December window is tested FIRST: if the fill session is one of the "
                            "last DEC_TAIL December sessions and 0 < target < held weight, the "
                            "resize is postponed; otherwise A1u's gain-scaled band decides. "
                            "Exits to zero and up-resizes always fill. A suppressed or postponed "
                            "resize is never re-tried: the target is re-evaluated at every close."),
            "signals": "production R2 (equity_leveraged_trend_v1 1.2.0): E2 2-of-3 on price closes "
                       "(SMA200, 252-session price return > 0, close > 126-session max/min midpoint) "
                       "+ early re-entry at 0.5 x vt while E2 off and close > SMA50, vol20 < its "
                       "252-session median and close > close 20 sessions ago; exit early below SMA50",
            "sizing": "vt = min(1, 0.35/vol20) on synthetic QLD*, cap 0.98",
            "instrument": "synthetic QLD* daily-reset 2x on QQQ total return, expense 0.95%/yr, "
                          "financing DTB3 + {base 0.0, calibrated 0.75%/yr}",
            "cost_per_side": COST_PER_SIDE, "cost_stress_m": M_STRESS,
            "main_window": pre["window"], "start": START,
            "after_tax": {"engine": "tax_engine.py (byte copy of gain_band_v3/tax_engine.py)",
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
        },
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "reproduction_checks": hs["reproduction_checks"],
                    "r2_anchors": hs["r2_anchors"], "after_tax_anchors": hs["after_tax_anchors"],
                    "bootstrap_control_medians": hs["bootstrap_control"],
                    "deep_reference": {k: v for k, v in hs["deep"].items() if k != "boot_ref"}},
        "reference_check": {k: v for k, v in rc.items()},
        "pre_tax_main": pre,
        "pre_tax_main_r_usd": res["pre_usd"],
        "after_tax_windows": win,
        "cost_stress_m4": res["cost"],
        "oos": res["oos"],
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
    L = [f"band_h2_combo_v1 | A1u (gain-scaled downsize band) + H2 (December vol-resize deferral) "
         f"vs A1u alone | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
         f"control = production R2 | arms = C0 + A1u + H2 + K(=A1u+H2)",
         f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance); "
         f"control R2 anchors {'PASS' if hs['r2_anchors']['pass'] else 'FAIL'}; "
         f"C0 engine == simulate_band max|diff| "
         f"{hs['reproduction_checks']['c0_engine_equals_simulate_band']['max_abs_nav_diff']:.2e}",
         f"deep reference block: {rc['n_cells']} cells ({', '.join(rc['sections'])}), "
         f"{rc['n_mismatch']} mismatches (tol {rc['tolerance']}), max|diff| {rc['max_abs_diff']:.2e} -> "
         f"{'PASS' if rc['pass'] else 'FAIL'}",
         f"ADOPTED: {res['gates']['adopted']} -- {res['gates']['reason']}", ""]

    L.append("per arm (pre-tax main window m = 2, R-INDEX; after tax at $100k, base, m = 2, liquidated):")
    L.append("  arm   CAGR base  CAGR cal   maxDD base  maxDD cal   turn/yr | dAT2000  dAT2010  dAT2017 "
             "(pp vs C0) | dB2000  dB2010  dB2017 (pp vs A1u) | boot d vs C0 B1/B2 | boot d vs A1u B1/B2 | "
             "OOS med d (pp) | E1 E2 E3 E4 vs C0")
    for arm in ARMS:
        a, ac = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        ats = "".join(f"{(ctrl[wn][arm]['after_tax_cagr'] - ctrl[wn][CONTROL]['after_tax_cagr']) * 100:+9.3f}"
                      for wn in TAX_WINDOWS)
        atb = "".join(f"{(ctrl[wn][arm]['after_tax_cagr'] - ctrl[wn][BASELINE]['after_tax_cagr']) * 100:+9.3f}"
                      for wn in TAX_WINDOWS)
        bc = "".join(f"{boot_ref['tax'][GATE_CAPITAL][md][f'{arm}_vs_C0']['median_delta_cagr_pp']:+7.3f}"
                     for md in ("progressive", "flat"))
        bb = "".join(f"{boot_ref['tax'][GATE_CAPITAL][md][f'{arm}_vs_{BASELINE}']['median_delta_cagr_pp']:+7.3f}"
                     if arm != BASELINE else "      -"
                     for md in ("progressive", "flat"))
        eo = float(np.median([res["oos"][wn][arm]["delta_cagr_pp"] for wn in OOS_WINDOWS]))
        if arm == CONTROL:
            fl = "    -     -     -     -"
        else:
            fl = "  ".join(f"{x}={'P' if g[arm][x] else 'F'}" for x in ("E1", "E2", "E3", "E4"))
        L.append(f"  {arm:4s} {p(a['cagr'])} {p(ac['cagr'])} {p(a['max_dd'])} {p(ac['max_dd'])} "
                 f"{a['turnover_annual']:9.2f} |{ats} |{atb} |{bc} |{bb} | {eo:+12.3f} | {fl}")
    L.append("")
    L.append(f"K's adoption gate vs A1u (K1-K4) and soundness vs C0 (S1-S3):")
    kk = g["K"]
    L.append(f"  K1 after-tax $100k K - A1u >= 0 in all three windows: "
             + ", ".join(f"{wn} {kk['K1_detail']['deltas_pp'][wn]:+.3f}pp" for wn in TAX_WINDOWS)
             + f" -> {'PASS' if kk['K1'] else 'FAIL'}")
    L.append("  K2 clean bootstrap (seed 20261011, block 20, 1,000 paths, $100k):")
    for md in ("progressive", "flat"):
        c = kk["K2_detail"]["cells"][md]
        L.append(f"     {md:12s} median {c['median_delta_pp']:+.3f}pp mean {c['mean_delta_pp']:+.3f}pp "
                 f"share>0 {c['share_gt_0'] * 100:.1f}% p05/p95 {c['p05_pp']:+.2f}/{c['p95_pp']:+.2f} -> "
                 f"{'PASS' if c['pass'] else 'FAIL'}")
    L.append(f"     K2 overall -> {'PASS' if kk['K2'] else 'FAIL'}")
    L.append(f"  K3 main-window max DD, K <= A1u + {K3_MAX_DD_PP} pp: base "
             f"{kk['K3_detail']['max_dd_K']['base'] * 100:.2f}% vs A1u "
             f"{kk['K3_detail']['max_dd_A1u']['base'] * 100:.2f}% ({kk['K3_detail']['max_dd_delta_pp']['base']:+.2f}pp), "
             f"calibrated {kk['K3_detail']['max_dd_K']['calibrated'] * 100:.2f}% vs "
             f"{kk['K3_detail']['max_dd_A1u']['calibrated'] * 100:.2f}% "
             f"({kk['K3_detail']['max_dd_delta_pp']['calibrated']:+.2f}pp) -> {'PASS' if kk['K3'] else 'FAIL'}")
    L.append(f"  K4 rolling January starts, K - A1u >= 0 in >= {K4_MIN_SHARE:.0%}: "
             f"{kk['K4_detail']['n_ge_0']}/{kk['K4_detail']['n_starts']} "
             f"({kk['K4_detail']['share_ge_0'] * 100:.1f}%), median {kk['K4_detail']['median_delta_pp']:+.3f}pp, "
             f"min {kk['K4_detail']['min_delta_pp']:+.3f}pp, max {kk['K4_detail']['max_delta_pp']:+.3f}pp -> "
             f"{'PASS' if kk['K4'] else 'FAIL'}")
    s = kk["soundness"]
    L.append(f"  S1 (CAGR >= C0 {S1_MIN_CAGR_PP} pp): base {s['S1_detail']['cagr_delta_pp']['base']:+.3f}pp, cal "
             f"{s['S1_detail']['cagr_delta_pp']['calibrated']:+.3f}pp -> {'PASS' if s['S1'] else 'FAIL'}")
    L.append(f"  S2 (max DD <= C0 + {S2_MAX_DD_PP} pp): base {s['S2_detail']['max_dd_delta_pp']['base']:+.2f}pp, "
             f"cal {s['S2_detail']['max_dd_delta_pp']['calibrated']:+.2f}pp -> {'PASS' if s['S2'] else 'FAIL'}")
    L.append(f"  S3 (turnover <= {S3_MAX_TURNOVER_MULT} x C0): ratio {s['S3_detail']['turnover_ratio']:.3f} -> "
             f"{'PASS' if s['S3'] else 'FAIL'}")
    L.append("")
    L.append(f"postponement counters (main window, m = 2, base, $100k, progressive):")
    for arm in ARMS:
        r = ctrl["2000_03_to_2026_09"][arm]
        L.append(f"  {arm:4s} postponements {r['postponements']:5d} (band {r['blocked_band']:5d}, "
                 f"December-deferred {r['deferred']:4d}) sells/yr {r['sells_per_year']:.2f}")
    L.append("")
    L.append("E1 after tax on the 21 quality-passing ETFs (R-INDEX, $100k, calibrated, m = 2, liquidated) -- "
             "reporting block vs C0:")
    L.append("  instrument  window                     C0 AT    A1u d     H2 d      K d    K vs A1u")
    for key in INSTRUMENTS:
        r = e1[key]
        L.append(f"  {key:10s}  {r['window']['start']}..{r['window']['end']} {p(r[CONTROL]['after_tax_cagr'])}"
                 f"  {r['A1u']['delta_after_tax_pp']:+7.3f}  {r['H2']['delta_after_tax_pp']:+7.3f}  "
                 f"{r['K']['delta_after_tax_pp']:+7.3f}  "
                 f"{(r['K']['after_tax_cagr'] - r['A1u']['after_tax_cagr']) * 100:+7.3f}")
    for arm in CANDIDATES:
        L.append(f"  E1 {arm}: median {g[arm]['E1_detail']['median_delta_pp']:+.3f}pp "
                 f"share>0 {g[arm]['E1_detail']['share_gt_0'] * 100:.1f}% ({g[arm]['E1_detail']['n_gt_0']}/21) "
                 f"-> {'PASS' if g[arm]['E1'] else 'FAIL'} (vs C0, parent gate)")
    L.append("")
    L.append("E2 rolling January starts (after tax $100k, base, m = 2, to 2026-09-29):")
    L.append("  year   start        C0 AT     A1u d      H2 d       K d    K vs A1u")
    for yr in E2_START_YEARS:
        r = e2[str(yr)]
        L.append(f"  {yr}   {r['start']}  {p(r[CONTROL]['after_tax_cagr'])} {r['A1u']['delta_after_tax_pp']:+8.3f} "
                 f"{r['H2']['delta_after_tax_pp']:+9.3f} {r['K']['delta_after_tax_pp']:+9.3f}  "
                 f"{(r['K']['after_tax_cagr'] - r['A1u']['after_tax_cagr']) * 100:+8.3f}")
    for arm in CANDIDATES:
        L.append(f"  E2 {arm}: median {g[arm]['E2_detail']['median_delta_pp']:+.3f}pp "
                 f"share>0 {g[arm]['E2_detail']['share_gt_0'] * 100:.1f}% ({g[arm]['E2_detail']['n_gt_0']}/21) "
                 f"-> {'PASS' if g[arm]['E2'] else 'FAIL'} (vs C0, parent gate)")
    L.append("")
    L.append(f"E3 fresh after-tax bootstraps (seed {E3_SEED}, {E3_PATHS} paths, $100k, blocks {E3_BLOCKS}) "
             f"-- vs C0 and vs A1u:")
    L.append("  run                    arm    d vs C0  share>0    d vs A1u  share>0")
    for key in sorted(e3):
        for arm in CANDIDATES:
            v = e3[key][f"{arm}_vs_{CONTROL}"]
            w2 = e3[key].get(f"{arm}_vs_{BASELINE}")
            L.append(f"  {key:20s} {arm:4s} {v['median_delta_cagr_pp']:+10.3f} "
                     f"{v['share_gt_0'] * 100:7.1f}%   "
                     + (f"{w2['median_delta_cagr_pp']:+10.3f} {w2['share_gt_0'] * 100:8.1f}%"
                        if w2 else f"{'-':>10s} {'-':>8s}"))
    L.append("")
    L.append(f"E4 pre-tax non-inferiority (clean bootstrap seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} "
             f"pre-tax paths; margin {E4_MIN_MEDIAN_PP} pp) + main-window max DD:")
    L.append("  arm   boot median d base  boot median d cal   per-window max DD d vs C0 (pp)  gate")
    for arm in CANDIDATES:
        d = g[arm]["E4_detail"]
        L.append(f"  {arm:4s} {d['bootstrap_median_pp']['base']:+19.3f} "
                 f"{d['bootstrap_median_pp']['calibrated']:+19.3f}   "
                 f"{d['max_dd_delta_pp']['base']:+9.2f}/{d['max_dd_delta_pp']['calibrated']:+9.2f}   "
                 f"{'PASS' if g[arm]['E4'] else 'FAIL'}")
    L.append("")
    L.append("E5 (report only): fill-decision differences on the main path (pre-tax, $100k, m = 2)")
    dec = res["e5"]["decisions"]
    for pair in ("K_vs_A1u", "K_vs_C0", "A1u_vs_C0", "H2_vs_A1u", "H2_vs_C0"):
        a, b = pair.split("_vs_")
        d = dec[pair]
        L.append(f"  {pair:12s}: {d['sessions_decision_differs']:5d} sessions differ "
                 f"({d['share_decision_differs'] * 100:.2f}%); {a} sells where {b} holds "
                 f"{d[f'{a}_sells_{b}_holds']}, {a} holds where {b} sells {d[f'{a}_holds_{b}_sells']}")
    L.append(f"  sells (downward resizes+exits) per arm: "
             + ", ".join(f"{a} {dec['sells'][a]}" for a in ARMS))
    for cl in E5_CAPITALS:
        L.append(f"  after tax at ${cl} (progressive, liquidated, base, m = 2):")
        for wn in TAX_WINDOWS:
            c0 = win[cl][wn][CONTROL]["after_tax_cagr"]
            L.append(f"    {wn:20s} C0 {p(c0)} | " + " ".join(
                f"{arm} {p(win[cl][wn][arm]['after_tax_cagr'])} "
                f"{(win[cl][wn][arm]['after_tax_cagr'] - c0) * 100:+7.3f}pp" for arm in CANDIDATES))
    L.append("")
    L.append(f"m = 4 cost stress, after tax at $100k:")
    for wn in TAX_WINDOWS:
        c0 = res["cost"][wn][CONTROL]["after_tax_cagr"]
        L.append(f"  {wn:20s} C0 {p(c0)} | " + " ".join(
            f"{arm} {p(res['cost'][wn][arm]['after_tax_cagr'])} "
            f"{(res['cost'][wn][arm]['after_tax_cagr'] - c0) * 100:+7.3f}pp" for arm in CANDIDATES))
    L.append("")
    L.append("six OOS windows (pre-tax, calibrated, m = 2, R-USD rule) -- delta vs C0:")
    L.append("  window            C0 CAGR   C0 maxDD   A1u d     H2 d      K d   (CAGR pp)")
    for wn_ in OOS_WINDOWS:
        r = res["oos"][wn_]
        L.append(f"  {wn_:16s} {p(r[CONTROL]['cagr'])}  {p(r[CONTROL]['max_dd'])}  "
                 f"{r['A1u']['delta_cagr_pp']:+8.3f} {r['H2']['delta_cagr_pp']:+9.3f} "
                 f"{r['K']['delta_cagr_pp']:+9.3f}")
    L.append("")
    L.append("reference reproduction: " + f"{rc['n_cells']} cells, {rc['n_mismatch']} mismatches, "
             f"max|diff| {rc['max_abs_diff']:.2e} -> {'PASS' if rc['pass'] else 'FAIL'}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
