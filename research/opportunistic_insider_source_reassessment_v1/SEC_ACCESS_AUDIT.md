# SEC access audit

## Verdict

`OFFICIAL_SEC_BULK_SOURCE_USABLE_WITH_ONE_CURRENT_QUARTER_ACCESS_GAP`

The SEC quarterly Form 3/4/5 ZIP files are the primary source. Requests used a
declared research product and a user-supplied administrative contact in the
HTTP User-Agent. The contact address is intentionally not persisted here or in
any tracked artifact.

The cache contains 53 official quarterly archives from 2013-Q1 through
2026-Q1. The requested 2026-Q2 file was not acquired: the legacy `www.sec.gov`
path returned HTTP 404, while the Data.gov catalog's current official
`dcm.sec.gov` resource could not be resolved by this environment's DNS. This is
an explicit current-quarter acquisition gap, not silently treated as zero data.

## Fair-access contract

- Declare an organization/product and administrative email in User-Agent.
- Keep aggregate traffic at or below 10 requests/second; this run was strictly
  sequential with retry/backoff.
- Accept compression, make only necessary requests, and back off on 403, 429,
  and 5xx responses.
- Do not store the personal contact identity in research outputs.

Official references: [Accessing EDGAR Data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data),
[SEC Developer Resources](https://www.sec.gov/about/developer-resources), and
[SEC Webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions).

## Bulk provenance

`STRUCTURAL_MAPPING_COUNTS.json` records filename, byte size, and SHA-256 for
every cached ZIP. The first archive, `2013q1_form345.zip`, has SHA-256
`795607b5f0c1510ffa086ca86f3f45abb54a432f49038f4ace16d8aa6ac12756`.
The full cache is ignored external research data; only receipts and analysis
outputs are committed.

The [Data.gov catalog](https://catalog.data.gov/dataset/insider-transactions-data-sets)
is published by the SEC and links to SEC infrastructure. It is a discovery
catalog, not an independent evidentiary mirror. The SEC's own
[Insider Transactions Data Sets page](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets)
and packaged readme remain the schema authority.

## Deterministic raw-EDGAR trace

The deterministic sample is accession `0000900440-16-000170`, filing date
2016-03-31, issuer CIK `0000929545`, as-filed symbol `CDOR`, owner CIK
`0001194716`.

Archive directory:
`https://www.sec.gov/Archives/edgar/data/929545/000090044016000170/`

Downloaded official objects and SHA-256:

- `index.json`: `d496b3184fcd71bc0ab7d37b7a22be56c8f1fe20cf4425b03b21143c07a62c10`
- filing index HTML: `86203a6c79d5488d0837c34829afb32e864adb26f00acf80c196b1e0b338254f`
- complete submission text: `ee0827cad0fad465dda75301923d412afa48caf86f81cf8e3c56381eb4e6c89a`
- `primary_doc.xml`: `1ac040eb6bb9881c9ca02e2927360df05c273488d43fb4ac1e2b97b9e1a591ab`

The raw XML and 2016-Q1 bulk tables agree on issuer CIK/symbol, owner CIK, two
Table-I code-P acquisitions, shares 131 and 69, and prices 1.6 and 2.1. The
accession prefix is not assumed to be the issuer CIK; the archive path is
resolved and then identifiers are checked in the raw filing.

## Authority boundary

Raw EDGAR index/header/submission/XML is authoritative for a selected filing.
Quarterly bulk tables are reproducible discovery and reconstruction inputs.
Acceptance timestamp is absent from the bulk tables, so `FILING_DATE` is only a
conservative day-level public-availability proxy; a live collector would need
its own first-observed timestamp.
