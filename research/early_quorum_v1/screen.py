"""early_quorum_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Idea I2 of docs/research/idea_survey_v4.md: replace the early re-entry leg's AND-of-3 entry
condition with a 2-of-3 quorum (and, in Q3, a hysteretic exit), keeping everything else of the
production rule K fixed. Control = production **K** (`equity_leveraged_trend_v1` 1.2.0 = E2 +
R2 early re-entry + A1u gain-scaled downsize band + H2 December deferral), built on
research/band_h2_combo_v1's harness (its `engine_run` and tax engine are v3/th2's, validated
cell-by-cell against gain_band_v3's A1u and tax_harvest_v2's H2 in --harness).

Arms (pre-registered; every parameter fixed):
  K    production K, verbatim (the control).
  C0   production **R2** (K's execution layer OFF) -- the report-only reference row.
  Q1   early leg active while >= 2 of the 3 early conditions hold; exit below SMA50 (production
       exit).
  Q2   drop only the 20-session momentum condition: SMA50 and vol20 < median252(vol20); exit
       below SMA50.
  Q3   Q1's entry (2 of 3) with a hysteretic exit: leave only when >= 2 of the 3 conditions fail.
  The early weight stays 0.5 x vt in every arm; vt, cap, band, A1u and H2 are unchanged.
  N40/N60/N126/N504/W13/W23  the six fast_reentry_v2 one-parameter neighbours of the *production*
       early path (SMA 40/60, vol-median window 126/504, early weight 1/3 and 2/3) on K's
       execution layer -- the plateau comparison block, report-only.

Stages (pre-registered before any non-control arm was simulated on real data):
  pre      pre-tax main window, base/calibrated x m = 1/2/4, plus the R-USD row.
  PLT      the plateau block: the six neighbours + the three Q arms, main window, base, m = 2.
  AT       after tax at $100k on the three windows (gate capital).
  B12      clean bootstrap, seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths,
           $100k + $1M report-only, B1-recent (progressive) and B2-flat.
  E1       after tax on the 21 quality-passing ETFs under R-INDEX, $100k, calibrated, m = 2.
  E2       rolling January starts 2001..2021, after tax at $100k, base, m = 2.
  E5       report only: fill-decision traces.
  LOEO     leave-one-episode-out for the three early episodes the survey names
           (2001-10-31, 2002-11-04, 2023-01-20): the candidate's early-leg decisions are replaced
           by the control's over the episode span, main window, base, m = 2.
  OOS      the six ewma_band_v1 windows, pre-tax, calibrated, m = 2, R-USD rule.
  IE       the 21 quality-passing ETFs, pre-tax, calibrated, m = 2, R-USD rule (gate EI).

Gates (pre-registered, every candidate arm vs K; see PREREGISTRATION.md section 6). All must
hold for adoption:
  G1 return (m = 2, base and calibrated): CAGR >= K's, or Calmar >= 1.1 x K's with
     CAGR >= K - 0.5 pp.
  G2 max DD <= K + 1 pp (base and calibrated).
  G3 2017+ CAGR >= K - 1 pp (base and calibrated).
  G4 CAGR above K in >= 2 of the 3 sub-periods.
  G5 block-20 bootstrap median of arm - K >= 0 (base and calibrated, 5,000 paths).
  G6 G1 at m = 1 (base and calibrated).
  G7 six OOS windows: median delta CAGR >= 0; >= 5 of 6 with delta CAGR >= -0.5 pp and
     delta DD <= +3 pp; delta DD never above +8 pp.
  G8 pooled OOS bootstrap median >= 0.
  G9 turnover <= 2 x K (the ratio is also reported).
  EI 21 ETFs (pre-tax, calibrated, m = 2, own E2 + own early path vs own K): median delta
     CAGR >= 0; share > 0 >= 60 %; share delta DD <= +3 pp >= 80 %; pooled instrument bootstrap
     (500 paths per instrument, seed 20261011, block 20) median >= 0.
  T1 after tax at $100k: arm - K >= 0 in all three windows.
  T2 B1-recent (progressive) at $100k: median >= 0 and share > 0 >= 55 %.
  T3 B2 (flat 20 %) at $100k: median >= 0 and share > 0 >= 55 %.
  PL plateau: all three Q arms have pre-tax main-window base-m2 delta CAGR > 0 and delta max DD
     <= +1 pp (the plateau comparison the survey names; the neighbour table is reported, not gated).

--harness reproduces the control's published K anchors (band_h2_combo_v1/RESULTS.json: pre-tax
15.7649 / 15.1785 % CAGR, max DD 46.6698 / 46.7948 %, turnover 5.4561; after tax at $100k
10.7533 / 15.2354 / 23.9720 %) and, in its deep part, the parent campaigns' published cells
cell-by-cell (gain_band_v3's A1u against research/gain_band_v3/RESULTS.json, tax_harvest_v2's
H2 against H2_ANCHORS.json) -- the check that the per-arm target-series plumbing added here did
not move the engine.

--- inherited description of the parent study (band_h2_combo_v1) ---

Combination study. Question: does the USD gain-scaled downsize band (gain_band_v3's A1u, being
implemented now) plus the year-end vol-resize deferral (tax_harvest_v2's H2) beat A1u alone
after Turkish tax, and does the pair stay sound? Arms: C0 (production R2), A1u, H2, K = A1u+H2.

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

Modes
  --smoke    synthetic random-walk prices only, no market data: the rule-gain probes (TRY and
             the A1u USD path) vs the engine's own sell gain, K == the reference
             `simulate_band_overlay` for the tail-10 window, the production early-state spec ==
             production `r2_state` bit for bit, the quorum specs' monotonicity (Q1's active set
             contains the production set, Q3's contains Q1's), finiteness and after-tax <= pre-tax.
  --harness  reproduction on real data: the control's K anchors (pre-tax and after tax at $100k),
             the R2 reference row, the E2 fidelity anchor, K engine == `simulate_band_overlay`,
             the production early spec == `r2_state`, then the deep block (A1u vs gain_band_v3's
             published cells, H2 vs tax_harvest_v2's published cells) and the clean bootstrap
             reused by `run()`.
  --run      harness (deep), then the stages above, the reference comparison, the gates and the
             SUMMARY. Writes RESULTS.json, SUMMARY.txt.

One engine serves every stage: `engine_run` is a stateful session loop over the Turkish
after-tax account (research/early_quorum_v1/tax_engine.py, a byte copy of
research/band_h2_combo_v1/tax_engine.py = research/gain_band_v3/tax_engine.py =
research/tax_harvest_v2/tax_engine.py = the r2_tbill_combo_v1 / vt_level_tax_v1 corrected
engine) with production K's execution layer plus the arm's fill rule. The rule's decision state
(the USD FIFO gain fraction) is computed independently of the regime, so the pre-tax and
after-tax runs follow the same trade path. Every arm label runs its own target series
(`EARLY_SPEC`); with the production spec the series is bit-identical to `r2_state`.

Engine helpers (synthetic 2x, SMA/TSM/DON, vol, vt, metrics) come from
research/trend_robustness_v2/screen.py (its main() is never run); the production R2 signal,
`simulate_band`, `simulate_band_overlay` and the OOS/instrument loaders are the copies used by
research/band_h2_combo_v1/screen.py (itself from tax_harvest_v2 / fast_reentry_v1 / ewma_band_v1
/ fast_reentry_v2, i.e. e2_confirmation_v1's and range_vol_v2's designs).
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
# K is production K verbatim: A1u is gain_band_v3's A1u (same tiers, same USD gain fraction),
# H2 is tax_harvest_v2's H2 (postpone a non-exit downward resize whose *fill* session is one of
# the last DEC_TAIL December sessions), composed H2-first. The control of THIS study is K, so
# every candidate arm keeps K's execution layer and changes only the early leg's entry/exit
# condition (`EARLY_SPEC`). "C0" is production R2 -- the report-only reference row.
CONTROL = "K"           # production K = A1u + H2 (the control of this study)
REFERENCE = "C0"        # production R2 (band off, no deferral) -- the reference row
CANDIDATES = ("Q1", "Q2", "Q3")
NEIGHBOURS = ("N40", "N60", "N126", "N504", "W13", "W23")   # the plateau comparison block
ARMS = (CONTROL, REFERENCE) + CANDIDATES                    # the run's arms
ARMS_ALL = ARMS + NEIGHBOURS
REF_ARMS = ("C0", "A1u", "H2", "K")   # the deep-harness reproduction block (band_h2's labels)
STUDY_ARMS = ("C0", "A1u", "H2", "K", "Q1", "Q2", "Q3")     # every stage's arm set
SMOKE_ARMS = ("K", "C0", "Q1", "Q2", "Q3", "A1u", "H2")     # --smoke's arm set (all specs + refs)
RULE = {
    "C0": {"band": False, "usd_rule": False, "defer": False},   # production R2
    "A1u": {"band": True, "usd_rule": True, "defer": False},    # reference label (deep harness)
    "H2": {"band": False, "usd_rule": False, "defer": True},    # reference label (deep harness)
    "K": {"band": True, "usd_rule": True, "defer": True},       # production K = the control
}
for _arm in CANDIDATES + NEIGHBOURS:
    RULE[_arm] = RULE["K"]           # every candidate keeps K's execution layer exactly
RULE["E2"] = RULE["C0"]              # alias: the harness's pure-E2 fidelity anchor series
B_UP = C0_BAND                                   # entries / exits / up-resizes keep 0.5 pp
GAIN_TIERS = ((0.01, 0.005), (0.05, 0.03), (math.inf, 0.08))  # g_frac ceiling -> B_down
DEC_TAIL = 10                                    # H2: the last 10 December sessions
YEAR_END = "12"                                  # month prefix of the December sessions

# ---- the early re-entry leg (pre-registered; see PREREGISTRATION.md section 4)
# Each spec fixes the entry condition set, the quorum threshold, the exit rule, the SMA length,
# the vol-median window and the early weight. "prod" is production R2's leg verbatim.
EARLY_CONDS = ("sma", "vol", "mom")


def _early(conds=EARLY_CONDS, need=3, exit="sma", sma=SMA_FAST, med=MED_WINDOW, weight=0.5):
    return {"conds": tuple(conds), "need": int(need), "exit": str(exit),
            "sma": int(sma), "med": int(med), "weight": float(weight)}


EARLY_PROD = _early()
EARLY_SPEC = {
    # ---- production early path (the control's and every reference label's leg)
    "prod": EARLY_PROD,
    # ---- the study's arms: the early leg only
    "Q1": _early(need=2, exit="sma"),          # >= 2 of 3 hold; exit below SMA50
    "Q2": _early(conds=("sma", "vol"), need=2, exit="sma"),   # momentum condition dropped
    "Q3": _early(need=2, exit="majority"),     # >= 2 of 3 hold; exit when >= 2 fail
    # ---- the fast_reentry_v2 one-parameter neighbours of the production early path
    "N40": _early(sma=40), "N60": _early(sma=60),
    "N126": _early(med=126), "N504": _early(med=504),
    "W13": _early(weight=1 / 3), "W23": _early(weight=2 / 3),
}
for _arm in ("K", "C0", "A1u", "H2"):
    EARLY_SPEC[_arm] = EARLY_PROD
# the three early episodes the survey names (entry-decision session -> last held session),
# asserted against the reference R2 early path in --smoke / --run (fast_reentry_v1's table).
LOEO_EPISODES = {
    "2001-10": ("2001-10-31", "2001-12-05"),
    "2002-11": ("2002-11-04", "2002-12-02"),
    "2023-01": ("2023-01-20", "2023-02-01"),
}

# ---- after-tax stage
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITALS = {"10k": 10_000.0, "30k": 30_000.0, "100k": 100_000.0, "300k": 300_000.0, "1M": 1_000_000.0}
TAX_WINDOWS_LAST = "2017_01_to_end"   # the reporting window for per-arm trade/tax counters
E5_CAPITALS = ()   # report-only extra capitals (none in this study)
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

# ---- gates (pre-registered; see PREREGISTRATION.md section 6). Every candidate arm vs K.
G1_MIN_REL_CAGR_PP = -0.50   # G1 fallback: Calmar >= 1.1 x K's with CAGR >= K - this
G1_MIN_CALMAR_MULT = 1.10
G2_MAX_DD_PP = 1.0           # max DD <= K + this (base and calibrated)
G3_MIN_CAGR_PP = -1.00       # 2017+ CAGR >= K - this (base and calibrated)
G4_MIN_WINS = 2              # CAGR above K in at least this many of the 3 sub-periods
G5_MIN_MEDIAN_PP = 0.0       # block-20 pre-tax bootstrap median of arm - K (both financing)
G6 = "G1 at m = 1 (base and calibrated)"
G7_MIN_MEDIAN_PP = 0.0       # six OOS windows: median delta CAGR
G7_MIN_WINDOWS = 5           # of 6 with delta CAGR >= -0.5 pp and delta DD <= +3 pp
G7_MIN_CAGR_PP = -0.50
G7_MAX_DD_PP = 3.0
G7_MAX_DD_HARD_PP = 8.0
G8_MIN_MEDIAN_PP = 0.0       # pooled OOS bootstrap median
G9_MAX_TURNOVER_MULT = 2.0   # turnover <= this x K
EI_MIN_SHARE = 0.60          # share of the 21 ETFs with delta CAGR > 0
EI_MAX_DD_PP = 3.0           # per-instrument delta DD allowance
EI_MIN_DD_SHARE = 0.80       # share of the 21 inside the allowance
EI_PATHS = 500               # instrument bootstrap paths per instrument
T1_MIN_DELTA_PP = 0.0        # after tax at $100k: arm - K >= 0 in all three windows
T2_MIN_MEDIAN_PP = 0.0       # B1-recent (progressive) at $100k
T2_MIN_SHARE = 0.55          # ... share > 0 >= this
PL_MIN_DELTA_PP = 0.0        # plateau: all three Q arms' base-m2 delta CAGR > 0
PL_MAX_DD_PP = 1.0           # ... and delta max DD <= +1 pp
# ---- reporting-stage constants (the parent campaigns' stages, report-only here)
E1_MIN_SHARE = 0.60            # share of the 21 ETFs with after-tax delta > 0 (report)
E1_RINDEX_START = "2000-03-09"  # the TRY calendar's start; E1 start = max(ETF warm-up, this)
E2_START_YEARS = tuple(range(2001, 2022))   # 21 January starts, 2001..2021
E2_MIN_SHARE = 0.70            # reported, not gated
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

# ---- the control's published anchors: research/band_h2_combo_v1/RESULTS.json (the K cells)
# (pre_tax_main, cagr nav-based / max_dd / turnover_annual; after_tax_windows 100k pre_tax_cagr /
# after_tax_cagr / max_dd_after_tax; bootstrap_clean_seed20261011 tax.100k.*).
REF_K = {  # (cagr, max_dd, turnover)
    ("base", 1): (0.160173, 0.463529, 5.4568),
    ("base", 2): (0.157649, 0.466698, 5.4561),
    ("calibrated", 1): (0.154303, 0.464786, 5.4562),
    ("calibrated", 2): (0.151785, 0.467948, 5.4562),
}
TAX_ANCHOR_K = {  # window -> (pre_tax cagr, progressive cagr, progressive max_dd), $100k
    "2000_03_to_2026_09": (0.157615, 0.107533, 0.466698),
    "2010_01_to_end": (0.223435, 0.152354, 0.446272),
    "2017_01_to_end": (0.330215, 0.239720, 0.349774),
}
BOOT_ANCHOR_K = {  # mode -> (K cagr_median, K - R2 median delta pp, share > 0)
    "progressive": (0.038577, 0.2856, 0.7500),
    "flat": (0.040376, 0.1311, 0.6400),
}
REF_K_BOOT_PRE = {  # financing -> (K median, K - R2 median delta pp, R2 median), pre-tax leg
    "base": (0.077761, -0.007121, 0.078106),
    "calibrated": (0.073137, -0.011741, 0.073469),
}
TAX_ANCHORS["K"] = TAX_ANCHOR_K


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


def early_components(price, sma=SMA_FAST, med=MED_WINDOW):
    """The four path-independent early-entry conditions: SMA50, price vol20, its trailing
    252-session median, and 20-session momentum. `fast_reentry_v1`'s tuple also carried the
    Donchian vote and the 252-session running max; neither is used by the R2 or E2 branch of
    `r2_state`/`arm_signals`, so they are not computed here.

    `sma` and `med` are the study's only parameter overrides (the plateau neighbours move one of
    them); the production call is `early_components(price)` with the module defaults."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma50_up = trv2.sma_up(price, sma)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol(vol_p, med)
    close_lag20 = np.full_like(price, np.nan)
    close_lag20[:, LAG_FAST:] = price[:, :-LAG_FAST]
    lag_cond = price > close_lag20
    return sma50_up, vol_p, med_p, lag_cond


