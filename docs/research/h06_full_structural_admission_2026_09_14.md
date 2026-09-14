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

## Acquisition progress and remaining blocker

A declared SEC User-Agent was constructed only in process memory from the
existing local Git identity. Its value was never printed or persisted. The
official landing page was cached and parsed, and exactly one published href was
resolved for every required quarter. The previously supplied 2026 Q2 file was
reused after SHA, CRC, and schema verification. The other 29 packages were
downloaded using serial one-request-per-second access, atomic `.part` files,
bounded retry, and fail-closed ZIP validation. Therefore:

- quarterly packages acquired: 30 of 30;
- quarterly compressed bytes: 321,592,193;
- quarterly Form 4 submissions: 1,368,412;
- quarterly Form 4/A submissions: 24,959;
- July daily indexes acquired: 22 of 22 from the discovered SEC directory;
- July index Form 4 entries: 22,876;
- July index Form 4/A entries: 404;
- July unique Form 4/4-A accessions: 11,242;
- July archive submissions verified: 435 of 11,242;
- unique Form 4 / Form 4-A accession reconciliation: incomplete, 10,807 remaining;
- downstream normalization, lineage, owner-year classification, transaction
  admission, identity, denominator, and decision mapping: not performed.

The remaining acquisition blocker is filing-specific archive retrieval and
binding for 10,807 July accessions. The acquisition was stopped cleanly after
SEC response latency increased; the next invocation revalidates and skips all
435 completed files. The population-scale structural runner
also does not yet exist; the current admission module contains frozen row-level
rules and offline contract tests only. The status is not based on sample size or
an economic judgment.

## Reproducible continuation

The acquisition client resolves an operator identity from `SEC_USER_AGENT`, a
local untracked environment file, or local Git identity, without persisting the
value. It requires official HTTPS SEC URLs, caches successful responses,
executes sequentially at one request per second, and uses bounded retry backoff.
Large raw files remain under the ignored local root
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
