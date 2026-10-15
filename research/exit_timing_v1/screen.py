#!/usr/bin/env python3
"""exit_timing_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Idea I4 of docs/research/idea_survey_v4.md: the exit-leg tax timing the A1u band and H2 both
exempt by construction. Control = production **K** (`equity_leveraged_trend_v1` 1.2.0 = E2 +
R2 early re-entry + A1u gain-scaled downsize band + H2 December deferral), built on
research/band_h2_combo_v1's harness (its `engine_run` and tax engine are v3/th2's, validated
cell-by-cell against gain_band_v3's A1u and tax_harvest_v2's H2 in --harness).

Arms (pre-registered; every parameter fixed):
  K    production K, verbatim (the control).
  C0   production **R2** (K's execution layer OFF) -- the report-only reference row, and the
       label the deep reproduction block asserts against the R2 anchors.
  A1u  gain_band_v3's A1u alone  -- reference label, needed by the deep reproduction block.
  H2   tax_harvest_v2's H2 alone -- reference label, needed by the deep reproduction block.
  X1   an exit to zero whose FILL session is one of the last DEC_TAIL December sessions is
       postponed (the target is re-evaluated at every close; the ordinary rule then fills the
       exit at the first January decision and it is never postponed twice).
  X2   an exit to zero whose consumed FIFO lots have a USD gain fraction > X2_GAIN sells only
       down to the current early-leg weight (0.5 x vt), with a hard time cap of X2_CAP_SESSIONS
       sessions and an immediate full exit as soon as the early leg's own gate fails.
  X3   X1 + X2, X1's December window tested first.
Every arm runs production R2's target series; only the fill rule differs. X1/X2/X3 delay
de-risking, so the drawdown and the named worst episodes are reported per arm.

Stages (pre-registered before any non-control arm was simulated on real data):
  pre      pre-tax main window, base/calibrated x m = 1/2/4 plus the R-USD row.
  AT       after tax at $100k (gate capital) and $10k/$30k/$300k/$1M on the three windows.
  B12      clean bootstrap, seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths,
           $100k (+$1M report-only), B1-recent (progressive) and B2-flat.
  E2       rolling January starts 2001..2021, after tax at $100k, base, m = 2 (report only).
  EP       report only: per-arm max drawdown and total return in the five named episodes
           (2000-02, 2008, Dec 2015-Jan 2016, Dec 2018, Dec 2021-Jan 2022) plus the whole-path
           max DD and the X1/X2 event ledgers.
  OOS      the six ewma_band_v1 windows, pre-tax, calibrated, m = 2, R-USD rule (G7).
  G8       pooled OOS bootstrap over M1-M5, 2,000 paths each, block 20 (G8).
  COST     report only: the m = 4 cost stress at $100k.

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
  T1 after tax at $100k: arm - K >= 0 in all three windows.
  T2 B1-recent (progressive) at $100k: median >= 0 and share > 0 >= 55 %.
  T3 B2 (flat 20 %) at $100k: median >= 0 and share > 0 >= 55 %.
No non-inferiority relaxation: these arms change exposure, so G2/G5/T1-T3 stand as written.

--harness reproduces the control's published K anchors (band_h2_combo_v1/RESULTS.json: pre-tax
15.7649 / 15.1785 % CAGR, max DD 46.6698 / 46.7948 %, turnover 5.456136; after tax at $100k
10.7533 / 15.2354 / 23.9720 %) plus the R2 reference row and the pure-E2 fidelity series, and
in its deep part reproduces the parent campaigns' published cells cell-by-cell (gain_band_v3's
A1u against research/gain_band_v3/RESULTS.json, tax_harvest_v2's H2 against H2_ANCHORS.json).

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
# K is production K verbatim and is the control of THIS study: A1u is gain_band_v3's A1u (same
# tiers, same USD gain fraction), H2 is tax_harvest_v2's H2 (postpone a non-exit downward resize
# whose *fill* session is one of the last DEC_TAIL December sessions), composed H2-first. "C0"
# is production R2 (K's execution layer off) -- the report-only reference row; "A1u"/"H2" are
# K's two devices alone, kept because the deep reproduction block asserts their published cells.
# X1/X2/X3 keep K's execution layer exactly and change only the fill rule of an exit to zero.
CONTROL = "K"           # production K = A1u + H2 (the control of this study)
REFERENCE = "C0"        # production R2 (band off, no deferral) -- the reference row
CANDIDATES = ("X1", "X2", "X3")
ARMS = (CONTROL, REFERENCE, "A1u", "H2") + CANDIDATES
ARMS_ALL = ARMS
RULE = {
    "C0": {"band": False, "usd_rule": False, "defer": False, "dec_exit": False, "stage": False},
    "A1u": {"band": True, "usd_rule": True, "defer": False, "dec_exit": False, "stage": False},
    "H2": {"band": False, "usd_rule": False, "defer": True, "dec_exit": False, "stage": False},
    "K": {"band": True, "usd_rule": True, "defer": True, "dec_exit": False, "stage": False},
    # X1: postpone a December exit to zero (H2's mechanism on the event H2 exempts).
    "X1": {"band": True, "usd_rule": True, "defer": True, "dec_exit": True, "stage": False},
    # X2: stage a gain-heavy exit to zero down to the early-leg weight.
    "X2": {"band": True, "usd_rule": True, "defer": True, "dec_exit": False, "stage": True},
    # X3: X1 + X2, X1's December window tested first.
    "X3": {"band": True, "usd_rule": True, "defer": True, "dec_exit": True, "stage": True},
}
RULE["R2"] = RULE["C0"]  # alias: the label the R2 anchors are asserted under
RULE["E2"] = RULE["C0"]  # alias: the harness's pure-E2 fidelity anchor series (band off)
B_UP = C0_BAND                                   # entries / exits / up-resizes keep 0.5 pp
GAIN_TIERS = ((0.01, 0.005), (0.05, 0.03), (math.inf, 0.08))  # g_frac ceiling -> B_down
DEC_TAIL = 10                                    # H2 / X1: the last 10 December sessions
YEAR_END = "12"                                  # month prefix of the December sessions
# ---- X2 (pre-registered; see PREREGISTRATION.md section 4)
X2_GAIN = 0.05        # stage the exit only if the consumed FIFO lots' USD gain fraction is above
X2_CAP_SESSIONS = 21  # hard cap: the staged position is fully exited within 21 sessions

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
T1_MIN_DELTA_PP = 0.0        # after tax at $100k: arm - K >= 0 in all three windows
T2_MIN_MEDIAN_PP = 0.0       # B1-recent (progressive) / B2 (flat): median delta
T2_MIN_SHARE = 0.55          # ... and share of paths with delta > 0
# the bootstrap's delta cells: the candidates vs the control, plus the parent labels the deep
# reproduction block needs (A1u_vs_C0 and H2_vs_C0 are cells gain_band_v3 / tax_harvest_v2
# published, so the comparison fails if this study's code path has drifted).
BOOT_DELTAS = ("A1u", "H2", "K", "C0") + CANDIDATES

# ---- episodes (report-only; see PREREGISTRATION.md section 5)
EPISODES = (
    ("2000_02_dotcom", "2000-03-09", "2002-12-31"),
    ("2008_gfc", "2007-10-31", "2009-03-09"),
    ("dec2015_jan2016", "2015-12-01", "2016-01-31"),
    ("dec2018", "2018-12-01", "2018-12-31"),
    ("dec2021_jan2022", "2021-12-01", "2022-01-31"),
)
# ---- report-only after-tax stage
E2_START_YEARS = tuple(range(2001, 2022))   # 21 January starts, 2001..2021
E3_SEED = 20261015                          # fresh bootstrap (report only): seed / blocks / paths
E3_BLOCKS = (10, 40)
E3_PATHS = 1_000
E1_RINDEX_START = "2000-03-09"              # E1's start = max(ETF warm-up, the TRY calendar's start)
TAX_WINDOWS_LAST = "2017_01_to_end"         # the reporting window for per-arm trade/tax counters
TAX_WINDOW_MAIN = "2000_03_to_2026_09"      # the main window (the selection rule's tie-break)
# ---- published reference evidence
V3REF = ROOT / "research/gain_band_v3/RESULTS.json"      # A1u's published cells
H2REF = HERE / "H2_ANCHORS.json"                         # H2's published cells (from the tag)
KREF = ROOT / "research/band_h2_combo_v1/RESULTS.json"   # K's published anchors (the control)

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
# production K anchors: the control of THIS study, asserted cell-for-cell against
# research/band_h2_combo_v1/RESULTS.json (the campaign that adopted K). The "C0" label is
# asserted on REF_R2 / TAX_ANCHORS["R2"] / BOOT_ANCHOR["R2"] as the report-only reference row.
REF_K = {  # (cagr, max_dd, turnover), pre-tax main window, nav-based cagr
    ("base", 1): (0.160173, 0.463529, 5.456781),
    ("base", 2): (0.157649, 0.466698, 5.456136),
    ("base", 4): (0.152541, 0.472981, 5.456630),
    ("calibrated", 1): (0.154303, 0.464786, 5.456216),
    ("calibrated", 2): (0.151785, 0.467948, 5.456216),
    ("calibrated", 4): (0.146703, 0.474217, 5.456709),
}
REF_K_RUSD = {   # the R-USD main-window row, m = 2: (cagr, max_dd, turnover)
    "base": (0.157649, 0.466698, 5.456136),
    "calibrated": (0.151785, 0.467948, 5.456216),
}
# after tax at $100k, base, m = 2, liquidated: (pre_tax cagr, after_tax cagr, max_dd after tax)
TAX_ANCHOR_K = {
    "2000_03_to_2026_09": (0.157615, 0.107533, 0.466698),
    "2010_01_to_end": (0.223435, 0.152354, 0.446272),
    "2017_01_to_end": (0.330215, 0.239720, 0.349774),
}
REF_K_COST_M4 = {   # after tax at $100k, m = 4: (after_tax, pre_tax, max_dd after tax)
    "2000_03_to_2026_09": (0.103539, 0.152472, 0.472981),
    "2010_01_to_end": (0.148202, 0.218076, 0.453889),
    "2017_01_to_end": (0.236749, 0.326395, 0.354250),
}
BOOT_ANCHOR_K = {   # clean bootstrap (seed 20261011, block 20), median cagr
    ("progressive", "100k"): 0.038577, ("progressive", "1M"): 0.027020,
    ("flat", "100k"): 0.040376, ("flat", "1M"): 0.040376,
}
REF_K_DEFERRALS = {   # main path, m = 2, base, $100k, progressive: (band, December)
    "2000_03_to_2026_09": (1836, 81), "2010_01_to_end": (1413, 60), "2017_01_to_end": (787, 29)}


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


def early_weight(vol_instr):
    """X2's staged target: the early leg's own target 0.5 x vt (the production early weight,
    cap-free because 0.5 x vt <= 0.5 < CAP). Returns [P, T] like `sized_wants`."""
    vt = trv2.vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)))
    return 0.5 * vt


def exit_ctx(price, vol_instr, comps=None):
    """X1/X2's decision inputs, evaluated on the *price* series the rule is computed on:
    `w_early` = the early-leg target 0.5 x vt (X2's staged target), and `hold` = **the early
    conditions** in the brief's own sense -- the early leg's full three-condition set
    (close > SMA50, vol20 below its 252-session median, close > close 20 sessions ago), i.e.
    exactly the set whose failure X2's text makes an immediate-full-exit trigger.
    PREREGISTRATION section 4.3 records the alternatives: a 3-condition *entry* gate is
    provably inert (an exit to zero implies the conditions already fail), and the probe shows
    every gain-heavy exit is below SMA50, so X2's staged state is released almost immediately
    by this condition -- the device is self-defeating on this rule, which is a finding.
    Both arrays are [P, T] and are read at index i - 1 by `engine_run` (the decision close)."""
    comps = early_components(price) if comps is None else comps
    sma50_up, vol_p, med_p, lag_cond = comps
    hold = np.atleast_2d(sma50_up) & np.isfinite(med_p) & (vol_p < med_p) & np.atleast_2d(lag_cond)
    return {"w_early": early_weight(vol_instr), "hold": hold}


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
               trace=False, x2=None):
    """One arm through the Turkish after-tax account, session by session.

    mode in {"pre_tax", "progressive", "flat"}; arm in ARMS. Every arm shares the target series
    `wq` (production R2's, for every arm label including the control "K"); only the fill decision
    differs: entries, exits to zero and up-resizes always fill (0.5 pp band), while a
    discretionary downward resize (`tgt > 0`, `tgt < w`) is subject to the arm's band (A1u's gain
    tiers) and/or its December window (H2: postponed when the fill session is one of the last
    DEC_TAIL December sessions). A downsize
    between two positive targets is a downward resize whatever its size, so the rule also covers
    the transfer between R2's full weight and its half-weight early-re-entry leg. `rule_index`
    selects the R-INDEX (True) or R-USD (False) instantiation of the decision state. With arm
    "C0" this is exactly research/tax_harvest_v2/screen.py:tax_run_overlay(overlay="none"), and
    with arm "K" exactly research/band_h2_combo_v1/screen.py's "K".

    X1/X2/X3 (this study) touch only the exit-to-zero fill:
      X1  `spec["dec_exit"]`: an exit to zero (tgt <= 0 with a live position) whose FILL session
          is in `dec_tail` is postponed; the ordinary rule is re-evaluated at every close, so it
          fills at the first January decision and is never postponed twice.
      X2  `spec["stage"]`: an exit to zero whose consumed FIFO lots have a USD gain fraction
          above X2_GAIN (the A1u USD formula) sells only down to the current early-leg weight
          `w_early` instead of to zero, and the remainder is held while the early leg's own gate
          `hold` stands, at most X2_CAP_SESSIONS sessions after the staging fill. `x2` carries
          those two [P, T] arrays. The staged sale is an ordinary downsize between two positive
          targets, so it is subject to K's H2 window and A1u band exactly like any other.
    An exit to zero with no arm rule (or when the arm's rule declines to act) always fills."""
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
    exit_defer_by_year = defaultdict(int)
    dec_tail = december_tail(days, i0, i1, DEC_TAIL) if (spec["defer"] or spec["dec_exit"]) else set()
    prev_tgt = None
    dec = [] if trace else None
    # ---- X2 state (per path): the session of the staging fill and the release reason counters
    staged_at = None
    staged_done = False     # released -> this exit episode may not be staged again (see below)
    staged_events = staged_release_cap = staged_release_gate = staged_sessions = 0
    staged_release_target = 0     # the ordinary rule turned positive: it takes the position over
    deferred_exit = 0
    if spec["stage"] and x2 is None:
        raise ValueError("arm needs an x2 context (w_early / hold arrays)")
    w_early_arr = None if x2 is None else np.asarray(x2["w_early"], dtype=float).flatten()
    hold_arr = None if x2 is None else np.asarray(x2["hold"], dtype=bool).flatten()

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

        # ---- X1/X2/X3: the arm's rule on an exit to zero (the fill A1u and H2 both exempt by
        #      construction). This block only picks the target and the trade flag; K's ordinary
        #      layer below (H2 window first, then A1u's band) still decides every fill.
        exit_zero = bool(tgt <= 1e-12 and w > 1e-12)
        if staged_at is not None and (tgt > 1e-12 or w <= 1e-12):
            if tgt > 1e-12:
                staged_release_target += 1        # the ordinary rule takes the position over
            staged_at = None
        if tgt > 1e-12:
            staged_done = False                   # the rule is in: a new exit episode may stage
        if (spec["dec_exit"] or spec["stage"]) and exit_zero:
            if spec["dec_exit"] and i in dec_tail:
                # X1: postpone the whole exit into the next tax year
                trade = False
                postponed += 1
                post_by_year[year_i] += 1
                deferred_exit += 1
                exit_defer_by_year[year_i] += 1
            elif spec["stage"]:
                if staged_at is not None and (i - staged_at >= X2_CAP_SESSIONS
                                              or not bool(hold_arr[i - 1])):
                    if i - staged_at >= X2_CAP_SESSIONS:
                        staged_release_cap += 1
                    else:
                        staged_release_gate += 1
                    # released: the remaining position exits in full here and this exit episode
                    # may not be staged again (the hard cap is a cap, not a new timer)
                    staged_at, staged_done = None, True
                if staged_at is None and not staged_done:
                    # the text's only entry condition: the consumed lots' USD gain fraction
                    g_full, p_full = rule_sale_usd(i, po, qa.units, fee_total, qa.lots)
                    gf = max(0.0, g_full) / p_full if p_full > 0 else 0.0
                    if gf > X2_GAIN:
                        staged_at, staged_events = i, staged_events + 1
                if staged_at is not None:
                    # stage: retarget to the early-leg weight, leave the fill to the layer below
                    tgt = float(w_early_arr[i - 1])
                    drift = abs(tgt - w)
                    fee_total = nav_open * cost * drift
                    nav_after = nav_open - fee_total
                    target_units = tgt * nav_after / po if po > 0 else 0.0
                    du = target_units - qa.units
                    trade = bool(drift > C0_BAND or du < -1e-12 or du > 1e-12)

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
        if staged_at is not None:
            staged_sessions += 1
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
        "deferred": int(deferred), "deferred_exit": int(deferred_exit),
        "staged_events": int(staged_events), "staged_sessions": int(staged_sessions),
        "staged_release_cap": int(staged_release_cap),
        "staged_release_gate": int(staged_release_gate),
        "staged_release_target": int(staged_release_target),
        "postponements_by_year": {int(k): int(v) for k, v in sorted(post_by_year.items())},
        "deferrals_by_year": {int(k): int(v) for k, v in sorted(defer_by_year.items())},
        "exit_deferrals_by_year": {int(k): int(v) for k, v in sorted(exit_defer_by_year.items())},
        "fees_usd": float(qa.fees), "index_relief_try": float(qa.index_relief_try),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.gain_by_reason.items())},
        "pos_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.pos_gain_by_reason.items())},
        "neg_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.neg_gain_by_reason.items())},
        "year_gain_try": {int(k): float(v) for k, v in sorted(qa.year_gain.items())},
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
    return days, o, c, w_e2, w_r2, e2, exit_ctx(price, vol_instr, comps)


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
    vol_instr = trv2.vol_n(sc)[0]
    _w_e2, w_r2 = wants_of(c, e2, vol_instr, comps)
    xc = exit_ctx(c, vol_instr, comps)
    mkt = usd_market(days)
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        r = engine_run(1.0, start, len(days) - 1, days, mkt, so, sc, w_r2, e2, arm, "pre_tax",
                       m=M, rule_index=False, x2=xc)
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
    vol_instr = trv2.vol_n(sc)[0]
    _w_e2, w_r2 = wants_of(c, e2, vol_instr, comps)
    xc = exit_ctx(c, vol_instr, comps)
    mkt = usd_market(days)
    res = {}
    for arm in arms:
        r = engine_run(1.0, start, len(days) - 1, days, mkt, so, sc, w_r2, e2, arm, "pre_tax",
                       m=M, rule_index=False, x2=xc)
        st = window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


