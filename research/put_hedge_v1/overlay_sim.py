"""overlay_sim -- a copy of `research.fast_engine.fe_sim.sim_one` with a protective-put overlay.

The only additions are exogenous cash flows and a mark-to-model asset:

  * at a roll session the option is bought at the close (premium paid from cash; if cash is
    short, QLD units are sold at that close with the production cost model), and
  * at expiry the intrinsic value is credited to cash; the position is marked to Black-Scholes
    every session for the NAV path (unrealised; the tax base uses only the realised flows).

Everything else -- the target series, the 0.5 pp band, A1u's gain-scaled downsize band, H2's
December deferral, the FIFO lot book and the Turkish tax arithmetic -- is copied verbatim from
`fe_sim`/`fe_tax` (the helpers are imported, not re-implemented).  With `ov_on = 0` the kernel
must be bit-identical to `fe_sim.sim_one`; parity asserts it on the NAV path, the trace and
every scalar.
"""

from __future__ import annotations

import datetime as _dt
import math

import numpy as np
from numba import njit

from research.fast_engine import fe_tax
from research.fast_engine.fe_config import BAND
from research.fast_engine.fe_sim import (
    C_BANDBLK,
    C_DEFER,
    C_POSTP,
    C_QSELLS,
    C_TAXSALES,
    C_TRADES,
    COST_PER_SIDE,
    DEC_TAIL,
    DEFAULT_M,
    N_CNT,
    N_ST,
    N_YR,
    RULE,
    S_CASH,
    S_FEES,
    S_IDXREL,
    S_LOSSCF,
    S_TPUSD,
    S_UNITS,
    SC_BANDBLK,
    SC_CASH_END,
    SC_DEFER,
    SC_FEES,
    SC_IDXREL,
    SC_LIQ,
    SC_LIQ_TAX,
    SC_MTM,
    SC_POSTP,
    SC_QSELLS,
    SC_TAXSALES,
    SC_TPUSD,
    SC_TRADES,
    SC_TURNOVER,
    SC_UNITS_END,
    YR_DEFER,
    YR_DUE,
    YR_GAIN,
    YR_PAID,
    YR_POST,
    _assess,
    _buy,
    _rule_sale,
    _rule_sale_usd,
    _sell,
    gain_band,
)
from research.fast_engine.fe_tax import (
    MODE_FLAT,
    MODE_PRE_TAX,
    MODE_PROGRESSIVE,
    MODES,
    R_LIQUIDATION,
    R_RESIZE,
    R_TAX,
    REASON_NAMES,
)

# appended slots, so the original indices do not move
R_OPT = 3
REASONS_OV = REASON_NAMES + ("option_funding",)
N_REASONS_OV = 4
C_OPTROLL, C_OPTFUND, C_OPTEXPIRE = N_CNT, N_CNT + 1, N_CNT + 2
N_CNT_OV = N_CNT + 3
SC_OPT_PREM, SC_OPT_PAYOFF, SC_OPT_MARK_END = 15, 16, 17
N_SCAL_OV = 18
YR_OPT = N_YR
N_YR_OV = N_YR + 1

QLD_BETA = 2.0


@njit(cache=True, nogil=True, inline="always")
def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@njit(cache=True, nogil=True, inline="always")
def bs_put(S, K, T, r, q, sig):
    """Black-Scholes European put with a continuous dividend yield."""
    if T <= 0.0:
        return K - S if K > S else 0.0
    if sig < 1e-8:
        sig = 1e-8
    if S <= 0.0 or K <= 0.0:
        return 0.0
    sq = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / sq
    d2 = d1 - sq
    return K * math.exp(-r * T) * _ncdf(-d2) - S * math.exp(-q * T) * _ncdf(-d1)


