# Signal reconstruction audit

The frozen rule unambiguously defines:

- candidate `AV49_D1_NORMALRET_M1_LONG`;
- 49 prior sessions plus the formation session;
- dollar-volume rank within the inclusive 50-observation own-security time series;
- ascending average ranks and high-volume eligibility at rank 46–50;
- 50 close-to-close formation returns, requiring the preceding close;
- formation-return rank 16–35 within the same security's inclusive 50-return history;
- long-only selection, at most six names;
- final ordering by descending volume rank, then descending `DV(t)/Q0.90(DV[t-49:t-1])`, then ascending stable identity;
- equal weight `min(0.10, 0.55/N)` with unused allocation held as cash.

The return comparison axis is own-security time series, not same-day cross-sectional. The prompt's requested “cross-sectional comparison set” is therefore inapplicable; the design authority wins.

## Blocking underspecification

No frozen artifact defines how `Q0.90` is calculated for the 49 prior observations. Linear interpolation, nearest-rank, lower, higher, midpoint, and other accepted quantile conventions can produce different denominators and therefore different six-slot ordering. Because that ordering is explicitly selection-relevant, selecting a library default is not a clerical implementation choice.

A deterministic non-economic counterexample proves selection relevance. Let candidate A's 49 prior dollar volumes contain 44 values of 1 and five values of 100, with current dollar volume 200. Let candidate B's 49 priors all equal 10, with current dollar volume 100. Both current observations have rank 50. For A, a lower quantile gives 1, a higher quantile gives 100, and linear interpolation gives 20.8; its intensity is respectively 200, 2, or about 9.615. B's intensity is 10 under all three. A ranks ahead of B under `lower` but behind B under `higher` or `linear`. With a six-slot cutoff, the selected set can change.

“Stable security identity” naturally maps to canonical `entity_id`, but the quantile ambiguity alone prevents deterministic faithful reconstruction. No targets, ranks, or weights were produced. The frozen design was not edited.
