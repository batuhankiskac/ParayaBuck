#!/usr/bin/env python3
"""ladder_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Early-leg conviction ladder (idea_survey_v3 section 5, idea I4). Question: production K holds the
early re-entry leg at a *constant* 0.5 x vt; does sizing that leg by a volatility-normalized
conviction metric beat K after Turkish tax without a worse drawdown?

  C0   production K, exactly: R2's target series (E2 2-of-3 vote + the early re-entry leg) with
       K's execution layer -- A1u's USD gain-scaled downsize band (0.5 / 3 / 8 pp at a FIFO gain
       fraction <= 1 % / <= 5 % / > 5 %) plus H2 (a non-exit downward resize whose fill session is
       one of the last 10 December sessions is postponed to January). The control.
  L1   three-step ladder (survey arm E1): g = 0.5 / 0.75 / 1.0 on the conviction terciles
       m < 0.5 / 0.5 <= m < 1.5 / m >= 1.5.
  L2   two-step ladder (survey arm E2): g = 0.5 for m < 1.0, else 1.0.
  L3   continuous (survey arm E3): g = clip(m / 2, 0.5, 1.0).
  L4   momentum metric (survey arm E4): L1's three steps keyed on
       m20 = 20-session return / (vol20 / sqrt(252) * sqrt(20)), same 0.5 / 1.5 cuts.

  m    = (close / SMA50 - 1) / (vol20 / sqrt(252)) on the QQQ decision close, evaluated only
       while the early path is active. vol20 is the annualized QQQ close vol20 that the early
       path's own vol filter already uses (`early_components`). Every arm keeps R2's early-path
       state (entry/exit/E2-vote-override), K's execution layer and the E2 vote, so the early-leg
       weight g(m) is the only difference between arms.

  Survey-arm mapping: L1 = E1, L2 = E2, L3 = E3, L4 = E4 of docs/research/idea_survey_v3.md
  (renamed to avoid colliding with this harness's stage labels E1-E4 and its pure-E2 anchor label).

Stages (pre-registered before any non-control arm was simulated on real data):
  pre      pre-tax main window, base/calibrated x m = 1/2/4 plus the R-USD row (G1-G4, G6, G9).
  AT       after tax at $100k (gate capital) and $10k/$30k/$300k/$1M (report-only) on the three
           windows (2000-03-09 / 2010-01-01 / 2017-01-03 -> 2026-09-29, liquidated).
  B12      clean bootstrap, seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths, fixed
           2015-09..2026-09 calendar: G5 (pre-tax, both financing) and B1-recent (progressive) /
           B2 (flat 20 %) medians at $100k.
  OOS      the six ewma_band_v1 windows (^GSPC 1957-1999, M1-M5), pre-tax, calibrated, m = 2 (G7).
  OOSB     pooled out-of-sample block-20 bootstrap over M1-M5, 2,000 paths each (G8).
  R2ROLL   report-only: 21 rolling January after-tax starts 2001..2021 at $100k.
  EP       report-only: per-episode early-path P&L and leave-one-episode-out for the three big
           episodes (2001-10-31, 2002-11-04, 2023-01-20).
  PLAT     pre-registered plateau check: L1 and L4 re-run with the alternative cut-sets
           (0.25 / 1.0) and (1.0 / 2.0).
  E5       report-only: how often the ladder changes a fill decision on the main path.

Gates (pre-registered; every gate must pass). Versus the K control:
  G1 (m = 2, both fin.): CAGR >= C0, or Calmar >= 1.1 x C0 with CAGR >= C0 - 0.5 pp.
  G2 (both): max DD <= C0 + 1 pp.
  G3 (both): 2017+ CAGR >= C0 - 1 pp.
  G4: CAGR above C0 in at least 2 of 3 sub-periods (P1 2000-08, P2 2009-16, P3 2017-).
  G5 (both): block-20 bootstrap (seed 20261011, 5,000 ten-year paths) median of arm - C0 >= 0.
  G6 (both): G1 at m = 1.
  G7: six OOS windows, calibrated, m = 2: median dCAGR >= 0; dCAGR >= -0.5 pp and dDD <= +3 pp in
      at least 5 of 6; dDD never > +8 pp.
  G8: pooled OOS bootstrap over M1-M5 (2,000 paths each) median d >= 0.
  G9: turnover/yr <= 2 x C0 (a ladder trades inside the early path; the pre-registered limit is
      the two-times one, as for W1 in ewma_band_v1).
  A1 after tax at $100k: arm - C0 >= 0 in all three windows.
  A2 clean bootstrap at $100k: B1-recent and B2 median arm - C0 >= 0 and share > 0 >= 55 %.
  P1 plateau: the sign of the pre-tax base m = 2 dCAGR and of the after-tax 2000-window dCAGR
      (both versus C0) is unchanged under both alternative cut-sets (L1 and L4).

--harness reproduces the control's published K cells (pre-tax CAGR/max DD/turnover at m = 1/2 under
both financing models, the three after-tax windows at $100k, the B1/B2 medians, the six OOS
windows) from research/ladder_v1/K_ANCHORS.json -- copied from research/band_h2_combo_v1/RESULTS
.json on branch research/band_h2_combo_v1 -- and the R2 reference row from the same file, plus the
pure-E2 anchor series against e2_integration_v1 and R2's target series against `simulate_band`.

One engine serves every stage: `engine_run` is a stateful session loop over the Turkish after-tax
account (research/ladder_v1/tax_engine.py, a byte copy of research/band_h2_combo_v1/tax_engine.py
= research/gain_band_v3/tax_engine.py = research/tax_harvest_v2/tax_engine.py = the
r2_tbill_combo_v1 / vt_level_tax_v1 corrected engine) with the arm's target series and K's
execution layer. The rule's decision state (the indexed rule gain and its year accumulator) is
computed independently of the regime, so the pre-tax and after-tax runs follow the same trade path.
The label "R2" is the pre-K production rule (band off, no deferral) -- a reference row only.

Engine helpers (synthetic 2x, SMA/TSM/DON, vol, vt, metrics) come from
research/trend_robustness_v2/screen.py (its main() is never run); the production R2 signal,
`simulate_band` and the OOS loaders are the copies used by research/band_h2_combo_v1/screen.py.
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
# Every arm carries K's execution layer (the control IS production K); the only difference between
# the arms is the early-leg weight g(m), i.e. the target series itself.
K_LAYER = {"band": True, "usd_rule": True, "defer": True}
ARMS = ("C0", "L1", "L2", "L3", "L4")
ARMS_ALL = ARMS
CONTROL = "C0"
CANDIDATES = ("L1", "L2", "L3", "L4")
RULE = {a: dict(K_LAYER) for a in ARMS}
# Reference labels (never in ARMS): the pre-K production rule (band off, no deferral; the R2 row
# of the report and the pure-E2 anchor), and A1u/H2/K as single-layer labels used only by
# `smoke()` to assert the composed C0 layer against the two reference pre-tax simulators.
RULE["R2"] = {"band": False, "usd_rule": False, "defer": False}
RULE["E2"] = RULE["R2"]
RULE["A1u"] = {"band": True, "usd_rule": True, "defer": False}
RULE["H2"] = {"band": False, "usd_rule": False, "defer": True}
RULE["K"] = dict(K_LAYER)
B_UP = C0_BAND                                   # entries / exits / up-resizes keep 0.5 pp
GAIN_TIERS = ((0.01, 0.005), (0.05, 0.03), (math.inf, 0.08))  # g_frac ceiling -> B_down
DEC_TAIL = 10                                    # H2: the last 10 December sessions
YEAR_END = "12"                                  # month prefix of the December sessions

# ---- the early-leg conviction ladder (pre-registered; this study's only change)
LADDER_TERCILES = (0.5, 1.5)          # L1/L4 cut points on the conviction metric
LADDER_STEPS = (0.5, 0.75, 1.0)       # L1/L4 g by tercile (C0's constant early weight is 0.5)
LADDER_MID = 1.0                      # L2's two-step cut
LADDER_LINEAR_SCALE = 2.0             # L3: g = clip(m / 2, 0.5, 1.0)
LADDER_PLATEAU_CUTS = ((0.25, 1.0), (1.0, 2.0))   # the pre-registered plateau neighbours
LADDER_SHAPE = {"L1": ("steps", "m"), "L2": ("twostep", "m"), "L3": ("linear", "m"),
                "L4": ("steps", "m20")}
# Uniform early weight 0.5 under K's/R2's execution layer. "C0" is the control; "R2", "A1u", "H2"
# and "K" exist only as reference/identity labels (the report's pre-K row and the smoke checks).
LADDER_REF_LABELS = ("C0", "R2", "A1u", "H2", "K")

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
G1_MIN_CAGR_PP = 0.0         # m = 2 CAGR >= C0 (or the Calmar alternative), both financing
G1_CALMAR_MULT = 1.1         # ... or Calmar >= this x C0 ...
G1_CALMAR_CAGR_FLOOR_PP = -0.5   # ... with CAGR >= C0 - this
G2_MAX_DD_PP = 1.0           # max DD <= C0 + this (both financing)
G3_MIN_CAGR_PP = -1.0        # 2017+ CAGR >= C0 + this (both)
G4_MIN_SUBPERIODS = 2        # sub-periods with CAGR > C0
G5_MIN_MEDIAN_PP = 0.0       # block-20 bootstrap median arm - C0 (both)
G7_MIN_MEDIAN_PP = 0.0       # six OOS windows: median dCAGR >= this
G7_PER_WINDOW_CAGR_PP = -0.5 # per-window dCAGR floor
G7_PER_WINDOW_DD_PP = 3.0    # per-window dDD ceiling
G7_MAX_DD_PP = 8.0           # never breached
G7_MIN_WINDOWS = 5           # of 6
G8_MIN_MEDIAN_PP = 0.0       # pooled OOS bootstrap median arm - C0
G9_MAX_TURNOVER_MULT = 2.0   # turnover/yr <= this x C0
A1_MIN_WINDOW_PP = 0.0       # after tax at $100k: arm - C0 >= this in all three windows
A2_MIN_MEDIAN_PP = 0.0       # clean bootstrap $100k: median arm - C0 >= this (B1 and B2)
A2_MIN_SHARE = 0.55          # ... and share > 0 >= this
# ---- reporting stages
R2ROLL_MIN_SHARE = 0.70      # report-only threshold for the 21 rolling after-tax starts
EPISODE_ENTRIES = ("2001-10-31", "2002-11-04", "2023-01-20")   # the three big early episodes
# ---- published reference evidence (the control's own cells)
KREF = HERE / "K_ANCHORS.json"   # K and R2 cells from research/band_h2_combo_v1/RESULTS.json

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

# ---- instruments: none. This study's stages run on the QQQ main series and the six OOS
# indices only; the parent's 21-ETF E1 stage and its quality rule are not part of ladder_v1.

# ----------------------------------------------------------------------------- reproduction anchors
REF_E2 = {  # e2_integration_v1 DEVIATION.json, E2_prod_price_h0 (cagr, max_dd, turnover)
    ("base", 1): (0.1494912807457318, 0.4924710732832198, 5.968939533490137),
    ("base", 2): (0.14674909060373587, 0.4956428339322504, 5.968939533490137),
    ("calibrated", 1): (0.14377164501317918, 0.49333140001226616, 5.968913400107268),
    ("calibrated", 2): (0.14104311139315073, 0.49649783199459774, 5.968913400107268),
}
TAX_ANCHORS = {  # pure-E2 anchor series -> window -> (pre cagr, progressive cagr, prog max dd)
    "E2": {
        "2000_03_to_2026_09": (0.146715, 0.095340, 0.495643),
        "2010_01_to_end": (0.218852, 0.144968, 0.446920),
        "2017_01_to_end": (0.322564, 0.226932, 0.376052),
    },
}
BOOT_ANCHOR = {"progressive": {"E2": 0.03079}, "flat": {"E2": 0.035201}}
# The control's (K) and the pre-K incumbent's (R2) published cells, copied from
# research/band_h2_combo_v1/RESULTS.json on branch research/band_h2_combo_v1 -- the evidence file
# this study's --harness asserts against. Loaded, not hard-coded, so the manifest can pin it.
KANCH = json.loads(KREF.read_text())


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


# ----------------------------------------------------------------------------- the early-leg ladder
def sma_val(c, n):
    """The n-session SMA *level* at each close (NaN before the window is full)."""
    c = np.atleast_2d(np.asarray(c, dtype=float))
    cs = np.cumsum(c, axis=1)
    out = np.full_like(c, np.nan)
    out[:, n - 1:] = (cs[:, n - 1:] - np.concatenate([np.zeros((c.shape[0], 1)), cs[:, :-n]],
                                                     axis=1)) / n
    return out


def conviction_metric(price, comps=None, kind="m"):
    """The early-path conviction metric on the QQQ decision close.

    m   = (close / SMA50 - 1) / (vol20 / sqrt(252))            -- distance to SMA50 in daily sigmas
    m20 = (close / close[-20] - 1) / (vol20 / sqrt(252) * sqrt(20))  -- 20-session return in
          20-session sigmas
    vol20 is the annualized QQQ close vol20 the early path's own vol filter uses, so the metric is
    read off the same series as the entry condition and adds no new data. `idea_survey_v3` writes
    the m20 denominator as "vol20 . sqrt(20)"; taken literally with the annualized vol20 its scale
    would differ from m by a factor sqrt(252) and the pre-registered 0.5 / 1.5 cuts would be
    meaningless, so the scale-consistent reading (annualized vol20 / sqrt(252) = daily sigma) is
    the one implemented -- see PREREGISTRATION section 3.2."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    _sma50_up, vol_p, _med_p, _lag = early_components(price) if comps is None else comps
    daily = np.where(vol_p > 0, vol_p / math.sqrt(252.0), np.nan)
    if kind == "m":
        num = price / sma_val(price, SMA_FAST) - 1.0
    elif kind == "m20":
        lag = np.full_like(price, np.nan)
        lag[:, LAG_FAST:] = price[:, :-LAG_FAST]
        num = price / lag - 1.0
        daily = daily * math.sqrt(LAG_FAST)
    else:
        raise ValueError(f"unknown conviction metric {kind!r}")
    return num / daily


