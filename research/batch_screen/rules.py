"""batch_screen.rules -- the fill-rule options an arm may declare, and the cash-yield schedule.

An arm's execution layer is three booleans, exactly `fe_sim.RULE`'s:

    band      A1u's gain-scaled downward-resize band (0.5 / 3 / 8 pp by USD gain fraction)
    usd_rule  the band's gain fraction in USD rather than TRY
    defer     H2's December-tail postponement

The engine ships four combinations (C0, A1u, H2, K). `register_rule` adds the remaining ones to
`fe_sim.RULE` -- the engine's own extension point (its `simulate` and `_boot_chunk` both look the
rule up by name there), so no engine file is edited. A *new mechanism* (not a re-combination of
these three) is expressible only through `register_rule(..., opts={...})`, which routes the arm to
`research.fast_engine_opts`'s kernel (`bridge`); see that package's README section "requesting an
option". A mechanism outside that option set still has to be declared `needs_kernel`.
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
#: the *kernel* options of each registered rule, by name: `research/fast_engine_opts`'s option set
#: (the mechanisms the engine does not implement -- a per-direction band width, a dollar floor on
#: a downsize, slicing, the drawdown governor, margin, the early-close calendar, T+1 funding, the
#: FX conversion cost, the fill reference, dividend withholding). `{}` for the engine's own rules.
#: The drivers branch on `arm["opts"]`: empty -> the engine, otherwise `bridge`.
RULE_OPTS = {}
_CASH = {}


def register_rule(name: str, band: bool, usd_rule: bool, defer: bool,
                  opts: dict | None = None) -> dict:
    """Add a re-combination of the three options to `fe_sim.RULE` (idempotent).

    `opts` carries the kernel options of `research/fast_engine_opts` (any `fe_opts.Opts` field plus
    the bridge's own `margin_spread` / `wht`); the framework's drivers route a rule with a
    non-empty `opts` to `fe_opts_sim.sim_one_opts` instead of the engine's kernel. The three
    booleans stay the engine's (`fe_sim.RULE`), so an *empty* option set is simulated by the
    engine's own code path and every published cell is unchanged.
    """
    o = {"band": bool(band), "usd_rule": bool(usd_rule), "defer": bool(defer)}
    kopt = dict(opts or {})
    cur = fe_sim.RULE.get(name)
    if cur is not None and dict(cur) != o:
        raise ValueError(f"rule {name!r} already means {cur}, cannot redefine it as {o}")
    prev = RULE_OPTS.get(name)
    if prev is not None and prev != kopt:
        raise ValueError(f"rule {name!r} already carries the kernel options {prev}, not {kopt}")
    fe_sim.RULE[name] = o
    RULES[name] = dict(o)
    RULE_OPTS[name] = dict(kopt)
    return o


def rule_of(name: str) -> dict:
    """The three booleans of a rule label."""
    if name not in RULES:
        raise KeyError(f"unknown rule {name!r}; known: {sorted(RULES)}")
    return RULES[name]


def opts_of(name: str) -> dict:
    """The kernel options a rule label carries (`{}` for the engine's own rules)."""
    if name not in RULES:
        raise KeyError(f"unknown rule {name!r}; known: {sorted(RULES)}")
    return dict(RULE_OPTS.get(name, {}))


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


__all__ = ["CONTROL_ARM", "RULES", "RULE_OPTS", "cash_rates", "opts_of", "register_rule",
           "rule_of"]
