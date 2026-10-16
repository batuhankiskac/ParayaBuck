"""fe2_sim -- the two-leg simulation kernel: `research/fast_engine.fe_sim.sim_one` with a second
instrument in the same cash pool.

One account, one cash pool, **two** instruments (leg A and leg B), each with its own units and its
own FIFO lot book; one taxpayer, so both legs' realisations land in the same annual net TRY gain
(`year_gain`) and the Turkish after-tax engine of `fast_engine.fe_tax` is reused unchanged. Per
session, per leg: the 0.5 pp resize band, the A1u gain-scaled downsize band (on *that leg's* own
lot book, its own USD gain fraction) and the H2 December deferral; costs are charged per leg on
that leg's own traded notional; the legs share the post-cost NAV that sizes the fill.

With leg B's target weight, units and prices identically zero the kernel is **bit-identical** to
`fe_sim.sim_one` (pre-tax, progressive, flat, and the bootstrap): every operation leg A performs is
the same operation in the same order (`fe2_parity.py` asserts max |diff| = 0 on the NAV path, the
tax rows and every reported scalar).

Differences from `fe_sim.sim_one`, all deliberate:

* the units scalar lives in `un[leg]` (a 2-array) instead of `st[S_UNITS]`, so `fe_sim`'s
  `S_UNITS` slot is unused here; `st` keeps `fe_sim`'s layout so `fe_sim._assess`,
  `fe_sim._rule_sale_usd` and `fe_sim.gain_band` are called unchanged;
* the per-leg decision/execution is two passes. Pass 1 computes each leg's drift from the
  pre-cost NAV and a provisional shared cost (`nav_open * cost * sum(drift)`), exactly as the
  single-leg kernel does. Pass 2 applies the per-leg A1u/H2 rule to that leg's `du`; if a leg is
  blocked the surviving legs' cost is recomputed before execution. For one leg the two passes
  collapse to the single-leg code path (blocked -> nothing happens; otherwise the same fee, the
  same `nav_after`, the same `du`), which is why the parity is exact rather than close;
* a leg's own fill fee is `nav_open * cost * drift[leg]` (its own traded notional, as the
  single-leg kernel and `riskoff_after_tax_v1` both charge it) and only legs whose own rule fired
  are executed -- a blocked leg is *not* dragged along by the other leg's fill;
* tax-funding sales walk the legs in order A then B and sell from the first leg holding units
  (with one leg, that is the single-leg behaviour);
* the strategy counters are per leg and reported both per leg and summed.
"""

from __future__ import annotations

import numpy as np
from numba import njit

from research.fast_engine import fe_sim, fe_tax
from research.fast_engine.fe_tax import (
    MODE_FLAT,
    MODE_PROGRESSIVE,
    MODES,
    N_REASONS,
    R_LIQUIDATION,
    R_RESIZE,
    R_TAX,
    REASON_NAMES,
)

N_LEGS = 2
LEG_A, LEG_B = 0, 1
LEG_NAMES = ("A", "B")

# `st` slots: `fe_sim`'s layout, so the shared kernels keep working. S_UNITS is fe_sim's
# single-leg units scalar and is unused here (the legs' units live in `un`).
S_CASH, S_UNITS, S_FEES, S_IDXREL, S_TPUSD, S_LOSSCF = range(6)
N_ST = 6

# per-leg counters (`cnt`, int64)
C_TRADES, C_QSELLS, C_TAXSALES, C_POSTP, C_BANDBLK, C_DEFER = range(6)
N_CNT = 6

# per-leg scalar outputs (`legscal`, float64)
L_TRADES, L_QSELLS, L_TAXSALES, L_POSTP, L_BANDBLK, L_DEFER, L_TURNOVER, L_FEES, L_UNITS_END = range(9)
N_LEGSCAL = 9

# account-level scalar outputs (`scal`, float64) -- fe_sim.SC_* names, same order
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
) = range(15)
N_SCAL = 15
# per-year output rows (same as `fe_sim`)
YR_GAIN, YR_DUE, YR_PAID, YR_POST, YR_DEFER, N_YR = range(6)
# per-year per-leg postponement rows
YR2_POST, YR2_DEFER = range(2)
N_YR2 = 2


