# SEC source audit

## Coverage and authority

The official [Insider Transactions Data Sets](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets)
cover January 2006 through June 2026 (82 quarters), are refreshed quarterly,
and flatten as-filed ownership XML for Forms 3, 4, 5 and amendments. This gives
more than the three prior calendar years needed before a 2017 research start.

The SEC warns that the extracts can contain filer or extraction errors, omit
certain filing metadata, and are not substitutes for filings. The
[official readme](https://www.sec.gov/files/insider_transactions_readme.pdf)
also says amendments, redundancies, inconsistencies, and discrepancies remain
as filed.

Accordingly:

1. quarterly bulk is discovery and normalization input;
2. raw ownership XML plus complete-submission header is authoritative;
3. the submissions API is a timestamp/metadata cross-check;
4. rendered HTML is a human rendering check only.

## Field matrix

| Concept | Bulk | Raw requirement |
|---|---|---|
| accession | yes | retain header accession |
| filing date | yes | cross-check |
| acceptance time | no | raw header / submissions API |
| transaction date | yes | XML value + footnote refs |
| issuer CIK/name/as-filed symbol | yes | XML canonical |
| owner CIK/name/relationship/title | yes | preserve repeated owners |
| Table I/Table II | separate tables | preserve XML table identity |
| transaction code/direction | yes | XML canonical |
| shares/price | yes; price schema is two decimals | XML preserves higher precision |
| direct/indirect ownership | yes | XML plus nature/footnotes |
| amendment | document type and original date | append-only raw version; relationship may remain ambiguous |
| 10b5-1 | filing-level field only in 2023+ refreshed sets | raw filing-level field; plan date is free text |
| footnotes/remarks | yes | raw text and field references required |

## Important precision and identity limits

Bulk `TRANS_PRICEPERSHARE` is documented as `NUMBER(16,2)`. Raw XML can contain
more precision; exact notional or weighted-average work must use raw XML.
Indexes locate filings but do not provide acceptance time. There is no universal
economic-trade identifier across accessions.

## Acquisition result

The official HTML documentation was reviewed. A non-personal repository-URL
User-Agent received HTTP 403 from the ZIP endpoint. No personal contact value
was transmitted and no credentials were persisted. Therefore no bulk rows were
used for counts in this campaign; this is recorded as a source-acquisition
block, not as evidence of zero events.

## Sources

- [SEC Insider Transactions Data Sets](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets)
- [SEC Insider Transactions readme](https://www.sec.gov/files/insider_transactions_readme.pdf)
- [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [Accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
