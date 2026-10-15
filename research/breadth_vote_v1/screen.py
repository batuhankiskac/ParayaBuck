#!/usr/bin/env python3
"""breadth_vote_v1 screen (see PREREGISTRATION.md). Offline except the two committed index
files fetched by fetch_panel_data.py; reads data/historical only.

Idea I1 of docs/research/idea_survey_v3.md: put **breadth** into production R2's trend vote.
Production reads three votes (close > SMA200, 252-session price return > 0, close > 126-session
max/min midpoint) on the *held* index (QQQ) and goes flat on a 2-of-3 majority break. This study
replaces/extents that with a panel of three economically different equity indices,
Panel(X) = {X, SPY, IWM} (IWM = ^RUT before 2000-05-26; SPY = ^GSPC before 1993-01-29), each
member carrying the production votes on its own closes. The early re-entry path is unchanged and
still reads the instrument's own closes (QQQ in the main window).

Modes
  --smoke    synthetic random-walk prices only, no market data: every arm builds a finite target
             series on a synthetic panel, the reference `simulate_band` mechanics, and the fact
             that C0's target series equals production R2's. The only mode run before the
             pre-registration commit.
  --harness  control reproduction on real data: C0 (= production R2's target series) pre-tax
             CAGR/max DD/turnover at m = 1/2 x base/calibrated vs fast_reentry_v1's R2 anchors, the
             pure-E2 anchor series vs e2_integration_v1, the after-tax anchors at $100k vs
             vt_level_tax_v1/r2_tbill_combo_v1, the B1/B2 bootstrap medians vs tax_harvest_v2, the
             C0 engine == `simulate_band` identity and the panel-splice sanity checks. No
             non-control arm is simulated on real data here.
  --run      harness, then C0 + B1..B4 on every stage: pre-tax main window (base/calibrated,
             m = 1/2/4), the after-tax windows at $100k (progressive, liquidated) plus B1/B2
             bootstraps and the pre-tax block-20 bootstrap, the four reduced OOS windows and their
             pooled bootstrap, the 21 instruments, then the gates and the selection rule. Writes
             RESULTS.json, SUMMARY.txt.

Engine helpers (synthetic 2x, SMA/TSM/DON, vol20, vt, metrics, bootstrap draws) come from
research/trend_robustness_v2/screen.py (its main() is never run). The pre-tax band simulator,
the OOS loaders and the instrument loaders are the copies used by research/fast_reentry_v1,
research/fast_reentry_v2 and research/gain_band_v2. The after-tax account, the Turkish tariff
with YI-UFE indexation and the two clean bootstraps are research/breadth_vote_v1/tax_engine.py,
a byte copy of research/gain_band_v2/tax_engine.py = research/vt_level_tax_v1's corrected engine.
"""

from __future__ import annotations

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
_te_spec = importlib.util.spec_from_file_location("bv_tax_engine", HERE / "tax_engine.py")
te = importlib.util.module_from_spec(_te_spec)
_te_spec.loader.exec_module(te)

FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
DAILY = ROOT / "data/historical/stress_robustness_v1_daily.csv"
RUT_CSV = ROOT / "data/historical/breadth_vote_v1_rut.csv"
GSPCPX_CSV = ROOT / "data/historical/breadth_vote_v1_gspc.csv"
SPY_CSV = ROOT / "data/historical/range_vol_v1_spy.csv"
IWM_CSV = ROOT / "data/historical/range_vol_v1_iwm.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = 200, 252, 126
SMA_FAST, LAG_FAST, MED_WINDOW = 50, 20, 252
C0_BAND = trv2.BAND  # 0.005 (production 0.5 pp resize band; no arm changes it)
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1  # 253 -> first measured session 2000-03-09
SEED, BLOCK = 20261011, 20
TOL = 1e-4  # 0.01 pp pre-tax harness tolerance
TAX_TOL = 0.005  # 0.005 pp after-tax harness tolerance
M = 2
M_STRESS = 4
CHUNK = 500
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))

# ---- arms (pre-registered; see PREREGISTRATION.md sections 3 and 4)
ARMS = ("C0", "B1", "B2", "B3", "B4")
CONTROL = "C0"
CANDIDATES = ("B1", "B2", "B3", "B4")

# ---- panel splices (pre-registered)
SPY_REAL_FIRST = "1993-01-29"   # SPY's first session in data/historical/range_vol_v1_spy.csv
IWM_REAL_FIRST = "2000-05-26"   # IWM's first session in data/historical/range_vol_v1_iwm.csv

# ---- after-tax stage
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITAL = 100_000.0
GATE_CAPITAL = "100k"
TAX_BOOT_PATHS = 1_000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_L = 253 + 10 * 252  # 2,773 sessions (253 warm-up + 2,520 measured)
TAX_BOOT_EXT = 3
TAX_BOOT_I0 = TAX_BOOT_EXT + 253
PRE_BOOT_PATHS = 5_000  # G5 (pre-tax, both financings)

# ---- gates (pre-registered)
BOOT_SHARE_MIN = 0.55
BOOT_MEDIAN_MIN_PP = 0.0
GATE_INSTR_MAX_DD_PP = 3.0
GATE_INSTR_MIN_SHARE = 0.80
GATE_OOS_MAX_DD_PP = 3.0
GATE_OOS_NEVER_DD_PP = 8.0
GATE_OOS_CAGR_PP = -0.50

