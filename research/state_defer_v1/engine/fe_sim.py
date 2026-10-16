"""fe_sim -- the pre-tax/after-tax daily simulation as numba kernels over numpy arrays.

`sim_one` is a line-for-line port of `research/band_h2_combo_v1/screen.py:engine_run`: the
production R2 execution layer (next-open fill, the 0.5 pp band, the A1u gain-scaled downsize
band, the H2 December deferral) driving the Turkish after-tax account of `fe_tax`. One kernel
serves the pre-tax, progressive and flat modes because the Python engine does: the account always
runs, `mode` only decides whether `assess` charges anything.

The path-dependent part is here; the signal/target computation stays in numpy (`fe_signals`).
Everything is float64 with no reassociation and no fastmath, so the NAV path is bit-identical to
the Python engine (parity.py asserts <= 1e-9 relative and in fact gets exact equality).

State that the Python engine keeps in Python objects (the FIFO lot list, the per-year tax dicts,
`defaultdict` reason counters) lives here in flat arrays:

  * lot book            four parallel arrays + a head/count pair (`lh`); FIFO is a pointer walk,
                        a popped lot is `head += 1`, and the book resets when the Python engine
                        would hand back `[]`.
  * per-year tax state  dense arrays indexed by `year - Y0`; a year is "present in the dict"
                        iff `has_due[k] == 1`, which is what `tax_assessed_try_by_year` and
                        `outstanding()` iterate (dict insertion order == increasing year, see
                        README "dict iteration order").
  * months              `year * 12 + month - 1`; `upm[m]` is the YI-UFE index of the month before
                        `m`, the only lookup the indexation rule performs.
"""

from __future__ import annotations

from datetime import date as _date

import numpy as np
from numba import njit

from . import fe_tax
from .fe_config import BAND
from .fe_tax import (
    MODE_FLAT,
    MODE_PRE_TAX,
    MODE_PROGRESSIVE,
    MODES,
    N_REASONS,
    R_LIQUIDATION,
    R_RESIZE,
    R_TAX,
    REASON_NAMES,
)

# production execution constants (research/band_h2_combo_v1/screen.py lines 137-165)
C0_BAND = BAND
GAIN_TIERS_HI = np.array([0.01, 0.05, np.inf], dtype=np.float64)
GAIN_TIERS_B = np.array([0.005, 0.03, 0.08], dtype=np.float64)
DEC_TAIL = 10
DEFAULT_M = 2
COST_PER_SIDE = 4.0 / 10_000
YEAR_END = "12"

# ----------------------------------------------------------------------------- the K fork
# This copy adds ONE kernel option on top of `research/fast_engine/fe_sim.py`, for the
# state-keyed realization-deferral study (`research/state_defer_v1`). With `dfr_trig == 0` the
# option is off and every original number is bit-identical to the shared engine (the study's
# `--harness` asserts it cell by cell on the returned dicts).
#
# A *deferral* suppresses a discretionary downward resize (`tgt < w`, `tgt > 0`) that the
# ordinary rule would otherwise fill, exactly like H2's December window does; the ordinary rule
# is re-evaluated at every later close. The option generalizes *when* it fires and *what state*
# has to hold:
#   `dfr_trig`  0 none | 1 the session is in the last `MTAIL_TAIL` sessions of its calendar
#                    month (all twelve months) | 2 the session is in the last `DEC_WIDE`
#                    December sessions (H2's trigger with a wider window) | 3 always
#   `dfr_state` 0 none | 1 the FIFO walk of the sale nets a positive *indexed-TRY* gain |
#                    2 the estimated tax (the fill year's marginal bracket rate x that indexed
#                    gain) exceeds `DFR_TAX_FRAC` x NAV
#   `dfr_max`   hard cap on the length of one postponement episode, in sessions (0 = uncapped)
#   `dfr_drift` hard drift cap: never defer while `held - target` exceeds it (0 = uncapped)
#   `dfr_ufe_lag` 0 = the engine's YI-UFE convention everywhere; 1 = use M-2 for the first three
#                    sessions of a month (the state's own publication-delay sensitivity)
DFR_TRIG_NONE, DFR_TRIG_MONTH, DFR_TRIG_DEC, DFR_TRIG_ALWAYS = 0, 1, 2, 3
DFR_STATE_NONE, DFR_STATE_GAINPOS, DFR_STATE_TAX = 0, 1, 2
DFR_TAX_FRAC = 0.0005
DFR_MAX_SESS = 21
DFR_DRIFT_CAP = 0.10
MTAIL_TAIL = 10
DEC_WIDE = 20
DFR_EARLY_SESS = 3

_RULE_OFF = {"dfr_trig": DFR_TRIG_NONE, "dfr_state": DFR_STATE_NONE, "dfr_max": 0, "dfr_drift": 0.0, "dfr_ufe_lag": 0}

