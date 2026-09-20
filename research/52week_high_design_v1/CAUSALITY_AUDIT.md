# Causality audit

## Frozen information-decision-execution map

1. **Information:** after the final trading session of calendar month `m` is
   complete, use only the 252 valid, completed, split-adjusted daily closes for
   each PIT-eligible entity through that session `t`.
2. **Score:** compute `Close(i,t) / max(Close(i,s))` for those 252 sessions,
   including `t`. Fewer than 252 valid sessions or any stale, missing,
   nonfinite, nonpositive, or lifecycle-ambiguous input makes the entity
   unscoreable.
3. **Observation set:** rank the unchanged broad PIT scanner pool by this exact
   score, highest first, before truncating to the bounded candidate observation
   set. SPY/SH/PSQ/DOG may remain pinned for their canonical roles but cannot be
   ordinary targets.
4. **Decision:** select the six highest-ranked scoreable ordinary equities and
   create equal targets totaling at most `0.55`, subject to the shared risk
   overlay and available account slots.
5. **Execution:** create orders only after the completed close. The earliest
   fill is the next tradable session's open using the shared adverse slippage,
   half-spread, volume-participation, regulatory-fee, minimum-order, and
   rebalance-band rules. There is no same-close fill.
6. **Hold:** do not rebuild the target between month-ends. Canonical lifecycle,
   risk-reducing, reconciliation, and execution actions remain active.

No future open, high, low, volume, membership, or lifecycle event may influence
the month-end score or target.

## Current-engine comparison

Completed-bar decision and next-open order ordering are causal and suitable.
Two current semantics do not reproduce the frozen convention closely enough:

- the generic monthly policy decides after the first completed bar of the new
  month, not after the preceding month's final close; and
- broad-pool observation ranking is through `t-1`, while completed `t` ranking
  occurs only inside the already bounded observation set.

The later economic experiment therefore requires a deterministic research-only
month-end boundary policy and a broad-pool-through-`t` rank/truncation path,
both tested against hand-built calendars. Treating the existing generic
monthly path as equivalent is prohibited.

## Required causal tests before economics

- decision bar is included and no later bar is visible;
- month-end holiday/early-close calendars choose the actual final session;
- every created order timestamp precedes its fill timestamp;
- observation rank and target score use one shared scoring function;
- a symbol added after `t` cannot enter the `t` pool;
- a lifecycle-ended or ambiguous entity fails closed;
- split-scale multiplication leaves every ratio and rank unchanged;
- cash dividends never alter the price-high ratio;
- 251 valid sessions fail and 252 valid sessions pass;
- stale or missing final bars fail without imputation.

Because these tests and paths do not yet exist on the branch, causality is
specified but execution readiness is blocked.
