"""fe_config -- the production constants and data paths the research studies all copy.

One place for the numbers that every study screen repeats verbatim
(`trend_robustness_v2.screen.py` lines 15-34 plus the OOS/instrument tables of
`band_h2_combo_v1.screen.py`), so a study imports them instead of cloning them again.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # this fork sits one level deeper than the engine
HIST = ROOT / "data/historical"

# data files (data/historical only; nothing outside it is read)
BARS = HIST / "stress_robustness_v1_daily.csv"
DTB3_CSV = HIST / "stress_robustness_v1_dtb3.csv"
FX_CSV = HIST / "after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = HIST / "after_tax_tr_v1_yiufe.csv"
GSPC_CSV = HIST / "ewma_band_v1_gspc.csv"
INDEX_CSV = HIST / "ewma_band_v1_index.csv"
TB3MS_CSV = HIST / "ewma_band_v1_tb3ms.csv"

# market mechanics (trend_robustness_v2.screen.py lines 30-31)
VOL_N = 20
VOL_TARGET = 0.35
BAND = 0.005
CAP = 0.98
COST_PER_SIDE = 4.0 / 10_000  # per unit of traded notional; multiplied by m
EXP_Q = 0.0095  # QLD* daily-reset 2x expense ratio

# signals
SMA_N, TSM_N, DON_N = 200, 252, 126
SMA_FAST, LAG_FAST, MED_WINDOW = 50, 20, 252

# windows (the QQQ main window and the two financing models)
START = TSM_N + 1  # 253 -> first measured session 2000-03-09
FIN = {"base": 0.0, "calibrated": 0.0075}
EXTRA_FIN = 0.0075  # calibrated financing for the OOS / instrument windows
M = 2
M_STRESS = 4

# OOS windows (ewma_band_v1 / e2_confirmation_v1)
OOS_GSPC_FIRST, OOS_GSPC_LAST = "1957-01-02", "1999-12-31"
DTB3_FIRST = "1954-01-04"
MARKETS = {
    "M1": ("^IXIC", "1971-01-01", "1975-01-02", "1999-12-31"),
    "M2": ("^GSPC", "1934-01-01", None, "1956-12-31"),
    "M3": ("^N225", "1900-01-01", None, "2026-09-29"),
    "M4": ("^GDAXI", "1900-01-01", None, "2026-09-29"),
    "M5": ("^FTSE", "1900-01-01", None, "2026-09-29"),
}
OOS_WINDOWS = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")

# the quality-passing ETFs (EWC excluded by range_vol_v2's fixed rule)
INSTRUMENT_FILES = {
    "XLB": "range_vol_v2_xlb.csv",
    "XLE": "range_vol_v2_xle.csv",
    "XLF": "range_vol_v2_xlf.csv",
    "XLI": "range_vol_v2_xli.csv",
    "XLK": "range_vol_v2_xlk.csv",
    "XLP": "range_vol_v2_xlp.csv",
    "XLU": "range_vol_v2_xlu.csv",
    "XLV": "range_vol_v2_xlv.csv",
    "XLY": "range_vol_v2_xly.csv",
    "EWJ": "range_vol_v2_ewj.csv",
    "EWG": "range_vol_v2_ewg.csv",
    "EWU": "range_vol_v2_ewu.csv",
    "EWC": "range_vol_v2_ewc.csv",
    "EWA": "range_vol_v2_ewa.csv",
    "EWH": "range_vol_v2_ewh.csv",
    "EWS": "range_vol_v2_ews.csv",
    "EWL": "range_vol_v2_ewl.csv",
    "EFA": "range_vol_v2_efa.csv",
    "EEM": "range_vol_v2_eem.csv",
    "SPY": "range_vol_v1_spy.csv",
    "DIA": "range_vol_v1_dia.csv",
    "IWM": "range_vol_v1_iwm.csv",
}
INSTRUMENTS = tuple(sorted(k for k in INSTRUMENT_FILES if k != "EWC"))
EXCLUDED_INSTRUMENTS = ("EWC",)
QUALITY_MAX_BAD_FRAC = 0.05

# the block bootstrap (band_h2_combo_v1 / tax_harvest_v2)
SEED, BLOCK = 20261011, 20
E3_SEED, E3_BLOCKS, E3_PATHS = 20261015, (10, 40), 1000
TAX_BOOT_PATHS = 1000
PRE_BOOT_PATHS = 5000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_L = 253 + 10 * 252
TAX_BOOT_EXT = 3
TAX_BOOT_I0 = TAX_BOOT_EXT + 253

# the after-tax stage (band_h2_combo_v1 / vt_level_tax_v1)
TAX_CAPITALS = {"10k": 10_000.0, "30k": 30_000.0, "100k": 100_000.0, "300k": 300_000.0, "1M": 1_000_000.0}
GATE_CAPITAL = "100k"
# this fork's study: the control is K, the candidates are the four state-keyed deferral arms
CANDIDATES = ("D1", "D2", "D3", "D4")

# the pooled out-of-sample bootstrap (G8): block-20 over M1-M5, seed SEED, 2,000 paths per market
OOS_PATHS_PER_MARKET = 2_000
WARM, MEASURED = 250, 2520
OOS_BOOT_L = TSM_N + MEASURED  # 2,772
OOS_BOOT_CHUNK = 500
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
E2_START_YEARS = tuple(range(2001, 2022))
E1_RINDEX_START = "2000-03-09"
