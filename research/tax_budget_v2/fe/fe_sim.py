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

--- tax_budget_v2 fork (this copy) ---

Two execution options are added to the rule family, both off on the parent's code path:

  * `yearend` -- 0 off, 1 Y1's loss-pool fill, 2 Y2's bracket-headroom fill: on the last session
    of each calendar year fully inside the window, if the account's YTD realised net TRY gain
    passes the arm's trigger, sell-and-rebuy the matched quantity **at that close** (both legs,
    the sale consuming the FIFO queue oldest-first), capped at 25 % of NAV.
  * `realloc` -- Y3/Y4's state window: a pending non-exit downward resize whose fill session is
    one of the last 10 December or the first 10 January sessions fills only when the session's own
    tax year is the cheaper of the two adjacent years; A1u's band then applies after that test,
    exactly as in K.

With `yearend=0` and `realloc=False` every added branch is skipped and the kernel is the parent's
rule family exactly; `research/tax_budget_v2/screen.py --smoke` asserts bit-identical NAV paths
against `research.fast_engine.fe_sim`. Everything else here is the parent package at tag
`archive/research/fast-engine-v1-2026-10`; see `research/tax_budget_v2/PREREGISTRATION.md`.
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
    R_YE,
    REASON_NAMES,
    TARIFF_TOP,
)

# production execution constants (research/band_h2_combo_v1/screen.py lines 137-165)
C0_BAND = BAND
GAIN_TIERS_HI = np.array([0.01, 0.05, np.inf], dtype=np.float64)
GAIN_TIERS_B = np.array([0.005, 0.03, 0.08], dtype=np.float64)
DEC_TAIL = 10
DEFAULT_M = 2
COST_PER_SIDE = 4.0 / 10_000
YEAR_END = "12"
JAN_WINDOW = "01"

# tax_budget_v2: `yearend` options (0 = the parent engine) and Y1/Y2's pre-registered constants
YE_NONE, YE_POOL, YE_HEADROOM = 0, 1, 2
NAV_CAP_FRAC = 0.25
HEADROOM_TRIGGER_FRAC = 0.25
# a Y3/Y4 `realloc` hold is a third `blocked` kind (0 none, 1 deferred, 2 band, 3 realloc)
BLK_NONE, BLK_DEFER, BLK_BAND, BLK_REALLOC = 0, 1, 2, 3

# the arm rules (screen.py:150). `usd_rule` selects A1u's USD gain fraction; `rule_index`
# (engine_run's argument) only ever reaches the dead `rule_sale` band branch, see README.
# `yearend`/`realloc` are this study's additions: the parent engines never set them.
RULE = {
    "C0": {"band": False, "usd_rule": False, "defer": False, "yearend": YE_NONE, "realloc": False},
    "A1u": {"band": True, "usd_rule": True, "defer": False, "yearend": YE_NONE, "realloc": False},
    "H2": {"band": False, "usd_rule": False, "defer": True, "yearend": YE_NONE, "realloc": False},
    "K": {"band": True, "usd_rule": True, "defer": True, "yearend": YE_NONE, "realloc": False},
    "Y1": {"band": True, "usd_rule": True, "defer": True, "yearend": YE_POOL, "realloc": False},
    "Y2": {"band": True, "usd_rule": True, "defer": True, "yearend": YE_HEADROOM, "realloc": False},
    "Y3": {"band": True, "usd_rule": True, "defer": False, "yearend": YE_NONE, "realloc": True},
    "Y4": {"band": True, "usd_rule": True, "defer": False, "yearend": YE_POOL, "realloc": True},
}
ARMS = ("C0", "A1u", "H2", "K")
CONTROL = "C0"
BASELINE = "A1u"

