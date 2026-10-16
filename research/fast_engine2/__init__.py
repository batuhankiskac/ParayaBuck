"""research/fast_engine2 -- the two-leg extension of `research/fast_engine`.

A copy-and-extend of the shared Numba engine (never an edit of it): the same session loop, the
same Turkish after-tax accounting, the same block-bootstrap draw -- with a **second instrument**
in the same cash pool and its own FIFO lot book.

```
fe2_sim.py     the two-leg session kernel (`sim2`/`simulate2`); with leg B identically
               zero it is bit-identical to `fe_sim.sim_one` (see `fe2_parity.py`)
fe2_boot.py    the two-leg block bootstrap driver (`boot2`), the same draws as `fe_boot.boot`
fe2_parity.py  the parity cells: (a) leg B == 0 vs the engine, (b) the riskoff two-leg case
```

The Turkish tax engine is not copied: both legs' realisations land in one `year_gain` row and
each leg keeps its own lot arrays, so `fast_engine.fe_tax`'s kernels (and `fe_sim`'s
`_assess`/`_rule_sale_usd`/`gain_band`) are used unchanged.
"""

from . import fe2_sim

__all__ = ["fe2_sim"]
