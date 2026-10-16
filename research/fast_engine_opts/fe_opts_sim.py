"""fe_opts_sim -- `fe_sim.sim_one` with the single-leg execution options, and its wrapper.

`sim_one_opts` is `research/fast_engine/fe_sim.py:sim_one` verbatim, with the option code inserted
at the places the mechanisms act on (each marked `# -- opt NAME`). Nothing in the production path
was re-ordered, re-associated or rewritten: every option branch is guarded by a threshold that
`fe_opts.Opts()` leaves at its production value, so **with `Opts()` the kernel is the engine** --
`parity_opts.py` asserts exact equality of the NAV path and of every returned cell (pre-tax,
progressive, flat, all four arms, four capitals) and of the bootstrap driver.

The hooks, in session order:

    1  margin interest on a negative cash balance                          (IC39)
    2  pre-funding a scheduled tax payment                                 (IC55-B)
    3  the TRY conversion cost on every conversion                         (IC55-A/C)
    4  the decision index (an early close can decline a decision)          (IC57-A)
    5  the drawdown governor (re-sizes the target from the realised NAV)   (IC49)
    6  the fill reference (open / midpoint / caller-supplied)              (IC54)
    7  the resize threshold and the A1u/H2 rule block, unchanged           (IC59)
    8  the dollar floor on a permitted downsize                            (IC52)
    9  the early-close downsize                                            (IC57-C)
   10  order slicing over the following sessions' opens                    (IC53)
   11  T+1 settlement: a same-session sale's proceeds cannot fund a buy    (IC58)
   12  the pre-funded reserve in the NAV mark                              (IC55-B)

The option *arrays* are per path and per session: `fill`, `mrate`, `early` and `wvol` are `(T,)`
(an empty array switches the mechanism off, except `fill`/`early`, which also need their mode).
The scalar options travel in the two packed vectors of `fe_opts.Opts`.
"""

from __future__ import annotations

from datetime import date as _date  # noqa: F401  (kept next to the engine's own import block)

import numpy as np
from numba import njit

from research.fast_engine.fe_config import CAP, VOL_TARGET
from research.fast_engine.fe_sim import (
    C_BANDBLK,
    C_DEFER,
    C_POSTP,
    C_QSELLS,
    C_TAXSALES,
    C_TRADES,
    N_CNT,
    N_ST,
    N_YR,
    RULE,
    RunEnv,  # noqa: F401  (re-exported for the bridge)
    S_CASH,
    S_FEES,
    S_IDXREL,
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
    SC_TRADES,
    SC_TPUSD,
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
    MODE_PROGRESSIVE,
    MODES,
    N_REASONS,
    R_LIQUIDATION,
    R_RESIZE,
    R_TAX,
    REASON_NAMES,
)

from .fe_opts import Opts

# the option counters, appended to the engine's 15 scalar slots
SC_EARLY_SKIPS, SC_EARLY_BLOCKS, SC_SETTLE_DEFERS, SC_SLICE_TRIGGERS = 15, 16, 17, 18
SC_SLICE_SESSIONS, SC_NOTIONAL_BLOCKS, SC_MARGIN_USD, SC_FXCOST_USD = 19, 20, 21, 22
SC_PREFUND_USD = 23
N_SCAL_OPTS = 24

# the `blocked` codes: the engine's two, plus the options' own
BLK_DEFER, BLK_BAND, BLK_NOTIONAL, BLK_EARLY = 1, 2, 3, 4


