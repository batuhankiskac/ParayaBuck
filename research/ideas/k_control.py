"""k_control -- production K as a batch_screen idea. **This arm is the framework's control.**

K = `equity_leveraged_trend_v1` 1.2.0: the production target series under the production
execution layer `fe_sim.RULE["K"]` (A1u's USD-gain-scaled downsize band + H2's December
postponement). Published anchors this module must reproduce through the framework
(`research/band_h2_combo_v1/RESULTS.json`, m = 2):

    pre-tax CAGR          base 15.7649 %   calibrated 15.1785 %   m = 1 base 16.0173 %
    pre-tax max DD        base 46.6698 %   calibrated 46.7948 %
    turnover / year       5.4561
    after tax, $100k, liquidated (progressive): 10.7533 / 15.2354 / 23.9720 %
                          on 2000-03-09→2026-09-29 / 2010-01→end / 2017-01→end

Target series: E2's 2-of-3 vote on the QQQ *price* close plus the half-weight early re-entry
(SMA50 up, vol20 below its 252-session median, close above the close 20 sessions ago), sized
`min(1, 0.35/vol20)` on the held leg's own vol, total cap 0.98. Decided at the close, filled at
the next open, 0.5 pp resize band (`fe_signals` is the production pipeline).

Fixed parameters: none -- the engine's published constants.
"""

from __future__ import annotations

from research.fast_engine import fe_signals

ARMS = {
    "K": {"rule": "K", "leg": "2x", "cash_yield": "none"},
}


def targets(data, spec):
    """`fe_signals.sized_wants` on the production E2 + early-re-entry state machine."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
