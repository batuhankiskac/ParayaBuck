"""IC58 -- T+1 settlement funding constraint (cash account), batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  if a decision contains both a downsize and an up-resize, defer the buy half by one session
       (cash-account convention)
    B  defer only when the cash balance would otherwise go negative by more than 1% of NAV
    C  defer every buy funded by same-session sale proceeds by one session (the strict version)

Kernel mechanism: `research/fast_engine_opts`'s `settle_floor`, requested through a registered
rule: a buy may spend `cash - unsettled + settle_floor x NAV`, where `unsettled` is the same
session's sale proceeds. `settle_floor = 0.0` is the cash account (nothing of this session's
proceeds is spendable); `settle_floor = 0.01` (arm B) allows the balance to run 1% of NAV negative,
which is the arm's threshold. A clipped buy is not cancelled -- the target is unchanged, so the next
session re-attempts it, which is the one session of lost exposure the arm prices.

**Arms A and C coincide in a single-leg account** (the kernel package's own finding, repeated here):
only a sale and a buy in the *same* session can be an unsettled-proceeds pair, and with one
instrument any such pair is exactly the downsize/up-resize case A defers. C's "the strict version" is
therefore the same fill rule as A, and both register `settle_floor = 0.0`; the two trials are kept
as the bank lists them. The real trigger is a tax-funding sale followed by an up-resize in the same
session, so the evidence comes from the after-tax windows (the option is inert on a bootstrap chunk
of production targets).

Readings fixed here: the targets are production K's; the arm changes the cash routing. No extra
data, every window applicable.
"""

from __future__ import annotations

from research.batch_screen import rules
from research.fast_engine import fe_signals

#: arm B's allowed deficit, as a fraction of NAV
SETTLE_FLOOR_B = 0.01

rules.register_rule("ic58_settle_cash", band=True, usd_rule=True, defer=True,
                    opts={"settle_floor": 0.0})
rules.register_rule("ic58_settle_1pct", band=True, usd_rule=True, defer=True,
                    opts={"settle_floor": SETTLE_FLOOR_B})

ARMS = {
    "A": {"rule": "ic58_settle_cash", "leg": "2x", "cash_yield": "none"},
    "B": {"rule": "ic58_settle_1pct", "leg": "2x", "cash_yield": "none",
          "settle_floor": SETTLE_FLOOR_B},
    # C's "strict version" is A's rule in a single-leg account (see the docstring)
    "C": {"rule": "ic58_settle_cash", "leg": "2x", "cash_yield": "none"},
}

def targets(data, spec):
    """Production K's target series (settlement is a cash-routing choice, not a target rule)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
