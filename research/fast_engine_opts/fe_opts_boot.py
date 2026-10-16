"""fe_opts_boot -- the framework's two bootstrap drivers over `sim_one_opts`.

`chunk_opts` and `pt_opts` are `research/batch_screen/boot.py:_chunk` / `_pt` verbatim, with the
per-arm option bundle (the `fe_opts_arrays.Bundle` of the bridge) carried into the kernel instead
of only the rule's three booleans. The drivers, the path draws and the panel construction are the
framework's (`batch_screen.boot`), so an options-off bundle is the framework's own driver bit for
bit -- `parity_opts.off_boot_parity` asserts exactly that cell for cell.

A bundle holds `(n_arms, ...)` arrays: `optf`, `opti` (`fe_opts.FLOAT_OPTS` / `INT_OPTS`), `fill`,
`mrate`, `early` (two-dimensional, the second axis `T` or `0` when no arm in the group uses them)
and `wvol` (`(n_arms, W, T)`, the last axis `0` when no arm runs the governor). A row of a
zero-width array is a zero-length array, which is what switches the mechanism off inside the
kernel -- there is no `None` in numba.
"""

from __future__ import annotations

from numba import njit, prange

from research.fast_engine import fe_sim
from research.fast_engine.fe_tax import MODE_PRE_TAX

from .fe_opts_sim import sim_one_opts


@njit(parallel=True, cache=True, nogil=True)
def chunk_opts(
    oq_b, cq_b, wq_b, oq_c, cq_c, wq_c, fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
    pay_yr, pay_part, cap_pre, cap_at, i0, i1, cost, yrs, Y0, M_lo, band, usd_rule, defer,
    rule_index, mode_ids, cash_rate, p0, n_pre, n_tax, dbg0, out_pre, out_at,
    optf_b, opti_b, fill_b, mrate_b, early_b, wvol_b,
    optf_c, opti_c, fill_c, mrate_c, early_c, wvol_c,
):
    """`batch_screen.boot._chunk` with a per-arm, per-model option bundle."""
    W = oq_b.shape[0]
    n_arms = wq_b.shape[0]
    n_caps = out_at.shape[0]
    n_modes = out_at.shape[1]
    k = i1 - i0 + 1
    for p in prange(W):
        pg = p0 + p
        if pg < n_pre:
            for a in range(n_arms):
                nav, _d, _s, _y, _g, _gn, _h = sim_one_opts(
                    oq_b[p], cq_b[p], wq_b[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                    pay_ptr, pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a],
                    defer[a], MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0,
                    optf_b[a], opti_b[a], fill_b[a], mrate_b[a], early_b[a], wvol_b[a, p],
                )
                out_pre[0, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
                nav, _d, _s, _y, _g, _gn, _h = sim_one_opts(
                    oq_c[p], cq_c[p], wq_c[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                    pay_ptr, pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a],
                    defer[a], MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0,
                    optf_c[a], opti_c[a], fill_c[a], mrate_c[a], early_c[a], wvol_c[a, p],
                )
                out_pre[1, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
        if pg < n_tax:
            for ci in range(n_caps):
                for mi in range(n_modes):
                    mode = mode_ids[mi]
                    for a in range(n_arms):
                        _nav, _d, scal, _y, _g, _gn, _h = sim_one_opts(
                            oq_b[p], cq_b[p], wq_b[a, p], fx, upm, mth_of, yr_of, scale_y,
                            dec_tail, pay_ptr, pay_yr, pay_part, cap_at[ci], i0, i1, cost, band[a],
                            usd_rule[a], defer[a], mode, rule_index, Y0, M_lo, cash_rate, False,
                            dbg0, optf_b[a], opti_b[a], fill_b[a], mrate_b[a], early_b[a],
                            wvol_b[a, p],
                        )
                        out_at[ci, mi, a, p] = (scal[fe_sim.SC_LIQ] / cap_at[ci]) ** (1 / yrs) - 1


@njit(parallel=True, cache=True, nogil=True)
def pt_opts(
    so, sc, wq, fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr, pay_yr, pay_part, capital,
    i0, i1, cost, band, usd_rule, defer, rule_index, Y0, M_lo, cash_rate, dbg0, out,
    optf, opti, fill, mrate, early, wvol,
):
    """`batch_screen.boot._pt` with the option bundle (the pooled OOS bootstrap)."""
    k = i1 - i0 + 1
    n_arms = wq.shape[0]
    for p in prange(so.shape[0]):
        for a in range(n_arms):
            nav, _d, _s, _y, _g, _gn, _h = sim_one_opts(
                so[p], sc[p], wq[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                pay_yr, pay_part, capital, i0, i1, cost, band[a], usd_rule[a], defer[a],
                MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0, optf[a], opti[a],
                fill[a], mrate[a], early[a], wvol[a, p],
            )
            out[a, p] = (nav[k] / nav[0]) ** (252 / k) - 1


__all__ = ["chunk_opts", "pt_opts"]