# ----------------------------------------------------------------------------- bootstrap E1 (fixed 2015-09..2026-09 calendar)
def boot(seed=SEED, block=BLOCK, arms=ARMS_ALL, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
         capitals=("100k", "1M"), modes=("progressive", "flat"), anchor_e2=True,
         deltas=BOOT_DELTAS, baselines=(CONTROL, REFERENCE), label="E1"):
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
        ctxs = {"base": exit_ctx(pc, trv2.vol_n(sc)[0], comps),
                "calibrated": exit_ctx(pc, trv2.vol_n(sc_c)[0], comps)}
        # every arm label (the control "K" and the reference "C0" included) is driven by
        # production R2's target series; only the fill rule differs.
        if pidx < n_pre:
            for md, (o_, c_, want) in legs.items():
                for arm in arms:
                    r = engine_run(PRE_CAPITAL, i0, i1, fixed_dates, mkt, o_, c_, want, e2,
                                   arm, "pre_tax", m=M, x2=ctxs[md])
                    k = len(r["_nav"]) - 1
                    pre[(md, arm)].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
        if pidx < n_tax:
            o_, c_, want = legs["base"]
            for cap_label in capitals:
                for mode in modes:
                    for arm in arms:
                        r = engine_run(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, o_, c_,
                                       want, e2, arm, mode, m=M, x2=ctxs["base"])
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

    def _delta(c, base):
        d = c - base
        return {"median_delta_pp": float(np.median(d) * 100), "mean_delta_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)), "p05_pp": float(np.percentile(d, 5) * 100),
                "p95_pp": float(np.percentile(d, 95) * 100)}

    for md in (FIN if n_pre > 0 else ()):   # no pre-tax paths -> no pre-tax block
        ref_base = np.array(pre[(md, REFERENCE)])
        out["pre"][md] = {}
        for arm in arms:
            c = np.array(pre[(md, arm)])
            d = c - ref_base
            # the v3-comparable pre-tax cells: deltas against the reference label "C0" (R2), the
            # convention gain_band_v3's published bootstrap used
            out["pre"][md][arm] = {
                "cagr_median": float(np.median(c)), "diff_vs_C0_median_pp": float(np.median(d) * 100),
                "diff_vs_C0_mean_pp": float(np.mean(d) * 100), "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100), "p95_delta_pp": float(np.percentile(d, 95) * 100)}
        for ctrl in baselines:
            base = np.array(pre[(md, ctrl)])
            for arm in deltas:
                if arm == ctrl:
                    continue
                out["pre"][md][f"{arm}_vs_{ctrl}"] = _delta(np.array(pre[(md, arm)]), base)
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
        w_early = early_weight(vol_instr)
        hold = trv2.sma_up(cc, SMA_FAST)
        for arm in arms:
            want = w_r2   # production R2 target series for every label, the control included
            for p in range(P):
                r = engine_run(1.0, START, cc.shape[1] - 1, days, mkt, so[p], sc[p], want[p], e2[p],
                               arm, "pre_tax", m=M, rule_index=False,
                               x2={"w_early": w_early[p], "hold": hold[p]})
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
    xc_u = exit_ctx(price_up, trv2.vol_n(sc_u)[0], early_components(price_up))
    xc = exit_ctx(price, v20, early_components(price))
    blocked = {}
    for arm in ARMS:
        rr = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u,
                        e2_u, arm, "pre_tax", x2=xc_u)
        blocked[arm] = {"band": rr["blocked_band"], "deferred": rr["deferred"],
                        "exit": rr["deferred_exit"], "postponed": rr["postponements"]}
    checks["band_fires"] = bool(blocked["A1u"]["band"] > 0 or blocked["K"]["band"] > 0)
    checks["deferral_fires"] = bool(blocked["H2"]["deferred"] > 0 and blocked["K"]["deferred"] > 0)
    checks["C0_blocks_nothing"] = bool(blocked["C0"]["postponed"] == 0)
    checks["postponements_decompose"] = bool(all(
        blocked[a]["postponed"] == blocked[a]["band"] + blocked[a]["deferred"] + blocked[a]["exit"]
        for a in ARMS))

    # 4b. the tax modes bite in a trending series: progressive <= pre-tax, and flat < pre-tax
    flat_bites = prog_bites = False
    for arm in ARMS:
        want_u = wr_u   # the control label "C0" is the R2 target series too
        pre_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, want_u, e2_u, arm, "pre_tax", x2=xc_u)
        flat_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, want_u, e2_u, arm, "flat", x2=xc_u)
        prog_u = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, want_u, e2_u, arm, "progressive", x2=xc_u)
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

    # 4d. the X arms (this study). Identities first: with no December window X1 is K, and with
    # the staging threshold above any possible gain fraction X2 is K. Then the mechanisms:
    # an engineered exit to zero in a December tail must be postponed by X1 (and by X3), and an
    # engineered gain-heavy exit to zero with a live early gate must stage and release.
    keep_tail, DEC_TAIL = DEC_TAIL, 0
    x1t = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u, e2_u, "X1", "pre_tax",
                     x2=xc_u)
    kt = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u, e2_u, "K", "pre_tax")
    DEC_TAIL = keep_tail
    checks["X1_without_a_window_equals_K"] = bool(np.array_equal(x1t["_nav"], kt["_nav"]))
    keep_gain, globals()["X2_GAIN"] = globals()["X2_GAIN"], math.inf
    x2t = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, wr_u, e2_u, "X2", "pre_tax",
                     x2=xc_u)
    globals()["X2_GAIN"] = keep_gain
    checks["X2_with_an_unreachable_threshold_equals_K"] = bool(np.array_equal(x2t["_nav"], kt["_nav"]))
    # engineered: hold 0.98 in a rising market, then exit to zero inside every December tail
    # (the target is read at i - 1, hence the one-session shift of the mask)
    idx = np.arange(1500)
    w_dec = np.where(np.isin(idx - 1, sorted(dt10u)), 0.0, 0.98)
    kd = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_dec, e2_u, "K", "pre_tax")
    x1d = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_dec, e2_u, "X1", "pre_tax",
                     x2=xc_u)
    x3d = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_dec, e2_u, "X3", "pre_tax",
                     x2={"w_early": np.full(1500, 0.4), "hold": np.ones(1500, dtype=bool)})
    checks["X1_defers_december_exits_only"] = bool(
        x1d["deferred_exit"] > 0 and len(x1d["exit_deferrals_by_year"]) > 0
        and x1d["deferred"] == kd["deferred"] and x1d["blocked_band"] == kd["blocked_band"]
        and x1d["qld_sells"] == kd["qld_sells"]
        and np.max(np.abs(x1d["_nav"] - kd["_nav"])) > 1e-9)
    checks["X3_defers_the_same_december_exits"] = bool(x3d["deferred_exit"] == x1d["deferred_exit"])
    # engineered: gain-heavy exit to zero with the early gate standing -> X2 stages, cap release
    w_gap = np.where(idx >= 1000, 0.0, 0.98)
    x2d = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_gap, e2_u, "X2", "pre_tax",
                     x2={"w_early": np.full(1500, 0.4), "hold": np.ones(1500, dtype=bool)})
    kd2 = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_gap, e2_u, "K", "pre_tax")
    checks["X2_stages_a_gain_heavy_exit"] = bool(
        x2d["staged_events"] == 1 and x2d["staged_sessions"] > 0
        and x2d["staged_release_cap"] + x2d["staged_release_gate"] == 1
        and np.max(np.abs(x2d["_nav"] - kd2["_nav"])) > 1e-9)
    x2g = engine_run(1.0, START, 1499, days_u, mkt_u, so_u, sc_u, w_gap, e2_u, "X2", "pre_tax",
                     x2={"w_early": np.full(1500, 0.4),
                         "hold": np.where(idx <= 1000, True, False)})
    checks["X2_releases_on_the_gate"] = bool(
        x2g["staged_events"] == 1 and x2g["staged_release_gate"] == 1
        and x2g["staged_release_cap"] == 0 and x2g["staged_sessions"] == 1)

    # 5. finite, positive, and after tax <= pre-tax
    for arm in ARMS:
        want = w_r2
        rr = engine_run(1.0, START, n - 1, days, mkt, so, sc, want, e2, arm, "pre_tax", x2=xc)
        rp = engine_run(1.0, START, n - 1, days, mkt, so, sc, want, e2, arm, "progressive", x2=xc)
        rf = engine_run(1.0, START, n - 1, days, mkt, so, sc, want, e2, arm, "flat", x2=xc)
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
                    "postponed_pre": rr["postponements"],
                    "deferred_exit": rr["deferred_exit"],
                    "staged_events": rr["staged_events"]}
    return {"checks": checks, "arms": res, "mechanism": blocked,
            "pass": bool(all(checks.values()))}