# ----------------------------------------------------------------------------- per-leg kernels
@njit(cache=True, nogil=True, inline="always")
def _buy_leg(st, un, leg, lu, lp, lf, lm, lh, fx, mth_of, i, price, usd, fee):
    """`fe_sim._buy` with the units scalar moved into `un[leg]` and the lot book rows `lu[leg]`."""
    if usd <= 0:
        return
    u = usd / price
    st[S_CASH] = st[S_CASH] - (usd + fee)
    st[S_FEES] = st[S_FEES] + fee
    un[leg] = un[leg] + u
    tail = lh[leg, 1]
    lu[leg, tail] = u
    lp[leg, tail] = (usd + fee) / u
    lf[leg, tail] = fx[i]
    lm[leg, tail] = mth_of[i]
    lh[leg, 1] = tail + 1


@njit(cache=True, nogil=True, inline="always")
def _sell_leg(
    st,
    un,
    leg,
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
    """`fe_sim._sell` for one leg of the two-leg book. Returns the realised gain in TRY."""
    u = units_in
    avail = un[leg]
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
    head = lh[leg, 0]
    tail = lh[leg, 1]
    n = tail - head
    while left > 0 and n > 0:
        take = left
        if lu[leg, head] < take:
            take = lu[leg, head]
        proceeds = take * per_unit * fx_s
        cost = take * lp[leg, head] * lf[leg, head]
        g = proceeds - cost
        if index_on and g > 0:
            rise = upm[m_sale - M_lo] / upm[lm[leg, head] - M_lo] - 1
            if rise >= fe_tax.INDEX_MIN_RISE:
                gi = proceeds - cost * (1 + rise)
                if gi < 0:
                    gi = 0.0
                st[S_IDXREL] = st[S_IDXREL] + (g - gi)
                g = gi
        gain += g
        lu[leg, head] = lu[leg, head] - take
        left -= take
        if lu[leg, head] <= take * 1e-12:
            head += 1
            n -= 1
    un[leg] = un[leg] - u
    lh[leg, 0] = head
    if un[leg] <= 1e-12 * max(1.0, u) or n == 0:
        un[leg] = 0.0
        lh[leg, 0] = 0
        lh[leg, 1] = 0
    year_gain[yr_of[i] - Y0] += gain
    gbr[leg, reason] += gain
    gbr_n[leg, reason] += 1
    return gain


# ----------------------------------------------------------------------------- the session loop
@njit(cache=True, nogil=True)
def sim2(
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
):
    """One two-leg arm's session loop: `fe_sim.sim_one` generalized to two instruments.

    `oq`/`cq` are (2, T) open/close panels (row 0 = leg A, row 1 = leg B), `wq` the (2, T) target
    weights (decide at the close of `i - 1`, fill at the open of `i`). Returns `sim_one`'s tuple
    plus the per-leg blocks: `(nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2)` with `gbr`
    now (2, N_REASONS) (realised TRY gain per leg per reason) and `yr2` the per-leg postponement
    rows. A leg whose prices and targets are all zero is inert (see the module docstring).
    """
    dbg_on = dbg.shape[0] > 1
    nsess = i1 - i0 + 1
    ny = yr_of[i1] - Y0 + 1
    nav = np.empty(nsess + 1, dtype=np.float64)
    nav[0] = capital
    dec = np.zeros(nsess, dtype=np.int8)
    scal = np.zeros(N_SCAL, dtype=np.float64)
    legscal = np.zeros((N_LEGS, N_LEGSCAL), dtype=np.float64)
    cnt = np.zeros((N_LEGS, N_CNT), dtype=np.int64)
    yr = np.zeros((N_YR, ny), dtype=np.float64)
    yr2 = np.zeros((N_YR2, ny), dtype=np.float64)
    gbr = np.zeros((N_LEGS, N_REASONS), dtype=np.float64)
    gbr_n = np.zeros((N_LEGS, N_REASONS), dtype=np.int64)
    has_due = np.zeros(ny, dtype=np.int8)
    st = np.zeros(N_ST, dtype=np.float64)
    st[S_CASH] = capital
    un = np.zeros(N_LEGS, dtype=np.float64)
    maxlots = nsess + 4
    lu = np.zeros((N_LEGS, maxlots), dtype=np.float64)
    lp = np.zeros((N_LEGS, maxlots), dtype=np.float64)
    lf = np.zeros((N_LEGS, maxlots), dtype=np.float64)
    lm = np.zeros((N_LEGS, maxlots), dtype=np.int64)
    lh = np.zeros((N_LEGS, 2), dtype=np.int64)

    gain_row = yr[YR_GAIN]
    div_row = np.zeros(ny, dtype=np.float64)  # dividends are not modelled: always zero
    wht_row = np.zeros(ny, dtype=np.float64)  # no withholding: always zero
    index_on = mode == MODE_PROGRESSIVE
    first = np.ones(N_LEGS, dtype=np.bool_)
    prev_tgt = np.zeros(N_LEGS, dtype=np.float64)
    # per-session scratch, allocated once (nothing is allocated inside the session loop)
    held = np.zeros(N_LEGS, dtype=np.float64)
    w = np.zeros(N_LEGS, dtype=np.float64)
    tgt = np.zeros(N_LEGS, dtype=np.float64)
    drift = np.zeros(N_LEGS, dtype=np.float64)
    du = np.zeros(N_LEGS, dtype=np.float64)
    fee_leg = np.zeros(N_LEGS, dtype=np.float64)
    trade = np.zeros(N_LEGS, dtype=np.bool_)
    blocked = np.zeros(N_LEGS, dtype=np.int64)

    has_cash_rate = cash_rate.shape[0] > 0
    for i in range(i0, i1 + 1):
        yi = yr_of[i]
        # ---- optional idle-cash yield, accrued before the fill and before any tax payment
        if has_cash_rate:
            c0 = st[S_CASH]
            if c0 > 0:
                st[S_CASH] = c0 + c0 * cash_rate[i]
        # ---- year boundary: assess the previous year (one taxpayer, both legs' gains pooled)
        if i > i0 and yi != yr_of[i - 1]:
            prevy = yi - 1
            tax = fe_sim._assess(prevy, mode, gain_row, div_row, wht_row, scale_y, st, Y0)
            yr[YR_DUE, prevy - Y0] = tax
            has_due[prevy - Y0] = 1
            if mode == MODE_FLAT:  # B2 pays immediately, no instalments
                due = yr[YR_DUE, prevy - Y0]
                if due > 1e-9:
                    usd = due / fx[i]
                    if st[S_CASH] < usd:
                        need = usd - st[S_CASH]
                        leg = -1
                        for l in range(N_LEGS):
                            if leg < 0 and un[l] > 1e-12:
                                leg = l
                        if leg >= 0:
                            p = cq[leg, i - 1]
                            _sell_leg(
                                st, un, leg, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm,
                                mth_of, M_lo, i - 1, p, need / p, need * cost, R_TAX, index_on,
                                yr_of, Y0,
                            )
                            cnt[leg, C_TAXSALES] += 1
                            legscal[leg, L_FEES] = legscal[leg, L_FEES] + need * cost
                    st[S_CASH] = st[S_CASH] - usd
                    yr[YR_PAID, prevy - Y0] = yr[YR_PAID, prevy - Y0] + due
                    st[S_TPUSD] = st[S_TPUSD] + usd
        # ---- tax instalments (funded by selling); flat mode has none
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
                    for l in range(N_LEGS):
                        if un[l] > 1e-12:
                            _sell_leg(
                                st, un, l, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm,
                                mth_of, M_lo, i, oq[l, i], need / oq[l, i], need * cost, R_TAX,
                                index_on, yr_of, Y0,
                            )
                            cnt[l, C_TAXSALES] += 1
                            legscal[l, L_FEES] = legscal[l, L_FEES] + need * cost
                            break
                st[S_CASH] = st[S_CASH] - usd
                yr[YR_PAID, ti] = yr[YR_PAID, ti] + amt
                st[S_TPUSD] = st[S_TPUSD] + usd

        for l in range(N_LEGS):
            held[l] = un[l] * oq[l, i]
        nav_open = st[S_CASH]
        for l in range(N_LEGS):
            nav_open = nav_open + held[l]
        if nav_open <= 0:
            nav_open = st[S_CASH]
        if nav_open > 0:
            for l in range(N_LEGS):
                w[l] = held[l] / nav_open
        for l in range(N_LEGS):
            if i > 0:
                t = wq[l, i - 1]
            else:
                t = wq[l, 0]
            if t != t:
                t = 0.0
            tgt[l] = t
            blocked[l] = 0
            drift[l] = 0.0
            trade[l] = abs(t - w[l]) > fe_sim.C0_BAND
            if un[l] == 0 and t > 1e-12 and first[l]:
                trade[l] = True
            if un[l] > 1e-12 and t <= 0 and (not first[l]) and prev_tgt[l] > 0:
                trade[l] = True
            if trade[l]:
                drift[l] = abs(t - w[l])

        # ---- pass 1: the provisional shared cost of every leg that wants to trade (the
        #      single-leg code path, with the legs' drifts summed into one fee)
        fee_total = 0.0
        for l in range(N_LEGS):
            fee_leg[l] = nav_open * cost * drift[l]
            fee_total = fee_total + fee_leg[l]
        nav_after = nav_open - fee_total
        for l in range(N_LEGS):
            if oq[l, i] > 0:
                target_units = tgt[l] * nav_after / oq[l, i]
            else:
                target_units = 0.0
            du[l] = target_units - un[l]

        # ---- pass 2: the arm's rule per leg, on that leg's own lot book
        for l in range(N_LEGS):
            if trade[l] and tgt[l] > 1e-12:
                if defer and dec_tail[i] and tgt[l] < w[l]:
                    trade[l] = False
                    blocked[l] = 1
                elif du[l] < -1e-12 and band_on:
                    if usd_rule:
                        gain, proceeds = fe_sim._rule_sale_usd(lu[l], lp[l], lh[l], i, oq[l, i],
                                                               -du[l], fee_leg[l])
                    else:
                        gain, proceeds = fe_sim._rule_sale(lu[l], lp[l], lf[l], lm[l], lh[l], fx,
                                                           upm, mth_of, M_lo, i, oq[l, i], -du[l],
                                                           fee_leg[l], rule_index)
                    if proceeds > 0:
                        g_frac = max(0.0, gain) / proceeds if usd_rule else gain / proceeds
                    else:
                        g_frac = 0.0
                    if drift[l] <= fe_sim.gain_band(g_frac):
                        trade[l] = False
                        blocked[l] = 2

        # ---- execute; the surviving legs' cost is recomputed (a no-op with one leg, where the
        #      rule check either blocks the fill entirely or leaves the provisional cost intact)
        fee_total = 0.0
        for l in range(N_LEGS):
            if trade[l]:
                fee_total = fee_total + fee_leg[l]
        nav_after = nav_open - fee_total
        for l in range(N_LEGS):
            if trade[l]:
                if oq[l, i] > 0:
                    target_units = tgt[l] * nav_after / oq[l, i]
                else:
                    target_units = 0.0
                d = target_units - un[l]
                scal[SC_TURNOVER] = scal[SC_TURNOVER] + drift[l]
                legscal[l, L_TURNOVER] = legscal[l, L_TURNOVER] + drift[l]
                if d > 1e-12:
                    _buy_leg(st, un, l, lu, lp, lf, lm, lh, fx, mth_of, i, oq[l, i], d * oq[l, i],
                             fee_leg[l])
                    legscal[l, L_FEES] = legscal[l, L_FEES] + fee_leg[l]
                elif d < -1e-12:
                    _sell_leg(
                        st, un, l, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of,
                        M_lo, i, oq[l, i], -d, fee_leg[l], R_RESIZE, index_on, yr_of, Y0,
                    )
                    legscal[l, L_FEES] = legscal[l, L_FEES] + fee_leg[l]
                    cnt[l, C_QSELLS] += 1
                cnt[l, C_TRADES] += 1
            elif blocked[l] != 0:
                cnt[l, C_POSTP] += 1
                yr[YR_POST, yi - Y0] += 1
                yr2[YR2_POST, yi - Y0] += 1
                if blocked[l] == 2:
                    cnt[l, C_BANDBLK] += 1
                else:
                    cnt[l, C_DEFER] += 1
                    yr[YR_DEFER, yi - Y0] += 1
                    yr2[YR2_DEFER, yi - Y0] += 1

        if trace:
            traded = False
            down = False
            for l in range(N_LEGS):
                if trade[l]:
                    traded = True
                    if du[l] < -1e-12:
                        down = True
            if not traded:
                dec[i - i0] = 0
            elif down:
                dec[i - i0] = 2
            else:
                dec[i - i0] = 1
        mtm = st[S_CASH]
        for l in range(N_LEGS):
            mtm = mtm + un[l] * cq[l, i]
        nav[i - i0 + 1] = mtm
        if dbg_on:
            dbg[i - i0, 0] = st[S_CASH]
            dbg[i - i0, 1] = un[LEG_A]
            dbg[i - i0, 2] = lh[LEG_A, 0]
            dbg[i - i0, 3] = lh[LEG_A, 1] - lh[LEG_A, 0]
            dbg[i - i0, 4] = drift[LEG_A]
            dbg[i - i0, 5] = du[LEG_A]
        for l in range(N_LEGS):
            first[l] = False
            prev_tgt[l] = tgt[l]

    # ---- liquidation at the window end (both legs, at the final close), then one assessment
    end_y = yr_of[i1]
    for l in range(N_LEGS):
        if un[l] > 0:
            u0 = un[l]
            fee0 = u0 * cq[l, i1] * cost
            _sell_leg(
                st, un, l, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i1,
                cq[l, i1], u0, fee0, R_LIQUIDATION, index_on, yr_of, Y0,
            )
            legscal[l, L_FEES] = legscal[l, L_FEES] + fee0
    liq_tax = fe_sim._assess(end_y, mode, gain_row, div_row, wht_row, scale_y, st, Y0)
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

    for l in range(N_LEGS):
        legscal[l, L_TRADES] = cnt[l, C_TRADES]
        legscal[l, L_QSELLS] = cnt[l, C_QSELLS]
        legscal[l, L_TAXSALES] = cnt[l, C_TAXSALES]
        legscal[l, L_POSTP] = cnt[l, C_POSTP]
        legscal[l, L_BANDBLK] = cnt[l, C_BANDBLK]
        legscal[l, L_DEFER] = cnt[l, C_DEFER]
        legscal[l, L_UNITS_END] = un[l]
        scal[SC_TRADES] += cnt[l, C_TRADES]
        scal[SC_QSELLS] += cnt[l, C_QSELLS]
        scal[SC_TAXSALES] += cnt[l, C_TAXSALES]
        scal[SC_POSTP] += cnt[l, C_POSTP]
        scal[SC_BANDBLK] += cnt[l, C_BANDBLK]
        scal[SC_DEFER] += cnt[l, C_DEFER]
    scal[SC_FEES] = st[S_FEES]
    scal[SC_IDXREL] = st[S_IDXREL]
    scal[SC_TPUSD] = st[S_TPUSD]
    scal[SC_CASH_END] = st[S_CASH]
    scal[SC_UNITS_END] = un[LEG_A] + un[LEG_B]
    scal[SC_LIQ_TAX] = liq_tax
    scal[SC_LIQ] = st[S_CASH]
    scal[SC_MTM] = nav[nsess]
    return nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2


# ----------------------------------------------------------------------------- the result dict
def _outputs(nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2, env, capital, i0, i1, trace,
             n_reasons=N_REASONS, reason_names=REASON_NAMES):
    """`simulate2`'s post-processing tail: the published result dict of one two-leg run.

    `n_reasons`/`reason_names` let a kernel with extra reasons reuse it unchanged (`fe2_opts` adds
    the netting reason); the account-level and per-leg reason dicts iterate `range(n_reasons)`
    over `reason_names`, in that order.
    """
    days = env.days
    yrs = fe_sim.calendar_years(days[i0 - 1], days[i1])
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
    reasons = {reason_names[r]: float(gbr[LEG_A, r] + gbr[LEG_B, r]) for r in range(n_reasons)
               if gbr_n[LEG_A, r] + gbr_n[LEG_B, r] > 0}
    legs = {}
    for l in range(N_LEGS):
        name = LEG_NAMES[l]
        lr = {reason_names[r]: float(gbr[l, r]) for r in range(n_reasons) if gbr_n[l, r] > 0}
        legs[name] = {
            "trades": int(legscal[l, L_TRADES]),
            "sells": int(legscal[l, L_QSELLS]),
            "tax_funding_sales": int(legscal[l, L_TAXSALES]),
            "postponements": int(legscal[l, L_POSTP]),
            "blocked_band": int(legscal[l, L_BANDBLK]),
            "deferred": int(legscal[l, L_DEFER]),
            "turnover_total": float(legscal[l, L_TURNOVER]),
            "turnover_annual": float(legscal[l, L_TURNOVER] / yrs),
            "fees_usd": float(legscal[l, L_FEES]),
            "units_end": float(legscal[l, L_UNITS_END]),
            "realised_gain_try_by_reason": dict(sorted(lr.items())),
        }
    return {
        "window": [days[i0], days[i1]],
        "years": float(yrs),
        "capital_usd": float(capital),
        "ending_nav_usd": mtm,
        "max_dd_nav_usd": float(fe_sim.max_dd(nav)[0]),
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
        "_nav": nav,
        "_dec": dec if trace else None,
        "_legs": legs,
    }

# ----------------------------------------------------------------------------- one run
def simulate2(
    opens_a,
    closes_a,
    wants_a,
    opens_b,
    closes_b,
    wants_b,
    env,
    capital,
    i0,
    i1,
    arm="C0",
    mode="pre_tax",
    m=fe_sim.DEFAULT_M,
    rule_index=True,
    trace=False,
    dbg=None,
):
    """`fe_sim.simulate` over two legs: the same dict plus the per-leg blocks under `_legs`.

    `env` is a `fe_sim.RunEnv` (leg-agnostic: the calendar, YI-UFE, instalment and December-tail
    tables are shared). `arm` names the fill rule (`fe_sim.RULE`), applied to **both** legs; mode
    and `m` are `fe_sim.simulate`'s.
    """
    spec = fe_sim.RULE["C0"] if arm in ("R2", "E2") else fe_sim.RULE[arm]
    mode_i = MODES.index(mode) if isinstance(mode, str) else int(mode)
    if mode_i == MODE_PROGRESSIVE and not env.scale_ok:
        raise ValueError("progressive mode needs a YI-UFE table with the 2025-10 anchor")
    oq = np.ascontiguousarray(np.stack([np.asarray(opens_a, dtype=np.float64).ravel(),
                                        np.asarray(opens_b, dtype=np.float64).ravel()]))
    cq = np.ascontiguousarray(np.stack([np.asarray(closes_a, dtype=np.float64).ravel(),
                                        np.asarray(closes_b, dtype=np.float64).ravel()]))
    wq = np.ascontiguousarray(np.stack([np.asarray(wants_a, dtype=np.float64).ravel(),
                                        np.asarray(wants_b, dtype=np.float64).ravel()]))
    nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2 = sim2(
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
    )
    if env.ufe_missing and not (np.isfinite(nav).all() and np.isfinite(scal).all()):
        raise ValueError(f"a YI-UFE month is missing from the table: {env.ufe_missing[:5]}")
    return _outputs(nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2, env, capital, i0, i1,
                    trace)

__all__ = [
    "LEG_A",
    "LEG_B",
    "LEG_NAMES",
    "N_LEGS",
    "N_LEGSCAL",
    "N_SCAL",
    "N_ST",
    "SC_FEES",
    "SC_LIQ",
    "SC_MTM",
    "SC_TRADES",
    "SC_TURNOVER",
    "YR_GAIN",
    "YR_PAID",
    "sim2",
    "simulate2",
]
