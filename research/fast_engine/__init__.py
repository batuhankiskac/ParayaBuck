"""fast_engine -- a shared Numba-accelerated research engine for the ParayaBuck studies.

A study imports this package instead of copying `screen.py` + `tax_engine.py` again. See
README.md for the API and parity/benchmark results.

    from research.fast_engine import api

    env = api.RunEnv(days, fx, ufe, i0, i1, mode="progressive")
    r = api.simulate(opens, closes, wants, env, 100_000.0, i0, i1, arm="K", mode="progressive")
"""

from . import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_tax  # noqa: F401

__all__ = ["fe_boot", "fe_config", "fe_data", "fe_signals", "fe_sim", "fe_tax"]
