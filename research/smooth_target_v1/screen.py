#!/usr/bin/env python3
"""smooth_target_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Tests idea I3 of docs/research/idea_survey_v3.md: partial-adjustment / smoothed target weight
on top of production R2. R2's decisions, vote, early re-entry path, vol target, cap and 0.5 pp
resize band are unchanged; only the *path* by which the target is reached is smoothed, so the
traded volume Sum|dw| (and with it the realised Turkish taxable gain, ~91 % of which comes from
vol-target trims) falls.

Arms: C0 = production R2 exactly; S1 5-session mean of the raw target; S2 10-session mean;
S3 partial adjustment (w <- w + 0.2(w* - w) per session); S4 5-session mean on the on-state
vol sizing only, the early leg unsmoothed. Exits to flat are exempt (immediate) in S1-S3.
Every parameter is fixed in the pre-registration.

Modes
  --smoke    plumbing only, on synthetic random prices: every arm's target, the early path, the
             smoothing transforms, the instrument path, the leave-one-out path and the after-tax
             engine (progressive + flat) run end to end and the arm invariants are asserted.
             Touches no real data.
  --harness  real data, controls only: C0 reproduces e2_integration_v1's production-E2
             deviation row (base/calibrated x m=1/m=2) and research/fast_reentry_v1's R2
             main-window rows to 0.01 pp, the band simulator at 0.5 pp equals
             trend_robustness_v2.simulate_multi bit for bit, the fast rolling median equals the
             v1 loop reference bit for bit, C0's targets equal production E2's wants where the
             vote is on and R2's half weight where it is off, and C0 reproduces R2's after-tax
             anchors (three $100k windows, B1 3.5155 %, B2 3.9678 %).
  --run      re-runs --harness, stops if it fails, then runs every arm (C0, S1..S4) on the
             main window, the main block-20 bootstrap, the out-of-sample windows (^GSPC
             1957-1999 and e2_confirmation_v1's M1..M5), the pooled OOS bootstrap, gates
             G1-G4 and G6-G9 plus the non-inferiority replacement for G5, the 21-instrument
             test (gate I, drawdown-spread condition), the audit checks, the report-only
             trade/smoothing/gain-by-reason statistics and the corrected after-tax engine at
             $100k (three windows) with the B1-recent and B2 clean bootstraps. Writes
             RESULTS.json and SUMMARY.txt.

Engine helpers (synthetic 2x, SMA, vol20, vt, metrics, bootstrap, OOS loaders) are the
fast_reentry_v1 screen (itself built on research/trend_robustness_v2/screen.py, whose main()
is never run). The after-tax engine is research/smooth_target_v1/tax_engine.py, a byte copy of
the engine research/deriv_vote_v1 vendored from research/vt_level_tax_v1/screen.py (see
tax_engine.py's docstring).
"""

from __future__ import annotations

import bisect
import csv
import importlib.util
import json
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)
_te_spec = importlib.util.spec_from_file_location("fr2_tax_engine", HERE / "tax_engine.py")
te = importlib.util.module_from_spec(_te_spec)
_te_spec.loader.exec_module(te)

GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
DEVIATION = HERE / "E2_INTEGRATION_DEVIATION.json"
V1_RESULTS = ROOT / "research/fast_reentry_v1/RESULTS.json"
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = 200, 252, 126
LAG_FAST = 20
C0_BAND = trv2.BAND  # 0.005
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1  # 253 -> first measured session 2000-03-09 (e2_integration_v1 / signal_ensemble_v1)
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1  # 3 back-extension sessions (signal_ensemble_v1)
TOL = 1e-4  # 0.01 pp
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))
STAGE_B_WINDOW = ("2017-01-02", "2026-07-30")  # seg(): from the last close <= 2017-01-02 (2016-12-30)
# OOS
OOS_GSPC_FIRST, OOS_GSPC_LAST = "1957-01-02", "1999-12-31"
EXTRA_FIN = 0.0075
MARKETS = {  # e2_confirmation_v1
    "M1": ("^IXIC", "1971-01-01", "1975-01-02", "1999-12-31"),
    "M2": ("^GSPC", "1934-01-01", None, "1956-12-31"),
    "M3": ("^N225", "1900-01-01", None, "2026-09-29"),
    "M4": ("^GDAXI", "1900-01-01", None, "2026-09-29"),
    "M5": ("^FTSE", "1900-01-01", None, "2026-09-29"),
}
OOS_PATHS_PER_MARKET = 2000
OOS_BOOT_L = TSM_N + MEASURED
DTB3_FIRST = "1954-01-04"

# ----------------------------------------------------------------------------- pre-registered arm definitions
# The vote is production E2's in every arm (at least 2 of SMA200, TSM252 > 0, DON126 on the
# QQQ price close); only the path by which the target weight is reached changes. Every
# parameter is fixed in the pre-registration.
VOTE_SPEC = {
    "C0": {"votes": ("sma200", "tsm252", "don126"), "quorum": 2},          # production E2
}
# early re-entry path: R2's, identical in every arm (half production weight, SMA 50,
# vol-median window 252, 20-session momentum lag)
EARLY = {"sma": 50, "med": 252, "w": 0.5}
# smoothing: kind, sessions in the rolling mean / partial-adjustment step
SMOOTH_KIND = {"S1": "mean", "S2": "mean", "S3": "partial", "S4": "on_state_mean"}
SMOOTH_MEAN_K = {"S1": 5, "S2": 10, "S4": 5}
SMOOTH_LAMBDA = {"S3": 0.2}

ARMS = ("C0", "S1", "S2", "S3", "S4")
CANDIDATES = ("S1", "S2", "S3", "S4")

# instruments (gate I): 19 range_vol_v2 symbols + 3 range_vol_v1 symbols; EWC fails the
# range_vol_v2 quality rule and is excluded before simulation (see PREREGISTRATION.md).
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
INSTRUMENTS = tuple(sorted(INSTRUMENT_FILES))
QUALITY_MAX_BAD_FRAC = 0.05  # range_vol_v2's fixed rule
INSTRUMENT_MIN_SESSIONS = 500
GATE_I_SHARE_POS = 0.60
GATE_I_DD_SLACK = 0.03
GATE_I_DD_SHARE = 0.80
GATE_I_PATHS = 2000
# G5 is replaced, by pre-registration, with a non-inferiority test on the pre-tax clean
# block-20 bootstrap: median(arm - C0) >= -0.10 pp under both financing models.
GATE5_NONINFERIORITY_PP = 0.10
# gate I is gated, by pre-registration, on the drawdown-spread condition only; the return
# components are reported as reference but do not decide the arm.
GATE_I_GATED_KEYS = ("pass_share_dd",)

# after-tax: vt_level_tax_v1's windows at $100k
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITAL = 100_000.0
# C0 anchors: R2's rows from research/r2_tbill_combo_v1/RESULTS.json (progressive, $100k)
TAX_ANCHORS = {
    "2000_03_to_2026_09": {"pre_tax": 0.154732, "progressive": 0.101600, "progressive_max_dd": 0.467584},
    "2010_01_to_end": {"pre_tax": 0.221587, "progressive": 0.147059, "progressive_max_dd": 0.437839},
    "2017_01_to_end": {"pre_tax": 0.325330, "progressive": 0.228861, "progressive_max_dd": 0.362426},
}
# clean after-tax bootstrap (tax_asym_v2 construction, fixed 2015-09 -> 2026-09 calendar)
TAX_BOOT_PATHS = 1000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_EXT = 3
TAX_BOOT_L = 253 + 10 * 252
TAX_BOOT_I0 = TAX_BOOT_EXT + 253
TAX_BOOT_ANCHORS = {"B1_recent": 0.035155, "B2_flat": 0.039678}  # C0 (R2) medians, r2_tbill_combo_v1
GATE_TAX_SHARE_POS = 0.55
# reference only (never re-run here): gain_band_v2's A1 arm as reported in
# docs/research/gain_band_v2.md, whose control is production R2 and whose gate failure is G5.
GAIN_BAND_V2_A1_REFERENCE = {
    "source": "docs/research/gain_band_v2.md (evidence tag archive/research/gain-band-v2-2026-10)",
    "arm": "A1 gain-scaled no-trade band on downward resizes (0.5 / 3 / 8 pp by embedded indexed TRY gain)",
    "pre_tax_main_m2_base_cagr": 0.1568, "pre_tax_main_m2_cal_cagr": 0.1507,
    "pre_tax_max_dd_base": 0.4746, "pre_tax_max_dd_cal": 0.4758, "pre_tax_m1_base_cagr": 0.1594,
    "delta_cagr_base_pp": 0.21, "delta_cagr_cal_pp": 0.17, "delta_max_dd_pp": 0.70,
    "cagr_2017_on": 0.3318, "subperiod_cagr": {"P1": 0.0007, "P2": 0.1431, "P3": 0.3318},
    "turnover_annual": 5.48, "sells_per_year_main": 10.4,
    "boot_e1_base_median_delta_pp": 0.0002, "boot_e1_cal_median_delta_pp": -0.004,
    "boot_e1_share_gt_0": 0.50, "gate5_pass": False,
    "after_tax_100k_delta_pp": {"2000_03_to_2026_09": 0.457, "2010_01_to_end": 0.436,
                                "2017_01_to_end": 0.853},
    "b1_recent_median_delta_pp": 0.228, "b1_recent_share_gt_0": 0.755,
    "b2_flat_median_delta_pp": 0.093, "b2_flat_share_gt_0": 0.619,
    "oos_median_delta_cagr_pp": 0.040, "oos_pooled_boot_median_delta_pp": 0.032,
    "instruments_share_dd_within_3pp": 1.0, "instruments_n": 21,
    "verdict": "FAIL (G5 only)", "run_here": False}
AUDIT_PROBES = 64
AUDIT_SEED = 20261014


# ----------------------------------------------------------------------------- signals (production E2)
def tsm_up_price(c, n=TSM_N):
    """up[t]: n-session price return > 0 (production 1.1.0: zero hurdle on split-adjusted price)."""
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


def e2_up(price):
    """Production E2 vote on the price close: at least 2 of SMA200, TSM252 (> 0), DON126."""
    s = trv2.sma_up(price, SMA_N).astype(int)
    t = tsm_up_price(price).astype(int)
    d = don_up(price).astype(int)
    return (s + t + d) >= 2


# ----------------------------------------------------------------------------- production sizing
def wants_for(e2, vol):
    """[P, T] QLD* targets: E2 vote x vt(vol), total capped at 0.98 as trend_robustness_v2.build_wants."""
    raw = np.atleast_2d(e2.astype(float) * trv2.vt_of(vol))
    f = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * f


def _rolling_median_vol_ref(price_vol, window=252):
    """Reference (v1) trailing rolling median of the vol series; NaN until the window is full."""
    P, T = price_vol.shape
    out = np.full((P, T), np.nan)
    with warnings.catch_warnings():  # all-NaN windows before the vol window fills -> NaN, guarded by cnt
        warnings.simplefilter("ignore", RuntimeWarning)
        for t in range(T):
            lo = max(0, t - window + 1)
            w = price_vol[:, lo:t + 1]
            cnt = np.sum(np.isfinite(w), axis=1)
            med = np.nanmedian(np.where(np.isfinite(w), w, np.nan), axis=1)
            out[:, t] = np.where(cnt >= window, med, np.nan)
    return out


