"""IC52 -- minimum-notional order filter, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  skip a permitted downsize when its notional < $250 (re-evaluate at the next decision)
    B  skip a permitted downsize when its notional < $1,000
    C  skip when its notional < max($250, 0.05% of NAV)

Kernel mechanism: `research/fast_engine_opts`'s `min_notional` / `min_notional_frac`, requested
through a registered rule. The filter acts inside the fill decision (after K's A1u band and H2 have
permitted a downsize, on the session's own book) and the skipped drift accumulates toward the next
band crossing -- the bank's "re-evaluate at the next decision". The option's threshold is
`max(min_notional, min_notional_frac x NAV)` against `|sold units| x fill price`, exactly arm C's
`max($250, 0.05% of NAV)`.

Readings fixed here: the targets are production K's (`fe_signals.sized_wants` of the production
state machine); only the fill decision changes. No extra data, every window applicable. The kernel
applies the option in every stage (`kernel_scope: all_stages`, the bank's declaration); the
$250/$1,000 floors are account-size effects -- at $100k a 0.5 pp band move is $500 -- so the
small-capital and 21-ETF cells are where they bite.
"""

from __future__ import annotations

from research.batch_screen import rules
from research.fast_engine import fe_signals

rules.register_rule("ic52_floor250", band=True, usd_rule=True, defer=True,
                    opts={"min_notional": 250.0})
rules.register_rule("ic52_floor1000", band=True, usd_rule=True, defer=True,
                    opts={"min_notional": 1000.0})
rules.register_rule("ic52_floor250_frac5bp", band=True, usd_rule=True, defer=True,
                    opts={"min_notional": 250.0, "min_notional_frac": 0.0005})

ARMS = {
    "A": {"rule": "ic52_floor250", "leg": "2x", "cash_yield": "none"},
    "B": {"rule": "ic52_floor1000", "leg": "2x", "cash_yield": "none"},
    "C": {"rule": "ic52_floor250_frac5bp", "leg": "2x", "cash_yield": "none"},
}

def targets(data, spec):
    """Production K's target series (the filter is a fill decision, not a target rule)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
