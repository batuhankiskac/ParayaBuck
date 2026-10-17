"""batch_screen -- a batch screening framework for ParayaBuck ideas, on `research/fast_engine`.

The pieces:

    api          the plugin contract: `ARMS = {arm_id: spec}` + `targets(data, spec)` in numpy
    rules        the engine's fill-rule options (band / usd_rule / defer) and the cash-yield knob
    windows      the market windows as `(P, T)` panels (main, six OOS, 21 ETFs, bootstrap chunks)
    boot         the plugin-aware block bootstrap drivers (numba over `fe_sim.sim_one`)
    stages       stage 1 (screen) and stage 2 (holdout) plus the fixed gate set
    registry     REGISTRY.jsonl, the append-only trial ledger, and the commit gate
    deflation    White RC / Romano-Wolf stepdown / deflated Sharpe over the ledger
    run          the CLI (`--harness`, `--batch`, `--deflate`)

See README.md for the API, the gates, the deflation method and its assumptions, and
PREREGISTRATION.md for the fixed protocol.
"""

from __future__ import annotations

from .api import MarketData, NeedsKernel, PluginError, call_targets, load_idea, panel
from .rules import CONTROL_ARM, cash_rates, register_rule, rule_of

__all__ = [
    "CONTROL_ARM",
    "MarketData",
    "NeedsKernel",
    "PluginError",
    "call_targets",
    "cash_rates",
    "load_idea",
    "panel",
    "register_rule",
    "rule_of",
]
