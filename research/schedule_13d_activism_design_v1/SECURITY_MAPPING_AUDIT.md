# Security mapping audit

## Required mapping chain

The mapping key is not ticker and not issuer CIK alone. At the filing-availability time:

`subject issuer CIK + exact covered class title + CUSIP(s) -> effective-dated security -> canonical lifetime entity -> tradable instrument`

The mapper must retain exact class titles and distinguish common classes, preferred, convertible securities, warrants, ADRs, units, SPAC-related securities, and ambiguous classes. One-to-many, many-to-one, stale-symbol, missing-effective-date, and conflicting-CUSIP results fail closed. Current tickers must never repair historical identity.

## Repository fit

The v5 lifetime source provides effective-dated symbols and terminal semantics but lacks CIK, CUSIP, and covered class. Broad `US_EQUITY` is not a complete point-in-time product taxonomy. Issuer-linked 13D data narrows the search space but does not solve those omissions.

After a unique class mapping, the ordinary scanner still applies active/tradable/exchange/product, price, dollar-volume, ATR, and data-completeness gates. A 13D target is not entitled to bypass them. Delistings and takeovers require existing terminal-lifecycle handling and real tradable bars.

Mapping does not itself admit a newly disclosed target to observation. Scanner pool membership is caller-owned, runtime pins can be force-retained, and only the capped observation set is streamed. A target absent from the point-in-time candidate file/observation set may have no causal data at the decision time. Any future research harness would need synthetic tests for absent/present candidates, ordinary eligibility failure, stream-cap admission, pin behavior without gate bypass, and stale-universe handling. This is an additional integration block, not permission to expand the production scanner.

## Verdict

`13D_SECURITY_MAPPING_BLOCKED`

The required external, effective-dated CIK/CUSIP/class crosswalk and raw covered-class fields are absent. This is a material implementation block, but the overall design decision remains rejection because the economic hypothesis already fails the pre-outcome evidence gate.
