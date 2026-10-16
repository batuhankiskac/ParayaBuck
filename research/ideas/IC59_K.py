"""IC59_K -- the asymmetric resize band (IC59's arms) on the new `band_up` / `band_down` option.

Idea bank `docs/research/idea_bank_v2.json` (IC59), the *same three specs* as
`research/ideas/IC59.py`, now expressible:

    A_K  up-resize band 0.0 pp;  down-resize band = K's A1u (0.5 / 3 / 8 pp by the USD FIFO gain
         fraction)
    B_K  up-resize band 0.25 pp; the same A1u down band
    C_K  up band 0.0 pp and the down band scored on the indexed-TRY gain fraction instead of USD

`research/fast_engine_opts` implements the mechanism (`band_up` / `band_down`: the resize test is
per direction, A1u's gain band and H2 still gate downsizes on top), reachable through a registered
rule. The registered rules carry the bank's three settings:

    ic59k_band_up0      band_up = 0.0     (K's A1u down band, USD gain fraction)
    ic59k_band_up25     band_up = 0.0025  (the same down band)
    ic59k_band_up0_try  band_up = 0.0, usd_rule = False (the indexed-TRY gain fraction; the
                        production band_down of 0.5 pp is unchanged, so with band_up = 0.0 the
                        *up* side is immediate exactly as arm A_K asks)

**Why a second module.** `research/ideas/IC59.py`'s three arms are pinned in the ledger
(`REGISTRY.jsonl`, status `needs_kernel`) with their spec hashes; `resolve_arm` applies a module's
level `NEEDS_KERNEL` to *every* arm of that module, so the module level declaration cannot be kept
while new arms in the same file become runnable, and adding a per-arm `needs_kernel` to the old
arms would re-hash their specs -- `verify_ledger` reads that as `spec_drift` and refuses every
batch. The old module is therefore left byte-for-byte as it was committed, and the runnable
versions of its arms live here under the `_K` names the ledger will register.

The spec keys (`up_band_pp`, `down_band`, `gain_fraction`) repeat the arm text for the registry;
the mechanism reads the values from the registered rule's `opts`. `targets` is the production
target series, as in `IC59.py`.
"""

from __future__ import annotations

from research.batch_screen import rules
from research.fast_engine import fe_signals

#: the bound-down band is K's A1u in controllers A_K and B_K, and the indexed-TRY gain fraction
#: keys it in C_K (`usd_rule = False`); the up band is the arm's own parameter.
rules.register_rule("ic59k_band_up0", band=True, usd_rule=True, defer=True,
                    opts={"band_up": 0.0})
rules.register_rule("ic59k_band_up25", band=True, usd_rule=True, defer=True,
                    opts={"band_up": 0.0025})
rules.register_rule("ic59k_band_up0_try", band=True, usd_rule=False, defer=True,
                    opts={"band_up": 0.0})

ARMS = {
    "A_K": {"rule": "ic59k_band_up0", "leg": "2x", "cash_yield": "none", "up_band_pp": 0.0,
            "down_band": "A1u", "gain_fraction": "usd"},
    "B_K": {"rule": "ic59k_band_up25", "leg": "2x", "cash_yield": "none", "up_band_pp": 0.25,
            "down_band": "A1u", "gain_fraction": "usd"},
    "C_K": {"rule": "ic59k_band_up0_try", "leg": "2x", "cash_yield": "none", "up_band_pp": 0.0,
            "down_band": "A1u", "gain_fraction": "try_indexed"},
}

def targets(data, spec):
    """Production K's target series (the band widths are a fill decision, not a target rule)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
