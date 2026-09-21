# Liquidity-shock formula specification

## Published primary formula

For stock `i`, day `d`, and month `t`:

```text
DV_i,d    = price_i,d * share_volume_i,d
DAILY_i,d = abs(R_i,d) / DV_i,d
ILLIQ_i,t = mean_d_in_t(DAILY_i,d) * 1,000,000
LIQU_i,t  = -(ILLIQ_i,t - mean(ILLIQ_i,t-12 ... ILLIQ_i,t-1))
```

The final RFS `LIQU` is demeaned only. It is not divided by prior mean or
standard deviation. The current month is excluded from the reference period.

Paper-level rules:

- require at least 15 daily returns for monthly `ILLIQ`;
- daily dollar volume must be positive;
- positive `LIQU` ranks above negative `LIQU`;
- use month `t` information only for a month `t+1` portfolio.

Fail-closed local rules needed for any future implementation:

- require 12 consecutive valid reference months;
- reject nonfinite/nonpositive price or volume, duplicate sessions and a zero
  reference history only when the arithmetic itself is undefined;
- never impute zero volume, add an epsilon or shorten IPO history;
- retain all daily terms, valid-day counts, monthly values and reference
  months in an audit trail.

## Worked sign example

Suppose prior monthly `ILLIQ` values have mean `0.40` and current monthly
`ILLIQ` is `0.25`:

```text
LIQU = -(0.25 - 0.40) = +0.15
```

Liquidity improved, so the shock is positive. If current `ILLIQ` were `0.55`,
`LIQU=-0.15`, a negative liquidity shock. This also proves why ranking raw
illiquidity in the opposite direction is not equivalent to measuring a shock.

## No frozen candidate

This specification records the published accounting of the signal, but no
candidate is frozen because modern liquid-stock evidence and concentrated
expression fail independent design gates.