# `st` slots (float64 scratch; the account's scalar state)
S_CASH, S_UNITS, S_FEES, S_IDXREL, S_TPUSD, S_LOSSCF = range(6)
N_ST = 6
# `cnt` slots (int64 counters)
C_TRADES, C_QSELLS, C_TAXSALES, C_POSTP, C_BANDBLK, C_DEFER, C_REALLOC, C_YE = range(8)
N_CNT = 8
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
    SC_REALLOC,
    SC_YE,
    SC_YE_NOTIONAL,
) = range(18)
N_SCAL = 18
# per-year output rows
YR_GAIN, YR_DUE, YR_PAID, YR_POST, YR_DEFER, N_YR = range(6)


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
def _ye_matched_units(lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i, price, cost, need, index_on):
    """Y1/Y2's `screen.gain_matched_units`: the smallest FIFO-prefix quantity whose realised
    (indexed) TRY gain reaches `need`, using `_sell`'s own per-lot arithmetic.

    The gain is linear in the quantity inside each lot, so the crossing is solved in closed form;
    if it is never reached, the whole book is returned. `need > 0` is guaranteed by both triggers.
    """
    head = lh[0]
    n = lh[1] - head
    if need <= 0.0 or price <= 0.0 or n == 0:
        return 0.0
    per_unit = price * (1.0 - cost)
    fx_s = fx[i]
    m_sale = mth_of[i]
    q = 0.0
    cum = 0.0
    for k in range(n):
        take = lu[head + k]
        proceeds = take * per_unit * fx_s
        cost_lot = take * lp[head + k] * lf[head + k]
        g = proceeds - cost_lot
        if index_on and g > 0:
            rise = upm[m_sale - M_lo] / upm[lm[head + k] - M_lo] - 1
            if rise >= fe_tax.INDEX_MIN_RISE:
                gi = proceeds - cost_lot * (1 + rise)
                if gi < 0:
                    gi = 0.0
                g = gi
        if cum + g >= need:
            if take > 0.0 and g > 0.0:
                return q + (need - cum) / (g / take)
            return q + take
        cum += g
        q += take
    return q


