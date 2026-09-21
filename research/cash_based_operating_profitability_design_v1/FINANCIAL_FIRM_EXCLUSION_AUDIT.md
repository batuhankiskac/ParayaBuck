# Financial-firm exclusion audit

## Verdict

`PIT_FINANCIAL_EXCLUSION_FEASIBLE`

The paper excludes one-digit SIC 6. The causal source is the raw accession
SGML/header SIC that was public with each annual filing:

- exclude `6000 <= SIC <= 6999`;
- missing, `0000`, malformed or conflicting SIC fails closed;
- today's Submissions SIC is diagnostic only and never backfills history.

The 14-issuer pilot intentionally contains JPMorgan and Citigroup; both current
Submissions records report SIC 6021 and are excluded diagnostically. That does
not substitute for parsing the header of every historical accession.

Only 10-K/financial-statement-bearing 10-K/A using US-GAAP are in scope. 20-F,
20-F/A, 40-F, IFRS facts, foreign-private-issuer ADRs, and 10-KT transition
years are excluded. This source rule is accounting fidelity, not a performance
filter.