def ladder_g(m, shape, cuts=None):
    """The early-leg weight multiplier g(m), the study's only change to production K.

    shape "steps":   g = LADDER_STEPS[0 / 1 / 2] on m < cuts[0] / cuts[0] <= m < cuts[1] / above
    shape "twostep": g = LADDER_STEPS[0] for m < LADDER_MID, else LADDER_STEPS[2]
    shape "linear":  g = clip(m / LADDER_LINEAR_SCALE, LADDER_STEPS[0], LADDER_STEPS[2])
    `cuts` defaults to the pre-registered terciles; the plateau check passes the neighbours."""
    lo, hi = LADDER_TERCILES if cuts is None else cuts
    m = np.asarray(m, dtype=float)
    g0, g1, g2 = LADDER_STEPS
    if shape == "twostep":
        g = np.where(m < LADDER_MID, g0, g2)
    elif shape == "linear":
        g = np.clip(m / LADDER_LINEAR_SCALE, g0, g2)
    elif shape == "steps":
        g = np.where(m < lo, g0, np.where(m < hi, g1, g2))
    else:
        raise ValueError(f"unknown ladder shape {shape!r}")
    # an undefined metric must reach ladder_state's degeneracy rule for every shape (a bare
    # `np.where` comparison against NaN would quietly send `steps`/`twostep` to the top step)
    return np.where(np.isfinite(m), g, np.nan)