# the arm rules (screen.py:150). `usd_rule` selects A1u's USD gain fraction; `rule_index`
# (engine_run's argument) only ever reaches the dead `rule_sale` band branch, see README.
# C0/A1u/H2/K are unchanged, byte for byte, from `research/fast_engine/fe_sim.py`.
RULE = {
    "C0": {"band": False, "usd_rule": False, "defer": False, **_RULE_OFF},
    "A1u": {"band": True, "usd_rule": True, "defer": False, **_RULE_OFF},
    "H2": {"band": False, "usd_rule": False, "defer": True, **_RULE_OFF},
    "K": {"band": True, "usd_rule": True, "defer": True, **_RULE_OFF},
    # ---- the study's arms (all keep A1u and the 0.5 pp band; only the deferral device changes)
    "D1": {
        "band": True, "usd_rule": True, "defer": False,
        "dfr_trig": DFR_TRIG_MONTH, "dfr_state": DFR_STATE_GAINPOS,
        "dfr_max": DFR_MAX_SESS, "dfr_drift": 0.0, "dfr_ufe_lag": 0,
    },
    "D2": {
        "band": True, "usd_rule": True, "defer": False,
        "dfr_trig": DFR_TRIG_MONTH, "dfr_state": DFR_STATE_GAINPOS,
        "dfr_max": DFR_MAX_SESS, "dfr_drift": DFR_DRIFT_CAP, "dfr_ufe_lag": 0,
    },
    "D3": {
        "band": True, "usd_rule": True, "defer": False,
        "dfr_trig": DFR_TRIG_ALWAYS, "dfr_state": DFR_STATE_TAX,
        "dfr_max": DFR_MAX_SESS, "dfr_drift": DFR_DRIFT_CAP, "dfr_ufe_lag": 0,
    },
    "D4": {
        "band": True, "usd_rule": True, "defer": False,
        "dfr_trig": DFR_TRIG_DEC, "dfr_state": DFR_STATE_NONE,
        "dfr_max": 0, "dfr_drift": DFR_DRIFT_CAP, "dfr_ufe_lag": 0,
    },
}
ARMS = ("C0", "A1u", "H2", "K", "D1", "D2", "D3", "D4")
STUDY_ARMS = ("K", "D1", "D2", "D3", "D4")  # this study's control + candidates
REFERENCE_ARMS = ("C0",)  # R2, priced as a reference row
CONTROL = "K"
BASELINE = "K"

# `st` slots (float64 scratch; the account's scalar state)
S_CASH, S_UNITS, S_FEES, S_IDXREL, S_TPUSD, S_LOSSCF, S_DEFGAIN, S_DEFGAIN_EP, S_DEFEP = range(9)
N_ST = 9
# `cnt` slots (int64 counters)
C_TRADES, C_QSELLS, C_TAXSALES, C_POSTP, C_BANDBLK, C_DEFER = range(6)
N_CNT = 6
# `scal` slots (float64 outputs of the kernel)
(
    SC_TRADES,
    SC_QSELLS,
    SC_TAXSALES,
    SC_POSTP,
    SC_BANDBLK,
    SC_DEFER,
    SC_TURNOVER,
    SC_FEES,
    SC_IDXREL,
    SC_TPUSD,
    SC_CASH_END,
    SC_UNITS_END,
    SC_LIQ_TAX,
    SC_LIQ,
    SC_MTM,
    SC_DEFGAIN,
    SC_DEFGAIN_EP,
    SC_MAXDRIFT,
    SC_DEFEP,
    SC_MAXEP,
    SC_MEANW,
    SC_MEANGAP,
) = range(22)
N_SCAL = 22
# per-year output rows
YR_GAIN, YR_DUE, YR_PAID, YR_POST, YR_DEFER, N_YR = range(6)
# `dfr_stats` columns (one row per deferred fill, in order; row 4 flags a used row)
DF_DRIFT, DF_GAIN, DF_EPLEN, DF_SESS, DF_FLAG = range(5)


# ----------------------------------------------------------------------------- stats ports
def max_dd(nav):
    """`research/trend_robustness_v2/screen.py:max_dd`, one path."""
    x = np.atleast_2d(np.asarray(nav, dtype=float))
    return 1 - (x / np.maximum.accumulate(x, axis=1)).min(axis=1)


def calendar_years(d0, d1):
    """`trend_robustness_v2.calendar_years`."""
    return (_date.fromisoformat(d1) - _date.fromisoformat(d0)).days / 365.25


def cagr(first, last, years):
    """`trend_robustness_v2.cagr`."""
    return (last / first) ** (1 / years) - 1


# ----------------------------------------------------------------------------- kernels
@njit(cache=True, nogil=True, inline="always")
def gain_band(g_frac):
    """`screen.gain_band`: B_down(g_frac) = 0.5 pp <= 1 %, 3 pp <= 5 %, 8 pp above."""
    for k in range(3):
        if g_frac <= GAIN_TIERS_HI[k]:
            return GAIN_TIERS_B[k]
    return GAIN_TIERS_B[2]


@njit(cache=True, nogil=True, inline="always")
def marginal_rate(base_try, scale):
    """The 2026 tariff's marginal bracket rate at a TRY base (the year's YTD realised gain).

    The bracket ceilings are the engine's own (`fe_tax.tariff_tax`), scaled by the same YI-UFE
    deflator. A base at or below zero sits in the first bracket, like any positive income does.
    """
    for k in range(5):
        hi = fe_tax.TARIFF_HI[k]
        if np.isinf(hi) or base_try <= hi * scale:
            return fe_tax.TARIFF_RATE[k]
    return fe_tax.TARIFF_RATE[4]


@njit(cache=True, nogil=True, inline="always")
def _buy(st, lu, lp, lf, lm, lh, fx, mth_of, i, price, usd, fee):
    """`BaseAccount.buy` (tax_engine.py:147)."""
    if usd <= 0:
        return
    u = usd / price
    st[S_CASH] = st[S_CASH] - (usd + fee)
    st[S_FEES] = st[S_FEES] + fee
    st[S_UNITS] = st[S_UNITS] + u
    tail = lh[1]
    lu[tail] = u
    lp[tail] = (usd + fee) / u
    lf[tail] = fx[i]
    lm[tail] = mth_of[i]
    lh[1] = tail + 1


