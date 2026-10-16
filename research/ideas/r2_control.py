"""r2_control -- R2 (K's predecessor) as a batch_screen idea: the same target series as K under
the *unbanded* execution layer.

The campaign's R2 reference row: `research/fast_reentry_v1` R2 = E2 + early re-entry with the
plain 0.5 pp resize band (no A1u gain band, no H2 deferral -- `fe_sim.RULE["C0"]`, which
`fe_sim.simulate` also accepts as the alias "R2"). Published anchors
(`research/band_h2_combo_v1/RESULTS.json`'s C0 row, m = 2):

    pre-tax CAGR          base 15.4766 %   calibrated 14.8961 %   m = 1 base 15.7538 %
    pre-tax max DD        base 46.7584 %   calibrated 46.8797 %
    after tax, $100k     10.1600 / 14.7059 / 22.8861 %

Screened against K this arm is the *negative* demo: stage 1 stops it on the after-tax 2000-window
gate (~-0.59 pp), which is the expected, pre-registered outcome of the K campaign.

Fixed parameters: none -- the engine's published constants.
"""

from __future__ import annotations

from research.fast_engine import fe_signals

ARMS = {
    "R2": {"rule": "C0", "leg": "2x", "cash_yield": "none"},
}


def targets(data, spec):
    """Production R2's target series (identical to K's; only the fill rule differs)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