def early_state(price, e2_on, comps, spec):
    """(on_eff, factor) of one early-leg spec: the E2 vote plus the arm's early re-entry leg.

    `spec` (EARLY_SPEC) fixes the condition set (`conds`), the quorum threshold (`need`), the
    exit rule (`exit`), the SMA/vol-median windows and the early weight. `exit == "sma"` leaves
    the leg when the close is at or below its SMA (production's exit); `exit == "majority"`
    (Q3) leaves it only when `len(conds) - need + 1` conditions fail, i.e. at most `need - 1`
    still hold -- the symmetric hysteresis partner of the entry quorum. E2 always overrides:
    the leg is off while E2 is on, and E2's own entry immediately takes the position over.

    The session recurrence is
        active[t] = E[t] | (active[t-1] & ~M[t]),   M[t] = e2[t] | exit[t],
    where `E` is the entry condition (~e2 and at least `need` conditions hold and the vol
    median exists) and `M` is the mandatory-exit condition (E2 on, or the arm's exit rule).
    That is exactly "the last entry with no mandatory exit after it", so it is computed in one
    pass: with `L`/`R` the running maxima of the indices of the last `E` and the last `M`,
    `active = L >= R` (E wins a tie because the loop exits before it enters). `_early_state_loop`
    is the session-by-session reference of this recurrence and --smoke asserts the two agree
    for every spec.

    With `spec = EARLY_PROD` this is bit-for-bit production `r2_state`: holds >= 3 of
    {sma, vol < its median, 20-session momentum} is exactly `sma50_up & (vol < med) & lag`, and
    both entry paths require a finite median."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    P, T = price.shape
    sma_up, vol_p, med_p, lag_cond = comps
    conds = spec["conds"]
    hold = np.zeros((P, T), dtype=np.int64)
    if "sma" in conds:
        hold = hold + sma_up
    if "vol" in conds:
        hold = hold + (np.isfinite(med_p) & (vol_p < med_p))
    if "mom" in conds:
        hold = hold + lag_cond
    need = spec["need"]
    if spec["exit"] == "sma":
        exit_cond = ~sma_up
    else:                                        # "majority": the leg survives on `need - 1`
        exit_cond = hold <= len(conds) - need
    entry = (~e2_on) & (hold >= need) & np.isfinite(med_p)
    mandatory = e2_on | exit_cond
    idx = np.broadcast_to(np.arange(T, dtype=np.int64), (P, T))
    last_entry = np.maximum.accumulate(np.where(entry, idx, -1), axis=1)
    last_exit = np.maximum.accumulate(np.where(mandatory, idx, -1), axis=1)
    early_active = last_entry >= last_exit
    on_eff = e2_on | early_active
    factor = np.where(e2_on, 1.0, np.where(early_active, spec["weight"], 0.0))
    return on_eff, factor


def _early_state_loop(price, e2_on, comps, spec):
    """The session-by-session reference of `early_state` (the recurrence it vectorises);
    called only by --smoke, which asserts the two agree for every spec."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    P, T = price.shape
    sma_up, vol_p, med_p, lag_cond = comps
    conds = spec["conds"]
    hold = np.zeros((P, T), dtype=np.int64)
    if "sma" in conds:
        hold = hold + sma_up
    if "vol" in conds:
        hold = hold + (np.isfinite(med_p) & (vol_p < med_p))
    if "mom" in conds:
        hold = hold + lag_cond
    need = spec["need"]
    if spec["exit"] == "sma":
        exit_cond = ~sma_up
    else:
        exit_cond = hold <= len(conds) - need
    early_active = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    for t in range(T):
        e2t = e2_on[:, t]
        early_active = np.where(e2t, False, early_active)
        early_active = np.where(early_active & exit_cond[:, t] & (~e2t), False, early_active)
        can_enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & (hold[:, t] >= need), True, early_active)
        on_eff[:, t] = e2t | early_active
        factor[:, t] = np.where(e2t, 1.0, np.where(early_active, spec["weight"], 0.0))
    return on_eff, factor


def early_flag(price, e2_on, spec, comps=None):
    """Just the early leg's active flag [P, T] (the study's episode/LOEO machinery)."""
    comps = early_components(price, spec["sma"], spec["med"]) if comps is None else comps
    return early_state(price, e2_on, comps, spec)[0] & ~np.atleast_2d(np.asarray(e2_on, dtype=bool))


def r2_state(price, e2_on, comps=None):
    """(on_eff, factor) of production R2's path: the E2 vote plus the half-weight early
    re-entry leg. Model-independent -- the entry/exit conditions use the *price* series only,
    so one call serves both financing models. `fast_reentry_v1.arm_signals`' R2 branch,
    separated from the sizing so the session loop runs once per path.

    Kept as the independent reference implementation of the production leg: `--smoke` and
    `--harness` assert `early_state(..., EARLY_PROD)` == this function."""
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


def spec_comps(price, arms=ARMS_ALL):
    """{(sma, med): comps} for the arms' distinct early windows -- computed once per call."""
    out = {}
    for a in arms:
        s = EARLY_SPEC[a]
        k = (s["sma"], s["med"])
        if k not in out:
            out[k] = early_components(price, s["sma"], s["med"])
    return out


def _spec_key(spec):
    return (spec["conds"], spec["need"], spec["exit"], spec["sma"], spec["med"], spec["weight"])


def wants_by_arm(price, e2, vol_instr, arms=ARMS_ALL):
    """{arm label: target weights [P, T]} for the given arms (P = the input's path count, so a
    single series gives [1, T] and a batched resample [P, T]), one state loop per distinct spec
    and one `early_components` per distinct (sma, med) pair."""
    cm = spec_comps(price, arms)
    out, cache = {}, {}
    for a in arms:
        s = EARLY_SPEC[a]
        k = _spec_key(s)
        if k not in cache:
            comps = cm[(s["sma"], s["med"])]
            on_eff, factor = early_state(price, e2, comps, s)
            cache[k] = sized_wants(on_eff, factor, vol_instr)
        out[a] = cache[k]
    return out


def sized_pair(price, e2, comps, arms, vol_base, vol_cal):
    """{arm: (wants [P, T] under base financing, wants [P, T] under calibrated financing)} with
    one state loop per distinct spec -- the two financing models differ only in the vol series
    used for sizing. Every arm here shares the production windows, so one `comps` serves them."""
    out, cache = {}, {}
    for a in arms:
        s = EARLY_SPEC[a]
        k = _spec_key(s)
        if k not in cache:
            on_eff, factor = early_state(price, e2, comps, s)
            cache[k] = (sized_wants(on_eff, factor, vol_base),
                        sized_wants(on_eff, factor, vol_cal))
        out[a] = cache[k]
    return out


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
_PRICE = {}


def qqq_price():
    """The QQQ close series: the series production `equity_leveraged_trend_v1` evaluates the E2
    vote and the early leg's conditions on, and the series `main_inputs` sizes from. It is NOT the
    synthetic 2x close: the production early path is active in 164 measured sessions on the price
    against 126 on the synthetic close, and 164 is production's own count (fast_reentry_v1: 164 of
    6,679, 2.46 %). Spread-independent, so one cached load serves every reporting stage."""
    if not _PRICE:
        _PRICE["price"] = trv2.load()[1]["QQQ"][1]
    return _PRICE["price"]