@njit(cache=True, nogil=True, inline="always")
def _sell(
    st,
    gbr,
    gbr_n,
    year_gain,
    lu,
    lp,
    lf,
    lm,
    lh,
    fx,
    upm,
    mth_of,
    M_lo,
    i,
    price,
    units_in,
    fee,
    reason,
    index_on,
    yr_of,
    Y0,
):
    """`BaseAccount.sell` (tax_engine.py:161). Returns the realised gain in TRY."""
    u = units_in
    avail = st[S_UNITS]
    if u > avail:
        u = avail
    if u <= 0:
        return 0.0
    gross = u * price
    st[S_CASH] = st[S_CASH] + (gross - fee)
    st[S_FEES] = st[S_FEES] + fee
    fx_s = fx[i]
    m_sale = mth_of[i]
    per_unit = (gross - fee) / u
    left = u
    gain = 0.0
    head = lh[0]
    tail = lh[1]
    n = tail - head
    while left > 0 and n > 0:
        take = left
        if lu[head] < take:
            take = lu[head]
        proceeds = take * per_unit * fx_s
        cost = take * lp[head] * lf[head]
        g = proceeds - cost
        if index_on and g > 0:
            # `upm` is pre-shifted: upm[m - M_lo] is ufe[prev_month(m)], the only month the
            # indexation rule asks for, so the sale month and the lot month index it directly.
            rise = upm[m_sale - M_lo] / upm[lm[head] - M_lo] - 1
            if rise >= fe_tax.INDEX_MIN_RISE:
                gi = proceeds - cost * (1 + rise)
                if gi < 0:
                    gi = 0.0
                st[S_IDXREL] = st[S_IDXREL] + (g - gi)
                g = gi
        gain += g
        lu[head] = lu[head] - take
        left -= take
        if lu[head] <= take * 1e-12:
            head += 1
            n -= 1
    st[S_UNITS] = st[S_UNITS] - u
    lh[0] = head
    if st[S_UNITS] <= 1e-12 * max(1.0, u) or n == 0:
        st[S_UNITS] = 0.0
        lh[0] = 0
        lh[1] = 0
    year_gain[yr_of[i] - Y0] += gain
    gbr[reason] += gain
    gbr_n[reason] += 1
    return gain


@njit(cache=True, nogil=True)
def _rule_sale(lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i, price, units_in, fee, index_on):
    """`screen.rule_sale`: read-only FIFO walk, (net rule gain, proceeds) in TRY."""
    if units_in <= 0 or price <= 0 or lh[1] == lh[0]:
        return 0.0, 0.0
    per_unit = (units_in * price - fee) / units_in
    fx_s = fx[i]
    proceeds = units_in * per_unit * fx_s
    m_sale = mth_of[i]
    left = units_in
    gain = 0.0
    k = 0
    head = lh[0]
    n = lh[1] - lh[0]
    while left > 1e-15 and k < n:
        take = left
        if lu[head + k] < take:
            take = lu[head + k]
        p = take * per_unit * fx_s
        cost = take * lp[head + k] * lf[head + k]
        g = p - cost
        if index_on and g > 0:
            rise = upm[m_sale - M_lo] / upm[lm[head + k] - M_lo] - 1
            if rise >= fe_tax.INDEX_MIN_RISE:
                gi = p - cost * (1 + rise)
                if gi < 0:
                    gi = 0.0
                g = gi
        gain += g
        left -= take
        k += 1
    return gain, proceeds


@njit(cache=True, nogil=True)
def _rule_sale_usd(lu, lp, lh, i, price, units_in, fee):
    """`screen.rule_sale_usd`: read-only USD FIFO walk (the A1u decision state)."""
    if units_in <= 0 or price <= 0 or lh[1] == lh[0]:
        return 0.0, 0.0
    per_unit = (units_in * price - fee) / units_in
    proceeds = units_in * per_unit
    left = units_in
    gain = 0.0
    k = 0
    head = lh[0]
    n = lh[1] - lh[0]
    while left > 1e-15 and k < n:
        take = left
        if lu[head + k] < take:
            take = lu[head + k]
        gain += take * per_unit - take * lp[head + k]
        left -= take
        k += 1
    return gain, proceeds


@njit(cache=True, nogil=True)
def _assess(year, mode, year_gain, year_div, year_wht, scale_y, st, Y0):
    """`BaseAccount.assess` / `FlatAccount.assess`. Mutates `loss_cf` in flat mode."""
    k = year - Y0
    if mode == MODE_PRE_TAX:
        return 0.0
    if mode == MODE_FLAT:
        net = year_gain[k] + st[S_LOSSCF]
        if net > 0:
            tax = fe_tax.FLAT_RATE * net
            st[S_LOSSCF] = 0.0
        else:
            tax = 0.0
            st[S_LOSSCF] = net
        if tax < 0:
            tax = 0.0
        return tax
    g = year_gain[k]
    if g < 0:
        g = 0.0
    d = year_div[k]
    sc = scale_y[k]
    if d <= fe_tax.DIV_THRESHOLD_2026 * sc:
        d = 0.0
    base = g + d
    tax = fe_tax.tariff_tax(base, sc)
    if d > 0 and base > 0:
        w = year_wht[k]
        sub = tax * d / base
        if w > sub:
            w = sub
        tax -= w
    if tax < 0:
        tax = 0.0
    return tax