def _rolling_median_vol(price_vol, window=252, block=32):
    """Same as _rolling_median_vol_ref, computed with sliding windows (bit-identical; harness-checked).

    Valid only when NaNs form a prefix per path (true for trv2.vol_n output); asserted, so a
    series with interior NaNs fails loudly instead of silently returning a wrong median.
    """
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
            if np.any(np.diff(fin[r].astype(np.int8)) < 0):  # a finite value followed by a NaN
                raise ValueError("interior NaN in the vol series; use _rolling_median_vol_ref")
        j0 = int(first.min())
        if T - j0 < window:  # no session has a full trailing window yet
            continue
        out[s:e, j0 + window - 1:] = np.median(
            sliding_window_view(blk[:, j0:], window, axis=1), axis=2)
    return out


def early_components(price, sma_n, med_window):
    """Path-independent early-path inputs: SMA_n vote, vol20, trailing median of vol20, close > close[-20]."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma_up_v = trv2.sma_up(price, sma_n)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol(vol_p, med_window)
    close_lag = np.full_like(price, np.nan)
    close_lag[:, LAG_FAST:] = price[:, :-LAG_FAST]
    return sma_up_v, vol_p, med_p, price > close_lag


def comps_for(price, sma_n, med_window, cache):
    key = (sma_n, med_window)
    if key not in cache:
        cache[key] = early_components(price, sma_n, med_window)
    return cache[key]


# ----------------------------------------------------------------------------- vote components
# Production's three level conditions on the QQQ price close; the vote is E2's in every arm.
VOTE_FUNCS = {
    "sma200": lambda p: trv2.sma_up(p, SMA_N),
    "tsm252": lambda p: tsm_up_price(p, TSM_N),
    "don126": lambda p: don_up(p, DON_N),
}


def vote_components(price, cache=None):
    """Every vote component on `price`, memoised per component key + shape when `cache` is given."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    out = {}
    for key, fn in VOTE_FUNCS.items():
        if cache is not None and (key, price.shape) in cache:
            out[key] = cache[(key, price.shape)]
        else:
            out[key] = fn(price)
            if cache is not None:
                cache[(key, price.shape)] = out[key]
    return out


def vote_of(price, arm, cache=None):
    """[P, T] boolean vote of one arm: at least `quorum` of its components hold."""
    spec = VOTE_SPEC[arm]
    comps = vote_components(price, cache)
    s = np.zeros(np.atleast_2d(price).shape, dtype=int)
    for key in spec["votes"]:
        s = s + comps[key].astype(int)
    return s >= spec["quorum"]


def r2_raw(price, vote, vol_instr, comps=None):
    """R2's raw target series [P, T], R2's early-path flag and the on-state vol sizing.

    The raw target is production R2's: on-state (the vote holds) -> `min(1, 0.35/vol20)` of the
    held instrument capped at 0.98; vote off with the early path active -> half that; vote off
    with the early path inactive -> 0. The early path is R2's: while the vote is off, enter at
    half the on-state weight if close > SMA50 and vol20 < the trailing 252-session median of
    vol20 and close > close[-20]; exit if close < SMA50; the path ends the moment the vote turns
    on. `vote` is production E2 in every arm (see VOTE_SPEC).
    """
    price = np.atleast_2d(np.asarray(price, dtype=float))
    vote = np.atleast_2d(np.asarray(vote, dtype=bool))
    vt = trv2.vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)))
    P, T = price.shape
    if comps is None:
        comps = early_components(price, EARLY["sma"], EARLY["med"])
    sma_up_v, vol_p, med_p, lag_cond = comps
    on_eff = np.zeros((P, T), dtype=bool)
    early_hist = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    active = np.zeros(P, dtype=bool)
    for t in range(T):
        vt_ = vote[:, t]
        active = np.where(vt_, False, active)  # early path ends when the vote turns on
        active = np.where(active & (~sma_up_v[:, t]) & (~vt_), False, active)  # exit: close < SMA50
        entry = sma_up_v[:, t] & np.isfinite(med_p[:, t]) & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t]
        can_enter = (~active) & (~vt_) & np.isfinite(med_p[:, t])
        active = np.where(can_enter & entry, True, active)
        on_eff[:, t] = vt_ | active
        early_hist[:, t] = active
        factor[:, t] = np.where(vt_, 1.0, np.where(active, EARLY["w"], 0.0))
    raw = on_eff.astype(float) * factor * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    raw = raw * fcap
    # the on-state sizing on its own (the same cap applied to vt alone; it binds only at vt > CAP)
    fcap_v = np.where(vt > CAP, CAP / np.where(vt > 0, vt, 1.0), 1.0)
    return raw, early_hist, vt * fcap_v


def smooth_target(kind, w_star, vote, early, sizing, k=5, lam=0.2):
    """The arm's target series [P, T] from R2's raw target `w_star` [P, T].

    * ``mean``            rolling mean of ``w_star`` over the last ``k`` completed closes,
                          zeros (sessions the raw target was 0) included.
    * ``partial``         ``w_t = w_{t-1} + lam * (w*_t - w_{t-1})``, the state starting at 0
                          and reset to 0 on an exempt session.
    * ``on_state_mean``   rolling mean of the on-state vol sizing ``sizing`` over the last ``k``
                          closes where the vote is on; R2's own unsmoothed half of ``sizing``
                          while the early path is active; 0 otherwise.

    Exemption in every kind: where the vote is off and the early path is inactive the target is
    0 - the exit to flat is immediate, exactly as in R2. The 0.5 pp resize band is applied
    afterwards, to this target against the held weight (simulate_band / the after-tax engine).
    """
    vote = np.atleast_2d(np.asarray(vote, dtype=bool))
    early = np.atleast_2d(np.asarray(early, dtype=bool))
    w_star = np.atleast_2d(np.asarray(w_star, dtype=float))
    sizing = np.atleast_2d(np.asarray(sizing, dtype=float))
    flat = (~vote) & (~early)
    if kind in ("mean", "on_state_mean"):
        src = w_star if kind == "mean" else sizing
        P, T = src.shape
        cs = np.concatenate([np.zeros((P, 1)), np.cumsum(src, axis=1)], axis=1)
        idx = np.arange(T)
        lo = np.maximum(0, idx - k + 1)
        cnt = (idx + 1 - lo).astype(float)
        out = (cs[:, idx + 1] - cs[:, lo]) / cnt
        if kind == "on_state_mean":
            out = np.where(vote, out, np.where(early, EARLY["w"] * sizing, 0.0))
        return np.where(flat, 0.0, out)
    if kind == "partial":
        P, T = w_star.shape
        out = np.empty((P, T))
        state = np.zeros(P)
        for t in range(T):
            state = np.where(flat[:, t], 0.0, state + lam * (w_star[:, t] - state))
            out[:, t] = state
        return out
    raise ValueError(kind)


def arm_target(price, arm, vote, vol_instr, comps=None):
    """One arm's target series [P, T] and R2's early-path flag.

    C0 is R2's own raw target series bit for bit; S1-S4 are the pre-registered smoothings of it.
    """
    w_star, early, sizing = r2_raw(price, vote, vol_instr, comps)
    if arm == "C0":
        return w_star, early
    w = smooth_target(SMOOTH_KIND[arm], w_star, vote, early, sizing,
                      k=SMOOTH_MEAN_K.get(arm, 5), lam=SMOOTH_LAMBDA.get(arm, 0.2))
    return w, early


def arm_wants(price, vol_instr, arms):
    """{arm: (targets, early_hist)}; every arm shares production E2's vote and R2's early path.

    The cache is keyed by price shape so the chunked bootstrap reuses one computation per chunk.
    """
    price = np.atleast_2d(np.asarray(price, dtype=float))
    comps = comps_for(price, EARLY["sma"], EARLY["med"], {})
    vote = vote_of(price, "C0")
    return {arm: arm_target(price, arm, vote, vol_instr, comps) for arm in arms}


def votes_by_arm(price, arms):
    """{arm: vote [P, T]} - the same production E2 vote for every arm."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    v = vote_of(price, "C0")
    return {arm: v for arm in arms}


# ----------------------------------------------------------------------------- engine
def simulate_band(opens, closes, wants, cost, start, band):
    """Single-leg trend_robustness_v2 mechanics with a resize band.

    Per session i: overnight growth; target = wants[i-1] (decided at the completed close);
    an entry (held 0, target > 0) or exit (held > 0, target 0) always trades to target;
    otherwise (both > 0) the position is resized to target only if |target - held| > band.
    Cost = cost x |delta w|; then intraday growth. Idle cash earns zero.
    With band = 0.005 this equals trend_robustness_v2.simulate_multi (one leg).
    Returns nav [P, T-start+1], turnover [P], trades [P], event codes [P, T-start]
    (0 none, 1 entry, 2 exit, 3 resize up, 4 resize down) and traded |dw| [P, T-start]."""
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
    """From the last close <= a (first bar if a is None or before data) to the last close <= b (end if None)."""
    ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1), "max_dd": float(trv2.max_dd(s)[0])}


def event_stats(dates, codes, traded, a=None, b=None):
    """Trade counts per year between the sessions after close a and through close b (dates[k+1] is session k)."""
    sess = dates[1:]
    lo = 0 if a is None else next(k for k, d in enumerate(sess) if d > a)
    hi = len(sess) if b is None else max(k for k, d in enumerate(sess) if d <= b) + 1
    c, tr = codes[lo:hi], traded[lo:hi]
    yrs = trv2.calendar_years(dates[lo], sess[hi - 1])
    n = {name: int(np.sum(c == v)) for name, v in (("entries", 1), ("exits", 2), ("resize_up", 3), ("resize_down", 4))}
    return {
        "years": yrs,
        "counts": n,
        "round_trips_per_year": n["exits"] / yrs,
        "trades_per_year": (n["entries"] + n["exits"] + n["resize_up"] + n["resize_down"]) / yrs,
        "resizes_per_year": (n["resize_up"] + n["resize_down"]) / yrs,
        "sells_per_year": (n["exits"] + n["resize_down"]) / yrs,
        "turnover_per_year": float(tr.sum() / yrs),
        "turnover_from_entries_exits_per_year": float(tr[(c == 1) | (c == 2)].sum() / yrs),
        "turnover_from_resizes_per_year": float(tr[(c == 3) | (c == 4)].sum() / yrs),
    }


def arm_stats(dates, nav, turn, ntr, codes, traded, wants, start, extras, early_active=None):
    years = trv2.calendar_years(dates[0], dates[-1])
    st = trv2.curve_stats(dates, nav)
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["sharpe"] = trv2.sharpe_daily(nav)
    st["turnover_annual"] = float(turn / years)
    st["sum_abs_dw"] = float(turn)
    st["trades"] = int(ntr)
    st["subperiods"] = {name: seg(dates, nav, a, b) for name, a, b in SUBPERIODS}
    if extras:
        st["stage_b_window"] = seg(dates, nav, *STAGE_B_WINDOW)
        st["events_full"] = event_stats(dates, codes, traded)
        st["events_2017_on"] = event_stats(dates, codes, traded, "2016-12-31")
        tgt = wants[start - 1:-1]
        st["time_in_market"] = float((tgt > 0).mean())
        st["mean_target"] = float(tgt.mean())
        if early_active is not None:
            ea = np.atleast_2d(early_active)
            sl = slice(start - 1, start - 1 + len(tgt))
            st["early_time_in_market"] = float(ea[0, sl].mean())
        st["sessions_target_below_10pp_in_trend"] = int(np.sum((tgt > 0) & (tgt < 0.10)))
    return st


# ----------------------------------------------------------------------------- main window
def early_episodes(dates, nav, nav_c0, early_aligned):
    """Per-episode P&L of the early positions (base financing, m = 2).

    early_aligned[k] is the early path's decision at the close of dates[k], so the episode holds the
    early position over the sessions k0+1 .. k1+1. The P&L span runs from the close of dates[k0] to
    the close of the reconciliation session (the next open after the early path stops driving the
    target), where the early-driven position is either sold to cash or merged into production E2's
    position. That includes the exit fill and the overnight gap the early position earned, so the
    product of (1 + delta) over episodes reproduces the arm's total relative gain."""
    ea = np.asarray(early_aligned, dtype=bool)
    n = min(len(ea), len(nav) - 1, len(nav_c0) - 1)
    idx = np.flatnonzero(ea[:n])
    eps = []
    if idx.size:
        cut = np.flatnonzero(np.diff(idx) > 1)
        for s, e in zip(np.concatenate([[0], cut + 1]), np.concatenate([cut, [idx.size - 1]])):
            k0, k1 = int(idx[s]), int(idx[e])
            i1 = min(k1 + 2, len(nav) - 1)
            ra = float(nav[i1] / nav[k0] - 1)
            rc = float(nav_c0[i1] / nav_c0[k0] - 1)
            eps.append({"entry_decision": dates[k0], "last_early_decision": dates[k1],
                        "first_fill_session": dates[k0 + 1], "last_held_session": dates[k1 + 1],
                        "reconciliation_session": dates[i1], "sessions_held": k1 + 1 - k0,
                        "arm_return": ra, "c0_return": rc, "delta": ra - rc})
    return eps