def main_inputs(spread, arms=ARMS_ALL):
    """The main QQQ series inputs plus {arm: target series} for `arms` (and the pure-E2 anchor)."""
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    price, e2, vol_instr = q[1], e2_up(q[1])[0], trv2.vol_n(c)[0]
    comps = early_components(price)
    w_e2 = arm_signals(price, e2, vol_instr, "E2", comps)[0][0]   # fidelity anchor only
    W = wants_by_arm(price, e2, vol_instr, arms)
    W["E2"] = w_e2
    return days, o, c, e2, W, w_e2


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
    """One OOS window: own E2 + own early path, own 2x leg (calibrated, m = 2), R-USD rule.
    Every arm label is driven by its own target series."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)[0]
    W = wants_by_arm(c, e2, trv2.vol_n(sc)[0], arms)
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
    """Own E2 + own early path on the instrument's adjusted close, own 2x leg (R-USD rule).
    Every arm label is driven by its own target series."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)[0]
    W = wants_by_arm(c, e2, trv2.vol_n(sc)[0], arms)
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
        # one state loop per arm per path; the sizing differs per financing model only
        pair = sized_pair(pc, e2, comps, arms, trv2.vol_n(sc)[0], trv2.vol_n(sc_c)[0])
        w_e2 = sized_wants(np.atleast_2d(e2), np.ones_like(pc), trv2.vol_n(sc)[0])[0]
        legs = {"base": (so[0], sc[0], 0), "calibrated": (so_c[0], sc_c[0], 1)}
        # every arm label runs its own target series; with the production spec that series is
        # bit-identical to production R2's.
        if pidx < n_pre:
            for md, (o_, c_, i_) in legs.items():
                for arm in arms:
                    r = engine_run(PRE_CAPITAL, i0, i1, fixed_dates, mkt, o_, c_, pair[arm][i_], e2,
                                   arm, "pre_tax", m=M)
                    k = len(r["_nav"]) - 1
                    pre[(md, arm)].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
        if pidx < n_tax:
            o_, c_, i_ = legs["base"]
            for cap_label in capitals:
                for mode in modes:
                    for arm in arms:
                        r = engine_run(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, o_, c_,
                                       pair[arm][i_], e2, arm, mode, m=M)
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
        out["pre"][md] = {}
        for arm in arms:
            c = np.array(pre[(md, arm)])
            e = {"cagr_median": float(np.median(c))}
            for ctrl in baselines:
                if arm == ctrl:
                    continue
                d = c - np.array(pre[(md, ctrl)])
                e[f"{arm}_vs_{ctrl}"] = {
                    "median_delta_pp": float(np.median(d) * 100),
                    "mean_delta_pp": float(np.mean(d) * 100), "share_gt_0": float(np.mean(d > 0)),
                    "p05_pp": float(np.percentile(d, 5) * 100),
                    "p95_pp": float(np.percentile(d, 95) * 100)}
            if "C0" in baselines and arm != "C0":
                # the parent campaign's cell names for this block: its "C0" is production R2, so
                # these five leaves are the arm-versus-R2 comparison gain_band_v3 published.
                s = e[f"{arm}_vs_C0"]
                e["diff_vs_C0_median_pp"] = s["median_delta_pp"]
                e["diff_vs_C0_mean_pp"] = s["mean_delta_pp"]
                e["share_gt_0"] = s["share_gt_0"]
                e["p05_delta_pp"] = s["p05_pp"]
                e["p95_delta_pp"] = s["p95_pp"]
            out["pre"][md][arm] = e
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
        W = wants_by_arm(cc, e2, vol_instr, arms)
        for arm in arms:
            want = W[arm]   # the arm's own target series
            for p in range(P):
                r = engine_run(1.0, START, cc.shape[1] - 1, days, mkt, so[p], sc[p], want[p], e2[p],
                               arm, "pre_tax", m=M, rule_index=False)
                k = len(r["_nav"]) - 1
                acc[arm].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
    return {a: np.array(v, dtype=float) for a, v in acc.items()}


def boot_instrument(key, rng, arms, n_paths=EI_PATHS):
    """The instruments' pooled bootstrap (gate EI, part d): the `boot_oos_market` draw machinery
    on one ETF's own adjusted OHLC and its own synthetic 2x leg, R-USD rule, m = 2, pre-tax."""
    days, o, _h, _l, c, y = load_instrument(key)
    start = START
    pool = np.arange(start, len(days))
    npool = len(pool)
    mkt = usd_market(days)
    acc = {a: [] for a in arms}
    for chunk in range(0, n_paths, trv2.CHUNK):
        P = min(trv2.CHUNK, n_paths - chunk)
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
        W = wants_by_arm(cc, e2, trv2.vol_n(sc), arms)
        for arm in arms:
            want = W[arm]
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
    comps = early_components(price)
    w_e2, w_r2 = (w[0] for w in wants_of(price, e2, v20, comps))
    W = wants_by_arm(price, e2, v20, SMOKE_ARMS)
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
    # 2c. the per-arm plumbing: the production spec reproduces the R2 series bit for bit, and the
    # quorum specs sit where they should (production subset Q1; Q1 subset Q2, the loosest entry;
    # Q3 is Q1's entry with a longer hold, so neither contains the other)
    checks["smoke_prod_spec_equals_r2_series"] = bool(
        np.array_equal(W["K"][0], w_ref) and np.array_equal(W["C0"][0], w_ref)
        and np.array_equal(W["A1u"][0], w_ref) and np.array_equal(W["H2"][0], w_ref))
    fp = early_flag(price, e2, EARLY_PROD, comps)[0]
    f1 = early_flag(price, e2, EARLY_SPEC["Q1"], comps)[0]
    f2 = early_flag(price, e2, EARLY_SPEC["Q2"], comps)[0]
    f3 = early_flag(price, e2, EARLY_SPEC["Q3"], comps)[0]
    checks["smoke_prod_subset_Q1"] = bool(np.all(f1 | ~fp))
    checks["smoke_prod_subset_Q2"] = bool(np.all(f2 | ~fp))
    checks["smoke_Q2_subset_Q1"] = bool(np.all(f1 | ~f2))
    checks["smoke_prod_strictly_looser_in_Q1"] = bool(f1.sum() > fp.sum())
    checks["smoke_prod_strictly_looser_in_Q2"] = bool(f2.sum() > fp.sum())
    checks["smoke_Q2_strictly_tighter_than_Q1"] = bool(f1.sum() > f2.sum())
    checks["smoke_Q3_is_a_different_device"] = bool(not np.array_equal(f1, f3))
    early_sessions = {"prod": int(fp.sum()), "Q1": int(f1.sum()),
                      "Q2": int(f2.sum()), "Q3": int(f3.sum())}
    # 2d. the vectorised `early_state` == the session-by-session reference, for every spec
    ok_vec = True
    for a in ARMS_ALL:
        s = EARLY_SPEC[a]
        c_ = early_components(price, s["sma"], s["med"])
        o1, x1 = early_state(price, e2, c_, s)
        o2, x2 = _early_state_loop(price, e2, c_, s)
        if not (np.array_equal(o1, o2) and np.array_equal(x1, x2)):
            ok_vec = False
    checks["smoke_vectorised_state_equals_loop_all_specs"] = bool(ok_vec)
    # ... and an adversarial case: a price series whose conditions flicker every session
    rng2 = np.random.default_rng(23)
    flick = 100 * np.cumprod(1 + rng2.normal(0, 0.03, 900))
    e2f = (rng2.random(900) < 0.3)
    cf = early_components(flick)
    for a in ARMS_ALL:
        s = EARLY_SPEC[a]
        o1, x1 = early_state(flick, e2f, cf, s)
        o2, x2 = _early_state_loop(flick, e2f, cf, s)
        if not (np.array_equal(o1, o2) and np.array_equal(x1, x2)):
            ok_vec = False
    checks["smoke_vectorised_state_equals_loop_flicker"] = bool(ok_vec)

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
    W_UP = wants_by_arm(price_up, e2_u, trv2.vol_n(sc_u)[0], SMOKE_ARMS)
    blocked = {}
    for arm in SMOKE_ARMS:
        rr = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, W_UP[arm],
                        e2_u, arm, "pre_tax")
        blocked[arm] = {"band": rr["blocked_band"], "deferred": rr["deferred"],
                        "postponed": rr["postponements"]}
    checks["band_fires"] = bool(blocked["A1u"]["band"] > 0 or blocked["K"]["band"] > 0)
    checks["deferral_fires"] = bool(blocked["H2"]["deferred"] > 0 and blocked["K"]["deferred"] > 0)
    checks["C0_blocks_nothing"] = bool(blocked["C0"]["postponed"] == 0)
    checks["postponements_decompose"] = bool(all(
        blocked[a]["postponed"] == blocked[a]["band"] + blocked[a]["deferred"] for a in SMOKE_ARMS))

    # 4b. the tax modes bite in a trending series: progressive <= pre-tax, and flat < pre-tax
    flat_bites = prog_bites = False
    for arm in SMOKE_ARMS:
        want_u = W_UP[arm]
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
    for arm in SMOKE_ARMS:
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
    return {"checks": checks, "arms": res, "mechanism": blocked, "early_sessions": early_sessions,
            "pass": bool(all(v for v in checks.values() if isinstance(v, bool)))}


