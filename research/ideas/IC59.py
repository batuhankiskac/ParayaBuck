"""IC59 -- asymmetric band (immediate up-resizes, K's gate on downsizes): NEEDS_KERNEL.

Idea bank `docs/research/idea_bank_v2.json` (main), three arms, parameters frozen by the spec:

    A  up-resize band 0.0 pp;  down-resize band = K's A1u (0.5 / 3 / 8 pp by the USD FIFO gain
       fraction)
    B  up-resize band 0.25 pp; the same A1u down band
    C  up band 0.0 pp and the down band scored on the indexed-TRY gain fraction instead of USD

**None of the three arms is expressible as a plugin.**  The bank's plugin note asks to check
`fe_sim.RULE` first; it was checked.  The engine's resize decision is

    trade = abs(tgt - w) > C0_BAND          # research/fast_engine/fe_sim.py, sim_one

with `C0_BAND = fe_config.BAND = 0.5 pp`, a compiled constant applied *symmetrically* to up- and
downsizes, before the A1u gain band can block a downsize.  `fe_sim.RULE` (and
`batch_screen.rules.register_rule`) only re-combines three booleans -- `band` (A1u's gain-scaled
downsize band), `usd_rule` (USD vs TRY gain fraction) and `defer` (H2) -- and has no width, let
alone a per-direction width.  An up-resize threshold of 0.0 or 0.25 pp therefore changes the
kernel's fill schedule itself, which is a new kernel option in a copy of the engine plus an
off/on parity cell (the threshold back at 0.5 pp must reproduce K bit for bit) -- a separate study,
not a target-weight rule.

Arm C's TRY-scored down band alone *would* be expressible (`register_rule(name, band=True,
usd_rule=False, defer=True)`), but its spec also sets the up band to 0.0 pp, so it needs the same
kernel option.  No arm is approximated (e.g. by inflating targets to force an up-fill): that would
be a different mechanism with different turnover and tax behaviour.

The arms are registered exactly as specified and refused by the runner.  `targets` is the
production target series (only so the module honours the plugin contract; it is never simulated).
"""

from __future__ import annotations

from research.fast_engine import fe_signals

NEEDS_KERNEL = (
    "an asymmetric resize band (up-resize threshold 0.0 / 0.25 pp, A1u's gate on downsizes) "
    "changes fe_sim.C0_BAND, the compiled symmetric 0.5 pp threshold of sim_one's trade test; "
    "fe_sim.RULE / register_rule only re-combine the band/usd_rule/defer booleans and cannot set "
    "a per-direction width -- needs a kernel option in an engine copy plus an off/on parity cell"
)

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "up_band_pp": 0.0,
          "down_band": "A1u", "gain_fraction": "usd"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "up_band_pp": 0.25,
          "down_band": "A1u", "gain_fraction": "usd"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "up_band_pp": 0.0,
          "down_band": "A1u", "gain_fraction": "try_indexed"},
}


def targets(data, spec):
    """Production K's target series (never simulated: the module declares NEEDS_KERNEL)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)


__all__ = ["ARMS", "NEEDS_KERNEL", "targets"]
