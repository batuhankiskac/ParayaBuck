"""IC40 -- S&P 500 2x on-state instrument (SSO / synthetic 2x SPY), batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  the arm's instrument = the synthetic daily-reset 2x of SPY; the vote stays QQQ's 2-of-3
    B  as A and the early leg is also 2x SPY (the unconditional early instrument change)
    C  as A but the vote is the 2-of-3 computed on the S&P index itself (signal and instrument
       aligned)

The mechanism is `leg = "2x_spy"` (`research/fast_engine_opts`: `fe_opts_legs.main_spy_triple` +
`bridge.prepare_panel`), i.e. the window's own SPY column put through the engine's daily-reset 2x
recurrence. On a window without an SPY series (the six OOS index windows, the 21 ETF windows) the
option leg degenerates to a copy of that window's own `2x` leg -- the bank's own reduced-OOS note
(the on-state leg is already 2x of the window's own index there), so arms A and B are
control-identical on those stages and only C differs (its vote is still the S&P's).

**Frozen readings.**
* Arm B's distinction from A cannot exist in a one-instrument account: production holds the *same*
  instrument in the on-state and in the early state, so naming the on-state instrument `2x_spy`
  already changes the early leg's instrument.  A per-state instrument switch is the framework's
  `NEEDS_KERNEL` boundary; B's spec is therefore A's (and the bank's own "also for the early leg"
  is what the fixed leg does).
* Arm C's signal: the **whole** production state machine (the 2-of-3 vote *and* the early path's
  SMA50 / vol-median / 20-session-lag components) is computed on the S&P price index -- the
  window's *own* price series when that window's underlying is the S&P itself (`M2`,
  `GSPC_1957_1999`; there the vote is production's own and the arm reduces to the control, which is
  the bank's documented degeneration), otherwise the registered `sp500` extra series (`^GSPC`),
  carried forward with `ffill=True` so a session the US market was closed for -- possible on the
  foreign OOS windows' calendars -- reads the last US close instead of a `NaN`.  The registered
  `^GSPC` series starts 1970-01-02, so `M3` (1965->, measured from 1966) is **refused**
  (`NotApplicable`) rather than voted on with a fabricated price; `M1`, `M4` and `M5` are covered.
  Sizing stays production's (`data.vol`, the held 2x_spy leg's vol20).

**Status: runnable.**  The option leg's panel is built by the framework's call sites with
`research.fast_engine_opts.bridge.base_leg(arm["leg"])` (`stages.panel_leg` for every
`windows.*_panel` call, and `smoke.real_panels`), and `stages.run_one` / `boot.boot` add the leg
through `bridge.prepare_panel` -- the plumbing bug (`research/plugins_d3/BUGS.md` B1) this arm was
declared `needs_kernel` for is fixed by batch_bank_v3's pre-screen amendment, so these three arms
run unchanged.  The mechanism itself is exercised by `check_spy_leg` in
`research/fast_engine_opts/checks.py` and by the framework's own regression test.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x_spy", "cash_yield": "none", "vote": "qqq"},
    "B": {"rule": "K", "leg": "2x_spy", "cash_yield": "none", "vote": "qqq"},
    "C": {"rule": "K", "leg": "2x_spy", "cash_yield": "none", "vote": "sp500",
          "inputs": ["sp500"]},
}

#: the windows whose own underlying *is* the S&P 500 index (the framework's own window keys; the
#: extra-data registry resolves its per-window series with the same vocabulary).  There the S&P
#: vote is the window's own price series, which also reaches back before the registered `^GSPC`
#: series starts (1970-01-02).
SP500_WINDOWS = ("GSPC_1957_1999", "M2")

def vote_price(data):
    """The S&P 500 price series arm C's state machine runs on (see the docstring)."""
    if data.window in SP500_WINDOWS:
        return data.price
    x = data.extra("sp500", ffill=True)
    if not np.isfinite(x).all():
        raise NotApplicable("the S&P 500 series does not cover this window (the registered ^GSPC "
                            "series starts 1970-01-02)")
    return x

def targets(data, spec):
    """The production state machine on the arm's signal series, sized on the held leg."""
    price = vote_price(data) if spec["vote"] == "sp500" else data.price
    e2 = fe_signals.e2_up(price)
    comps = fe_signals.early_components(price)
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    return np.atleast_2d(fe_signals.sized_wants(on_eff, factor, data.vol))
