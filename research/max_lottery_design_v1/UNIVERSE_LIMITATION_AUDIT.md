# Universe Limitation Audit

## Verdict

`UNIVERSE_LIMITATION_INVALIDATES_MAX_TEST`

## Why this limitation is unusually dangerous for LOW-MAX

The historical and live universe is broad `US_EQUITY`, not a proven
point-in-time common-stock universe. The scanner can admit ETFs, ETNs,
preferreds, warrants, units, ADRs, and structured products if ordinary price,
liquidity, completeness, ATR, and static leveraged/inverse checks pass.

LOW-MAX ranks the smallest extreme positive daily return as best. Product type
can dominate that ranking:

- bond and short-duration ETFs can naturally have very small daily maxima;
- defensive and low-volatility ETPs can rank as attractive without representing
  firm-level lottery-demand mispricing;
- preferreds can exhibit bond-like maxima;
- stale or episodically traded products can look artificially quiet;
- warrants, units, volatility products, and structured products can populate
  the opposite tail for mechanical reasons.

This is not generic label noise. It can turn the proposed signal into an
unintended product-type classifier. A six-name portfolio magnifies the problem:
even one or two non-common-stock products can dominate the interpretation.

## Why current gates do not solve it

The $5 price and $20 million median-dollar-volume gates improve tradability but
do not identify security type. The ATR band and leveraged/inverse list remove
some extreme products, not ordinary bond/defensive ETFs, preferreds, ETNs, or
all structured products. The spread-quality setting is soft rather than a hard
50 bps exclusion. There is no point-in-time market-cap variable or complete
effective-dated security master, and this task forbids inventing a proxy or
solving the security-master problem.

The seminal and strongest later evidence studies firms/common stocks. Testing
LOW-MAX on broad products would not be a faithful replication or a clean
translation of the economic mechanism.

## Shared A/B comparability is insufficient

Using the same broad universe for V3 and MAX would provide mechanical A/B
symmetry, but not construct validity. V3's positive residual-momentum gate and
SPY-relative model may naturally exclude many low-motion products; LOW-MAX
would positively seek them. Shared contamination is therefore not shared
exposure.

## Required future unblock condition

A future redesign may begin only after a verified, point-in-time,
effective-dated ordinary-common-stock taxonomy is available for every eligible
session, with fail-closed treatment of unknown product types. That work is out
of scope here. No ad hoc current-symbol blacklist is acceptable.