def harness(deep=False):
    """Control reproduction on real data (no candidate arm).

    The study's control is production **K**, so the label "K" -- the run's control label, its
    target series = production R2's and its fill rule = A1u's band + H2's December window -- is
    asserted against research/band_h2_combo_v1/RESULTS.json's K cells: pre-tax 15.7649 / 15.1785 %
    CAGR and max DD 46.6698 / 46.7948 % (m = 2), turnover 5.4561; after tax at $100k
    10.7533 / 15.2354 / 23.9720 % CAGR with max DD 46.6698 / 44.6272 / 34.9774 %; bootstrap
    medians 3.8577 / 4.0376 % with deltas +0.2856 pp (75.0 % of paths ahead) and +0.1311 pp
    (64.0 %) versus R2. The label "C0" (band off, no deferral) is asserted against
    fast_reentry_v1's production-R2 anchors and vt_level_tax_v1's R2 after-tax anchors; the pure
    E2 series under its own label "E2" (e2_integration_v1 / tax_harvest_v2). `early_state` with
    the production spec is asserted bit-for-bit against `r2_state`, and the K engine against
    `simulate_band_overlay`'s tail-10 window."""
    checks, bad = {}, 0
    days, o, c, e2, W0, w_e2 = main_inputs(0.0, REF_ARMS)
    w_r2, w_k = W0["C0"][0], W0["K"][0]     # 1-D rows: the reference simulators want [P, T]
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    for md, spread in FIN.items():
        d_, o_, c_, e_, W_, we_ = main_inputs(spread, REF_ARMS)
        for label, wants, ref in (("E2", we_, REF_E2), ("C0", W_["C0"], REF_R2), ("K", W_["K"], REF_K)):
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
        for label, wants, series in (("E2", w_e2, "E2"), ("C0", w_r2, "R2"), ("K", w_k, "K")):
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
    # ---- explicit K anchor block (the run's control label)
    k_anchors = {
        "reference": {"pre_tax": "band_h2_combo_v1/RESULTS.json pre_tax_main[K]: 15.7649 / 15.1785 % "
                                 "CAGR, max DD 46.6698 / 46.7948 %, turnover 5.4561",
                      "after_tax": "band_h2_combo_v1/RESULTS.json after_tax_windows[100k][K]: "
                                   "10.7533 / 15.2354 / 23.9720 % with max DD 46.6698 / 44.6272 / "
                                   "34.9774 %"},
        "pre_tax": {f"{md}_m{m}": {
            "reference_cagr": REF_K[(md, m)][0],
            "reproduced_cagr": checks[f"K_{md}_m{m}_cagr_usd_unliquidated"]["reproduced"],
            "reference_max_dd": REF_K[(md, m)][1],
            "reproduced_max_dd": checks[f"K_{md}_m{m}_max_dd_nav_usd"]["reproduced"],
            "reference_turnover": REF_K[(md, m)][2],
            "reproduced_turnover": checks[f"K_{md}_m{m}_turnover_annual"]["reproduced"]}
            for md in FIN for m in (1, 2)},
        "after_tax_100k": {},
    }
    for wn in TAX_WINDOWS:
        k_anchors["after_tax_100k"][wn] = {
            "reference": list(TAX_ANCHOR_K[wn]),
            "reproduced": [tax[wn]["K"]["pre_tax"]["reproduced"],
                           tax[wn]["K"]["progressive"]["reproduced"],
                           tax[wn]["K"]["progressive_max_dd"]["reproduced"]],
            "pass": bool(all(abs(v["diff_pp"]) <= 0.005 for v in tax[wn]["K"].values()))}
    k_anchors["pass"] = bool(
        all(abs(checks[f"K_{md}_m{m}_{k}"]["diff"]) <= TOL for md in FIN for m in (1, 2)
            for k in ("cagr_usd_unliquidated", "max_dd_nav_usd", "turnover_annual"))
        and all(k_anchors["after_tax_100k"][wn]["pass"] for wn in TAX_WINDOWS))
    if not k_anchors["pass"]:
        bad += 1
    # ---- the H2 engine == the reference overlay simulator (tail-10 December window, verbatim
    # H2 rule), the production early spec == production `r2_state` bit for bit, and K differs
    # from C0 only by its fill rule (both run production R2's target series)
    dt10 = december_tail(days, START, len(days) - 1, DEC_TAIL)
    ref_ov, _, _ = simulate_band_overlay(o[None, :], c[None, :], w_r2[None, :], COST_PER_SIDE * M,
                                         START, C0_BAND, dec_tail=dt10)
    h2ov = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, "H2", "pre_tax")
    d_kov = float(np.max(np.abs(h2ov["_nav"] - ref_ov[0])))
    checks["h2_engine_equals_simulate_band_overlay"] = {
        "max_abs_nav_diff": d_kov, "dec_tail_sessions": int(len(dt10)),
        "deferred": int(h2ov["deferred"])}
    if d_kov > 1e-9:
        bad += 1
    comps_full = early_components(qqq_price())
    on_prod, fac_prod = early_state(qqq_price()[None, :], e2, comps_full, EARLY_PROD)
    on_ref, fac_ref = r2_state(qqq_price(), e2, comps_full)
    checks["early_state_prod_equals_r2_state"] = {
        "on_eff_equal": bool(np.array_equal(on_prod, on_ref)),
        "factor_equal": bool(np.array_equal(fac_prod, fac_ref))}
    if not (checks["early_state_prod_equals_r2_state"]["on_eff_equal"]
            and checks["early_state_prod_equals_r2_state"]["factor_equal"]):
        bad += 1
    # ---- the device's structural ordering on the real main series: the production early set is
    # contained in Q2's (drop `mom`) and Q2's in Q1's (the quorum). Both inclusions are logical
    # (same exit rule, strictly larger entry sets), so they gate; the Q1/Q3 relation is measured
    # and reported (Q3 shares Q1's entry but holds through one-condition dips, and Q1 can stay
    # active through a session Q3 has left).
    f_prod = early_flag(qqq_price(), e2, EARLY_PROD, comps_full)[0]
    f_q1 = early_flag(qqq_price(), e2, EARLY_SPEC["Q1"], comps_full)[0]
    f_q2 = early_flag(qqq_price(), e2, EARLY_SPEC["Q2"], comps_full)[0]
    f_q3 = early_flag(qqq_price(), e2, EARLY_SPEC["Q3"], comps_full)[0]
    checks["quorum_prod_subset_Q2"] = bool(np.all(f_q2 | ~f_prod))
    checks["quorum_Q2_subset_Q1"] = bool(np.all(f_q1 | ~f_q2))
    checks["quorum_Q2_strictly_tighter_than_Q1"] = bool(f_q1.sum() > f_q2.sum())
    if not (checks["quorum_prod_subset_Q2"] and checks["quorum_Q2_subset_Q1"]
            and checks["quorum_Q2_strictly_tighter_than_Q1"]):
        bad += 1
    hold_all = np.sum([comps_full[0],
                       (np.isfinite(comps_full[2]) & (comps_full[1] < comps_full[2])),
                       comps_full[3]], axis=0)[0]
    monotonicity = {"early_sessions": {"prod": int(f_prod.sum()), "Q1": int(f_q1.sum()),
                                       "Q2": int(f_q2.sum()), "Q3": int(f_q3.sum())},
                    "Q1_only_sessions": int((f_q1 & ~f_q3).sum()),
                    "Q3_only_sessions": int((f_q3 & ~f_q1).sum()),
                    "hold_counts": {str(h): int(np.sum(hold_all == h)) for h in (0, 1, 2, 3)}}
    # ---- K and C0 share production R2's target series; they differ only by K's fill rule
    checks["K_and_C0_share_target_series_and_differ_in_rule"] = {
        "max_abs_want_diff": float(np.max(np.abs(w_k - w_r2))),
        "K_rule_differs_from_C0_rule": bool(h2ov["deferred"] > 0)}
    kk = checks["K_and_C0_share_target_series_and_differ_in_rule"]
    if kk["max_abs_want_diff"] != 0.0 or not kk["K_rule_differs_from_C0_rule"]:
        bad += 1
    boot_control = {}
    out = {"reproduction_checks": checks, "n_outside_tolerance": bad, "after_tax_anchors": tax,
           "r2_anchors": r2_anchors, "k_anchors": k_anchors, "bootstrap_control": boot_control,
           "device_structure": monotonicity, "pass": bool(bad == 0)}
    if deep:
        out["deep"] = harness_deep()
        out["bootstrap_control"] = out["deep"]["k_bootstrap_anchors"]
        if not out["deep"]["pass"]:
            out["pass"] = False
    return out


def harness_deep() -> dict:
    """The parent campaigns' published cells re-run with this study's code (A1u against
    gain_band_v3/RESULTS.json, H2 against H2_ANCHORS.json) plus the clean bootstrap (seed
    20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths) that `run()` reuses as
    `boot_ref`. The same bootstrap supplies this study's control anchors: K's $100k medians and
    its deltas versus R2, compared against band_h2_combo_v1's published K cells. A mismatch is a
    bug in this study, not a result."""
    pre, pre_usd = pre_tax_main(STUDY_ARMS)
    win = after_tax_windows((GATE_CAPITAL,), STUDY_ARMS)
    cost = cost_stress(STUDY_ARMS)
    print(f"  deep harness: clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)",
          flush=True)
    boot_ref = boot(seed=SEED, block=BLOCK, arms=STUDY_ARMS, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
                    capitals=("100k", "1M"), modes=("progressive", "flat"),
                    deltas=("A1u", "H2", "K", "Q1", "Q2", "Q3"), baselines=("C0", "A1u", "K"),
                    label="clean")
    ref = reference_checks({"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost,
                            "boot_ref": boot_ref}, full=False)
    ref["boot_ref"] = boot_ref
    # ---- the control's bootstrap anchors (this study's own cells, from band_h2's RESULTS.json)
    bad = 0
    kboot = {}
    for mode in ("progressive", "flat"):
        c = boot_ref["tax"][GATE_CAPITAL][mode]
        kmed, kdelta, kshare = BOOT_ANCHOR_K[mode]
        kboot[mode] = {"cagr_median": c["K"]["cagr_median"], "reference_cagr_median": kmed,
                       "delta_vs_R2_pp": c["K_vs_C0"]["median_delta_cagr_pp"],
                       "reference_delta_pp": kdelta,
                       "share_gt_0": c["K_vs_C0"]["share_gt_0"], "reference_share": kshare,
                       "r2_cagr_median": c["C0"]["cagr_median"], "reference_r2_cagr_median":
                           BOOT_ANCHOR[mode]["R2"]}
        if (abs(kboot[mode]["cagr_median"] - kmed) > 1e-4
                or abs(kboot[mode]["delta_vs_R2_pp"] - kdelta) > 0.005
                or abs(kboot[mode]["share_gt_0"] - kshare) > 0.005):
            bad += 1
    for md in FIN:
        kboot[f"pre_{md}"] = {
            "K_cagr_median": boot_ref["pre"][md]["K"]["cagr_median"],
            "reference_K_cagr_median": REF_K_BOOT_PRE[md][0],
            "K_vs_R2_median_pp": boot_ref["pre"][md]["K"]["diff_vs_C0_median_pp"],
            "reference_K_vs_R2_median_pp": REF_K_BOOT_PRE[md][1],
            "R2_cagr_median": boot_ref["pre"][md]["C0"]["cagr_median"],
            "reference_R2_cagr_median": REF_K_BOOT_PRE[md][2]}
        if (abs(kboot[f"pre_{md}"]["K_cagr_median"] - REF_K_BOOT_PRE[md][0]) > 1e-4
                or abs(kboot[f"pre_{md}"]["K_vs_R2_median_pp"] - REF_K_BOOT_PRE[md][1]) > 0.005
                or abs(kboot[f"pre_{md}"]["R2_cagr_median"] - REF_K_BOOT_PRE[md][2]) > 1e-4):
            bad += 1
    kboot["anchor_e2"] = {mode: {"cagr_median": boot_ref["anchor_e2_100k"][mode]["cagr_median"],
                                 "reference": BOOT_ANCHOR[mode]["E2"]}
                          for mode in ("progressive", "flat")}
    for mode in ("progressive", "flat"):
        if abs(kboot["anchor_e2"][mode]["cagr_median"] - BOOT_ANCHOR[mode]["E2"]) > 1e-4:
            bad += 1
    ref["k_bootstrap_anchors"] = kboot
    if bad:
        ref["pass"] = False
    return ref


# ----------------------------------------------------------------------------- the run
def pre_tax_main(arms):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD main-window row."""
    days, o, c, e2, W0, _ = main_inputs(0.0, arms)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    usd_mkt = usd_market(days)
    pre, pre_usd = {}, {}
    for md, spread in FIN.items():
        d_, o_, c_, e_, W, _ = main_inputs(spread, arms)
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
    m = 2) on the three TAX_WINDOWS. Every arm label runs its own target series."""
    days, o, c, e2, WANTS, _ = main_inputs(0.0, arms)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
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
    days, o, c, e2, W, _ = main_inputs(0.0, arms)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    cost = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        cost[wn] = {}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, W[arm], e2, arm,
                            "progressive", m=M_STRESS)
            # v2's convention (see after_tax_windows): the pre-tax leg of the cost stress runs
            # at capital 1.0, so the reference check reproduces v2 exactly.
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


