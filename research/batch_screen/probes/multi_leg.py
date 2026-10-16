"""multi_leg -- engineering probe for the framework's N-leg routing (NOT a registered trial).

Arms, all plugin-path smoke tests of a `"legs": [...]` declaration or a margin loan:

* `zeros` -- legs `2x`, `1x`, `2x_spy`, with legs B and C at a target of exactly zero. Neither
  holds units, so every number must equal the framework's K control *bit for bit*: the N-leg
  routing's identity, the probe the framework's harness asserts.
* `offstate_blend` -- the IC34-C/IC36 pattern: while the E2 trend vote is off, hold 0.98 split
  equally between three extra-data instruments (GLD, IEF, SHY) in the *same* account as leg A;
  production target while it is on. `start = "auto"` makes the window reach every sleeve's own
  history (GLD starts 2004-11).
* `margin_qqq` -- the IC39 pattern on the N-leg kernel: leg A target is twice the production weight
  (the account goes past its cap), the excess is a cash loan at `margin_spread`.
* `sleeve_margin` -- the IC45-C/IC46-C pattern: production plus a fixed 0.25 sleeve on a second
  book, which sums above the 0.98 cap on most sessions, so the excess is funded by the loan.
* `spy_pair` -- the IC41 pattern: QLD* (leg A) plus the S&P 500 2x leg (`2x_spy`, leg B), so both
  are real books and the option leg construction has to reach the multi-leg kernel.

Lives in `probes/`, outside `research/ideas/`, so it is never registered as a trial (see
`README.md` section 5).
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import rules
from research.fast_engine import fe_signals

#: the margin rule the probe's loan arms use (idempotent; the framework's `register_rule`)
MARGIN_RULE = "probe_margin_k"
rules.register_rule(MARGIN_RULE, band=True, usd_rule=True, defer=True,
                    opts={"margin_spread": 0.015})
#: a rule carrying a *single-leg* option, for the refusal cell (a multi-leg arm may not use it)
BAND_RULE = "probe_band0_k"
rules.register_rule(BAND_RULE, band=True, usd_rule=True, defer=True, opts={"band_up": 0.0})


def production_wants(data):
    """The production R2/E2 target series of the panel (`k_control`'s targets)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)


ARMS = {
    "zeros": {"rule": "K", "legs": ["2x", "1x", "2x_spy"], "cash_yield": "none",
              "kind": "zeros"},
    "offstate_blend": {"rule": "K", "legs": ["2x", "gld", "ief", "shy"], "cash_yield": "none",
                       "start": "auto", "kind": "blend", "sleeve": 0.98, "inputs": ["gld", "ief",
                                                                                   "shy"]},
    "margin_qqq": {"rule": MARGIN_RULE, "legs": ["2x", "1x"], "cash_yield": "none",
                   "kind": "margin", "gross": 2.0},
    "sleeve_margin": {"rule": MARGIN_RULE, "legs": ["2x", "1x"], "cash_yield": "none",
                      "kind": "sleeve", "sleeve": 0.25},
    "spy_pair": {"rule": "K", "legs": ["2x", "2x_spy"], "cash_yield": "none", "kind": "pair",
                 "share_a": 0.5},
    # a *single*-leg loan (IC39's shape): the published option kernel's own `mrate`, not this one
    "one_leg_loan": {"rule": MARGIN_RULE, "leg": "2x", "cash_yield": "none", "kind": "one_leg",
                     "gross": 2.0},
}


def targets(data, spec):
    w = production_wants(data)
    zero = np.zeros_like(w)
    kind = spec["kind"]
    if kind == "zeros":
        return [w, zero, zero]
    if kind == "one_leg":
        return float(spec["gross"]) * w
    if kind == "margin":
        return [float(spec["gross"]) * w, zero]
    if kind == "sleeve":
        return [w, np.full_like(w, float(spec["sleeve"]))]
    if kind == "pair":
        fa = float(spec["share_a"])
        return [w * fa, w * (1.0 - fa)]
    if kind == "blend":
        off = 1.0 - fe_signals.e2_up(data.price)          # the trend vote's off state
        on = 1.0 - off
        per = float(spec["sleeve"]) / 3.0
        return [w * on, np.full_like(w, per) * off, np.full_like(w, per) * off,
                np.full_like(w, per) * off]
    raise ValueError(f"unknown probe kind {kind!r}")


__all__ = ["ARMS", "MARGIN_RULE", "production_wants", "targets"]
