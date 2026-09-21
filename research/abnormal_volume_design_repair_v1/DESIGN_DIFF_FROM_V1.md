# Design diff from frozen V1

Historical authority:

- original candidate: `AV49_D1_NORMALRET_M1_LONG`;
- original frozen design: `4f454f5deb4c77151b574f87c29228c859cfc884`;
- original blocked readiness: `93e85169b9e1417902d3c13ac91fc1c304dda4f8`;
- updated maintenance main: `980c6b90b7f316bcec6a9466dfc9b0e1756cc748`.

V1 remains immutable blocked evidence. V2 has exactly two semantic differences:

1. **Quantile clarification.** V1's undefined `Q0.90(DV[t-49:t-1])` is now the explicit repository empirical order statistic `x_(44)`, where `j=1+floor(9*(n-1)/10)` for `n=49`. No interpolation is permitted.
2. **Correct generic scheduler availability.** Updated main provides the candidate-neutral `month_end_first_open` and `require_first_open_bar` contract for completed month-end close to next-month first exchange open, with no later-session fallback. The legacy `RebalancePolicy("monthly")` path remains unchanged and forbidden for this candidate.

No other candidate or gate semantics differ. In particular V2 does not change the 49-session reference, one-day formation, dollar-volume measure, inclusive-50 high-volume rank, middle-40% return condition, direction, score direction, calendar cadence, holding convention, top six, equal weighting, capital/caps, scanner, exclusions, lifecycle rules, missing-data rules, or G2–G9 thresholds.

## Versioning

This is a specification completion inside the same economic candidate family, so the identifier is `AV49_D1_NORMALRET_M1_LONG / DESIGN_V2`, not a new `_V2` candidate. The repair makes the already intended `Q0.90` denominator uniquely reconstructible using a pre-campaign convention; it does not replace the score or change its direction. Because the clarification is selection-relevant, it receives an explicit design version and never overwrites V1.
