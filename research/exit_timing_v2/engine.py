"""engine.py -- `research/fast_engine/fe_sim.py` plus the X1/X2/X3 exit-timing options.

This file is a **copy of `research/fast_engine/fe_sim.py`** (the Numba research engine), with one
additive option block for `exit_timing_v2`:

  * `dec_exit` (X1) -- a fill that would be an **exit to zero** (`tgt <= 0` with a live position)
    whose **fill** session is one of the last `DEC_TAIL` December sessions is not executed; the
    target is re-evaluated at every close, so the exit fills at the first January decision and is
    never postponed twice;
  * `stage` (X2) -- an exit to zero whose consumed FIFO lots have a USD gain fraction above
    `X2_GAIN` sells only down to the current early-leg weight `w_early` (`0.5 x vt`) instead of
    to zero; the remainder is held while the early-conditions set `hold` stands, at most
    `X2_CAP_SESSIONS` sessions after the staging fill, and is exited in full at the first decision
    close at which `hold` fails; the ordinary target takes the position over if it turns positive;
  * `gbr_neg` -- a per-reason accumulator of the *negative* realised TRY gain (the report's
    `neg_gain_try_by_reason`), which `fe_sim` does not carry.

Both options default to **off** and change nothing when off: `--smoke` asserts that
`simulate_x` with the options off reproduces `fe_sim.simulate` bit-for-bit (max |NAV diff| = 0)
for every base arm and mode, and that `boot_x` reproduces `fe_boot.boot` bit-for-bit.
Everything else (the kernel, the tax model, the constants) is `fe_sim`'s, unchanged.

`sim_one` used to be described as "a line-for-line port of
`research/band_h2_combo_v1/screen.py:engine_run`": that remains true, and the X block below is the
same block as `research/exit_timing_v1`'s pre-registered (old-engine) implementation, moved into
the kernel. The path-dependent part is here; the signal/target computation stays in numpy
(`research.fast_engine.fe_signals`).

State that `fe_sim` keeps in flat arrays: the FIFO lot book (four parallel arrays + a head/count
pair), the per-year tax state (dense arrays indexed by `year - Y0`, `has_due` standing in for
"present in the dict"), months as `year * 12 + month - 1` and the YI-UFE month-before lookup.
"""

from __future__ import annotations

from datetime import date as _date

import numpy as np
from numba import njit, prange