def ladder_state(price, e2_on, arm, comps=None, cuts=None, stats=None):
    """(on_eff, factor) of the arm's path: production R2's early-path state, with the early-leg
    weight replaced by the arm's ladder g(m) -- C0 keeps R2's constant 0.5, and every arm's
    E2-on state is 1.0 (E2's own vote overrides the early path, as in production). `stats`, when
    given, tallies the sessions where the degeneracy rule (below) fired."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    comps = early_components(price) if comps is None else comps
    on_eff, factor = r2_state(price, e2_on, comps)
    if arm in LADDER_REF_LABELS:
        return on_eff, factor
    if arm not in LADDER_SHAPE:
        raise ValueError(f"unknown ladder arm {arm!r}")
    shape, kind = LADDER_SHAPE[arm]
    g = ladder_g(conviction_metric(price, comps, kind), shape, cuts)
    # Degeneracy rule (pre-committed before any arm result existed; see PREREGISTRATION
    # "Amendments"): where the early path is active but the metric is not finite -- a zero-vol
    # stretch, which ^N225-style block resampling produces -- the arm falls back to the control's
    # 0.5 weight. 0.5 carries no conviction information, so this is neutral by construction and
    # can only damp a candidate's measured edge.
    early = (factor > 0.0) & (factor < 1.0)
    if stats is not None:
        key = f"{arm}_undefined_metric_sessions"
        stats[key] = stats.get(key, 0) + int(np.sum(early & ~np.isfinite(g)))
    g = np.where(np.isfinite(g), g, LADDER_STEPS[0])
    return on_eff, np.where(early, g, factor)


def ladder_wants(price, e2_on, vol_instr, arm, comps=None, cuts=None, stats=None):
    """Target weights [P, T] of one arm: production E2 + the arm's laddered early leg, sized
    min(1, 0.35/vol20) x factor, total cap 0.98 (`sized_wants`)."""
    on_eff, factor = ladder_state(price, e2_on, arm, comps, cuts, stats)
    return sized_wants(on_eff, factor, vol_instr)


def wants_for(price, e2_on, vol_instr, arms=ARMS, comps=None, cuts=None, stats=None):
    """{arm: target weights} for one price series; one early-path state loop per arm."""
    comps = early_components(price) if comps is None else comps
    return {a: ladder_wants(price, e2_on, vol_instr, a, comps, cuts, stats) for a in arms}


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
    """(days, synthetic QLD* open, close, pure-E2 wants, R2 wants, E2 vote, QQQ price, comps).

    The last two entries feed the ladder's metric; `comps` is passed on so one state loop serves
    every arm."""
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    price, e2, vol_instr = q[1], e2_up(q[1])[0], trv2.vol_n(c)[0]
    comps = early_components(price)
    w_e2 = arm_signals(price, e2, vol_instr, "E2", comps)[0][0]   # fidelity anchor only
    w_r2 = arm_signals(price, e2, vol_instr, "R2", comps)[0][0]   # production R2 = the control
    return days, o, c, w_e2, w_r2, e2, price, comps


def wants_of(price, e2, vol_instr, comps=None):
    """(production-E2 wants [P, T], production-R2 wants [P, T]) for one price series; one state
    loop for the pair. Index 0 is the harness fidelity anchor; index 1 is production R2's target
    series, which the control arm ("C0") is asserted to reproduce through `ladder_wants`."""
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
    Each arm is driven by its own target series (the ladder), all under K's execution layer."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)[0]
    comps = early_components(c)
    W = wants_for(c, e2, trv2.vol_n(sc)[0], arms, comps)
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


# ----------------------------------------------------------------------------- bootstrap E1 (fixed 2015-09..2026-09 calendar)
def boot(seed=SEED, block=BLOCK, arms=ARMS_ALL, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
         capitals=("100k",), modes=("progressive", "flat"),
         deltas=CANDIDATES, baselines=(CONTROL,), label="B12"):
    """Block bootstrap on the fixed 2015-09 -> 2026-09 calendar with its realised USD/TRY and
    Yİ-ÜFE; market returns (QQQ overnight/intraday/dividend + DTB3) resampled exactly as
    tax_asym_v2 / vt_level_tax_v1 / tax_harvest_v2 / gain_band_v3. Pre-tax at `n_pre` paths under
    both financing models; after-tax `modes` at the first `n_tax` paths. G5 = the pre-tax block,
    B1-recent = progressive at $100k, B2 = flat 20 % at $100k.

    `seed` and `block` are parameters (the pre-registered G5/B1/B2 = seed 20261011, block 20). The
    path draws depend only on (seed, block, n_paths); every arm runs its own ladder target series."""
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
    degeneracy = {}   # sessions where the ladder's metric was undefined (see ladder_state)
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
        # one early-path state loop per arm per path; the sizing differs per financing model only
        W = {arm: None for arm in arms}
        for arm in arms:
            on_eff, factor = ladder_state(pc, e2, arm, comps, stats=degeneracy)
            W[arm] = {"base": sized_wants(on_eff, factor, trv2.vol_n(sc)[0])[0],
                      "calibrated": sized_wants(on_eff, factor, trv2.vol_n(sc_c)[0])[0]}
        legs = {md: (so[0] if md == "base" else so_c[0], sc[0] if md == "base" else sc_c[0])
                for md in FIN}
        # every arm label is driven by its own ladder target series; only the fill rule is shared
        if pidx < n_pre:
            for md, (o_, c_) in legs.items():
                for arm in arms:
                    r = engine_run(PRE_CAPITAL, i0, i1, fixed_dates, mkt, o_, c_, W[arm][md], e2,
                                   arm, "pre_tax", m=M)
                    k = len(r["_nav"]) - 1
                    pre[(md, arm)].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
        if pidx < n_tax:
            o_, c_ = legs["base"]
            for cap_label in capitals:
                for mode in modes:
                    for arm in arms:
                        r = engine_run(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, o_, c_,
                                       W[arm]["base"], e2, arm, mode, m=M)
                        if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                            raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode} {cap_label}")
                        at[cap_label][mode][arm].append(r["cagr_usd_liquidated"])
        if (pidx + 1) % 500 == 0:
            print(f"  {label} path {pidx + 1}/{n_paths}", flush=True)

    out = {"n_paths_pre": n_pre, "n_paths_tax": n_tax, "seed": seed, "block": block,
           "arms": list(arms), "calendar_start": fixed_dates[0], "calendar_end": fixed_dates[-1],
           "degeneracy": {k: int(v) for k, v in sorted(degeneracy.items())},
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
    return out


# ----------------------------------------------------------------------------- bootstrap E2 (pooled OOS, M1-M5)
def boot_oos_market(key, rng, arms):
    """fast_reentry_v1.bootstrap_market draws (shared generator, markets in order), R-USD rule."""
    days, o, c, y, start = load_market(key)
    pool = np.arange(start, len(days))
    npool = len(pool)
    mkt = usd_market(days)
    acc = {a: [] for a in arms}
    degeneracy = {}
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
        W = wants_for(cc, e2, trv2.vol_n(sc), arms, stats=degeneracy)
        for arm in arms:
            for p in range(P):
                r = engine_run(1.0, START, cc.shape[1] - 1, days, mkt, so[p], sc[p], W[arm][p], e2[p],
                               arm, "pre_tax", m=M, rule_index=False)
                k = len(r["_nav"]) - 1
                acc[arm].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
    return ({a: np.array(v, dtype=float) for a, v in acc.items()},
            {k: int(v) for k, v in sorted(degeneracy.items())})


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
    """Whole rule path on synthetic random-walk prices; no market data touched.

    Checks, in order: the two rule-gain probes against the engine's own sell gain; H2 against the
    reference overlay simulator and K's composition against A1u; the fast rolling vol median; the
    ladder's own identities (C0's target series IS production R2's; every g inside [0.5, 1.0]; g
    monotone in the metric; the pre-registered step boundaries; the early-leg mask identical across
    arms; the E2-on state untouched at 1.0); R2 (band off) against `simulate_band` and C0 against
    R2 (the K layer must bite); that the band and the deferral fire; finiteness and after-tax <=
    pre-tax for every arm on its own series."""
    global DEC_TAIL
    n = 1600
    o, price, y = synthetic_bars(n, 7)
    days = _trading_days(n)
    so, sc = trv2.synthetic_2x(o, price, np.zeros(n), y, EXP_Q)
    e2 = e2_up(price)[0]
    v20 = trv2.vol_n(sc)[0]
    comps = early_components(price)
    w_e2, w_r2 = (w[0] for w in wants_of(price, e2, v20, comps))
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

    # 1c. H2 == the reference H2 simulator (tail 10); the overlay with an empty tail ==
    # `simulate_band`; K with an empty window == A1u (the composition adds exactly the deferral)
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
    checks["C0_rule_is_K"] = bool(RULE["C0"] == RULE["K"] == K_LAYER and RULE["R2"]["band"] is False)

    # 2. fast rolling vol median == the loop reference
    pv = trv2.vol_n(100 * np.cumprod(1 + rng.normal(0, 0.01, 1400)))
    checks["rolling_median_fast_equals_ref"] = bool(np.array_equal(
        _rolling_median_vol(pv), _rolling_median_vol_ref(pv), equal_nan=True))

    # 2b. the split state/sizing helpers == the reference arm_signals (R2 want series); the
    # control label "C0" has the *same target series* as "R2" (the v1 bug was the opposite) and
    # the ladder arms do not
    w_ref = arm_signals(price, e2, v20, "R2")[0][0]
    checks["r2_state_sizing_matches_arm_signals"] = bool(
        np.array_equal(w_ref, sized_wants(*r2_state(price, e2, comps), v20)[0]))
    checks["wants_of_matches_arm_signals"] = bool(
        np.array_equal(wants_of(price, e2, v20, comps)[1][0], w_ref))
    checks["control_C0_target_series_is_R2s"] = bool(
        np.array_equal(ladder_wants(price, e2, v20, "C0", comps)[0], w_ref)
        and np.array_equal(arm_signals(price, e2, v20, "C0")[0][0], w_ref)
        and np.array_equal(w_r2, w_ref))
    checks["E2_anchor_differs_from_R2_series"] = bool(
        not np.array_equal(arm_signals(price, e2, v20, "E2")[0][0], w_ref))
    checks["reference_labels_share_R2s_target_series"] = bool(all(
        np.array_equal(ladder_wants(price, e2, v20, lab, comps)[0], w_ref)
        for lab in ("R2", "A1u", "H2", "K")))

    # 2c. the ladder's own identities
    on_eff_c0, fac_c0 = ladder_state(price, e2, "C0", comps)
    early_mask = (fac_c0 > 0.0) & (fac_c0 < 1.0)
    on_mask = fac_c0 >= 1.0
    checks["early_path_is_not_empty"] = bool(np.sum(early_mask) > 20)
    ladders_ok = g_range_ok = mask_ok = on_ok = distinct = True
    gs = {}
    for arm in CANDIDATES:
        on_a, fac_a = ladder_state(price, e2, arm, comps)
        shape, kind = LADDER_SHAPE[arm]
        g = ladder_g(conviction_metric(price, comps, kind), shape)
        gs[arm] = g
        if not (np.all(fac_a[early_mask] >= LADDER_STEPS[0] - 1e-12)
                and np.all(fac_a[early_mask] <= LADDER_STEPS[-1] + 1e-12)):
            g_range_ok = False
        if not np.array_equal(on_a, on_eff_c0):
            mask_ok = False
        if not np.allclose(fac_a[on_mask], 1.0):
            on_ok = False
        if np.array_equal(fac_a, fac_c0):
            distinct = False
        # monotone non-decreasing in the metric on the active mask
        m_a = conviction_metric(price, comps, kind)
        idx = np.argsort(m_a[early_mask])
        gg = fac_a[early_mask][idx]
        if np.any(np.diff(gg) < -1e-12):
            ladders_ok = False
    checks["ladder_factor_in_range"] = bool(g_range_ok)
    checks["ladder_preserves_on_eff_and_E2_state"] = bool(mask_ok and on_ok)
    checks["ladder_arms_differ_from_C0"] = bool(distinct)
    checks["ladder_monotone_in_metric"] = bool(ladders_ok)
    checks["ladder_arms_differ_from_each_other"] = bool(
        len({ladder_wants(price, e2, v20, a, comps)[0].tobytes() for a in CANDIDATES}) == len(CANDIDATES))

    # 2d. the pre-registered boundaries, evaluated directly on `ladder_g`
    probe_m = np.array([[-1.0, 0.0, 0.49, 0.5, 0.51, 0.99, 1.0, 1.49, 1.5, 1.51, 5.0]])
    checks["ladder_steps_boundary_values"] = bool(
        np.allclose(ladder_g(probe_m, "steps"), [0.5, 0.5, 0.5, 0.75, 0.75, 0.75, 0.75, 0.75, 1.0, 1.0, 1.0]))
    checks["ladder_twostep_boundary_values"] = bool(
        np.allclose(ladder_g(probe_m, "twostep"), [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 1.0, 1.0, 1.0, 1.0, 1.0]))
    lin = ladder_g(probe_m, "linear")
    checks["ladder_linear_boundary_values"] = bool(
        np.allclose(lin, np.clip(probe_m / 2.0, 0.5, 1.0)) and abs(lin[0, 6] - 0.5) < 1e-12
        and abs(lin[0, 10] - 1.0) < 1e-12)
    checks["ladder_plateau_cuts_change_the_weight"] = bool(
        not np.allclose(ladder_g(probe_m, "steps"), ladder_g(probe_m, "steps", (0.25, 1.0))))
    # the degeneracy rule: `ladder_g` propagates a non-finite metric (so a zero-vol stretch
    # reaches the fallback) and the fallback is the production 0.5 weight
    g_nan = ladder_g(np.array([[np.nan, 0.2, 2.0]]), "linear")
    g_fb = np.where(np.isfinite(g_nan), g_nan, LADDER_STEPS[0])
    checks["undefined_metric_falls_back_to_the_control_weight"] = bool(
        all(not np.isfinite(ladder_g(np.array([[np.nan]]), sh)[0, 0])
            for sh in ("steps", "twostep", "linear"))
        and not np.isfinite(g_nan[0, 0]) and g_fb[0, 0] == LADDER_STEPS[0]
        and g_fb[0, 1] == LADDER_STEPS[0] and g_fb[0, 2] == 1.0)
    stats_probe = {}
    _onf, _facf = ladder_state(np.array([[np.nan, 1.0, 2.0]]), np.array([[True, False, False]]),
                               "L1", cuts=(0.5, 1.5), stats=stats_probe)
    checks["stats_tally_is_wired"] = bool(stats_probe == {} or
                                          "L1_undefined_metric_sessions" in stats_probe)
    lag20 = np.full_like(price, np.nan)
    lag20[LAG_FAST:] = price[:-LAG_FAST]
    m20_direct = (price / lag20 - 1.0) / (trv2.vol_n(price) / math.sqrt(252.0) * math.sqrt(LAG_FAST))
    checks["m20_matches_its_formula"] = bool(np.allclose(
        conviction_metric(price, comps, "m20"), m20_direct, equal_nan=True))
    m_direct = (price / sma_val(price, SMA_FAST) - 1.0) / (trv2.vol_n(price) / math.sqrt(252.0))
    checks["m_matches_its_formula"] = bool(np.allclose(
        conviction_metric(price, comps, "m"), m_direct, equal_nan=True))

    # 3. R2 (band off) engine == the reference simulate_band; the K layer must then bite on C0
    ref_nav, _, _ = simulate_band(so[None, :], sc[None, :], w_r2, COST_PER_SIDE * 2, START, C0_BAND)
    r2 = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_r2, e2, "R2", "pre_tax")
    checks["R2_engine_equals_simulate_band"] = bool(np.max(np.abs(r2["_nav"] - ref_nav[0])) < 1e-9)
    c0 = engine_run(1.0, START, n - 1, days, mkt, so, sc, w_r2, e2, "C0", "pre_tax")
    checks["C0_nav_differs_from_R2"] = bool(np.max(np.abs(c0["_nav"] - r2["_nav"])) > 1e-12)

    # 4. the mechanisms fire: a sustained synthetic uptrend must block at least one downsize and
    # defer at least one December fill
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
    for arm in ARMS + ("A1u", "H2", "R2"):
        rr = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u,
                        e2_u, arm, "pre_tax")
        blocked[arm] = {"band": rr["blocked_band"], "deferred": rr["deferred"],
                        "postponed": rr["postponements"]}
    checks["band_fires"] = bool(blocked["A1u"]["band"] > 0 and blocked["C0"]["band"] > 0)
    checks["R2_label_blocks_nothing"] = bool(blocked["R2"]["postponed"] == 0)
    checks["postponements_decompose"] = bool(all(
        blocked[a]["postponed"] == blocked[a]["band"] + blocked[a]["deferred"]
        for a in blocked))

    # 4c. the H2 mechanism on an oscillating target (0.98 / 0.49) == the reference overlay, and
    # never touches an exit to zero
    w_osc = np.where((np.arange(1500) // 60) % 2 == 0, 0.98, 0.49)
    dt10u = december_tail(days_u, START, 1499, DEC_TAIL)
    ref_u, _, _ = simulate_band_overlay(so_u[None, :], sc_u[None, :], w_osc[None, :],
                                        COST_PER_SIDE * 2, START, C0_BAND, dec_tail=dt10u)
    h2_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_osc, e2_u, "H2", "pre_tax")
    checks["H2_equals_reference_overlay_oscillating_target"] = bool(
        np.max(np.abs(h2_u["_nav"] - ref_u[0])) < 1e-9 and len(dt10u) > 0)
    checks["H2_defers_only_in_December"] = bool(
        h2_u["deferred"] == h2_u["postponements"] and h2_u["blocked_band"] == 0
        and len(h2_u["deferrals_by_year"]) > 0)
    k_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_osc, e2_u, "C0", "pre_tax")
    checks["deferral_fires"] = bool(h2_u["deferred"] > 0 and k_u["deferred"] > 0)

    # 5. finite, positive, and after tax <= pre-tax, for every arm on its own series; and (4b) on
    # this random-walk series the early path IS active, so the ladder must change fill decisions
    Wn = wants_for(price, e2, v20, ARMS)
    checks["ladder_target_series_differ_from_C0"] = bool(all(
        not np.array_equal(Wn[a], Wn["C0"]) for a in CANDIDATES))
    trc = {a: engine_run(1.0, START, n - 1, days, mkt, so, sc, Wn[a], e2, a, "pre_tax", trace=True)
           for a in ARMS}
    checks["ladder_changes_a_fill_decision"] = bool(all(
        np.sum(trc[a]["_dec"] != trc["C0"]["_dec"]) > 0 for a in CANDIDATES))
    res["decisions_differ_from_C0"] = {a: int(np.sum(trc[a]["_dec"] != trc["C0"]["_dec"]))
                                       for a in CANDIDATES}
    for arm in ARMS:
        rr = trc[arm]
        rp = engine_run(1.0, START, n - 1, days, mkt, so, sc, Wn[arm], e2, arm, "progressive")
        rf = engine_run(1.0, START, n - 1, days, mkt, so, sc, Wn[arm], e2, arm, "flat")
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


# ----------------------------------------------------------------------------- harness (control anchors)
def harness(run_boot=True):
    """Control reproduction on real data (no candidate arm).

    The study's control IS production K, so the label "C0" (K's target series, K's execution
    layer) is asserted against every published K cell in K_ANCHORS.json: pre-tax CAGR / max DD /
    turnover at m = 1 and m = 2 under both financing models, the three after-tax windows at
    $100k, the six OOS windows, and the clean-bootstrap medians (B1-recent, B2) at $100k. The
    pre-K production rule is asserted under its own label "R2" (band off, no deferral) against the
    R2 cells of the same file, and the pure-E2 anchor series under "E2" (e2_integration_v1 /
    vt_level_tax_v1). The engine's own mechanics are pinned by R2 == `simulate_band`, and the
    control's target series by `ladder_wants(C0) == w_r2` (asserted in --smoke).

    `run_boot` also runs the pre-registered clean bootstrap (seed 20261011, block 20, 5,000
    pre-tax / 1,000 after-tax paths) and returns it as `boot_ref` for `run()`."""
    checks, bad = {}, 0
    fails = []

    def add(label, got, ref, tol):
        nonlocal bad
        d = float(got) - float(ref)
        checks[label] = {"reference": float(ref), "reproduced": float(got), "diff": d,
                         "diff_pp": d * 100, "tolerance": tol, "pass": bool(abs(d) <= tol)}
        if not checks[label]["pass"]:
            bad += 1
            fails.append(label)

    days, o, c, w_e2, w_r2, e2, price, comps = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    K = KANCH["control_C0_K"]
    R2A = KANCH["reference_R2"]

    # ---- pre-tax: control vs K's published cells, R2 vs R2's
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_, p_, comps_ = main_inputs(spread)
        W = wants_for(p_, e_, trv2.vol_n(c_)[0], ARMS + ("R2",), comps_)
        for label, anchor in (("C0", K), ("R2", R2A)):
            for m in (1, 2):
                r = engine_run(PRE_CAPITAL, START, T, d_, mkt, o_, c_, W[label], e_, label,
                               "pre_tax", m=m)
                st = pre_stats(d_, START, T, r)
                ref = anchor["pre_tax"][md][f"m{m}"]
                add(f"{label}_{md}_m{m}_cagr", st["cagr"], ref["cagr"], 1e-4)
                add(f"{label}_{md}_m{m}_max_dd", st["max_dd"], ref["max_dd"], 1e-4)
                add(f"{label}_{md}_m{m}_turnover", st["turnover_annual"], ref["turnover_annual"], 1e-4)

    # ---- after-tax: control vs K's published windows, R2 vs R2's, E2 vs vt_level_tax_v1
    for wn, (a, b) in TAX_WINDOWS.items():
        for label, anchor in (("C0", K), ("R2", R2A)):
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, {"C0": w_r2, "R2": w_r2}[label],
                            e2, label, "progressive")
            ref = anchor["after_tax_100k"][wn]
            add(f"{label}_{wn}_after_tax_cagr", rq["cagr_usd_liquidated"], ref["after_tax_cagr"], 5e-5)
            add(f"{label}_{wn}_after_tax_max_dd", rq["max_dd_nav_usd"], ref["max_dd_after_tax"], 5e-5)
        rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, w_e2, e2, "E2", "progressive")
        add(f"E2_{wn}_after_tax_cagr", rq["cagr_usd_liquidated"], TAX_ANCHORS["E2"][wn][1], 5e-5)
        add(f"E2_{wn}_after_tax_max_dd", rq["max_dd_nav_usd"], TAX_ANCHORS["E2"][wn][2], 5e-5)

    # ---- pure-E2 anchor series (pre-tax) and the engine identity on the R2 series
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_, p_, comps_ = main_inputs(spread)
        for m in (1, 2):
            r = engine_run(1.0, START, T, d_, mkt, o_, c_, we_, e_, "E2", "pre_tax", m=m)
            add(f"E2_anchor_{md}_m{m}_cagr", r["cagr_usd_unliquidated"], REF_E2[(md, m)][0], 1e-4)
            add(f"E2_anchor_{md}_m{m}_max_dd", r["max_dd_nav_usd"], REF_E2[(md, m)][1], 1e-4)
    ref_nav, _, _ = simulate_band(o[None, :], c[None, :], w_r2[None, :], COST_PER_SIDE * 2, START, C0_BAND)
    r2p = engine_run(1.0, START, T, days, mkt, o, c, w_r2, e2, "R2", "pre_tax")
    add("R2_engine_equals_simulate_band", 0.0, -float(np.max(np.abs(r2p["_nav"] - ref_nav[0]))), 1e-9)
    c0p = engine_run(1.0, START, T, days, mkt, o, c, w_r2, e2, "C0", "pre_tax")
    checks["control_nav_differs_from_R2_nav"] = {
        "max_abs_nav_diff": float(np.max(np.abs(c0p["_nav"] - r2p["_nav"]))),
        "cagr_control_K": float(c0p["cagr_usd_unliquidated"]),
        "cagr_R2_reference": float(r2p["cagr_usd_unliquidated"])}
    if not checks["control_nav_differs_from_R2_nav"]["max_abs_nav_diff"] > 1e-6:
        bad += 1
        fails.append("control_nav_differs_from_R2_nav")
    e2p = engine_run(1.0, START, T, days, mkt, o, c, w_e2, e2, "E2", "pre_tax")
    checks["control_series_is_not_the_E2_anchor_series"] = {
        "max_abs_nav_diff": float(np.max(np.abs(c0p["_nav"] - e2p["_nav"])))}
    if not checks["control_series_is_not_the_E2_anchor_series"]["max_abs_nav_diff"] > 0.01:
        bad += 1
        fails.append("control_series_is_not_the_E2_anchor_series")

    # ---- the six OOS windows (control vs K's published cells), R-USD, calibrated, m = 2
    oos_checks = {}
    for key in OOS_WINDOWS:
        if key == "GSPC_1957_1999":
            gd, go, gc, gy, gstart = load_gspc()
            w = oos_window(gd, go, gc, gy, gstart, ("C0", "R2"))
        else:
            md_, mo_, mc_, my_, mstart_ = load_market(key)
            w = oos_window(md_, mo_, mc_, my_, mstart_, ("C0", "R2"))
        ref = K["oos"][key]
        add(f"oos_{key}_cagr", w["C0"]["cagr"], ref["cagr"], 1e-4)
        add(f"oos_{key}_max_dd", w["C0"]["max_dd"], ref["max_dd"], 1e-4)
        oos_checks[key] = {a: {"cagr": w[a]["cagr"], "max_dd": w[a]["max_dd"]} for a in ("C0", "R2")}

    boot_ref = None
    if run_boot:
        boot_ref = boot(seed=SEED, block=BLOCK, arms=ARMS + ("R2",), n_pre=PRE_BOOT_PATHS,
                        n_tax=TAX_BOOT_PATHS, capitals=("100k",), modes=("progressive", "flat"),
                        deltas=CANDIDATES + ("R2",), baselines=(CONTROL,), label="clean")
        for mode in ("progressive", "flat"):
            for label, anchor in (("C0", K), ("R2", R2A)):
                add(f"boot_{mode}_{label}_median", boot_ref["tax"]["100k"][mode][label]["cagr_median"],
                    anchor["bootstrap_clean"][mode]["cagr_median"], 1e-3)
            # the paired median delta pins the bootstrap draws, not just the marginals
            add(f"boot_{mode}_R2_vs_C0_median_delta_pp",
                boot_ref["tax"]["100k"][mode]["R2_vs_C0"]["median_delta_cagr_pp"],
                -KANCH["pair_check"][mode]["K_vs_C0_median_delta_pp"], 0.001)
    return {"reproduction_checks": checks, "n_outside_tolerance": bad, "fails": fails,
            "oos": oos_checks, "boot_ref": boot_ref, "pass": bool(bad == 0)}


# ----------------------------------------------------------------------------- the stages
def pre_tax_main(arms, cuts=None):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD main-window row."""
    days, o, c, w_e2, w_r2, e2, price, comps = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    usd_mkt = usd_market(days)
    labels = tuple(arms) + ("R2",) if "R2" not in arms else tuple(arms)
    pre, pre_usd = {}, {}
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_, p_, comps_ = main_inputs(spread)
        W = wants_for(p_, e_, trv2.vol_n(c_)[0], labels, comps_, cuts)
        pre[md], pre_usd[md] = {}, {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in labels:
                r = engine_run(PRE_CAPITAL, START, T, d_, mkt, o_, c_, W[arm], e_, arm, "pre_tax", m=m)
                pre[md][f"m{m}"][arm] = pre_stats(d_, START, T, r)
        for arm in labels:
            r = engine_run(PRE_CAPITAL, START, T, d_, usd_mkt, o_, c_, W[arm], e_, arm, "pre_tax", m=M,
                           rule_index=False)
            pre_usd[md][arm] = pre_stats(d_, START, T, r, real_days=d_)
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": T - START + 1,
                     "years": float(trv2.calendar_years(days[START - 1], days[-1]))}
    return pre, pre_usd


