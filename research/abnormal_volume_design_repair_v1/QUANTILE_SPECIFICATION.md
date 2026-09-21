# Binding quantile specification

Candidate design version: `AV49_D1_NORMALRET_M1_LONG / DESIGN_V2`.

For each otherwise eligible security at formation session `t`:

1. Form the 49-element multiset `X={DV(t-49),...,DV(t-1)}`. Every member must already satisfy the frozen positive, finite, complete-data rule.
2. Sort without deduplication: `x_(1) <= x_(2) <= ... <= x_(49)`.
3. Compute the one-based index using exact integer arithmetic:

   `j = 1 + floor(9 * (49 - 1) / 10) = 44`.

4. Define `Q0.90(X) = x_(44)`.
5. Define abnormal-volume intensity `I(t) = DV(t) / x_(44)`.

There is no interpolation, midpoint, averaging, rounding of values, or library-default percentile. Reference ties remain repeated observations and occupy repeated order-statistic positions. Because the frozen data rule requires every reference dollar volume to be positive, the denominator is strictly positive.

The complete selection order remains:

1. descending inclusive-50 ascending-average dollar-volume rank;
2. descending `I(t)`;
3. ascending stable security identity.

Exact equal intensities are not perturbed by epsilon; they proceed to stable identity. The inclusive-50 volume-rank eligibility rule and its average-tie ranks remain separate from this prior-49 order statistic.

Reconstruction must implement the formula above directly or prove exact equivalence. A bare `quantile`, `percentile`, or library-default call is nonconforming.
