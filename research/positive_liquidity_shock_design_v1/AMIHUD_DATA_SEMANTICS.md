# Amihud data semantics

## Required daily observation

The paper uses a CRSP daily stock return and daily dollar trading volume. A
valid local diagnostic observation requires finite positive current and prior
closes, finite positive volume and one unambiguous session. Zero volume makes
the denominator undefined; it is missing, not zero illiquidity. Missing
sessions, duplicate sessions and nonpositive prices fail closed.

The monthly mean requires at least 15 valid daily return/dollar-volume pairs.
The local structural audit then requires 12 consecutive valid prior monthly
values because the paper supplies no partial-history or imputation rule.

## Local return limitation

The canonical CSV provides split-adjusted OHLCV, not an explicit CRSP total
return. Its cash-dividend file is designed for portfolio cash accounting.
Combining retrospectively split-adjusted prices with a historical per-share
cash dividend without an explicitly matching share basis could manufacture a
return. The structural audit therefore uses close-to-close price returns only,
labels them diagnostic and does not claim exact CRSP-return parity.

This unresolved implementation detail does not motivate a substitute formula.
It would be a G1 issue if the hypothesis otherwise passed. Here the design is
rejected earlier on modern long-only and concentration evidence.

## Auditability

Any future implementation would have to persist daily absolute returns, dollar
volumes, price-impact terms, valid-day counts, monthly `ILLIQ`, the exact 12
reference months and `LIQU`. No future return may enter that table.