def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    return days, arr, o, c, q[1], trv2.vol_n(c)[0]


def main_stage(arms, extras=False, m_list=(1, 2)):
    """Main window for every arm and both financing models; returns (results, context)."""
    res = {}
    ctx = {}
    for md, spread in FIN.items():
        days, arr, o, c, price, v20 = main_inputs(spread)
        dates = days[START - 1:]
        got = arm_wants(price, v20, arms)
        res[md] = {}
        for m in m_list:
            hm = {}
            navs = {}
            for arm in arms:
                w, ea = got[arm]
                nav, turn, ntr, codes, traded = simulate_band(o, c, w, COST_PER_SIDE * m, START, C0_BAND)
                navs[arm] = nav[0]
                hm[arm] = arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], w[0], START,
                                    extras and m == 2, early_active=ea)
            if extras and m == 2 and "C0" in arms:
                for arm in arms:
                    if arm != "C0":
                        hm[arm]["early_episodes"] = early_episodes(
                            dates, navs[arm], navs["C0"], got[arm][1][0][START - 1:])
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
        ctx[md] = {"days": days, "o": o, "c": c, "dates": dates, "price": price,
                   "wants": {a: got[a][0][0] for a in arms}, "early": {a: got[a][1][0] for a in arms}}
    return res, ctx


# ----------------------------------------------------------------------------- harness
def jload(path):
    return json.loads(Path(path).read_text())


def e2_only_rows(m_list=(1, 2)):
    """Production E2 with no early path: the e2_integration_v1 deviation row, reproduced."""
    out = {}
    for md, spread in FIN.items():
        days, arr, o, c, price, v20 = main_inputs(spread)
        dates = days[START - 1:]
        w = wants_for(e2_up(price), v20)
        out[md] = {}
        for m in m_list:
            nav, turn, ntr, codes, traded = simulate_band(o, c, w, COST_PER_SIDE * m, START, C0_BAND)
            out[md][f"m{m}"] = arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], w[0], START, False)
    return out


