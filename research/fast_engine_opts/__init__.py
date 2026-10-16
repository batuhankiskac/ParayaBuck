"""research.fast_engine_opts -- single-leg execution options for the Numba research engine.

`research/fast_engine` is not edited. This package holds a *copy* of its session kernel
(`fe_sim.sim_one` -> `fe_opts_sim.sim_one_opts`) with the execution mechanisms the idea bank's
`kernel_option` arms need, every one of them inert by default so that options-off is bit-for-bit
the engine (`parity_opts.py` proves it per arm, mode and capital, plus the bootstrap).

The mechanisms:

    IC59  band_up / band_down          per-direction resize thresholds (K's A1u/H2 on downsizes)
    IC52  min_notional / _frac         skip a permitted downsize below a dollar floor / NAV %
    IC53  slice_parts / slice_frac     fill a large resize over N sessions' opens
    IC49  dd_start / dd_floor          scale the target by the realised NAV drawdown
    IC39  mrate                        charge interest on a negative cash balance (margin loan)
    IC57  early (+ early_mode)         NYSE early-close sessions: skip the decision / the downsize
    IC58  settle_floor                 sale proceeds are not spendable until the next session
    IC55  fx_cost / prefund_sessions   TRY conversion cost, and pre-funding the tax liability
    IC54  fill (+ fill_mode)           fill at a per-session reference instead of the open
    IC56  (panel)                      net dividend withholding in the leg construction
    IC40  (panel)                      an S&P 500 2x leg beside the Nasdaq one

`fe_opts.Opts` is the option set a plugin requests (through `batch_screen.rules.register_rule`),
`fe_opts_sim` the kernel, `fe_opts_legs` the two panel mechanisms, `fe_opts_calendar` the NYSE
early-close calendar, `bridge` the dispatch `batch_screen` calls.
"""

from __future__ import annotations

from .fe_opts import FLOAT_OPTS, INT_OPTS, Opts

__all__ = ["Opts", "FLOAT_OPTS", "INT_OPTS"]
