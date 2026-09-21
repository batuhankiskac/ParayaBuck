# Quantile estimator research

## Decision

`QUANTILE_SPEC_REPAIRED`

The selection-relevant `Q0.90(DV[t-49:t-1])` denominator is the repository empirical rejection-threshold order statistic. For sorted reference observations `x_(1) <= ... <= x_(n)`:

`j = 1 + floor(9 * (n - 1) / 10)` and `Q0.90 = x_(j)`.

Here `n=49`, so `j=44` and `Q0.90=x_(44)`. This is an order statistic with no interpolation.

## Evidence hierarchy

### 1. Primary literature

Gervais, Kaniel, and Mingelgrin (2001), [“The High Volume Return Premium”](https://rodneywhitecenter.wharton.upenn.edu/wp-content/uploads/2014/04/9901.pdf), defines the daily event ordinally. Formation-day dollar volume is ranked with 49 reference days; rank 50 is highest, and ranks 46 through 50 are high volume. The paper therefore supports an empirical rank/extreme-volume treatment rather than a synthetic interpolated cutoff.

The paper does not define `Q0.90` for the V1 candidate's later, engineered prior-49 intensity denominator. It does not choose between `x_(44)`, inverse-ECDF nearest-rank `x_(45)`, or an interpolated value for that denominator. Literature alone therefore does not uniquely repair V1.

### 2. Original design intent

At `4f454f5deb4c77151b574f87c29228c859cfc884`, `CANDIDATE_DESIGN.md` freezes `DV(t)/Q0.90(DV[t-49:t-1])` as a secondary ordering after the inclusive-50 ordinal volume rank. `LITERATURE_REVIEW.md` describes GKM as an own-history ordinal shock. The design calls the ratio a selection-relevant engineering extension, not published GKM methodology.

This supports a historical empirical threshold, but the original artifacts do not specify an estimator. Readiness SHA `93e85169b9e1417902d3c13ac91fc1c304dda4f8` correctly blocked rather than choosing one.

### 3. Pre-existing repository convention

Commit `2ac8d4452eb108b9350a4e3a9287f6c8624c8188`, dated 2026-08-23 and an ancestor of the 2026-09-21 V1 design, introduced candidate-neutral production signal helper `reject_quantile_threshold` in `src/parayabuck/portfolio/momentum.py`. Its binding formula is `sorted(values)[int(q * (n - 1))]`, with deterministic input-order independence and threshold ties kept. It is used by the production residual-momentum IVOL filter and covered by `tests/test_portfolio_momentum.py`.

The repository also contains a linear-interpolation percentile for execution telemetry. That observability summary is not a portfolio-signal threshold. The pre-existing production signal convention is the closer and authoritative candidate-neutral precedent.

The source helper informally calls its rule “nearest-rank.” The V2 specification does not rely on that ambiguous label: conventional inverse-ECDF nearest rank is `ceil(q*n)` and would give `x_(45)` here. V2 freezes the software-independent repository formula and the resulting `x_(44)` explicitly.

### 4. Governance fallback

No fallback choice is needed because Priority 3 resolves the ambiguity without outcomes. The choice is also statistically coherent with the rank-based anomaly: for 49 distinct observations, `x_(44)` leaves 44/49, or approximately 89.8%, at or below the threshold, closer to 90% than `x_(45)`.

## Synthetic materiality checks

These examples contain no future return or candidate outcome:

- For `x=(1,2,...,49)`, V2 gives `Q0.90=44`; Type-7 linear interpolation gives `44.2`; inverse-ECDF nearest rank gives `45`.
- For 44 ones followed by five hundreds, V2 gives `1`; linear interpolation gives `20.8`; the higher/inverse-ECDF boundary gives `100`.
- For 43 ones followed by six twos, V2 gives `2`; for 44 ones followed by five twos, it gives `1`. Duplicate observations occupy ordinary sorted positions; they are never averaged.

No estimator was evaluated against returns or economic performance.