def harness():
    ref = jload(DEVIATION)
    e2r = e2_only_rows()
    h, _ = main_stage(("C0",))
    checks = {}
    for md in FIN:
        for m in (1, 2):
            r = ref[f"{md}_m{m}"]["arms"]["E2_prod_price_h0"]
            x = e2r[md][f"m{m}"]
            for k in ("cagr", "max_dd"):
                checks[f"{md}_m{m}_{k}"] = {"reference": r[k], "reproduced": x[k], "diff_pp": (x[k] - r[k]) * 100}
    ok_dev = all(abs(v["diff_pp"]) <= TOL * 100 for v in checks.values())

    # C0's vote (the component machinery with VOTE_SPEC["C0"]) == production e2_up
    days, arr, o, c, price, v20 = main_inputs(0.0)
    e2_prod = e2_up(price)
    vote_c0 = vote_of(price, "C0")[0]
    e2_equal = bool(np.array_equal(vote_c0, e2_prod[0]))
    # C0's targets: production wants where the vote is on, 0.5 x vt or flat where it is off
    w_prod = wants_for(e2_prod, v20)[0]
    w_c0, ea_c0 = arm_wants(price, v20, ("C0",))["C0"]
    w_c0 = w_c0[0]
    on = e2_prod[0]
    off = ~on
    half_w = 0.5 * trv2.vt_of(v20)
    ok_on = bool(np.allclose(w_c0[on], w_prod[on], atol=1e-12))
    ok_off = bool(np.allclose(w_c0[off], np.where(ea_c0[0][off], half_w[off], 0.0), atol=1e-12))
    c0_structure = {"on_sessions_equal_production_E2": ok_on,
                    "off_sessions_equal_half_vt_or_flat": ok_off, "pass": bool(ok_on and ok_off)}

    # band simulator at 0.5 pp == trend_robustness_v2.simulate_multi (bit for bit), base m = 2
    w = wants_for(e2_prod, v20)
    nav_b = simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0]
    nav_m = trv2.simulate_multi(o, c, w[:, None, :], COST_PER_SIDE * 2, START)[0]
    sim_equal = bool(np.array_equal(nav_b, nav_m))

    # fast rolling median == v1 loop reference (bit for bit), on the main window and on 3 paths
    vol_p = trv2.vol_n(price)
    med_ref = _rolling_median_vol_ref(vol_p)
    med_fast = _rolling_median_vol(vol_p)
    med_equal_1d = bool(np.array_equal(med_ref, med_fast, equal_nan=True))
    rng = np.random.default_rng(SEED)
    pv = np.abs(rng.normal(0.3, 0.05, (3, 1500)))
    pv[:, :20] = np.nan
    med_equal_multi = bool(np.array_equal(_rolling_median_vol_ref(pv, 252), _rolling_median_vol(pv, 252),
                                          equal_nan=True))
    med_equal = med_equal_1d and med_equal_multi

    # S1-S4 smoothing transforms and the C0 identity, vs a direct per-session recomputation
    comp_checks = smoothing_checks(price, e2_prod, v20, rng)

    # recomputed C0 main-window rows vs fast_reentry_v1's RESULTS.json R2 row (0.01 pp):
    # R2 there is C0 here. Both arms of the v1 table are checked against this study's C0.
    v1 = jload(V1_RESULTS)
    v1_checks = {}
    for md in FIN:
        for m in (1, 2):
            r1 = v1["main_window"][md][f"m{m}"]["R2"]
            h2 = h[md][f"m{m}"]["C0"]
            for k in ("cagr", "max_dd"):
                d = (h2[k] - r1[k]) * 100
                v1_checks[f"{md}_m{m}_{k}"] = {"reference": r1[k], "reproduced": h2[k], "diff_pp": d}
            if m == 2:
                d = (h2["turnover_annual"] - r1["turnover_annual"])
                v1_checks[f"{md}_m2_turnover"] = {"reference": r1["turnover_annual"],
                                                  "reproduced": h2["turnover_annual"], "diff_pp": d}
    ok_v1 = all(abs(v["diff_pp"]) <= 0.01 for v in v1_checks.values())

    # report-only reference: v1's R2 bootstrap median (base)
    boot_check = None
    try:
        b1 = v1["bootstrap_main_block20_m2"]["base"]["R2"]["diff_vs_C0_median"]
        boot_check = {"v1_reference_pp": b1 * 100}
    except KeyError:
        pass
    ok = (ok_dev and e2_equal and c0_structure["pass"] and sim_equal and med_equal
          and comp_checks["pass"] and ok_v1)
    return {"window": h["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100,
            "vote_C0_equals_production_E2": e2_equal,
            "C0_structure_checks": c0_structure,
            "component_checks": comp_checks,
            "band_simulator_equals_simulate_multi": sim_equal,
            "rolling_median_fast_equals_v1_reference": med_equal,
            "v1_reproduction_checks": v1_checks, "v1_reproduction_pass": ok_v1,
            "v1_bootstrap_reference": boot_check, "pass": bool(ok)}


def _naive_target(kind, w_star, vote, early, sizing, t, k, lam):
    """Direct per-session recomputation of one arm's smoothed target at session `t`.

    Independent of `smooth_target` (plain Python loops) so a vectorisation error cannot hide.
    """
    if not vote[t] and not early[t]:  # the flat exemption, in every kind
        return 0.0
    if kind == "mean":
        lo = max(0, t - k + 1)
        return float(np.mean(w_star[lo:t + 1]))
    if kind == "on_state_mean":
        if vote[t]:
            lo = max(0, t - k + 1)
            return float(np.mean(sizing[lo:t + 1]))
        return float(EARLY["w"] * sizing[t]) if early[t] else 0.0
    if kind == "partial":
        w = 0.0
        for u in range(t + 1):
            w = 0.0 if (not vote[u] and not early[u]) else w + lam * (w_star[u] - w)
        return w
    raise ValueError(kind)


def smoothing_checks(price, vote, vol_instr, rng):
    """S1-S4 target series vs the direct recomputation, plus the exemption and the C0 identity."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    vote = np.atleast_2d(np.asarray(vote, dtype=bool))
    w_star, early, sizing = r2_raw(price, vote, vol_instr)
    w_star, early, sizing, v = w_star[0], early[0], sizing[0], vote[0]
    T = price.shape[1]
    ts = sorted(rng.choice(np.arange(600, T), size=min(AUDIT_PROBES, T - 600), replace=False).tolist())
    tgt = {a: arm_target(price, a, vote, vol_instr)[0][0] for a in ARMS}
    bad = {}
    for a in CANDIDATES:
        kind, k = SMOOTH_KIND[a], SMOOTH_MEAN_K.get(a, 5)
        lam = SMOOTH_LAMBDA.get(a, 0.2)
        bad[a] = [t for t in ts
                  if abs(tgt[a][t] - _naive_target(kind, w_star, v, early, sizing, t, k, lam)) > 1e-9]
    flat = (~v) & (~early)
    exempt = {a: bool(np.all(tgt[a][flat] == 0.0)) for a in CANDIDATES}
    bounds = {a: bool(np.all(tgt[a] <= CAP + 1e-12) and np.all(tgt[a] >= -1e-15)) for a in CANDIDATES}
    c0_ident = bool(np.array_equal(tgt["C0"], w_star))
    return {"probes": len(ts), "mismatches": {k: v for k, v in bad.items() if v},
            "exemption_holds": exempt, "targets_within_bounds": bounds,
            "c0_equals_raw_r2_targets": c0_ident,
            "pass": bool(not any(bad.values()) and all(exempt.values()) and all(bounds.values())
                         and c0_ident)}


# ----------------------------------------------------------------------------- bootstrap (main window)
def boot_main(arms):
    """Stationary block bootstrap (mean block 20), draws identical to signal_ensemble_v1 (seed, order,
    chunks, 3-session back-extension). Signals on the resampled price level; instrument on the
    resampled total-return level. All arms on the same paths, m = 2."""
    _, arr, y = trv2.load()
    q = arr["QQQ"]
    T = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = len(pool)
    L = WARM + MEASURED
    start = trv2.SMA_MAX + 1 + EXT
    cg = {(md, a): [] for md in FIN for a in arms}
    tv = {(md, a): [] for md in FIN for a in arms}
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
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        vcache = {}
        ecache = {}
        for md in FIN:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            vol_instr = trv2.vol_n(scl)
            comps = comps_for(pc, EARLY["sma"], EARLY["med"], ecache)
            for arm in arms:
                w, _ = arm_target(pc, arm, vote_of(pc, "C0", vcache), vol_instr, comps)
                nav, turn, _, _, _ = simulate_band(so, scl, w, COST_PER_SIDE * 2, start, C0_BAND)
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(turn / (k / 252))
    return ({key: np.concatenate(v) for key, v in cg.items()}, {key: np.concatenate(v) for key, v in tv.items()})


def boot_summary(cg, tv, arms, models):
    out = {}
    for md in models:
        out[md] = {}
        c0 = cg[(md, "C0")]
        for a in arms:
            c = cg[(md, a)]
            d = c - c0
            out[md][a] = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                          "turnover_median": float(np.median(tv[(md, a)])),
                          "diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                          "diff_vs_C0_p25": float(np.percentile(d, 25)), "diff_vs_C0_p75": float(np.percentile(d, 75)),
                          "share_ge_C0": float(np.mean(d >= 0))}
    return out


# ----------------------------------------------------------------------------- OOS data (copied loaders)
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
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    vol_instr = trv2.vol_n(sc)
    got = arm_wants(c, vol_instr, arms)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        w = got[arm][0]
        nav, turn, ntr, codes, traded = simulate_band(so, sc, w, COST_PER_SIDE * 2, start, C0_BAND)
        nav = nav[0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        ev = event_stats(dates, codes[0], traded[0])
        res[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd), "turnover_annual": float(turn[0] / years),
                    "round_trips_per_year": ev["round_trips_per_year"], "sells_per_year": ev["sells_per_year"],
                    "resizes_per_year": ev["resizes_per_year"]}
    return res


def boot_oos_market(key, rng, arms):
    """e2_confirmation_v1.bootstrap_market draws (shared generator, markets in order), all arms, m = 2."""
    days, o, c, y, start = load_market(key)
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
        cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        vol_instr = trv2.vol_n(sc)
        got = arm_wants(cc, vol_instr, arms)
        for arm in arms:
            nav = simulate_band(so, sc, got[arm][0], COST_PER_SIDE * 2, START, C0_BAND)[0]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


# ----------------------------------------------------------------------------- gates (ewma_band_v1, unchanged)
def g_return(a, c):
    """CAGR >= C0, or (Calmar >= 1.1 x C0 and CAGR >= C0 - 0.5 pp)."""
    return bool(a["cagr"] >= c["cagr"] or (a["calmar"] >= 1.10 * c["calmar"] and a["cagr"] >= c["cagr"] - 0.005))


def historical_gates(main, boot, arm):
    r = {}
    for md in FIN:
        a2, c2 = main[md]["m2"][arm], main[md]["m2"]["C0"]
        a1, c1 = main[md]["m1"][arm], main[md]["m1"]["C0"]
        r[f"G1_{md}"] = g_return(a2, c2)
        r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
        r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
        # G5 is replaced by pre-registration with non-inferiority on the pre-tax clean bootstrap
        r[f"G5n_{md}"] = bool(boot[md][arm]["diff_vs_C0_median"] >= -GATE5_NONINFERIORITY_PP / 100.0)
        r[f"G6_{md}"] = g_return(a1, c1)
    a2, c2 = main["base"]["m2"][arm], main["base"]["m2"]["C0"]
    wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
    r["G4"] = bool(len(wins) >= 2)
    r["G4_wins"] = wins
    lim = c2["turnover_annual"] * 2.0
    r["G9"] = bool(a2["turnover_annual"] <= lim)
    return r


def oos_gates(oos, oos_boot_pooled, arm):
    keys = ["GSPC"] + list(MARKETS)
    dc = {k: oos[k][arm]["cagr"] - oos[k]["C0"]["cagr"] for k in keys}
    dd = {k: oos[k][arm]["max_dd"] - oos[k]["C0"]["max_dd"] for k in keys}
    med = float(np.median(list(dc.values())))
    n_c = sum(1 for v in dc.values() if v >= -0.005)
    n_d = sum(1 for v in dd.values() if v <= 0.03 + 1e-12)
    g7 = bool(med >= 0 and n_c >= 5 and n_d >= 5 and max(dd.values()) <= 0.08 + 1e-12)
    g8 = bool(oos_boot_pooled[arm]["diff_vs_C0_median"] >= 0)
    return {"G7": g7, "G7_detail": {"cagr_diff": dc, "max_dd_diff": dd, "median_cagr_diff": med,
                                    "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                                    "max_dd_diff_max": max(dd.values())},
            "G8": g8, "G8_pooled_median_diff": oos_boot_pooled[arm]["diff_vs_C0_median"]}


GATE_KEYS = ([f"{g}_{md}" for g in ("G1", "G2", "G3", "G5n", "G6") for md in FIN]
             + ["G4", "G7", "G8", "G9"])
CORE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3") for md in FIN]


# ----------------------------------------------------------------------------- instruments (gate I)
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
    """range_vol_v2's fixed data-quality rule: > 5% of bars with high == low or an invalid open."""
    bad = 0
    for hi, lo, op, cl in zip(high, low, opn, close):
        if hi is None or lo is None or hi == lo:
            bad += 1
            continue
        if op is None or op <= 0 or op < lo * (1 - 1e-9) or op > hi * (1 + 1e-9):
            bad += 1
    frac = bad / max(1, len(high))
    return frac, bool(frac <= QUALITY_MAX_BAD_FRAC)


def instrument_window(days, o, high, low, c, y, arms, start=START):
    """Own E2 + own early path on the instrument's adjusted close, own 2x synthetic leg (calibrated, m = 2)."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    vol_instr = trv2.vol_n(sc)
    got = arm_wants(c, vol_instr, arms)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        nav, turn, ntr, codes, traded = simulate_band(so, sc, got[arm][0], COST_PER_SIDE * 2, start, C0_BAND)
        nav = nav[0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        ev = event_stats(dates, codes[0], traded[0])
        res[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd), "turnover_annual": float(turn[0] / years),
                    "round_trips_per_year": ev["round_trips_per_year"], "sells_per_year": ev["sells_per_year"]}
    return res


def boot_instrument(key, rng, arms):
    """Block-20 bootstrap on one instrument's own leg (calibrated, m = 2), 2,000 paths."""
    days, o, h, l, c, y = load_instrument(key)
    pool = np.arange(START, len(days))
    npool = len(pool)
    acc = {a: [] for a in arms}
    for chunk in range(0, GATE_I_PATHS, CHUNK):
        P = min(CHUNK, GATE_I_PATHS - chunk)
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
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        vol_instr = trv2.vol_n(sc)
        got = arm_wants(cc, vol_instr, arms)
        for arm in arms:
            nav = simulate_band(so, sc, got[arm][0], COST_PER_SIDE * 2, START, C0_BAND)[0]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


def instrument_stage(arms, keys):
    """Per-instrument windows + quality report; returns (res, quality, pooled bootstrap)."""
    res, quality = {}, {}
    for k in keys:
        days, o, h, l, c, y = load_instrument(k)
        frac, ok = quality_ok(h, l, o, c)
        quality[k] = {"bad_bar_fraction": frac, "sessions": len(days), "pass": ok,
                      "first": days[0], "last": days[-1]}
        if not ok or len(days) < INSTRUMENT_MIN_SESSIONS:
            continue
        res[k] = instrument_window(days, o, h, l, c, y, arms)
    rng = np.random.default_rng(SEED)
    per = {k: boot_instrument(k, rng, arms) for k in res}
    pooled = {a: np.concatenate([per[k][a] for k in res]) for a in arms}
    return res, quality, per, pooled


def gate_i(inst, pooled, arm):
    keys = sorted(inst)
    dc = {k: inst[k][arm]["cagr"] - inst[k]["C0"]["cagr"] for k in keys}
    dd = {k: inst[k][arm]["max_dd"] - inst[k]["C0"]["max_dd"] for k in keys}
    med = float(np.median(list(dc.values())))
    share_pos = float(np.mean([v > 0 for v in dc.values()]))
    share_dd = float(np.mean([v <= GATE_I_DD_SLACK + 1e-12 for v in dd.values()]))
    p = pooled[arm] - pooled["C0"]
    pooled_med = float(np.median(p))
    return {"arm": arm, "n_instruments": len(keys), "median_delta_cagr": med,
            "share_delta_cagr_gt_0": share_pos, "share_delta_dd_within_3pp": share_dd,
            "cagr_median": float(np.median([inst[k][arm]["cagr"] for k in keys])),
            "pooled_median_delta_cagr": pooled_med, "pooled_share_ge_0": float(np.mean(p >= 0)),
            "pooled_paths": int(len(p)),
            "pass_median": bool(med >= 0), "pass_share_pos": bool(share_pos >= GATE_I_SHARE_POS),
            "pass_share_dd": bool(share_dd >= GATE_I_DD_SHARE), "pass_pooled": bool(pooled_med >= 0),
            "deltas": dc, "dd_diffs": dd,
            "rule": (f"GATED (pre-registration): share dDD <= +{GATE_I_DD_SLACK * 100:.0f} pp >= "
                     f"{GATE_I_DD_SHARE:.0%}. Reference only: median dCAGR >= 0, share dCAGR > 0 >= "
                     f"{GATE_I_SHARE_POS:.0%}, pooled bootstrap median d >= 0 ({GATE_I_PATHS} paths/instrument)")}


# ----------------------------------------------------------------------------- leave-one-episode-out
def leave_one_episode_out(dates, o, c, wants_c0, wants_arm, episodes, n_top=3):
    """Re-simulate an arm with each of its largest early episodes' decisions replaced by C0's."""
    c0_nav = simulate_band(o, c, wants_c0, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
    c0_cagr = trv2.cagr(c0_nav[0], c0_nav[-1], trv2.calendar_years(dates[0], dates[-1]))
    out = []
    for e in sorted(episodes, key=lambda x: -x["delta"])[:n_top]:
        k0 = dates.index(e["entry_decision"])
        i1 = dates.index(e["reconciliation_session"])
        w = wants_arm.copy()
        w[START - 1 + k0:START - 1 + i1] = wants_c0[START - 1 + k0:START - 1 + i1]
        nav = simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
        cg = trv2.cagr(nav[0], nav[-1], trv2.calendar_years(dates[0], dates[-1]))
        out.append({"episode_entry": e["entry_decision"], "episode_last_held": e["last_held_session"],
                    "episode_delta_pp": e["delta"] * 100, "sessions_held": e["sessions_held"],
                    "cagr_with_episode_flat": float(cg), "cagr_delta_vs_C0": float(cg - c0_cagr)})
    return {"c0_cagr": float(c0_cagr), "episodes": out}


# ----------------------------------------------------------------------------- audit of the arm machinery
def audit(days, o, c, price, v20, arm, wants, early_hist):
    """Look-ahead, fill-timing, state-machine and cost checks on one arm's mechanics."""
    x = {}
    # A1 truncation probe: the arm's decision and vote at t must not change when the future is removed
    rng = np.random.default_rng(AUDIT_SEED)
    T = len(price)
    ts = sorted(rng.choice(np.arange(300, T), size=min(AUDIT_PROBES, T - 300), replace=False).tolist())
    mism = []
    for t in ts:
        w_t, ea_t = arm_target(price[:t + 1], arm, vote_of(price[:t + 1], "C0"), v20[:t + 1])
        if not (w_t[0, -1] == wants[t] and ea_t[0, -1] == early_hist[t]):
            mism.append(days[t])
    x["truncation_probe"] = {"probes": len(ts), "mismatches": mism, "pass": not mism,
                             "sessions": f"{arm} target and early flag at t from closes <= t"}
    # A2 direct recomputation of the early inputs at sampled sessions
    bad = []
    sma_n, med_w = EARLY["sma"], EARLY["med"]
    vol_p = trv2.vol_n(price)[0]
    comps = early_components(price, sma_n, med_w)
    for t in ts:
        want_sma = bool(t >= sma_n and price[t] > price[t - sma_n + 1:t + 1].mean())
        want_lag = bool(t >= LAG_FAST and price[t] > price[t - LAG_FAST])
        win = vol_p[t - med_w + 1:t + 1]
        want_med = float(np.median(win)) if (t >= med_w + 19 and np.all(np.isfinite(win))) else None
        if bool(comps[0][0, t]) != want_sma or bool(comps[3][0, t]) != want_lag:
            bad.append(days[t])
        got_med = float(comps[2][0, t])
        if want_med is None:
            if np.isfinite(got_med):
                bad.append(days[t])
        elif not (np.isfinite(got_med) and abs(got_med - want_med) < 1e-12):
            bad.append(days[t])
    x["trailing_window_recompute"] = {"probes": len(ts), "mismatches": bad, "pass": not bad}
    # A3 fill timing: the decision at the close of session i-1 is filled at the open of session i
    oo = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
    cc = np.array([100.0, 100.0, 100.0, 200.0, 200.0])
    ww = np.array([0.0, 1.0, 1.0, 1.0, 1.0])
    nav = simulate_band(oo, cc, ww, 0.0, 1, C0_BAND)[0][0]
    x["fill_timing"] = {"nav": float(nav[-1]), "expected_close_last_over_open_session2": float(cc[-1] / oo[2]),
                        "pass": bool(abs(nav[-1] - cc[-1] / oo[2]) < 1e-12)}
    # A4 cost linearity: nav drag roughly doubles when the per-side cost doubles
    nav0 = simulate_band(o, c, wants, 0.0, START, C0_BAND)[0][0]
    nav1 = simulate_band(o, c, wants, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
    nav2 = simulate_band(o, c, wants, COST_PER_SIDE * 4, START, C0_BAND)[0][0]
    d1 = float(1 - nav1[-1] / nav0[-1])
    d2 = float(1 - nav2[-1] / nav0[-1])
    x["cost_linearity"] = {"drag_4bps_m2": d1, "drag_8bps_m2": d2, "ratio": (d2 / d1 if d1 else None),
                           "pass": bool(d1 > 0 and 1.5 <= d2 / d1 <= 2.5)}
    # A5 state machine invariants
    vote = vote_of(price, "C0")[0]
    flat = (~vote) & (~np.asarray(early_hist, dtype=bool))
    inv = {
        "early_only_while_vote_off": not bool(np.any(early_hist & vote)),
        "targets_within_cap": bool(np.all(wants <= CAP + 1e-12)),
        "targets_non_negative": bool(np.all(wants >= -1e-15)),
        # the pre-registered flat exemption: vote off and early path inactive -> target 0
        "flat_sessions_target_zero": bool(np.all(np.asarray(wants, dtype=float)[flat] == 0.0)),
    }
    x["invariants"] = inv
    x["pass"] = bool(x["truncation_probe"]["pass"] and x["trailing_window_recompute"]["pass"]
                     and x["fill_timing"]["pass"] and x["cost_linearity"]["pass"] and all(inv.values()))
    return x


def c0_wants_base():
    """C0 (production R2) target weights on base financing: the after-tax engine's C0 input."""
    days, arr, o, c, price, v20 = main_inputs(0.0)
    return arm_wants(price, v20, ("C0",))["C0"][0][0]


# ----------------------------------------------------------------------------- after tax (report only)
def after_tax_stage(wants, arms=ARMS):
    """Corrected vt_level_tax_v1 engine at $100k on the three windows, every arm."""
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y, EXP_Q)  # base financing, as vt_level_tax_v1
    te.init(days, {a: (o, c) for a in arms}, {a: wants[a] for a in arms})
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    out = {"capital_usd": TAX_CAPITAL, "windows": {}, "anchors": {}}
    for wn, (a, b) in TAX_WINDOWS.items():
        out["windows"][wn] = {}
        for arm in arms:
            pre = te.run_strategy(arm, "pre_tax", TAX_CAPITAL, a, b, mkt)
            prog = te.run_strategy(arm, "progressive", TAX_CAPITAL, a, b, mkt)
            out["windows"][wn][arm] = {
                "pre_tax": {"cagr": pre["cagr_usd_liquidated"], "max_dd": pre["max_dd_nav_usd"],
                            "trades_per_year": pre["strategy_trades"] / pre["years"]},
                "progressive": {"cagr": prog["cagr_usd_liquidated"], "max_dd": prog["max_dd_nav_usd"],
                                "tax_usd": prog["tax_total_usd_at_payment_fx"], "terminal_usd": prog["terminal_usd_liquidated"],
                                "trades_per_year": prog["strategy_trades"] / prog["years"],
                                "sells_per_year": sells_per_year(prog),
                                "gain_try_by_reason": prog["realised_gain_try_by_reason"],
                                "gain_share_from_trims_vs_exits": trim_exit_share(prog)},
            }
        ref = TAX_ANCHORS[wn]
        got = out["windows"][wn]["C0"]
        out["anchors"][wn] = {
            "pre_tax": {"reference": ref["pre_tax"], "reproduced": got["pre_tax"]["cagr"],
                        "diff_pp": (got["pre_tax"]["cagr"] - ref["pre_tax"]) * 100},
            "progressive": {"reference": ref["progressive"], "reproduced": got["progressive"]["cagr"],
                            "diff_pp": (got["progressive"]["cagr"] - ref["progressive"]) * 100},
            "progressive_max_dd": {"reference": ref["progressive_max_dd"], "reproduced": got["progressive"]["max_dd"],
                                   "diff_pp": (got["progressive"]["max_dd"] - ref["progressive_max_dd"]) * 100},
        }
    out["pass"] = all(abs(v["diff_pp"]) <= 0.005 for wn in out["anchors"].values() for v in wn.values())
    return out


def sells_per_year(r):
    """Down-trades per year (trims + exits, excluding tax-funding sales and the liquidation)."""
    return r["strategy_sells"] / r["years"] if r.get("strategy_sells") is not None else None


def trim_exit_share(r):
    """Share of the realised TRY gain booked by down-resizes (trims) against full exits.

    `realised_gain_try_by_reason` carries `resize` (down-trade that keeps a positive target),
    `exit` (down-trade to cash), `tax` (tax-funding sale) and `liquidation`. Net here means
    positive + negative gain in that reason, so a reason that books net losses contributes a
    negative amount; the share uses the sum over all reasons as its denominator (the same
    construction as the 91 % figure quoted in the survey)."""
    g = dict(r.get("realised_gain_try_by_reason", {}))
    total = sum(g.values())
    trims = g.get("resize", 0.0)
    exits = g.get("exit", 0.0)
    return {"gain_try_by_reason": {k: float(v) for k, v in sorted(g.items())},
            "trim_gain_try": float(trims), "exit_gain_try": float(exits),
            "total_net_gain_try": float(total),
            "trim_share_of_total": float(trims / total) if total else None,
            "exit_share_of_total": float(exits / total) if total else None,
            "trim_pos_share_of_pos_gain": None}


def run_strategy_flat(capital, i0, i1, dates, opens_arr, closes_arr, wants_arr, mkt):
    """B2 engine: vt_level_tax_v1's `run_strategy_flat` (flat 20 % on the annual net realised
    USD gain, losses carried forward) with the reporting-only reason labels of
    tax_engine.run_strategy (down-trade to a zero target -> "exit")."""
    days_all = list(dates)
    opens_arr = np.asarray(opens_arr, float).flatten()
    closes_arr = np.asarray(closes_arr, float).flatten()
    wants_arr = np.asarray(wants_arr, float).flatten()
    cost = COST_PER_SIDE * 2
    cashbox = {"v": float(capital)}
    account = te.FlatAccount(mkt, 0.0, cashbox)
    cashbox["v"] = float(capital)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = sells = tax_sales = 0
    for i in range(i0, i1 + 1):
        if i > i0 and int(days_all[i][:4]) != int(days_all[i - 1][:4]):
            prevy = int(days_all[i][:4]) - 1
            acct_tax = account.assess(prevy)
            account.tax_due[prevy] = acct_tax
            due = acct_tax
            if due > 1e-9:
                usd = due / mkt.fx[i] if mkt.fx[i] != 0 else due
                if cashbox["v"] < usd:
                    need = usd - cashbox["v"]
                    if account.units > 1e-12:
                        price = float(closes_arr[i - 1])
                        units_sell = need / price if price > 0 else 0.0
                        account.sell(i - 1 if i - 1 >= 0 else i, price, units_sell,
                                     units_sell * price * cost, "tax")
                        tax_sales += 1
                cashbox["v"] -= usd
                account.tax_paid_try[prevy] = account.tax_paid_try.get(prevy, 0.0) + due
                account.tax_paid_usd += usd

        po = float(opens_arr[i])
        pc = float(closes_arr[i])
        nav_open = cashbox["v"] + account.units * po
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = (account.units * po) / nav_open if nav_open > 0 else 0.0
        tgt = float(wants_arr[i - 1]) if i > 0 else float(wants_arr[0])
        tgt = 0.0 if tgt != tgt else tgt
        trade = abs(tgt - w) > C0_BAND
        if account.units == 0 and tgt > 1e-12:
            trade = True
        if account.units > 1e-12 and tgt <= 0:
            trade = True
        if trade:
            drift = abs(tgt - w)
            fee = nav_open * cost * drift if drift > 0 else 0.0
            target_units = tgt * (nav_open - fee) / po if po > 0 else 0.0
            du = target_units - account.units
            if du > 1e-12:
                account.buy(i, po, du * po, fee)
            elif du < -1e-12:
                account.sell(i, po, -du, fee, "exit" if tgt <= 0 else "resize")
                sells += 1
            trades += 1
        nav[i - i0 + 1] = cashbox["v"] + account.units * pc

    end_y = int(days_all[i1][:4])
    if account.units > 0:
        account.sell(i1, float(closes_arr[i1]), account.units,
                     account.units * float(closes_arr[i1]) * cost, "liquidation")
    liq_tax = account.assess(end_y) + account.outstanding()
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - (liq_tax / fx1 if fx1 != 0 else liq_tax)
    yrs = trv2.calendar_years(days_all[i0 - 1], days_all[i1])
    cg = lambda v0, v1: (v1 / v0) ** (1.0 / yrs) - 1 if yrs > 0 else float("nan")
    maxdd = float(trv2.max_dd(nav.reshape(1, -1))[0])
    return {
        "window": [days_all[i0], days_all[i1]],
        "years": float(yrs),
        "capital_usd": capital,
        "ending_nav_usd": float(nav[-1]),
        "max_dd_nav_usd": maxdd,
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "terminal_usd_liquidated": float(liq),
        "strategy_trades": int(trades),
        "strategy_sells": int(sells),
        "tax_funding_sales": int(tax_sales),
        "fees_usd": float(account.fees),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(account.gain_by_reason.items())},
    }


def tax_boot_calendar():
    """Fixed 2015-09-01 -> 2026-09-30 calendar with its real FX (vt_level_tax_v1 / tax_asym_v2)."""
    all_days, arr, y = trv2.load()
    sub = [d for d in all_days if TAX_BOOT_LO <= d <= TAX_BOOT_HI]
    need = TAX_BOOT_L + TAX_BOOT_EXT
    if len(sub) < need:
        raise RuntimeError(f"fixed calendar {TAX_BOOT_LO}..{TAX_BOOT_HI} has only {len(sub)} days, need {need}")
    return all_days, arr, y, list(sub[:need])


def fx_at(dates):
    """CBRT USD/TRY forex buying, forward-filled to `dates` (bisect_right - 1, as te.load_market)."""
    fd, fv = [], []
    with FX_CSV.open() as fh:
        for r in csv.DictReader(fh):
            fd.append(r["date"])
            fv.append(float(r["usdtry_forex_buying"]))
    return np.array([fv[bisect.bisect_right(fd, d) - 1] for d in dates], dtype=float)


def after_tax_bootstrap(n_paths=TAX_BOOT_PATHS, capital=TAX_CAPITAL, arms=ARMS):
    """Clean after-tax bootstrap (B1 = progressive, B2 = 20 % flat, losses carried forward):
    block-20 stationary bootstrap of 10-year paths on the fixed 2015-09 -> 2026-09 calendar with
    its real FX, wants recomputed per arm on each resampled price path. Returns per-arm medians
    and, when C0 is among `arms`, the per-path delta of every other arm against C0."""
    all_days, arr, y, fixed_dates = tax_boot_calendar()
    fixed_fx = fx_at(fixed_dates)
    base_mkt = te.load_market(FX_CSV, UFE_CSV, all_days)
    mkt = te.Market(fixed_dates, fixed_fx, base_mkt.ufe)
    q = arr["QQQ"]
    Tsrc = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, Tsrc)
    npool = len(pool)
    L = TAX_BOOT_L
    i0, i1 = TAX_BOOT_I0, len(fixed_dates) - 1
    res = {mode: {a: {"cagr": [], "maxdd": [], "sells": []} for a in arms}
           for mode in ("progressive", "flat")}
    for pidx in range(n_paths):
        pos = np.empty((1, L), dtype=np.int64)
        pos[0, 0] = rng.integers(0, npool)
        jump = rng.random((1, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (1, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(TAX_BOOT_EXT, 0, -1)], axis=1)
        pos = np.concatenate([pre, pos], axis=1)
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
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + 0.0, EXP_Q)
        vol = trv2.vol_n(sc)
        got = arm_wants(pc, vol, arms)
        te.init(fixed_dates, {a: (so[0], sc[0]) for a in arms},
                {a: got[a][0][0] for a in arms})
        for arm in arms:
            w = got[arm][0][0]
            r = te.run_strategy(arm, "progressive", capital, i0, i1, mkt)
            if not (r["terminal_usd_liquidated"] > 0):
                raise RuntimeError(f"non-positive terminal on path {pidx} arm {arm} (progressive)")
            rf = run_strategy_flat(capital, i0, i1, fixed_dates, so[0], sc[0], w, mkt)
            if not (rf["terminal_usd_liquidated"] > 0):
                raise RuntimeError(f"non-positive terminal on path {pidx} arm {arm} (flat)")
            for mode, rr in (("progressive", r), ("flat", rf)):
                res[mode][arm]["cagr"].append(rr["cagr_usd_liquidated"])
                res[mode][arm]["maxdd"].append(rr["max_dd_nav_usd"])
                res[mode][arm]["sells"].append(rr["strategy_sells"] / rr["years"])
    out = {}
    for mode in ("progressive", "flat"):
        e = {"tax_kind": mode, "n_paths": n_paths, "calendar_start": fixed_dates[0],
             "calendar_end": fixed_dates[-1], "seed": SEED, "block": BLOCK, "capital_usd": capital,
             "arms": {}}
        c0 = np.array(res[mode]["C0"]["cagr"]) if "C0" in arms else None
        for a in arms:
            c = np.array(res[mode][a]["cagr"])
            d = c - c0 if c0 is not None else np.zeros_like(c)
            e["arms"][a] = {
                "cagr_median": float(np.median(c)), "maxdd_median": float(np.median(res[mode][a]["maxdd"])),
                "sells_per_year_median": float(np.median(res[mode][a]["sells"])),
                "delta_vs_C0_median_pp": float(np.median(d) * 100) if c0 is not None else None,
                "delta_vs_C0_mean_pp": float(np.mean(d) * 100) if c0 is not None else None,
                "share_delta_gt_0": float(np.mean(d > 0)) if c0 is not None else None,
                "p05_delta_pp": float(np.percentile(d, 5) * 100), "p95_delta_pp": float(np.percentile(d, 95) * 100),
            }
        out[mode] = e
    out["anchor_checks"] = {} if "C0" not in arms else {
        mode: {"reference": TAX_BOOT_ANCHORS[key], "reproduced": out[mode]["arms"]["C0"]["cagr_median"],
               "diff_pp": (out[mode]["arms"]["C0"]["cagr_median"] - TAX_BOOT_ANCHORS[key]) * 100}
        for mode, key in (("progressive", "B1_recent"), ("flat", "B2_flat"))}
    out["pass"] = all(abs(v["diff_pp"]) <= 0.01 for v in out["anchor_checks"].values())
    return out


