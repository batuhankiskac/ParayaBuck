"""research.fast_engine3 -- the N-leg (N <= 4) extension of `research/fast_engine`.

A copy-and-extend of the two-leg kernel `research/fast_engine2.fe2_sim.sim2`, generalised from a
fixed two-instrument account to **N instruments in one cash pool** (N up to `fe3_sim.N_LEGS` = 4),
plus the one mechanism the multi-leg account still lacks: an optional **margin loan** that accrues
interest on a negative cash balance (`research.fast_engine_opts`' `mrate` option, ported here).

Nothing in `research/fast_engine/` or `research/fast_engine2/` is edited: the per-leg primitives
(`fe2_sim._buy_leg` / `fe2_sim._sell_leg`) and the Turkish after-tax engine (`fast_engine.fe_tax`)
are imported unchanged, so there is exactly one implementation of every arithmetic step.

```
fe3_sim.py     the N-leg session kernel (`sim3`/`simulate3`) and the result dict
fe3_boot.py    the N-leg block bootstrap driver (`boot3`, the same draws as `fe_boot.boot`)
fe3_parity.py  the parity cells: N-leg with legs 2..N == 0 vs the engine, legs 3..N == 0 vs
               `fe2_sim`, and margin on a single leg vs `research.fast_engine_opts`
bridge3.py     the dispatch the batch-screen framework calls for a multi-leg / margin arm
```

With every leg beyond the first identically zero (zero prices and zero targets) the kernel is
**bit-identical** to `fe2_sim.sim2`, and with every leg beyond the second identically zero it is
bit-identical to `fe_sim.sim_one` -- `fe3_parity.py` asserts both on the real window, the after-tax
windows and the clean bootstrap. With the margin array empty it is bit-identical to both as well;
with it set on a single leg it reproduces `fe_opts_sim.simulate_opts`' margin cells.
"""

from . import fe3_sim

__all__ = ["fe3_sim"]