def after_tax_windows(capitals, arms, cuts=None):
    """After tax at the given capitals (progressive 2026 tariff, liquidated, base financing,
    m = 2) on the three TAX_WINDOWS, each arm on its own target series."""
    days, o, c, w_e2, w_r2, e2, price, comps = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    labels = tuple(arms) + ("R2",) if "R2" not in arms else tuple(arms)
    WANTS = wants_for(price, e2, trv2.vol_n(c)[0], labels, comps, cuts)
    win = {}
    for cap_label in capitals:
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            win[cap_label][wn] = {}
            for arm in labels:
                rq = engine_run(TAX_CAPITALS[cap_label], a, b, days, mkt, o, c, WANTS[arm], e2, arm,
                                "progressive")
                rp = engine_run(PRE_CAPITAL, a, b, days, mkt, o, c, WANTS[arm], e2, arm, "pre_tax")
                win[cap_label][wn][arm] = {
                    "pre_tax_cagr": rp["cagr_usd_liquidated"],
                    "after_tax_cagr": rq["cagr_usd_liquidated"],
                    "after_tax_max_dd": rq["max_dd_nav_usd"],
                    "terminal_usd": rq["terminal_usd_liquidated"],
                    "tax_usd": rq["tax_total_usd_at_payment_fx"],
                    "sells_per_year": rq["sells"] / rq["years"],
                    "trades_per_year": rq["strategy_trades"] / rq["years"],
                    "qld_sells_per_year": rq["qld_sells"] / rq["years"],
                    "postponements": rq["postponements"], "blocked_band": rq["blocked_band"],
                    "deferred": rq["deferred"], "years": float(rq["years"]),
                    "realised_gain_try_by_reason": rq["realised_gain_try_by_reason"],
                }
    return win