@njit(cache=True, nogil=True)
def sim_one(
    oq,
    cq,
    wq,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    capital,
    i0,
    i1,
    cost,
    band_on,
    usd_rule,
    defer,
    mode,
    rule_index,
    Y0,
    M_lo,
    cash_rate,
    trace,
    dbg,
    mtail,
    dec_wide,
    early_m,
    upm2,
    dfr_trig,
    dfr_state,
    dfr_max,
    dfr_drift,
    dfr_ufe_lag,
    dfr_stats,
):
    """One arm's session loop, exactly `screen.engine_run`.

    Returns (nav, dec, scal, yr, gbr, gbr_n, has_due): `nav` has `i1 - i0 + 2` entries, `dec` the
    per-session decision trace (only filled when `trace`), `scal` the flat scalar block indexed by
    the SC_* constants, `yr` the N_YR x NY per-year block, `gbr`/`gbr_n` the per-reason gain and
    count accumulators and `has_due` the "year present in the tax_due dict" flags.

    `cash_rate` is an optional per-session yield on the idle cash (an empty array means 0, which
    is what every published study runs with -- see README "cash yield").

    `dbg` is an (nsess, 6) scratch buffer for bisecting a divergence against the Python engine:
    when it has more than one row it receives cash, units, lot head, lot count, drift and du per
    session. Pass a zero-row array to switch it off.

    `mtail`/`dec_wide`/`early_m`/`upm2` and the `dfr_*` scalars belong to this fork's deferral
    option (see the header); `dfr_stats` is an optional (nsess, 5) per-event record of a deferred
    fill -- [held-target weight, the sale's indexed-TRY rule gain, the episode length in sessions,
    the session index, 1.0] -- and is only filled when it has more than one row.
    """
    dbg_on = dbg.shape[0] > 1
    dfr_on = dfr_trig != DFR_TRIG_NONE
    stats_on = dfr_stats.shape[0] > 1
    dfr_events = 0
    ep_on = False
    ep_start = 0
    nsess = i1 - i0 + 1
    ny = yr_of[i1] - Y0 + 1
    nav = np.empty(nsess + 1, dtype=np.float64)
    nav[0] = capital
    dec = np.zeros(nsess, dtype=np.int8)
    scal = np.zeros(N_SCAL, dtype=np.float64)
    cnt = np.zeros(N_CNT, dtype=np.int64)
    yr = np.zeros((N_YR, ny), dtype=np.float64)
    gbr = np.zeros(N_REASONS, dtype=np.float64)
    gbr_n = np.zeros(N_REASONS, dtype=np.int64)
    has_due = np.zeros(ny, dtype=np.int8)
    st = np.zeros(N_ST, dtype=np.float64)
    st[S_CASH] = capital
    maxlots = nsess + 4
    lu = np.zeros(maxlots, dtype=np.float64)
    lp = np.zeros(maxlots, dtype=np.float64)
    lf = np.zeros(maxlots, dtype=np.float64)
    lm = np.zeros(maxlots, dtype=np.int64)
    lh = np.zeros(2, dtype=np.int64)  # [first live lot index, next free slot]

    gain_row = yr[YR_GAIN]
    div_row = np.zeros(ny, dtype=np.float64)  # dividends are not modelled: always zero
    wht_row = np.zeros(ny, dtype=np.float64)  # no withholding: always zero
    index_on = mode == MODE_PROGRESSIVE  # `rg.kind != "none"`
    first = True
    prev_tgt = 0.0

    has_cash_rate = cash_rate.shape[0] > 0
    for i in range(i0, i1 + 1):
        yi = yr_of[i]
        # ---- optional idle-cash yield, accrued before the fill and before any tax payment
        if has_cash_rate:
            c0 = st[S_CASH]
            if c0 > 0:
                st[S_CASH] = c0 + c0 * cash_rate[i]
        # ---- year boundary: assess the previous year (screen.py:598)
        if i > i0 and yi != yr_of[i - 1]:
            prevy = yi - 1
            tax = _assess(prevy, mode, gain_row, div_row, wht_row, scale_y, st, Y0)
            yr[YR_DUE, prevy - Y0] = tax
            has_due[prevy - Y0] = 1
            if mode == MODE_FLAT:  # B2 pays immediately, no instalments
                due = yr[YR_DUE, prevy - Y0]
                if due > 1e-9:
                    usd = due / fx[i]
                    if st[S_CASH] < usd:
                        need = usd - st[S_CASH]
                        p = cq[i - 1]
                        if st[S_UNITS] > 1e-12:
                            _sell(
                                st,
                                gbr,
                                gbr_n,
                                gain_row,
                                lu,
                                lp,
                                lf,
                                lm,
                                lh,
                                fx,
                                upm,
                                mth_of,
                                M_lo,
                                i - 1,
                                p,
                                need / p,
                                need * cost,
                                R_TAX,
                                index_on,
                                yr_of,
                                Y0,
                            )
                            cnt[C_TAXSALES] += 1
                    st[S_CASH] = st[S_CASH] - usd
                    yr[YR_PAID, prevy - Y0] = yr[YR_PAID, prevy - Y0] + due
                    st[S_TPUSD] = st[S_TPUSD] + usd
        # ---- tax instalments (funded by selling); flat mode has none (screen.py:624)
        if mode != MODE_FLAT:
            for j in range(pay_ptr[i], pay_ptr[i + 1]):
                ty = pay_yr[j]
                ti = ty - Y0
                if has_due[ti] == 0:
                    yr[YR_DUE, ti] = 0.0
                    has_due[ti] = 1
                due = yr[YR_DUE, ti]
                if pay_part[j] == 0:
                    amt = due / 2
                else:
                    amt = due - yr[YR_PAID, ti]
                if amt <= 1e-9:
                    continue
                usd = amt / fx[i]
                if st[S_CASH] < usd:
                    need = (usd - st[S_CASH]) / (1 - cost)
                    if st[S_UNITS] > 1e-12:
                        _sell(
                            st,
                            gbr,
                            gbr_n,
                            gain_row,
                            lu,
                            lp,
                            lf,
                            lm,
                            lh,
                            fx,
                            upm,
                            mth_of,
                            M_lo,
                            i,
                            oq[i],
                            need / oq[i],
                            need * cost,
                            R_TAX,
                            index_on,
                            yr_of,
                            Y0,
                        )
                        cnt[C_TAXSALES] += 1
                st[S_CASH] = st[S_CASH] - usd
                yr[YR_PAID, ti] = yr[YR_PAID, ti] + amt
                st[S_TPUSD] = st[S_TPUSD] + usd

        po = oq[i]
        pc = cq[i]
        held = st[S_UNITS] * po
        nav_open = st[S_CASH] + held
        if nav_open <= 0:
            nav_open = st[S_CASH]
        if nav_open > 0:
            w = (st[S_UNITS] * po) / nav_open
        else:
            w = 0.0
        if i > 0:
            tgt = wq[i - 1]
        else:
            tgt = wq[0]
        if tgt != tgt:
            tgt = 0.0
        trade = abs(tgt - w) > C0_BAND
        if st[S_UNITS] == 0 and tgt > 1e-12 and first:
            trade = True
        if st[S_UNITS] > 1e-12 and tgt <= 0 and (not first) and prev_tgt > 0:
            trade = True
        if dfr_on and w - tgt > scal[SC_MAXDRIFT]:
            scal[SC_MAXDRIFT] = w - tgt
        scal[SC_MEANW] += w
        scal[SC_MEANGAP] += abs(tgt - w)

        if trade:
            drift = abs(tgt - w)
        else:
            drift = 0.0
        fee_total = nav_open * cost * drift
        nav_after = nav_open - fee_total
        if po > 0:
            target_units = tgt * nav_after / po
        else:
            target_units = 0.0
        du = target_units - st[S_UNITS]

        # ---- the arm: the state-keyed deferral (this fork's option) first, then H2's December
        #      window, then A1u's gain-scaled band, on a discretionary downward resize
        blocked = 0
        if trade and tgt > 1e-12:
            if dfr_on and tgt < w:
                # ---- the deferral trigger
                if dfr_trig == DFR_TRIG_ALWAYS:
                    trig_ok = True
                elif dfr_trig == DFR_TRIG_MONTH:
                    trig_ok = mtail[i]
                else:
                    trig_ok = dec_wide[i]
                if trig_ok and dfr_drift > 0 and (w - tgt) > dfr_drift:
                    trig_ok = False  # the hard drift cap: hold no more than this above target
                if trig_ok:
                    if dfr_ufe_lag == 1 and early_m[i]:
                        rule_gain, rule_proceeds = _rule_sale(
                            lu, lp, lf, lm, lh, fx, upm2, mth_of, M_lo, i, po, -du, fee_total, True
                        )
                    else:
                        rule_gain, rule_proceeds = _rule_sale(
                            lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i, po, -du, fee_total, True
                        )
                    st_ok = True
                    if dfr_state == DFR_STATE_GAINPOS:
                        st_ok = rule_gain > 0.0
                    elif dfr_state == DFR_STATE_TAX:
                        g_pos = rule_gain if rule_gain > 0.0 else 0.0
                        rate = marginal_rate(gain_row[yi - Y0] + g_pos, scale_y[yi - Y0])
                        st_ok = rate * g_pos > DFR_TAX_FRAC * nav_open * fx[i]
                    if st_ok:
                        if not ep_on:
                            ep_on = True
                            ep_start = i
                            st[S_DEFEP] = st[S_DEFEP] + 1
                            st[S_DEFGAIN_EP] = st[S_DEFGAIN_EP] + rule_gain
                        if dfr_max <= 0 or (i - ep_start) < dfr_max:
                            # the shared reporting block below counts this postponement (blocked
                            # == 1), so only the fork's own accumulators are touched here
                            trade = False
                            blocked = 1
                            st[S_DEFGAIN] = st[S_DEFGAIN] + rule_gain
                            eplen = i - ep_start
                            if eplen > scal[SC_MAXEP]:
                                scal[SC_MAXEP] = eplen
                            if stats_on and dfr_events < dfr_stats.shape[0]:
                                dfr_stats[dfr_events, DF_DRIFT] = w - tgt
                                dfr_stats[dfr_events, DF_GAIN] = rule_gain
                                dfr_stats[dfr_events, DF_EPLEN] = eplen + 0.0
                                dfr_stats[dfr_events, DF_SESS] = i + 0.0
                                dfr_stats[dfr_events, DF_FLAG] = 1.0
                                dfr_events += 1
            if blocked == 0:
                ep_on = False
                if defer and dec_tail[i] and tgt < w:
                    trade = False
                    blocked = 1
                elif du < -1e-12 and band_on:
                    if usd_rule:
                        gain, proceeds = _rule_sale_usd(lu, lp, lh, i, po, -du, fee_total)
                        if proceeds > 0:
                            g_frac = max(0.0, gain) / proceeds
                        else:
                            g_frac = 0.0
                    else:
                        gain, proceeds = _rule_sale(
                            lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i, po, -du, fee_total, rule_index
                        )
                        if proceeds > 0:
                            g_frac = gain / proceeds
                        else:
                            g_frac = 0.0
                    if drift <= gain_band(g_frac):
                        trade = False
                        blocked = 2
        else:
            ep_on = False

        if trade:
            scal[SC_TURNOVER] = scal[SC_TURNOVER] + drift
            if du > 1e-12:
                _buy(st, lu, lp, lf, lm, lh, fx, mth_of, i, po, du * po, fee_total)
            elif du < -1e-12:
                _sell(
                    st,
                    gbr,
                    gbr_n,
                    gain_row,
                    lu,
                    lp,
                    lf,
                    lm,
                    lh,
                    fx,
                    upm,
                    mth_of,
                    M_lo,
                    i,
                    po,
                    -du,
                    fee_total,
                    R_RESIZE,
                    index_on,
                    yr_of,
                    Y0,
                )
                cnt[C_QSELLS] += 1
            cnt[C_TRADES] += 1
        elif blocked != 0:
            cnt[C_POSTP] += 1
            yr[YR_POST, yi - Y0] += 1
            if blocked == 2:
                cnt[C_BANDBLK] += 1
            else:
                cnt[C_DEFER] += 1
                yr[YR_DEFER, yi - Y0] += 1

        if trace:
            if not trade:
                dec[i - i0] = 0
            elif du < -1e-12:
                dec[i - i0] = 2
            else:
                dec[i - i0] = 1
        nav[i - i0 + 1] = st[S_CASH] + st[S_UNITS] * pc
        if dbg_on:
            dbg[i - i0, 0] = st[S_CASH]
            dbg[i - i0, 1] = st[S_UNITS]
            dbg[i - i0, 2] = lh[0]
            dbg[i - i0, 3] = lh[1] - lh[0]
            dbg[i - i0, 4] = drift
            dbg[i - i0, 5] = du
        first = False
        prev_tgt = tgt

    # ---- liquidation at the window end (screen.py:703)
    end_y = yr_of[i1]
    if st[S_UNITS] > 0:
        _sell(
            st,
            gbr,
            gbr_n,
            gain_row,
            lu,
            lp,
            lf,
            lm,
            lh,
            fx,
            upm,
            mth_of,
            M_lo,
            i1,
            cq[i1],
            st[S_UNITS],
            st[S_UNITS] * cq[i1] * cost,
            R_LIQUIDATION,
            index_on,
            yr_of,
            Y0,
        )
    liq_tax = _assess(end_y, mode, gain_row, div_row, wht_row, scale_y, st, Y0)
    outstanding = 0.0
    for k in range(ny):
        if has_due[k] == 1:
            outstanding += yr[YR_DUE, k] - yr[YR_PAID, k]
    liq_tax = liq_tax + outstanding
    ei = end_y - Y0
    yr[YR_DUE, ei] = yr[YR_DUE, ei] - yr[YR_PAID, ei] + liq_tax
    has_due[ei] = 1
    fx1 = fx[i1]
    if fx1 != 0:
        st[S_CASH] = st[S_CASH] - liq_tax / fx1
    else:
        st[S_CASH] = st[S_CASH] - liq_tax

    scal[SC_TRADES] = cnt[C_TRADES]
    scal[SC_QSELLS] = cnt[C_QSELLS]
    scal[SC_TAXSALES] = cnt[C_TAXSALES]
    scal[SC_POSTP] = cnt[C_POSTP]
    scal[SC_BANDBLK] = cnt[C_BANDBLK]
    scal[SC_DEFER] = cnt[C_DEFER]
    scal[SC_FEES] = st[S_FEES]
    scal[SC_IDXREL] = st[S_IDXREL]
    scal[SC_TPUSD] = st[S_TPUSD]
    scal[SC_CASH_END] = st[S_CASH]
    scal[SC_UNITS_END] = st[S_UNITS]
    scal[SC_LIQ_TAX] = liq_tax
    scal[SC_LIQ] = st[S_CASH]
    scal[SC_MTM] = nav[nsess]
    scal[SC_DEFGAIN] = st[S_DEFGAIN]
    scal[SC_DEFGAIN_EP] = st[S_DEFGAIN_EP]
    scal[SC_DEFEP] = st[S_DEFEP]
    scal[SC_MEANW] = scal[SC_MEANW] / nsess
    scal[SC_MEANGAP] = scal[SC_MEANGAP] / nsess
    return nav, dec, scal, yr, gbr, gbr_n, has_due


