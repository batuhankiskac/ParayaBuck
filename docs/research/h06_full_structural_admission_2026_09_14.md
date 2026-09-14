# H06 full structural admission — acquisition blocker record

Status: `H06_FULL_ACQUISITION_INCOMPLETE`

This work began from accepted commit
`e24c355f197b2a804e9b47b7efd7a4db7a5d04d3`. The accepted H06 structural
contracts and the three canonical V5 inputs were verified before acquisition
work began.

## Boundary

This record contains only source inventory, acquisition controls, frozen
row-level structural rules, and fail-closed custody metadata. It contains no
H06 score, issuer aggregation, ranking, decile, order, fill, return, P&L,
portfolio, or holdout outcome data. V3, PAPER, LIVE, the registry, and canonical
V5 bytes remain unchanged.

## Official inventory

The tracked acquisition manifest freezes all 30 required SEC Insider
Transactions Data Set packages from 2019 Q1 through 2026 Q2. The ordinary SEC
path and the 2026 Q2 path exception are recorded individually. July 2026 is
separately defined as 22 EDGAR daily-index dates followed by filing-specific
archive retrieval and unique-accession reconciliation.

The official SEC dataset page documents that these packages are flattened from
ownership XML and currently cover January 2006 through June 2026:
<https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets>.

## Blocking evidence

No operator-approved `SEC_USER_AGENT` with contact information was available in
the process environment. A bounded request to the official 2026 Q2 ZIP returned
HTTP 403. An initial browser-mediated attempt created only a stalled partial
download; those incomplete bytes were rejected. The user then completed the
browser permission step and supplied the finished official 2026 Q2 ZIP. Its
11,498,860 bytes, SHA-256, exact ten-member archive schema, and CRC integrity
were validated and receipt-bound under the ignored local cache. Therefore:

- quarterly packages acquired: 1 of 30 (2026 Q2);
- July index days acquired: 0 of 22;
- unique Form 4 / Form 4-A accession reconciliation: incomplete;
- downstream normalization, lineage, owner-year classification, transaction
  admission, identity, denominator, and decision mapping: not performed.

The status is driven by missing official coverage, not by sample size or an
economic judgment.

## Reproducible continuation

Run the acquisition only after supplying `SEC_USER_AGENT` process-locally with
an operator-approved project and contact identity. The acquisition client
requires official HTTPS SEC URLs, caches successful responses, executes
sequentially at no more than five requests per second, and uses bounded retry
backoff. Large raw files remain under the ignored local root
`data/research/local/h06_full_acquisition_2026_09_14/`.

After complete acquisition, the admission script's pure functions enforce:

- point-in-time amendment visibility;
- owner-CIK-only three-prior-year routine classification using both P and S;
- fail-closed open/private and 10b5-1 treatment;
- all-of transaction gates with one primary exclusion;
- exact identity and shares-fact selection;
- intervening-split and raw-price denominator gates;
- prior-21-session transaction-to-decision membership.

The currently tracked manifests and receipts are self-hashed blocker records.
They must be replaced only by a complete, receipt-bound rebuild; zero counts in
them are explicit `not performed because upstream acquisition is incomplete`,
not claims that the true populations are empty.
