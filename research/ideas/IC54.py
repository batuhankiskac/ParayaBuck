"""IC54 -- delayed intraday fill (auction vs continuous), batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  fills at the close of the first 30 minutes of the fill session      -> NEEDS_DATA
    B  fills at the first-30-minute VWAP of the fill session               -> NEEDS_DATA
    C  fills at the midpoint of the fill session's open-to-close range (a daily-data proxy)

**Arm C** is a kernel mechanism: `research/fast_engine_opts`'s `fill_mode = 1`, requested through a
registered rule, which replaces the fill reference (production: the session's open) with
`(open + close) / 2` of the *fill session*. The decision is still taken at the previous close, so
the position exists for the same session's drift -- the tested day-level delays
(`exec_delay_v1`) are a different object.

**Arms A and B are blocked on data**, not on the kernel: the mechanism they need
(`fill_mode = 2` plus a per-session `fill` array) is implemented and tested (`check_fill_reference`
in `research/fast_engine_opts`), but no 30-minute series exists for the window.  The bank's own
`new_downloads` names the two sources and neither is reachable here: Yahoo's chart API serves
`interval=30m` only for roughly the last 730 days (the fill sessions of a 2000-2026 window are
outside it, and it is the *fill price of every decision session* that is needed, not a recent
sample), and the Alpaca SIP bars API that the LIVE path consumes has no offline access in this
repository (no credentials, no vendored bars).  Arms A and B therefore stay declared and refused;
nothing is approximated (a daily-data stand-in for a 30-minute print is a different mechanism).

Readings fixed here: the targets are production K's; the fill reference is a kernel choice. No
extra data was added (a partial 730-day intraday sample would not make either arm evaluable).
"""

from __future__ import annotations

from research.batch_screen import rules
from research.fast_engine import fe_signals

#: no 30-minute bar series exists for the screening window; see the docstring
NEEDS_DATA_30M = (
    "the arm needs the fill session's first-30-minute price (close for A, VWAP for B) on every "
    "decision session of a 2000-2026 window; no such series exists in the repository and none can "
    "be sourced here -- the Yahoo chart API serves interval=30m for roughly the last 730 days "
    "only, and the Alpaca SIP bars API has no offline access (no credentials, no vendored bars). "
    "The kernel mechanism itself is implemented (`fill_mode = 2` + a `fill` array, "
    "fe_opts_sim/check_fill_reference), so this is a data gap, not a kernel one"
)

rules.register_rule("ic54_fill_mid", band=True, usd_rule=True, defer=True,
                    opts={"fill_mode": 1})

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "needs_kernel": NEEDS_DATA_30M},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "needs_kernel": NEEDS_DATA_30M},
    "C": {"rule": "ic54_fill_mid", "leg": "2x", "cash_yield": "none"},
}

def targets(data, spec):
    """Production K's target series (the fill reference is a kernel choice)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
