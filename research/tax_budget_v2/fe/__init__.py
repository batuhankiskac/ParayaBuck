"""tax_budget_v2's copy of `research/fast_engine` (tag `archive/research/fast-engine-v1-2026-10`).

Only `fe_sim.py` (the Y1/Y2 year-end round trip and Y3/Y4's state window, both off on the parent's
code path), `fe_boot.py` (the rule arrays it passes to the kernel), `fe_tax.py` (the `yearend`
gain reason and `TARIFF_TOP`) and `fe_config.py` (the deeper `ROOT`) differ from the parent; the
new `fe_oos.py` is gate G8's pooled OOS bootstrap. See
`research/tax_budget_v2/PREREGISTRATION.md` section 2.

    from research.tax_budget_v2 import fe as api

    env = api.fe_sim.RunEnv(days, fx, ufe, i0, i1)
    r = api.fe_sim.simulate(opens, closes, wants, env, 100_000.0, i0, i1, arm="Y1",
                            mode="progressive")
"""

from . import fe_boot, fe_config, fe_data, fe_oos, fe_signals, fe_sim, fe_study, fe_tax  # noqa: F401

__all__ = [
    "fe_boot",
    "fe_config",
    "fe_data",
    "fe_oos",
    "fe_signals",
    "fe_sim",
    "fe_study",
    "fe_tax",
]