def harness(run_boot=True, deep=False):
    """Control reproduction on real data (no candidate arm).

    The study's control is production **K**, so the label "K" -- the label the run uses for the
    control, its fill rule, target series = production R2's -- is asserted against the K cells
    `band_h2_combo_v1` published (pre-tax CAGR / max DD / turnover at m = 1/2/4 x
    base/calibrated, the R-USD row, the three after-tax windows at $100k with their max DD, the
    m = 4 cost stress and the two clean-bootstrap medians). The "C0" label (production R2) is
    asserted against `fast_reentry_v1`'s R2 anchors and the pure-E2 series against
    `e2_integration_v1`'s, so these checks fail if either reference label drifts (the
    gain_band_v1 bug)."""
    checks, bad = {}, 0
    days, o, c, w_e2, w_r2, e2, x2c = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    # ---- the control: production K (label "K"), pre-tax m = 1/2/4 and the R-USD row
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_, _xc = main_inputs(spread)
        for m in (1, 2, M_STRESS):
            r = engine_run(1.0, START, len(d_) - 1, d_, mkt, o_, c_, wr_, e_, "K", "pre_tax", m=m)
            for key, val in (("cagr_usd_unliquidated", REF_K[(md, m)][0]),
                             ("max_dd_nav_usd", REF_K[(md, m)][1]),
                             ("turnover_annual", REF_K[(md, m)][2])):
                got = float(r[key])
                checks[f"K_{md}_m{m}_{key}"] = {"reference": val, "reproduced": got, "diff": got - val}
                if abs(got - val) > TOL:
                    bad += 1
        r = engine_run(PRE_CAPITAL, START, len(d_) - 1, d_, usd_market(d_), o_, c_, wr_, e_, "K",
                       "pre_tax", m=M, rule_index=False)
        for key, val in (("cagr_usd_unliquidated", REF_K_RUSD[md][0]),
                         ("max_dd_nav_usd", REF_K_RUSD[md][1]),
                         ("turnover_annual", REF_K_RUSD[md][2])):
            got = float(r[key])
            checks[f"K_{md}_rusd_{key}"] = {"reference": val, "reproduced": got, "diff": got - val}
            if abs(got - val) > TOL:
                bad += 1
    # ---- reference rows: production R2 (label "C0") and the pure-E2 fidelity series
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_, _xc = main_inputs(spread)
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
    # ---- explicit K anchor block (the run's control label "K")
    k_anchors = {
        "reference": {"pre_tax": "research/band_h2_combo_v1/RESULTS.json (K): 15.7649 / 15.1785 % CAGR, "
                                 "max DD 46.6698 / 46.7948 %, turnover 5.456136",
                      "after_tax": "research/band_h2_combo_v1/RESULTS.json (K) at $100k: 10.753 / 15.235 / "
                                   "23.972 % CAGR, max DD 46.670 / 44.627 / 34.977 %",
                      "bootstrap": "research/band_h2_combo_v1/RESULTS.json (K), seed 20261011 block 20, "
                                   "1000 paths: B1-recent 3.8577 %, B2-flat 4.0376 %"},
        "pre_tax": {f"{md}_m{m}": {
            "reference_cagr": REF_K[(md, m)][0],
            "reproduced_cagr": checks[f"K_{md}_m{m}_cagr_usd_unliquidated"]["reproduced"],
            "reference_max_dd": REF_K[(md, m)][1],
            "reproduced_max_dd": checks[f"K_{md}_m{m}_max_dd_nav_usd"]["reproduced"],
            "reference_turnover": REF_K[(md, m)][2],
            "reproduced_turnover": checks[f"K_{md}_m{m}_turnover_annual"]["reproduced"]}
            for md in FIN for m in (1, 2, M_STRESS)},
        "after_tax_100k": {},
    }
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
    h2_nav, _, _ = simulate_band_overlay(o[None, :], c[None, :], w_r2[None, :], COST_PER_SIDE * 2,
                                          START, C0_BAND,
                                          december_tail(days, START, len(days) - 1, DEC_TAIL))
    mine_k = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, "K", "pre_tax")
    mine_h2 = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, "H2", "pre_tax")
    checks["h2_engine_equals_simulate_band_overlay"] = {
        "max_abs_nav_diff": float(np.max(np.abs(mine_h2["_nav"] - h2_nav[0])))}
    if checks["h2_engine_equals_simulate_band_overlay"]["max_abs_nav_diff"] > 1e-9:
        bad += 1
    e2_run = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_e2, e2, "E2", "pre_tax")
    checks["control_is_not_the_E2_anchor_series"] = {
        "max_abs_nav_diff_vs_E2": float(np.max(np.abs(mine_k["_nav"] - e2_run["_nav"]))),
        "cagr_control_K": mine_k["cagr_usd_unliquidated"],
        "cagr_E2_anchor": e2_run["cagr_usd_unliquidated"]}
    if not checks["control_is_not_the_E2_anchor_series"]["max_abs_nav_diff_vs_E2"] > 0.01:
        bad += 1
    for arm in CANDIDATES:   # X1/X2/X3 must not be K: each arm has to move the NAV path
        r = engine_run(1.0, START, len(days) - 1, days, mkt, o, c, w_r2, e2, arm, "pre_tax", x2=x2c)
        checks[f"{arm}_nav_differs_from_K"] = {
            "max_abs_nav_diff": float(np.max(np.abs(r["_nav"] - mine_k["_nav"])))}
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
        # ---- the control's after-tax anchors (K, $100k): (pre_tax, progressive, progressive DD)
        rp = engine_run(100_000.0, a, b, days, mkt, o, c, w_r2, e2, "K", "pre_tax")
        rq = engine_run(100_000.0, a, b, days, mkt, o, c, w_r2, e2, "K", "progressive")
        refk = TAX_ANCHOR_K[wn]
        rq4 = engine_run(100_000.0, a, b, days, mkt, o, c, w_r2, e2, "K", "progressive", m=M_STRESS)
        k_anchors["after_tax_100k"][wn] = {
            "reference": list(refk), "reference_cost_m4": list(REF_K_COST_M4[wn]),
            "reproduced": [rp["cagr_usd_liquidated"], rq["cagr_usd_liquidated"], rq["max_dd_nav_usd"]],
            "reproduced_cost_m4": [rq4["cagr_usd_liquidated"], rq4["max_dd_nav_usd"]],
            "deferrals": [rq["blocked_band"], rq["deferred"]],
            "reference_deferrals": list(REF_K_DEFERRALS[wn]),
            "diff_pp": [(rp["cagr_usd_liquidated"] - refk[0]) * 100,
                        (rq["cagr_usd_liquidated"] - refk[1]) * 100,
                        (rq["max_dd_nav_usd"] - refk[2]) * 100],
            "pass": bool(all(abs((x - y) * 100) <= 0.005 for x, y in
                             zip([rp["cagr_usd_liquidated"], rq["cagr_usd_liquidated"],
                                  rq["max_dd_nav_usd"]], refk))
                         and rq["blocked_band"] == REF_K_DEFERRALS[wn][0]
                         and rq["deferred"] == REF_K_DEFERRALS[wn][1])}
        if not k_anchors["after_tax_100k"][wn]["pass"]:
            bad += 1
    r2_anchors["pass"] = bool(
        all(abs(checks[f"C0_{md}_m{m}_{k}"]["diff"]) <= TOL for md in FIN for m in (1, 2)
            for k in ("cagr_usd_unliquidated", "max_dd_nav_usd", "turnover_annual"))
        and checks["c0_engine_equals_simulate_band"]["max_abs_nav_diff"] <= 1e-9
        and all(r2_anchors["after_tax_100k"][wn]["pass"] for wn in TAX_WINDOWS))
    if not r2_anchors["pass"]:
        bad += 1
    k_anchors["pass"] = bool(
        all(abs(checks[f"K_{md}_m{m}_{k}"]["diff"]) <= TOL for md in FIN for m in (1, 2, M_STRESS)
            for k in ("cagr_usd_unliquidated", "max_dd_nav_usd", "turnover_annual"))
        and all(abs(checks[f"K_{md}_rusd_{k}"]["diff"]) <= TOL for md in FIN
                for k in ("cagr_usd_unliquidated", "max_dd_nav_usd", "turnover_annual"))
        and checks["h2_engine_equals_simulate_band_overlay"]["max_abs_nav_diff"] <= 1e-9
        and all(k_anchors["after_tax_100k"][wn]["pass"] for wn in TAX_WINDOWS))
    if not k_anchors["pass"]:
        bad += 1
    # ---- the K anchor constants must be the published cells, not a transcription slip
    pub = json.loads(KREF.read_text())
    kref_bad = []
    for md in FIN:
        for m in (1, 2, M_STRESS):
            c = pub["pre_tax_main"][md][f"m{m}"]["K"]
            for got, want in ((REF_K[(md, m)][0], c["cagr"]), (REF_K[(md, m)][1], c["max_dd"]),
                              (REF_K[(md, m)][2], c["turnover_annual"])):
                if abs(got - want) > 1e-9:
                    kref_bad.append(f"pre_tax_main.{md}.m{m}")
            cu = pub["pre_tax_main_r_usd"][md]["K"]
            for got, want in ((REF_K_RUSD[md][0], cu["cagr"]), (REF_K_RUSD[md][1], cu["max_dd"]),
                              (REF_K_RUSD[md][2], cu["turnover_annual"])):
                if abs(got - want) > 1e-9:
                    kref_bad.append(f"pre_tax_main_r_usd.{md}")
    for wn in TAX_WINDOWS:
        c = pub["after_tax_windows"]["100k"][wn]["K"]
        for got, want in ((TAX_ANCHOR_K[wn][0], c["pre_tax_cagr"]),
                          (TAX_ANCHOR_K[wn][1], c["after_tax_cagr"]),
                          (TAX_ANCHOR_K[wn][2], c["max_dd_after_tax"])):
            if abs(got - want) > 1e-9:
                kref_bad.append(f"after_tax_windows.100k.{wn}")
        if (REF_K_DEFERRALS[wn][0], REF_K_DEFERRALS[wn][1]) != (c["blocked_band"], c["deferred"]):
            kref_bad.append(f"deferrals.{wn}")
        c4 = pub["cost_stress_m4"][wn]["K"]
        for got, want in ((REF_K_COST_M4[wn][0], c4["after_tax_cagr"]),
                          (REF_K_COST_M4[wn][1], c4["pre_tax_cagr"]),
                          (REF_K_COST_M4[wn][2], c4["max_dd_after_tax"])):
            if abs(got - want) > 1e-9:
                kref_bad.append(f"cost_stress_m4.{wn}")
    for mode in ("progressive", "flat"):
        for cl in ("100k", "1M"):
            c = pub["bootstrap_clean_seed20261011"]["tax"][cl][mode]["K"]["cagr_median"]
            if abs(BOOT_ANCHOR_K[(mode, cl)] - c) > 1e-9:
                kref_bad.append(f"bootstrap.{cl}.{mode}")
    checks["k_anchor_constants_match_published"] = {"n_mismatch": len(kref_bad),
                                                    "mismatch_labels": kref_bad[:10]}
    if kref_bad:
        bad += 1
    boot_control = {}
    if run_boot:
        b = boot(n_pre=TAX_BOOT_PATHS, n_tax=TAX_BOOT_PATHS, capitals=("100k",), arms=ARMS_ALL,
                 deltas=BOOT_DELTAS, baselines=(CONTROL, REFERENCE))
        for mode in ("progressive", "flat"):
            # boot() drives every arm label from production R2's target series, so its "C0" is
            # production R2 (BOOT_ANCHOR["R2"] = tax_harvest_v2's R2 medians), its "K" is the
            # control (BOOT_ANCHOR_K) and the pure-E2 series is tracked under its own label.
            boot_control[mode] = {"R2": b["tax"]["100k"][mode][REFERENCE]["cagr_median"],
                                  "K": b["tax"]["100k"][mode][CONTROL]["cagr_median"],
                                  "E2": b["anchor_e2_100k"][mode]["cagr_median"]}
            for label, ref in (("R2", BOOT_ANCHOR[mode]["R2"]),
                               ("K", BOOT_ANCHOR_K[(mode, "100k")]),
                               ("E2", BOOT_ANCHOR[mode]["E2"])):
                if abs(boot_control[mode][label] - ref) > 0.0010:
                    bad += 1
        boot_control["pre_base"] = {a: b["pre"]["base"][a]["cagr_median"] for a in ARMS}
        boot_control["pre_cal"] = {a: b["pre"]["calibrated"][a]["cagr_median"] for a in ARMS}
    out = {"reproduction_checks": checks, "n_outside_tolerance": bad, "after_tax_anchors": tax,
           "k_anchors": k_anchors, "r2_anchors": r2_anchors, "bootstrap_control": boot_control,
           "pass": bool(bad == 0)}
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
                    capitals=("100k", "1M"), modes=("progressive", "flat"), deltas=BOOT_DELTAS,
                    baselines=(CONTROL, REFERENCE), label="clean")
    ref = reference_checks({"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost,
                            "boot_ref": boot_ref}, full=False)
    ref["boot_ref"] = boot_ref
    return ref