def oos_bootstrap(arms=(CONTROL,) + CANDIDATES):
    """Pooled OOS bootstrap (G8): block-20 over M1-M5, rng seed SEED, arms in order. G8 gates the
    candidates against the control, so only those arms are simulated."""
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, arms) for k in MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in arms}
    out = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        out["per_market"][k] = {a: {"diff_vs_C0_median_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)}
                                for a in arms}
    for a in arms:
        d = pooled[a] - pooled[CONTROL]
        out["pooled"][a] = {"diff_vs_C0_median_pp": float(np.median(d) * 100),
                            "diff_vs_C0_mean_pp": float(np.mean(d) * 100),
                            "share_gt_0": float(np.mean(d > 0)), "paths": int(len(d))}
    return out


def instrument_bootstrap(arms=(CONTROL,) + CANDIDATES):
    """Pooled instrument bootstrap (gate EI, part d): the same machinery on the 21 ETFs; only the
    control and the candidates are simulated (EI gates the candidates against the control)."""
    rng = np.random.default_rng(SEED)
    per = {k: boot_instrument(k, rng, arms) for k in INSTRUMENTS}
    pooled = {a: np.concatenate([per[k][a] for k in INSTRUMENTS]) for a in arms}
    out = {"paths_per_instrument": EI_PATHS, "per_instrument": {}, "pooled": {}}
    for k in INSTRUMENTS:
        out["per_instrument"][k] = {a: {"diff_vs_C0_median_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)}
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
        W = wants_by_arm(ic, e2, trv2.vol_n(sc)[0], arms)
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
    days, o, c, e2, W, _ = main_inputs(0.0, arms)
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


def stage_plateau(arms):
    """PLT: the plateau comparison block -- the six fast_reentry_v2 one-parameter neighbours of
    the production early path plus the three Q arms, all on K's execution layer, main window,
    base financing, m = 2, pre-tax. Report-only; PL (the gate) reads its three Q rows."""
    days, o, c, e2, W, _ = main_inputs(0.0, arms)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    out = {"window": {"start": days[START], "end": days[-1], "sessions": T - START + 1}, "rows": {}}
    for arm in arms:
        r = engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, W[arm], e2, arm, "pre_tax", m=M)
        st = pre_stats(days, START, T, r)
        s = EARLY_SPEC[arm]
        out["rows"][arm] = {
            "spec": {"conds": list(s["conds"]), "need": s["need"], "exit": s["exit"],
                     "sma": s["sma"], "med": s["med"], "weight": s["weight"]},
            "cagr": st["cagr"], "max_dd": st["max_dd"], "calmar": st["calmar"],
            "turnover_annual": st["turnover_annual"],
            "late_2017_cagr": st["2017_end"]["cagr"],
            "early_sessions": int(np.sum(early_flag(qqq_price(), e2, s)[0])),
        }
    ctrl = out["rows"][CONTROL]
    for arm in arms:
        r = out["rows"][arm]
        r["delta_cagr_pp"] = (r["cagr"] - ctrl["cagr"]) * 100 if arm != CONTROL else 0.0
        r["delta_max_dd_pp"] = (r["max_dd"] - ctrl["max_dd"]) * 100 if arm != CONTROL else 0.0
    return out


def stage_loeo(arms):
    """LOEO: for each of the three early episodes the survey names, the candidate's early-leg
    decisions are replaced by the control's over the published episode span (the entry-decision
    date -> last-held date of `fast_reentry_v1`'s per-episode table, mapped to sessions by date),
    and the main window (base, m = 2, pre-tax) is recomputed. Report-only."""
    days, o, c, e2, W, _ = main_inputs(0.0, tuple(arms) + (CONTROL,))
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    comps = early_components(qqq_price())
    ctrl_flag = early_flag(qqq_price(), e2, EARLY_SPEC[CONTROL], comps)[0]
    spans = {}
    for name, (d0, d1) in LOEO_EPISODES.items():
        i0, i1 = days.index(d0), days.index(d1)
        spans[name] = (i0, i1)
    ctrl_cagr = pre_stats(days, START, T, engine_run(PRE_CAPITAL, START, T, days, mkt, o, c,
                                                     W[CONTROL], e2, CONTROL, "pre_tax", m=M))["cagr"]
    out = {"window": {"start": days[START], "end": days[-1]},
           "episode_spans": {k: {"entry_decision": days[v[0]], "last_held": days[v[1]],
                                 "sessions": v[1] - v[0] + 1,
                                 "control_early_sessions_inside": int(ctrl_flag[v[0]:v[1] + 1].sum())}
                             for k, v in spans.items()},
           "control_cagr": ctrl_cagr, "loeo": {}}
    for arm in arms:
        s = EARLY_SPEC[arm]
        flag = early_flag(qqq_price(), e2, s, comps)[0]
        full = pre_stats(days, START, T, engine_run(PRE_CAPITAL, START, T, days, mkt, o, c,
                                                    W[arm], e2, arm, "pre_tax", m=M))["cagr"]
        rec = {"episodes": {}, "cagr_full": full, "delta_cagr_full_vs_K_pp": (full - ctrl_cagr) * 100,
               "early_sessions": int(flag.sum()),
               "early_sessions_inside_spans": {k: int(flag[v[0]:v[1] + 1].sum())
                                               for k, v in spans.items()}}
        for name, (i0, i1) in spans.items():
            f2 = flag.copy()
            f2[i0:i1 + 1] = ctrl_flag[i0:i1 + 1]
            e2b, f2b = np.atleast_2d(e2), f2[None, :]
            on_eff = f2b | e2b
            factor = np.where(e2b, 1.0, np.where(f2b, s["weight"], 0.0))
            w2 = sized_wants(on_eff, factor, trv2.vol_n(c)[0])[0]
            st = pre_stats(days, START, T, engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, w2, e2,
                                                      arm, "pre_tax", m=M))
            rec["episodes"][name] = {
                "cagr_with_episode_replaced": st["cagr"],
                "delta_cagr_vs_K_pp": (st["cagr"] - ctrl_cagr) * 100,
                "lost_cagr_pp": (full - st["cagr"]) * 100,
                "max_dd": st["max_dd"]}
        out["loeo"][arm] = rec
    return out


def stage_e5_decisions(arms):
    """E5 (report only): how often the arms' fill decisions differ on the main path
    (base financing, m = 2, $100k, R-INDEX, pre-tax so no tax-funding sales interfere).
    Decision code: 0 = no trade, 1 = up/entry/exit trade, 2 = downward resize."""
    days, o, c, e2, W, _ = main_inputs(0.0, arms)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    traces = {}
    for arm in arms:
        traces[arm] = engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, W[arm], e2, arm, "pre_tax",
                                 trace=True)
    out = {"window": {"start": days[START], "end": days[-1], "sessions": int(len(traces[CONTROL]["_dec"]))},
           "sells": {}, "postponements": {}, "early_sessions": {}}
    for arm in arms:
        r = traces[arm]
        out["sells"][arm] = int(np.sum(r["_dec"] == 2))
        out["postponements"][arm] = {"band": int(r["blocked_band"]), "deferred": int(r["deferred"])}
        out["early_sessions"][arm] = int(np.sum(early_flag(qqq_price(), e2, EARLY_SPEC[arm])[0]))
    for a, b in ((REFERENCE, CONTROL), (CONTROL, REFERENCE)) + tuple((q, CONTROL) for q in CANDIDATES):
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