from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_tax
from research.fast_engine.fe_config import BAND
from research.fast_engine.fe_tax import (
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

# the arm rules (screen.py:150). `usd_rule` selects A1u's USD gain fraction; `rule_index`
# (engine_run's argument) only ever reaches the dead `rule_sale` band branch, see README.
RULE = {
    "C0": {"band": False, "usd_rule": False, "defer": False, "dec_exit": False, "stage": False},
    "A1u": {"band": True, "usd_rule": True, "defer": False, "dec_exit": False, "stage": False},
    "H2": {"band": False, "usd_rule": False, "defer": True, "dec_exit": False, "stage": False},
    "K": {"band": True, "usd_rule": True, "defer": True, "dec_exit": False, "stage": False},
    # X1: postpone a December exit to zero (H2's mechanism on the event H2 exempts).
    "X1": {"band": True, "usd_rule": True, "defer": True, "dec_exit": True, "stage": False},
    # X2: stage a gain-heavy exit to zero down to the early-leg weight.
    "X2": {"band": True, "usd_rule": True, "defer": True, "dec_exit": False, "stage": True},
    # X3: X1 + X2, X1's December window tested first.
    "X3": {"band": True, "usd_rule": True, "defer": True, "dec_exit": True, "stage": True},
}
RULE["R2"] = RULE["C0"]  # alias: the label the R2 anchors are asserted under
RULE["E2"] = RULE["C0"]  # alias: the pure-E2 fidelity anchor series (band off)
ARMS = ("C0", "A1u", "H2", "K")
CONTROL = "C0"
BASELINE = "A1u"
# the X2 parameters (pre-registered; PREREGISTRATION.md section 4.2)
X2_GAIN = 0.05
X2_CAP_SESSIONS = 21

# study constants (the bootstrap and OOS conventions of the parent campaigns, via fe_config)
SEED, BLOCK = fe_config.SEED, fe_config.BLOCK
PRE_BOOT_PATHS, TAX_BOOT_PATHS = fe_config.PRE_BOOT_PATHS, fe_config.TAX_BOOT_PATHS
TAX_BOOT_L, TAX_BOOT_EXT = fe_config.TAX_BOOT_L, fe_config.TAX_BOOT_EXT
TAX_CAPITALS = fe_config.TAX_CAPITALS
PRE_CAPITAL = fe_config.TAX_CAPITALS[fe_config.GATE_CAPITAL]
DEFAULT_CHUNK = fe_boot.DEFAULT_CHUNK
START = fe_config.START
M = fe_config.M
EXTRA_FIN = fe_config.EXTRA_FIN
EXP_Q = fe_config.EXP_Q
OOS_PATHS_PER_MARKET = 2_000
OOS_CHUNK = 500  # trend_robustness_v2.CHUNK
OOS_BOOT_L = 252 + 2520  # TSM_N + trend_robustness_v2.MEASURED

# `st` slots (float64 scratch; the account's scalar state)
S_CASH, S_UNITS, S_FEES, S_IDXREL, S_TPUSD, S_LOSSCF = range(6)
N_ST = 6
# `cnt` slots (int64 counters)
(
    C_TRADES,
    C_QSELLS,
    C_TAXSALES,
    C_POSTP,
    C_BANDBLK,
    C_DEFER,
    C_DEFEXIT,
    C_SEVENTS,
    C_SSESS,
    C_SCAP,
    C_SGATE,
    C_STGT,
) = range(12)
N_CNT = 12
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
    SC_DEFEXIT,
    SC_SEVENTS,
    SC_SSESS,
    SC_SCAP,
    SC_SGATE,
    SC_STGT,
) = range(21)
N_SCAL = 21
# per-year output rows
YR_GAIN, YR_DUE, YR_PAID, YR_POST, YR_DEFER, YR_DEFEXIT, N_YR = range(7)


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
    gbr_neg,
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
    """`BaseAccount.sell` (tax_engine.py:161), plus the per-reason negative-gain accumulator."""
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
    if gain < 0:
        gbr_neg[reason] += gain
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
    dec_exit,
    stage,
    w_early,
    hold,
    x2_gain,
    x2_cap,
    mode,
    rule_index,
    Y0,
    M_lo,
    cash_rate,
    trace,
    dbg,
):
    """One arm's session loop, exactly `screen.engine_run`, plus the X1/X2 options.

    Returns (nav, dec, scal, yr, gbr, gbr_n, gbr_neg, has_due): `nav` has `i1 - i0 + 2` entries,
    `dec` the per-session decision trace (only filled when `trace`), `scal` the flat scalar block
    indexed by the SC_* constants, `yr` the N_YR x NY per-year block, `gbr`/`gbr_n`/`gbr_neg` the
    per-reason gain, count and negative-gain accumulators and `has_due` the "year present in the
    tax_due dict" flags.

    `dec_exit` (X1) and `stage` (X2) are the study's exit-timing options; `w_early` (`0.5 x vt`)
    and `hold` (the early-conditions set) are the `[T]` context arrays X2 reads at `i - 1`. With
    both options off (the default) the kernel is `fe_sim.sim_one` and nothing below reads them.

    `cash_rate` is an optional per-session yield on the idle cash (an empty array means 0, which
    is what every published study runs with -- see README "cash yield").

    `dbg` is an (nsess, 6) scratch buffer for bisecting a divergence against the Python engine:
    when it has more than one row it receives cash, units, lot head, lot count, drift and du per
    session. Pass a zero-row array to switch it off.
    """
    dbg_on = dbg.shape[0] > 1
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
    gbr_neg = np.zeros(N_REASONS, dtype=np.float64)
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
    # ---- X2 state (per path): the session of the staging fill and the release counters
    staged_at = -1
    staged_done = False  # released -> this exit episode may not be staged again

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
                                gbr_neg,
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
                            gbr_neg,
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

        # ---- X1/X2/X3: the arm's rule on an exit to zero (the fill A1u and H2 both exempt by
        #      construction). This block only picks the target and the trade flag; K's ordinary
        #      layer below (H2 window first, then A1u's band) still decides every fill.
        exit_zero = bool(tgt <= 1e-12 and w > 1e-12)
        if staged_at >= 0 and (tgt > 1e-12 or w <= 1e-12):
            if tgt > 1e-12:
                cnt[C_STGT] += 1  # the ordinary rule takes the position over
            staged_at = -1
        if tgt > 1e-12:
            staged_done = False  # the rule is in: a new exit episode may stage
        if (dec_exit or stage) and exit_zero:
            if dec_exit and dec_tail[i]:
                # X1: postpone the whole exit into the next tax year
                trade = False
                cnt[C_POSTP] += 1
                yr[YR_POST, yi - Y0] += 1
                cnt[C_DEFEXIT] += 1
                yr[YR_DEFEXIT, yi - Y0] += 1
            elif stage:
                if staged_at >= 0 and (i - staged_at >= x2_cap or not hold[i - 1]):
                    if i - staged_at >= x2_cap:
                        cnt[C_SCAP] += 1
                    else:
                        cnt[C_SGATE] += 1
                    # released: the remaining position exits in full here and this exit episode
                    # may not be staged again (the hard cap is a cap, not a new timer)
                    staged_at = -1
                    staged_done = True
                if staged_at < 0 and not staged_done:
                    # the text's only entry condition: the consumed lots' USD gain fraction
                    g_full, p_full = _rule_sale_usd(lu, lp, lh, i, po, st[S_UNITS], fee_total)
                    gf = max(0.0, g_full) / p_full if p_full > 0 else 0.0
                    if gf > x2_gain:
                        staged_at = i
                        cnt[C_SEVENTS] += 1
                if staged_at >= 0:
                    # stage: retarget to the early-leg weight, leave the fill to the layer below
                    tgt = w_early[i - 1]
                    drift = abs(tgt - w)
                    fee_total = nav_open * cost * drift
                    nav_after = nav_open - fee_total
                    if po > 0:
                        target_units = tgt * nav_after / po
                    else:
                        target_units = 0.0
                    du = target_units - st[S_UNITS]
                    trade = bool(drift > C0_BAND or du < -1e-12 or du > 1e-12)

        # ---- the arm: H2's December window first, then A1u's gain-scaled band, on a
        #      discretionary downward resize (screen.py:658)
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
                    st,
                    gbr,
                    gbr_n,
                    gbr_neg,
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
        if staged_at >= 0:
            cnt[C_SSESS] += 1
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
            gbr_neg,
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
    scal[SC_DEFEXIT] = cnt[C_DEFEXIT]
    scal[SC_SEVENTS] = cnt[C_SEVENTS]
    scal[SC_SSESS] = cnt[C_SSESS]
    scal[SC_SCAP] = cnt[C_SCAP]
    scal[SC_SGATE] = cnt[C_SGATE]
    scal[SC_STGT] = cnt[C_STGT]
    return nav, dec, scal, yr, gbr, gbr_n, gbr_neg, has_due


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