# ----------------------------------------------------------------------------- the run
def pre_tax_main(arms):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD main-window row."""
    days, o, c, w_e2, w_r2, e2, _x2 = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    usd_mkt = usd_market(days)
    pre, pre_usd = {}, {}
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_, xc = main_inputs(spread)
        W = {arm: wr_ for arm in arms}
        pre[md], pre_usd[md] = {}, {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in arms:
                r = engine_run(PRE_CAPITAL, START, T, d_, mkt, o_, c_, W[arm], e_, arm, "pre_tax",
                               m=m, x2=xc)
                pre[md][f"m{m}"][arm] = pre_stats(d_, START, T, r)
        for arm in arms:
            r = engine_run(PRE_CAPITAL, START, T, d_, usd_mkt, o_, c_, W[arm], e_, arm, "pre_tax", m=M,
                           rule_index=False, x2=xc)
            pre_usd[md][arm] = pre_stats(d_, START, T, r, real_days=d_)
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": T - START + 1,
                     "years": float(trv2.calendar_years(days[START - 1], days[-1]))}
    return pre, pre_usd


def after_tax_windows(capitals, arms):
    """After tax at the given capitals (progressive 2026 tariff, liquidated, base financing,
    m = 2) on the three TAX_WINDOWS. Every arm label runs production R2's target series."""
    days, o, c, w_e2, w_r2, e2, xc = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    WANTS = {arm: w_r2 for arm in arms}
    win = {}
    for cap_label in capitals:
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            win[cap_label][wn] = {}
            for arm in arms:
                rq = engine_run(TAX_CAPITALS[cap_label], a, b, days, mkt, o, c, WANTS[arm], e2, arm,
                                "progressive", x2=xc)
                # v2's convention for this reporting row: the pre-tax leg of the after-tax
                # windows runs at capital 1.0 (so A2/A3's budget is inert here). Kept identical
                # to v2, which is what the reference check verifies; it is not a v3 gate cell.
                rp = engine_run(1.0, a, b, days, mkt, o, c, WANTS[arm], e2, arm, "pre_tax", x2=xc)
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
                    "deferred": rq["deferred"], "deferred_exit": rq["deferred_exit"],
                    "staged_events": rq["staged_events"], "staged_sessions": rq["staged_sessions"],
                    "staged_release_cap": rq["staged_release_cap"],
                    "staged_release_gate": rq["staged_release_gate"],
                    "staged_release_target": rq["staged_release_target"],
                    "exit_deferrals_by_year": rq["exit_deferrals_by_year"],
                    "postponements_by_year": rq["postponements_by_year"],
                    "deferrals_by_year": rq["deferrals_by_year"],
                    "index_relief_try": rq["index_relief_try"],
                    "realised_gain_try_by_reason": rq["realised_gain_try_by_reason"],
                    "neg_gain_try_by_reason": rq["neg_gain_try_by_reason"],
                    "year_gain_try": rq["year_gain_try"],
                }
    return win