# ----------------------------------------------------------------------------- smoke (synthetic only)
def synthetic_bars(n, seed):
    """Random-walk synthetic bars for the smoke test (no real data)."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.013, n)
    price = 100.0 * np.cumprod(1 + r)
    o = np.empty(n)
    o[0] = price[0]
    o[1:] = price[:-1] * (1 + rng.normal(0, 0.004, n - 1))
    div = np.zeros(n)
    y = np.full(n, 0.01)
    d0 = date(2000, 1, 3)
    days = [(d0 + timedelta(days=k)).isoformat() for k in range(n)]
    return days, o, price, div, y


def smoke():
    """Run the whole path on synthetic data; assert the arm invariants."""
    days, o, price, div, y = synthetic_bars(1400, 7)
    so, sc = trv2.synthetic_2x(o, price, div, y, EXP_Q)
    v20 = trv2.vol_n(sc)[0]
    vote = vote_of(price, "C0")
    got = arm_wants(price, v20, ARMS)
    checks = {}
    for arm in ARMS:
        w, ea = got[arm]
        assert w.shape == (1, len(price)), arm
        assert np.all(w <= CAP + 1e-12) and np.all(w >= 0), arm
        assert not np.any(ea[0] & vote[0]), arm  # the early path is only active while the vote is off
        flat = (~vote[0]) & (~ea[0])
        assert np.all(w[0][flat] == 0.0), arm    # the flat exemption
        checks[arm] = {"mean_target": float(w.mean()), "vote_share": float(vote[0].mean()),
                       "early_share": float(ea[0].mean()),
                       "share_flat_sessions": float(flat.mean()),
                       "vote_flips_per_year": float(np.sum(vote[0][1:] != vote[0][:-1])
                                                     / trv2.calendar_years(days[0], days[-1]))}
    # C0 == R2's raw target series, and the smoothing transforms == the direct recomputation
    sm = smoothing_checks(price, vote, v20, np.random.default_rng(11))
    assert sm["pass"], sm
    # main-window simulator + stats
    stats = {}
    for arm in ARMS:
        nav, turn, ntr, codes, traded = simulate_band(o, sc, got[arm][0], COST_PER_SIDE * 2, START, C0_BAND)
        assert np.all(np.isfinite(nav)) and np.all(nav > 0)
        stats[arm] = {"cagr": float(trv2.cagr(nav[0, 0], nav[0, -1], trv2.calendar_years(days[START - 1], days[-1]))),
                      "turnover": float(turn[0]), "sells": int(np.sum((codes[0] == 2) | (codes[0] == 4)))}
    # instrument path on a second synthetic series
    _, o2, p2, _, _ = synthetic_bars(900, 11)
    h2 = np.maximum(o2, p2); l2 = np.minimum(o2, p2)
    frac, ok = quality_ok(h2, l2, o2, p2)
    iwin = instrument_window(days[:900], o2, h2, l2, p2, y[:900], ARMS)
    # after-tax paths on a synthetic market (progressive + flat)
    ufe = {f"{yr}-{mo:02d}": float(1000 + 12 * yr + 5 * mo) for yr in range(1995, 2027) for mo in range(1, 13)}
    mkt = te.Market(days, np.full(len(days), 30.0), ufe)
    w_c0 = np.zeros(len(price)); w_c0[600:] = 0.5
    te.init(days, {a: (so, sc) for a in ARMS}, {a: w_c0 for a in ARMS})
    tax = te.run_strategy("C0", "progressive", TAX_CAPITAL, days[300], days[-1], mkt)
    flat = run_strategy_flat(TAX_CAPITAL, 300, len(days) - 1, days, so, sc, w_c0, mkt)
    return {"checks": checks, "stats": stats, "smoothing": sm,
            "instrument_quality": {"fraction": frac, "pass": ok},
            "instrument_window_cagr": {a: iwin[a]["cagr"] for a in ARMS},
            "after_tax_cagr": tax["cagr_usd_liquidated"],
            "flat_cagr": flat["cagr_usd_liquidated"],
            "sells_per_year": tax["strategy_sells"] / tax["years"],
            "pass": bool(all(np.isfinite(list(stats[a].values())).all() for a in stats)
                         and np.isfinite(tax["cagr_usd_liquidated"])
                         and np.isfinite(flat["cagr_usd_liquidated"])
                         and sm["pass"])}




# ----------------------------------------------------------------------------- report-only statistics
def vote_stats(dates, price, arm, cache, since=None):
    """Vote on/off flips per year on the measured window (optionally the tail after `since`)."""
    v = vote_of(price, "C0", cache)[0][START - 1:]
    d = dates
    if since is not None:
        k = next(i for i, x in enumerate(d) if x > since)
        v = v[k:]
        d = d[k:]
    up = int(np.sum((~v[:-1]) & v[1:]))
    down = int(np.sum(v[:-1] & (~v[1:])))
    yrs = trv2.calendar_years(d[0], d[-1])
    return {"years": yrs, "vote_share": float(v.mean()),
            "vote_on_flips_per_year": up / yrs, "vote_off_flips_per_year": down / yrs,
            "vote_flips_per_year": (up + down) / yrs}


def after_tax_report(out):
    """Health of the after-tax anchors (three windows and the two clean bootstraps)."""
    return {"windows_pass": out["windows_pass"], "bootstrap_pass": out["boot"]["pass"],
            "window_anchor_max_abs_pp": max(abs(v["diff_pp"]) for wn in out["windows_anchors"].values()
                                            for v in wn.values())}


# ----------------------------------------------------------------------------- main
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--smoke", "--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    if mode == "--smoke":
        s = smoke()
        print(json.dumps(s, indent=2, sort_keys=True))
        print("smoke PASS" if s["pass"] else "smoke FAIL")
        sys.exit(0 if s["pass"] else 1)

    hc = harness()
    for k, v in hc["deviation_checks"].items():
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
    n_bad = sum(1 for v in hc["v1_reproduction_checks"].values() if abs(v["diff_pp"]) > 0.01)
    print(f"harness v1 R2 reproduction: {len(hc['v1_reproduction_checks'])} checks, {n_bad} outside 0.01 pp")
    print(f"harness vote(C0) == production E2: {hc['vote_C0_equals_production_E2']}; "
          f"C0 structure (on = production, off = half vt): {hc['C0_structure_checks']['pass']}")
    print(f"harness smoothing checks: {hc['component_checks']['probes']} probes, pass "
          f"{hc['component_checks']['pass']}")
    print(f"harness band simulator == simulate_multi: {hc['band_simulator_equals_simulate_multi']}")
    print(f"harness rolling median fast == v1 reference: {hc['rolling_median_fast_equals_v1_reference']}")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    for k, v in hc["component_checks"]["mismatches"].items():
        print(f"  smoothing mismatch {k}: {v}")
    if mode == "--harness":
        c0w = c0_wants_base()
        taxh = after_tax_stage({"C0": c0w}, arms=("C0",))
        booth = after_tax_bootstrap(arms=("C0",))
        for wn, ch in taxh["anchors"].items():
            print(f"harness after-tax {wn}: " + "  ".join(f"{k} ref {v['reference']:.6f} repro {v['reproduced']:.6f} "
                                                          f"diff {v['diff_pp']:+.6f} pp" for k, v in ch.items()))
        for md, ch in booth["anchor_checks"].items():
            print(f"harness bootstrap {md}: ref {ch['reference']:.6f} repro {ch['reproduced']:.6f} "
                  f"diff {ch['diff_pp']:+.6f} pp")
        ok = hc["pass"] and taxh["pass"] and booth["pass"]
        print(f"harness after-tax anchors {'PASS' if taxh['pass'] else 'FAIL'}; "
              f"clean bootstraps {'PASS' if booth['pass'] else 'FAIL'}")
        print(f"harness (all) {'PASS' if ok else 'FAIL'}")
        sys.exit(0 if ok else 1)
    if not hc["pass"]:
        sys.exit(1)

    main_res, ctx = main_stage(ARMS, extras=True, m_list=(1, 2, 4))
    print("main window done", flush=True)
    cg, tv = boot_main(ARMS)
    boot = boot_summary(cg, tv, ARMS, FIN)
    print("main bootstrap done", flush=True)
    oos = {"GSPC": oos_window(*load_gspc(), ARMS)}
    for k in MARKETS:
        oos[k] = oos_window(*load_market(k), ARMS)
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, ARMS) for k in MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in ARMS}
    oos_boot = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        oos_boot["per_market"][k] = {a: {"median_diff": float(np.median(per[k][a] - per[k]["C0"])),
                                         "cagr_median": float(np.median(per[k][a]))} for a in ARMS}
    for a in ARMS:
        d = pooled[a] - pooled["C0"]
        oos_boot["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                                 "share_ge_C0": float(np.mean(d >= 0)), "paths": int(len(d))}
    print("OOS done", flush=True)

    gates = {}
    for arm in CANDIDATES:
        g = historical_gates(main_res, boot, arm)
        g.update(oos_gates(oos, oos_boot["pooled"], arm))
        g["pass_all"] = bool(all(g[k] for k in GATE_KEYS))
        g["core_G1_G3"] = bool(all(g[k] for k in CORE_KEYS))
        gates[arm] = g

    # ---- gate I: 21 independent instruments, each with its own C0 ----
    inst, inst_quality, inst_boot_per, inst_boot_pooled = instrument_stage(ARMS, INSTRUMENTS)
    gi = {arm: gate_i(inst, inst_boot_pooled, arm) for arm in CANDIDATES}
    for arm in CANDIDATES:
        gi[arm]["pass_all"] = bool(all(gi[arm][k] for k in GATE_I_GATED_KEYS))
        gates[arm]["G_I"] = gi[arm]["pass_all"]
        gates[arm]["pass_all"] = bool(gates[arm]["pass_all"] and gi[arm]["pass_all"])
    excluded = sorted(k for k in INSTRUMENTS if k not in inst)
    print("instruments done", flush=True)

    # ---- audit (all arms) + leave-one-episode-out + report-only statistics ----
    aud = {}
    for arm in ARMS:
        aud[arm] = audit(ctx["base"]["days"], ctx["base"]["o"], ctx["base"]["c"], ctx["base"]["price"],
                         trv2.vol_n(ctx["base"]["c"])[0], arm, ctx["base"]["wants"][arm],
                         ctx["base"]["early"][arm])
    vcache = {}
    vstats = {arm: {"full": vote_stats(ctx["base"]["dates"], ctx["base"]["price"], arm, vcache),
                    "since_2017": vote_stats(ctx["base"]["dates"], ctx["base"]["price"], arm, vcache,
                                             since="2016-12-31")} for arm in ARMS}
    loo = {}
    for arm in CANDIDATES:
        eps = main_res["base"]["m2"][arm]["early_episodes"]
        if eps:
            loo[arm] = leave_one_episode_out(ctx["base"]["dates"], ctx["base"]["o"], ctx["base"]["c"],
                                             ctx["base"]["wants"]["C0"], ctx["base"]["wants"][arm], eps)
    print("audit, vote stats and leave-one-out done", flush=True)

    # ---- after tax ----
    c0w = c0_wants_base()
    assert np.array_equal(c0w, ctx["base"]["wants"]["C0"])  # the after-tax C0 input is the pre-tax one
    tax = after_tax_stage({a: ctx["base"]["wants"][a] for a in ARMS}, arms=ARMS)
    tax["windows_pass"] = tax["pass"]
    tax["windows_anchors"] = tax["anchors"]
    tax_boot = after_tax_bootstrap(arms=ARMS)
    print("after tax done", flush=True)

    # ---- pre-registered after-tax gate + selection ----
    tax_gates = {}
    for wn in TAX_WINDOWS:
        for arm in CANDIDATES:
            d = tax["windows"][wn][arm]["progressive"]["cagr"] - tax["windows"][wn]["C0"]["progressive"]["cagr"]
            tax_gates.setdefault(arm, {})[f"T_{wn}"] = {
                "rule": "candidate after-tax CAGR > C0 after tax in this window",
                "delta_pp": d * 100, "pass": bool(d > 0)}
    for arm in CANDIDATES:
        for mode in ("progressive", "flat"):
            e = tax_boot[mode]["arms"][arm]
            tax_gates[arm][f"B_{'B1_recent' if mode == 'progressive' else 'B2_flat'}_median"] = {
                "rule": "bootstrap median delta vs C0 >= 0",
                "delta_pp": e["delta_vs_C0_median_pp"], "pass": bool(e["delta_vs_C0_median_pp"] >= 0)}
            tax_gates[arm][f"B_{'B1_recent' if mode == 'progressive' else 'B2_flat'}_share"] = {
                "rule": f"bootstrap share > 0 >= {GATE_TAX_SHARE_POS:.0%}",
                "share": e["share_delta_gt_0"], "pass": bool(e["share_delta_gt_0"] >= GATE_TAX_SHARE_POS)}
        tax_gates[arm]["windows_all_pass"] = all(v["pass"] for k, v in tax_gates[arm].items()
                                                 if k.startswith("T_"))
        tax_gates[arm]["bootstrap_all_pass"] = all(v["pass"] for k, v in tax_gates[arm].items()
                                                   if k.startswith("B_"))
        tax_gates[arm]["pass_all"] = bool(tax_gates[arm]["windows_all_pass"]
                                          and tax_gates[arm]["bootstrap_all_pass"])
        gates[arm]["T_after_tax"] = tax_gates[arm]["pass_all"]
        gates[arm]["pass_all"] = bool(gates[arm]["pass_all"] and tax_gates[arm]["pass_all"])

    h2 = main_res["base"]["m2"]
    eligible = [arm for arm in CANDIDATES if gates[arm]["pass_all"]]
    sel = max(eligible, key=lambda a: h2[a]["cagr"]) if eligible else "none"
    selection = {"eligible": eligible, "selected": sel,
                 "rule": "candidate passing G1-G9, gate I and every after-tax condition, highest base m=2 CAGR"}

    out = {
        "campaign": "smooth_target_v1",
        "verdict": "CANDIDATE" if sel != "none" else "FAIL",
        "params": {"arms": list(ARMS), "candidates": list(CANDIDATES), "vote_spec": dict(VOTE_SPEC),
                   "early_path": dict(EARLY), "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N,
                   "smoothing": {"kind": dict(SMOOTH_KIND), "mean_k": dict(SMOOTH_MEAN_K),
                                 "partial_lambda": dict(SMOOTH_LAMBDA),
                                 "flat_exemption": "target 0 where the vote is off and the early "
                                                   "path is inactive (exit to flat immediate)"},
                   "gate5_replacement": {"rule": f"pre-tax clean block-20 bootstrap median(arm - C0) "
                                                 f">= -{GATE5_NONINFERIORITY_PP} pp under base and calibrated",
                                         "models": list(FIN)},
                   "gate_i_gated_keys": list(GATE_I_GATED_KEYS),
                   "c0_band": C0_BAND, "cap": CAP,
                   "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q, "main_window_cost_multipliers": [1, 2, 4],
                   "financing_spread": FIN, "momentum_lag": LAG_FAST,
                   "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warmup": WARM,
                                      "back_extension": EXT, "measured": MEASURED, "m": 2},
                   "bootstrap_oos": {"seed": SEED, "paths_per_market": OOS_PATHS_PER_MARKET, "block": BLOCK,
                                     "returns_per_path": OOS_BOOT_L, "m": 2},
                   "bootstrap_tax": {"seed": SEED, "paths": TAX_BOOT_PATHS, "block": BLOCK,
                                     "calendar": [TAX_BOOT_LO, TAX_BOOT_HI], "m": 2},
                   "gate_i": {"paths_per_instrument": GATE_I_PATHS, "block": BLOCK,
                              "returns_per_path": OOS_BOOT_L, "financing_spread": EXTRA_FIN, "m": 2,
                              "instruments": list(INSTRUMENTS), "quality_rule_max_bad_fraction": QUALITY_MAX_BAD_FRAC},
                   "signals": "C0 = production R2 = E2 1.2.0 (the vote is E2's 2 of 3 on the QQQ "
                              "price close in every arm) plus R2's half-weight early re-entry path; "
                              "S1-S4 smooth only the path by which the target weight is reached"},
        "harness_check": hc,
        "audit": aud,
        "vote_stats": vstats,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates_g1_g9": {a: {k: v for k, v in gates[a].items()} for a in CANDIDATES},
        "gate_I_instruments": gi,
        "instruments": {"quality": inst_quality, "excluded": excluded, "windows": inst,
                        "bootstrap_median": {a: float(np.median(inst_boot_pooled[a])) for a in ARMS}},
        "leave_one_episode_out": loo,
        "after_tax_100k": tax,
        "after_tax_bootstrap": tax_boot,
        "after_tax_gates": tax_gates,
        "reference_gain_band_v2_A1": GAIN_BAND_V2_A1_REFERENCE,
        "selection": selection,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    w = main_res["window"]
    L = [f"smooth_target_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
         f"C0 = production R2 (E2 + half-weight early path) | verdict {out['verdict']}",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'} (max |diff| "
         f"{max(abs(v['diff_pp']) for v in hc['deviation_checks'].values()):.6f} pp vs e2_integration_v1; "
         f"v1 R2 reproduction {'PASS' if hc['v1_reproduction_pass'] else 'FAIL'}; "
         f"smoothing {'PASS' if hc['component_checks']['pass'] else 'FAIL'}; "
         f"band sim == simulate_multi {hc['band_simulator_equals_simulate_multi']})",
         f"after-tax anchors: windows {'PASS' if tax['windows_pass'] else 'FAIL'}, "
         f"clean bootstraps {'PASS' if tax_boot['pass'] else 'FAIL'}", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ P1 P2 P3 turn/yr | boot20 median, median(arm-C0) | "
                 f"OOS median dCAGR | m=1 CAGR")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            d = np.median([oos[k][arm]["cagr"] - oos[k]["C0"]["cagr"] for k in oos])
            L.append(f"  {arm:4s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} {a['turnover_annual']:5.2f} | "
                     f"{p(b['cagr_median'])} {b['diff_vs_C0_median'] * 100:+.3f}pp | {d * 100:+.3f}pp | "
                     f"{p(main_res[md]['m1'][arm]['cagr'])}")
        L.append("")
    L.append("exposure and smoothing (base, m=2): arm | mean target | % of C0's | time in market | sum|dw| total | "
             "sum|dw|/yr | trades/yr | resizes/yr | sells/yr | 2017+ sells/yr")
    for arm in ARMS:
        a = main_res["base"]["m2"][arm]
        e = a["events_full"]
        c0_mean = main_res["base"]["m2"]["C0"]["mean_target"]
        L.append(f"  {arm:4s} {a['mean_target'] * 100:6.2f}% {a['mean_target'] / c0_mean * 100:8.2f}% "
                 f"{a['time_in_market'] * 100:8.2f}% {a['sum_abs_dw']:9.2f} {a['turnover_annual']:7.3f} "
                 f"{e['trades_per_year']:7.2f} {e['resizes_per_year']:7.2f} {e['sells_per_year']:7.2f} "
                 f"{main_res['base']['m2'][arm]['events_2017_on']['sells_per_year']:7.2f}")
    L.append("")
    L.append("votes (main window, identical E2 vote in every arm): vote share | flips/yr (on / off) | 2017+ flips/yr")
    v, v17 = vstats["C0"]["full"], vstats["C0"]["since_2017"]
    L.append(f"  C0/S1-S4 {v['vote_share'] * 100:6.2f}% {v['vote_flips_per_year']:6.2f} "
             f"({v['vote_on_flips_per_year']:5.2f} / {v['vote_off_flips_per_year']:5.2f}) | "
             f"{v17['vote_flips_per_year']:6.2f}")
    L.append("")
    L.append("trades (base, m=2): arm | round trips/yr, resizes/yr, sells/yr, turnover/yr | 2017+ sells/yr, turnover/yr")
    for arm in ARMS:
        e = main_res["base"]["m2"][arm]["events_full"]
        f = main_res["base"]["m2"][arm]["events_2017_on"]
        L.append(f"  {arm:4s} {e['round_trips_per_year']:5.2f} {e['resizes_per_year']:6.2f} {e['sells_per_year']:6.2f} "
                 f"{e['turnover_per_year']:5.2f} | {f['sells_per_year']:6.2f} {f['turnover_per_year']:5.2f}")
    L.append("")
    L.append("OOS (calibrated, m=2): window | arm CAGR / maxDD")
    for k, v in oos.items():
        L.append(f"  {k:4s} {v['window']['start']}->{v['window']['end']} | " +
                 "  ".join(f"{a} {p(v[a]['cagr']).strip()}/{p(v[a]['max_dd']).strip()}" for a in ARMS))
    L.append("OOS pooled bootstrap median(arm-C0): " +
             "  ".join(f"{a} {oos_boot['pooled'][a]['diff_vs_C0_median'] * 100:+.3f}pp" for a in CANDIDATES))
    L.append("")
    L.append("gates G1-G9, gate I and the after-tax gates (" + " ".join(GATE_KEYS) + " G_I T_after_tax):")
    for arm in CANDIDATES:
        g = gates[arm]
        L.append(f"  {arm:4s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in GATE_KEYS) +
                 f" G_I={'P' if g['G_I'] else 'F'} T_after_tax={'P' if g['T_after_tax'] else 'F'}"
                 f" -> {'PASS' if g['pass_all'] else 'FAIL'}")
    L.append("")
    L.append("after tax ($100k, base financing, m=2, progressive 2026 tariff, liquidated): "
             "window | arm | pre-tax -> after-tax CAGR, max DD, realised net TRY gain, tax USD, sells/yr, sum|dw|")
    for wn in TAX_WINDOWS:
        for arm in ARMS:
            x = tax["windows"][wn][arm]
            s_ = x["progressive"]["gain_share_from_trims_vs_exits"]
            L.append(f"  {wn:22s} {arm:4s} {x['pre_tax']['cagr'] * 100:6.2f}% -> "
                     f"{x['progressive']['cagr'] * 100:6.2f}%  DD {x['progressive']['max_dd'] * 100:5.2f}%  "
                     f"gain {s_['total_net_gain_try'] / 1e6:9.2f}m TRY  "
                     f"tax {x['progressive']['tax_usd']:>11,.0f} USD  "
                     f"sells/yr {x['progressive']['sells_per_year']:5.2f}  "
                     f"sum|dw| {main_res['base']['m2'][arm]['sum_abs_dw']:8.2f}")
    L.append("")
    L.append("gain by reason (2000 window, progressive): arm | trims TRY | exits TRY | tax-sale TRY | liquid TRY | "
             "trim share | exit share")
    for arm in ARMS:
        s_ = tax["windows"]["2000_03_to_2026_09"][arm]["progressive"]["gain_share_from_trims_vs_exits"]
        g = s_["gain_try_by_reason"]
        L.append(f"  {arm:4s} {s_['trim_gain_try'] / 1e6:8.2f}m {s_['exit_gain_try'] / 1e6:8.2f}m "
                 f"{g.get('tax', 0.0) / 1e6:6.2f}m {g.get('liquidation', 0.0) / 1e6:8.2f}m "
                 f"{s_['trim_share_of_total'] * 100 if s_['trim_share_of_total'] is not None else float('nan'):6.1f}% "
                 f"{s_['exit_share_of_total'] * 100 if s_['exit_share_of_total'] is not None else float('nan'):6.1f}%")
    L.append("")
    L.append("clean after-tax bootstrap (block-20, %d paths, %s..%s, fixed FX, $100k): mode | arm | median | "
             "median delta vs C0 | share > 0 | p05 / p95" % (TAX_BOOT_PATHS, TAX_BOOT_LO, TAX_BOOT_HI))
    for mode, label in (("progressive", "B1-recent"), ("flat", "B2 flat20%")):
        for arm in ARMS:
            e = tax_boot[mode]["arms"][arm]
            L.append(f"  {label:10s} {arm:4s} {e['cagr_median'] * 100:6.2f}% "
                     f"{(e['delta_vs_C0_median_pp'] if e['delta_vs_C0_median_pp'] is not None else 0.0):+7.3f}pp "
                     f"{(e['share_delta_gt_0'] if e['share_delta_gt_0'] is not None else 0.0) * 100:6.1f}% "
                     f"{e['p05_delta_pp']:+6.2f} / {e['p95_delta_pp']:+6.2f}")
        L.append("")
    L.append("after-tax gate detail:")
    for arm in CANDIDATES:
        tg = tax_gates[arm]
        L.append(f"  {arm:4s} " + " ".join(f"{k}={'P' if v['pass'] else 'F'}"
                                           for k, v in sorted(tg.items()) if k.startswith("T_")) +
                 "  " + " ".join(f"{k}={'P' if v['pass'] else 'F'}"
                                 for k, v in sorted(tg.items()) if k.startswith("B_")) +
                 f" -> {'PASS' if tg['pass_all'] else 'FAIL'}")
    L.append("")
    L.append("gate I (21 instruments, calibrated m=2, each vs its own C0): arm | median dCAGR | share>0 | "
             "DD<=+3pp | pooled boot median | pass")
    for arm in CANDIDATES:
        g = gi[arm]
        L.append(f"  {arm:4s} {g['median_delta_cagr'] * 100:+7.3f}pp {g['share_delta_cagr_gt_0'] * 100:6.1f}% "
                 f"{g['share_delta_dd_within_3pp'] * 100:6.1f}% {g['pooled_median_delta_cagr'] * 100:+7.3f}pp "
                 f"-> {'PASS' if g['pass_all'] else 'FAIL'}")
    L.append("  excluded by quality rule: " + (", ".join(excluded) if excluded else "none"))
    L.append("")
    L.append("per-instrument dCAGR (calibrated m=2, sorted by S1): instrument | " + " | ".join(CANDIDATES))
    for k in sorted(gi["S1"]["deltas"], key=lambda z: -gi["S1"]["deltas"][z]):
        L.append(f"  {k:4s} " + "  ".join(f"{a} {gi[a]['deltas'][k] * 100:+7.3f}pp (dDD "
                                          f"{gi[a]['dd_diffs'][k] * 100:+6.2f}pp)" for a in CANDIDATES))
    L.append("")
    L.append("early-path episodes (base, m=2, R2's own early episodes; arm vs C0 over each window): "
             "arm | early share | episodes | sum delta | median delta")
    for arm in CANDIDATES:
        a = main_res["base"]["m2"][arm]
        eps = a["early_episodes"]
        d = np.array([e["delta"] for e in eps]) if eps else np.array([0.0])
        L.append(f"  {arm:4s} {a['early_time_in_market'] * 100:6.2f}% {len(eps):4d} {d.sum() * 100:+8.2f}pp "
                 f"{np.median(d) * 100:+8.2f}pp")
    L.append("")
    L.append("audit (per arm): look-ahead, trailing-window recompute, fill timing, cost linearity, invariants")
    for arm in ARMS:
        x = aud[arm]
        L.append(f"  {arm:4s} {x['truncation_probe']['pass']} {x['trailing_window_recompute']['pass']} "
                 f"{x['fill_timing']['pass']} {x['cost_linearity']['pass']} {all(x['invariants'].values())} "
                 f"-> {'PASS' if x['pass'] else 'FAIL'}")
    L.append("")
    L.append(f"eligible {eligible}; selected: {sel}")
    ref = GAIN_BAND_V2_A1_REFERENCE
    L.append("")
    L.append("reference only, not re-run here - gain_band_v2's A1 vs R2 (docs/research/gain_band_v2.md): "
             f"pre-tax CAGR {ref['pre_tax_main_m2_base_cagr'] * 100:.2f}% base / "
             f"{ref['pre_tax_main_m2_cal_cagr'] * 100:.2f}% cal, max DD {ref['pre_tax_max_dd_base'] * 100:.2f}%, "
             f"turnover {ref['turnover_annual']:.2f}, sells/yr {ref['sells_per_year_main']:.1f}, "
             f"E1 boot median {ref['boot_e1_base_median_delta_pp']:+.4f} pp base / "
             f"{ref['boot_e1_cal_median_delta_pp']:+.4f} pp cal (G5 FAIL), after tax $100k "
             + " / ".join(f"{v:+.3f} pp" for v in ref["after_tax_100k_delta_pp"].values())
             + f", B1 {ref['b1_recent_median_delta_pp']:+.3f} pp ({ref['b1_recent_share_gt_0'] * 100:.1f}%), "
             f"B2 {ref['b2_flat_median_delta_pp']:+.3f} pp ({ref['b2_flat_share_gt_0'] * 100:.1f}%), "
             f"21-ETF dDD within +3 pp {ref['instruments_share_dd_within_3pp'] * 100:.0f}% of "
             f"{ref['instruments_n']}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    if out["verdict"] != "CANDIDATE":
        sys.exit(0)  # a clean FAIL is a result; the caller reads the verdict from RESULTS.json


if __name__ == "__main__":
    main()
