"""IC57 -- early-close session handling, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  skip the decision on an early-close session (carry it to the next full session)
    B  decide on the previous full session's close and fill in the early-close session
    C  skip only downsizes on early-close sessions (entries/exits/up-resizes keep production)

Kernel mechanism: `research/fast_engine_opts`'s `early_mode` plus its NYSE early-close calendar
(`fe_opts_calendar`, the exchange's own 13:00 rule; the `early` flags are built by the bridge from
each window's session list, the plugin supplies nothing). `early_mode = 1` (A) makes the fill use
the newest decision that is not an early-close session, i.e. the decision is skipped and carried to
the next full session; `early_mode = 2` (C) declines only a *downsize* on an early-close session
(the gate has already run -- the deferred downsize is not cancelled).

**Arm B is the engine**: production already decides on the previous session's close and fills at the
*open* of the next session, and an early close shortens the *close* at 13:00, not the following
open. So arm B's rule is what `rule = "K"` does; it is registered as the arm text says, i.e. as the
engine's production execution layer, and it will report as its own trial.

Readings fixed here: the targets are production K's. The bank records the effect as nil on the OOS
windows (their own holiday calendars are not modelled), which the framework's rule-derived calendar
approximates on their own session lists; the main window and the after-tax stages carry the arm. No
extra data was added (the calendar is derived from the exchange's published rule by the kernel
package, not registered as a series).
"""

from __future__ import annotations

from research.batch_screen import rules
from research.fast_engine import fe_signals

rules.register_rule("ic57_skip_early", band=True, usd_rule=True, defer=True,
                    opts={"early_mode": 1})
rules.register_rule("ic57_block_downsize_early", band=True, usd_rule=True, defer=True,
                    opts={"early_mode": 2})

ARMS = {
    "A": {"rule": "ic57_skip_early", "leg": "2x", "cash_yield": "none"},
    # B is production's own behaviour (see the docstring): the engine's rule, no options
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none"},
    "C": {"rule": "ic57_block_downsize_early", "leg": "2x", "cash_yield": "none"},
}

def targets(data, spec):
    """Production K's target series (the calendar is a decision/fill choice, not a target rule)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
