# hrp_sleeve_v1 copy of research/fast_engine3/fe3_sim.py. The only change is a per-leg resize band
# (`band_legs`, one threshold per leg; default fe_sim.C0_BAND for every leg, reproducing fe3_sim
# bit-for-bit, checked by screen.py --harness). Nothing else differs.
"""fe3_sim -- the N-leg simulation kernel: `fe2_sim.sim2` generalised to N <= 5 instruments.

One account, one cash pool, **N** instruments, each with its own units and its own FIFO lot book;
one taxpayer, so every leg's realisation lands in the same annual net TRY gain (`year_gain`) and
the Turkish after-tax engine of `fast_engine.fe_tax` is reused unchanged. Per session, per leg: the
0.5 pp resize band, the A1u gain-scaled downsize band (on *that leg's* own lot book, its own gain
fraction) and the H2 December deferral; costs are charged per leg on that leg's own traded notional;
the legs share the post-cost NAV that sizes the fill.

The kernel is leg-index generic: the number of legs is `oq.shape[0]` (1..`N_LEGS`), the per-leg
primitives are imported from `fe2_sim` (`_buy_leg`/`_sell_leg` are already indexed by a leg slot),
and the session loop is `sim2`'s with `range(N_LEGS)` widened to `range(n)`. That is what makes the
two parity identities hold **bit for bit**:

  * legs 2..N identically zero (zero opens, closes and targets) -> `fe_sim.sim_one`
    (pre-tax, progressive, flat, and the bootstrap): every extra leg adds `0.0` to a sum, is
    skipped by every `un[l] > 1e-12` test and reports a zero count;
  * legs 3..N identically zero -> `fe2_sim.sim2`, in the same way.

`fe3_parity.py` asserts both on the NAV path and on every reported scalar.

The one mechanism added over `fe2_sim` is the **margin loan** of `research.fast_engine_opts`
(IC39): when `mrate` carries one entry per session, a *negative* cash balance accrues interest at
`cash * mrate[i]` (i.e. cash becomes more negative by the interest) at the top of each session,
before the idle-cash yield and before any tax payment -- exactly the hook `fe_opts_sim.sim_one_opts`
applies. It lets a target that sums above the account's cap (a sleeve on top of production, IC45-C
/ IC46-C) hold a cash loan and pay for it. With `mrate` empty the branch is unreachable and the
kernel is `fe2_sim.sim2` exactly.

The second mechanism added over `fe2_sim` is the **gain-aware downsize router** of the two-leg
option copy `research/fast_engine2/fe2_opts.py` (`route_cap`), generalised to N legs and with a
selectable score. On a session in which at least two legs want to *downsize*, the units the most
expensive selling leg would realise are moved to the cheapest selling leg, bounded by
`route_cap * nav_after` of the cheap leg's own post-cost weight and by either leg's own sale (the
`fe2_opts` move verbatim). `route_score` picks the score: 1 (`indexed`) is the indexed-TRY FIFO
gain fraction -- exactly `fe2_opts`'s -- and 0 (`usd`) is the USD FIFO gain fraction, both from
`fe_sim`'s read-only FIFO walks. With `route_cap = 0.0` the block is unreachable and the kernel is
`fe2_sim.sim2`/`fe_sim.sim_one` bit for bit; `fe3_parity` proves the indexed router reproduces
`fe2_opts`'s `route_cap` cell bit for bit on two legs. Routing changes only the *split* of a
downsize between the books: `trade[l]`, the H2 deferral and the December rules are untouched, but a
leg whose whole sale is moved becomes a no-op fill, so the fill count can fall while `trade[l]`
stays set.

The third is a **declaration** capability rather than a kernel branch: two legs may share one price
series (the `"legs": ["2x", "2x"]` "two books of one instrument" of IB38-C). The kernel is already
leg-index generic -- each leg has its own units/FIFO book and its own band -- so a duplicated key
is exactly two books of one instrument; `batch_screen.api` and `bridge3` are what refuse and then
resolve the duplicate declaration.

Deliberate differences from `fe2_sim.sim2`: the units live in `un[leg]` for `leg` in
`range(n)` rather than a two-row array; the per-leg `scal`/`yr2` blocks are `n` rows; the
tax-funding sale walks legs in order `0..n-1` (with one leg that is the single-leg behaviour, with
two the two-leg behaviour); and `scal` carries one extra slot (`SC_MARGIN_USD`).
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
from research.fast_engine2.fe2_sim import _buy_leg, _sell_leg

#: the account's leg capacity (N <= 5); a caller passes exactly as many rows as it holds.
N_LEGS = 5
LEG_A, LEG_B = 0, 1
LEG_NAMES = ("A", "B", "C", "D", "E")

#: the downsize router's gain-fraction scores (`score_mode`'s convention: `usd` = the USD FIFO
#: gain fraction `fe_sim._rule_sale_usd`, `indexed` = the YI-UFE-indexed TRY FIFO gain fraction
#: `fe_sim._rule_sale(..., index_on=True)`). `indexed` is the two-leg option copy's own score
#: (`research/fast_engine2/fe2_opts.py`, `route_cap`), so it is the one the parity cells prove
#: bit-for-bit; the IC41/IB28 arms ask for `usd` (the bank's "lower USD FIFO gain fraction").
ROUTE_SCORES = {"usd": 0, "indexed": 1}

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

# account-level scalar outputs (`scal`, float64) -- fe_sim.SC_* names, same order, plus the margin
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
SC_MARGIN_USD = 15
N_SCAL = 16
# per-year output rows (same as `fe_sim`)
YR_GAIN, YR_DUE, YR_PAID, YR_POST, YR_DEFER, N_YR = range(6)
# per-year per-leg postponement rows
YR2_POST, YR2_DEFER = range(2)
N_YR2 = 2


# ----------------------------------------------------------------------------- the session loop
@njit(cache=True, nogil=True)
def sim3(
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
    band_legs,
    band_on,
    usd_rule,
    defer,
    mode,
    rule_index,
    Y0,
    M_lo,
    cash_rate,
    mrate,
    route_cap,
    route_score,
    trace,
    dbg,
):
    """One N-leg arm's session loop: `fe2_sim.sim2` generalised to `oq.shape[0]` legs.

    `oq`/`cq`/`wq` are `(n, T)` open/close/target panels (row `l` = leg `l`, row 0 = leg A), the
    target decided at the close of `i - 1` and filled at the open of `i`. `mrate` is an optional
    per-session margin rate on a negative cash balance (an empty array switches it off, which is
    the `fe2_sim`/`fe_sim` behaviour). `route_cap` (a weight fraction, 0 = off) is the gain-aware
    downsize router: on a session where at least two legs want to *downsize*, the units the most
    expensive leg (`route_score` = 0 `usd` / 1 `indexed`) would sell are moved to the cheapest
    selling leg, bounded by `route_cap * nav_after` -- the `research/fast_engine2/fe2_opts.py`
    `route_cap` mechanism, generalised from two legs to `n` and with a selectable score. Returns
    `sim2`'s tuple plus the `n`-row per-leg blocks: `(nav, dec, scal, yr, gbr, gbr_n, has_due,
    legscal, yr2)` with `gbr` `(n, N_REASONS)`.
    """
    dbg_on = dbg.shape[0] > 1
    n = oq.shape[0]
    nsess = i1 - i0 + 1
    ny = yr_of[i1] - Y0 + 1
    nav = np.empty(nsess + 1, dtype=np.float64)
    nav[0] = capital
    dec = np.zeros(nsess, dtype=np.int8)
    scal = np.zeros(N_SCAL, dtype=np.float64)
    legscal = np.zeros((n, N_LEGSCAL), dtype=np.float64)
    cnt = np.zeros((n, N_CNT), dtype=np.int64)
    yr = np.zeros((N_YR, ny), dtype=np.float64)
    yr2 = np.zeros((N_YR2, ny), dtype=np.float64)
    gbr = np.zeros((n, N_REASONS), dtype=np.float64)
    gbr_n = np.zeros((n, N_REASONS), dtype=np.int64)
    has_due = np.zeros(ny, dtype=np.int8)
    st = np.zeros(N_ST, dtype=np.float64)
    st[S_CASH] = capital
    un = np.zeros(n, dtype=np.float64)
    maxlots = nsess + 4
    lu = np.zeros((n, maxlots), dtype=np.float64)
    lp = np.zeros((n, maxlots), dtype=np.float64)
    lf = np.zeros((n, maxlots), dtype=np.float64)
    lm = np.zeros((n, maxlots), dtype=np.int64)
    lh = np.zeros((n, 2), dtype=np.int64)

    gain_row = yr[YR_GAIN]
    div_row = np.zeros(ny, dtype=np.float64)  # dividends are not modelled: always zero
    wht_row = np.zeros(ny, dtype=np.float64)  # no withholding: always zero
    index_on = mode == MODE_PROGRESSIVE
    first = np.ones(n, dtype=np.bool_)
    prev_tgt = np.zeros(n, dtype=np.float64)
    # per-session scratch, allocated once (nothing is allocated inside the session loop)
    held = np.zeros(n, dtype=np.float64)
    w = np.zeros(n, dtype=np.float64)
    tgt = np.zeros(n, dtype=np.float64)
    drift = np.zeros(n, dtype=np.float64)
    du = np.zeros(n, dtype=np.float64)
    fee_leg = np.zeros(n, dtype=np.float64)
    trade = np.zeros(n, dtype=np.bool_)
    blocked = np.zeros(n, dtype=np.int64)
    fr = np.zeros(n, dtype=np.float64)  # the router's per-leg FIFO gain fractions

    has_cash_rate = cash_rate.shape[0] > 0
    has_mrate = mrate.shape[0] > 0
    for i in range(i0, i1 + 1):
        yi = yr_of[i]
        # ---- optional margin loan: interest accrues while the cash balance is negative
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
        # ---- year boundary: assess the previous year (one taxpayer, all legs' gains pooled)
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
                        for l in range(n):
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
                    for l in range(n):
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

        for l in range(n):
            held[l] = un[l] * oq[l, i]
        nav_open = st[S_CASH]
        for l in range(n):
            nav_open = nav_open + held[l]
        if nav_open <= 0:
            nav_open = st[S_CASH]
        if nav_open > 0:
            for l in range(n):
                w[l] = held[l] / nav_open
        for l in range(n):
            if i > 0:
                t = wq[l, i - 1]
            else:
                t = wq[l, 0]
            if t != t:
                t = 0.0
            tgt[l] = t
            blocked[l] = 0
            drift[l] = 0.0
            trade[l] = abs(t - w[l]) > band_legs[l]
            if un[l] == 0 and t > 1e-12 and first[l]:
                trade[l] = True
            if un[l] > 1e-12 and t <= 0 and (not first[l]) and prev_tgt[l] > 0:
                trade[l] = True
            if trade[l]:
                drift[l] = abs(t - w[l])

        # ---- pass 1: the provisional shared cost of every leg that wants to trade (the
        #      single-leg code path, with the legs' drifts summed into one fee)
        fee_total = 0.0
        for l in range(n):
            fee_leg[l] = nav_open * cost * drift[l]
            fee_total = fee_total + fee_leg[l]
        nav_after = nav_open - fee_total
        for l in range(n):
            if oq[l, i] > 0:
                target_units = tgt[l] * nav_after / oq[l, i]
            else:
                target_units = 0.0
            du[l] = target_units - un[l]

        # ---- pass 2a: the arm's H2 December deferral per leg (today's first branch, unchanged)
        for l in range(n):
            if trade[l] and tgt[l] > 1e-12:
                if defer and dec_tail[i] and tgt[l] < w[l]:
                    trade[l] = False
                    blocked[l] = 1

        # ---- pass 2b: the optional gain-aware downsize router (N legs; off -> unreachable).
        #      Generalises `fe2_opts`'s two-leg `route_cap`: with two selling legs it picks the
        #      same (cheap, other) pair and applies the same single, cap-bounded move, so with
        #      `route_score = 1` (indexed) it is `fe2_opts` bit for bit (fe3_parity proves it).
        routed = False
        if route_cap > 0.0:
            nsell = 0
            for l in range(n):
                if trade[l] and tgt[l] > 1e-12 and du[l] < -1e-12 and oq[l, i] > 0:
                    nsell += 1
            if nsell >= 2:
                cheap = -1
                other = -1
                for l in range(n):
                    if trade[l] and tgt[l] > 1e-12 and du[l] < -1e-12 and oq[l, i] > 0:
                        if route_score == 0:
                            g_r, p_r = fe_sim._rule_sale_usd(lu[l], lp[l], lh[l], i, oq[l, i],
                                                             -du[l], fee_leg[l])
                        else:
                            g_r, p_r = fe_sim._rule_sale(lu[l], lp[l], lf[l], lm[l], lh[l], fx,
                                                         upm, mth_of, M_lo, i, oq[l, i], -du[l],
                                                         fee_leg[l], True)
                        fr[l] = g_r / p_r if p_r > 0 else 0.0
                        if cheap < 0 or fr[l] < fr[cheap]:
                            cheap = l
                for l in range(n):
                    if l != cheap and trade[l] and tgt[l] > 1e-12 and du[l] < -1e-12 and oq[l, i] > 0:
                        if other < 0 or fr[l] > fr[other]:
                            other = l
                if cheap >= 0 and other >= 0:
                    x = -du[other]
                    capu = un[cheap] + du[cheap]  # the cheap leg's units after its own sale
                    if capu < x:
                        x = capu
                    lim = route_cap * nav_after / oq[cheap, i]
                    if lim < x:
                        x = lim
                    if x > 0:
                        du[cheap] = du[cheap] - x
                        du[other] = du[other] + x
                        for l in range(n):
                            if -du[l] > 0:
                                drift[l] = (-du[l]) * oq[l, i] / nav_after
                            else:
                                drift[l] = 0.0
                            fee_leg[l] = nav_open * cost * drift[l]
                        routed = True

        # ---- pass 2c: the A1u gain-scaled downsize band per leg, on that leg's own lot book
        for l in range(n):
            if trade[l] and blocked[l] == 0 and tgt[l] > 1e-12 and du[l] < -1e-12 and band_on:
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
        for l in range(n):
            if trade[l]:
                fee_total = fee_total + fee_leg[l]
        nav_after = nav_open - fee_total
        for l in range(n):
            if trade[l]:
                if oq[l, i] > 0:
                    target_units = tgt[l] * nav_after / oq[l, i]
                else:
                    target_units = 0.0
                d = target_units - un[l]
                if routed:
                    d = du[l]  # a routed session fills the routed units, not the recomputed ones
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
            for l in range(n):
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
        for l in range(n):
            mtm = mtm + un[l] * cq[l, i]
        nav[i - i0 + 1] = mtm
        if dbg_on:
            dbg[i - i0, 0] = st[S_CASH]
            dbg[i - i0, 1] = un[LEG_A]
            dbg[i - i0, 2] = lh[LEG_A, 0]
            dbg[i - i0, 3] = lh[LEG_A, 1] - lh[LEG_A, 0]
            dbg[i - i0, 4] = drift[LEG_A]
            dbg[i - i0, 5] = du[LEG_A]
        for l in range(n):
            first[l] = False
            prev_tgt[l] = tgt[l]

    # ---- liquidation at the window end (every leg, at the final close), then one assessment
    end_y = yr_of[i1]
    for l in range(n):
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

    for l in range(n):
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
    units_end = 0.0
    for l in range(n):
        units_end = units_end + un[l]
    scal[SC_UNITS_END] = units_end
    scal[SC_LIQ_TAX] = liq_tax
    scal[SC_LIQ] = st[S_CASH]
    scal[SC_MTM] = nav[nsess]
    return nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2


# ----------------------------------------------------------------------------- the result dict
def _outputs3(nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2, env, capital, i0, i1, trace,
              margin=False):
    """`simulate3`'s post-processing tail: `fe2_sim._outputs` for `gbr.shape[0]` legs.

    The account-level cells are `fe2_sim._outputs`'s, summed over every leg in leg order (with the
    extra legs identically zero they are the two-leg kernel's cells exactly); the per-leg `_legs`
    dict is keyed by `LEG_NAMES`. `margin=True` adds `opts_stats` (the loan interest charged), the
    same key `fe_opts_sim.simulate_opts` reports, so a single-leg margin run compares cell by cell.
    """
    n = gbr.shape[0]
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
    reasons = {}
    for r in range(N_REASONS):
        nc, tot = 0, 0.0
        for l in range(n):
            nc = nc + int(gbr_n[l, r])
            tot = tot + gbr[l, r]
        if nc > 0:
            reasons[REASON_NAMES[r]] = float(tot)
    legs = {}
    for l in range(n):
        name = LEG_NAMES[l] if l < len(LEG_NAMES) else f"L{l}"
        lr = {REASON_NAMES[r]: float(gbr[l, r]) for r in range(N_REASONS) if gbr_n[l, r] > 0}
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
    out = {
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
    if margin:
        out["opts_stats"] = {"margin_usd": float(scal[SC_MARGIN_USD])}
    return out


# ----------------------------------------------------------------------------- one run
def _stack(seq, what):
    """A `(N, T)` panel from a 2-D array or a sequence of one-dimensional (or `(1, T)`) arrays."""
    if isinstance(seq, np.ndarray) and seq.ndim == 2 and seq.dtype != object:
        arr = np.asarray(seq, dtype=np.float64)
    else:
        rows = [np.asarray(x, dtype=np.float64).ravel() for x in seq]
        if not rows:
            raise ValueError(f"{what} is empty")
        width = rows[0].shape[0]
        if any(r.shape[0] != width for r in rows):
            raise ValueError(f"the {what} legs must share one session count")
        arr = np.stack(rows)
    if arr.ndim != 2:
        raise ValueError(f"{what} must be (N, T) or a sequence of 1-D arrays; got shape {arr.shape}")
    return np.ascontiguousarray(arr)


def simulate3(
    opens,
    closes,
    wants,
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
    mrate=None,
    route_cap=0.0,
    route_score=1,
    band_legs=None,
):
    """`fe2_sim.simulate2` over N legs (N <= 5): the same dict plus the per-leg blocks.

    `opens`/`closes`/`wants` are `(N, T)` panels (or a sequence of 1-D arrays); row 0 is leg A.
    `mrate` is an optional `(T,)` (or `(P, T)`-row) margin rate: an empty/None array is the
    `fe2_sim`/`fe_sim` behaviour. `route_cap`/`route_score` are the optional downsize router
    (`route_cap = 0.0` -> off; `route_score` 0 `usd` / 1 `indexed`, the score is ignored when the
    router is off). `arm` names the fill rule (`fe_sim.RULE`), applied to **all** legs; `mode` and
    `m` are `fe_sim.simulate`'s.
    """
    spec = fe_sim.RULE["C0"] if arm in ("R2", "E2") else fe_sim.RULE[arm]
    mode_i = MODES.index(mode) if isinstance(mode, str) else int(mode)
    if mode_i == MODE_PROGRESSIVE and not env.scale_ok:
        raise ValueError("progressive mode needs a YI-UFE table with the 2025-10 anchor")
    oq = _stack(opens, "opens")
    cq = _stack(closes, "closes")
    wq = _stack(wants, "wants")
    if not (oq.shape == cq.shape == wq.shape):
        raise ValueError(f"the leg panels must share one shape; got {oq.shape}, {cq.shape}, "
                         f"{wq.shape}")
    if oq.shape[0] > N_LEGS:
        raise ValueError(f"the kernel holds at most {N_LEGS} legs; got {oq.shape[0]}")
    if mrate is None:
        mrate_a = np.zeros(0, dtype=np.float64)
    else:
        mrate_a = np.ascontiguousarray(np.asarray(mrate, dtype=np.float64).ravel())
        if mrate_a.shape[0] != wq.shape[1]:
            raise ValueError(f"mrate must have one entry per session ({wq.shape[1]})")
    if band_legs is None:
        band_legs_a = np.full(oq.shape[0], fe_sim.C0_BAND, dtype=np.float64)
    else:
        band_legs_a = np.ascontiguousarray(np.asarray(band_legs, dtype=np.float64).ravel())
        if band_legs_a.shape[0] != oq.shape[0]:
            raise ValueError('band_legs needs one entry per leg')
    route_cap = float(route_cap)
    route_score = int(route_score)
    if not np.isfinite(route_cap) or route_cap < 0.0:
        raise ValueError(f"route_cap must be finite and >= 0; got {route_cap!r}")
    if route_score not in (0, 1):
        raise ValueError(f"route_score must be 0 (usd) or 1 (indexed); got {route_score!r}")
    nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2 = sim3(
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
        band_legs_a,
        bool(spec["band"]),
        bool(spec["usd_rule"]),
        bool(spec["defer"]),
        mode_i,
        bool(rule_index),
        env.Y0,
        env.M_lo,
        env.cash_rate,
        mrate_a,
        float(route_cap),
        int(route_score),
        bool(trace),
        np.zeros((0, 6), dtype=np.float64) if dbg is None else dbg,
    )
    if env.ufe_missing and not (np.isfinite(nav).all() and np.isfinite(scal).all()):
        raise ValueError(f"a YI-UFE month is missing from the table: {env.ufe_missing[:5]}")
    return _outputs3(nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2, env, capital, i0, i1,
                     trace, margin=mrate_a.shape[0] > 0)


__all__ = [
    "LEG_A",
    "LEG_B",
    "LEG_NAMES",
    "N_CNT",
    "N_LEGS",
    "N_LEGSCAL",
    "N_SCAL",
    "N_ST",
    "N_YR",
    "N_YR2",
    "ROUTE_SCORES",
    "SC_FEES",
    "SC_LIQ",
    "SC_MARGIN_USD",
    "SC_MTM",
    "SC_TRADES",
    "SC_TURNOVER",
    "YR_GAIN",
    "YR_PAID",
    "sim3",
    "simulate3",
]