def _parent_convention(rec, arm="A1u", ref=REFERENCE):
    """Re-express one of this study's stage records in the parent campaigns' published convention.

    The parents (gain_band_v3) publish their E1/E2/OOS/instrument cells as **arm-minus-C0** deltas
    (C0 = production R2); this study's stages publish **arm-minus-K**. Comparing the two raw would
    flag every delta cell as a mismatch even though the numbers agree exactly, so the study's
    deltas are shifted by its own C0 delta (`arm-K - (C0-K) = arm-C0`) -- the same convention fix
    amendment A2.3 applied to the bootstrap block. Only `delta_*` leaves are touched; levels,
    counters and windows are compared as published."""
    out = dict(rec[arm])
    for k, v in rec[ref].items():
        if k.startswith("delta_") and k in out:
            out[k] = out[k] - v
    return out


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
        for arm in ("A1u", "H2", "K"):
            if arm not in v3["pre_tax_main_r_usd"][md]:
                continue   # H2 and K are new here; v3 has no published cell to compare
            _compare_subtree(f"pre_usd.{md}.{arm}", res["pre_usd"][md][arm],
                             v3["pre_tax_main_r_usd"][md][arm], cells, only_mine, only_theirs)
    sections.append("after_tax_windows")
    for cl in TAX_CAPITALS:
        if cl not in res["windows"]:
            continue   # full=False (the deep harness) runs the gate capital only
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
            _compare_subtree(f"e1.{key}.A1u", _parent_convention(res["e1"][key]),
                             v3["E1_after_tax_etfs_rindex"][key]["A1u"], cells, only_mine, only_theirs)
        for yr in E2_START_YEARS:
            _compare_subtree(f"e2.{yr}.A1u", _parent_convention(res["e2"][str(yr)]),
                             v3["E2_rolling_starts"][str(yr)]["A1u"], cells, only_mine, only_theirs)
        for key in sorted(res.get("e3", {})):
            _compare_subtree(f"e3.{key}.A1u_vs_C0", res["e3"][key]["A1u_vs_C0"],
                             v3["E3_fresh_bootstraps"][key]["A1u_vs_C0"], cells, only_mine, only_theirs)
        for wn in OOS_WINDOWS:
            _compare_subtree(f"oos.{wn}.A1u", _parent_convention(res["oos"][wn]),
                             v3["oos"][wn]["A1u"], cells, only_mine, only_theirs)
        for key in res["instruments"]:
            _compare_subtree(f"instruments.{key}.A1u", _parent_convention(res["instruments"][key]),
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
            add(f"h2.boot.{cl}.{mode}.R2_cagr_median", a["C0"]["cagr_median"], b["R2_cagr_median"])
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
def gates_quorum(res):
    """Every candidate arm's gate block against the control K (PREREGISTRATION.md section 6).
    See the constants G1_-G9_/EI_/T1_/T2_/PL_ above for the thresholds."""
    pre, win = res["pre"], res["windows"][GATE_CAPITAL]
    bt, plt = res["boot_ref"], res["plt"]
    g = {}
    for arm in CANDIDATES:
        r = {}
        # ---- G1 / G6: return (m = 2 and m = 1, base and calibrated)
        for mkey in ("m1", "m2"):
            det, ok = {}, True
            for mdl in ("base", "calibrated"):
                a, k = pre[mdl][mkey][arm], pre[mdl][mkey][CONTROL]
                cagr_ok = bool(a["cagr"] >= k["cagr"])
                calmar_ok = bool(a["calmar"] >= G1_MIN_CALMAR_MULT * k["calmar"]
                                 and (a["cagr"] - k["cagr"]) * 100 >= G1_MIN_REL_CAGR_PP)
                det[mdl] = {"cagr": a["cagr"], "cagr_K": k["cagr"],
                            "delta_pp": (a["cagr"] - k["cagr"]) * 100,
                            "calmar": a["calmar"], "calmar_K": k["calmar"],
                            "pass": bool(cagr_ok or calmar_ok)}
                ok = ok and det[mdl]["pass"]
            r[f"G1_detail_{mkey}"] = det
            r["G6" if mkey == "m1" else "G1"] = bool(ok)
        # ---- G2: max DD (m = 2, base and calibrated)
        det, ok = {}, True
        for mdl in ("base", "calibrated"):
            a, k = pre[mdl]["m2"][arm], pre[mdl]["m2"][CONTROL]
            d = (a["max_dd"] - k["max_dd"]) * 100
            det[mdl] = {"max_dd": a["max_dd"], "max_dd_K": k["max_dd"], "delta_pp": d,
                        "pass": bool(d <= G2_MAX_DD_PP + 1e-9)}
            ok = ok and det[mdl]["pass"]
        r["G2_detail"] = det
        r["G2"] = bool(ok)
        # ---- G3: 2017+ CAGR (base and calibrated)
        det, ok = {}, True
        for mdl in ("base", "calibrated"):
            a = pre[mdl]["m2"][arm]["2017_end"]["cagr"]
            k = pre[mdl]["m2"][CONTROL]["2017_end"]["cagr"]
            d = (a - k) * 100
            det[mdl] = {"cagr_2017": a, "cagr_2017_K": k, "delta_pp": d,
                        "pass": bool(d >= G3_MIN_CAGR_PP)}
            ok = ok and det[mdl]["pass"]
        r["G3_detail"] = det
        r["G3"] = bool(ok)
        # ---- G4: sub-periods (base, m = 2)
        det = {}
        n_win = 0
        for nm in ("P1", "P2", "P3"):
            a = pre["base"]["m2"][arm]["subperiods"][nm]["cagr"]
            k = pre["base"]["m2"][CONTROL]["subperiods"][nm]["cagr"]
            d = (a - k) * 100
            det[nm] = {"cagr": a, "cagr_K": k, "delta_pp": d, "win": bool(d > 0)}
            n_win += int(d > 0)
        r["G4_detail"] = {"periods": det, "n_wins": n_win, "required": G4_MIN_WINS}
        r["G4"] = bool(n_win >= G4_MIN_WINS)
        # ---- G5: pre-tax block-20 bootstrap (base and calibrated)
        det, ok = {}, True
        for mdl in ("base", "calibrated"):
            c = bt["pre"][mdl][arm][f"{arm}_vs_{CONTROL}"]
            good = bool(c["median_delta_pp"] >= G5_MIN_MEDIAN_PP)
            det[mdl] = {"median_delta_pp": c["median_delta_pp"], "mean_delta_pp": c["mean_delta_pp"],
                        "share_gt_0": c["share_gt_0"], "p05_pp": c["p05_pp"],
                        "p95_pp": c["p95_pp"],
                        "cagr_median": bt["pre"][mdl][arm]["cagr_median"], "pass": good}
            ok = ok and good
        r["G5_detail"] = det
        r["G5"] = bool(ok)
        # ---- G7: the six OOS windows
        d_c = {wn: res["oos"][wn][arm]["delta_cagr_pp"] for wn in OOS_WINDOWS}
        d_d = {wn: res["oos"][wn][arm]["delta_max_dd_pp"] for wn in OOS_WINDOWS}
        good_w = [wn for wn in OOS_WINDOWS
                  if d_c[wn] >= G7_MIN_CAGR_PP and d_d[wn] <= G7_MAX_DD_PP]
        r["G7_detail"] = {"delta_cagr_pp": d_c, "delta_max_dd_pp": d_d,
                          "median_delta_pp": float(np.median(list(d_c.values()))),
                          "n_within": len(good_w), "required_windows": G7_MIN_WINDOWS,
                          "max_delta_max_dd_pp": float(max(d_d.values()))}
        r["G7"] = bool(np.median(list(d_c.values())) >= G7_MIN_MEDIAN_PP
                       and len(good_w) >= G7_MIN_WINDOWS
                       and max(d_d.values()) <= G7_MAX_DD_HARD_PP)
        # ---- G8: pooled OOS bootstrap
        pb = res["oos_boot"]["pooled"][arm]
        r["G8_detail"] = {"median_delta_pp": pb["diff_vs_C0_median_pp"],
                          "mean_delta_pp": pb["diff_vs_C0_mean_pp"], "share_gt_0": pb["share_gt_0"],
                          "paths": pb["paths"]}
        r["G8"] = bool(pb["diff_vs_C0_median_pp"] >= G8_MIN_MEDIAN_PP)
        # ---- G9: turnover
        t_a = pre["base"]["m2"][arm]["turnover_annual"]
        t_k = pre["base"]["m2"][CONTROL]["turnover_annual"]
        r["G9_detail"] = {"turnover": t_a, "turnover_K": t_k, "ratio": t_a / t_k,
                          "ratio_vs_K_1.0": t_a / t_k <= 1.0 + 1e-9}
        r["G9"] = bool(t_a / t_k <= G9_MAX_TURNOVER_MULT + 1e-9)
        # ---- EI: the 21 quality-passing ETFs (pre-tax, calibrated, m = 2, own target series)
        ds = [res["instruments"][k][arm]["delta_cagr_pp"] for k in INSTRUMENTS]
        dds = [res["instruments"][k][arm]["delta_max_dd_pp"] for k in INSTRUMENTS]
        n_pos = int(sum(1 for d in ds if d > 0))
        n_dd = int(sum(1 for d in dds if d <= EI_MAX_DD_PP + 1e-9))
        ip = res["instr_boot"]["pooled"][arm]
        r["EI_detail"] = {"n_instruments": len(ds), "median_delta_cagr_pp": float(np.median(ds)),
                          "share_gt_0": n_pos / len(ds), "n_gt_0": n_pos,
                          "min_delta_cagr_pp": float(min(ds)), "max_delta_cagr_pp": float(max(ds)),
                          "share_dd_within": n_dd / len(dds), "n_dd_within": n_dd,
                          "worst_delta_max_dd_pp": float(max(dds)),
                          "boot_median_delta_pp": ip["diff_vs_C0_median_pp"],
                          "boot_share_gt_0": ip["share_gt_0"], "boot_paths": ip["paths"]}
        r["EI"] = bool(np.median(ds) >= 0.0 and n_pos / len(ds) >= EI_MIN_SHARE
                       and n_dd / len(dds) >= EI_MIN_DD_SHARE
                       and ip["diff_vs_C0_median_pp"] >= 0.0)
        # ---- T1: after tax at $100k in all three windows
        t1 = {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                   "after_tax_cagr_K": win[wn][CONTROL]["after_tax_cagr"],
                   "delta_pp": (win[wn][arm]["after_tax_cagr"]
                                - win[wn][CONTROL]["after_tax_cagr"]) * 100}
              for wn in TAX_WINDOWS}
        r["T1_detail"] = {"windows": t1, "min_delta_pp": float(min(v["delta_pp"] for v in t1.values())),
                          "tax_paid_usd": win[TAX_WINDOWS_LAST][arm]["tax_usd"],
                          "sells_per_year": win[TAX_WINDOWS_LAST][arm]["sells_per_year"],
                          "max_dd_after_tax": {wn: win[wn][arm]["max_dd_after_tax"] for wn in TAX_WINDOWS},
                          "tax_relief_try": win[TAX_WINDOWS_LAST][arm]["index_relief_try"],
                          "postponements": win[TAX_WINDOWS_LAST][arm]["postponements"]}
        r["T1"] = bool(all(v["delta_pp"] >= T1_MIN_DELTA_PP for v in t1.values()))
        # ---- T2 / T3: the two clean bootstraps at $100k
        for mode, key in (("progressive", "T2"), ("flat", "T3")):
            c = bt["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{CONTROL}"]
            good = bool(c["median_delta_cagr_pp"] >= (T2_MIN_MEDIAN_PP if mode == "progressive"
                                                      else T2_MIN_MEDIAN_PP)
                        and c["share_gt_0"] >= T2_MIN_SHARE)
            r[f"{key}_detail"] = {"mode": mode, "median_delta_pp": c["median_delta_cagr_pp"],
                                  "mean_delta_pp": c["mean_delta_cagr_pp"],
                                  "share_gt_0": c["share_gt_0"], "p05_pp": c["p05_delta_cagr_pp"],
                                  "p95_pp": c["p95_delta_cagr_pp"], "pass": good,
                                  "cagr_median": bt["tax"][GATE_CAPITAL][mode][arm]["cagr_median"]}
            r[key] = bool(good)
        # ---- PL: the plateau criterion over the device's three configurations
        rows = plt["rows"]
        pl = {q: {"delta_cagr_pp": rows[q]["delta_cagr_pp"],
                  "delta_max_dd_pp": rows[q]["delta_max_dd_pp"],
                  "pass": bool(rows[q]["delta_cagr_pp"] > PL_MIN_DELTA_PP
                               and rows[q]["delta_max_dd_pp"] <= PL_MAX_DD_PP + 1e-9)}
              for q in CANDIDATES}
        r["PL_detail"] = {"arms": pl, "this_arm": pl[arm],
                          "neighbours_delta_cagr_pp": {n: rows[n]["delta_cagr_pp"] for n in NEIGHBOURS},
                          "neighbours_delta_max_dd_pp": {n: rows[n]["delta_max_dd_pp"] for n in NEIGHBOURS},
                          "neighbours_positive_share": float(np.mean(
                              [rows[n]["delta_cagr_pp"] > 0 for n in NEIGHBOURS]))}
        r["PL"] = bool(pl[arm]["pass"])
        # ---- the record
        r["vs_K"] = {"pre_tax": {mdl: {m_: {"cagr": pre[mdl][m_][arm]["cagr"],
                                            "max_dd": pre[mdl][m_][arm]["max_dd"],
                                            "turnover_annual": pre[mdl][m_][arm]["turnover_annual"]}
                                    for m_ in ("m1", "m2", "m4")} for mdl in ("base", "calibrated")},
                     "after_tax_100k": {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                                             "delta_vs_K_pp": t1[wn]["delta_pp"],
                                             "pre_tax_cagr": win[wn][arm]["pre_tax_cagr"],
                                             "max_dd_after_tax": win[wn][arm]["max_dd_after_tax"],
                                             "tax_usd": win[wn][arm]["tax_usd"],
                                             "sells_per_year": win[wn][arm]["sells_per_year"]}
                                        for wn in TAX_WINDOWS},
                     "oos_median_delta_pp": float(np.median(list(d_c.values()))),
                     "ei_median_delta_cagr_pp": float(np.median(ds)),
                     "rolling_starts_after_tax": {
                         "n": len(res["e2"]),
                         "n_ge_0": int(sum(1 for y in E2_START_YEARS
                                           if res["e2"][str(y)][arm]["delta_after_tax_pp"] >= 0)),
                         "share": float(np.mean([res["e2"][str(y)][arm]["delta_after_tax_pp"] >= 0
                                                 for y in E2_START_YEARS])),
                         "median_delta_pp": float(np.median(
                             [res["e2"][str(y)][arm]["delta_after_tax_pp"] for y in E2_START_YEARS])),
                         "min_delta_pp": float(min(
                             res["e2"][str(y)][arm]["delta_after_tax_pp"] for y in E2_START_YEARS)),
                         "deltas_pp": {str(y): res["e2"][str(y)][arm]["delta_after_tax_pp"]
                                       for y in E2_START_YEARS}},
                     "e1_after_tax_etfs": {"median_delta_pp": float(np.median(
                         [res["e1"][k][arm]["delta_after_tax_pp"] for k in INSTRUMENTS])),
                         "share_gt_0": float(np.mean(
                             [res["e1"][k][arm]["delta_after_tax_pp"] > 0 for k in INSTRUMENTS]))},
                     "loeo": res["loeo"]["loeo"][arm],
                     "early_sessions": res["e5"]["decisions"]["early_sessions"][arm]}
        g[arm] = r
    # ---- the selection rule
    keys = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "EI", "T1", "T2", "T3", "PL")
    passed = [a for a in CANDIDATES if all(g[a][k] for k in keys)]
    if passed:
        rank = {a: float(np.mean([g[a]["T1_detail"]["windows"][wn]["delta_pp"]
                                  for wn in TAX_WINDOWS])) for a in passed}
        adopted = max(passed, key=lambda a: (rank[a], g[a]["T2_detail"]["median_delta_pp"]))
        reason = (f"{adopted} passes all of {'/'.join(keys)}"
                  + (f"; also passing: {', '.join(x for x in passed if x != adopted)}"
                     if len(passed) > 1 else "")
                  + f" (rank by mean after-tax $100k delta {rank[adopted]:+.3f} pp)")
    else:
        adopted = CONTROL
        reason = ("no candidate clears every gate: "
                  + "; ".join(a + " fails " + ",".join(k for k in keys if not g[a][k])
                              for a in CANDIDATES)
                  + f" -> production keeps {CONTROL}")
    return {"arms": g, "adopted": adopted, "reason": reason, "gate_keys": list(keys),
            "gate": {
                "G1": f"m = 2 return: CAGR >= K's, or Calmar >= {G1_MIN_CALMAR_MULT} x K's with "
                      f"CAGR >= K {G1_MIN_REL_CAGR_PP} pp (base and calibrated)",
                "G2": f"max DD <= K + {G2_MAX_DD_PP} pp (base and calibrated, m = 2)",
                "G3": f"2017+ CAGR >= K {G3_MIN_CAGR_PP} pp (base and calibrated)",
                "G4": f"CAGR above K in >= {G4_MIN_WINS} of the 3 sub-periods",
                "G5": f"block-20 bootstrap median (base and calibrated) >= {G5_MIN_MEDIAN_PP} pp",
                "G6": G6,
                "G7": f"six OOS windows: median >= {G7_MIN_MEDIAN_PP} pp; >= {G7_MIN_WINDOWS}/6 with "
                      f"delta CAGR >= {G7_MIN_CAGR_PP} pp and delta DD <= {G7_MAX_DD_PP} pp; "
                      f"delta DD never above {G7_MAX_DD_HARD_PP} pp",
                "G8": f"pooled OOS bootstrap median >= {G8_MIN_MEDIAN_PP} pp",
                "G9": f"turnover <= {G9_MAX_TURNOVER_MULT} x K",
                "EI": f"21 ETFs: median delta CAGR >= 0, share > 0 >= {EI_MIN_SHARE:.0%}, "
                      f"share delta DD <= {EI_MAX_DD_PP} pp >= {EI_MIN_DD_SHARE:.0%}, pooled "
                      f"bootstrap median >= 0 ({EI_PATHS} paths/instrument)",
                "T1": f"after tax at $100k: arm - K >= {T1_MIN_DELTA_PP} pp in all three windows",
                "T2": f"B1-recent bootstrap median >= {T2_MIN_MEDIAN_PP} pp and share > 0 >= {T2_MIN_SHARE:.0%}",
                "T3": f"B2 flat-20% bootstrap median >= {T2_MIN_MEDIAN_PP} pp and share > 0 >= {T2_MIN_SHARE:.0%}",
                "PL": f"plateau: every Q arm has base-m2 delta CAGR > {PL_MIN_DELTA_PP} pp and "
                      f"delta max DD <= {PL_MAX_DD_PP} pp",
                "adopt": "a candidate is adopted only if every gate passes; ties by mean "
                         "after-tax $100k delta, then B1 median; else production keeps K"}}