# ----------------------------------------------------------------------------- run environment
def december_tail(days, i0, i1, tail):
    """`screen.december_tail`, as a dense bool array (True = a last-`tail` December session)."""
    if tail <= 0:
        return np.zeros(len(days), dtype=np.bool_)
    by_year = {}
    for i in range(i0, i1 + 1):
        if days[i][5:7] == YEAR_END:
            by_year.setdefault(days[i][:4], []).append(i)
    out = np.zeros(len(days), dtype=np.bool_)
    for idxs in by_year.values():
        for k in idxs[-tail:]:
            out[k] = True
    return out


def month_tail(days, i0, i1, tail, month=None):
    """True on the last `tail` sessions of every calendar month (`month` = "MM" restricts it)."""
    if tail <= 0:
        return np.zeros(len(days), dtype=np.bool_)
    by_month = {}
    for i in range(i0, i1 + 1):
        if month is not None and days[i][5:7] != month:
            continue
        by_month.setdefault(days[i][:7], []).append(i)
    out = np.zeros(len(days), dtype=np.bool_)
    for idxs in by_month.values():
        for k in idxs[-tail:]:
            out[k] = True
    return out


def early_month(days, i0, i1, n):
    """True on the first `n` sessions of every calendar month (the YI-UFE publication delay)."""
    out = np.zeros(len(days), dtype=np.bool_)
    seen = {}
    for i in range(i0, i1 + 1):
        m = days[i][:7]
        c = seen.get(m, 0)
        if c < n:
            out[i] = True
        seen[m] = c + 1
    return out