def cost_stress(arms, cuts=None):
    """m = 4 cost stress, after tax at $100k on the three windows (report-only)."""
    days, o, c, w_e2, w_r2, e2, price, comps = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    labels = tuple(arms) + ("R2",) if "R2" not in arms else tuple(arms)
    WANTS = wants_for(price, e2, trv2.vol_n(c)[0], labels, comps, cuts)
    cost = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        cost[wn] = {}
        for arm in labels:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, WANTS[arm], e2, arm,
                            "progressive", m=M_STRESS)
            rp = engine_run(PRE_CAPITAL, a, b, days, mkt, o, c, WANTS[arm], e2, arm, "pre_tax",
                            m=M_STRESS)
            cost[wn][arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                             "pre_tax_cagr": rp["cagr_usd_liquidated"],
                             "max_dd_after_tax": rq["max_dd_nav_usd"],
                             "turnover_annual": rp["turnover_annual"]}
    return cost


def oos_stages(arms, cuts=None):
    """Six OOS windows (^GSPC 1957-1999 + M1-M5), pre-tax, calibrated, m = 2, R-USD rule."""
    labels = tuple(arms) + ("R2",) if "R2" not in arms else tuple(arms)
    oos = {}
    gd, go, gc, gy, gstart = load_gspc()
    oos["GSPC_1957_1999"] = oos_window(gd, go, gc, gy, gstart, labels)
    for key in ("M1", "M2", "M3", "M4", "M5"):
        md_, mo_, mc_, my_, mstart_ = load_market(key)
        oos[key] = oos_window(md_, mo_, mc_, my_, mstart_, labels)
    for wn in OOS_WINDOWS:
        for arm in labels:
            if arm == CONTROL:
                continue
            oos[wn][arm]["delta_cagr_pp"] = (oos[wn][arm]["cagr"] - oos[wn][CONTROL]["cagr"]) * 100
            oos[wn][arm]["delta_max_dd_pp"] = (oos[wn][arm]["max_dd"] - oos[wn][CONTROL]["max_dd"]) * 100
    return oos


def oos_bootstrap(arms):
    """Pooled OOS bootstrap (G8): block-20 over M1-M5, 2,000 paths each, rng seed SEED."""
    labels = tuple(arms) + ("R2",) if "R2" not in arms else tuple(arms)
    rng = np.random.default_rng(SEED)
    raw = {k: boot_oos_market(k, rng, labels) for k in MARKETS}
    per = {k: v[0] for k, v in raw.items()}
    degeneracy = {k: v[1] for k, v in raw.items()}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in labels}
    out = {"per_market": {}, "pooled": {}, "degeneracy": degeneracy}
    for k in MARKETS:
        out["per_market"][k] = {a: {"median_diff_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)}
                                for a in labels}
    for a in labels:
        d = pooled[a] - pooled[CONTROL]
        out["pooled"][a] = {"diff_vs_C0_median_pp": float(np.median(d) * 100),
                            "diff_vs_C0_mean_pp": float(np.mean(d) * 100),
                            "share_gt_0": float(np.mean(d > 0)), "paths": int(len(d))}
    return out


def stage_r2roll(arms, cuts=None):
    """Report-only: 21 rolling January after-tax starts 2001..2021 on the QQQ main series, $100k,
    base financing, m = 2, each run to 2026-09-29."""
    days, o, c, w_e2, w_r2, e2, price, comps = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    labels = tuple(arms) + ("R2",) if "R2" not in arms else tuple(arms)
    WANTS = wants_for(price, e2, trv2.vol_n(c)[0], labels, comps, cuts)
    out = {}
    for yr in range(2001, 2022):
        i0 = bisect.bisect_left(days, f"{yr}-01-01")
        rec = {"start": days[i0], "index": int(i0)}
        for arm in labels:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], i0, T, days, mkt, o, c, WANTS[arm], e2, arm,
                            "progressive")
            rec[arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                        "max_dd_after_tax": rq["max_dd_nav_usd"],
                        "sells_per_year": rq["sells"] / rq["years"]}
        for arm in labels:
            if arm == CONTROL:
                continue
            rec[arm]["delta_after_tax_pp"] = (rec[arm]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
        out[str(yr)] = rec
    return out


def stage_e5_decisions(arms, cuts=None):
    """Report-only: how often the arms' fill decisions differ from the control's on the main path
    (base financing, m = 2, $100k, R-INDEX, pre-tax so no tax-funding sales interfere).
    Decision code: 0 = no trade, 1 = up/entry/exit trade, 2 = downward resize."""
    days, o, c, w_e2, w_r2, e2, price, comps = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    labels = tuple(arms) + ("R2",) if "R2" not in arms else tuple(arms)
    WANTS = wants_for(price, e2, trv2.vol_n(c)[0], labels, comps, cuts)
    traces = {arm: engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, WANTS[arm], e2, arm,
                              "pre_tax", trace=True) for arm in labels}
    out = {"window": {"start": days[START], "end": days[-1],
                      "sessions": int(len(traces[CONTROL]["_dec"]))},
           "sells": {}, "postponements": {}}
    for arm in labels:
        r = traces[arm]
        out["sells"][arm] = int(np.sum(r["_dec"] == 2))
        out["postponements"][arm] = {"band": int(r["blocked_band"]), "deferred": int(r["deferred"])}
    for a in labels:
        if a == CONTROL:
            continue
        da, db = traces[a]["_dec"], traces[CONTROL]["_dec"]
        out[f"{a}_vs_{CONTROL}"] = {
            "sessions_decision_differs": int(np.sum(da != db)),
            "share_decision_differs": float(np.mean(da != db)),
            f"{a}_sells_C0_holds": int(np.sum((da == 2) & (db != 2))),
            f"{a}_holds_C0_sells": int(np.sum((da != 2) & (db == 2)))}
    return out