def simulate_x(
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
    dec_exit=None,
    stage=None,
    w_early=None,
    hold=None,
    x2_gain=X2_GAIN,
    x2_cap=X2_CAP_SESSIONS,
):
    """`fe_sim.simulate` plus the X1/X2 options: the same dict, the same numbers.

    `arm` selects the fill rule (`RULE`); `mode` is "pre_tax" | "progressive" | "flat"; every arm
    label is driven by the target series `wants`, production R2's. `dec_exit`/`stage` default to
    the arm's `RULE` flags (so the X arms behave as pre-registered) and can be forced off for the
    parity cells; `w_early` (`0.5 x vt`) and `hold` (the early-conditions set) are X2's context
    arrays, `[T]` on the same session grid as `wants` (an X2 arm requires them).
    """
    spec = RULE["C0"] if arm in ("R2", "E2") else RULE[arm]
    # `dec_exit`/`stage` default to the arm's own flags; an explicit value can only *disable* a
    # device the arm has (the smoke identities), and `x2_gain`/`x2_cap` are the pre-registered
    # constants (exposed only so the smoke tests can make the device unreachable / the cap bind).
    dec_exit, stage = (
        bool(spec["dec_exit"]) if dec_exit is None else bool(dec_exit) and bool(spec["dec_exit"])
    ), (bool(spec["stage"]) if stage is None else bool(stage) and bool(spec["stage"]))
    if stage and (w_early is None or hold is None):
        raise ValueError("an X2 arm needs the w_early / hold context arrays")
    if w_early is None:
        w_early = np.zeros(0, dtype=np.float64)
    if hold is None:
        hold = np.zeros(0, dtype=np.bool_)
    mode_i = MODES.index(mode) if isinstance(mode, str) else int(mode)
    if mode_i == MODE_PROGRESSIVE and not env.scale_ok:
        raise ValueError("progressive mode needs a YI-UFE table with the 2025-10 anchor")
    oq = np.ascontiguousarray(np.asarray(opens, dtype=np.float64).ravel())
    cq = np.ascontiguousarray(np.asarray(closes, dtype=np.float64).ravel())
    wq = np.ascontiguousarray(np.asarray(wants, dtype=np.float64).ravel())
    wa = np.ascontiguousarray(np.asarray(w_early, dtype=np.float64).ravel())
    ho = np.ascontiguousarray(np.asarray(hold, dtype=np.bool_)).ravel()
    nav, dec, scal, yr, gbr, gbr_n, gbr_neg, has_due = sim_one(
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
        bool(dec_exit),
        bool(stage),
        wa,
        ho,
        float(x2_gain),
        int(x2_cap),
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
    dexit = {int(k + env.Y0): int(yr[YR_DEFEXIT, k]) for k in range(yr.shape[1]) if yr[YR_DEFEXIT, k]}
    reasons = {REASON_NAMES[r]: float(gbr[r]) for r in range(N_REASONS) if gbr_n[r] > 0}
    neg_reasons = {REASON_NAMES[r]: float(gbr_neg[r]) for r in range(N_REASONS) if gbr_neg[r] < 0}
    year_gain = {int(k + env.Y0): float(yr[YR_GAIN, k]) for k in range(yr.shape[1])}
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
        "deferred_exit": int(scal[SC_DEFEXIT]),
        "staged_events": int(scal[SC_SEVENTS]),
        "staged_sessions": int(scal[SC_SSESS]),
        "staged_release_cap": int(scal[SC_SCAP]),
        "staged_release_gate": int(scal[SC_SGATE]),
        "staged_release_target": int(scal[SC_STGT]),
        "postponements_by_year": dict(sorted(post.items())),
        "deferrals_by_year": dict(sorted(defer.items())),
        "exit_deferrals_by_year": dict(sorted(dexit.items())),
        "fees_usd": float(scal[SC_FEES]),
        "index_relief_try": float(scal[SC_IDXREL]),
        "realised_gain_try_by_reason": dict(sorted(reasons.items())),
        "neg_gain_try_by_reason": dict(sorted(neg_reasons.items())),
        "year_gain_try": dict(sorted(year_gain.items())),
        "_nav": nav,
        "_dec": dec if trace else None,
    }


