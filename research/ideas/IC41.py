"""IC41 -- dual-core: QLD + SSO, one FIFO book per index. **NEEDS_KERNEL (all three arms).**

Per `docs/research/idea_bank_v2.json` IC41 (`category: two-leg`, `main_window: full`, arms frozen):

    A  each leg carries 0.5 of the target; downsizes fill in the leg with the lower USD FIFO gain
       fraction (route cap 10 pp), up-resizes in the leg further below its share
    B  leg weights proportional to 1/vol20 of each index, same routing as A
    C  as A, but the early path (half weight) is held only in the Nasdaq leg

**Why it cannot be screened here.** The account is a two-leg account (available: one cash pool,
one FIFO book per leg, one taxpayer, and the gain-aware router `route_cap_pp` the arms ask for),
but leg B must be an **S&P-500 2x book**: the bank's `plugin.detail` is `second_symbol=SSO` with
"the S&P leg is synthetic 2x from SPY before 2006-06 and the real SSO afterwards in the
cross-check". The framework's panel builds the `2x` leg **only from the window's own underlying**
(`research/batch_screen/api.py::panel` -> `fe_signals.synthetic_2x` on the window's own
open/close/dividend/rate triple, gearing exactly 2, QLD*'s expense), and an extra-data key used as
`leg_b` is held **at gearing 1**: `leg_series` returns that key's single adjusted-close column as
both the fill and the close price, with no gearing, no financing and no expense plumbing. So a
SPY-based 2x leg is a *new leg construction* (a second data triple + the levered recurrence in the
kernel), the same boundary the framework names for IC40, and `strength_rank 9` or not, it is a
separate kernel study with its own parity cell.

The cheap-looking alternative -- registering the real fund (a Yahoo `SSO` download, held at
gearing 1) -- is rejected here on purpose, and the reason is the arm's own purpose: SSO's adjusted
close carries the fund's own expense ratio and tracking, i.e. a *different* leg convention from the
campaign's synthetic 2x leg every published number uses, mixed into one account against a synthetic
QLD* book; and SSO starts 2006-06-21, which drops 2000-2006 -- exactly the 2000-02 concentration
episode the arm exists to dilute ("Two equally leveraged but 0.9-correlated books dilute the single
index concentration (the 2000-02 problem)"). An SSO-anchored reduced window would answer a
different question from the bank's, so it is not screened as if it were this one.

The frozen arm parameters are recorded below so the trial is registered with its spec (the runner
counts it in the multiple-testing ledger and simulates no arm). The follow-up kernel study needs
what the bank's `new_downloads` names: the Yahoo `SSO` series (plus SPY's dividend/OHLC triple for
the synthetic-2x cross-check), a `leg` construction for an arbitrary index at 2x, and the router
the framework already carries.
"""

from __future__ import annotations

from research.batch_screen import api

NEEDS_KERNEL = (
    "leg B must be a 2x S&P-500 book (synthetic daily-reset 2x from SPY, the campaign's leg "
    "convention / the real SSO in the cross-check): the panel builds the 2x leg only from the "
    "window's own underlying, and an extra-data leg is held at gearing 1 without financing or "
    "expense plumbing -- a second leg family (data triple + levered recurrence in the kernel), the "
    "IC40 boundary, not a plugin"
)

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "sso", "cash_yield": "none",
          "split": "equal", "leg_b_gearing": 2, "route_cap_pp": 10.0,
          "route_score": "usd", "up_resize": "furthest_below_share", "kernel_scope": "all_stages"},
    "B": {"rule": "K", "leg": "2x", "leg_b": "sso", "cash_yield": "none",
          "split": "inverse_vol20", "leg_b_gearing": 2, "route_cap_pp": 10.0,
          "route_score": "usd", "up_resize": "furthest_below_share", "kernel_scope": "all_stages"},
    "C": {"rule": "K", "leg": "2x", "leg_b": "sso", "cash_yield": "none",
          "split": "equal", "early_leg": "nasdaq_only", "leg_b_gearing": 2,
          "route_cap_pp": 10.0, "route_score": "usd", "kernel_scope": "all_stages"},
}

def targets(data, spec):
    """Never reached: every arm of this module is registered `needs_kernel` and refused."""
    raise api.NeedsKernel(NEEDS_KERNEL)
