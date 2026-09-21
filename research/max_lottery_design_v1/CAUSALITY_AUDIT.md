# Causality Audit

## Timing chain

A causal MAX experiment would require this exact information sequence:

1. establish the point-in-time eligible ordinary-common-stock universe using
   only information available by the final exchange close of calendar month t;
2. compute simple close-to-close returns only from completed sessions inside
   calendar month t, using already split-adjusted closes;
3. form MAX only after the final session close; the decision session is an
   input, never a fill;
4. place orders no earlier than the first tradable open of month t+1;
5. apply next-open price, slippage, half-spread, commission, effective-dated
   fees, participation limits, partial fills, rebalance band, and minimum
   notional;
6. keep cash dividends in portfolio accounting, not in a price-only proxy
   signal; do not reapply splits.

This prevents same-close execution, forward universe membership, and future
information leakage.

## Current causal strengths

- Bars are timestamp-normalized, session-screened, deduplicated, SIP sourced,
  and split-adjusted.
- Lifetimes distinguish explicit terminal events, active horizon censoring, and
  unresolved endpoints.
- The execution engine supports next-open fills and all-in costs.
- Reserved-role symbols remain observable but cannot be ordinary targets.

## Current causal blockers

### Product identity

There is no effective-dated ordinary-common-stock taxonomy. A MAX outcome could
therefore be caused by product composition rather than firm-level lottery
demand. This is a construct-validity failure, not a cosmetic reporting issue.

### Monthly boundary

The generic monthly policy observes the first completed daily bar of the new
month and fills at the following open. It cannot directly represent final
month-t close decision and first month-t+1 open execution. Using it unchanged
would introduce an extra session of information and a one-session delay.

## Missing and extreme observations

Were the blockers cleared, the literature-faithful primitive would be MAX1 over
the prior completed calendar month using simple daily returns. Valid zero
returns would remain observations; missing sessions would not be imputed; a
minimum-observation rule would fail closed; new listings without sufficient
history would be ineligible. The 15-observation rule is a later-literature
precedent, not part of a frozen candidate in this decision.

Known forward/reverse-split close boundaries are continuous in the corrected
data. The NVDA 2024-06-10 intraday high is anomalous while the close is
continuous, reinforcing the choice of close-to-close rather than high-based
extreme-return variants if a future redesign occurs.

## Conclusion

The data path can support a causal close-based statistic, but the experiment as
requested is not presently causally interpretable. No implementation or
economic test is authorized by this design.