# ----------------------------------------------------------------------------- study context
def exit_ctx(price, vol_instr, comps=None):
    """X2's decision inputs on the price series the rule is computed on.

    `w_early` = the early leg's own target `0.5 x vt` (`vt` from the instrument's vol series, the
    production early weight; cap-free because `0.5 x vt <= 0.5 < CAP`), and `hold` = the early
    leg's **three-condition set** (close > SMA50, vol20 below its 252-session median, close > close
    20 sessions ago) -- exactly the set whose failure the brief makes X2's immediate-full-exit
    trigger. Both are `[P, T]` and are read at index `i - 1` (the decision close) by the kernel.
    PREREGISTRATION.md sections 4.2/4.3 record the reading and its alternatives.
    """
    comps = fe_signals.early_components(price) if comps is None else comps
    sma50_up, vol_p, med_p, lag_cond = (np.atleast_2d(x) for x in comps)
    hold = sma50_up & np.isfinite(med_p) & (vol_p < med_p) & lag_cond
    return {"w_early": 0.5 * fe_signals.vt_of(np.atleast_2d(vol_instr)),
            "hold": np.asarray(hold, dtype=np.bool_)}


def arm_flags(arms):
    """(band, usd_rule, defer, dec_exit, stage) bool arrays for a list of arm labels."""
    return tuple(
        np.array([RULE[a][k] for a in arms], dtype=np.bool_)
        for k in ("band", "usd_rule", "defer", "dec_exit", "stage")
    )