def cost_stress(arms):
    """m = 4 cost stress, after tax at $100k on the three windows."""
    days, o, c, w_e2, w_r2, e2, xc = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    cost = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        cost[wn] = {}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, w_r2, e2, arm,
                            "progressive", m=M_STRESS, x2=xc)
            # v2's convention (see after_tax_windows): the pre-tax leg of the cost stress runs
            # at capital 1.0, so the reference check reproduces v2 exactly.
            rp = engine_run(1.0, a, b, days, mkt, o, c, w_r2, e2, arm, "pre_tax", m=M_STRESS, x2=xc)
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
    """Pooled OOS bootstrap (G8): block-20 over M1-M5, rng seed SEED, arms in order. G8 gates the
    candidates against the control K, so only those arms are simulated."""
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, arms) for k in MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in arms}
    out = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        out["per_market"][k] = {a: {"median_delta_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)}
                                for a in arms}
    for a in arms:
        d = pooled[a] - pooled[CONTROL]
        out["pooled"][a] = {"median_delta_pp": float(np.median(d) * 100),
                            "mean_delta_pp": float(np.mean(d) * 100),
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
        vol_instr = trv2.vol_n(sc)[0]
        icomps = early_components(ic)
        _w_e2, w_r2 = wants_of(ic, e2, vol_instr, icomps)
        xc = exit_ctx(ic, vol_instr, icomps)
        mkt = te.load_market(FX_CSV, UFE_CSV, idays)
        rec = {"window": {"start": idays[start], "end": idays[-1], "sessions": len(idays) - start}}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], start, len(idays) - 1, idays, mkt, so, sc,
                            w_r2, e2, arm, "progressive", x2=xc)
            rp = engine_run(PRE_CAPITAL, start, len(idays) - 1, idays, mkt, so, sc, w_r2, e2, arm,
                            "pre_tax", x2=xc)
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
    days, o, c, w_e2, w_r2, e2, xc = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    out = {}
    for yr in E2_START_YEARS:
        i0 = bisect.bisect_left(days, f"{yr}-01-01")
        rec = {"start": days[i0], "index": int(i0)}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], i0, T, days, mkt, o, c, w_r2, e2, arm,
                            "progressive", x2=xc)
            rp = engine_run(PRE_CAPITAL, i0, T, days, mkt, o, c, w_r2, e2, arm, "pre_tax", x2=xc)
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
                     capitals=(GATE_CAPITAL,), modes=(mode,), anchor_e2=False, deltas=BOOT_DELTAS,
                     baselines=(CONTROL, REFERENCE), label=f"E3 block {block} {mode}")
            out[f"block{block}_{mode}"] = b["tax"][GATE_CAPITAL][mode]
    return out