@njit(cache=True, nogil=True)
def sim_one_opts(
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
    optf,
    opti,
    fill,
    mrate,
    early,
    wvol,
):
    """One arm's session loop: `fe_sim.sim_one` plus the options (see the module docstring).

    Returns the engine's 7-tuple with `scal` widened to `N_SCAL_OPTS` (the engine's 15 slots plus
    the option counters 15..23).
    """
    # -- options: unpack (every default is the production value)
    BAND_UP = optf[0]
    BAND_DOWN = optf[1]
    MIN_NOTIONAL = optf[2]
    MIN_FRAC = optf[3]
    DD_START = optf[4]
    DD_FLOOR = optf[5]
    SLICE_FRAC = optf[6]
    SLICE_PARTS = optf[7]
    FX_COST = optf[8]
    PREFUND = optf[9]
    SETTLE_FLOOR = optf[10]
    DD_UPSIZE_ONLY = opti[0]
    EARLY_MODE = opti[1]
    FILL_MODE = opti[2]
    has_fill = fill.shape[0] > 0
    has_mrate = mrate.shape[0] > 0
    has_early = early.shape[0] > 0
    has_wvol = wvol.shape[0] > 0
    dd_on = DD_START > 0.0 and has_wvol
    slice_on = SLICE_PARTS >= 2.0
    settle_on = SETTLE_FLOOR >= 0.0
    fx_on = FX_COST > 0.0
    prefund_on = PREFUND >= 1.0
    early_skip = has_early and EARLY_MODE == 1
    early_block = has_early and EARLY_MODE == 2

    dbg_on = dbg.shape[0] > 1
    nsess = i1 - i0 + 1
    ny = yr_of[i1] - Y0 + 1
    nav = np.empty(nsess + 1, dtype=np.float64)
    nav[0] = capital
    dec = np.zeros(nsess, dtype=np.int8)
    scal = np.zeros(N_SCAL_OPTS, dtype=np.float64)
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
    # -- options: the mechanisms' own state
    dec_idx = i0 - 1 if i0 > 0 else 0  # the newest decision a fill may use (IC57)
    peak = capital                     # the running NAV peak of the governor (IC49)
    restry = 0.0                       # the pre-funded TRY reserve (IC55)
    sl_units = 0.0                     # the remaining units of a sliced order (IC53)
    sl_left = 0                        # ... and how many sessions of it are left
    unsettled = 0.0                    # the sale proceeds not yet spendable (IC58)

    has_cash_rate = cash_rate.shape[0] > 0
    for i in range(i0, i1 + 1):
        yi = yr_of[i]
        # -- opt (IC58): a sale's proceeds settle overnight, so nothing carried in is unsettled
        if settle_on:
            unsettled = 0.0
        # -- opt (IC39): the loan accrues interest while the cash balance is negative
        if has_mrate:
            if st[S_CASH] < 0.0:
                pay = st[S_CASH] * mrate[i]
                st[S_CASH] = st[S_CASH] + pay
                scal[SC_MARGIN_USD] = scal[SC_MARGIN_USD] - pay
        # ---- optional idle-cash yield, accrued before the fill and before any tax payment
        if has_cash_rate:
            c0 = st[S_CASH]
            if c0 > 0:
                st[S_CASH] = c0 + c0 * cash_rate[i]
        # -- opt (IC55-B): convert `PREFUND` sessions before a scheduled payment, once known
        if prefund_on:
            j_lo = pay_ptr[i + 1]
            j_hi = pay_ptr[i1 + 1]
            s_end = i + int(PREFUND)
            if s_end < i1:
                j_hi = pay_ptr[s_end + 1]
            if j_hi > j_lo:
                want = 0.0
                for j in range(j_lo, j_hi):
                    ty_j = pay_yr[j] - Y0
                    if has_due[ty_j] == 0:
                        continue  # the year is not assessed yet: the amount is unknown
                    if pay_part[j] == 0:
                        amt_j = yr[YR_DUE, ty_j] / 2
                    else:
                        amt_j = yr[YR_DUE, ty_j] - yr[YR_PAID, ty_j]
                    if amt_j > 1e-9:
                        want = want + amt_j
                need_try = want - restry
                if need_try > 1e-9:
                    usd = need_try / fx[i]
                    if fx_on:
                        usd = usd / (1.0 - FX_COST)
                    if st[S_CASH] >= usd:
                        st[S_CASH] = st[S_CASH] - usd
                        restry = restry + need_try
                        scal[SC_PREFUND_USD] = scal[SC_PREFUND_USD] + usd
                        if fx_on:
                            scal[SC_FXCOST_USD] = scal[SC_FXCOST_USD] + usd * FX_COST
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
                    if fx_on:
                        # -- opt (IC55-A): the conversion itself costs `FX_COST`
                        scal[SC_FXCOST_USD] = scal[SC_FXCOST_USD] + usd * FX_COST / (1.0 - FX_COST)
                        usd = usd / (1.0 - FX_COST)
                    if st[S_CASH] < usd:
                        need = usd - st[S_CASH]
                        p = cq[i - 1]
                        if st[S_UNITS] > 1e-12:
                            _sell(
                                st, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of,
                                M_lo, i - 1, p, need / p, need * cost, R_TAX, index_on, yr_of, Y0,
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
                # -- opt (IC55-B): the pre-funded reserve pays first, with no further conversion
                paid_try = 0.0
                if prefund_on and restry > 0.0:
                    if restry >= amt:
                        paid_try = amt
                        restry = restry - amt
                        amt = 0.0
                    else:
                        paid_try = restry
                        amt = amt - restry
                        restry = 0.0
                if amt > 1e-9:
                    usd = amt / fx[i]
                    if fx_on:
                        # -- opt (IC55-A): the conversion of the rest costs `FX_COST`
                        scal[SC_FXCOST_USD] = scal[SC_FXCOST_USD] + usd * FX_COST / (1.0 - FX_COST)
                        usd = usd / (1.0 - FX_COST)
                    if st[S_CASH] < usd:
                        need = (usd - st[S_CASH]) / (1 - cost)
                        if st[S_UNITS] > 1e-12:
                            c_before = st[S_CASH]
                            _sell(
                                st, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo,
                                i, oq[i], need / oq[i], need * cost, R_TAX, index_on, yr_of, Y0,
                            )
                            cnt[C_TAXSALES] += 1
                            if settle_on:
                                # -- opt (IC58): these proceeds are not spendable until tomorrow
                                unsettled = unsettled + (st[S_CASH] - c_before)
                    st[S_CASH] = st[S_CASH] - usd
                    st[S_TPUSD] = st[S_TPUSD] + usd
                yr[YR_PAID, ti] = yr[YR_PAID, ti] + paid_try + amt

        po = oq[i]
        # -- opt (IC54): the fill reference
        if has_fill:
            po = fill[i]
        elif FILL_MODE == 1:
            po = (oq[i] + cq[i]) / 2.0
        pc = cq[i]
        held = st[S_UNITS] * po
        nav_open = st[S_CASH] + held
        if nav_open <= 0:
            nav_open = st[S_CASH]
        if nav_open > 0:
            w = (st[S_UNITS] * po) / nav_open
        else:
            w = 0.0
        # -- opt (IC57-A): which decision's target this fill uses
        if i > 0:
            if early_skip:
                tgt = wq[dec_idx]
            else:
                tgt = wq[i - 1]
        else:
            tgt = wq[0]
        if tgt != tgt:
            tgt = 0.0
        # -- opt (IC49): the drawdown governor re-sizes the (un-capped) target
        if dd_on:
            nav_prev = nav[i - i0]
            if nav_prev > peak:
                peak = nav_prev
            dd = 0.0
            if peak > 0:
                dd = 1.0 - nav_prev / peak
            sb = (VOL_TARGET - dd) / (VOL_TARGET - DD_START)
            if sb > 1.0:
                sb = 1.0
            if sb < DD_FLOOR:
                sb = DD_FLOOR
            sb = VOL_TARGET * sb
            vol = wvol[i - 1] if i > 0 else wvol[0]
            ratio = 1.0
            if vol > 0:
                vt = VOL_TARGET / vol
                if vt > 1.0:
                    vt = 1.0
                vte = sb / vol
                if vte > vt:
                    vte = vt
                ratio = vte / vt
            raw = tgt * ratio
            if raw > CAP:
                raw = raw * (CAP / raw)
            if (not DD_UPSIZE_ONLY) or raw > w:
                tgt = raw
        slice_pre = slice_on and sl_left > 0
        # -- opt (IC59): the resize threshold is per direction
        if tgt > w:
            trade = abs(tgt - w) > BAND_UP
        else:
            trade = abs(tgt - w) > BAND_DOWN
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

        # ---- the arm: H2's December window first, then A1u's gain-scaled band, on a
        #      discretionary downward resize (screen.py:658)
        blocked = 0
        if trade and tgt > 1e-12 and (not slice_pre):
            if defer and dec_tail[i] and tgt < w:
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
        # -- opt (IC52): a dollar floor on a permitted downsize
        if trade and tgt > 1e-12 and du < -1e-12 and (not slice_pre):
            if MIN_NOTIONAL > 0.0 or MIN_FRAC > 0.0:
                thr = MIN_NOTIONAL
                if MIN_FRAC > 0.0:
                    v = MIN_FRAC * nav_open
                    if v > thr:
                        thr = v
                if abs(du) * po < thr:
                    trade = False
                    blocked = BLK_NOTIONAL
                    scal[SC_NOTIONAL_BLOCKS] = scal[SC_NOTIONAL_BLOCKS] + 1.0
            # -- opt (IC57-C): an early close declines the downsize, and only the downsize
            if early_block and early[i]:
                trade = False
                blocked = BLK_EARLY
                scal[SC_EARLY_BLOCKS] = scal[SC_EARLY_BLOCKS] + 1.0
        # -- opt (IC53): split an approved order over the following sessions' opens
        if trade and slice_on and (not slice_pre):
            frac = 0.0
            if nav_open > 0:
                frac = abs(du) * po / nav_open
            if frac > SLICE_FRAC and abs(du) * po > 1e-15:
                parts = int(SLICE_PARTS)
                sl_units = du / parts
                sl_left = parts - 1
                du = sl_units
                drift = abs(du) * po / nav_open if nav_open > 0 else 0.0
                fee_total = abs(du) * po * cost
                scal[SC_SLICE_TRIGGERS] = scal[SC_SLICE_TRIGGERS] + 1.0
        if slice_pre:
            # -- opt (IC53): a scheduled part fills unconditionally (the gate ran at the trigger)
            trade = True
            blocked = 0
            du = sl_units
            drift = abs(du) * po / nav_open if nav_open > 0 else 0.0
            fee_total = abs(du) * po * cost
            sl_left = sl_left - 1
            scal[SC_SLICE_SESSIONS] = scal[SC_SLICE_SESSIONS] + 1.0
        # -- opt (IC58): a buy may only spend settled cash (plus the allowed deficit)
        if trade and settle_on and du > 1e-12 and (not slice_pre):
            avail = st[S_CASH] - unsettled + SETTLE_FLOOR * nav_after
            maxu = 0.0
            if avail > 0.0 and po > 0:
                maxu = avail / po
            if du > maxu:
                du = maxu
                if du <= 1e-15:
                    trade = False
                else:
                    drift = du * po / nav_open if nav_open > 0 else 0.0
                    fee_total = du * po * cost
                scal[SC_SETTLE_DEFERS] = scal[SC_SETTLE_DEFERS] + 1.0

        if trade:
            scal[SC_TURNOVER] = scal[SC_TURNOVER] + drift
            if du > 1e-12:
                _buy(st, lu, lp, lf, lm, lh, fx, mth_of, i, po, du * po, fee_total)
            elif du < -1e-12:
                c_before = st[S_CASH]
                _sell(
                    st, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i, po,
                    -du, fee_total, R_RESIZE, index_on, yr_of, Y0,
                )
                cnt[C_QSELLS] += 1
                if settle_on:
                    unsettled = unsettled + (st[S_CASH] - c_before)
            cnt[C_TRADES] += 1
        elif blocked != 0:
            cnt[C_POSTP] += 1
            yr[YR_POST, yi - Y0] += 1
            if blocked == BLK_BAND:
                cnt[C_BANDBLK] += 1
            elif blocked == BLK_DEFER:
                cnt[C_DEFER] += 1
                yr[YR_DEFER, yi - Y0] += 1

        if trace:
            if not trade:
                dec[i - i0] = 0
            elif du < -1e-12:
                dec[i - i0] = 2
            else:
                dec[i - i0] = 1
        nav_i = st[S_CASH] + st[S_UNITS] * pc
        if prefund_on:
            # -- opt (IC55-B): the TRY reserve is part of the account
            nav_i = nav_i + restry / fx[i]
        nav[i - i0 + 1] = nav_i
        if nav_i > peak:
            peak = nav_i
        if dbg_on:
            dbg[i - i0, 0] = st[S_CASH]
            dbg[i - i0, 1] = st[S_UNITS]
            dbg[i - i0, 2] = lh[0]
            dbg[i - i0, 3] = lh[1] - lh[0]
            dbg[i - i0, 4] = drift
            dbg[i - i0, 5] = du
        first = False
        if not slice_pre:
            prev_tgt = tgt
        # -- opt (IC57-A): an early close may decline to make this session's decision
        if early_skip and early[i]:
            scal[SC_EARLY_SKIPS] = scal[SC_EARLY_SKIPS] + 1.0
        else:
            dec_idx = i

    # ---- liquidation at the window end (screen.py:703)
    if prefund_on and restry > 0.0:
        # -- opt (IC55-B): a reserve left over at the window end is converted back
        back = restry / fx[i1]
        if fx_on:
            back = back * (1.0 - FX_COST)
        st[S_CASH] = st[S_CASH] + back
        restry = 0.0
    end_y = yr_of[i1]
    if st[S_UNITS] > 0:
        _sell(
            st, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of, M_lo, i1, cq[i1],
            st[S_UNITS], st[S_UNITS] * cq[i1] * cost, R_LIQUIDATION, index_on, yr_of, Y0,
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
        usd_liq = liq_tax / fx1
        if fx_on:
            # -- opt (IC55-A): the liquidation's conversion costs `FX_COST` too
            usd_liq = usd_liq / (1.0 - FX_COST)
            scal[SC_FXCOST_USD] = scal[SC_FXCOST_USD] + usd_liq * FX_COST
        st[S_CASH] = st[S_CASH] - usd_liq
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


def simulate_opts(
    opens,
    closes,
    wants,
    env,
    capital,
    i0,
    i1,
    arm="C0",
    mode="pre_tax",
    m=2,
    rule_index=True,
    trace=False,
    dbg=None,
    opts=None,
    fill=None,
    mrate=None,
    early=None,
    wvol=None,
):
    """`fe_sim.simulate` with the option set. `opts=None` (or `Opts()`) is the engine.

    The extra arrays are per path, one entry per session of the window: `fill` (the fill
    reference, IC54), `mrate` (the per-session margin rate, IC39), `early` (early-close flags,
    IC57) and `wvol` (the leg's vol20 row, IC49). `opts.needs()` names the ones an option set
    reads; a missing one raises here rather than silently switching the mechanism off.
    """
    from research.fast_engine.fe_sim import calendar_years, max_dd  # local: keeps import order

    o = Opts() if opts is None else opts
    spec = RULE["C0"] if arm in ("R2", "E2") else RULE[arm]
    mode_i = MODES.index(mode) if isinstance(mode, str) else int(mode)
    if mode_i == MODE_PROGRESSIVE and not env.scale_ok:
        raise ValueError("progressive mode needs a YI-UFE table with the 2025-10 anchor")
    oq = np.ascontiguousarray(np.asarray(opens, dtype=np.float64).ravel())
    cq = np.ascontiguousarray(np.asarray(closes, dtype=np.float64).ravel())
    wq = np.ascontiguousarray(np.asarray(wants, dtype=np.float64).ravel())
    if not o.is_off():
        given = {"fill": fill, "mrate": mrate, "early": early, "wvol": wvol}
        for key in o.needs():
            if given[key] is None:
                raise ValueError(f"the option set needs the {key!r} array")
    optf, opti = o.pack()

    def _vec(arr, dtype):
        if arr is None:
            return np.zeros(0, dtype=np.float64 if dtype == np.float64 else np.bool_)
        return np.ascontiguousarray(np.asarray(arr, dtype=dtype).ravel())

    fill_a = _vec(fill, np.float64)
    mrate_a = _vec(mrate, np.float64)
    early_a = _vec(early, np.bool_)
    wvol_a = _vec(wvol, np.float64)
    for name, arr in (("fill", fill_a), ("mrate", mrate_a), ("early", early_a), ("wvol", wvol_a)):
        if arr.shape[0] and arr.shape[0] != wq.shape[0]:
            raise ValueError(f"{name} must have one entry per session ({wq.shape[0]})")
    nav, dec, scal, yr, gbr, gbr_n, has_due = sim_one_opts(
        oq, cq, wq, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail, env.pay_ptr,
        env.pay_yr, env.pay_part, float(capital), int(i0), int(i1), env.cost(m),
        bool(spec["band"]), bool(spec["usd_rule"]), bool(spec["defer"]), mode_i, bool(rule_index),
        env.Y0, env.M_lo, env.cash_rate, bool(trace),
        np.zeros((0, 6), dtype=np.float64) if dbg is None else dbg,
        optf, opti, fill_a, mrate_a, early_a, wvol_a,
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
    out = {
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
        "_nav": nav,
        "_dec": dec if trace else None,
    }
    if not o.is_off() or fill_a.size or mrate_a.size or early_a.size or wvol_a.size:
        out["opts_stats"] = {
            "early_skips": int(scal[SC_EARLY_SKIPS]),
            "early_blocks": int(scal[SC_EARLY_BLOCKS]),
            "settle_defers": int(scal[SC_SETTLE_DEFERS]),
            "slice_triggers": int(scal[SC_SLICE_TRIGGERS]),
            "slice_sessions": int(scal[SC_SLICE_SESSIONS]),
            "notional_blocks": int(scal[SC_NOTIONAL_BLOCKS]),
            "margin_usd": float(scal[SC_MARGIN_USD]),
            "fx_cost_usd": float(scal[SC_FXCOST_USD]),
            "prefund_usd": float(scal[SC_PREFUND_USD]),
        }
    return out


__all__ = ["N_SCAL_OPTS", "SC_EARLY_BLOCKS", "SC_EARLY_SKIPS", "SC_FXCOST_USD", "SC_MARGIN_USD",
           "SC_NOTIONAL_BLOCKS", "SC_PREFUND_USD", "SC_SETTLE_DEFERS", "SC_SLICE_SESSIONS",
           "SC_SLICE_TRIGGERS", "sim_one_opts", "simulate_opts"]