@njit(cache=True, nogil=True, inline="always")
def _year_cheaper(gain_row, yi, Y0, dec):
    """Y3's state test: is the session's own tax year the cheaper of the two adjacent years?

    December: the pair is (this year, next year), whose YTD is 0 by definition. January: the pair
    is (the finished previous year, this year). Ties favour this year.
    """
    cur = gain_row[yi - Y0]
    if dec:
        other = 0.0
    else:
        other = gain_row[yi - 1 - Y0]
    return cur <= other


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
    yearend,
    realloc,
    jan_head_arr,
    ye_tr,
    re_tr,
    mode,
    rule_index,
    Y0,
    M_lo,
    cash_rate,
    trace,
    dbg,
):
    """One arm's session loop, exactly `screen.engine_run`.

    Returns (nav, dec, scal, yr, gbr, gbr_n, has_due): `nav` has `i1 - i0 + 2` entries, `dec` the
    per-session decision trace (only filled when `trace`), `scal` the flat scalar block indexed by
    the SC_* constants, `yr` the N_YR x NY per-year block, `gbr`/`gbr_n` the per-reason gain and
    count accumulators and `has_due` the "year present in the tax_due dict" flags.

    `cash_rate` is an optional per-session yield on the idle cash (an empty array means 0, which
    is what every published study runs with -- see README "cash yield").

    `yearend` (0/1/2) is Y1/Y2's year-end sell-and-rebuy, `realloc` Y3/Y4's state window and
    `jan_head_arr` its first-10-January mask; with `yearend=0` and `realloc=False` the parent's
    code path is taken exactly. `ye_tr`/`re_tr` are optional (nsess, 10) / (nsess, 4) event logs,
    written only when they have rows: `ye_tr[i] = (fired, ytd_before, ytd_after, q, notional,
    want, nav_at_close, ceiling, units_before)` (`want >= units_before` marks a fill whose
    crossing the whole book could not reach), `re_tr[i] = (held, ytd_cur, ytd_other, drift)`.

    `dbg` is an (nsess, 6) scratch buffer for bisecting a divergence against the Python engine:
    when it has more than one row it receives cash, units, lot head, lot count, drift and du per
    session. Pass a zero-row array to switch it off.
    """
    dbg_on = dbg.shape[0] > 1
    ye_on = ye_tr.shape[0] > 0
    re_on = re_tr.shape[0] > 0
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

        # ---- the arm: Y3's state window (or H2's December window) first, then A1u's
        #      gain-scaled band, on a discretionary downward resize (screen.py:658)
        blocked = BLK_NONE
        state_hold = False
        if trade and tgt > 1e-12 and realloc and tgt < w:
            if dec_tail[i] or (jan_head_arr[i] == 1):
                state_hold = not _year_cheaper(gain_row, yi, Y0, dec_tail[i])
                if state_hold and re_on:
                    re_tr[i - i0, 0] = 1.0
                    re_tr[i - i0, 1] = gain_row[yi - Y0]
                    re_tr[i - i0, 2] = 0.0 if dec_tail[i] else gain_row[yi - 1 - Y0]
                    re_tr[i - i0, 3] = drift
        if trade and tgt > 1e-12:
            if state_hold:
                trade = False
                blocked = BLK_REALLOC
            elif defer and dec_tail[i] and tgt < w:
                trade = False
                blocked = BLK_DEFER
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
                    blocked = BLK_BAND

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
        elif blocked != BLK_NONE:
            cnt[C_POSTP] += 1
            yr[YR_POST, yi - Y0] += 1
            if blocked == BLK_BAND:
                cnt[C_BANDBLK] += 1
            elif blocked == BLK_REALLOC:
                cnt[C_REALLOC] += 1
            else:
                cnt[C_DEFER] += 1
                yr[YR_DEFER, yi - Y0] += 1

        # ---- Y1/Y2: the year-end sell-and-rebuy, on the last session of each calendar year
        #      fully inside the window. Both legs fill at this close (see PREREGISTRATION 4).
        if yearend != YE_NONE and i < i1 and yr_of[i + 1] != yi and st[S_UNITS] > 1e-12:
            ytd = gain_row[yi - Y0]
            ceiling = TARIFF_TOP * scale_y[yi - Y0]
            fire = False
            need = 0.0
            if yearend == YE_POOL:
                if ytd < 0.0:
                    fire = True
                    need = -ytd
            elif ytd < HEADROOM_TRIGGER_FRAC * ceiling:
                fire = True
                need = ceiling - ytd
            if fire:
                units_before = st[S_UNITS]
                nav_c = st[S_CASH] + st[S_UNITS] * pc
                q_cap = 0.0 if pc <= 0 else NAV_CAP_FRAC * nav_c / pc
                if q_cap > st[S_UNITS]:
                    q_cap = st[S_UNITS]
                want = _ye_matched_units(
                    lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i, pc, cost, need, index_on
                )
                q = want if want < q_cap else q_cap
                if q > 1e-12:
                    fee = q * pc * cost
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
                        pc,
                        q,
                        fee,
                        R_YE,
                        index_on,
                        yr_of,
                        Y0,
                    )
                    _buy(st, lu, lp, lf, lm, lh, fx, mth_of, i, pc, q * pc, fee)
                    cnt[C_YE] += 1
                    notional = q * pc
                    scal[SC_YE_NOTIONAL] = scal[SC_YE_NOTIONAL] + notional
                    if nav_c > 0:
                        scal[SC_TURNOVER] = scal[SC_TURNOVER] + 2.0 * notional / nav_c
                    if ye_on:
                        ye_tr[i - i0, 0] = 1.0
                        ye_tr[i - i0, 1] = ytd
                        ye_tr[i - i0, 2] = gain_row[yi - Y0]
                        ye_tr[i - i0, 3] = q
                        ye_tr[i - i0, 4] = notional
                        ye_tr[i - i0, 5] = want
                        ye_tr[i - i0, 6] = nav_c
                        ye_tr[i - i0, 7] = ceiling
                        ye_tr[i - i0, 8] = units_before
                        ye_tr[i - i0, 9] = st[S_UNITS]

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
    scal[SC_REALLOC] = cnt[C_REALLOC]
    scal[SC_YE] = cnt[C_YE]
    scal[SC_FEES] = st[S_FEES]
    scal[SC_IDXREL] = st[S_IDXREL]
    scal[SC_TPUSD] = st[S_TPUSD]
    scal[SC_CASH_END] = st[S_CASH]
    scal[SC_UNITS_END] = st[S_UNITS]
    scal[SC_LIQ_TAX] = liq_tax
    scal[SC_LIQ] = st[S_CASH]
    scal[SC_MTM] = nav[nsess]
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