# ----------------------------------------------------------------------------- the bootstrap
@njit(parallel=True, cache=True, nogil=True)
def _paths_cagr(
    oq,
    cq,
    wq,
    w_early,
    hold,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    cap,
    i0,
    i1,
    cost,
    Y0,
    M_lo,
    mode,
    band,
    usd_rule,
    defer,
    dec_exit,
    stage,
    x2_gain,
    x2_cap,
    rule_index,
    exponent,
    use_liq,
    out,
):
    """One chunk of paths x arms, `prange` over the paths (one path per iteration, arms inner).

    `out[p, a]` is the path's annualised pre-tax NAV CAGR (`use_liq=False`, `exponent = 252/k`) or
    its liquidated after-tax CAGR (`use_liq=True`, `exponent = 1/yrs`) -- the two conventions of
    `fe_boot._boot_chunk`. `w_early`/`hold` are the (W, T) X2 context rows of the chunk.
    """
    rate = np.zeros(0, dtype=np.float64)
    dbg0 = np.zeros((0, 6), dtype=np.float64)
    W = oq.shape[0]
    k = i1 - i0 + 1
    for p in prange(W):
        for a in range(band.shape[0]):
            nav, _d, scal, _y, _g, _gn, _gneg, _h = sim_one(
                oq[p],
                cq[p],
                wq[p],
                fx,
                upm,
                mth_of,
                yr_of,
                scale_y,
                dec_tail,
                pay_ptr,
                pay_yr,
                pay_part,
                cap,
                i0,
                i1,
                cost,
                band[a],
                usd_rule[a],
                defer[a],
                dec_exit[a],
                stage[a],
                w_early[p],
                hold[p],
                x2_gain,
                x2_cap,
                mode,
                rule_index,
                Y0,
                M_lo,
                rate,
                False,
                dbg0,
            )
            if use_liq:
                out[p, a] = (scal[SC_LIQ] / cap) ** exponent - 1
            else:
                out[p, a] = (nav[k] / nav[0]) ** exponent - 1


def chunk_legs_x(q, y, pos):
    """`fe_boot.chunk_legs` plus the X2 context of each financing model.

    The reconstruction of the resampled price series `pc` repeats `chunk_legs`' four lines, so the
    X2 context is built on exactly the series `hold`/`w_early` are read from.
    """
    legs, e2, w_e2 = fe_boot.chunk_legs(q, y, pos)
    idx = pos + 1
    prev = q[1][idx - 1]
    r_on_p = q[0][idx] / prev
    r_id = q[1][idx] / q[0][idx]
    pc = np.concatenate([np.ones((idx.shape[0], 1)), np.cumprod(r_on_p * r_id, axis=1)], axis=1)
    comps = fe_signals.early_components(pc)
    ctx = {
        "base": exit_ctx(pc, fe_signals.vol_n(legs["base"][1]), comps),
        "calibrated": exit_ctx(pc, fe_signals.vol_n(legs["calibrated"][1]), comps),
    }
    return legs, e2, w_e2, ctx