# ----------------------------------------------------------------------------- the early episodes
def early_episode_spans(factor):
    """Maximal runs of the early path being held (0 < factor < 1), extended by one session so the
    run carries its reconciliation fill. Arm-independent: every arm shares the early-path state."""
    held = ((factor[0] > 0.0) & (factor[0] < 1.0)).astype(np.int8)
    spans, i, T = [], 0, len(held)
    while i < T:
        if held[i]:
            j = i
            while j + 1 < T and held[j + 1]:
                j += 1
            spans.append((max(0, i), min(T - 1, j + 1)))
            i = j + 1
        else:
            i += 1
    return spans


def stage_episodes(c0_cagr, arms, cuts=None):
    """Report-only. Per-episode P&L of the early path for every arm (base financing, m = 2), and
    leave-one-episode-out for the three big episodes: the arm's ladder is replaced by the control's
    early-leg weight (0.5) inside the episode, so the remaining delta is the arm's edge without
    that episode's decisions. The episode span is the control's early-path run (plus its
    reconciliation session), arm-independent."""
    days, o, c, w_e2, w_r2, e2, price, comps = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    labels = tuple(arms) + ("R2",) if "R2" not in arms else tuple(arms)
    W = wants_for(price, e2, trv2.vol_n(c)[0], labels, comps, cuts)
    _on, fac_c0 = ladder_state(price, e2, CONTROL, comps)
    spans = early_episode_spans(fac_c0)
    navs = {a: engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, W[a], e2, a, "pre_tax")["_nav"]
            for a in labels}
    out = {"n_episodes": len(spans), "episodes": []}
    tot = {a: 0.0 for a in CANDIDATES}
    for (s, e) in spans:
        k0, k1 = s + 1 - START, e + 1 - START     # _nav index offset (nav starts at START-1)
        rec = {"entry_decision": days[s], "span_start": days[s], "span_end": days[e],
               "sessions": int(e - s + 1)}
        c0r = navs[CONTROL][k1] / navs[CONTROL][k0] - 1.0
        rec["C0_return_pct"] = float(c0r * 100)
        for a in CANDIDATES:
            ra = navs[a][k1] / navs[a][k0] - 1.0
            rec[f"{a}_return_pct"] = float(ra * 100)
            rec[f"{a}_delta_pp"] = float((ra - c0r) * 100)
            tot[a] += (ra - c0r) * 100
        out["episodes"].append(rec)
    out["sum_delta_pp"] = {a: float(tot[a]) for a in CANDIDATES}
    out["largest_episodes"] = [r["entry_decision"] for r in
                               sorted(out["episodes"], key=lambda r: -abs(r[f"{CANDIDATES[0]}_delta_pp"]))[:6]]
    # leave-one-episode-out on the three pre-registered episodes
    loo = {}
    for tag in EPISODE_ENTRIES:
        span = next((r for r in out["episodes"] if r["entry_decision"] == tag), None)
        if span is None:
            loo[tag] = {"found": False}
            continue
        s = days.index(span["span_start"]); e = days.index(span["span_end"])
        loo[tag] = {"found": True, "span": [days[s], days[e]]}
        for a in CANDIDATES:
            W2 = W[a].copy()
            W2[:, s:e + 1] = w_r2[:, s:e + 1]     # the arm's early leg == the control's there
            r2_ = engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, W2, e2, "R2", "pre_tax")
            st = pre_stats(days, START, T, r2_)
            d = (st["cagr"] - c0_cagr) * 100
            loo[tag][a] = {"cagr": float(st["cagr"]), "delta_cagr_vs_C0_pp": float(d)}
    out["leave_one_out"] = loo
    return out


def stage_plateau(pre_tax, windows, arms=("L1", "L4")):
    """Report-only cells of the pre-registered plateau check, reusing the pre-registered-case
    numbers already computed. Every alternative cut-set is run through the same two gated cells:
    the pre-tax base m = 2 CAGR and the after-tax 2000-window CAGR at $100k, both versus C0."""
    days, o, c, w_e2, w_r2, e2, price, comps = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    a, b = TAX_WINDOWS["2000_03_to_2026_09"]
    out = {"pre_registered": {}, "alt": {}}
    for arm in arms:
        out["pre_registered"][arm] = {
            "pre_tax_base_m2_delta_pp": float((pre_tax["base"]["m2"][arm]["cagr"]
                                               - pre_tax["base"]["m2"][CONTROL]["cagr"]) * 100),
            "after_tax_2000_delta_pp": float((windows[GATE_CAPITAL]["2000_03_to_2026_09"][arm]["after_tax_cagr"]
                                              - windows[GATE_CAPITAL]["2000_03_to_2026_09"][CONTROL]["after_tax_cagr"]) * 100)}
    for tag, cuts in ((f"cuts_{lo}_{hi}", (lo, hi)) for lo, hi in LADDER_PLATEAU_CUTS):
        W = wants_for(price, e2, trv2.vol_n(c)[0], (CONTROL,) + tuple(arms), comps, cuts)
        out["alt"][tag] = {}
        for arm in arms:
            r = engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, W[arm], e2, arm, "pre_tax", m=M)
            st = pre_stats(days, START, T, r)
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, W[arm], e2, arm,
                            "progressive")
            out["alt"][tag][arm] = {
                "pre_tax_base_m2_delta_pp": float((st["cagr"] - pre_tax["base"]["m2"][CONTROL]["cagr"]) * 100),
                "after_tax_2000_delta_pp": float((rq["cagr_usd_liquidated"]
                                                  - windows[GATE_CAPITAL]["2000_03_to_2026_09"][CONTROL]["after_tax_cagr"]) * 100)}
    return out


def plateau_ok(plateau, arms=("L1", "L4"), eps_pp=0.01):
    """P1: for each arm and each alternative cut-set, neither gated delta may change sign (a delta
    inside +/-eps_pp counts as zero and cannot flip)."""
    res = {}
    for arm in arms:
        base_pre = plateau["pre_registered"][arm]["pre_tax_base_m2_delta_pp"]
        base_at = plateau["pre_registered"][arm]["after_tax_2000_delta_pp"]
        ok, detail = True, {}
        for tag in plateau["alt"]:
            v = plateau["alt"][tag][arm]
            for key, base in (("pre_tax_base_m2_delta_pp", base_pre),
                              ("after_tax_2000_delta_pp", base_at)):
                alt = v[key]
                def sgn(x):
                    return 0 if abs(x) < eps_pp else (1 if x > 0 else -1)
                flip = sgn(base) != 0 and sgn(alt) != 0 and sgn(base) != sgn(alt)
                detail[f"{tag}_{key}"] = {"base_pp": float(base), "alt_pp": float(alt), "flip": bool(flip)}
                if flip:
                    ok = False
        res[arm] = {"pass": bool(ok), "detail": detail}
    return res