class RunEnv:
    """Dense lookup tables for one window; every arm and mode of that window shares one instance.

    `days`/`fx`/`ufe` are exactly the `screen.engine_run` inputs (`te.Market(days, fx, ufe)`).
    `cash_rate` is the optional per-session idle-cash yield (default: none, which is what every
    published study uses); it is indexed by absolute session, like `days`.
    Every table is always built: the kernel's `mode` decides what it reads (the flat branch never
    touches the instalment or scale tables, the pre-tax branch never charges tax), so one env
    serves all three modes. Missing YI-UFE months raise, as the Python dict lookup would.

    `scale_ok` is False when the YI-UFE table has no 2025-10 anchor -- i.e. for the synthetic
    R-USD market, where the Python engine never reaches `Market.scale` either (it only runs
    pre-tax). `simulate(mode="progressive")` refuses such an env.
    """

    def __init__(self, days, fx, ufe, i0, i1, tail=DEC_TAIL, cash_rate=None):
        days = list(days)
        self.days = days
        self.i0, self.i1 = int(i0), int(i1)
        self.n = len(days)
        self.mth_of = np.array([fe_tax.month_index(d) for d in days], dtype=np.int64)
        self.yr_of = np.array([int(d[:4]) for d in days], dtype=np.int64)
        self.fx = np.ascontiguousarray(np.asarray(fx, dtype=np.float64))
        self.dec_tail = december_tail(days, i0, i1, tail)
        # this fork's deferral option: the A1-style all-month tail, H2's December window with the
        # wider `DEC_WIDE` length, the month-opening sessions and the M-2 YI-UFE table
        self.mtail = month_tail(days, i0, i1, MTAIL_TAIL)
        self.dec_wide = month_tail(days, i0, i1, DEC_WIDE, month=YEAR_END)
        self.early_m = early_month(days, i0, i1, DFR_EARLY_SESS)
        self.Y0 = int(self.yr_of[: i1 + 1].min()) - 1
        self.M_lo = int(self.mth_of[: i1 + 1].min()) - 1
        m_hi = int(self.mth_of[: i1 + 1].max())
        self.upm, self.ufe_missing = fe_tax.prev_month_table(ufe, self.M_lo, m_hi)
        self.upm2 = np.zeros_like(self.upm)
        for m in range(self.M_lo, m_hi + 1):
            ym = fe_tax.prev_month(f"{m // 12}-{m % 12 + 1:02d}")
            ym2 = fe_tax.prev_month(ym)
            self.upm2[m - self.M_lo] = ufe.get(ym2, np.nan)
        self.ufe = ufe
        years = np.arange(self.Y0, int(self.yr_of[i1]) + 1)
        try:
            self.scale_y = fe_tax.scale_table(ufe, years)
            self.scale_ok = True
        except KeyError:
            self.scale_y = np.ones(len(years), dtype=np.float64)
            self.scale_ok = False
        self.pay_ptr, self.pay_yr, self.pay_part = self._instalments()
        if cash_rate is None:
            self.cash_rate = np.zeros(0, dtype=np.float64)
        else:
            self.cash_rate = np.ascontiguousarray(np.asarray(cash_rate, dtype=np.float64))
            if self.cash_rate.shape[0] < self.n:
                raise ValueError("cash_rate must cover every session (indexed by absolute session)")

    def _instalments(self):
        sess, yrs, parts = fe_tax.instalments(self.days, self.i0, self.i1)
        ptr = np.zeros(self.n + 2, dtype=np.int64)
        for s in sess:
            ptr[s + 1] += 1
        np.cumsum(ptr, out=ptr)
        return ptr, yrs, parts

    def cost(self, m=DEFAULT_M):
        return COST_PER_SIDE * m