# ---- reduced OOS windows (pre-registered; see PREREGISTRATION.md section 5)
# The panel needs peers with free long daily history. ^RUT starts 1987-09-10 and ^GSPC 1970-01-02
# in the fetched files, so every window's date array begins at 1987-09-10 and the first measured
# session is the 254th session of that array (START = 253). ewma_band_v1's ^GSPC 1957-1999 and
# M2 ^GSPC 1934-1956 windows are declared not applicable: their instrument *is* the SPY proxy, so
# the panel collapses to {^GSPC, ^RUT} and ^RUT does not start until 1987.
OOS_PANEL_FIRST = "1987-09-10"
OOS_MARKETS = {
    "U1": ("^IXIC", OOS_PANEL_FIRST, None, "1999-12-31"),
    "U2": ("^N225", OOS_PANEL_FIRST, None, "2026-09-29"),
    "U3": ("^GDAXI", OOS_PANEL_FIRST, None, "2026-09-29"),
    "U4": ("^FTSE", OOS_PANEL_FIRST, None, "2026-09-29"),
}
OOS_WINDOWS = tuple(OOS_MARKETS)
OOS_NOT_APPLICABLE = ("GSPC_1957_1999", "M2_GSPC_1934_1956")
EXTRA_FIN = 0.0075
DTB3_FIRST = "1954-01-04"
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
REF_E2 = {  # e2_integration_v1 / fast_reentry_v1 main_window (cagr, max_dd, turnover)
    ("base", 1): (0.1494912807457318, 0.4924710732832198, 5.968939533490137),
    ("base", 2): (0.14674909060373587, 0.4956428339322504, 5.968939533490137),
    ("calibrated", 1): (0.14377164501317918, 0.49333140001226616, 5.968913400107268),
    ("calibrated", 2): (0.14104311139315073, 0.49649783199459774, 5.968913400107268),
}
REF_R2 = {  # research/fast_reentry_v1/RESULTS.json main_window (the study's control)
    ("base", 1): (0.157538, 0.464143, 5.991522),
    ("base", 2): (0.154766, 0.467584, 5.991522),
    ("calibrated", 1): (0.151719, 0.465364, 5.991496),
    ("calibrated", 2): (0.148961, 0.468797, 5.991496),
}
REF_BOOT_CAGR_MEDIAN = {  # fast_reentry_v1 bootstrap_main_block20_m2, R2
    "base": 0.078856, "calibrated": 0.074346,
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


# ----------------------------------------------------------------------------- signals
def tsm_up_price(c, n=TSM_N):
    """up[t]: n-session price return > 0 (production 1.1.0: zero hurdle on the price close)."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    up[:, n:] = (c[:, n:] / c[:, :-n]) > 1.0
    return up


def don_up(c, n=DON_N):
    """up[t]: close[t] > midpoint of the max/min of the n closes ending at t (inclusive)."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1:] = c[:, n - 1:] > mid
    return up


def vote_parts(c):
    """The three production votes on one series: (majority, broken, sma, tsm, don), each [P, T].
    majority = at least 2 of 3 hold; broken = at most 1 holds (the exact complement)."""
    c = np.atleast_2d(np.asarray(c, dtype=float))
    s, t, d = trv2.sma_up(c, SMA_N), tsm_up_price(c), don_up(c)
    n = s.astype(np.int8) + t.astype(np.int8) + d.astype(np.int8)
    return n >= 2, n <= 1, s, t, d


def e2_up(price):
    """Production E2 vote: at least 2 of SMA200, TSM252 (> 0), DON126 on the price close."""
    return vote_parts(price)[0]


# ----------------------------------------------------------------------------- panel data
_SERIES_CACHE: dict = {}


def _load_close(path: Path, col: str = "close") -> dict:
    key = (str(path), col)
    if key not in _SERIES_CACHE:
        out = {}
        with path.open() as fh:
            for r in csv.DictReader(fh):
                out[r["date"]] = float(r[col])
        _SERIES_CACHE[key] = out
    return _SERIES_CACHE[key]


def _series_symbol(sym: str) -> dict:
    """One symbol's closes out of the multi-index file."""
    key = ("index", sym)
    if key not in _SERIES_CACHE:
        out = {}
        with INDEX.open() as fh:
            for r in csv.DictReader(fh):
                if r["symbol"] == sym:
                    out[r["date"]] = float(r["close"])
        _SERIES_CACHE[key] = out
    return _SERIES_CACHE[key]


def panel_sources():
    """(spy_real, gspc, iwm_real, rut) close dicts and the two splice factors."""
    key = "panel_sources"
    if key not in _SERIES_CACHE:
        spy = _load_close(SPY_CSV)
        gspc = _load_close(GSPCPX_CSV)
        iwm = _load_close(IWM_CSV)
        rut = _load_close(RUT_CSV)
        c_spy = spy[SPY_REAL_FIRST] / gspc[SPY_REAL_FIRST]
        c_iwm = iwm[IWM_REAL_FIRST] / rut[IWM_REAL_FIRST]
        _SERIES_CACHE[key] = (spy, gspc, iwm, rut, c_spy, c_iwm)
    return _SERIES_CACHE[key]


def member_closes(dates, real, proxy, c, first):
    """The pre-registered splice: `real` (the ETF, unadjusted split-adjusted close) from `first`
    onwards, `proxy` x c before it. Dates missing from the active source are forward-filled (the
    member's own calendar can differ from the instrument's); leading gaps stay NaN and read as
    "vote off" because every vote is NaN -> False."""
    out = np.full(len(dates), np.nan)
    for k, d in enumerate(dates):
        if d in real:
            v = real[d]
        elif d >= first:
            v = np.nan  # inside the real series' period but no bar: forward-fill
        elif d in proxy:
            v = proxy[d] * c
        else:
            v = np.nan
        out[k] = v
    last = np.nan
    for k in range(len(dates)):
        if np.isfinite(out[k]):
            last = out[k]
        else:
            out[k] = last
    return out


def panel_members(dates):
    """(SPY member closes, IWM member closes) aligned to `dates` by the pre-registered splice."""
    spy, gspc, iwm, rut, c_spy, c_iwm = panel_sources()
    return (member_closes(dates, spy, gspc, c_spy, SPY_REAL_FIRST),
            member_closes(dates, iwm, rut, c_iwm, IWM_REAL_FIRST))


# ----------------------------------------------------------------------------- the arm
def _rolling_median_vol(price_vol, window=MED_WINDOW, block=32):
    """`fast_reentry_v1._rolling_median_vol` via sliding windows (same values; asserted in the
    harness against the O(T) reference)."""
    P, T = price_vol.shape
    out = np.full((P, T), np.nan)
    if T < window:
        return out
    for s in range(0, P, block):
        e = min(P, s + block)
        blk = price_vol[s:e]
        fin = np.isfinite(blk)
        first = np.argmax(fin, axis=1)
        j0 = int(first.min())
        if T - j0 < window:
            continue
        out[s:e, j0 + window - 1:] = np.median(sliding_window_view(blk[:, j0:], window, axis=1), axis=2)
    return out


def _rolling_median_vol_ref(price_vol, window=MED_WINDOW):
    """fast_reentry_v1's O(T) reference implementation (harness check only)."""
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


def early_components(price):
    """The four path-independent early-entry conditions (SMA50, price vol20, its trailing
    252-session median, 20-session momentum); production R2's early path, unchanged."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma50_up = trv2.sma_up(price, SMA_FAST)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol(vol_p, MED_WINDOW)
    close_lag20 = np.full_like(price, np.nan)
    close_lag20[:, LAG_FAST:] = price[:, :-LAG_FAST]
    lag_cond = price > close_lag20
    return sma50_up, vol_p, med_p, lag_cond


def early_state(price, main_on, comps=None):
    """(on_eff, factor) of production R2's early leg, gated on the arm's main vote `main_on`
    instead of E2's. Identical to fast_reentry_v1.arm_signals' R2 branch with e2_on := main_on:
    the early leg is active only while the main vote is off, exits below SMA50, and is worth half
    weight. Never touches the panel -- the early path stays on the instrument's own closes."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    main_on = np.atleast_2d(np.asarray(main_on, dtype=bool))
    P, T = price.shape
    sma50_up, vol_p, med_p, lag_cond = early_components(price) if comps is None else comps
    early_active = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    for t in range(T):
        mt = main_on[:, t]
        early_active = np.where(mt, False, early_active)
        exit_cond = ~sma50_up[:, t]
        entry_cond = (sma50_up[:, t] & np.isfinite(med_p[:, t])
                      & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t])
        early_active = np.where(early_active & exit_cond & (~mt), False, early_active)
        can_enter = (~early_active) & (~mt) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & entry_cond, True, early_active)
        on_eff[:, t] = mt | early_active
        factor[:, t] = np.where(mt, 1.0, np.where(early_active, 0.5, 0.0))
    return on_eff, factor


def sized_wants(on_eff, factor, vol_instr):
    """Production sizing: min(1, 0.35/vol20) x the path factor, total cap 0.98."""
    vt = trv2.vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)))
    raw = np.atleast_2d(np.asarray(on_eff, dtype=float)) * np.atleast_2d(factor) * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def main_on(arm, inst_parts, spy_parts, iwm_parts):
    """The arm's main (non-early) on-state [P, T]. `inst_parts` etc. are `vote_parts` tuples of the
    instrument, the SPY member and the IWM member. C0 = production E2 on the instrument."""
    im, ib, is_, it, id_ = inst_parts
    sm, sb, ss, st, sd = spy_parts
    wm, wb, ws, wt, wd = iwm_parts
    if arm in ("C0",):
        return im
    if arm == "B1":   # the three votes read on SPY; QLD stays the instrument
        return sm
    if arm == "B2":   # breadth-confirmed exit: latched, exit only on the instrument AND breadth
        P, T = im.shape
        out = np.zeros((P, T), dtype=bool)
        cur = np.zeros(P, dtype=bool)
        for t in range(T):
            cur = np.where(cur, ~(ib[:, t] & (sb[:, t] | wb[:, t])), im[:, t])
            out[:, t] = cur
        return out
    if arm == "B3":   # breadth-gated state: instrument majority AND >= 2 of the 3 panel members
        return im & (sm | wm)
    if arm == "B4":   # 5-vote majority: QQQ SMA/TSM/DON + SPY TSM + IWM TSM, hold on >= 3 of 5
        n = (is_.astype(np.int8) + it.astype(np.int8) + id_.astype(np.int8)
             + st.astype(np.int8) + wt.astype(np.int8))
        return n >= 3
    raise ValueError(f"unknown arm label {arm!r}")


def arm_wants(arm, inst_c, spy_c, iwm_c, vol_instr, comps=None):
    """Target weights [P, T] for one arm. Path-independent given the three close series; `comps`
    caches `early_components(inst_c)` across arms of the same window."""
    ip = vote_parts(inst_c)
    sp = vote_parts(spy_c)
    wp = vote_parts(iwm_c)
    mo = main_on(arm, ip, sp, wp)
    on_eff, factor = early_state(inst_c, mo, comps)
    return sized_wants(on_eff, factor, vol_instr)