# ----------------------------------------------------------------------------- gates (this study)
def gates_ladder(res, boot_ref, oosb, plateau):
    """G1-G9 pre-tax versus the K control, the after-tax gates A1/A2 and the plateau gate P1.
    Every gate must pass. See PREREIGISTRATION section 5 / the module docstring."""
    pre, win = res["pre"], res["windows"][GATE_CAPITAL]
    g = {}
    for arm in CANDIDATES:
        r = {}
        # G1 (m = 2, both financing) and G6 (G1 at m = 1)
        for tag, mkey in (("G1", "m2"), ("G6", "m1")):
            ok = True
            detail = {}
            for md in ("base", "calibrated"):
                c0, a_ = pre[md][mkey][CONTROL], pre[md][mkey][arm]
                alt = bool(a_["calmar"] >= G1_CALMAR_MULT * c0["calmar"]
                           and a_["cagr"] >= c0["cagr"] + G1_CALMAR_CAGR_FLOOR_PP)
                ok &= bool(a_["cagr"] >= c0["cagr"]) or alt
                detail[md] = {"cagr": a_["cagr"], "cagr_C0": c0["cagr"], "delta_pp": (a_["cagr"] - c0["cagr"]) * 100,
                              "calmar": a_["calmar"], "calmar_C0": c0["calmar"], "calmar_alt": alt,
                              "pass": bool(a_["cagr"] >= c0["cagr"]) or alt}
            r[tag] = bool(ok)
            r[f"{tag}_detail"] = detail
        # G2 (both)
        r["G2_detail"] = {md: {"max_dd": pre[md]["m2"][arm]["max_dd"],
                               "max_dd_C0": pre[md]["m2"][CONTROL]["max_dd"],
                               "delta_pp": (pre[md]["m2"][arm]["max_dd"] - pre[md]["m2"][CONTROL]["max_dd"]) * 100,
                               "pass": bool(pre[md]["m2"][arm]["max_dd"]
                                            <= pre[md]["m2"][CONTROL]["max_dd"] + G2_MAX_DD_PP)}
                           for md in ("base", "calibrated")}
        r["G2"] = bool(all(v["pass"] for v in r["G2_detail"].values()))
        # G3 (both)
        r["G3_detail"] = {md: {"cagr_2017": pre[md]["m2"][arm]["2017_end"]["cagr"],
                               "cagr_2017_C0": pre[md]["m2"][CONTROL]["2017_end"]["cagr"],
                               "delta_pp": (pre[md]["m2"][arm]["2017_end"]["cagr"]
                                            - pre[md]["m2"][CONTROL]["2017_end"]["cagr"]) * 100,
                               "pass": bool(pre[md]["m2"][arm]["2017_end"]["cagr"]
                                            >= pre[md]["m2"][CONTROL]["2017_end"]["cagr"] + G3_MIN_CAGR_PP)}
                           for md in ("base", "calibrated")}
        r["G3"] = bool(all(v["pass"] for v in r["G3_detail"].values()))
        # G4 (base, m = 2)
        subs = {k: {"cagr": pre["base"]["m2"][arm]["subperiods"][k]["cagr"],
                    "cagr_C0": pre["base"]["m2"][CONTROL]["subperiods"][k]["cagr"]}
                for k in ("P1", "P2", "P3")}
        n_above = int(sum(1 for k in subs if subs[k]["cagr"] > subs[k]["cagr_C0"]))
        r["G4_detail"] = {"subperiods": subs, "n_above": n_above}
        r["G4"] = bool(n_above >= G4_MIN_SUBPERIODS)
        # G5 (both): pre-tax clean bootstrap median delta >= 0
        r["G5_detail"] = {}
        for md in ("base", "calibrated"):
            b = boot_ref["pre"][md]
            r["G5_detail"][md] = {"median_delta_pp": b[f"{arm}_vs_{CONTROL}"]["diff_vs_C0_median_pp"],
                                  "share_gt_0": b[f"{arm}_vs_{CONTROL}"]["share_gt_0"],
                                  "pass": bool(b[f"{arm}_vs_{CONTROL}"]["diff_vs_C0_median_pp"] >= G5_MIN_MEDIAN_PP)}
        r["G5"] = bool(all(v["pass"] for v in r["G5_detail"].values()))
        # G7: six OOS windows
        det = {k: {"delta_cagr_pp": res["oos"][k][arm]["delta_cagr_pp"],
                   "delta_max_dd_pp": res["oos"][k][arm]["delta_max_dd_pp"]} for k in OOS_WINDOWS}
        med = float(np.median([det[k]["delta_cagr_pp"] for k in OOS_WINDOWS]))
        n_ok = int(sum(1 for k in OOS_WINDOWS if det[k]["delta_cagr_pp"] >= G7_PER_WINDOW_CAGR_PP
                       and det[k]["delta_max_dd_pp"] <= G7_PER_WINDOW_DD_PP))
        worst_dd = max(det[k]["delta_max_dd_pp"] for k in OOS_WINDOWS)
        r["G7_detail"] = {"windows": det, "median_delta_cagr_pp": med, "n_windows_ok": n_ok,
                          "worst_delta_dd_pp": float(worst_dd)}
        r["G7"] = bool(med >= G7_MIN_MEDIAN_PP and n_ok >= G7_MIN_WINDOWS and worst_dd <= G7_MAX_DD_PP)
        # G8: pooled OOS bootstrap
        r["G8_detail"] = oosb["pooled"][arm]
        r["G8"] = bool(oosb["pooled"][arm]["diff_vs_C0_median_pp"] >= G8_MIN_MEDIAN_PP)
        # G9: turnover <= 2 x C0
        t_arm, t_c0 = pre["base"]["m2"][arm]["turnover_annual"], pre["base"]["m2"][CONTROL]["turnover_annual"]
        r["G9_detail"] = {"turnover": t_arm, "turnover_C0": t_c0, "ratio": t_arm / t_c0}
        r["G9"] = bool(t_arm <= G9_MAX_TURNOVER_MULT * t_c0)
        # A1: after tax at $100k, all three windows
        r["A1_detail"] = {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                               "after_tax_cagr_C0": win[wn][CONTROL]["after_tax_cagr"],
                               "delta_pp": (win[wn][arm]["after_tax_cagr"] - win[wn][CONTROL]["after_tax_cagr"]) * 100}
                          for wn in TAX_WINDOWS}
        r["A1"] = bool(all(v["delta_pp"] >= A1_MIN_WINDOW_PP for v in r["A1_detail"].values()))
        # A2: clean bootstrap B1-recent and B2
        r["A2_detail"] = {}
        for mode in ("progressive", "flat"):
            c = boot_ref["tax"][GATE_CAPITAL][mode]
            d = c[f"{arm}_vs_{CONTROL}"]
            r["A2_detail"][mode] = {"median_delta_pp": d["median_delta_cagr_pp"],
                                    "share_gt_0": d["share_gt_0"],
                                    "pass": bool(d["median_delta_cagr_pp"] >= A2_MIN_MEDIAN_PP
                                                 and d["share_gt_0"] >= A2_MIN_SHARE)}
        r["A2"] = bool(all(v["pass"] for v in r["A2_detail"].values()))
        # P1: the plateau check
        r["P1_detail"] = plateau[arm]
        r["P1"] = bool(plateau[arm]["pass"])
        r["pass"] = bool(all(r[k] for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9",
                                            "A1", "A2", "P1")))
        r["failed"] = [k for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "A1", "A2", "P1")
                       if not r[k]]
        g[arm] = r
    # ---- selection: the passing candidate with the highest base m = 2 CAGR
    passing = [a for a in CANDIDATES if g[a]["pass"]]
    order = sorted(passing, key=lambda a: -res["pre"]["base"]["m2"][a]["cagr"])
    adopted = order[0] if order else CONTROL
    reason = ("best base m = 2 CAGR among the passing arms" if order
              else "no candidate passed every gate; production K stands")
    return {"arms": g, "gate": {"G1": list(CANDIDATES), "G2": list(CANDIDATES), "G3": list(CANDIDATES),
                                "G4": list(CANDIDATES), "G5": list(CANDIDATES), "G6": list(CANDIDATES),
                                "G7": list(CANDIDATES), "G8": list(CANDIDATES), "G9": list(CANDIDATES),
                                "A1": list(CANDIDATES), "A2": list(CANDIDATES), "P1": list(CANDIDATES)},
            "adopted": adopted, "reason": reason,
            "control_is": "production K"}