def simulate(
    opens,
    closes,
    wants,
    env,
    capital,
    i0,
    i1,
    arm=CONTROL,
    mode="pre_tax",
    m=DEFAULT_M,
    rule_index=True,
    trace=False,
    dbg=None,
    stats=False,
):
    """`screen.engine_run` in numba: the same dict, the same numbers.

    `arm` selects the fill rule (`RULE`); `mode` is "pre_tax" | "progressive" | "flat"; every arm
    label is driven by the target series `wants`, production R2's. `stats=True` additionally
    returns `_dfr`, this fork's per-deferral-event record (see `sim_one`).
    """
    spec = RULE["C0"] if arm in ("R2", "E2") else RULE[arm]
    mode_i = MODES.index(mode) if isinstance(mode, str) else int(mode)
    if mode_i == MODE_PROGRESSIVE and not env.scale_ok:
        raise ValueError("progressive mode needs a YI-UFE table with the 2025-10 anchor")
    oq = np.ascontiguousarray(np.asarray(opens, dtype=np.float64).ravel())
    cq = np.ascontiguousarray(np.asarray(closes, dtype=np.float64).ravel())
    wq = np.ascontiguousarray(np.asarray(wants, dtype=np.float64).ravel())
    dfr_stats = np.zeros((int(i1) - int(i0) + 1, 5), dtype=np.float64) if stats else np.zeros((0, 5), dtype=np.float64)
    nav, dec, scal, yr, gbr, gbr_n, has_due = sim_one(
        oq,
        cq,
        wq,
        env.fx,
        env.upm,
        env.mth_of,
        env.yr_of,
        env.scale_y,
        env.dec_tail,
        env.pay_ptr,
        env.pay_yr,
        env.pay_part,
        float(capital),
        int(i0),
        int(i1),
        env.cost(m),
        bool(spec["band"]),
        bool(spec["usd_rule"]),
        bool(spec["defer"]),
        mode_i,
        bool(rule_index),
        env.Y0,
        env.M_lo,
        env.cash_rate,
        bool(trace),
        np.zeros((0, 6), dtype=np.float64) if dbg is None else dbg,
        env.mtail,
        env.dec_wide,
        env.early_m,
        env.upm2,
        int(spec["dfr_trig"]),
        int(spec["dfr_state"]),
        int(spec["dfr_max"]),
        float(spec["dfr_drift"]),
        int(spec["dfr_ufe_lag"]),
        dfr_stats,
    )
    if env.ufe_missing and not (np.isfinite(nav).all() and np.isfinite(scal).all()):
        raise ValueError(f"a YI-UFE month is missing from the table: {env.ufe_missing[:5]}")
    days = env.days
    yrs = calendar_years(days[i0 - 1], days[i1])
    mtm = float(scal[SC_MTM])
    liq = float(scal[SC_LIQ])
    liq_tax = float(scal[SC_LIQ_TAX])
    fx1 = float(env.fx[i1])
    tax_paid_sum = 0.0
    for k in range(yr.shape[1]):
        tax_paid_sum += float(yr[YR_PAID, k])
    due = {int(k + env.Y0): float(yr[YR_DUE, k]) for k in range(yr.shape[1]) if has_due[k] == 1}
    post = {int(k + env.Y0): int(yr[YR_POST, k]) for k in range(yr.shape[1]) if yr[YR_POST, k]}
    defer = {int(k + env.Y0): int(yr[YR_DEFER, k]) for k in range(yr.shape[1]) if yr[YR_DEFER, k]}
    reasons = {REASON_NAMES[r]: float(gbr[r]) for r in range(N_REASONS) if gbr_n[r] > 0}
    return {
        "window": [days[i0], days[i1]],
        "years": float(yrs),
        "capital_usd": float(capital),
        "ending_nav_usd": mtm,
        "max_dd_nav_usd": float(max_dd(nav)[0]),
        "cagr_usd_liquidated": float((liq / capital) ** (1 / yrs) - 1),
        "terminal_usd_liquidated": liq,
        "cagr_usd_unliquidated": float((mtm / capital) ** (1 / yrs) - 1),
        "terminal_usd_unliquidated": mtm,
        "tax_total_try": float(tax_paid_sum + liq_tax),
        "tax_total_usd_at_payment_fx": float(scal[SC_TPUSD] + (liq_tax / fx1 if fx1 != 0 else liq_tax)),
        "tax_assessed_try_by_year": dict(sorted(due.items())),
        "strategy_trades": int(scal[SC_TRADES]),
        "qld_sells": int(scal[SC_QSELLS]),
        "tax_funding_sales": int(scal[SC_TAXSALES]),
        "sells": int(scal[SC_QSELLS] + scal[SC_TAXSALES]),
        "turnover_total": float(scal[SC_TURNOVER]),
        "turnover_annual": float(scal[SC_TURNOVER] / yrs),
        "postponements": int(scal[SC_POSTP]),
        "blocked_band": int(scal[SC_BANDBLK]),
        "deferred": int(scal[SC_DEFER]),
        "postponements_by_year": dict(sorted(post.items())),
        "deferrals_by_year": dict(sorted(defer.items())),
        "fees_usd": float(scal[SC_FEES]),
        "index_relief_try": float(scal[SC_IDXREL]),
        "realised_gain_try_by_reason": dict(sorted(reasons.items())),
        # this fork's deferral option (all zero when `dfr_trig == 0`)
        "deferral_episodes": int(scal[SC_DEFEP]),
        "max_episode_sessions": int(scal[SC_MAXEP]),
        "deferred_gain_try_events": float(scal[SC_DEFGAIN]),
        "deferred_gain_try_episodes": float(scal[SC_DEFGAIN_EP]),
        "max_hold_above_target": float(scal[SC_MAXDRIFT]),
        "mean_held_weight": float(scal[SC_MEANW]),
        "mean_abs_target_gap": float(scal[SC_MEANGAP]),
        "_nav": nav,
        "_dec": dec if trace else None,
        "_dfr": dfr_stats[dfr_stats[:, DF_FLAG] == 1.0] if stats else None,
    }
