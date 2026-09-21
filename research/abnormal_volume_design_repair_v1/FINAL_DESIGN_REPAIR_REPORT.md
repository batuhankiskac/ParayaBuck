# Abnormal-volume design repair V1 — final report

## Decision

`QUANTILE_SPEC_REPAIRED`

The repaired specification is `AV49_D1_NORMALRET_M1_LONG / DESIGN_V2`. This is a pre-outcome clarification of the same economic candidate family, not a tuned replacement candidate.

## Immutable history

- Original frozen design: `4f454f5deb4c77151b574f87c29228c859cfc884`.
- Original readiness: `93e85169b9e1417902d3c13ac91fc1c304dda4f8`.
- Original verdict: `G1_BLOCKED_DATA_OR_EXECUTION` at `G1.2_FROZEN_SIGNAL_IDENTITY` because the selection-relevant Q90 estimator was unspecified.
- Updated main after accepted candidate-neutral maintenance: `980c6b90b7f316bcec6a9466dfc9b0e1756cc748`.

Neither historical commit was amended or rewritten.

## Binding repair

Sort the 49 prior split-consistent dollar volumes without deduplication. Compute the one-based index `j=1+floor(9*(49-1)/10)=44`; set `Q0.90=x_(44)`. There is no interpolation. The abnormal-volume intensity remains `DV(t)/x_(44)`.

Primary GKM methodology supports an empirical ordinal treatment but does not define this engineered denominator. The original design shows rank-threshold intent but remains incomplete. The deciding evidence is the repository's deterministic production-signal quantile convention, introduced in ancestor commit `2ac8d4452eb108b9350a4e3a9287f6c8624c8188` before the abnormal-volume campaign. The repair used no outcome data.

## Scheduler repair availability

Updated main exposes a candidate-neutral exchange-calendar primitive for completed month-end close to next-month first open, with exact-session validation and no later-session fallback. Fill-day completed volume is not used at the open. The legacy generic monthly cadence remains unchanged and is forbidden for this candidate.

## Complete semantic diff

V2 differs from V1 only by:

1. the explicit `x_(44)` Q90 definition;
2. availability and mandatory use of the corrected generic month-end/first-open primitive.

Every other signal, universe, lifecycle, sizing, capital, execution, chronology, exclusion, and future G2–G9 rule remains frozen. `DESIGN_DIFF_FROM_V1.md` is the binding enumeration.

## Versioning and governance

The original label remains blocked historical evidence. `/ DESIGN_V2` is required because the clarification can affect six-slot ordering, but no new `_V2` economic candidate is minted: the formula family and score direction are unchanged and the missing mathematical convention is now explicit before outcomes.

No candidate target, future return, P&L, NAV, risk/performance statistic, IC, hit rate, bucket return, estimator-performance comparison, or candidate/V3 outcome correlation was calculated or read. No production code, configuration, PAPER, LIVE, or promotion state changed in Stage B.

## Next step

After the nine repair artifacts receive a clean committed full SHA, that SHA becomes `FROZEN_DESIGN_V2_SHA`. Only then may a fresh `research/abnormal-volume-readiness-v2` branch run G1 reconstruction. Economics remain prohibited until G1 passes.