def boot_x(
    seed=SEED,
    block=BLOCK,
    arms=ARMS,
    n_pre=PRE_BOOT_PATHS,
    n_tax=TAX_BOOT_PATHS,
    capitals=("100k", "1M"),
    modes=("progressive", "flat"),
    anchor_e2=True,
    deltas=None,
    baselines=(CONTROL,),
    label="E1",
    m=DEFAULT_M,
    chunk=None,
    quiet=False,
    window=None,
    keep_paths=False,
):
    """`fe_boot.boot` with this study's arms: the same dict, the same published cells.

    The path draws replay `fe_boot`'s rng sequence (same seed/block/path count/chunk) and the
    per-path simulation is `_paths_cagr` over this study's `sim_one`. With the base arms and the
    X options off every cell is bit-identical to `fe_boot.boot` (asserted in `--smoke`).
    """
    win = window if window is not None else fe_boot.BootWindow()
    env = win.env
    n_paths = max(n_pre, n_tax)
    cap_at = np.array([TAX_CAPITALS[c] for c in capitals], dtype=np.float64)
    band, usd_rule, defer, dec_exit, stage = arm_flags(arms)
    cost = COST_PER_SIDE * m
    mode_ids = np.array(
        [MODE_PROGRESSIVE if mode == "progressive" else MODE_FLAT for mode in modes], dtype=np.int64
    )
    if deltas is None:
        deltas = tuple(a for a in arms if a not in baselines)
    if chunk is None:
        chunk = DEFAULT_CHUNK
    pre = {(mdi, a): np.empty(n_pre, dtype=np.float64) for mdi in ("base", "calibrated") for a in arms}
    at = {cl: {mode: {a: np.empty(n_tax, dtype=np.float64) for a in arms} for mode in modes} for cl in capitals}
    anchor = {mode: np.empty(n_tax, dtype=np.float64) for mode in modes}
    rng = np.random.default_rng(seed)
    i0, i1 = win.i0, win.i1
    k = i1 - i0 + 1
    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, TAX_BOOT_L, win.npool, TAX_BOOT_EXT)
        legs, _e2, w_e2, ctx = chunk_legs_x(win.q, win.y, pos)
        npre = max(0, min(W, n_pre - p0))
        ntax = max(0, min(W, n_tax - p0))
        for mdi in ("base", "calibrated"):
            if npre <= 0:
                continue
            o_, c_, want = legs[mdi]
            out = np.empty((W, len(arms)), dtype=np.float64)
            _paths_cagr(
                o_, c_, want, ctx[mdi]["w_early"], ctx[mdi]["hold"], env.fx, env.upm, env.mth_of,
                env.yr_of, env.scale_y, env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part,
                PRE_CAPITAL, i0, i1, cost, env.Y0, env.M_lo, MODE_PRE_TAX, band, usd_rule,
                defer, dec_exit, stage, X2_GAIN, X2_CAP_SESSIONS, True, 252.0 / k, False, out,
            )
            for ai, a in enumerate(arms):
                pre[(mdi, a)][p0 : p0 + npre] = out[:npre, ai]
        if ntax > 0:
            o_, c_, want = legs["base"]
            we, ho = ctx["base"]["w_early"], ctx["base"]["hold"]
            for ci, cl in enumerate(capitals):
                for mi, mode in enumerate(modes):
                    out = np.empty((W, len(arms)), dtype=np.float64)
                    _paths_cagr(
                        o_, c_, want, we, ho, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y,
                        env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, cap_at[ci], i0, i1,
                        cost, env.Y0, env.M_lo, mode_ids[mi], band, usd_rule, defer, dec_exit, stage,
                        X2_GAIN, X2_CAP_SESSIONS, True, 1.0 / win.yrs, True, out,
                    )
                    for ai, a in enumerate(arms):
                        at[cl][mode][a][p0 : p0 + ntax] = out[:ntax, ai]
            if anchor_e2:
                off = np.array([False], dtype=np.bool_)
                for mi, mode in enumerate(modes):
                    out = np.empty((W, 1), dtype=np.float64)
                    _paths_cagr(
                        o_, c_, w_e2, we, ho, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y,
                        env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, cap_at[0], i0, i1, cost,
                        env.Y0, env.M_lo, mode_ids[mi], off, off, off, off, off, X2_GAIN,
                        X2_CAP_SESSIONS, True, 1.0 / win.yrs, True, out,
                    )
                    anchor[mode][p0 : p0 + ntax] = out[:ntax, 0]
        if not quiet:
            print(f"  {label} path {min(p0 + W, n_paths)}/{n_paths}", flush=True)

    out = {
        "n_paths_pre": n_pre,
        "n_paths_tax": n_tax,
        "seed": seed,
        "block": block,
        "arms": list(arms),
        "calendar_start": win.dates[0],
        "calendar_end": win.dates[-1],
        "pre": {},
        "tax": {},
    }
    for mdi in (("base", "calibrated") if n_pre > 0 else ()):  # no pre-tax paths -> no block
        base = pre[(mdi, CONTROL)]
        out["pre"][mdi] = {}
        for arm in arms:
            c = pre[(mdi, arm)]
            d = c - base
            out["pre"][mdi][arm] = {
                "cagr_median": float(np.median(c)),
                "diff_vs_C0_median_pp": float(np.median(d) * 100),
                "diff_vs_C0_mean_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100),
                "p95_delta_pp": float(np.percentile(d, 95) * 100),
            }
        for ctrl in baselines:
            base = pre[(mdi, ctrl)]
            for arm in deltas:
                if arm == ctrl:
                    continue
                d = pre[(mdi, arm)] - base
                out["pre"][mdi][f"{arm}_vs_{ctrl}"] = {
                    "median_delta_pp": float(np.median(d) * 100),
                    "mean_delta_pp": float(np.mean(d) * 100),
                    "share_gt_0": float(np.mean(d > 0)),
                    "p05_pp": float(np.percentile(d, 5) * 100),
                    "p95_pp": float(np.percentile(d, 95) * 100),
                }
    for cl in capitals:
        out["tax"][cl] = {}
        for mode in modes:
            e = {"n_paths": n_tax, "capital_usd": TAX_CAPITALS[cl]}
            for arm in arms:
                e[arm] = {"cagr_median": float(np.median(at[cl][mode][arm]))}
            for ctrl in baselines:
                base = at[cl][mode][ctrl]
                for arm in deltas:
                    if arm == ctrl:
                        continue
                    d = at[cl][mode][arm] - base
                    e[f"{arm}_vs_{ctrl}"] = {
                        "median_delta_cagr_pp": float(np.median(d) * 100),
                        "mean_delta_cagr_pp": float(np.mean(d) * 100),
                        "share_gt_0": float(np.mean(d > 0)),
                        "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                        "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100),
                    }
            out["tax"][cl][mode] = e
    out["anchor_e2_100k"] = {
        mode: ({"cagr_median": float(np.median(anchor[mode]))} if anchor_e2 else {}) for mode in modes
    }
    if keep_paths:
        out["paths"] = {"pre": pre, "tax": at, "anchor_e2": anchor}
    return out