# ----------------------------------------------------------------------------- the run
def run(boot_ref=None) -> dict:
    print(f"pre-tax main window ({len(STUDY_ARMS)} arms)", flush=True)
    pre, pre_usd = pre_tax_main(STUDY_ARMS)
    print(f"after-tax windows ($100k)", flush=True)
    win = after_tax_windows((GATE_CAPITAL,), STUDY_ARMS)
    print("cost stress m = 4", flush=True)
    cost = cost_stress(STUDY_ARMS)
    print("OOS windows", flush=True)
    oos = oos_stages(STUDY_ARMS)
    print("instruments (R-USD, pre-tax)", flush=True)
    inst, quality = instrument_stages_usd(STUDY_ARMS)
    print("plateau comparison block", flush=True)
    plt = stage_plateau(ARMS_ALL)
    if boot_ref is None:
        print(f"clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)", flush=True)
        boot_ref = boot(seed=SEED, block=BLOCK, arms=STUDY_ARMS, n_pre=PRE_BOOT_PATHS,
                        n_tax=TAX_BOOT_PATHS, capitals=("100k", "1M"),
                        modes=("progressive", "flat"),
                        deltas=("A1u", "H2", "K", "Q1", "Q2", "Q3"), baselines=("C0", "A1u", "K"),
                        label="clean")
    print("E1: after-tax 21 ETFs (R-INDEX)", flush=True)
    e1 = stage_e1(STUDY_ARMS)
    print("E2: rolling January starts", flush=True)
    e2 = stage_e2(STUDY_ARMS)
    print("E5: decision traces", flush=True)
    e5 = {"decisions": stage_e5_decisions(STUDY_ARMS)}
    print("LOEO: leave-one-episode-out (2001-10, 2002-11, 2023-01)", flush=True)
    loeo = stage_loeo(CANDIDATES)
    print("G8: pooled OOS bootstrap", flush=True)
    oos_boot = oos_bootstrap()
    print(f"EI: pooled instrument bootstrap ({EI_PATHS} paths/instrument)", flush=True)
    instr_boot = instrument_bootstrap()
    res = {"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost, "oos": oos,
           "instruments": inst, "quality": quality, "plt": plt, "boot_ref": boot_ref,
           "e1": e1, "e2": e2, "e5": e5, "loeo": loeo, "oos_boot": oos_boot,
           "instr_boot": instr_boot}
    print("reference reproduction checks (A1u vs gain_band_v3, H2 vs tax_harvest_v2)", flush=True)
    res["reference_check"] = reference_checks(res)
    res["gates"] = gates_quorum(res)
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

    hs = harness(deep=True)
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
    kb = hs["bootstrap_control"]
    for mode_ in ("progressive", "flat"):
        kk = kb[mode_]
        print(f"boot anchor {mode_} K: median ref {kk['reference_cagr_median']:.6f} "
              f"repro {kk['cagr_median']:.6f} | K-R2 ref {kk['reference_delta_pp']:+.4f}pp "
              f"repro {kk['delta_vs_R2_pp']:+.4f}pp | share ref {kk['reference_share']:.3f} "
              f"repro {kk['share_gt_0']:.3f} | R2 median ref {kk['reference_r2_cagr_median']:.6f} "
              f"repro {kk['r2_cagr_median']:.6f}")
    for md in FIN:
        print(f"boot anchor pre {md}: K {kb[f'pre_{md}']['K_cagr_median']:.6f} "
              f"(ref {kb[f'pre_{md}']['reference_K_cagr_median']:.6f}), R2 "
              f"{kb[f'pre_{md}']['R2_cagr_median']:.6f} "
              f"(ref {kb[f'pre_{md}']['reference_R2_cagr_median']:.6f}), K-R2 "
              f"{kb[f'pre_{md}']['K_vs_R2_median_pp']:+.4f}pp "
              f"(ref {kb[f'pre_{md}']['reference_K_vs_R2_median_pp']:+.4f}pp)")
    for mode_ in ("progressive", "flat"):
        print(f"boot anchor {mode_} E2: {kb['anchor_e2'][mode_]['cagr_median']:.6f} "
              f"(ref {kb['anchor_e2'][mode_]['reference']:.6f})")
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
    print(f"R2 anchor block (the reference row): {'PASS' if ra['pass'] else 'FAIL'}")
    ka = hs["k_anchors"]
    for k in sorted(ka["pre_tax"]):
        v = ka["pre_tax"][k]
        print(f"K anchor pre_tax {k}: cagr ref {v['reference_cagr'] * 100:.4f}% "
              f"repro {v['reproduced_cagr'] * 100:.4f}% | maxDD ref {v['reference_max_dd'] * 100:.4f}% "
              f"repro {v['reproduced_max_dd'] * 100:.4f}% | turnover ref {v['reference_turnover']:.4f} "
              f"repro {v['reproduced_turnover']:.4f}")
    for wn in TAX_WINDOWS:
        v = ka["after_tax_100k"][wn]
        print(f"K anchor after_tax {wn}: ref {[f'{x * 100:.3f}%' for x in v['reference']]} "
              f"repro {[f'{x * 100:.3f}%' for x in v['reproduced']]} -> {'PASS' if v['pass'] else 'FAIL'}")
    print(f"K anchor block (the control label the run uses): {'PASS' if ka['pass'] else 'FAIL'}")
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
    pre, win, e1, e2 = res["pre"], res["windows"], res["e1"], res["e2"]
    boot_ref, plt, loeo = res["boot_ref"], res["plt"], res["loeo"]
    ctrl = win[GATE_CAPITAL]

    out = {
        "campaign": "early_quorum_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "reference_row": REFERENCE,
            "candidates": list(CANDIDATES), "plateau_neighbours": list(NEIGHBOURS),
            "rule": RULE, "dec_tail": DEC_TAIL,
            "gain_tiers": [[t, b] for t, b in GAIN_TIERS], "b_up": B_UP,
            "early_specs": {a: {"conds": list(s["conds"]), "need": s["need"], "exit": s["exit"],
                                "sma": s["sma"], "med": s["med"], "weight": s["weight"]}
                            for a, s in EARLY_SPEC.items()},
            "early_leg": ("all arms keep production K's execution layer; the candidates change "
                          "only the early re-entry leg's entry/exit condition"),
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
            "loeo_episodes": {k: list(v) for k, v in LOEO_EPISODES.items()},
            "after_tax": {"engine": "tax_engine.py (byte copy of band_h2_combo_v1/tax_engine.py)",
                          "financing": "base", "m": M, "windows": TAX_WINDOWS,
                          "capitals": TAX_CAPITALS, "gate_capital": GATE_CAPITAL,
                          "clean_bootstrap": {"paths_pre": PRE_BOOT_PATHS, "paths_tax": TAX_BOOT_PATHS,
                                              "seed": SEED, "block": BLOCK,
                                              "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI}},
            "e1": {"rule": "R-INDEX", "start": E1_RINDEX_START, "financing": "calibrated", "m": M,
                   "capital": GATE_CAPITAL, "n_instruments": len(INSTRUMENTS),
                   "excluded": list(EXCLUDED_INSTRUMENTS)},
            "e2": {"starts": list(E2_START_YEARS), "capital": GATE_CAPITAL},
            "ei_bootstrap": {"paths_per_instrument": EI_PATHS, "seed": SEED, "block": BLOCK},
            "gates": res["gates"]["gate"],
            "adopted": res["gates"]["adopted"], "reason": res["gates"]["reason"],
        },
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "reproduction_checks": hs["reproduction_checks"],
                    "device_structure": hs["device_structure"],
                    "r2_anchors": hs["r2_anchors"], "k_anchors": hs["k_anchors"],
                    "after_tax_anchors": hs["after_tax_anchors"],
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
        "plateau_block": plt,
        "bootstrap_clean_seed20261011": boot_ref,
        "oos_bootstrap_pooled": res["oos_boot"],
        "instrument_bootstrap_pooled": res["instr_boot"],
        "E1_after_tax_etfs_rindex": e1,
        "E2_rolling_starts": e2,
        "E5": res["e5"],
        "LOEO": loeo,
        "gates": res["gates"],
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = fmt_pct
    w = pre["window"]
    keys = res["gates"]["gate_keys"]
    L = [f"early_quorum_v1 | the early re-entry leg's trigger quorum (idea I2) on production K | "
         f"main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | control = production K "
         f"(A1u band + H2) | arms = K (control) + C0 (R2 reference) + Q1 + Q2 + Q3",
         f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance); "
         f"K anchors {'PASS' if hs['k_anchors']['pass'] else 'FAIL'} "
         f"(pre-tax base m2 {hs['k_anchors']['pre_tax']['base_m2']['reproduced_cagr'] * 100:.4f}%); "
         f"R2 reference anchors {'PASS' if hs['r2_anchors']['pass'] else 'FAIL'}; "
         f"H2 engine == simulate_band_overlay max|diff| "
         f"{hs['reproduction_checks']['h2_engine_equals_simulate_band_overlay']['max_abs_nav_diff']:.2e}; "
         f"early_state(prod) == r2_state "
         f"{hs['reproduction_checks']['early_state_prod_equals_r2_state']['on_eff_equal']}",
         f"early sessions on the main path (pre-tax, m = 2): "
         + ", ".join(f"{a} {res['e5']['decisions']['early_sessions'][a]}" for a in STUDY_ARMS)
         + " | plateau block: "
         + ", ".join(f"{a} {plt['rows'][a]['early_sessions']}" for a in CANDIDATES + NEIGHBOURS),
         f"device structure on the QQQ close (the pre-registered Q1/Q3 crossing measurement): "
         f"Q1-only {hs['device_structure']['Q1_only_sessions']} session(s), Q3-only "
         f"{hs['device_structure']['Q3_only_sessions']}, hold-count histogram "
         f"{hs['device_structure']['hold_counts']}",
         f"deep reference block: {rc['n_cells']} cells ({', '.join(rc['sections'])}), "
         f"{rc['n_mismatch']} mismatches (tol {rc['tolerance']}), max|diff| {rc['max_abs_diff']:.2e} -> "
         f"{'PASS' if rc['pass'] else 'FAIL'}",
         f"ADOPTED: {res['gates']['adopted']} -- {res['gates']['reason']}", ""]

    L.append("per arm (pre-tax main window m = 2, R-INDEX; after tax at $100k, base, m = 2, liquidated; "
             "deltas vs the control K):")
    L.append("  arm   CAGR base  CAGR cal   maxDD base  maxDD cal   turn/yr | dAT2000 dAT2010 dAT2017 "
             "| boot B1/B2 | EI med | OOS med | gates")
    for arm in ARMS:
        a, ac = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        ats = "".join(f"{(ctrl[wn][arm]['after_tax_cagr'] - ctrl[wn][CONTROL]['after_tax_cagr']) * 100:+8.3f}"
                      for wn in TAX_WINDOWS)
        # the reference row C0 is not in the tax block's `deltas` (it is the parent campaign's own
        # baseline, present there as K_vs_C0), so its B1/B2 cells are reported as "-", like K's.
        bts = "".join((f"{boot_ref['tax'][GATE_CAPITAL][md][f'{arm}_vs_{CONTROL}']['median_delta_cagr_pp']:+8.3f}"
                       if arm not in (CONTROL, REFERENCE) else "       -")
                      for md in ("progressive", "flat"))
        ei = (0.0 if arm == CONTROL else
              float(np.median([res["instruments"][k][arm]["delta_cagr_pp"] for k in INSTRUMENTS])))
        oo = (0.0 if arm == CONTROL else
              float(np.median([res["oos"][wn][arm].get("delta_cagr_pp", 0.0) for wn in OOS_WINDOWS])))
        fl = ("          -" if arm not in CANDIDATES
              else f"{sum(1 for x in keys if g[arm][x])}/{len(keys)}")
        L.append(f"  {arm:4s} {p(a['cagr'])} {p(ac['cagr'])} {p(a['max_dd'])} {p(ac['max_dd'])} "
                 f"{a['turnover_annual']:9.2f} |{ats} |{bts} | {ei:+6.3f} | {oo:+7.3f} | {fl:>9s}")
    L.append("")
    L.append(f"gates (every one must pass; {'/'.join(keys)}):")
    for x in keys:
        L.append(f"  {x:3s} " + "  ".join(f"{q}={'PASS' if g[q][x] else 'FAIL'}" for q in CANDIDATES))
    for arm in CANDIDATES:
        r = g[arm]
        L.append(f"  --- {arm}")
        for mkey, gk in (("m2", "G1"), ("m1", "G6")):
            L.append(f"      {gk} return {mkey}: base {r[f'G1_detail_{mkey}']['base']['delta_pp']:+.3f}pp "
                     f"(calmar ratio {r[f'G1_detail_{mkey}']['base']['calmar'] / r[f'G1_detail_{mkey}']['base']['calmar_K']:.3f}), "
                     f"cal {r[f'G1_detail_{mkey}']['calibrated']['delta_pp']:+.3f}pp "
                     f"(calmar ratio {r[f'G1_detail_{mkey}']['calibrated']['calmar'] / r[f'G1_detail_{mkey}']['calibrated']['calmar_K']:.3f})")
        L.append("      G2 DD: " + ", ".join(
            f"{mdl} {r['G2_detail'][mdl]['delta_pp']:+.2f}pp" for mdl in ("base", "calibrated")))
        L.append("      G3 2017+: " + ", ".join(
            f"{mdl} {r['G3_detail'][mdl]['delta_pp']:+.3f}pp" for mdl in ("base", "calibrated")))
        L.append(f"      G4 subperiods: {r['G4_detail']['n_wins']}/{3} "
                 + ", ".join(f"{k} {v['delta_pp']:+.2f}pp" for k, v in r["G4_detail"]["periods"].items()))
        L.append(f"      G5 pre-tax bootstrap: base {r['G5_detail']['base']['median_delta_pp']:+.3f}pp "
                 f"(share>0 {r['G5_detail']['base']['share_gt_0'] * 100:.1f}%), cal "
                 f"{r['G5_detail']['calibrated']['median_delta_pp']:+.3f}pp "
                 f"(share>0 {r['G5_detail']['calibrated']['share_gt_0'] * 100:.1f}%)")
        L.append(f"      G7 OOS: median {r['G7_detail']['median_delta_pp']:+.3f}pp, "
                 f"{r['G7_detail']['n_within']}/6 within, worst delta DD {r['G7_detail']['max_delta_max_dd_pp']:+.2f}pp")
        L.append(f"      G8 pooled OOS bootstrap: median {r['G8_detail']['median_delta_pp']:+.3f}pp "
                 f"(share>0 {r['G8_detail']['share_gt_0'] * 100:.1f}%, {r['G8_detail']['paths']} paths)")
        L.append(f"      G9 turnover ratio {r['G9_detail']['ratio']:.3f} "
                 f"(<= 1.0: {r['G9_detail']['ratio_vs_K_1.0']})")
        L.append(f"      EI 21 ETFs: median {r['EI_detail']['median_delta_cagr_pp']:+.3f}pp "
                 f"share>0 {r['EI_detail']['share_gt_0'] * 100:.1f}% "
                 f"({r['EI_detail']['n_gt_0']}/{r['EI_detail']['n_instruments']}), "
                 f"share dDD <= 3pp {r['EI_detail']['share_dd_within'] * 100:.1f}%, "
                 f"boot median {r['EI_detail']['boot_median_delta_pp']:+.3f}pp")
        L.append("      T1 after tax $100k: " + ", ".join(
            f"{wn} {r['T1_detail']['windows'][wn]['delta_pp']:+.3f}pp" for wn in TAX_WINDOWS)
            + f" | tax paid ${r['T1_detail']['tax_paid_usd']:,.0f} vs K "
              f"${ctrl[TAX_WINDOWS_LAST][CONTROL]['tax_usd']:,.0f} (2017 window), "
              f"sells/yr {r['T1_detail']['sells_per_year']:.2f} vs "
              f"{ctrl[TAX_WINDOWS_LAST][CONTROL]['sells_per_year']:.2f}")
        for md_, kk in (("progressive", "T2"), ("flat", "T3")):
            c = r[f"{kk}_detail"]
            L.append(f"      {kk} {md_} bootstrap: median {c['median_delta_pp']:+.3f}pp "
                     f"mean {c['mean_delta_pp']:+.3f}pp share>0 {c['share_gt_0'] * 100:.1f}% "
                     f"p05/p95 {c['p05_pp']:+.2f}/{c['p95_pp']:+.2f}")
        L.append(f"      PL plateau: delta CAGR base-m2 {r['PL_detail']['this_arm']['delta_cagr_pp']:+.3f}pp, "
                 f"delta DD {r['PL_detail']['this_arm']['delta_max_dd_pp']:+.2f}pp")
    L.append("")
    L.append("plateau comparison block (main window, base, m = 2, pre-tax; delta vs K) -- the "
             "fast_reentry_v2 one-parameter neighbours plus the three Q arms:")
    L.append("  arm    spec                                          early sess   CAGR     dCAGR     maxDD   dDD")
    for arm in CANDIDATES + NEIGHBOURS:
        row = plt["rows"][arm]
        s = row["spec"]
        spec = (f"conds={','.join(s['conds'])} need={s['need']} exit={s['exit']} "
                f"sma={s['sma']} med={s['med']} w={s['weight']:.4f}")
        L.append(f"  {arm:5s}  {spec:46s} {row['early_sessions']:6d}  {p(row['cagr'])} "
                 f"{row['delta_cagr_pp']:+8.3f} {p(row['max_dd'])} {row['delta_max_dd_pp']:+7.2f}")
    L.append(f"  K      production (reference row)                     "
             f"{plt['rows'][CONTROL]['early_sessions']:6d}  {p(plt['rows'][CONTROL]['cagr'])} "
             f"{0.0:+8.3f} {p(plt['rows'][CONTROL]['max_dd'])} {0.0:+7.2f}")
    L.append("")
    L.append("leave-one-episode-out (the candidate's early decisions replaced by K's over the "
             "published episode span; main window, base, m = 2, pre-tax):")
    L.append(f"  control K CAGR {p(loeo['control_cagr'])}; episodes "
             + ", ".join(f"{k} {v['entry_decision']}..{v['last_held']} ({v['sessions']}s, K early "
                         f"inside {v['control_early_sessions_inside']}s)"
                         for k, v in loeo["episode_spans"].items()))
    for arm in CANDIDATES:
        rec = loeo["loeo"][arm]
        L.append(f"  {arm}: full {p(rec['cagr_full'])} ({rec['delta_cagr_full_vs_K_pp']:+.3f}pp vs K), "
                 f"{rec['early_sessions']} early sessions | "
                 + " | ".join(f"{k} replaced: {p(v['cagr_with_episode_replaced'])} "
                              f"({v['delta_cagr_vs_K_pp']:+.3f}pp, lost {v['lost_cagr_pp']:.3f}pp)"
                              for k, v in rec["episodes"].items()))
    L.append("")
    L.append("postponement counters (main window, m = 2, base, $100k, progressive):")
    for arm in ARMS:
        rr = ctrl[TAX_WINDOWS_LAST][arm]
        L.append(f"  {arm:4s} postponements {rr['postponements']:5d} (band {rr['blocked_band']:5d}, "
                 f"December-deferred {rr['deferred']:4d}) sells/yr {rr['sells_per_year']:.2f}")
    L.append("")
    L.append("E1 after tax on the 21 quality-passing ETFs (R-INDEX, $100k, calibrated, m = 2, "
             "liquidated) -- reporting block vs K:")
    L.append("  instrument  window                     K AT      C0 d      Q1 d      Q2 d      Q3 d")
    for key in INSTRUMENTS:
        rr = e1[key]
        L.append(f"  {key:10s}  {rr['window']['start']}..{rr['window']['end']} {p(rr[CONTROL]['after_tax_cagr'])}"
                 f"  {rr[REFERENCE]['delta_after_tax_pp']:+8.3f}  "
                 + "  ".join(f"{rr[q]['delta_after_tax_pp']:+8.3f}" for q in CANDIDATES))
    L.append(f"  E1 Q arms: " + ", ".join(
        f"{q} median {float(np.median([e1[k][q]['delta_after_tax_pp'] for k in INSTRUMENTS])):+.3f}pp "
        f"share>0 {np.mean([e1[k][q]['delta_after_tax_pp'] > 0 for k in INSTRUMENTS]) * 100:.1f}%"
        for q in CANDIDATES) + " (vs K, report only)")
    L.append("")
    L.append("E2 rolling January starts (after tax $100k, base, m = 2, to 2026-09-29):")
    L.append("  year   start        K AT      C0 d      Q1 d      Q2 d      Q3 d")
    for yr in E2_START_YEARS:
        rr = e2[str(yr)]
        L.append(f"  {yr}   {rr['start']}  {p(rr[CONTROL]['after_tax_cagr'])} "
                 f"{rr[REFERENCE]['delta_after_tax_pp']:+8.3f}  "
                 + "  ".join(f"{rr[q]['delta_after_tax_pp']:+8.3f}" for q in CANDIDATES))
    for q in CANDIDATES:
        rs = g[q]["vs_K"]["rolling_starts_after_tax"]
        L.append(f"  E2 {q}: n>=0 {rs['n_ge_0']}/{rs['n']} ({rs['share'] * 100:.1f}%), "
                 f"median {rs['median_delta_pp']:+.3f}pp, min {rs['min_delta_pp']:+.3f}pp (report only)")
    L.append("")
    L.append("E5 (report only): fill-decision differences on the main path (pre-tax, $100k, m = 2)")
    dec = res["e5"]["decisions"]
    for pair in [f"{a}_vs_{b}" for a, b in ((REFERENCE, CONTROL), (CONTROL, REFERENCE))
                 + tuple((q, CONTROL) for q in CANDIDATES)]:
        a, b = pair.split("_vs_")
        d = dec[pair]
        L.append(f"  {pair:12s}: {d['sessions_decision_differs']:5d} sessions differ "
                 f"({d['share_decision_differs'] * 100:.2f}%); {a} sells where {b} holds "
                 f"{d[f'{a}_sells_{b}_holds']}, {a} holds where {b} sells {d[f'{a}_holds_{b}_sells']}")
    L.append("  sells (downward resizes+exits) per arm: "
             + ", ".join(f"{a} {dec['sells'][a]}" for a in ARMS))
    L.append("")
    L.append("m = 4 cost stress, after tax at $100k (delta vs K):")
    for wn in TAX_WINDOWS:
        c0 = res["cost"][wn][CONTROL]["after_tax_cagr"]
        L.append(f"  {wn:20s} K {p(c0)} | " + " ".join(
            f"{a} {p(res['cost'][wn][a]['after_tax_cagr'])} "
            f"{(res['cost'][wn][a]['after_tax_cagr'] - c0) * 100:+7.3f}pp" for a in CANDIDATES))
    L.append("")
    L.append("six OOS windows (pre-tax, calibrated, m = 2, R-USD rule) -- delta vs K:")
    L.append("  window            K CAGR   K maxDD    C0 d      Q1 d      Q2 d      Q3 d")
    for wn_ in OOS_WINDOWS:
        rr = res["oos"][wn_]
        L.append(f"  {wn_:16s} {p(rr[CONTROL]['cagr'])}  {p(rr[CONTROL]['max_dd'])}  "
                 f"{rr[REFERENCE]['delta_cagr_pp']:+8.3f}  "
                 + "  ".join(f"{rr[q]['delta_cagr_pp']:+8.3f}" for q in CANDIDATES))
    L.append("")
    L.append("reference reproduction: " + f"{rc['n_cells']} cells, {rc['n_mismatch']} mismatches, "
             f"max|diff| {rc['max_abs_diff']:.2e} -> {'PASS' if rc['pass'] else 'FAIL'}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