def stage_e5_decisions():
    """E5 (report only): how often the arms' fill decisions differ on the main path
    (base financing, m = 2, $100k, R-INDEX, pre-tax so no tax-funding sales interfere).
    Decision code: 0 = no trade, 1 = up/entry/exit trade, 2 = downward resize."""
    days, o, c, w_e2, w_r2, e2, xc = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    traces = {}
    for arm in ARMS:
        traces[arm] = engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, w_r2, e2, arm, "pre_tax",
                                 trace=True, x2=xc)
    out = {"window": {"start": days[START], "end": days[-1], "sessions": int(len(traces[CONTROL]["_dec"]))},
           "sells": {}, "postponements": {}}
    for arm in ARMS:
        r = traces[arm]
        out["sells"][arm] = int(np.sum(r["_dec"] == 2))
        out["postponements"][arm] = {"band": int(r["blocked_band"]), "deferred": int(r["deferred"]),
                                     "exit": int(r["deferred_exit"]),
                                     "staged": int(r["staged_events"])}
    for a, b in (("X1", CONTROL), ("X2", CONTROL), ("X3", CONTROL), ("X3", "X1"),
                 ("X3", "X2"), (CONTROL, "C0"), (CONTROL, "A1u"), ("H2", "C0")):
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
            add(f"h2.boot.{cl}.{mode}.R2_cagr_median", a[REFERENCE]["cagr_median"], b["R2_cagr_median"])
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
def gates_exit(res):
    """Every candidate arm's gate block against the control K (PREREGISTRATION.md section 6):
    the ewma_band_v1 pre-tax gates G1-G9 with K as the control, and the after-tax gates T1-T3
    ($100k: all three windows, then the two clean bootstraps with the 55 % share floor). No
    non-inferiority relaxation: these arms change exposure, so a DD or bootstrap loss is a
    loss. An arm is adopted only if every gate passes."""
    pre, win = res["pre"], res["windows"][GATE_CAPITAL]
    bt = res["boot_ref"]
    g = {}
    for arm in CANDIDATES:
        r = {}
        # ---- G1 (m = 2) / G6 (G1 at m = 1): return, base and calibrated
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
        # ---- G2: max DD <= K + 1 pp (m = 2, base and calibrated)
        det, ok = {}, True
        for mdl in ("base", "calibrated"):
            a, k = pre[mdl]["m2"][arm], pre[mdl]["m2"][CONTROL]
            d = (a["max_dd"] - k["max_dd"]) * 100
            det[mdl] = {"max_dd": a["max_dd"], "max_dd_K": k["max_dd"], "delta_pp": d,
                        "pass": bool(d <= G2_MAX_DD_PP + 1e-9)}
            ok = ok and det[mdl]["pass"]
        r["G2_detail"] = det
        r["G2"] = bool(ok)
        # ---- G3: 2017+ CAGR >= K - 1 pp (base and calibrated)
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
        # ---- G4: above K in >= 2 of the 3 sub-periods (base, m = 2)
        det, n_win = {}, 0
        for nm in ("P1", "P2", "P3"):
            a = pre["base"]["m2"][arm]["subperiods"][nm]["cagr"]
            k = pre["base"]["m2"][CONTROL]["subperiods"][nm]["cagr"]
            d = (a - k) * 100
            det[nm] = {"cagr": a, "cagr_K": k, "delta_pp": d, "win": bool(d > 0)}
            n_win += int(d > 0)
        r["G4_detail"] = {"periods": det, "n_wins": n_win, "required": G4_MIN_WINS}
        r["G4"] = bool(n_win >= G4_MIN_WINS)
        # ---- G5: pre-tax block-20 bootstrap, median of arm - K (base and calibrated)
        det, ok = {}, True
        for mdl in ("base", "calibrated"):
            c = bt["pre"][mdl][f"{arm}_vs_{CONTROL}"]
            good = bool(c["median_delta_pp"] >= G5_MIN_MEDIAN_PP)
            det[mdl] = {"median_delta_pp": c["median_delta_pp"], "mean_delta_pp": c["mean_delta_pp"],
                        "share_gt_0": c["share_gt_0"], "p05_pp": c["p05_pp"], "p95_pp": c["p95_pp"],
                        "cagr_median": bt["pre"][mdl][arm]["cagr_median"],
                        "cagr_median_K": bt["pre"][mdl][CONTROL]["cagr_median"], "pass": good}
            ok = ok and good
        r["G5_detail"] = det
        r["G5"] = bool(ok)
        # ---- G7: the six ewma_band_v1 OOS windows
        d_c = {wn: res["oos"][wn][arm]["delta_cagr_pp"] for wn in OOS_WINDOWS}
        d_d = {wn: res["oos"][wn][arm]["delta_max_dd_pp"] for wn in OOS_WINDOWS}
        good_w = [wn for wn in OOS_WINDOWS if d_c[wn] >= G7_MIN_CAGR_PP and d_d[wn] <= G7_MAX_DD_PP]
        r["G7_detail"] = {"delta_cagr_pp": d_c, "delta_max_dd_pp": d_d,
                          "median_delta_pp": float(np.median(list(d_c.values()))),
                          "n_within": len(good_w), "required_windows": G7_MIN_WINDOWS,
                          "max_delta_max_dd_pp": float(max(d_d.values()))}
        r["G7"] = bool(np.median(list(d_c.values())) >= G7_MIN_MEDIAN_PP
                       and len(good_w) >= G7_MIN_WINDOWS
                       and max(d_d.values()) <= G7_MAX_DD_HARD_PP)
        # ---- G8: pooled OOS bootstrap (M1-M5, 2,000 paths each)
        pb = res["oos_boot"]["pooled"][arm]
        r["G8_detail"] = {"median_delta_pp": pb["median_delta_pp"],
                          "mean_delta_pp": pb["mean_delta_pp"],
                          "share_gt_0": pb["share_gt_0"], "paths": pb["paths"]}
        r["G8"] = bool(pb["median_delta_pp"] >= G8_MIN_MEDIAN_PP)
        # ---- G9: turnover <= 2 x K
        t_a = pre["base"]["m2"][arm]["turnover_annual"]
        t_k = pre["base"]["m2"][CONTROL]["turnover_annual"]
        r["G9_detail"] = {"turnover": t_a, "turnover_K": t_k, "ratio": t_a / t_k,
                          "ratio_le_1": bool(t_a / t_k <= 1.0 + 1e-9)}
        r["G9"] = bool(t_a / t_k <= G9_MAX_TURNOVER_MULT + 1e-9)
        # ---- T1: after tax at $100k >= K in all three windows
        t1 = {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                   "after_tax_cagr_K": win[wn][CONTROL]["after_tax_cagr"],
                   "delta_pp": (win[wn][arm]["after_tax_cagr"]
                                - win[wn][CONTROL]["after_tax_cagr"]) * 100,
                   "max_dd_after_tax": win[wn][arm]["max_dd_after_tax"],
                   "max_dd_after_tax_K": win[wn][CONTROL]["max_dd_after_tax"],
                   "delta_max_dd_pp": (win[wn][arm]["max_dd_after_tax"]
                                       - win[wn][CONTROL]["max_dd_after_tax"]) * 100}
              for wn in TAX_WINDOWS}
        r["T1_detail"] = {"windows": t1,
                          "min_delta_pp": float(min(v["delta_pp"] for v in t1.values())),
                          "tax_usd": {wn: win[wn][arm]["tax_usd"] for wn in TAX_WINDOWS},
                          "tax_usd_K": {wn: win[wn][CONTROL]["tax_usd"] for wn in TAX_WINDOWS},
                          "sells_per_year": {wn: win[wn][arm]["sells_per_year"] for wn in TAX_WINDOWS},
                          "postponements": win[TAX_WINDOWS_LAST][arm]["postponements"],
                          "deferred_exit": win[TAX_WINDOWS_LAST][arm]["deferred_exit"],
                          "staged_events": win[TAX_WINDOWS_LAST][arm]["staged_events"],
                          "index_relief_try": win[TAX_WINDOWS_LAST][arm]["index_relief_try"],
                          "index_relief_try_K": win[TAX_WINDOWS_LAST][CONTROL]["index_relief_try"]}
        r["T1"] = bool(all(v["delta_pp"] >= T1_MIN_DELTA_PP for v in t1.values()))
        # ---- T2 / T3: the two clean bootstraps at $100k (median >= 0, share > 0 >= 55 %)
        for mode, key in (("progressive", "T2"), ("flat", "T3")):
            c = bt["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{CONTROL}"]
            good = bool(c["median_delta_cagr_pp"] >= T2_MIN_MEDIAN_PP
                        and c["share_gt_0"] >= T2_MIN_SHARE)
            r[f"{key}_detail"] = {"mode": mode, "median_delta_pp": c["median_delta_cagr_pp"],
                                  "mean_delta_pp": c["mean_delta_cagr_pp"],
                                  "share_gt_0": c["share_gt_0"],
                                  "p05_pp": c["p05_delta_cagr_pp"],
                                  "p95_pp": c["p95_delta_cagr_pp"], "pass": good,
                                  "cagr_median": bt["tax"][GATE_CAPITAL][mode][arm]["cagr_median"],
                                  "cagr_median_K": bt["tax"][GATE_CAPITAL][mode][CONTROL]["cagr_median"]}
            r[key] = bool(good)
        # ---- the record: the arm against K on every reporting cell, and the counters
        r["vs_K"] = {
            "pre_tax": {mdl: {m_: {"cagr": pre[mdl][m_][arm]["cagr"],
                                   "max_dd": pre[mdl][m_][arm]["max_dd"],
                                   "turnover_annual": pre[mdl][m_][arm]["turnover_annual"],
                                   "delta_cagr_pp": (pre[mdl][m_][arm]["cagr"]
                                                     - pre[mdl][m_][CONTROL]["cagr"]) * 100,
                                   "delta_max_dd_pp": (pre[mdl][m_][arm]["max_dd"]
                                                       - pre[mdl][m_][CONTROL]["max_dd"]) * 100}
                               for m_ in ("m1", "m2", "m4")} for mdl in ("base", "calibrated")},
            "after_tax_100k": {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                                    "delta_vs_K_pp": t1[wn]["delta_pp"],
                                    "pre_tax_cagr": win[wn][arm]["pre_tax_cagr"],
                                    "max_dd_after_tax": win[wn][arm]["max_dd_after_tax"],
                                    "tax_usd": win[wn][arm]["tax_usd"],
                                    "sells_per_year": win[wn][arm]["sells_per_year"],
                                    "deferred_exit": win[wn][arm]["deferred_exit"],
                                    "staged_events": win[wn][arm]["staged_events"],
                                    "staged_sessions": win[wn][arm]["staged_sessions"],
                                    "staged_release_cap": win[wn][arm]["staged_release_cap"],
                                    "staged_release_gate": win[wn][arm]["staged_release_gate"],
                                    "staged_release_target": win[wn][arm]["staged_release_target"],
                                    "realised_gain_try_by_reason":
                                        win[wn][arm]["realised_gain_try_by_reason"],
                                    "neg_gain_try_by_reason": win[wn][arm]["neg_gain_try_by_reason"]}
                               for wn in TAX_WINDOWS},
            "bootstrap_seed20261011": {mode: {cl: {
                "median_delta_pp": bt["tax"][cl][mode][f"{arm}_vs_{CONTROL}"]["median_delta_cagr_pp"],
                "share_gt_0": bt["tax"][cl][mode][f"{arm}_vs_{CONTROL}"]["share_gt_0"],
                "p05_pp": bt["tax"][cl][mode][f"{arm}_vs_{CONTROL}"]["p05_delta_cagr_pp"],
                "p95_pp": bt["tax"][cl][mode][f"{arm}_vs_{CONTROL}"]["p95_delta_cagr_pp"]}
                for cl in ("100k", "1M")} for mode in ("progressive", "flat")},
            "pre_tax_bootstrap": {mdl: {"median_delta_pp": bt["pre"][mdl][f"{arm}_vs_{CONTROL}"]["median_delta_pp"],
                                        "share_gt_0": bt["pre"][mdl][f"{arm}_vs_{CONTROL}"]["share_gt_0"]}
                                  for mdl in ("base", "calibrated")},
            "oos_median_delta_pp": float(np.median([d_c[wn] for wn in OOS_WINDOWS])),
            "e2_rolling_starts": {"median_delta_pp": float(np.median(
                [(res["e2"][str(y)][arm]["after_tax_cagr"] - res["e2"][str(y)][CONTROL]["after_tax_cagr"]) * 100
                 for y in E2_START_YEARS])),
                "n_ge_0": int(sum(1 for y in E2_START_YEARS
                                  if (res["e2"][str(y)][arm]["after_tax_cagr"]
                                      - res["e2"][str(y)][CONTROL]["after_tax_cagr"]) >= 0)),
                "n_starts": len(E2_START_YEARS)},
            "e1_21_etfs": {"median_delta_pp": float(np.median(
                [res["e1"][k][arm]["delta_after_tax_pp"] for k in INSTRUMENTS])),
                "n_gt_0": int(sum(1 for k in INSTRUMENTS
                                  if res["e1"][k][arm]["delta_after_tax_pp"] > 0)),
                "n_instruments": len(INSTRUMENTS),
                "worst_delta_max_dd_pp": float(max(
                    res["e1"][k][arm]["delta_max_dd_after_tax_pp"] for k in INSTRUMENTS))},
            "cost_stress_m4": {wn: {"after_tax_cagr": res["cost"][wn][arm]["after_tax_cagr"],
                                    "delta_vs_K_pp": (res["cost"][wn][arm]["after_tax_cagr"]
                                                      - res["cost"][wn][CONTROL]["after_tax_cagr"]) * 100}
                               for wn in TAX_WINDOWS},
        }
        r["adopted"] = bool(all(r[x] for x in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8",
                                               "G9", "T1", "T2", "T3")))
        g[arm] = r
    adopted = [a for a in CANDIDATES if g[a]["adopted"]]
    best = None
    if adopted:
        best = max(adopted, key=lambda a: win[TAX_WINDOW_MAIN][a]["after_tax_cagr"])
    failed = {a: [x for x in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9",
                              "T1", "T2", "T3") if not g[a][x]] for a in CANDIDATES}
    reason = ("adopt " + best + " (highest after-tax $100k CAGR among the arms that clear every "
              "gate)" if best else
              "no arm adopted; every candidate fails at least one pre-registered gate: "
              + "; ".join(f"{a}: {','.join(failed[a])}" for a in CANDIDATES))
    return {"arms": g, "adopted": best, "failed_gates": failed, "reason": reason,
            "gate": {
                "G1": f"CAGR >= K's, or Calmar >= {G1_MIN_CALMAR_MULT} x K's with CAGR >= K "
                      f"{G1_MIN_REL_CAGR_PP} pp (m = 2, base and calibrated)",
                "G2": f"pre-tax max DD <= K + {G2_MAX_DD_PP} pp (m = 2, base and calibrated)",
                "G3": f"2017+ CAGR >= K {G3_MIN_CAGR_PP} pp (m = 2, base and calibrated)",
                "G4": f"CAGR above K in >= {G4_MIN_WINS} of 3 sub-periods P1/P2/P3",
                "G5": f"block-20 pre-tax bootstrap (seed {SEED}, {PRE_BOOT_PATHS} paths) median of "
                      f"arm - K >= {G5_MIN_MEDIAN_PP} pp (base and calibrated)",
                "G6": "G1 at m = 1 (base and calibrated)",
                "G7": f"six OOS windows: median delta CAGR >= {G7_MIN_MEDIAN_PP} pp, "
                      f">= {G7_MIN_WINDOWS}/6 with delta CAGR >= {G7_MIN_CAGR_PP} pp and delta DD "
                      f"<= +{G7_MAX_DD_PP} pp, delta DD never above +{G7_MAX_DD_HARD_PP} pp",
                "G8": f"pooled OOS bootstrap (M1-M5, {OOS_PATHS_PER_MARKET} paths each) median "
                      f">= {G8_MIN_MEDIAN_PP} pp",
                "G9": f"turnover <= {G9_MAX_TURNOVER_MULT} x K",
                "T1": f"after tax at $100k: arm - K >= {T1_MIN_DELTA_PP} pp in all three windows",
                "T2": f"B1-recent (progressive) at $100k: median >= {T2_MIN_MEDIAN_PP} pp and "
                      f"share > 0 >= {T2_MIN_SHARE:.0%}",
                "T3": f"B2 (flat 20 %) at $100k: median >= {T2_MIN_MEDIAN_PP} pp and share > 0 "
                      f">= {T2_MIN_SHARE:.0%}",
                "adopt": "candidates are adopted only if all of G1-G9 and T1-T3 pass; the "
                         "selection among survivors is the highest after-tax $100k CAGR on the "
                         "main window"}}


# ----------------------------------------------------------------------------- episodes (report only)
def episode_stage(arms):
    """Per-arm max drawdown and return inside the five named episodes (the drawdown reporting
    this study's arms need, since they delay de-risking), plus the whole-path max DD on the
    same run and the X1/X2 event ledger. Main window, base financing, m = 2, pre-tax.

    The episode drawdown is the peak-to-trough of the arm's own NAV inside the episode window
    (dates clipped to the measured window); the episode return is NAV(end) / NAV(start) - 1."""
    days, o, c, w_e2, w_r2, e2, xc = main_inputs(0.0)
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    dates = days[START - 1:T + 1]      # aligned with the engine's NAV path (pre_stats' convention)
    out = {"whole_path": {}, "episodes": {}}
    runs = {arm: engine_run(PRE_CAPITAL, START, T, days, mkt, o, c, w_r2, e2, arm, "pre_tax",
                            x2=xc) for arm in arms}
    for arm in arms:
        r = runs[arm]
        out["whole_path"][arm] = {
            "cagr": float(r["cagr_usd_unliquidated"]),
            "max_dd": float(r["max_dd_nav_usd"]),
            "max_dd_delta_vs_K_pp": (float(r["max_dd_nav_usd"])
                                     - float(runs[CONTROL]["max_dd_nav_usd"])) * 100,
            "deferred_exit": int(r["deferred_exit"]), "staged_events": int(r["staged_events"]),
            "deferred": int(r["deferred"]), "blocked_band": int(r["blocked_band"])}
    for name, a, b in EPISODES:
        rec = {}
        for arm in arms:
            st = seg(dates, runs[arm]["_nav"], a, b)
            kst = seg(dates, runs[CONTROL]["_nav"], a, b)
            rec[arm] = {"from": st["from"], "to": st["to"], "total_return": st["total_return"],
                        "max_dd": st["max_dd"],
                        "delta_total_return_pp": (st["total_return"] - kst["total_return"]) * 100,
                        "delta_max_dd_pp": (st["max_dd"] - kst["max_dd"]) * 100,
                        "max_dd_K": kst["max_dd"]}
        out["episodes"][name] = rec
    return out


