"""fe2_opts -- `fe2_sim.sim2` with five research options: `score_mode`, `band4`, `route_cap`,
`net_frac`, `net_min_ytd`.

`sim2g` is `fe2_sim.sim2` verbatim plus the option arguments, which default to the OFF values
(`score_mode=0, band4=0.0, route_cap=0.0, net_frac=0.0, net_min_ytd=0.0`) and with them off are
**bit-identical** to `sim2` on every input (`fe2_opts_parity.py` asserts it on the real window and
on the bootstrap). The per-leg primitives, the index constants and the result-dict tail are
imported from `fe2_sim`, so there is exactly one implementation of each.

The five options, and what they are for:

* `score_mode` (int, 0/1/2) replaces only the *gain fraction that keys the A1u downsize band*
  (the pass-2 `g_frac`). 0 is today's rule (the leg's own USD fraction when the arm's `usd_rule`
  is on, else the TRY fraction through `_rule_sale`); 1 is the **TRY-indexed** FIFO gain fraction
  of the same units, always with indexation on; 2 is `max(usd_fraction, indexed_fraction)`. The
  band widths come from `fe_sim.gain_band`, except that with `band4 > 0` and `g_frac > 0.15` the
  width is `band4` -- a pre-registered fourth step, whose 0.15 threshold is fixed, not a knob. The
  test itself is unchanged: `drift[l] <= width` blocks the leg (reason 2).
* `route_cap` (weight fraction, 0 = off) is a per-session downsize router: when exactly the two
  legs both want to sell, the units are moved from the leg with the *larger* TRY-indexed gain
  fraction to the one with the smaller (up to `route_cap` of the post-cost NAV, and never past
  either leg's own sale: `x = min(-du[other], un[cheap] + du[cheap], route_cap * nav_after /
  oq[cheap, i])`, and both legs must have a positive open price to route at all). It routes
  **units**, not cash, and the routed quantities are what the band test and the fill use, so the
  cash of a routed session differs from the un-routed fill by the two instruments' price ratio;
  the cap bounds both legs' deviation from their own target weight. Routing never flips the
  `trade` decision of a leg -- `trade[l]`, the H2 deferral and the December rules are untouched --
  but a leg whose whole sale is moved becomes a no-op fill (`|d| <= 1e-12`), so the *fill* count
  can fall while `trade[l]` stays set.
* `net_frac` / `net_min_ytd` (loss netting, 0 = off) fire after a leg's downsize fill realised a
  positive TRY gain in a taxed mode: they additionally sell, from the other leg, the FIFO lots
  that carry an indexed TRY loss, up to `net_frac` x that gain in realised loss, gated on the
  year's net TRY gain so far exceeding `net_min_ytd` (`net_min_ytd = 0` = no gate). The netting
  sale is reason 3 (`R_NET`), a reason that only exists here.

The netting walk selects the loss lots in FIFO order and sums their units; the sale then executes
them as one `_sell_leg` call, because the engine's FIFO book can only consume from the head -- a
profitable lot that sits before a loss lot is therefore consumed by the netting sale rather than
skipped (the walk's skip only decides which lots *count* towards the loss budget). The netting sale
happens in the same session as the fill that triggered it, in the legs' own order: a leg whose own
fill is executed *after* the netting sale sees the reduced units, so its recomputed target
difference already includes the netting (a leg filled before the netting sale is not revisited).
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from research.fast_engine import fe_sim, fe_tax
from research.fast_engine.fe_tax import (
    MODE_FLAT,
    MODE_PRE_TAX,
    MODE_PROGRESSIVE,
    MODES,
    R_LIQUIDATION,
    R_RESIZE,
    R_TAX,
)

from .fe2_sim import (
    C_BANDBLK,
    C_DEFER,
    C_POSTP,
    C_QSELLS,
    C_TAXSALES,
    C_TRADES,
    L_BANDBLK,
    L_DEFER,
    L_FEES,
    L_POSTP,
    L_QSELLS,
    L_TAXSALES,
    L_TRADES,
    L_TURNOVER,
    L_UNITS_END,
    LEG_A,
    LEG_B,
    N_CNT,
    N_LEGSCAL,
    N_LEGS,
    N_SCAL,
    N_ST,
    N_YR,
    N_YR2,
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
    S_CASH,
    S_FEES,
    S_IDXREL,
    S_TPUSD,
    YR_DEFER,
    YR_DUE,
    YR_GAIN,
    YR_PAID,
    YR_POST,
    YR2_DEFER,
    YR2_POST,
    _buy_leg,
    _outputs,
    _sell_leg,
)

# the netting reason, appended to the engine's three
R_NET = fe_sim.N_REASONS
REASON_NAMES_OPTS = fe_sim.REASON_NAMES + ("net",)
N_REASONS_OPTS = fe_sim.N_REASONS + 1

# the fourth band step's fixed gain-fraction threshold (a pre-registered constant, not a knob)
BAND4_GATE = 0.15

# ----------------------------------------------------------------------------- knobs
KNOB_KEYS = ("band_score", "band_step4_pp", "route_cap_pp", "loss_net_frac", "loss_net_min_ytd_try")
SCORE_MODES = {"usd": 0, "indexed": 1, "max": 2}
KNOBS_OFF = {"band_score": "usd", "band_step4_pp": 0.0, "route_cap_pp": 0.0, "loss_net_frac": 0.0,
             "loss_net_min_ytd_try": 0.0}

def check_knobs(knobs):
    """Validate a knobs dict (`None`/`{}` = every option off); unknown key or bad value raises.

    Returns the canonical dict (every key present, percent points still in pp).
    """
    out = dict(KNOBS_OFF)
    if knobs:
        unknown = sorted(set(knobs) - set(KNOB_KEYS))
        if unknown:
            raise ValueError(f"unknown fill knob(s) {unknown}; known: {list(KNOB_KEYS)}")
        out.update(knobs)
    if out["band_score"] not in SCORE_MODES:
        raise ValueError(f"band_score must be one of {sorted(SCORE_MODES)}; got "
                         f"{out['band_score']!r}")
    for key in KNOB_KEYS[1:]:
        v = out[key]
        if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
            raise ValueError(f"{key} must be a number; got {v!r}")
        if not np.isfinite(float(v)) or float(v) < 0.0:
            raise ValueError(f"{key} must be finite and >= 0; got {v!r}")
    if out["loss_net_frac"] > 1.0:
        raise ValueError(f"loss_net_frac must be a fraction in [0, 1]; got "
                         f"{out['loss_net_frac']!r}")
    return out

def knob_values(knobs=None):
    """A knobs dict -> the kernel's five scalars `(score_mode, band4, route_cap, net_frac,
    net_min_ytd)`, with the percent-point keys converted to fractions."""
    k = check_knobs(knobs)
    return (SCORE_MODES[k["band_score"]], float(k["band_step4_pp"]) / 100.0,
            float(k["route_cap_pp"]) / 100.0, float(k["loss_net_frac"]),
            float(k["loss_net_min_ytd_try"]))

def knob_arrays(arms):
    """The kernel's knob arrays for a list of arm dicts.

    Each arm carries `"fill_knobs"` (a `simulate2` knobs dict; absent/`None` = off) and
    `"fill_knobs_pretax"` (bool: the knobs also apply to the pre-tax phase). Returns
    `(score_modes, band4s, route_caps, net_fracs, net_min_ytds, knobs_pre)`.
    """
    n = len(arms)
    score_modes = np.zeros(n, dtype=np.int64)
    band4s = np.zeros(n, dtype=np.float64)
    route_caps = np.zeros(n, dtype=np.float64)
    net_fracs = np.zeros(n, dtype=np.float64)
    net_min_ytds = np.zeros(n, dtype=np.float64)
    knobs_pre = np.zeros(n, dtype=np.bool_)
    for i, arm in enumerate(arms):
        s, b4, rc, nf, nm = knob_values(arm.get("fill_knobs"))
        score_modes[i] = s
        band4s[i] = b4
        route_caps[i] = rc
        net_fracs[i] = nf
        net_min_ytds[i] = nm
        knobs_pre[i] = bool(arm.get("fill_knobs_pretax", False))
    return score_modes, band4s, route_caps, net_fracs, net_min_ytds, knobs_pre

# ----------------------------------------------------------------------------- the session loop
@njit(cache=True, nogil=True)
def sim2g(
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
    score_mode,
    band4,
    route_cap,
    net_frac,
    net_min_ytd,
):
    """`fe2_sim.sim2` plus the five research options (see the module docstring).

    `score_mode` is 0/1/2 (`usd`/`indexed`/`max`), `band4` a band width in weight fraction,
    `route_cap` a weight fraction, `net_frac` a fraction of the downsize gain and `net_min_ytd` a
    TRY floor on the year's net gain; every one of them 0 means "off" and reproduces `sim2`
    exactly. `gbr`/`gbr_n` have one extra column (`R_NET`) that stays empty with the options off.
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
    gbr = np.zeros((N_LEGS, N_REASONS_OPTS), dtype=np.float64)
    gbr_n = np.zeros((N_LEGS, N_REASONS_OPTS), dtype=np.int64)
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
    fr = np.zeros(N_LEGS, dtype=np.float64)  # the router's per-leg indexed gain fractions

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

        # ---- pass 2a: the arm's H2 December deferral per leg (today's first branch, unchanged)
        for l in range(N_LEGS):
            if trade[l] and tgt[l] > 1e-12:
                if defer and dec_tail[i] and tgt[l] < w[l]:
                    trade[l] = False
                    blocked[l] = 1

        # ---- pass 2b: the optional downsize router (unit moves between the two legs)
        routed = False
        if route_cap > 0.0:
            both = trade[LEG_A] and trade[LEG_B]
            for l in range(N_LEGS):
                if not (tgt[l] > 1e-12 and du[l] < -1e-12 and oq[l, i] > 0):
                    both = False
            if both:
                for l in range(N_LEGS):
                    g_r, p_r = fe_sim._rule_sale(lu[l], lp[l], lf[l], lm[l], lh[l], fx, upm,
                                                 mth_of, M_lo, i, oq[l, i], -du[l], fee_leg[l], True)
                    fr[l] = g_r / p_r if p_r > 0 else 0.0
                if fr[LEG_A] <= fr[LEG_B]:
                    cheap = LEG_A
                    other = LEG_B
                else:
                    cheap = LEG_B
                    other = LEG_A
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
                    for l in range(N_LEGS):
                        if -du[l] > 0:
                            drift[l] = (-du[l]) * oq[l, i] / nav_after
                        else:
                            drift[l] = 0.0
                        fee_leg[l] = nav_open * cost * drift[l]
                    routed = True

        # ---- pass 2c: the A1u gain-scaled downsize band per leg, on that leg's own lot book
        for l in range(N_LEGS):
            if trade[l] and tgt[l] > 1e-12 and du[l] < -1e-12 and band_on:
                if score_mode == 0:
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
                elif score_mode == 1:
                    gain, proceeds = fe_sim._rule_sale(lu[l], lp[l], lf[l], lm[l], lh[l], fx, upm,
                                                       mth_of, M_lo, i, oq[l, i], -du[l],
                                                       fee_leg[l], True)
                    if proceeds > 0:
                        g_frac = gain / proceeds
                    else:
                        g_frac = 0.0
                else:
                    gain, proceeds = fe_sim._rule_sale_usd(lu[l], lp[l], lh[l], i, oq[l, i],
                                                           -du[l], fee_leg[l])
                    if proceeds > 0:
                        g_frac = max(0.0, gain) / proceeds
                    else:
                        g_frac = 0.0
                    gain, proceeds = fe_sim._rule_sale(lu[l], lp[l], lf[l], lm[l], lh[l], fx, upm,
                                                       mth_of, M_lo, i, oq[l, i], -du[l],
                                                       fee_leg[l], True)
                    if proceeds > 0:
                        g_i = gain / proceeds
                        if g_i > g_frac:
                            g_frac = g_i
                width = fe_sim.gain_band(g_frac)
                if band4 > 0.0 and g_frac > BAND4_GATE:
                    width = band4
                if drift[l] <= width:
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
                if routed:
                    d = du[l]  # a routed session fills the routed units, not the recomputed ones
                scal[SC_TURNOVER] = scal[SC_TURNOVER] + drift[l]
                legscal[l, L_TURNOVER] = legscal[l, L_TURNOVER] + drift[l]
                if d > 1e-12:
                    _buy_leg(st, un, l, lu, lp, lf, lm, lh, fx, mth_of, i, oq[l, i], d * oq[l, i],
                             fee_leg[l])
                    legscal[l, L_FEES] = legscal[l, L_FEES] + fee_leg[l]
                elif d < -1e-12:
                    g_sell = _sell_leg(
                        st, un, l, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm, mth_of,
                        M_lo, i, oq[l, i], -d, fee_leg[l], R_RESIZE, index_on, yr_of, Y0,
                    )
                    legscal[l, L_FEES] = legscal[l, L_FEES] + fee_leg[l]
                    cnt[l, C_QSELLS] += 1
                    # ---- loss netting: harvest the other leg's indexed-loss lots
                    if net_frac > 0.0 and mode != MODE_PRE_TAX and g_sell > 0.0:
                        o = 1 - l
                        if un[o] > 0 and gain_row[yi - Y0] > net_min_ytd:
                            p_o = oq[o, i]
                            budget = net_frac * g_sell
                            nk = lh[o, 0]
                            nk_end = lh[o, 1]
                            nqty = 0.0
                            while nk < nk_end and budget > 0.0:
                                nu = lu[o, nk]
                                if nu > 0:
                                    npu = (nu * p_o - nu * p_o * cost) / nu  # the sale's per unit
                                    nrise = upm[mth_of[i] - M_lo] / upm[lm[o, nk] - M_lo] - 1
                                    if nrise < fe_tax.INDEX_MIN_RISE:
                                        nrise = 0.0
                                    npl = lp[o, nk] * lf[o, nk] * (1 + nrise) - npu * fx[i]
                                    if npl < 0.0:
                                        nloss = -npl
                                        if nu * nloss <= budget:
                                            nqty = nqty + nu
                                            budget = budget - nu * nloss
                                        else:
                                            nqty = nqty + budget / nloss
                                            budget = 0.0
                                nk = nk + 1
                            if nqty > un[o]:
                                nqty = un[o]
                            if nqty > 0:
                                _sell_leg(
                                    st, un, o, gbr, gbr_n, gain_row, lu, lp, lf, lm, lh, fx, upm,
                                    mth_of, M_lo, i, p_o, nqty, nqty * p_o * cost, R_NET,
                                    index_on, yr_of, Y0,
                                )
                                legscal[o, L_FEES] = legscal[o, L_FEES] + nqty * p_o * cost
                                cnt[o, C_QSELLS] += 1
                                if nav_open > 0:
                                    scal[SC_TURNOVER] = scal[SC_TURNOVER] + nqty * p_o / nav_open
                                    legscal[o, L_TURNOVER] = (legscal[o, L_TURNOVER]
                                                              + nqty * p_o / nav_open)
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
    knobs=None,
):
    """`fe2_sim.simulate2` with the option knobs: `knobs=None`/`{}` is every option off.

    Any other key or value raises `ValueError`. The result dict is `fe2_sim._outputs`'s, with
    `R_NET` added to the reason names, so it compares cell by cell with `fe2_sim.simulate2`'s.
    """
    score_mode, band4, route_cap, net_frac, net_min_ytd = knob_values(knobs)
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
    nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2 = sim2g(
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
        score_mode,
        band4,
        route_cap,
        net_frac,
        net_min_ytd,
    )
    if env.ufe_missing and not (np.isfinite(nav).all() and np.isfinite(scal).all()):
        raise ValueError(f"a YI-UFE month is missing from the table: {env.ufe_missing[:5]}")
    return _outputs(nav, dec, scal, yr, gbr, gbr_n, has_due, legscal, yr2, env, capital, i0, i1,
                    trace, n_reasons=N_REASONS_OPTS, reason_names=REASON_NAMES_OPTS)

# ----------------------------------------------------------------------------- kernel driver
@njit(parallel=True, cache=True, nogil=True)
def boot_chunk2g(
    ob,
    cb,
    wb,
    oc,
    cc,
    wc,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    cap_pre,
    cap_at,
    i0,
    i1,
    cost,
    yrs,
    Y0,
    M_lo,
    band,
    usd_rule,
    defer,
    rule_index,
    mode_ids,
    cash_rate,
    p0,
    n_pre,
    n_tax,
    score_modes,
    band4s,
    route_caps,
    net_fracs,
    net_min_ytds,
    knobs_pre,
    dbg0,
    out_pre,
    out_at,
):
    """`fe2_boot._boot_chunk2` calling `sim2g`: one chunk of optioned two-leg paths.

    The five per-arm knob arrays are `(n_arms,)`; `knobs_pre[a]` says whether arm `a`'s knobs also
    apply to the pre-tax phase -- if it is `False` the pre-tax block runs that arm with every
    option off (0/0/0/0/0) while the tax block always uses the arm's own values.
    """
    W = ob.shape[0]
    n_arms = wb.shape[0]
    n_caps = out_at.shape[0]
    n_modes = out_at.shape[1]
    k = i1 - i0 + 1
    for p in prange(W):
        pg = p0 + p
        if pg < n_pre:
            for a in range(n_arms):
                sm = score_modes[a]
                b4 = band4s[a]
                rc = route_caps[a]
                nf = net_fracs[a]
                nm = net_min_ytds[a]
                if not knobs_pre[a]:
                    sm = 0
                    b4 = 0.0
                    rc = 0.0
                    nf = 0.0
                    nm = 0.0
                nav, _d, _s, _y, _g, _gn, _h, _ls, _y2 = sim2g(
                    ob[p], cb[p], wb[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                    pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a], defer[a],
                    MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0, sm, b4, rc, nf, nm,
                )
                out_pre[0, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
                nav, _d, _s, _y, _g, _gn, _h, _ls, _y2 = sim2g(
                    oc[p], cc[p], wc[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                    pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a], defer[a],
                    MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0, sm, b4, rc, nf, nm,
                )
                out_pre[1, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
        if pg < n_tax:
            for ci in range(n_caps):
                for mi in range(n_modes):
                    mode = mode_ids[mi]
                    for a in range(n_arms):
                        _nav, _d, scal, _y, _g, _gn, _h, _ls, _y2 = sim2g(
                            ob[p], cb[p], wb[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                            pay_ptr, pay_yr, pay_part, cap_at[ci], i0, i1, cost, band[a], usd_rule[a],
                            defer[a], mode, rule_index, Y0, M_lo, cash_rate, False, dbg0,
                            score_modes[a], band4s[a], route_caps[a], net_fracs[a], net_min_ytds[a],
                        )
                        out_at[ci, mi, a, p] = (scal[SC_LIQ] / cap_at[ci]) ** (1 / yrs) - 1

def boot_chunk2g_off(
    ob,
    cb,
    wb,
    oc,
    cc,
    wc,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    cap_pre,
    cap_at,
    i0,
    i1,
    cost,
    yrs,
    Y0,
    M_lo,
    band,
    usd_rule,
    defer,
    rule_index,
    mode_ids,
    cash_rate,
    p0,
    n_pre,
    n_tax,
    dbg0,
    out_pre,
    out_at,
):
    """`boot_chunk2g` with every option off (`fe2_boot._boot_chunk2`'s signature, same numbers)."""
    n = wb.shape[0]
    zeros_i = np.zeros(n, dtype=np.int64)
    zeros_f = np.zeros(n, dtype=np.float64)
    boot_chunk2g(
        ob, cb, wb, oc, cc, wc, fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr, pay_yr,
        pay_part, cap_pre, cap_at, i0, i1, cost, yrs, Y0, M_lo, band, usd_rule, defer, rule_index,
        mode_ids, cash_rate, p0, n_pre, n_tax, zeros_i, zeros_f, zeros_f, zeros_f, zeros_f,
        np.zeros(n, dtype=np.bool_), dbg0, out_pre, out_at,
    )

__all__ = [
    "BAND4_GATE",
    "KNOBS_OFF",
    "KNOB_KEYS",
    "N_REASONS_OPTS",
    "R_NET",
    "REASON_NAMES_OPTS",
    "SCORE_MODES",
    "boot_chunk2g",
    "boot_chunk2g_off",
    "check_knobs",
    "knob_arrays",
    "knob_values",
    "sim2g",
    "simulate2",
]