@njit(cache=True, nogil=True)
def _assess_opt(year, mode, year_gain, opt_gain, year_div, year_wht, scale_y, st, Y0, offset):
    """`_assess` plus the option book.

    Conservative rule (`offset == 0`): option losses offset only option gains within the year, so
    a negative net option P&L contributes zero.  Sensitivity (`offset == 1`): the net option P&L
    is added to the equity gain before the floor at zero.
    """
    k = year - Y0
    if mode == MODE_PRE_TAX:
        return 0.0
    if mode == MODE_FLAT:
        o = opt_gain[k]
        if offset == 0 and o < 0.0:
            o = 0.0
        net = year_gain[k] + o + st[S_LOSSCF]
        if net > 0.0:
            tax = fe_tax.FLAT_RATE * net
            st[S_LOSSCF] = 0.0
        else:
            tax = 0.0
            st[S_LOSSCF] = net
        if tax < 0.0:
            tax = 0.0
        return tax
    g = year_gain[k]
    if g < 0.0:
        g = 0.0
    o = opt_gain[k]
    if offset == 0:
        if o < 0.0:
            o = 0.0
        base = g + o
    else:
        base = g + o
        if base < 0.0:
            base = 0.0
    d = year_div[k]
    sc = scale_y[k]
    if d <= fe_tax.DIV_THRESHOLD_2026 * sc:
        d = 0.0
    base = base + d
    tax = fe_tax.tariff_tax(base, sc)
    if d > 0.0 and base > 0.0:
        w = year_wht[k]
        sub = tax * d / base
        if w > sub:
            w = sub
        tax -= w
    if tax < 0.0:
        tax = 0.0
    return tax


@njit(cache=True, nogil=True, inline="always")
def _sigma(vxn_i, S, K, skew_s):
    return vxn_i * (1.0 + skew_s * math.log(S / K))


