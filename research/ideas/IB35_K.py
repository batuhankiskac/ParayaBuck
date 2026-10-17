"""IB35_K -- IB35's arm C, "the plain fixed 3 pp band", on the new `band_up` / `band_down` option.

Idea bank `docs/research/idea_bank_v1.json` (IB35, `statistical-filter`), the *same* arm the module
`research/ideas/IB35.py` registers as `needs_kernel`:

    C  the plain fixed 3 pp band (control; between the tested 2.5 and 5 pp)

`research/fast_engine_opts` implements a per-direction resize threshold (`band_up` / `band_down`
replace the engine's single `C0_BAND` in the resize test; A1u's gain band and H2 still gate
downsizes on top when the rule carries them). The registered rule carries the arm's own width in
both directions and **none of K's execution layer**:

    ib35k_band3pp   band_up = band_down = 0.03, band = usd_rule = defer = False

**Why that reading.** The bank calls arm C "the plain fixed 3 pp band ... the only untested width in
that family", where "that family" is `ewma_band_v1`'s B25/B50/B100 -- *plain* output bands, whose
definition is "entries and exits always trade in full; while the position is held the strategy
rebalances to target only when |target − held| > B" (`docs/research/ewma_band_v1.md` §Method), i.e.
C0's execution layer with a wider B and no A1u/H2 (which post-date that study). The campaign's own
vocabulary is the same: "C0/R2 (plain 0.5 pp band)" (`batch_screen/rules.py`). So arm C = the plain
band at 3 pp: `band=False, usd_rule=False, defer=False` with both thresholds at 0.03 (3 pp). The
alternative reading of the frozen spec's `"rule": "K"` field -- K's A1u/H2 kept and only the base
width changed -- is not screened: it is not a *plain* band (A1u would still block every 3-8 pp
downsize of a >5 %-gain sale), and the arm text is what defines the arm. Entries and exits to zero
are immediate in the kernel either way, as the family's definition requires.

`targets` is the production target series (the band width is a fill decision, not a target rule),
as in `IC59_K.py`.
"""

from __future__ import annotations

from research.batch_screen import rules
from research.fast_engine import fe_signals

#: the arm's width in both directions, and no A1u (band=False), no USD gain fraction, no H2
rules.register_rule("ib35k_band3pp", band=False, usd_rule=False, defer=False,
                    opts={"band_up": 0.03, "band_down": 0.03})

ARMS = {
    "C_K": {"rule": "ib35k_band3pp", "leg": "2x", "cash_yield": "none", "band_pp": 3.0},
}


def targets(data, spec):
    """Production K's target series (the band width is a fill decision, not a target rule)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