# ----------------------------------------------------------------------------- reference engine
def simulate_band(opens, closes, wants, cost, start, band):
    """research/fast_reentry_v1/screen.py's single-leg mechanics, verbatim (returns codes and
    traded |dw| so sells/yr can be reported)."""
    opens, closes, wants = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (opens, closes, wants))
    P, T = closes.shape
    nav = np.ones(P)
    w = np.zeros(P)
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    turn = np.zeros(P)
    ntr = np.zeros(P, dtype=np.int64)
    codes = np.zeros((P, T - start), dtype=np.int8)
    traded = np.zeros((P, T - start))
    for i in range(start, T):
        g_on = opens[:, i] / closes[:, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = w * g_on
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        want = wants[:, i - 1]
        d = np.abs(want - w)
        held, tgt = w > 0, want > 0
        flip = held != tgt
        trade = flip | (d > band)
        nav = np.where(trade, nav * (1 - cost * d), nav)
        turn = turn + np.where(trade, d, 0.0)
        ntr = ntr + trade.astype(np.int64)
        k = i - start
        codes[:, k] = np.where(~trade, 0, np.where(flip & tgt, 1, np.where(flip, 2, np.where(want > w, 3, 4))))
        traded[:, k] = np.where(trade, d, 0.0)
        w = np.where(trade, want, w)
        ga = closes[:, i] / opens[:, i]
        va = w * ga
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        out[:, k + 1] = nav
    return out, turn, ntr, codes, traded


# ----------------------------------------------------------------------------- metrics
def seg(dates, nav, a, b):
    """From the last close <= a (first bar if a is None or before data) to the last close <= b."""
    ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1), "max_dd": float(trv2.max_dd(s)[0])}


def event_stats(dates, codes, traded, a=None, b=None):
    """Trade counts per year (dates[k+1] is the session of codes[k])."""
    sess = dates[1:]
    lo = 0 if a is None else next(k for k, d in enumerate(sess) if d > a)
    hi = len(sess) if b is None else max(k for k, d in enumerate(sess) if d <= b) + 1
    c, tr = codes[lo:hi], traded[lo:hi]
    yrs = trv2.calendar_years(dates[lo], sess[hi - 1])
    n = {name: int(np.sum(c == v)) for name, v in (("entries", 1), ("exits", 2), ("resize_up", 3), ("resize_down", 4))}
    return {
        "years": yrs, "counts": n,
        "round_trips_per_year": n["exits"] / yrs,
        "trades_per_year": (n["entries"] + n["exits"] + n["resize_up"] + n["resize_down"]) / yrs,
        "resizes_per_year": (n["resize_up"] + n["resize_down"]) / yrs,
        "sells_per_year": (n["exits"] + n["resize_down"]) / yrs,
        "turnover_per_year": float(tr.sum() / yrs),
        "turnover_from_entries_exits_per_year": float(tr[(c == 1) | (c == 2)].sum() / yrs),
        "turnover_from_resizes_per_year": float(tr[(c == 3) | (c == 4)].sum() / yrs),
    }


def band_stats(days, nav, turn, ntr, codes, traded, wants, start, extras=False):
    """Pre-tax stats of one `simulate_band` run (fast_reentry_v1.arm_stats convention)."""
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    st = {"cagr": float((nav[-1] / nav[0]) ** (1 / years) - 1),
          "max_dd": float(trv2.max_dd(nav.reshape(1, -1))[0]),
          "turnover_annual": float(turn / years), "trades": int(ntr), "years": float(years),
          "2017_end": seg(dates, nav, "2016-12-31", None),
          "subperiods": {n: seg(dates, nav, a, b) for n, a, b in SUBPERIODS}}
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["sharpe"] = trv2.sharpe_daily(nav)
    if extras:
        st["events_full"] = event_stats(dates, codes, traded)
        st["events_2017_on"] = event_stats(dates, codes, traded, "2016-12-31")
        tgt = wants[start - 1:-1]
        st["time_in_market"] = float((tgt > 0).mean())
        st["sessions_target_below_10pp_in_trend"] = int(np.sum((tgt > 0) & (tgt < 0.10)))
    return st


# ----------------------------------------------------------------------------- inputs
def main_inputs(spread):
    """Main-window inputs: days, synthetic 2x QLD* opens/closes, the QQQ price close, the two
    panel members aligned to `days`, and the vol sizing series."""
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    spy_m, iwm_m = panel_members(days)
    return days, o, c, q[1], spy_m, iwm_m, trv2.vol_n(c)[0]


def main_wants(spread, arms):
    """{arm: wants} on the main window for one financing model."""
    days, o, c, price, spy_m, iwm_m, vol_instr = main_inputs(spread)
    comps = early_components(price)
    out = {arm: arm_wants(arm, price, spy_m, iwm_m, vol_instr, comps)[0] for arm in arms}
    # pure-E2 anchor series (harness fidelity check only; never an arm)
    out["E2"] = sized_wants(np.atleast_2d(e2_up(price)), np.ones((1, len(price))), vol_instr)[0]
    return days, o, c, price, spy_m, iwm_m, vol_instr, out


def run_band(arms, spread, days, o, c, wants, m=M, extras=False):
    res = {}
    for arm in arms:
        nav, turn, ntr, codes, traded = simulate_band(o, c, wants[arm], COST_PER_SIDE * m, START, C0_BAND)
        res[arm] = band_stats(days, nav[0], turn[0], ntr[0], codes[0], traded[0], wants[arm], START, extras)
    return res


# ----------------------------------------------------------------------------- OOS loaders
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
    """e2_confirmation_v1.load_market (pre-registered open rule), with this study's windows."""
    sym, first, meas_from, last = OOS_MARKETS[key]
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
    """One OOS window: own E2/R2 early path on the instrument's own closes, own 2x leg
    (calibrated, m = 2); every arm's main vote reads the panel."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    spy_m, iwm_m = panel_members(days)
    comps = early_components(c)
    vol_instr = trv2.vol_n(sc)
    W = {arm: arm_wants(arm, c, spy_m, iwm_m, vol_instr, comps)[0] for arm in arms}
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        nav, turn, ntr, codes, traded = simulate_band(so, sc, W[arm], COST_PER_SIDE * M, start, C0_BAND)
        cg = float(trv2.cagr(nav[0, 0], nav[0, -1], trv2.calendar_years(days[start - 1], days[-1])))
        dd = float(trv2.max_dd(nav)[0])
        ev = event_stats(days[start - 1:], codes[0], traded[0])
        res[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd),
                    "turnover_annual": float(turn[0] / ev["years"]),
                    "sells_per_year": ev["sells_per_year"], "resizes_per_year": ev["resizes_per_year"],
                    "round_trips_per_year": ev["round_trips_per_year"]}
    return res


# ----------------------------------------------------------------------------- instruments
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
    """One ETF: own E2/R2 early path on the ETF's adjusted close, own 2x leg; panel =
    {the ETF, SPY, IWM}, the pre-registered rule for a non-QQQ instrument."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    spy_m, iwm_m = panel_members(days)
    comps = early_components(c)
    vol_instr = trv2.vol_n(sc)
    W = {arm: arm_wants(arm, c, spy_m, iwm_m, vol_instr, comps)[0] for arm in arms}
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        nav, turn, ntr, codes, traded = simulate_band(so, sc, W[arm], COST_PER_SIDE * M, start, C0_BAND)
        st = band_stats(days, nav[0], turn[0], ntr[0], codes[0], traded[0], W[arm], start, extras=False)
        st["sells_per_year"] = event_stats(days[start - 1:], codes[0], traded[0])["sells_per_year"]
        res[arm] = st
    return res


# ----------------------------------------------------------------------------- bootstrap G5 (pre-tax)
def boot_main(arms):
    """Stationary block bootstrap (mean block 20) of whole sessions on the main window, draws
    identical to signal_ensemble_v1 / fast_reentry_v1 (seed, chunk order, 3-session back-extension),
    both financing models, m = 2, all arms on the same paths. 5,000 paths."""
    _, arr, y = trv2.load()
    q = arr["QQQ"]
    T = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = len(pool)
    L = trv2.WARM + trv2.MEASURED
    start = trv2.SMA_MAX + 1 + (TSM_N - trv2.SMA_MAX + 1)
    cg = {(md, a): [] for md in FIN for a in arms}
    tv = {(md, a): [] for md in FIN for a in arms}
    for chunk in range(0, PRE_BOOT_PATHS, CHUNK):
        P = min(CHUNK, PRE_BOOT_PATHS - chunk)
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        ext = TSM_N - trv2.SMA_MAX + 1
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(ext, 0, -1)], axis=1)
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
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        # panel members: the same draws applied to their close-to-close returns
        sm_days, iwm_days = _panel_on_main_days()
        pm_spy = np.concatenate([one, np.cumprod(sm_days[idx] / sm_days[idx - 1], axis=1)], axis=1)
        pm_iwm = np.concatenate([one, np.cumprod(iwm_days[idx] / iwm_days[idx - 1], axis=1)], axis=1)
        comps = early_components(pc)
        for md in FIN:
            so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            vol_instr = trv2.vol_n(sc)
            for arm in arms:
                w = arm_wants(arm, pc, pm_spy, pm_iwm, vol_instr, comps)
                nav, turn, _, _, _ = simulate_band(so, sc, w, COST_PER_SIDE * M, start, C0_BAND)
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(turn / (k / 252))
    return ({key: np.concatenate(v) for key, v in cg.items()},
            {key: np.concatenate(v) for key, v in tv.items()})