@njit(cache=True, nogil=True)
def sim_one_ov(
    oq,
    cq,
    wq,
    uq,
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
    ov_on,
    vxn,
    rate,
    roll,
    exp_at_roll,
    tenor_at_roll,
    yfrac,
    hedge_on,
    long_otm,
    short_otm,
    sizing,
    budget_per_roll,
    opt_cost,
    div_yield,
    skew_s,
    hedge_ratio,
    opt_offset,
    flows,
):
    """One arm's session loop: the production rule plus the pre-registered option overlay."""
    dbg_on = dbg.shape[0] > 1
    nsess = i1 - i0 + 1
    ny = yr_of[i1] - Y0 + 1
    nav = np.empty(nsess + 1, dtype=np.float64)
    nav[0] = capital
    dec = np.zeros(nsess, dtype=np.int8)
    scal = np.zeros(N_SCAL_OV, dtype=np.float64)
    cnt = np.zeros(N_CNT_OV, dtype=np.int64)
    yr = np.zeros((N_YR_OV, ny), dtype=np.float64)
    gbr = np.zeros(N_REASONS_OV, dtype=np.float64)
    gbr_n = np.zeros(N_REASONS_OV, dtype=np.int64)
    has_due = np.zeros(ny, dtype=np.int8)
    st = np.zeros(N_ST, dtype=np.float64)
    st[S_CASH] = capital
    maxlots = nsess + 4
    lu = np.zeros(maxlots, dtype=np.float64)
    lp = np.zeros(maxlots, dtype=np.float64)
    lf = np.zeros(maxlots, dtype=np.float64)
    lm = np.zeros(maxlots, dtype=np.int64)
    lh = np.zeros(2, dtype=np.int64)

    gain_row = yr[YR_GAIN]
    opt_row = yr[YR_OPT]
    div_row = np.zeros(ny, dtype=np.float64)
    wht_row = np.zeros(ny, dtype=np.float64)
    index_on = mode == MODE_PROGRESSIVE
    first = True
    prev_tgt = 0.0

    opt_active = 0
    opt_ul = 0.0
    opt_us = 0.0
    opt_kl = 0.0
    opt_ks = 0.0
    opt_exp = -1
    opt_exp_y = 0.0
    flows_on = flows.shape[0] > 0

    has_cash_rate = cash_rate.shape[0] > 0
    for i in range(i0, i1 + 1):
        yi = yr_of[i]
        if has_cash_rate:
            c0 = st[S_CASH]
            if c0 > 0:
                st[S_CASH] = c0 + c0 * cash_rate[i]
        if i > i0 and yi != yr_of[i - 1]:
            prevy = yi - 1
            if ov_on:
                tax = _assess_opt(prevy, mode, gain_row, opt_row, div_row, wht_row, scale_y, st, Y0, opt_offset)
            else:
                tax = _assess(prevy, mode, gain_row, div_row, wht_row, scale_y, st, Y0)
            yr[YR_DUE, prevy - Y0] = tax
            has_due[prevy - Y0] = 1
            if mode == MODE_FLAT:
                due = yr[YR_DUE, prevy - Y0]
                if due > 1e-9:
                    usd = due / fx[i]
                    if st[S_CASH] < usd:
                        need = usd - st[S_CASH]
                        p = cq[i - 1]
                        if st[S_UNITS] > 1e-12:
                            _sell(
                                st, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i - 1, p,
                                need / p, need * cost, R_TAX, index_on, yr_of, Y0,
                            )
                            cnt[C_TAXSALES] += 1
                    st[S_CASH] = st[S_CASH] - usd
                    yr[YR_PAID, prevy - Y0] = yr[YR_PAID, prevy - Y0] + due
                    st[S_TPUSD] = st[S_TPUSD] + usd
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
                            st, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i, oq[i],
                            need / oq[i], need * cost, R_TAX, index_on, yr_of, Y0,
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
        trade = abs(tgt - w) > BAND
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

        blocked = 0
        if trade and tgt > 1e-12:
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

        if trade:
            scal[SC_TURNOVER] = scal[SC_TURNOVER] + drift
            if du > 1e-12:
                _buy(st, lu, lp, lf, lm, lh, fx, mth_of, i, po, du * po, fee_total)
            elif du < -1e-12:
                _sell(
                    st, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i, po, -du,
                    fee_total, R_RESIZE, index_on, yr_of, Y0,
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

        # ---------------------------------------------------------------- option overlay (close)
        mark = 0.0
        if ov_on:
            S = uq[i]
            # 1) expiry: cash settlement at intrinsic
            if opt_active == 1 and opt_exp >= 0 and i == opt_exp:
                pay = opt_ul * (opt_kl - S if opt_kl > S else 0.0)
                if opt_us > 0.0:
                    pay -= opt_us * (opt_ks - S if opt_ks > S else 0.0)
                st[S_CASH] = st[S_CASH] + pay
                opt_row[yi - Y0] = opt_row[yi - Y0] + pay * fx[i]
                scal[SC_OPT_PAYOFF] = scal[SC_OPT_PAYOFF] + pay
                if flows_on:
                    flows[i] = flows[i] + pay
                cnt[C_OPTEXPIRE] += 1
                opt_active = 0
                opt_ul = 0.0
                opt_us = 0.0
            # 2) roll: open a new position at the close
            if roll[i] == 1 and hedge_on[i] > 0.0:
                T = tenor_at_roll[i]
                if T > 0.0 and S > 0.0 and st[S_UNITS] > 1e-12:
                    mark_now = 0.0
                    if opt_active == 1:
                        Tn = opt_exp_y - yfrac[i]
                        if Tn < 0.0:
                            Tn = 0.0
                        mark_now = opt_ul * bs_put(
                            S, opt_kl, Tn, rate[i], div_yield, _sigma(vxn[i], S, opt_kl, skew_s)
                        )
                        if opt_us > 0.0:
                            mark_now -= opt_us * bs_put(
                                S, opt_ks, Tn, rate[i], div_yield, _sigma(vxn[i], S, opt_ks, skew_s)
                            )
                    nav_now = st[S_CASH] + st[S_UNITS] * cq[i] + mark_now
                    if nav_now > 0.0:
                        w_now = st[S_UNITS] * cq[i] / nav_now
                        if w_now > 1e-12:
                            kl = (1.0 - long_otm) * S
                            sigl = _sigma(vxn[i], S, kl, skew_s)
                            pl = bs_put(S, kl, T, rate[i], div_yield, sigl)
                            prem_l = pl * (1.0 + opt_cost)
                            if sizing == 1:
                                ul = hedge_ratio * QLD_BETA * w_now * nav_now / S
                            elif prem_l > 1e-14:
                                ul = (budget_per_roll * nav_now) / prem_l
                            else:
                                ul = 0.0
                            prem = ul * prem_l
                            us = 0.0
                            ks = 0.0
                            if short_otm > 0.0:
                                ks = (1.0 - short_otm) * S
                                sigs = _sigma(vxn[i], S, ks, skew_s)
                                ps = bs_put(S, ks, T, rate[i], div_yield, sigs)
                                us = ul
                                prem -= us * ps * (1.0 - opt_cost)
                            if prem > 1e-14 or us > 0.0:
                                if prem > st[S_CASH]:
                                    need = prem - st[S_CASH]
                                    u_sell = need / cq[i]
                                    if u_sell > st[S_UNITS]:
                                        u_sell = st[S_UNITS]
                                    fee = u_sell * cq[i] * cost
                                    _sell(
                                        st, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i,
                                        cq[i], u_sell, fee, R_OPT, index_on, yr_of, Y0,
                                    )
                                    cnt[C_OPTFUND] += 1
                                st[S_CASH] = st[S_CASH] - prem
                                opt_row[yi - Y0] = opt_row[yi - Y0] - prem * fx[i]
                                scal[SC_OPT_PREM] = scal[SC_OPT_PREM] + prem
                                if flows_on:
                                    flows[i] = flows[i] - prem
                                opt_ul = ul
                                opt_us = us
                                opt_kl = kl
                                opt_ks = ks
                                opt_exp = exp_at_roll[i]
                                opt_exp_y = yfrac[i] + T
                                opt_active = 1
                                cnt[C_OPTROLL] += 1
            # 3) daily mark for the NAV path
            if opt_active == 1:
                T = opt_exp_y - yfrac[i]
                if T < 0.0:
                    T = 0.0
                mark = opt_ul * bs_put(
                    S, opt_kl, T, rate[i], div_yield, _sigma(vxn[i], S, opt_kl, skew_s)
                )
                if opt_us > 0.0:
                    mark -= opt_us * bs_put(
                        S, opt_ks, T, rate[i], div_yield, _sigma(vxn[i], S, opt_ks, skew_s)
                    )

        if ov_on:
            nav[i - i0 + 1] = st[S_CASH] + st[S_UNITS] * pc + mark
        else:
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

    # ---- liquidation at the window end
    end_y = yr_of[i1]
    if ov_on and opt_active == 1:
        S = uq[i1]
        T = opt_exp_y - yfrac[i1]
        if T < 0.0:
            T = 0.0
        val = opt_ul * bs_put(S, opt_kl, T, rate[i1], div_yield, _sigma(vxn[i1], S, opt_kl, skew_s))
        val *= (1.0 - opt_cost)
        if opt_us > 0.0:
            val -= opt_us * bs_put(
                S, opt_ks, T, rate[i1], div_yield, _sigma(vxn[i1], S, opt_ks, skew_s)
            ) * (1.0 + opt_cost)
        st[S_CASH] = st[S_CASH] + val
        opt_row[end_y - Y0] = opt_row[end_y - Y0] + val * fx[i1]
        scal[SC_OPT_MARK_END] = val
        if flows_on:
            flows[i1] = flows[i1] + val
        opt_active = 0
    if st[S_UNITS] > 0:
        _sell(
            st, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i1, cq[i1], st[S_UNITS],
            st[S_UNITS] * cq[i1] * cost, R_LIQUIDATION, index_on, yr_of, Y0,
        )
    if ov_on:
        liq_tax = _assess_opt(end_y, mode, gain_row, opt_row, div_row, wht_row, scale_y, st, Y0, opt_offset)
    else:
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
    return nav, dec, scal, yr, gbr, gbr_n, has_due


# ----------------------------------------------------------------------------- calendar
def _third_friday(y, m):
    import datetime as dt

    d = 1
    fridays = []
    while True:
        try:
            if dt.date(y, m, d).weekday() == 4:
                fridays.append(d)
        except ValueError:
            break
        d += 1
    return dt.date(y, m, fridays[2])


def _add_months(y, m, k):
    m0 = m - 1 + k
    return y + m0 // 12, m0 % 12 + 1


def roll_schedule(days, i0, i1, months):
    """One-option-at-a-time roll calendar on the third Friday (preceding session if a holiday).

    The first roll is the first third Friday on or after `days[i0]`; the next is `months` months
    later, and so on.  `exp_at_roll[k]` is the expiry session (the next roll), or -1 when the
    expiry falls after `i1` (the position is then liquidated at the window end); `tenor_at_roll[k]`
    is the true roll-to-expiry year fraction in both cases.
    """
    n = len(days)
    roll = np.zeros(n, dtype=np.int8)
    exp_at = np.full(n, -1, dtype=np.int64)
    tenor = np.zeros(n, dtype=np.float64)
    cand = []
    y, m = int(days[i0][:4]), int(days[i0][5:7])
    y_end, m_end = int(days[i1][:4]), int(days[i1][5:7])
    while (y, m) <= (y_end, m_end):
        iso = _third_friday(y, m).isoformat()
        k = int(np.searchsorted(np.array(days), iso, side="right")) - 1
        if i0 <= k <= i1 and (not cand or k > cand[-1]):
            cand.append(k)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    sel = cand[::months]
    import datetime as dt

    for j, k in enumerate(sel):
        roll[k] = 1
        if j + 1 < len(sel):
            e = sel[j + 1]
            exp_at[k] = e
            tenor[k] = (dt.date.fromisoformat(days[e]) - dt.date.fromisoformat(days[k])).days / 365.25
        else:
            y, m = int(days[k][:4]), int(days[k][5:7])
            y2, m2 = _add_months(y, m, months)
            expiry = _third_friday(y2, m2)
            tenor[k] = (expiry - dt.date.fromisoformat(days[k])).days / 365.25
            exp_at[k] = -1
    return roll, exp_at, tenor


def year_fraction(days):
    import datetime as dt

    d0 = dt.date.fromisoformat(days[0])
    return np.array([(dt.date.fromisoformat(d) - d0).days / 365.25 for d in days], dtype=np.float64)


# ----------------------------------------------------------------------------- wrapper
def simulate(
    opens,
    closes,
    wants,
    env,
    capital,
    i0,
    i1,
    arm="K",
    mode="pre_tax",
    m=DEFAULT_M,
    rule_index=True,
    trace=False,
    dbg=None,
    overlay=None,
    underlying=None,
    flows=None,
):
    """`fe_sim.simulate` plus the overlay; the returned dict is fe_sim's plus the option block."""
    spec = RULE["K"] if arm in ("K", "R2", "E2") else RULE[arm]
    mode_i = MODES.index(mode) if isinstance(mode, str) else int(mode)
    if mode_i == MODE_PROGRESSIVE and not env.scale_ok:
        raise ValueError("progressive mode needs a YI-UFE table with the 2025-10 anchor")
    oq = np.ascontiguousarray(np.asarray(opens, dtype=np.float64).ravel())
    cq = np.ascontiguousarray(np.asarray(closes, dtype=np.float64).ravel())
    wq = np.ascontiguousarray(np.asarray(wants, dtype=np.float64).ravel())
    n = len(oq)
    if underlying is None:
        uq = cq
    else:
        uq = np.ascontiguousarray(np.asarray(underlying, dtype=np.float64).ravel())
    if flows is None:
        flows = np.zeros(0, dtype=np.float64)
    if overlay is None:
        ov_on = 0
        vxn = np.zeros(n)
        rate = np.zeros(n)
        roll = np.zeros(n, dtype=np.int8)
        exp_at = np.full(n, -1, dtype=np.int64)
        tenor = np.zeros(n)
        hedge_on = np.zeros(n)
        long_otm = short_otm = skew_s = opt_cost = div_yield = budget = hedge_ratio = 0.0
        sizing = 0
        offset = 0
    else:
        ov_on = 1
        vxn = np.ascontiguousarray(np.asarray(overlay["vxn"], dtype=np.float64))
        rate = np.ascontiguousarray(np.asarray(overlay["rate"], dtype=np.float64))
        roll = np.ascontiguousarray(np.asarray(overlay["roll"], dtype=np.int8))
        exp_at = np.ascontiguousarray(np.asarray(overlay["exp_at_roll"], dtype=np.int64))
        tenor = np.ascontiguousarray(np.asarray(overlay["tenor_at_roll"], dtype=np.float64))
        hedge_on = np.ascontiguousarray(np.asarray(overlay["hedge_on"], dtype=np.float64))
        long_otm = float(overlay["long_otm"])
        short_otm = float(overlay["short_otm"])
        sizing = int(overlay["sizing"])
        budget = float(overlay["budget_per_roll"])
        opt_cost = float(overlay["opt_cost"])
        div_yield = float(overlay["div_yield"])
        skew_s = float(overlay["skew_s"])
        hedge_ratio = float(overlay["hedge_ratio"])
        offset = int(overlay.get("offset", 0))
    yfrac = year_fraction(env.days)
    nav, dec, scal, yr, gbr, gbr_n, has_due = sim_one_ov(
        oq,
        cq,
        wq,
        uq,
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
        ov_on,
        vxn,
        rate,
        roll,
        exp_at,
        tenor,
        yfrac,
        hedge_on,
        long_otm,
        short_otm,
        sizing,
        budget,
        opt_cost,
        div_yield,
        skew_s,
        hedge_ratio,
        offset,
        np.ascontiguousarray(flows, dtype=np.float64),
    )
    days = env.days
    yrs = (_dt.date.fromisoformat(days[i1]) - _dt.date.fromisoformat(days[i0 - 1])).days / 365.25
    mtm = float(scal[SC_MTM])
    liq = float(scal[SC_LIQ])
    liq_tax = float(scal[SC_LIQ_TAX])
    fx1 = float(env.fx[i1])
    tax_paid_sum = 0.0
    for k in range(yr.shape[1]):
        tax_paid_sum += float(yr[YR_PAID, k])
    due = {int(k + env.Y0): float(yr[YR_DUE, k]) for k in range(yr.shape[1]) if has_due[k] == 1}
    post = {int(k + env.Y0): int(yr[YR_POST, k]) for k in range(yr.shape[1]) if yr[YR_POST, k]}
    defer_d = {int(k + env.Y0): int(yr[YR_DEFER, k]) for k in range(yr.shape[1]) if yr[YR_DEFER, k]}
    reasons = {REASONS_OV[r]: float(gbr[r]) for r in range(N_REASONS_OV) if gbr_n[r] > 0}
    return {
        "window": [days[i0], days[i1]],
        "years": float(yrs),
        "capital_usd": float(capital),
        "ending_nav_usd": mtm,
        "max_dd_nav_usd": float(1 - (nav / np.maximum.accumulate(nav)).min()),
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
        "deferrals_by_year": dict(sorted(defer_d.items())),
        "fees_usd": float(scal[SC_FEES]),
        "index_relief_try": float(scal[SC_IDXREL]),
        "realised_gain_try_by_reason": dict(sorted(reasons.items())),
        "option_premium_usd": float(scal[SC_OPT_PREM]),
        "option_payoff_usd": float(scal[SC_OPT_PAYOFF]),
        "option_terminal_value_usd": float(scal[SC_OPT_MARK_END]),
        "option_taxable_try_by_year": {int(k + env.Y0): float(yr[YR_OPT, k]) for k in range(yr.shape[1]) if yr[YR_OPT, k]},
        "_nav": nav,
        "_dec": dec if trace else None,
        "_flows": flows if flows.shape[0] else None,
    }
