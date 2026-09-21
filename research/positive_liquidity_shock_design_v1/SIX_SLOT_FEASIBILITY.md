# Six-slot feasibility

## Verdict

`LIQUIDITY_SHOCK_PREMIUM_REQUIRES_DIVERSIFICATION`

The academic evidence uses NYSE-breakpoint deciles, 5×5 double-sort cells,
value/equal-weighted broad portfolios and Fama–MacBeth slopes. It establishes
that the high tail is informative on average. It does not establish that the
six most extreme observations are stable, correctly measured or sufficiently
diversified.

Amihud extremes are especially exposed to denominator noise, zero/low-volume
days, stale trading, corporate actions and issuer-specific news. The $20M/top-
300 scanner removes much of the population where the anomaly is strongest,
and external evidence supplies no order-statistic or six-name validation.

Structural availability of six scoreable names is necessary but not evidence
that a six-name portfolio preserves the premium. Equal weighting would be a
transparent adaptation because PIT market capitalization is unavailable, but
the literature does not prove that this concentrated adaptation is sound.

In production, `max_positions=6` is account-wide. Two crypto positions can
leave only four equity slots, so even a successful isolated top-six experiment
would not be deployment-ready.