# ----------------------------------------------------------------------------- the run
def run(boot_ref=None) -> dict:
    print("pre-tax main window (5 arms + the R2 reference row)", flush=True)
    pre, pre_usd = pre_tax_main(ARMS)
    print("after-tax windows (5 capitals)", flush=True)
    win = after_tax_windows(tuple(TAX_CAPITALS), ARMS)
    print("cost stress m = 4", flush=True)
    cost = cost_stress(ARMS)
    print("OOS windows", flush=True)
    oos = oos_stages(ARMS)
    print("OOS bootstrap (M1-M5, 2,000 paths each)", flush=True)
    oosb = oos_bootstrap(ARMS)
    if boot_ref is None:
        print(f"clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)", flush=True)
        boot_ref = boot(seed=SEED, block=BLOCK, arms=ARMS + ("R2",), n_pre=PRE_BOOT_PATHS,
                        n_tax=TAX_BOOT_PATHS, capitals=("100k",), modes=("progressive", "flat"),
                        deltas=CANDIDATES + ("R2",), baselines=(CONTROL,), label="clean")
    print("R2 rolling January starts (report-only)", flush=True)
    r2roll = stage_r2roll(ARMS)
    print("early episodes + leave-one-episode-out", flush=True)
    ep = stage_episodes(pre["base"]["m2"][CONTROL]["cagr"], ARMS)
    print("plateau check", flush=True)
    plat = stage_plateau(pre, win)
    print("E5 decision traces", flush=True)
    e5 = stage_e5_decisions(ARMS)
    res = {"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost, "oos": oos, "oosb": oosb,
           "boot_ref": boot_ref, "r2roll": r2roll, "episodes": ep, "plateau": plat, "e5": e5}
    res["gates"] = gates_ladder(res, boot_ref, oosb, plateau_ok(plat))
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

    hs = harness(run_boot=True)
    for k in sorted(hs["reproduction_checks"]):
        v = hs["reproduction_checks"][k]
        if "reference" not in v:
            print(f"harness {k}: {v}")
            continue
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} "
              f"diff {v['diff']:+.6f} {'ok' if v['pass'] else 'FAIL'}")
    print(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)")
    if mode == "--harness" or not hs["pass"]:
        sys.exit(0 if hs["pass"] else 1)

    res = run(boot_ref=hs["boot_ref"])
    g = res["gates"]["arms"]
    pre, win, oos, oosb, boot_ref = res["pre"], res["windows"], res["oos"], res["oosb"], res["boot_ref"]

    out = {
        "campaign": "ladder_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "candidates": list(CANDIDATES),
            "ladder": {"metric_m": "(close/SMA50 - 1) / (vol20/sqrt(252)) on the QQQ close",
                       "metric_m20": "(close/close[-20] - 1) / (vol20/sqrt(252) * sqrt(20))",
                       "terciles": list(LADDER_TERCILES), "steps": list(LADDER_STEPS),
                       "two_step_cut": LADDER_MID, "linear_scale": LADDER_LINEAR_SCALE,
                       "plateau_cuts": [list(c) for c in LADDER_PLATEAU_CUTS],
                       "shape": {k: list(v) for k, v in LADDER_SHAPE.items()},
                       "survey_arm_names": {"L1": "E1", "L2": "E2", "L3": "E3", "L4": "E4"}},
            "execution_layer": "K for every arm (A1u gain-scaled downsize band + H2 December deferral)",
            "rule": RULE, "dec_tail": DEC_TAIL,
            "gain_tiers": [[t, b] for t, b in GAIN_TIERS], "b_up": B_UP,
            "signals": "production R2 (equity_leveraged_trend_v1 1.2.0): E2 2-of-3 on price closes "
                       "(SMA200, 252-session price return > 0, close > 126-session max/min midpoint) "
                       "+ the early re-entry path while E2 is off (close > SMA50, vol20 < its "
                       "252-session median, close > close 20 sessions ago; exit below SMA50) sized "
                       "g(m) x vt instead of the production 0.5 x vt",
            "sizing": "vt = min(1, 0.35/vol20) on synthetic QLD*, cap 0.98",
            "instrument": "synthetic QLD* daily-reset 2x on QQQ total return, expense 0.95%/yr, "
                          "financing DTB3 + {base 0.0, calibrated 0.75%/yr}",
            "cost_per_side": COST_PER_SIDE, "cost_stress_m": M_STRESS,
            "main_window": pre["window"], "start": START,
            "after_tax": {"engine": "tax_engine.py (byte copy of band_h2_combo_v1/tax_engine.py)",
                          "financing": "base", "m": M, "windows": TAX_WINDOWS,
                          "capitals": TAX_CAPITALS, "gate_capital": GATE_CAPITAL,
                          "clean_bootstrap": {"paths_pre": PRE_BOOT_PATHS, "paths_tax": TAX_BOOT_PATHS,
                                              "seed": SEED, "block": BLOCK,
                                              "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI}},
            "oos_bootstrap": {"paths_per_market": OOS_PATHS_PER_MARKET, "markets": list(MARKETS),
                              "block": BLOCK, "seed": SEED},
            "gates": {"G1_MIN_CAGR_PP": G1_MIN_CAGR_PP, "G1_CALMAR_MULT": G1_CALMAR_MULT,
                      "G2_MAX_DD_PP": G2_MAX_DD_PP, "G3_MIN_CAGR_PP": G3_MIN_CAGR_PP,
                      "G4_MIN_SUBPERIODS": G4_MIN_SUBPERIODS, "G5_MIN_MEDIAN_PP": G5_MIN_MEDIAN_PP,
                      "G7": {"min_median_pp": G7_MIN_MEDIAN_PP, "per_window_cagr_pp": G7_PER_WINDOW_CAGR_PP,
                             "per_window_dd_pp": G7_PER_WINDOW_DD_PP, "max_dd_pp": G7_MAX_DD_PP,
                             "min_windows": G7_MIN_WINDOWS},
                      "G8_MIN_MEDIAN_PP": G8_MIN_MEDIAN_PP,
                      "G9_MAX_TURNOVER_MULT": G9_MAX_TURNOVER_MULT,
                      "A1_MIN_WINDOW_PP": A1_MIN_WINDOW_PP, "A2_MIN_MEDIAN_PP": A2_MIN_MEDIAN_PP,
                      "A2_MIN_SHARE": A2_MIN_SHARE},
            "adopted": res["gates"]["adopted"], "reason": res["gates"]["reason"],
        },
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "fails": hs["fails"], "reproduction_checks": hs["reproduction_checks"],
                    "oos": hs["oos"]},
        "pre_tax_main": pre,
        "pre_tax_main_r_usd": pre_usd,
        "after_tax_windows": win,
        "cost_stress_m4": cost,
        "oos": oos,
        "oos_bootstrap": oosb,
        "bootstrap_clean_seed20261011": boot_ref,
        "R2_rolling_starts": r2roll,
        "episodes": ep,
        "plateau": res["plateau"],
        "E5": res["e5"],
        "gates": res["gates"],
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = fmt_pct
    w = pre["window"]
    hdr = (f"ladder_v1 | early-leg conviction ladder on production K | main window "
           f"{w['start']} -> {w['end']} ({w['sessions']} sessions) | control = production K "
           f"(A1u + H2) | arms = C0 + L1 + L2 + L3 + L4 (C0 + survey E1-E4) | R2 = pre-K reference")
    L = [hdr,
         f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)"
         f"{'' if hs['pass'] else ': ' + ', '.join(hs['fails'])}"
         f" | control C0 == published K cells | R2 reference row == published R2 cells",
         f"ADOPTED: {res['gates']['adopted']} -- {res['gates']['reason']}", "",
         "Pre-tax main window (m = 2 unless stated; R2 = pre-K production rule, reference only):",
         f"{'arm':<5}{'CAGR base':>11}{'CAGR cal':>10}{'maxDD base':>12}{'maxDD cal':>11}"
         f"{'Calmar':>8}{'2017+':>9}{'P1/P2/P3':>26}{'m=1':>8}{'turn':>7}{'boot d base':>13}{'boot d cal':>12}"]
    for arm in list(ARMS) + ["R2"]:
        b, cc = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        sub = " / ".join(f"{b['subperiods'][k]['cagr'] * 100:.2f}" for k in ("P1", "P2", "P3"))
        g5b = (boot_ref["pre"]["base"][f"{arm}_vs_{CONTROL}"]["diff_vs_C0_median_pp"] if arm != CONTROL else 0.0)
        g5c = (boot_ref["pre"]["calibrated"][f"{arm}_vs_{CONTROL}"]["diff_vs_C0_median_pp"] if arm != CONTROL else 0.0)
        L.append(f"{arm:<5}{p(b['cagr']):>11}{p(cc['cagr']):>10}{p(b['max_dd']):>12}{p(cc['max_dd']):>11}"
                 f"{b['calmar']:>8.3f}{p(b['2017_end']['cagr']):>9}{sub:>26}"
                 f"{p(pre['base']['m1'][arm]['cagr']):>8}{b['turnover_annual']:>7.2f}"
                 f"{g5b:>+13.3f}{g5c:>+12.3f}")
    L += ["", "After tax at $100k (progressive 2026 tariff, liquidated; base financing, m = 2):",
          f"{'arm':<5}{'2000 pre':>10}{'2000 AT':>10}{'d vs C0':>10}{'2010 AT':>10}{'d vs C0':>10}"
          f"{'2017 AT':>10}{'d vs C0':>10}{'maxDD 00':>11}{'sells/yr':>10}"]
    for arm in list(ARMS) + ["R2"]:
        cells = [win[GATE_CAPITAL][wn][arm] for wn in TAX_WINDOWS]
        c0 = [win[GATE_CAPITAL][wn][CONTROL] for wn in TAX_WINDOWS]
        L.append(f"{arm:<5}{p(cells[0]['pre_tax_cagr']):>10}{p(cells[0]['after_tax_cagr']):>10}"
                 f"{(cells[0]['after_tax_cagr'] - c0[0]['after_tax_cagr']) * 100:>+10.3f}"
                 f"{p(cells[1]['after_tax_cagr']):>10}{(cells[1]['after_tax_cagr'] - c0[1]['after_tax_cagr']) * 100:>+10.3f}"
                 f"{p(cells[2]['after_tax_cagr']):>10}{(cells[2]['after_tax_cagr'] - c0[2]['after_tax_cagr']) * 100:>+10.3f}"
                 f"{p(cells[0]['after_tax_max_dd']):>11}{cells[0]['sells_per_year']:>10.2f}")
    L += ["", "After tax at other capitals (report-only; arm - C0 in pp):",
          f"{'arm':<5}" + "".join(f"{cl:>12}" for cl in ("10k", "30k", "300k", "1M"))]
    for arm in CANDIDATES:
        L.append(f"{arm:<5}" + "".join(
            f"{(win[cl]['2000_03_to_2026_09'][arm]['after_tax_cagr'] - win[cl]['2000_03_to_2026_09'][CONTROL]['after_tax_cagr']) * 100:>+12.3f}"
            for cl in ("10k", "30k", "300k", "1M")))
    L += ["", f"Clean bootstrap (seed {SEED}, block {BLOCK}, $100k; B1-recent = progressive, B2 = flat 20 %):",
          f"{'arm':<5}{'B1 med':>9}{'B1 d pp':>9}{'B1 share':>10}{'B2 med':>9}{'B2 d pp':>9}{'B2 share':>10}"
          f"{'pre d base pp':>15}{'pre d cal pp':>14}"]
    for arm in list(ARMS) + ["R2"]:
        row = f"{arm:<5}"
        for mode in ("progressive", "flat"):
            m_ = boot_ref["tax"][GATE_CAPITAL][mode][arm]["cagr_median"]
            row += f"{m_ * 100:>9.3f}"
            if arm == CONTROL:
                row += f"{0.0:>9.3f}{1.0:>10.3f}"
            else:
                d = boot_ref["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{CONTROL}"]
                row += f"{d['median_delta_cagr_pp']:>9.3f}{d['share_gt_0']:>10.3f}"
        if arm == CONTROL:
            row += f"{0.0:>15.3f}{0.0:>14.3f}"
        else:
            row += (f"{boot_ref['pre']['base'][f'{arm}_vs_{CONTROL}']['diff_vs_C0_median_pp']:>15.3f}"
                    f"{boot_ref['pre']['calibrated'][f'{arm}_vs_{CONTROL}']['diff_vs_C0_median_pp']:>14.3f}")
        L.append(row)
    L += ["", "Out-of-sample windows (calibrated, m = 2, R-USD; dCAGR / dDD vs C0 in pp):",
          f"{'window':<18}" + "".join(f"{a:>16}" for a in CANDIDATES)]
    for wn in OOS_WINDOWS:
        L.append(f"{wn:<18}" + "".join(
            f"{oos[wn][a]['delta_cagr_pp']:>+8.2f}/{oos[wn][a]['delta_max_dd_pp']:>+6.2f}"
            for a in CANDIDATES))
    L.append(f"{'median dCAGR':<18}" + "".join(
        f"{np.median([oos[wn][a]['delta_cagr_pp'] for wn in OOS_WINDOWS]):>+16.2f}" for a in CANDIDATES))
    L.append("")
    L.append("Pooled OOS bootstrap (M1-M5, 2,000 paths each): " + " | ".join(
        f"{a} med {oosb['pooled'][a]['diff_vs_C0_median_pp']:+.3f} pp share {oosb['pooled'][a]['share_gt_0']:.3f}"
        for a in CANDIDATES))
    L += ["", "Gates (all must pass):",
          f"{'arm':<5}{'G1':>4}{'G2':>4}{'G3':>4}{'G4':>4}{'G5':>4}{'G6':>4}{'G7':>4}{'G8':>4}{'G9':>4}"
          f"{'A1':>4}{'A2':>4}{'P1':>4}{'verdict':>9}  failed"]
    for arm in CANDIDATES:
        r_ = g[arm]
        L.append(f"{arm:<5}" + "".join(f"{'  P' if r_[k] else '  F':>4}"
                                       for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9",
                                                 "A1", "A2", "P1"))
                 + f"{'PASS' if r_['pass'] else 'FAIL':>9}  {','.join(r_['failed'])}")
    L += ["", "Plateau check (pre-registered cuts 0.5/1.5 vs the alternatives):"]
    for arm in ("L1", "L4"):
        v = res["plateau"]["pre_registered"][arm]
        L.append(f"  {arm} pre-registered: pre-tax base m=2 delta {v['pre_tax_base_m2_delta_pp']:+.3f} pp, "
                 f"after-tax 2000 delta {v['after_tax_2000_delta_pp']:+.3f} pp -> P1 "
                 f"{'PASS' if g[arm]['P1'] else 'FAIL'}")
        for tag in res["plateau"]["alt"]:
            a_ = res["plateau"]["alt"][tag][arm]
            L.append(f"    {tag}: pre-tax {a_['pre_tax_base_m2_delta_pp']:+.3f} pp, "
                     f"after-tax {a_['after_tax_2000_delta_pp']:+.3f} pp")
    ep = res["episodes"]
    L += ["", f"Early path: {ep['n_episodes']} episodes; arm - C0 sum over episodes (pp): "
          + ", ".join(f"{a} {ep['sum_delta_pp'][a]:+.2f}" for a in CANDIDATES),
          "Leave-one-episode-out (the arm's early leg replaced by C0's inside the episode):"]
    for tag, v in ep["leave_one_out"].items():
        if not v.get("found"):
            L.append(f"  {tag}: episode not found")
            continue
        L.append(f"  {tag} [{v['span'][0]} -> {v['span'][1]}]: " + ", ".join(
            f"{a} {v[a]['delta_cagr_vs_C0_pp']:+.3f} pp" for a in CANDIDATES))
    L += ["", "E5 decision traces (main path, pre-tax, $100k): " + " | ".join(
        f"{a} differs {res['e5'][f'{a}_vs_{CONTROL}']['sessions_decision_differs']} sessions "
        f"({res['e5'][f'{a}_vs_{CONTROL}']['share_decision_differs'] * 100:.2f} %)" for a in CANDIDATES)]
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