def jan_head(days, i0, i1, tail):
    """The first `tail` January sessions of each calendar year in [i0, i1] (mirror of
    `december_tail`), as a dense bool array. Y3/Y4's state window is this OR December's tail."""
    if tail <= 0:
        return np.zeros(len(days), dtype=np.bool_)
    by_year = {}
    for i in range(i0, i1 + 1):
        if days[i][5:7] == JAN_WINDOW:
            by_year.setdefault(days[i][:4], []).append(i)
    out = np.zeros(len(days), dtype=np.bool_)
    for idxs in by_year.values():
        for k in idxs[:tail]:
            out[k] = True
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
        self.jan_head = jan_head(days, i0, i1, tail)
        self.Y0 = int(self.yr_of[: i1 + 1].min()) - 1
        self.M_lo = int(self.mth_of[: i1 + 1].min()) - 1
        m_hi = int(self.mth_of[: i1 + 1].max())
        self.upm, self.ufe_missing = fe_tax.prev_month_table(ufe, self.M_lo, m_hi)
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
):
    """`screen.engine_run` in numba: the same dict, the same numbers.

    `arm` selects the fill rule (`RULE`); `mode` is "pre_tax" | "progressive" | "flat"; every arm
    label is driven by the target series `wants`, production R2's.
    """
    spec = RULE["C0"] if arm in ("R2", "E2") else RULE[arm]
    mode_i = MODES.index(mode) if isinstance(mode, str) else int(mode)
    if mode_i == MODE_PROGRESSIVE and not env.scale_ok:
        raise ValueError("progressive mode needs a YI-UFE table with the 2025-10 anchor")
    oq = np.ascontiguousarray(np.asarray(opens, dtype=np.float64).ravel())
    cq = np.ascontiguousarray(np.asarray(closes, dtype=np.float64).ravel())
    wq = np.ascontiguousarray(np.asarray(wants, dtype=np.float64).ravel())
    empty = np.zeros((0, 0), dtype=np.float64)
    ye_tr = np.zeros((i1 - i0 + 1, 10), dtype=np.float64) if trace else empty
    re_tr = np.zeros((i1 - i0 + 1, 4), dtype=np.float64) if trace else empty
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
        int(spec["yearend"]),
        bool(spec["realloc"]),
        env.jan_head,
        ye_tr,
        re_tr,
        mode_i,
        bool(rule_index),
        env.Y0,
        env.M_lo,
        env.cash_rate,
        bool(trace),
        np.zeros((0, 6), dtype=np.float64) if dbg is None else dbg,
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
        "realloc_holds": int(scal[SC_REALLOC]),
        "yearend_round_trips": int(scal[SC_YE]),
        "yearend_notional_usd": float(scal[SC_YE_NOTIONAL]),
        "postponements_by_year": dict(sorted(post.items())),
        "deferrals_by_year": dict(sorted(defer.items())),
        "fees_usd": float(scal[SC_FEES]),
        "index_relief_try": float(scal[SC_IDXREL]),
        "realised_gain_try_by_reason": dict(sorted(reasons.items())),
        "realised_gain_try_by_year": {int(k + env.Y0): float(yr[YR_GAIN, k]) for k in range(yr.shape[1])},
        "_nav": nav,
        "_dec": dec if trace else None,
        "_ye_events": ye_tr if trace else None,
        "_re_events": re_tr if trace else None,
    }