_MAIN_PANEL: dict = {}


def _panel_on_main_days():
    if "v" not in _MAIN_PANEL:
        days, _, _ = trv2.load()
        _MAIN_PANEL["v"] = panel_members(days)
    return _MAIN_PANEL["v"]


def boot_summary(cg, tv, arms, models):
    out = {}
    for md in models:
        out[md] = {}
        c0 = cg[(md, CONTROL)]
        for a in arms:
            c = cg[(md, a)]
            d = c - c0
            out[md][a] = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                          "turnover_median": float(np.median(tv[(md, a)])),
                          "diff_vs_C0_median_pp": float(np.median(d) * 100),
                          "diff_vs_C0_mean_pp": float(np.mean(d) * 100),
                          "p25_pp": float(np.percentile(d, 25) * 100), "p75_pp": float(np.percentile(d, 75) * 100),
                          "share_ge_C0": float(np.mean(d >= 0)),
                          "share_gt_0": float(np.mean(d > 0))}
    return out


# ----------------------------------------------------------------------------- bootstrap G8 (OOS)
def boot_oos_market(key, rng, arms):
    """e2_confirmation_v1's K5 draws (shared generator, markets in order), m = 2, calibrated; the
    panel is resampled with the same session indices."""
    days, o, c, y, start = load_market(key)
    spy_m, iwm_m = panel_members(days)
    pool = np.arange(start, len(days))
    npool = len(pool)
    acc = {a: [] for a in arms}
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
        one = np.ones((P, 1))
        cc = np.concatenate([one, np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([one, cc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        vol_instr = trv2.vol_n(sc)
        pm_spy = np.concatenate([one, np.cumprod(spy_m[idx] / spy_m[idx - 1], axis=1)], axis=1)
        pm_iwm = np.concatenate([one, np.cumprod(iwm_m[idx] / iwm_m[idx - 1], axis=1)], axis=1)
        comps = early_components(cc)
        for arm in arms:
            w = arm_wants(arm, cc, pm_spy, pm_iwm, vol_instr, comps)
            nav, _, _, _, _ = simulate_band(so, sc, w, COST_PER_SIDE * M, START, C0_BAND)
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


# ----------------------------------------------------------------------------- after-tax stage
def usd_market(days):
    """R-USD instantiation (used only for the engine-equivalence harness check)."""
    days = list(days)
    years = sorted({int(d[:4]) for d in days})
    ufe = {f"{y}-{mo:02d}": 1.0 for y in range(min(years[0], 2025) - 1, max(years[-1], 2026) + 2)
           for mo in range(1, 13)}
    return te.Market(days, np.ones(len(days)), ufe)


def engine_run(capital, i0, i1, days, mkt, oq, cq, wq, mode, m=M):
    """One arm through the Turkish after-tax account with production R2's execution layer and
    the production 0.5 pp band (independently asserted == `simulate_band` in the harness)."""
    days = list(days)
    if not isinstance(i0, int):
        i0 = next(k for k, d in enumerate(days) if d >= i0)
        i1 = max(k for k, d in enumerate(days) if d <= i1)
    oq, cq, wq = (np.asarray(x, dtype=float).flatten() for x in (oq, cq, wq))
    cost = COST_PER_SIDE * m
    cashbox = {"v": float(capital)}
    if mode == "flat":
        qa = te.FlatAccount(mkt, 0.0, cashbox)
        pays = None
    else:
        rg = te.Regime("progressive", "tariff") if mode == "progressive" else te.Regime("pre_tax", "none")
        qa = te.BaseAccount(mkt, rg, 0.0, cashbox)
        pays = te.instalments(days, i0, i1) if mode == "progressive" else None
    cashbox["v"] = float(capital)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = q_sells = tax_sales = 0
    turnover = 0.0
    prev_tgt = None
    for i in range(i0, i1 + 1):
        if i > i0 and int(days[i][:4]) != int(days[i - 1][:4]):
            prevy = int(days[i][:4]) - 1
            qa.tax_due[prevy] = qa.assess(prevy)
            if mode == "flat":
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
        nav_open = cashbox["v"] + qa.units * po
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = (qa.units * po) / nav_open if nav_open > 0 else 0.0
        tgt = float(wq[i - 1]) if i > 0 else float(wq[0])
        tgt = 0.0 if tgt != tgt else tgt
        trade = abs(tgt - w) > C0_BAND
        if qa.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if qa.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True
        if trade:
            drift = abs(tgt - w)
            fee = nav_open * cost * drift
            target_units = tgt * (nav_open - fee) / po if po > 0 else 0.0
            du = target_units - qa.units
            if du > 1e-12:
                qa.buy(i, po, du * po, fee)
            elif du < -1e-12:
                qa.sell(i, po, -du, fee, "resize")
                q_sells += 1
            trades += 1
            turnover += drift
        nav[i - i0 + 1] = cashbox["v"] + qa.units * pc
        prev_tgt = tgt
    end_y = int(days[i1][:4])
    if qa.units > 0:
        qa.sell(i1, float(cq[i1]), qa.units, qa.units * float(cq[i1]) * cost, "liquidation")
    liq_tax = qa.assess(end_y) + qa.outstanding()
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
        "tax_total_usd_at_payment_fx": float(qa.tax_paid_usd + (liq_tax / fx1 if fx1 != 0 else liq_tax)),
        "strategy_trades": int(trades), "qlD_sells": int(q_sells), "tax_funding_sales": int(tax_sales),
        "sells": int(q_sells + tax_sales),
        "turnover_total": float(turnover), "turnover_annual": float(turnover / yrs),
        "fees_usd": float(qa.fees), "index_relief_try": float(qa.index_relief_try),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.gain_by_reason.items())},
        "_nav": nav,
    }


# ----------------------------------------------------------------------------- after-tax bootstrap
def boot_e1(n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS):
    """Block-20 bootstrap on the fixed 2015-09 -> 2026-09 calendar with its realised USD/TRY and
    YI-UFE; market returns (QQQ overnight/intraday/dividend + DTB3) and the two panel members'
    returns resampled exactly as tax_asym_v2 / vt_level_tax_v1 / gain_band_v2. Pre-tax at `n_pre`
    paths under both financing models (gate G5); after-tax progressive (B1) and flat 20 % (B2) at
    the first `n_tax` paths, $100k."""
    all_days, arr, y = trv2.load()
    sub = [d for d in all_days if TAX_BOOT_LO <= d <= TAX_BOOT_HI]
    need = TAX_BOOT_L + TAX_BOOT_EXT
    if len(sub) < need:
        raise RuntimeError(f"fixed calendar {TAX_BOOT_LO}..{TAX_BOOT_HI} has only {len(sub)} days, need {need}")
    fixed_dates = list(sub[:need])
    fd, fv = [], []
    with FX_CSV.open() as fh:
        for r in csv.DictReader(fh):
            fd.append(r["date"]); fv.append(float(r["usdtry_forex_buying"]))
    import bisect as _bisect
    fixed_fx = np.array([fv[_bisect.bisect_right(fd, d) - 1] for d in fixed_dates], dtype=float)
    ufe = te.load_market(FX_CSV, UFE_CSV, all_days).ufe
    mkt = te.Market(fixed_dates, fixed_fx, ufe)
    q = arr["QQQ"]
    Tsrc = q.shape[1]
    spy_m, iwm_m = _panel_on_main_days()
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, Tsrc)
    npool = len(pool)
    L, EXT_ = TAX_BOOT_L, TAX_BOOT_EXT
    i0, i1 = TAX_BOOT_I0, len(fixed_dates) - 1
    pre = {(md, a): [] for md in FIN for a in ARMS}
    at = {mode: {a: [] for a in ARMS} for mode in ("progressive", "flat")}
    anchor_e2 = {"progressive": [], "flat": []}
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
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        pm_spy = np.concatenate([one, np.cumprod(spy_m[idx] / spy_m[idx - 1], axis=1)], axis=1)
        pm_iwm = np.concatenate([one, np.cumprod(iwm_m[idx] / iwm_m[idx - 1], axis=1)], axis=1)
        comps = early_components(pc)
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN["base"], EXP_Q)
        so_c, sc_c = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN["calibrated"], EXP_Q)
        vol_b, vol_c = trv2.vol_n(sc)[0], trv2.vol_n(sc_c)[0]
        wants = {a: arm_wants(a, pc, pm_spy, pm_iwm, vol_b, comps)[0] for a in ARMS}
        wants_c = {a: arm_wants(a, pc, pm_spy, pm_iwm, vol_c, comps)[0] for a in ARMS}
        wants_e2 = sized_wants(np.atleast_2d(e2_up(pc)), np.ones((1, pc.shape[1])), vol_b)[0]
        legs = {"base": (so[0], sc[0], wants), "calibrated": (so_c[0], sc_c[0], wants_c)}
        if pidx < n_pre:
            for md, (o_, c_, W) in legs.items():
                for arm in ARMS:
                    r = engine_run(1.0, i0, i1, fixed_dates, mkt, o_, c_, W[arm], "pre_tax", m=M)
                    k = len(r["_nav"]) - 1
                    pre[(md, arm)].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
        if pidx < n_tax:
            o_, c_, W = legs["base"]
            for mode in ("progressive", "flat"):
                for arm in ARMS:
                    r = engine_run(TAX_CAPITAL, i0, i1, fixed_dates, mkt, o_, c_, W[arm], mode, m=M)
                    if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                        raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode}")
                    at[mode][arm].append(r["cagr_usd_liquidated"])
                r = engine_run(TAX_CAPITAL, i0, i1, fixed_dates, mkt, o_, c_, wants_e2, mode, m=M)
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
                "p05_pp": float(np.percentile(d, 5) * 100), "p95_pp": float(np.percentile(d, 95) * 100)}
    for mode in ("progressive", "flat"):
        e = {"n_paths": n_tax, "capital_usd": TAX_CAPITAL}
        for arm in ARMS:
            e[arm] = {"cagr_median": float(np.median(at[mode][arm]))}
        base = np.array(at[mode][CONTROL])
        for arm in CANDIDATES:
            d = np.array(at[mode][arm]) - base
            e[f"{arm}_vs_{CONTROL}"] = {
                "median_delta_cagr_pp": float(np.median(d) * 100),
                "mean_delta_cagr_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100)}
        out["tax"][mode] = e
    out["anchor_e2_100k"] = {mode: {"cagr_median": float(np.median(v))} for mode, v in anchor_e2.items()}
    return out


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
    pre, win, oos, inst, boot, oos_boot = (res["pre"], res["windows"], res["oos"], res["instruments"],
                                           res["bootstrap"], res["oos_bootstrap"])
    g = {}
    for arm in CANDIDATES:
        r = {}
        for md in FIN:
            a2, c2 = pre[md]["m2"][arm], pre[md]["m2"][CONTROL]
            a1, c1 = pre[md]["m1"][arm], pre[md]["m1"][CONTROL]
            r[f"G1_{md}"] = g_return(a2, c2)
            r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
            r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
            r[f"G5_{md}"] = bool(boot["pre"][md][arm]["diff_vs_C0_median_pp"] >= BOOT_MEDIAN_MIN_PP)
            r[f"G6_{md}"] = g_return(a1, c1)
        a2, c2 = pre["base"]["m2"][arm], pre["base"]["m2"][CONTROL]
        wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
        r["G4"] = bool(len(wins) >= 2)
        r["G4_wins"] = wins
        r["G9"] = bool(a2["turnover_annual"] <= 2.0 * c2["turnover_annual"])
        dc = [oos[wn][arm]["cagr"] - oos[wn][CONTROL]["cagr"] for wn in OOS_WINDOWS]
        ddd = [oos[wn][arm]["max_dd"] - oos[wn][CONTROL]["max_dd"] for wn in OOS_WINDOWS]
        n_c = sum(1 for v in dc if v >= GATE_OOS_CAGR_PP / 100)
        n_d = sum(1 for v in ddd if v <= GATE_OOS_MAX_DD_PP / 100 + 1e-12)
        r["G7"] = bool(float(np.median(dc)) >= 0 and n_c >= 3 and n_d >= 3
                       and max(ddd) <= GATE_OOS_NEVER_DD_PP / 100 + 1e-12)
        r["G7_detail"] = {"cagr_diff_pp": {k: (oos[k][arm]["cagr"] - oos[k][CONTROL]["cagr"]) * 100
                                           for k in OOS_WINDOWS},
                          "dd_diff_pp": {k: (oos[k][arm]["max_dd"] - oos[k][CONTROL]["max_dd"]) * 100
                                         for k in OOS_WINDOWS},
                          "median_cagr_diff_pp": float(np.median(dc) * 100),
                          "windows_cagr_ge_minus_0_5pp": int(n_c), "windows_dd_within_3pp": int(n_d),
                          "windows": list(OOS_WINDOWS),
                          "not_applicable": list(OOS_NOT_APPLICABLE)}
        r["G8"] = bool(oos_boot["pooled"][arm]["diff_vs_C0_median_pp"] >= 0)
        r["G8_pooled_median_pp"] = oos_boot["pooled"][arm]["diff_vs_C0_median_pp"]
        at_cells = {wn: {"arm": win[wn][arm]["after_tax_cagr"], "R2": win[wn][CONTROL]["after_tax_cagr"],
                         "pre_tax_arm": win[wn][arm]["pre_tax_cagr"],
                         "pre_tax_R2": win[wn][CONTROL]["pre_tax_cagr"],
                         "pass": bool(win[wn][arm]["after_tax_cagr"] > win[wn][CONTROL]["after_tax_cagr"])}
                    for wn in TAX_WINDOWS}
        r["AT"] = bool(all(v["pass"] for v in at_cells.values()))
        r["AT_cells"] = at_cells
        boots = {}
        for label, mode in (("B1_recent", "progressive"), ("B2_flat", "flat")):
            v = boot["tax"][mode][f"{arm}_vs_{CONTROL}"]
            boots[label] = {"median_delta_pp": v["median_delta_cagr_pp"], "share_gt_0": v["share_gt_0"],
                            "p05_pp": v["p05_delta_cagr_pp"], "p95_pp": v["p95_delta_cagr_pp"],
                            "pass": bool(v["median_delta_cagr_pp"] >= BOOT_MEDIAN_MIN_PP
                                         and v["share_gt_0"] >= BOOT_SHARE_MIN)}
        r["B1"] = boots["B1_recent"]["pass"]
        r["B2"] = boots["B2_flat"]["pass"]
        r["boot"] = boots
        keys = sorted(inst)
        dds = [inst[k][arm]["max_dd"] - inst[k][CONTROL]["max_dd"] for k in keys]
        dcs = [inst[k][arm]["cagr"] - inst[k][CONTROL]["cagr"] for k in keys]
        share = float(np.mean([v <= GATE_INSTR_MAX_DD_PP / 100 + 1e-12 for v in dds]))
        r["F3"] = bool(share >= GATE_INSTR_MIN_SHARE)
        r["F3_detail"] = {"n_instruments": len(keys), "share_dd_within_3pp": share,
                          "n_dd_within": int(sum(v <= GATE_INSTR_MAX_DD_PP / 100 + 1e-12 for v in dds)),
                          "worst_delta_max_dd_pp": float(max(dds) * 100),
                          "worst_instrument": keys[int(np.argmax(dds))],
                          "median_delta_cagr_pp": float(np.median(dcs) * 100),
                          "share_cagr_gt_0": float(np.mean([v > 0 for v in dcs]))}
        r["pass_all"] = bool(all(r[k] for k in
                                 [f"{x}_{md}" for x in ("G1", "G2", "G3", "G5", "G6")
                                  for md in FIN] + ["G4", "G7", "G8", "G9", "AT", "B1", "B2", "F3"]))
        g[arm] = r
    eligible = [a for a in CANDIDATES if g[a]["pass_all"]]
    key = lambda a: (win["2000_03_to_2026_09"][a]["after_tax_cagr"], pre["base"]["m2"][a]["cagr"])
    sel = max(eligible, key=key) if eligible else "none"
    selection = {"eligible": eligible, "selected": sel,
                 "rule": "arms passing G1-G9 + AT (after-tax $100k, all three windows) + B1 + B2 + F3; "
                         "selected = highest after-tax liquidated CAGR at $100k on the 2000 window"}
    return {"arms": g, "selection": selection}


# ----------------------------------------------------------------------------- harness
def harness(run_boot=True):
    """Control reproduction on real data. No candidate arm is simulated here."""
    checks, bad = {}, 0
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    # ---- panel splice sanity
    spy, gspc, iwm, rut, c_spy, c_iwm = panel_sources()
    spy_m, iwm_m = panel_members(days)
    pos = {d: k for k, d in enumerate(days)}
    daily_spy = {}
    with DAILY.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == "SPY":
                daily_spy[r["date"]] = float(r["close"])
    dmax = max(abs(spy_m[pos[d]] - daily_spy[d]) for d in daily_spy if d in pos)
    checks["spy_member_equals_daily_file_spy_on_main_window"] = {"max_abs_diff": float(dmax),
                                                                 "n": sum(1 for d in daily_spy if d in pos)}
    if dmax > 0:
        bad += 1
    first_iwm_day = next(k for k, d in enumerate(days) if d >= IWM_REAL_FIRST)
    dcut = max(abs(iwm_m[k] - _load_close(IWM_CSV)[days[k]]) for k in range(first_iwm_day, len(days)))
    drut = max(abs(iwm_m[k] - rut[days[k]] * c_iwm) for k in range(first_iwm_day) if days[k] in rut)
    checks["iwm_member_is_IWM_after_2000_05_26"] = {"max_abs_diff": float(dcut)}
    checks["iwm_member_is_scaled_RUT_before_2000_05_26"] = {"max_abs_diff": float(drut)}
    if dcut > 0 or drut > 0:
        bad += 1
    # ---- median helper vs reference
    fake_v = np.concatenate([np.full((2, 400), np.nan), np.linspace(0.01, 0.5, 400)[None, :].repeat(2, 0)], axis=1)
    dec = float(np.max(np.abs(np.nan_to_num(_rolling_median_vol(fake_v, 252) - _rolling_median_vol_ref(fake_v, 252)))))
    checks["rolling_median_matches_reference"] = {"max_abs_diff": dec}
    if dec > 1e-15:
        bad += 1
    # ---- pre-tax main window: control + pure-E2 anchor
    anchors = {"R2": {}, "E2": {}}
    for md, spread in FIN.items():
        d_, o_, c_, price, spy_d, iwm_d, vol_i, W = main_wants(spread, ARMS)
        for label, ref in (("E2", REF_E2), ("R2", REF_R2)):
            w = W["E2"] if label == "E2" else W[CONTROL]
            for m in (1, 2):
                nav, turn, ntr, codes, traded = simulate_band(o_, c_, w, COST_PER_SIDE * m, START, C0_BAND)
                st = band_stats(d_, nav[0], turn[0], ntr[0], codes[0], traded[0], w, START)
                got = (st["cagr"], st["max_dd"], st["turnover_annual"])
                for k, val, got_v in (("cagr", ref[(md, m)][0], got[0]),
                                      ("max_dd", ref[(md, m)][1], got[1]),
                                      ("turnover", ref[(md, m)][2], got[2])):
                    checks[f"{label}_{md}_m{m}_{k}"] = {"reference": val, "reproduced": got_v,
                                                        "diff_pp": (got_v - val) * 100}
                    if abs(got_v - val) > TOL:
                        bad += 1
            anchors[label][f"{md}_m2"] = {"cagr": got[0], "max_dd": got[1], "turnover": got[2]}
    # ---- engine == simulate_band for C0 (production R2 target series)
    d0, o0, c0, price0, spy0, iwm0, vol0, W0 = main_wants(0.0, ARMS)
    ref_nav, _, _, _, _ = simulate_band(o0, c0, W0[CONTROL], COST_PER_SIDE * M, START, C0_BAND)
    mine = engine_run(1.0, START, len(d0) - 1, d0, mkt, o0, c0, W0[CONTROL], "pre_tax", m=M)
    dnav = float(np.max(np.abs(mine["_nav"] - ref_nav[0])))
    checks["c0_engine_equals_simulate_band"] = {"max_abs_nav_diff": dnav}
    if dnav > 1e-9:
        bad += 1
    # No non-control arm is simulated on real data in this mode (pre-registration requirement);
    # the arm code paths are exercised in `smoke()` on synthetic prices instead.
    # ---- after-tax anchors at $100k
    tax = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        tax[wn] = {}
        for label, series in (("E2", "E2"), ("C0", "R2")):
            w = W0[label] if label in W0 else W0[series]
            rp = engine_run(TAX_CAPITAL, a, b, days, mkt, o0, c0, w, "pre_tax")
            rq = engine_run(TAX_CAPITAL, a, b, days, mkt, o0, c0, w, "progressive")
            ref = TAX_ANCHORS[series][wn]
            tax[wn][label] = {"pre_tax": {"reference": ref[0], "reproduced": rp["cagr_usd_liquidated"],
                                          "diff_pp": (rp["cagr_usd_liquidated"] - ref[0]) * 100},
                              "progressive": {"reference": ref[1], "reproduced": rq["cagr_usd_liquidated"],
                                              "diff_pp": (rq["cagr_usd_liquidated"] - ref[1]) * 100},
                              "progressive_max_dd": {"reference": ref[2], "reproduced": rq["max_dd_nav_usd"],
                                                     "diff_pp": (rq["max_dd_nav_usd"] - ref[2]) * 100}}
            for v in tax[wn][label].values():
                if abs(v["diff_pp"]) > TAX_TOL:
                    bad += 1
    boot = {}
    if run_boot:
        b = boot_e1(n_pre=TAX_BOOT_PATHS, n_tax=TAX_BOOT_PATHS)
        for mode, label in (("progressive", "R2"), ("flat", "R2")):
            got = b["tax"][mode]["C0"]["cagr_median"]
            boot[f"{mode}_R2"] = {"reference": BOOT_ANCHOR[mode][label], "reproduced": got,
                                  "diff_pp": (got - BOOT_ANCHOR[mode][label]) * 100}
            if abs(got - BOOT_ANCHOR[mode][label]) > 0.0010:
                bad += 1
        boot["e2_anchor"] = {mode: {"reference": BOOT_ANCHOR[mode]["E2"],
                                    "reproduced": b["anchor_e2_100k"][mode]["cagr_median"]}
                             for mode in ("progressive", "flat")}
        for md in FIN:
            got = b["pre"][md]["C0"]["cagr_median"]
            boot[f"pre_{md}"] = {"reference": REF_BOOT_CAGR_MEDIAN[md], "reproduced": got,
                                 "diff_pp": (got - REF_BOOT_CAGR_MEDIAN[md]) * 100}
            if abs(got - REF_BOOT_CAGR_MEDIAN[md]) > 0.0050:
                bad += 1
    return {"reproduction_checks": checks, "n_outside_tolerance": bad, "pre_tax_anchors": anchors,
            "after_tax_anchors": tax, "bootstrap_control": boot, "pass": bool(bad == 0)}


# ----------------------------------------------------------------------------- the run
def run() -> dict:
    days, arr, y = trv2.load()
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    T = len(days) - 1
    # ---- pre-tax main window
    print("pre-tax main window", flush=True)
    pre = {}
    for md, spread in FIN.items():
        d_, o_, c_, price, spy_d, iwm_d, vol_i, W = main_wants(spread, ARMS)
        pre[md] = {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = run_band(ARMS, None, d_, o_, c_, W, m=m, extras=(m == 2))
    _, o0, c0, price0, spy0, iwm0, vol0, W0 = main_wants(0.0, ARMS)
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": T - START + 1,
                     "years": float(trv2.calendar_years(days[START - 1], days[-1]))}
    # ---- after-tax windows at $100k (progressive, liquidated)
    print("after-tax windows", flush=True)
    win = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        win[wn] = {}
        for arm in ARMS:
            rq = engine_run(TAX_CAPITAL, a, b, days, mkt, o0, c0, W0[arm], "progressive")
            rp = engine_run(1.0, a, b, days, mkt, o0, c0, W0[arm], "pre_tax")
            win[wn][arm] = {
                "pre_tax_cagr": rp["cagr_usd_liquidated"],
                "after_tax_cagr": rq["cagr_usd_liquidated"],
                "after_tax_cagr_unliquidated": rq["cagr_usd_unliquidated"],
                "max_dd_after_tax": rq["max_dd_nav_usd"],
                "terminal_usd": rq["terminal_usd_liquidated"],
                "tax_usd": rq["tax_total_usd_at_payment_fx"],
                "sells_per_year": rq["sells"] / rq["years"],
                "turnover_annual_after_tax": rq["turnover_annual"],
                "trades_per_year": rq["strategy_trades"] / rq["years"],
                "index_relief_try": rq["index_relief_try"],
                "realised_gain_try_by_reason": rq["realised_gain_try_by_reason"],
            }
    # ---- OOS windows (reduced set)
    print("OOS windows", flush=True)
    oos = {}
    for key in OOS_WINDOWS:
        md_, mo_, mc_, my_, mstart_ = load_market(key)
        oos[key] = oos_window(md_, mo_, mc_, my_, mstart_, ARMS)
    # ---- instruments
    print("instruments", flush=True)
    inst, quality = {}, {}
    for key in sorted(INSTRUMENT_FILES):
        idays, io, ih, il, ic, iy = load_instrument(key)
        frac, ok = quality_ok(ih, il, io, ic)
        quality[key] = {"bad_frac": float(frac), "pass": bool(ok), "sessions": len(idays)}
        if key in EXCLUDED_INSTRUMENTS:
            continue
        inst[key] = instrument_window(idays, io, ic, iy, ARMS)
    # ---- bootstraps
    print("E1 bootstrap (pre-tax + B1/B2)", flush=True)
    boot = boot_e1()
    print("G8 OOS bootstrap", flush=True)
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, ARMS) for k in OOS_WINDOWS}
    pooled = {a: np.concatenate([per[k][a] for k in OOS_WINDOWS]) for a in ARMS}
    oos_boot = {"per_market": {}, "pooled": {}}
    for k in OOS_WINDOWS:
        oos_boot["per_market"][k] = {a: {"median_diff_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)}
                                     for a in ARMS}
    for a in ARMS:
        d = pooled[a] - pooled[CONTROL]
        oos_boot["pooled"][a] = {"diff_vs_C0_median_pp": float(np.median(d) * 100),
                                 "diff_vs_C0_mean_pp": float(np.mean(d) * 100),
                                 "share_gt_0": float(np.mean(d > 0)), "paths": int(len(d))}
    print("bootstraps done", flush=True)
    return {"pre": pre, "windows": win, "oos": oos, "instruments": inst, "quality": quality,
            "bootstrap": boot, "oos_bootstrap": oos_boot}


# ----------------------------------------------------------------------------- smoke (synthetic)
def _trading_days(n, y0=2005):
    from datetime import date, timedelta
    out, d = [], date(y0, 1, 3)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def r2_reference_wants(price, e2_on, vol_instr):
    """research/fast_reentry_v1/screen.py `arm_signals`' R2 branch, verbatim (with its own O(T)
    median reference), as an independent cross-check that C0's target series is production R2's.
    Smoke mode only."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    vol_instr = np.atleast_2d(np.asarray(vol_instr, dtype=float))
    P, T = price.shape
    sma50_up = trv2.sma_up(price, SMA_FAST)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol_ref(vol_p, MED_WINDOW)
    close_lag20 = np.full_like(price, np.nan)
    close_lag20[:, LAG_FAST:] = price[:, :-LAG_FAST]
    lag_cond = price > close_lag20
    early_active = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    for t in range(T):
        e2t = e2_on[:, t].copy()
        early_active = np.where(e2t, False, early_active)
        early_active = np.where(early_active & (~sma50_up[:, t]) & (~e2t), False, early_active)
        entry_cond = (sma50_up[:, t] & np.isfinite(med_p[:, t])
                      & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t])
        can_enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & entry_cond, True, early_active)
        active = e2t | early_active
        f = np.where(e2t, 1.0, np.where(early_active, 0.5, 0.0))
        f = np.where(active, f, 0.0)
        on_eff[:, t] = active
        factor[:, t] = f
    vt = trv2.vt_of(vol_instr)
    raw = on_eff.astype(float) * factor * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def smoke():
    """Whole arm path on synthetic random-walk prices; no market data touched."""
    n = 1600
    days = _trading_days(n)

    def walk(seed, base=100.0):
        r = np.random.default_rng(seed).normal(0.0004, 0.012, n)
        return base * np.cumprod(1 + r)

    inst = walk(11, 100.0)
    spy = walk(12, 60.0)
    iwm = walk(13, 40.0)
    o = np.empty(n)
    o[0] = inst[0]
    o[1:] = inst[:-1] * (1 + np.random.default_rng(14).normal(0, 0.003, n - 1))
    c = inst
    so, sc = trv2.synthetic_2x(o, c, np.zeros(n), np.full(n, 0.01), EXP_Q)
    vol_i = trv2.vol_n(sc)
    comps = early_components(c)
    asserts = {}
    for arm in ARMS:
        w = np.asarray(arm_wants(arm, c, spy, iwm, vol_i, comps)[0])
        asserts[f"{arm}_target_finite"] = bool(np.all(np.isfinite(w)) and w.min() >= 0 and w.max() <= CAP + 1e-9)
        nav, turn, ntr, codes, traded = simulate_band(so, sc, w, COST_PER_SIDE * M, START, C0_BAND)
        asserts[f"{arm}_nav_finite"] = bool(np.all(np.isfinite(nav)) and nav[0, -1] > 0)
    # C0's target series must equal production R2's, computed by the reference implementation
    ref = r2_reference_wants(c, e2_up(c), vol_i)
    mine = np.asarray(arm_wants("C0", c, spy, iwm, vol_i, comps)[0])
    asserts["C0_target_equals_production_R2_reference"] = bool(float(np.max(np.abs(ref - mine))) < 1e-12)
    ip, sp, wp = vote_parts(c), vote_parts(spy), vote_parts(iwm)
    inst_maj, spy_maj = ip[0][0], sp[0][0]
    asserts["C0_main_vote_is_instrument_majority"] = bool(np.array_equal(main_on("C0", ip, sp, wp)[0], inst_maj))
    asserts["B1_main_vote_is_SPY_majority"] = bool(np.array_equal(main_on("B1", ip, sp, wp)[0], spy_maj))
    b2 = main_on("B2", ip, sp, wp)[0]
    b3 = main_on("B3", ip, sp, wp)[0]
    asserts["B2_latch_dominates_C0_main_vote"] = bool(np.all(~inst_maj | b2) and b2.sum() >= inst_maj.sum())
    asserts["B3_main_vote_is_subset_of_C0_main_vote"] = bool(np.all(~b3 | inst_maj))
    asserts["B2_and_B3_are_not_the_same_arm"] = bool(not np.array_equal(b2, b3))
    w_c0 = mine
    for arm in CANDIDATES:
        w_a = np.asarray(arm_wants(arm, c, spy, iwm, vol_i, comps)[0])
        asserts[f"{arm}_target_differs_from_C0"] = bool(float(np.max(np.abs(w_a - w_c0))) > 1e-6)
    n5 = (ip[2].astype(np.int8) + ip[3].astype(np.int8) + ip[4].astype(np.int8)
          + sp[3].astype(np.int8) + wp[3].astype(np.int8))
    asserts["B4_is_3_of_5"] = bool(np.array_equal(main_on("B4", ip, sp, wp)[0], (n5 >= 3)[0]))
    # after-tax engine == simulate_band on a synthetic account (USD market, no tax)
    mkt = usd_market(days)
    ref_nav, _, _, _, _ = simulate_band(so, sc, np.asarray(mine), COST_PER_SIDE * M, START, C0_BAND)
    e_nav = engine_run(1.0, START, n - 1, days, mkt, so, sc, mine, "pre_tax", m=M)
    asserts["engine_equals_simulate_band_synthetic"] = bool(float(np.max(np.abs(e_nav["_nav"] - ref_nav[0]))) < 1e-9)
    return {"asserts": asserts, "pass": bool(all(asserts.values()))}


# ----------------------------------------------------------------------------- main
def fmt_pct(x):
    return f"{x * 100:7.2f}%"


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--smoke", "--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    sm = smoke()
    print(f"smoke {'PASS' if sm['pass'] else 'FAIL'}: " +
          ", ".join(f"{k}={'P' if v else 'F'}" for k, v in sm["asserts"].items()))
    if mode == "--smoke":
        sys.exit(0 if sm["pass"] else 1)
    if not sm["pass"]:
        sys.exit(1)
    hc = harness(run_boot=(mode == "--run"))
    for k, v in hc["reproduction_checks"].items():
        if isinstance(v, dict) and "diff_pp" in v:
            print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
    print(f"harness n_outside_tolerance={hc['n_outside_tolerance']} {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        if mode == "--harness":
            json.dump(trv2.rnd(hc), sys.stdout, indent=2, sort_keys=True)
            print()
        sys.exit(0 if hc["pass"] else 1)

    res = run()
    cg, tv = boot_main(ARMS)
    boot = boot_summary(cg, tv, ARMS, FIN)
    gates = gates_and_selection(res)

    out = {
        "campaign": "breadth_vote_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N,
            "c0_band": C0_BAND, "cap": CAP, "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q,
            "early_path": {"sma_fast": SMA_FAST, "momentum_lag": LAG_FAST, "vol_window": trv2.VOL_N,
                           "vol_median_window": MED_WINDOW, "half_weight": 0.5},
            "financing_spread": FIN, "main_window_cost_multipliers": [1, 2, M_STRESS],
            "panel_rule": "Panel(X) = {X, SPY member, IWM member}; SPY member = SPY from "
                          "1993-01-29 else ^GSPC x factor; IWM member = IWM from 2000-05-26 else "
                          "^RUT x factor; each member carries the production votes on its own closes",
            "bootstrap_main": {"seed": SEED, "paths": PRE_BOOT_PATHS, "block": BLOCK, "warmup": trv2.WARM,
                               "measured": trv2.MEASURED, "m": M},
            "bootstrap_e1": {"seed": SEED, "paths_pre": PRE_BOOT_PATHS, "paths_tax": TAX_BOOT_PATHS,
                             "calendar": [TAX_BOOT_LO, TAX_BOOT_HI], "block": BLOCK, "m": M},
            "bootstrap_oos": {"seed": SEED, "paths_per_market": OOS_PATHS_PER_MARKET, "block": BLOCK,
                              "returns_per_path": OOS_BOOT_L, "m": M},
            "oos_windows": {k: list(v) for k, v in OOS_MARKETS.items()},
            "oos_not_applicable": list(OOS_NOT_APPLICABLE),
            "signals": "production votes on each panel member's own price closes: close > SMA200; "
                       "252-session price return > 0; close > 126-session max/min midpoint",
        },
        "smoke": sm, "harness_check": hc,
        "main_window": res["pre"], "bootstrap_main_block20_m2": boot,
        "after_tax_windows": res["windows"], "after_tax_bootstrap": res["bootstrap"],
        "oos": res["oos"], "oos_bootstrap": res["oos_bootstrap"],
        "instruments": res["instruments"], "quality": res["quality"],
        "gates": gates["arms"], "selection": gates["selection"],
        "status": "complete",
    }
    text = json.dumps(trv2.rnd(out), indent=2, sort_keys=True, allow_nan=False) + "\n"
    OUT.write_text(text)

    # ------------------------------------------------------------------ SUMMARY
    p = fmt_pct
    w = res["pre"]["window"]
    L = [f"breadth_vote_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
         f"C0 = production R2 (E2 + 0.5x early re-entry)",
         f"smoke: {'PASS' if sm['pass'] else 'FAIL'}; harness: {'PASS' if hc['pass'] else 'FAIL'} "
         f"(n_outside_tolerance={hc['n_outside_tolerance']})", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ P1 P2 P3 turn/yr | boot20 median, median(arm-C0) | m=1 CAGR")
        for arm in ARMS:
            a = res["pre"][md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            L.append(f"  {arm:3s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} {a['turnover_annual']:5.2f} | "
                     f"{p(b['cagr_median'])} {b['diff_vs_C0_median_pp']:+7.3f}pp | "
                     f"{p(res['pre'][md]['m1'][arm]['cagr'])}")
        L.append("")
    L.append("trades (base, m=2): arm | full window: round trips/yr, resizes/yr, sells/yr, turnover/yr "
             "(entries+exits / resizes) | 2017+: round trips/yr, sells/yr, turnover/yr")
    for arm in ARMS:
        e = res["pre"]["base"]["m2"][arm]["events_full"]
        f = res["pre"]["base"]["m2"][arm]["events_2017_on"]
        L.append(f"  {arm:3s} {e['round_trips_per_year']:5.2f} {e['resizes_per_year']:6.2f} {e['sells_per_year']:6.2f} "
                 f"{e['turnover_per_year']:5.2f} ({e['turnover_from_entries_exits_per_year']:4.2f} / "
                 f"{e['turnover_from_resizes_per_year']:4.2f}) | {f['round_trips_per_year']:5.2f} "
                 f"{f['sells_per_year']:6.2f} {f['turnover_per_year']:5.2f}")
    L.append("")
    L.append("after-tax ($100k, progressive, liquidated): window | arm pre-tax -> after-tax CAGR "
             "(delta vs C0 pre-tax) maxDD taxUSD sells/yr turnover/yr")
    for wn, d in res["windows"].items():
        L.append(f"  {wn}")
        for arm in ARMS:
            x = d[arm]
            L.append(f"    {arm:3s} {p(x['pre_tax_cagr'])} -> {p(x['after_tax_cagr'])} "
                     f"({(x['after_tax_cagr'] - d[CONTROL]['pre_tax_cagr']) * 100:+7.3f}pp) "
                     f"DD {p(x['max_dd_after_tax'])} tax {x['tax_usd']:>10,.0f} "
                     f"sells/yr {x['sells_per_year']:5.1f} turn/yr {x['turnover_annual_after_tax']:5.2f}")
    L.append("")
    L.append("after-tax bootstraps (block-20, 1000 paths, 2015-09..2026-09 calendar, $100k)")
    for label, mode in (("B1_recent progressive", "progressive"), ("B2_flat 20%", "flat")):
        b = res["bootstrap"]["tax"][mode]
        L.append(f"  {label}: C0 median after-tax CAGR {p(b[CONTROL]['cagr_median'])}")
        for arm in CANDIDATES:
            d = b[f"{arm}_vs_{CONTROL}"]
            L.append(f"    {arm:3s} median dCAGR {d['median_delta_cagr_pp']:+7.3f}pp share>0 "
                     f"{d['share_gt_0']:.3f} p05 {d['p05_delta_cagr_pp']:+7.3f} "
                     f"p95 {d['p95_delta_cagr_pp']:+7.3f}")
    L.append("")
    L.append("OOS (calibrated, m=2, reduced window set): window instrument period | arm CAGR / maxDD")
    for k in OOS_WINDOWS:
        v = res["oos"][k]
        L.append(f"  {k} {OOS_MARKETS[k][0]:6s} {v['window']['start']}->{v['window']['end']} | " +
                 "  ".join(f"{a} {p(v[a]['cagr']).strip()}/{p(v[a]['max_dd']).strip()}" for a in ARMS))
    L.append(f"  not applicable: {', '.join(OOS_NOT_APPLICABLE)} (instrument is the SPY proxy; ^RUT "
             f"starts 1987-09-10)")
    L.append("OOS pooled bootstrap (U1..U4, 8,000 paths) median(arm-C0): " +
             "  ".join(f"{a} {res['oos_bootstrap']['pooled'][a]['diff_vs_C0_median_pp']:+.3f}pp" for a in CANDIDATES))
    L.append("")
    L.append("instruments (21 ETFs, panel = {ETF, SPY, IWM}): arm | median dCAGR pp, share CAGR>0, "
             "n DD within +3pp, share, worst DD instrument")
    for arm in CANDIDATES:
        f3 = gates["arms"][arm]["F3_detail"]
        L.append(f"  {arm:3s} {f3['median_delta_cagr_pp']:+7.3f} {f3['share_cagr_gt_0']:.3f} "
                 f"{f3['n_dd_within']}/{f3['n_instruments']} {f3['share_dd_within_3pp']:.3f} "
                 f"{f3['worst_instrument']} {f3['worst_delta_max_dd_pp']:+.2f}pp")
    L.append("")
    KEYS = [f"{x}_{md}" for x in ("G1", "G2", "G3", "G5", "G6") for md in FIN] + ["G4", "G7", "G8", "G9", "AT", "B1", "B2", "F3"]
    L.append("gates (" + " ".join(KEYS) + "):")
    for arm in CANDIDATES:
        g = gates["arms"][arm]
        L.append(f"  {arm:3s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in KEYS) +
                 f" -> {'PASS' if g['pass_all'] else 'FAIL'}")
        L.append(f"      G7 median {g['G7_detail']['median_cagr_diff_pp']:+.3f}pp, "
                 f"{g['G7_detail']['windows_cagr_ge_minus_0_5pp']}/4 windows >= -0.5pp, "
                 f"{g['G7_detail']['windows_dd_within_3pp']}/4 DD within +3pp; "
                 f"B1 {g['boot']['B1_recent']['median_delta_pp']:+.3f}pp/{g['boot']['B1_recent']['share_gt_0']:.2f}, "
                 f"B2 {g['boot']['B2_flat']['median_delta_pp']:+.3f}pp/{g['boot']['B2_flat']['share_gt_0']:.2f}")
    L.append("")
    L.append("AT cells (after-tax $100k, liquidated CAGR arm vs C0):")
    for arm in CANDIDATES:
        c = gates["arms"][arm]["AT_cells"]
        L.append(f"  {arm:3s} " + "  ".join(f"{wn}: {p(c[wn]['arm']).strip()} vs {p(c[wn]['R2']).strip()} "
                                            f"({'P' if c[wn]['pass'] else 'F'})" for wn in TAX_WINDOWS))
    L.append("")
    L.append("cost stress (report only): base CAGR at m=1/2/4 and m=4 max DD")
    for arm in ARMS:
        r = res["pre"]["base"]
        L.append(f"  {arm:3s} {p(r['m1'][arm]['cagr']).strip()} / {p(r['m2'][arm]['cagr']).strip()} / "
                 f"{p(r['m4'][arm]['cagr']).strip()} | {p(r['m4'][arm]['max_dd']).strip()}")
    L.append("")
    L.append(f"eligible {gates['selection']['eligible']}; selected: {gates['selection']['selected']}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
