# Volume semantics audit

## Decision

Use split-consistent daily **dollar volume**:

`DV(i,t) = split_adjusted_close(i,t) * split_adjusted_share_volume(i,t)`

This matches GKM's economic object without requiring shares outstanding. It is explicitly not turnover, raw share volume, cross-sectional dollar-volume level, a z-score, or a liquidity factor.

For each security and formation session `t`, rank `DV(t)` among the inclusive 50-session vector `DV[t-49:t]`. Eligibility requires `rank(DV(t)) >= 46`, using ascending average ranks for ties. This preserves GKM's top-decile ordinal definition. The return-control rank uses the analogous inclusive 50 daily close-to-close returns and requires ranks 16–35 inclusive.

Because a six-slot architecture needs an order among eligible names, define the complete frozen abnormal-volume ordering as:

1. descending inclusive-50 dollar-volume rank;
2. descending `DV(t) / quantile_0.90(DV[t-49:t-1])`;
3. ascending stable security identity.

Item 2 is a disclosed continuous abnormal-volume-intensity selection key. It is an engineering extension necessitated by the six-slot limit and is not published GKM methodology. It uses the same dollar-volume series and own-history concept, but it can change which eligible names are held and must not be mislabeled as neutral. It may not change after outcomes are viewed.

## Input validity

- Require 50 complete, finite, strictly positive adjusted closes and volumes ending at `t`, plus the prior close needed for the first return.
- A zero, blank, nonnumeric, negative, or nonfinite volume invalidates the security at that decision. Do not impute, forward-fill, replace with epsilon, or interpret adjusted zero as a no-trade fact.
- Require a unique security identity and active point-in-time scanner/lifecycle eligibility at `t`.
- Every signal field must be timestamped no later than the completed formation close. Orders cannot be filled before the next tradable open.

## Rejected alternatives

- Turnover: infeasible without point-in-time shares outstanding and represents a different paper.
- Raw shares: unstable across splits and cross-security share units.
- Relative volume versus mean: less literature-faithful and sensitive to outliers.
- Cross-sectional volume: confounds firm scale/liquidity with the shock.
