# V3 distinctness audit

The candidate is materially distinct from canonical V3.

| Dimension | Canonical V3 | Abnormal-volume candidate |
|---|---|---|
| Economic object | Medium-horizon residual price momentum | One-day own-history trading-activity shock |
| Inputs | Residual returns, 6/9/12 consensus, IVOL | Adjusted close, adjusted volume, same-day return rank |
| Lookback | 12-1 and multi-horizon consensus | 49 prior sessions plus formation session |
| Cadence | Weekly | Calendar month-end |
| Holding rule | Weekly re-evaluation | Hold until next month-first-open, no signal rebalance |
| Selection | Momentum score, IVOL rejection | Top-decile own-history volume eligibility and frozen relative-volume intensity ordering |
| Sizing | Inverse volatility | Equal weight subject to caps |

The middle-40% return rank is a control that excludes contemporaneous extremes, not a momentum overlay. No V3 score or inverse-volatility preference enters the candidate. Shared scanner, risk, capital, execution, and cost controls are experimental controls, not signal overlap.

Verdict: `DISTINCT_SIGNAL_AND_CADENCE`. Outcome correlation and portfolio complementarity remain untested and cannot be inferred from design distinctness.
