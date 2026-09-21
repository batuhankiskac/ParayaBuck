# Timing and causality audit

The primary paper measures month `t` liquidity shock at month-end, forms
portfolios for month `t+1` and rebalances monthly. The principal predictive
portfolio is one month; longer continuation is horizon-regression evidence and
does not justify choosing 3/6 months after observing local outcomes.

A causal ParayaBuck mapping would be:

```text
final completed NYSE session of month M
  -> finalize daily terms and monthly ILLIQ
  -> compute LIQU using only M-12 ... M-1
  -> rank after the final close
  -> execute only at the first NYSE open of M+1
  -> hold until the following first-open rebalance
```

`month_end_first_open` implements the decision and execution boundaries and
fails without later-session rollover. The fill-day completed volume is not
available at its open and must not enter the decision.

The generic monthly engine is one session late and is not acceptable for this
contract. A future authorized experiment would need a research-only scheduler,
explicit v5 lifetime loading, rename continuity, terminal events and exact
first-open availability. No such candidate harness is created because the
design is rejected.
