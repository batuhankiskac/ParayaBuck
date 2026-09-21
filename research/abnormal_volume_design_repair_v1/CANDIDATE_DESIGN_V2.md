# Candidate design: `AV49_D1_NORMALRET_M1_LONG / DESIGN_V2`

This is the only candidate admitted to the future economic test. It repairs the blocked V1 specification without replacing the economic candidate family.

## Frozen signal

At the last completed exchange session of each calendar month, for each point-in-time eligible security:

1. compute split-consistent `DV(t)=close(t)*volume(t)`;
2. require 50 complete positive finite daily observations from `t-49` through `t` and the preceding close needed for returns;
3. require the inclusive-50 ascending average rank of `DV(t)` to be at least 46;
4. require the inclusive-50 ascending average rank of the formation-day close-to-close return to be 16 through 35;
5. exclude reserved-role symbols and any unresolved identity/corporate-action input.

Rank eligible names by descending volume rank, descending `DV(t)/Q0.90(DV[t-49:t-1])`, then ascending stable security identity. For the 49 sorted prior dollar volumes `x_(1) <= ... <= x_(49)`, the binding definition is `Q0.90=x_(44)`, because `j=1+floor(9*(49-1)/10)=44`. Duplicate values occupy their sorted positions; there is no interpolation. Select at most six. The ratio remains the frozen selection-relevant six-slot engineering extension to GKM.

## Frozen trading rule

- Decision: after the final exchange close of the calendar month.
- Entry: next month's first tradable open; no same-close execution. Use the candidate-neutral primitive available at updated main `980c6b90b7f316bcec6a9466dfc9b0e1756cc748`; do not use the legacy generic monthly cadence.
- Exit/rebalance: the following calendar month's first tradable open. No interim signal rebalance. Risk exits already present in the canonical standalone architecture remain enabled and identical in control and candidate.
- Initial formation for the research chronology: 2016-12-30 close for a 2017-01-03 open. Continue the single calendar schedule through development and validation; never reset at the validation boundary.
- Sizing: equal weight. Per-name target is `min(0.10, 0.55/N)` for `N` selected names. Unallocated sleeve capital stays cash. Account gross remains at most 0.60, cash at least 0.40, and account-wide positions at most six.
- Direction: long-only. No low-volume short leg, leverage, or replacement asset.

## Frozen exclusions

No alternative quantile estimator, lookback, weekly signal, turnover definition, z-score, return band, holding horizon, overlapping cohort, top-N, weighting method, or liquidity subset may be selected after outcomes are observed. The unconditioned volume-only signal may appear only as a labeled diagnostic and cannot replace the candidate.

Status: `FROZEN_PRE_OUTCOME_DESIGN_V2` once this repair commit receives its full SHA.
