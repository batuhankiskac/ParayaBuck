"""put_hedge_v1 -- fixed parameters (the numbers PREREGISTRATION.md fixes before any arm runs).

Everything here is a study constant, not a tuned value.  The pricing model is:

    sigma(K, t) = VXN_t * (1 + SKEW_S * ln(S_t / K))          (multiplicative skew)
    Black-Scholes put, constant dividend yield DIV_YIELD, rate = DTB3 at the session
    premium charged at the roll close, OPT_COST per side of the premium
    monthly (or 2-/3-month) roll on the third Friday, preceding session if a holiday
    hold to expiry, cash settlement = intrinsic

Skew source: the SPX index-option skew is about 4-6 implied-vol points per 10 % of strike
below spot in the empirical literature (Onan, Salih & Yasar 2015, Table IV / Fig. 2: OTM put
IV 14 % against ATM 10 % in 2006, VIX 13.1 %); in the multiplicative form used here at a
~20 % vol level that is SKEW_S ~ 2.0.  The PPUT sanity check reports the +/-50 % rows.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data/historical"

# data
VXN_CSV = HIST / "put_hedge_v1_vxn.csv"
VIX_CSV = HIST / "put_hedge_v1_vix.csv"
SPX_CSV = HIST / "put_hedge_v1_spx.csv"
SPXTR_CSV = HIST / "put_hedge_v1_sp500tr.csv"
NDX_CSV = HIST / "put_hedge_v1_ndx.csv"
PPUT_CSV = HIST / "put_hedge_v1_pput.csv"

# pricing model (fixed)
SKEW_S = 2.0
DIV_YIELD = 0.006          # QQQ trailing dividend yield, constant
OPT_COST = 0.05            # 5 % of the premium, per side (buy and sell)
ROLL_RULE = "third_friday_preceding_session"
TENOR_MONTHS = 1.0 / 12.0  # one calendar month in years, by the actual roll-to-roll gap

# overlay sizing
HEDGE_RATIO = 1.0
QLD_BETA = 2.0
BUDGET_FRAC = 0.01         # H3: 1 % of NAV per year of premium

# arms (fixed)
ARMS = ("K", "H1", "H2", "H3", "H4", "H5")
ARM_SPEC = {
    "K": {"tenor_months": 0, "long_otm": None, "short_otm": None, "sizing": "none", "vt": 0.35, "e2_only": False},
    "H1": {"tenor_months": 1, "long_otm": 0.10, "short_otm": None, "sizing": "notional", "vt": 0.35, "e2_only": False},
    "H2": {"tenor_months": 2, "long_otm": 0.10, "short_otm": 0.25, "sizing": "notional", "vt": 0.35, "e2_only": False},
    "H3": {"tenor_months": 3, "long_otm": 0.15, "short_otm": None, "sizing": "budget", "vt": 0.35, "e2_only": False},
    "H4": {"tenor_months": 2, "long_otm": 0.10, "short_otm": 0.25, "sizing": "notional", "vt": 0.45, "e2_only": False},
    "H5": {"tenor_months": 2, "long_otm": 0.10, "short_otm": 0.25, "sizing": "notional", "vt": 0.35, "e2_only": True},
}

# windows
VXN_START = "2001-01-23"          # first VXN session; the reduced main window starts here
VXN_MAIN_END = "2026-09-29"       # last QQQ session in the engine input
OOS_START, OOS_END = "1990-01-02", "1999-12-29"
WINDOWS = {
    "vxn_main": (VXN_START, VXN_MAIN_END),
    "vxn_2010": ("2010-01-04", VXN_MAIN_END),
    "vxn_2017": ("2017-01-03", VXN_MAIN_END),
}
