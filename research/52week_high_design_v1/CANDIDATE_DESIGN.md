# Frozen candidate design

This campaign defines exactly one candidate:

## `equity_52week_high_close_v1`

### Signal

For ordinary equity entity `i` on the final completed trading session `t` of a
calendar month:

```text
score(i,t) = adjusted_close(i,t)
             / max(adjusted_close(i,s) for s in sessions[t-251:t])
```

- `sessions[t-251:t]` contains exactly 252 valid completed trading sessions
  and includes `t`.
- Both numerator and denominator use the same split-adjusted, price-only daily
  close series.
- Cash dividends are excluded from the signal. Split and stock-dividend
  actions are not reapplied.
- Higher ratio means closer to the annual closing-price high and receives the
  higher rank. Exact ties break by canonical symbol ascending only for
  determinism.
- No z-score, nonlinear transform, residualization, volatility adjustment,
  sector adjustment, momentum confirmation, or recency overlay is allowed.

### Eligibility and failure policy

- Use the unchanged production PIT equity scanner pool.
- Rank that broad eligible pool with this candidate's score; never reuse V3's
  ranked observations.
- Require exactly 252 valid sessions. A shorter history is unscoreable; the
  lookback is never shortened for a new listing.
- A missing, nonfinite, nonpositive, stale, or misaligned close anywhere in the
  window makes the entity unscoreable.
- A missing or ambiguous PIT entity/lifecycle mapping makes the entity
  unscoreable. No price or identity imputation is allowed.
- SPY is reference-only. SH, PSQ, and DOG are hedge-only. None can become an
  ordinary target.
- The broad `US_EQUITY` taxonomy limitation remains unchanged and must be
  disclosed in results.

### Portfolio

- Long-only alternative equity alpha; it is not an additional sleeve and is
  never combined with V3 in the first experiment.
- Select the highest-ranked six scoreable ordinary names: `top_n = 6`.
- For `n` available selected names, each raw target is
  `min(0.55 / n, 0.10)`. Any unavailable capacity remains cash; it is not
  redistributed above the cap.
- Maximum equity allocation: `0.55`.
- Maximum position weight: `0.10`.
- Shared account maximum positions: `6`; gross maximum `0.60`; minimum cash
  `0.40`. Existing external, crypto, manual, and pending positions consume
  slots identically for control and candidate.
- Equal weighting is part of the candidate identity. It isolates the ranking
  hypothesis and does not import V3's residual-volatility machinery.

### Cadence, holding, and execution

- Decision cadence: monthly, after the last completed trading session of each
  calendar month.
- Earliest execution: the next tradable session open; never the decision close.
- Between scheduled decisions, hold the last target. Canonical lifecycle,
  execution, reconciliation, and risk-reducing actions remain active.
- No stop loss, take profit, secondary exit, or intermediate alpha rebalance.
- Use the canonical 0.5% rebalance band, $10 minimum order, 10% volume
  participation, 2 bps slippage, 2 bps full spread, zero commission, and
  effective-dated regulatory fees. Future cost stresses scale every all-in
  cost component by 1x, 2x, and 3x.

### Identity boundary

Changing the reference field, lookback, window inclusion, cadence, selection
count, weighting, allocation, cap, universe, execution delay, cost semantics,
or holding rule creates a different candidate and is forbidden after economic
outputs are visible.