# ----------------------------------------------------------------------------- the run
def run(boot_ref=None) -> dict:
    print(f"pre-tax main window ({len(ARMS)} arms)", flush=True)
    pre, pre_usd = pre_tax_main(ARMS)
    print("after-tax windows (5 capitals)", flush=True)
    win = after_tax_windows(tuple(TAX_CAPITALS), ARMS)
    print("cost stress m = 4", flush=True)
    cost = cost_stress(ARMS)
    print("episodes (drawdown reporting)", flush=True)
    ep = episode_stage(ARMS)
    print("OOS windows", flush=True)
    oos = oos_stages(ARMS)
    print("instruments (R-USD, pre-tax)", flush=True)
    inst, quality = instrument_stages_usd(ARMS)
    if boot_ref is None:
        print(f"clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)", flush=True)
        boot_ref = boot(seed=SEED, block=BLOCK, arms=ARMS, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
                        capitals=("100k", "1M"), modes=("progressive", "flat"), deltas=BOOT_DELTAS,
                        baselines=(CONTROL, REFERENCE), label="clean")
    print("E1: after-tax 21 ETFs (R-INDEX)", flush=True)
    e1 = stage_e1(ARMS)
    print("E2: rolling January starts", flush=True)
    e2 = stage_e2(ARMS)
    print(f"E3: fresh bootstraps (seed {E3_SEED}, blocks {E3_BLOCKS})", flush=True)
    e3 = stage_e3(ARMS)
    print("G8: pooled OOS bootstrap (M1-M5)", flush=True)
    oos_boot = oos_bootstrap((CONTROL,) + CANDIDATES)
    print("E5: decision traces", flush=True)
    e5 = {"decisions": stage_e5_decisions()}
    res = {"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost, "oos": oos,
           "oos_boot": oos_boot, "episodes": ep,
           "instruments": inst, "quality": quality, "boot_ref": boot_ref,
           "e1": e1, "e2": e2, "e3": e3, "e5": e5}
    print("reference reproduction checks (A1u vs gain_band_v3, H2 vs tax_harvest_v2)", flush=True)
    res["reference_check"] = reference_checks(res)
    res["gates"] = gates_exit(res)
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
            for label, ref in (("R2", BOOT_ANCHOR[mode_]["R2"]),
                               ("K", BOOT_ANCHOR_K[(mode_, "100k")]),
                               ("E2", BOOT_ANCHOR[mode_]["E2"])):
                b = hs["bootstrap_control"][mode_][label]
                print(f"boot anchor {mode_} {label}: ref {ref:.6f} repro {b:.6f} "
                      f"diff {(b - ref) * 100:+.3f} pp")
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
              f"repro {[f'{x * 100:.3f}%' for x in v['reproduced']]} "
              f"deferrals {v['deferrals']} vs {v['reference_deferrals']} -> "
              f"{'PASS' if v['pass'] else 'FAIL'}")
    print(f"K anchor block (the control label the run uses): {'PASS' if ka['pass'] else 'FAIL'}")
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
        "campaign": "exit_timing_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "candidates": list(CANDIDATES),
            "reference_row": REFERENCE, "rule": RULE,
            "x2_gain_threshold": X2_GAIN, "x2_cap_sessions": X2_CAP_SESSIONS,
            "dec_tail": DEC_TAIL, "gain_tiers": [[t, b] for t, b in GAIN_TIERS], "b_up": B_UP,
            "composition": ("every arm runs production R2's target series with K's execution "
                            "layer (H2's December window tested first, then A1u's gain band); "
                            "X1 postpones an exit to zero whose fill session is in the last "
                            "DEC_TAIL December sessions, X2 stages a gain-heavy exit to zero "
                            "down to the early-leg weight (0.5 x vt) for at most "
                            "X2_CAP_SESSIONS sessions while the early leg's own gate stands, "
                            "X3 = X1 + X2 with X1's window first. Exits to zero and up-resizes "
                            "always fill otherwise; a suppressed or postponed fill is never "
                            "re-tried: the target is re-evaluated at every close."),
            "signals": "production R2 (equity_leveraged_trend_v1 1.2.0): E2 2-of-3 on price closes "
                       "(SMA200, 252-session price return > 0, close > 126-session max/min midpoint) "
                       "+ early re-entry at 0.5 x vt while E2 off and close > SMA50, vol20 < its "
                       "252-session median and close > close 20 sessions ago; exit early below SMA50",
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
                                              "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI},
                          "fresh_bootstrap": {"paths": E3_PATHS, "seed": E3_SEED, "blocks": list(E3_BLOCKS)}},
            "e1": {"rule": "R-INDEX", "start": E1_RINDEX_START, "financing": "calibrated", "m": M,
                   "capital": GATE_CAPITAL, "n_instruments": len(INSTRUMENTS),
                   "excluded": list(EXCLUDED_INSTRUMENTS)},
            "e2": {"starts": list(E2_START_YEARS), "capital": GATE_CAPITAL},
            "episodes": [list(x) for x in EPISODES],
            "oos_bootstrap": {"markets": list(MARKETS), "paths_per_market": OOS_PATHS_PER_MARKET,
                              "block": BLOCK, "seed": SEED, "treatment": "pre-tax, R-USD rule"},
            "gates": res["gates"]["gate"],
            "adopted": res["gates"]["adopted"], "reason": res["gates"]["reason"],
        },
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "reproduction_checks": hs["reproduction_checks"],
                    "k_anchors": hs["k_anchors"], "r2_anchors": hs["r2_anchors"],
                    "after_tax_anchors": hs["after_tax_anchors"],
                    "bootstrap_control_medians": hs["bootstrap_control"],
                    "deep_reference": {k: v for k, v in hs["deep"].items() if k != "boot_ref"}},
        "reference_check": {k: v for k, v in rc.items()},
        "pre_tax_main": pre,
        "pre_tax_main_r_usd": res["pre_usd"],
        "after_tax_windows": win,
        "cost_stress_m4": res["cost"],
        "episodes": res["episodes"],
        "oos": res["oos"],
        "oos_bootstrap": res["oos_boot"],
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
    L = [f"exit_timing_v1 | exit-leg tax timing under K (idea_survey_v4 I4) | main window "
         f"{w['start']} -> {w['end']} ({w['sessions']} sessions) | control = production K | "
         f"arms = K + C0(R2) + A1u + H2 + X1 + X2 + X3",
         f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance); "
         f"control K anchors {'PASS' if hs['k_anchors']['pass'] else 'FAIL'}; "
         f"R2 reference anchor {'PASS' if hs['r2_anchors']['pass'] else 'FAIL'}; "
         f"H2 engine == simulate_band_overlay max|diff| "
         f"{hs['reproduction_checks']['h2_engine_equals_simulate_band_overlay']['max_abs_nav_diff']:.2e}",
         f"deep reference block: {rc['n_cells']} cells ({', '.join(rc['sections'])}), "
         f"{rc['n_mismatch']} mismatches (tol {rc['tolerance']}), max|diff| {rc['max_abs_diff']:.2e} -> "
         f"{'PASS' if rc['pass'] else 'FAIL'}",
         f"ADOPTED: {res['gates']['adopted']} -- {res['gates']['reason']}", ""]

    L.append("per arm (pre-tax main window m = 2, R-INDEX; after tax at $100k, base, m = 2, liquidated; "
             "deltas vs the control K):")
    L.append("  arm   CAGR base  CAGR cal   maxDD base  maxDD cal   turn/yr | dAT2000  dAT2010  dAT2017 "
             "(pp) | boot B1 med/share | boot B2 med/share | OOS med d (pp) | T1 T2 T3")
    for arm in ARMS:
        a, ac = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        ats = "".join(f"{(ctrl[wn][arm]['after_tax_cagr'] - ctrl[wn][CONTROL]['after_tax_cagr']) * 100:+9.3f}"
                      for wn in TAX_WINDOWS)
        if arm == CONTROL:
            bb = "        -       -  |         -       - "
        else:
            bb = " | ".join(
                f"{boot_ref['tax'][GATE_CAPITAL][md][f'{arm}_vs_{CONTROL}']['median_delta_cagr_pp']:+7.3f} "
                f"{boot_ref['tax'][GATE_CAPITAL][md][f'{arm}_vs_{CONTROL}']['share_gt_0'] * 100:6.1f}%"
                for md in ("progressive", "flat"))
        eo = float(np.median([res["oos"][wn][arm].get("delta_cagr_pp", 0.0) for wn in OOS_WINDOWS]))
        fl = ("    -    -    -" if arm == CONTROL
              else "  ".join(f"{x}={'P' if g[arm][x] else 'F'}" for x in ("T1", "T2", "T3")))
        L.append(f"  {arm:4s} {p(a['cagr'])} {p(ac['cagr'])} {p(a['max_dd'])} {p(ac['max_dd'])} "
                 f"{a['turnover_annual']:9.2f} |{ats} | {bb} | {eo:+12.3f} | {fl}")
    L.append("")
    for arm in CANDIDATES:
        r = g[arm]
        L.append(f"  {arm} gates: " + " ".join(
            f"{x}={'PASS' if r[x] else 'FAIL'}" for x in ("G1", "G2", "G3", "G4", "G5", "G6", "G7",
                                                          "G8", "G9", "T1", "T2", "T3")))
        L.append(f"      G1 m2 base {r['G1_detail_m2']['base']['delta_pp']:+.3f}pp "
                 f"cal {r['G1_detail_m2']['calibrated']['delta_pp']:+.3f}pp | "
                 f"G1 m1 base {r['G1_detail_m1']['base']['delta_pp']:+.3f}pp "
                 f"cal {r['G1_detail_m1']['calibrated']['delta_pp']:+.3f}pp | "
                 f"G2 base {r['G2_detail']['base']['delta_pp']:+.2f}pp cal "
                 f"{r['G2_detail']['calibrated']['delta_pp']:+.2f}pp | "
                 f"G3 base {r['G3_detail']['base']['delta_pp']:+.3f}pp cal "
                 f"{r['G3_detail']['calibrated']['delta_pp']:+.3f}pp | "
                 f"G4 wins {r['G4_detail']['n_wins']}/3 | G9 ratio {r['G9_detail']['ratio']:.4f}")
        L.append(f"      G5 pre-tax boot med/base {r['G5_detail']['base']['median_delta_pp']:+.4f}pp "
                 f"(share>0 {r['G5_detail']['base']['share_gt_0'] * 100:.1f}%) cal "
                 f"{r['G5_detail']['calibrated']['median_delta_pp']:+.4f}pp "
                 f"(share>0 {r['G5_detail']['calibrated']['share_gt_0'] * 100:.1f}%) | "
                 f"G7 median {r['G7_detail']['median_delta_pp']:+.3f}pp within "
                 f"{r['G7_detail']['n_within']}/6 worst dDD {r['G7_detail']['max_delta_max_dd_pp']:+.2f}pp | "
                 f"G8 median {r['G8_detail']['median_delta_pp']:+.3f}pp")
        for x in ("T1", "T2", "T3"):
            d = r[f"{x}_detail"]
            if x == "T1":
                L.append(f"      T1 deltas "
                         + ", ".join(f"{wn} {d['windows'][wn]['delta_pp']:+.3f}pp" for wn in TAX_WINDOWS)
                         + " | dmaxDD "
                         + ", ".join(f"{wn} {d['windows'][wn]['delta_max_dd_pp']:+.2f}pp"
                                     for wn in TAX_WINDOWS))
            else:
                L.append(f"      {x} ({d['mode']}): median {d['median_delta_pp']:+.4f}pp mean "
                         f"{d['mean_delta_pp']:+.4f}pp share>0 {d['share_gt_0'] * 100:.1f}% "
                         f"p05/p95 {d['p05_pp']:+.2f}/{d['p95_pp']:+.2f} -> "
                         f"{'PASS' if d['pass'] else 'FAIL'}")
        L.append(f"      vs K report: E2 rolling starts median "
                 f"{r['vs_K']['e2_rolling_starts']['median_delta_pp']:+.3f}pp "
                 f"({r['vs_K']['e2_rolling_starts']['n_ge_0']}/21 >= 0) | E1 21 ETFs median "
                 f"{r['vs_K']['e1_21_etfs']['median_delta_pp']:+.3f}pp "
                 f"({r['vs_K']['e1_21_etfs']['n_gt_0']}/21 > 0, worst dmaxDD "
                 f"{r['vs_K']['e1_21_etfs']['worst_delta_max_dd_pp']:+.2f}pp) | m=4 cost "
                 + ", ".join(f"{wn} {r['vs_K']['cost_stress_m4'][wn]['delta_vs_K_pp']:+.3f}pp"
                             for wn in TAX_WINDOWS))
    L.append("")
    L.append("postponement / staging counters (main window, m = 2, base, $100k, progressive):")
    for arm in ARMS:
        r = ctrl[TAX_WINDOW_MAIN][arm]
        L.append(f"  {arm:4s} postponements {r['postponements']:5d} (band {r['blocked_band']:5d}, "
                 f"December-resize {r['deferred']:4d}, December-exit {r['deferred_exit']:4d}) "
                 f"sells/yr {r['sells_per_year']:6.2f} staged events {r['staged_events']:3d} "
                 f"sessions {r['staged_sessions']:4d} (cap {r['staged_release_cap']}, "
                 f"gate {r['staged_release_gate']}, target-takeover {r['staged_release_target']})")
    L.append("")
    L.append("named episodes (main window, base financing, m = 2, pre-tax; delta vs K):")
    L.append("  episode              arm   total ret   d ret pp    max DD    d DD pp")
    for name, _a, _b in EPISODES:
        for arm in ARMS:
            e = res["episodes"]["episodes"][name][arm]
            L.append(f"  {name:20s} {arm:4s} {e['total_return'] * 100:+9.2f}% "
                     f"{e['delta_total_return_pp']:+9.3f} {e['max_dd'] * 100:8.2f}% "
                     f"{e['delta_max_dd_pp']:+9.3f}")
    L.append("")
    L.append("whole-path (main window, pre-tax, m = 2) max DD and the X counters:")
    for arm in ARMS:
        e = res["episodes"]["whole_path"][arm]
        L.append(f"  {arm:4s} cagr {e['cagr'] * 100:7.4f}% maxDD {e['max_dd'] * 100:6.3f}% "
                 f"(d vs K {e['max_dd_delta_vs_K_pp']:+.3f}pp) deferred-exit {e['deferred_exit']:3d} "
                 f"staged events {e['staged_events']:3d}")
    L.append("")
    L.append("after-tax counters at $100k (base, m = 2, liquidated):")
    L.append("  arm   tax USD 2017+    relief TRY 2017+   deferred-exit 2017+   staged 2017+   "
             "sells/yr 2017+")
    for arm in ARMS:
        r = ctrl[TAX_WINDOWS_LAST][arm]
        L.append(f"  {arm:4s} {r['tax_usd']:12.0f} {r['index_relief_try']:19.0f} "
                 f"{r['deferred_exit']:21d} {r['staged_events']:13d} {r['sells_per_year']:14.2f}")
    L.append("")
    L.append("E1 after tax on the 21 quality-passing ETFs (R-INDEX, $100k, calibrated, m = 2, "
             "liquidated) -- delta vs K (report only, not gated):")
    L.append("  instrument  window                     K AT      X1 d      X2 d      X3 d")
    for key in INSTRUMENTS:
        r = e1[key]
        L.append(f"  {key:10s}  {r['window']['start']}..{r['window']['end']} {p(r[CONTROL]['after_tax_cagr'])}"
                 f"  {r['X1']['delta_after_tax_pp']:+7.3f}  {r['X2']['delta_after_tax_pp']:+7.3f}  "
                 f"{r['X3']['delta_after_tax_pp']:+7.3f}")
    L.append(f"  median delta: " + ", ".join(
        f"{arm} {float(np.median([e1[k][arm]['delta_after_tax_pp'] for k in INSTRUMENTS])):+.3f}pp "
        f"({sum(1 for k in INSTRUMENTS if e1[k][arm]['delta_after_tax_pp'] > 0)}/21 > 0)"
        for arm in CANDIDATES))
    L.append("")
    L.append("E2 rolling January starts (after tax $100k, base, m = 2, to 2026-09-29) -- delta vs K:")
    L.append("  year   start        K AT      X1 d      X2 d      X3 d")
    for yr in E2_START_YEARS:
        r = e2[str(yr)]
        L.append(f"  {yr}   {r['start']}  {p(r[CONTROL]['after_tax_cagr'])} "
                 f"{r['X1']['delta_after_tax_pp']:+8.3f} {r['X2']['delta_after_tax_pp']:+8.3f} "
                 f"{r['X3']['delta_after_tax_pp']:+8.3f}")
    L.append("")
    L.append(f"E3 fresh after-tax bootstraps (seed {E3_SEED}, {E3_PATHS} paths, $100k, blocks "
             f"{E3_BLOCKS}) -- delta vs K (report only):")
    L.append("  run                    arm    median d  share>0     p05       p95")
    for key in sorted(e3):
        for arm in CANDIDATES:
            v = e3[key][f"{arm}_vs_{CONTROL}"]
            L.append(f"  {key:20s} {arm:4s} {v['median_delta_cagr_pp']:+10.3f} "
                     f"{v['share_gt_0'] * 100:7.1f}% {v['p05_delta_cagr_pp']:+9.2f} "
                     f"{v['p95_delta_cagr_pp']:+9.2f}")
    L.append("")
    L.append("E5 (report only): fill-decision differences on the main path (pre-tax, $100k, m = 2)")
    dec = res["e5"]["decisions"]
    for pair in ("X1_vs_K", "X2_vs_K", "X3_vs_K", "K_vs_C0", "K_vs_A1u", "H2_vs_C0"):
        a, b = pair.split("_vs_")
        d = dec[pair]
        L.append(f"  {pair:12s}: {d['sessions_decision_differs']:5d} sessions differ "
                 f"({d['share_decision_differs'] * 100:.2f}%); {a} sells where {b} holds "
                 f"{d[f'{a}_sells_{b}_holds']}, {a} holds where {b} sells {d[f'{a}_holds_{b}_sells']}")
    L.append(f"  sells (downward resizes+exits) per arm: "
             + ", ".join(f"{a} {dec['sells'][a]}" for a in ARMS))
    L.append(f"  postponements per arm: "
             + ", ".join(f"{a} {dec['postponements'][a]['band']}+{dec['postponements'][a]['deferred']}"
                         for a in ARMS))
    for cl in E5_CAPITALS:
        L.append(f"  after tax at ${cl} (progressive, liquidated, base, m = 2):")
        for wn in TAX_WINDOWS:
            c0 = win[cl][wn][CONTROL]["after_tax_cagr"]
            L.append(f"    {wn:20s} K {p(c0)} | " + " ".join(
                f"{arm} {p(win[cl][wn][arm]['after_tax_cagr'])} "
                f"{(win[cl][wn][arm]['after_tax_cagr'] - c0) * 100:+7.3f}pp" for arm in CANDIDATES))
    L.append("")
    L.append(f"m = 4 cost stress, after tax at $100k (delta vs K):")
    for wn in TAX_WINDOWS:
        c0 = res["cost"][wn][CONTROL]["after_tax_cagr"]
        L.append(f"  {wn:20s} K {p(c0)} | " + " ".join(
            f"{arm} {p(res['cost'][wn][arm]['after_tax_cagr'])} "
            f"{(res['cost'][wn][arm]['after_tax_cagr'] - c0) * 100:+7.3f}pp" for arm in CANDIDATES))
    L.append("")
    L.append("six OOS windows (pre-tax, calibrated, m = 2, R-USD rule) -- delta vs K:")
    L.append("  window            K CAGR    K maxDD     X1 d      X2 d      X3 d   (CAGR pp)")
    for wn_ in OOS_WINDOWS:
        r = res["oos"][wn_]
        L.append(f"  {wn_:16s} {p(r[CONTROL]['cagr'])}  {p(r[CONTROL]['max_dd'])}  "
                 f"{r['X1']['delta_cagr_pp']:+8.3f} {r['X2']['delta_cagr_pp']:+9.3f} "
                 f"{r['X3']['delta_cagr_pp']:+9.3f}")
    L.append("")
    L.append("G8 pooled OOS bootstrap (pre-tax, R-USD, M1-M5, 2,000 paths each) -- delta vs K:")
    L.append("  " + ", ".join(
        f"{a} median {res['oos_boot']['pooled'][a]['median_delta_pp']:+.3f}pp "
        f"(share>0 {res['oos_boot']['pooled'][a]['share_gt_0'] * 100:.1f}%)" for a in CANDIDATES))
    L.append("")
    L.append("reference reproduction: " + f"{rc['n_cells']} cells, {rc['n_mismatch']} mismatches, "
             f"max|diff| {rc['max_abs_diff']:.2e} -> {'PASS' if rc['pass'] else 'FAIL'}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
