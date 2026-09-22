# Historical security mapping
CIK identifies a registrant, not automatically a unique tradable common equity. Joint filings, multiple classes, ADRs, ticker reuse, mergers and changing share units remain relevant. An issuer-linked source reduces accidental ETF/ETN inclusion but does not certify instrument eligibility.

The current historical lifecycle schema has 16 fields and no universal CIK bridge. Archived Form 4 research already demonstrated a bounded fail-closed method using as-filed symbol, dated lifetime intervals, as-of CIK collision checks and common-security descriptions. This is reusable methodology, not a complete management-guidance panel. Source inspected: archived `research/opportunistic_insider_source_reassessment_v1/SECURITY_MAPPING_REASSESSMENT.md` on `archive/research/opportunistic-insider-source-reassessment-v1-2026-09`.

Required join: filing accession → actual EPS-guiding registrant CIK → as-filed security/class and symbol → canonical entity whose lifetime contains disclosure date → dated eligible common equity. Retain all evidence and reject missing, conflicting or many-to-many resolutions. Do not use today's ticker or a later merged issuer to fill history. Delisted entities require historical coverage, not deletion from the sample.

Six CIKs were identified in the specimen ledger; zero canonical lifecycle joins were adjudicated. This is not six mapped tradable issuers. PG&E's joint filing demonstrates why the parent EPS cannot be assigned to every registrant/security. Split reconciliation also remains a separate per-share-basis check.
