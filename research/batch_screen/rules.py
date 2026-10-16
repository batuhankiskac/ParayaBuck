"""batch_screen.rules -- the fill-rule options an arm may declare, and the cash-yield schedule.

An arm's execution layer is three booleans, exactly `fe_sim.RULE`'s:

    band      A1u's gain-scaled downward-resize band (0.5 / 3 / 8 pp by USD gain fraction)
    usd_rule  the band's gain fraction in USD rather than TRY
    defer     H2's December-tail postponement

The engine ships four combinations (C0, A1u, H2, K). `register_rule` adds the remaining ones to
`fe_sim.RULE` -- the engine's own extension point (its `simulate` and `_boot_chunk` both look the
rule up by name there), so no engine file is edited. A *new mechanism* (not a re-combination of
these three) is not expressible and must be declared `needs_kernel` and screened separately.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import timedelta

import numpy as np

from research.fast_engine import fe_data, fe_sim

#: the plugin arm the framework's control is computed from (production K)
CONTROL_ARM = "k_control/K"

RULES = {name: dict(opts) for name, opts in fe_sim.RULE.items()}
# the engine's own aliases (`fe_sim.simulate` maps "R2"/"E2" onto RULE["C0"]): the unbanded
# execution layer, i.e. what production R2 ran.
RULES["R2"] = dict(RULES["C0"])
RULES["E2"] = dict(RULES["C0"])
_CASH = {}


def register_rule(name: str, band: bool, usd_rule: bool, defer: bool) -> dict:
    """Add a re-combination of the three options to `fe_sim.RULE` (idempotent)."""
    opts = {"band": bool(band), "usd_rule": bool(usd_rule), "defer": bool(defer)}
    cur = fe_sim.RULE.get(name)
    if cur is not None and dict(cur) != opts:
        raise ValueError(f"rule {name!r} already means {cur}, cannot redefine it as {opts}")
    fe_sim.RULE[name] = opts
    RULES[name] = dict(opts)
    return opts


def rule_of(name: str) -> dict:
    """The three booleans of a rule label."""
    if name not in RULES:
        raise KeyError(f"unknown rule {name!r}; known: {sorted(RULES)}")
    return RULES[name]


def cash_rates(days) -> np.ndarray:
    """The per-session idle-cash yield schedule of a window, from DTB3/TB3MS.

    The engine's session-based bridge from the calendar-day reference used by
    `research/crypto_sleeve_decision_v1/account_sim.py`: every calendar day since the previous
    session is folded into one rate, so a session that follows a weekend earns three days.
    Indexed by absolute session, as `fe_sim.RunEnv(cash_rate=...)` expects. Published studies run
    it *off* (`cash_yield: "none"`); turning it on changes every published cell.
    """
    key = tuple(days)
    if key in _CASH:
        return _CASH[key]
    d3, tb = fe_data.load_rates()
    d0 = _date.fromisoformat(days[0])
    d1 = _date.fromisoformat(days[-1])
    cal = [(d0 + timedelta(days=k)).isoformat() for k in range((d1 - d0).days + 1)]
    daily = fe_data.rate_on(cal, d3, tb) / 365.0
    rates = np.zeros(len(days), dtype=np.float64)
    prev = None
    for k, d in enumerate(days):
        idx = (_date.fromisoformat(d) - d0).days
        if prev is not None:
            acc = 1.0
            for j in range(prev + 1, idx + 1):
                acc *= 1.0 + daily[j]
            rates[k] = acc - 1.0
        prev = idx
    _CASH[key] = rates
    return rates


__all__ = ["CONTROL_ARM", "RULES", "cash_rates", "register_rule", "rule_of"]
