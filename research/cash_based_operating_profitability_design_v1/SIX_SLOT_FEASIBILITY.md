# Six-slot feasibility

## Verdict

`CBOP_REQUIRES_DIVERSIFICATION_INCOMPATIBLE_WITH_ARCHITECTURE`

The literature supports value-weighted deciles, 2x3 factors and continuous
cross-sectional slopes. It does not test the top six issuers, order-statistic
stability at that tail, or a single best issuer. The modern large/liquid study
requires at least 20 stocks per leg. Therefore:

- “top decile works” does not imply “top six works”;
- positive long-short evidence does not prove a concentrated long-only leg;
- equal-weight top-six would be an unvalidated departure from the paper's
  value-weighted evidence;
- historical PIT market capitalization is not canonical, so value weighting
  cannot be honestly reproduced today.

The production limit is account-wide. If crypto owns two positions, only four
slots remain; four 10% equity positions express at most 0.40 of the 0.55 equity
sleeve. Even a later equity 5+1 layout conflicts with concurrent crypto until a
separate total-account deployment review.

There is likewise no externally defensible top-one CbOP rule for a future
satellite. Alpha density at the extreme tail is unknown. No `top_n`, weighting
or allocation is frozen.