def boot_oos_market_x(key, rng, arms):
    """Pooled-OOS bootstrap of one market: `screen.boot_oos_market`'s draws, this study's arms.

    Draws, chunk size (500) and reconstruction are `fast_reentry_v1.bootstrap_market`'s (the shared
    generator, markets in order); every arm runs production R2's target series with the R-USD rule
    (`rule_index=False`, pre-tax, capital 1.0, `i1 = OOS_BOOT_L`), and X2 reads the three-condition
    `exit_ctx` on the resampled price series (v1's `boot_oos_market` used an SMA50-only proxy there
    -- see PREREGISTRATION.md A0).
    """
    days, o, c, y, start = fe_data.load_market(key)
    pool = np.arange(start, len(days))
    npool = len(pool)
    env = RunEnv(days, *fe_data.usd_market(days), START, OOS_BOOT_L)
    band, usd_rule, defer, dec_exit, stage = arm_flags(arms)
    acc = {a: [] for a in arms}
    for chunk in range(0, OOS_PATHS_PER_MARKET, OOS_CHUNK):
        P = min(OOS_CHUNK, OOS_PATHS_PER_MARKET - chunk)
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
        e2 = fe_signals.e2_up(cc)
        so, sc = fe_signals.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        comps = fe_signals.early_components(cc)
        _w_e2, w_r2 = fe_signals.wants_of(cc, e2, fe_signals.vol_n(sc), comps)
        ctx = exit_ctx(cc, fe_signals.vol_n(sc), comps)
        out = np.empty((P, len(arms)), dtype=np.float64)
        _paths_cagr(
            so, sc, w_r2, ctx["w_early"], ctx["hold"], env.fx, env.upm, env.mth_of, env.yr_of,
            env.scale_y, env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, 1.0, START,
            OOS_BOOT_L, COST_PER_SIDE * M, env.Y0, env.M_lo, MODE_PRE_TAX, band, usd_rule,
            defer, dec_exit, stage, X2_GAIN, X2_CAP_SESSIONS, False, 252.0 / OOS_BOOT_L,
            False, out,
        )
        for ai, a in enumerate(arms):
            acc[a].append(out[:, ai])
    return {a: (np.concatenate(v) if v else np.zeros(0)) for a, v in acc.items()}
