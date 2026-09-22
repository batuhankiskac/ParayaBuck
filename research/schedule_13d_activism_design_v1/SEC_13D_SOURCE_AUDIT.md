# SEC 13D source audit

## Authority and census

The authoritative discovery layer is the SEC daily/full/quarterly master/form index, filtering exact form types `SC 13D`, `SCHEDULE 13D`, `SC 13D/A`, and `SCHEDULE 13D/A`. The index provides discovery metadata, not the complete economic record. Each discovered accession must be archived from SEC EDGAR as an immutable package:

1. source index path, vintage, bytes, and SHA-256;
2. accession number and accession index JSON/XML;
3. complete submission `.txt` and every document/exhibit;
4. retrieval timestamp, URL, HTTP receipt, byte length, and SHA-256;
5. parser and schema versions plus parse status.

SEC access guidance: <https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data>.

## Required normalized fields

- Raw and dashed accession, exact form, amendment flag, archive filename.
- Raw `<ACCEPTANCE-DATETIME>`, Eastern-time interpretation, `FILED AS OF DATE`, and any stated event/transaction date.
- Subject-company CIK/name/file number kept separate from submission/filer/agent identities.
- Each cover-page reporting person, any explicit CIK, person/entity type, jurisdiction, group text, voting/dispositive powers, aggregate ownership, percentage, and excluded-shares flag.
- Exact covered class title, raw/normalized CUSIP, multiple-CUSIP rows, shares, percentage, denominator text, and source fragment.
- Items 1–7 raw markup/text, normalized text, hashes, source offsets, and amendment operation.

## Historical format dispatch

Mandatory Schedule 13D/G XML did not begin until 2024-12-18. The parser must therefore support legacy ASCII/HTML, PDF/manual-review cases, voluntary XML, and mandatory XML. XML structures the filing but exhibits remain unstructured. This is SEC-specific XML, not XBRL. Official specification: <https://www.sec.gov/file/schedule-13d-13g-tech-specs-20>.

Unreadable documents, PDF-only disclosures, conflicting identities, and incomplete covered-class data fail closed. Submissions JSON is a discovery/cross-check source, never the sole historical authority.

## Acceptance versus public availability

`<ACCEPTANCE-DATETIME>` is EDGAR acceptance time. It is not a guaranteed first-public timestamp. The SEC says postings commonly follow the EDGAR timestamp by roughly one to three minutes but does not record a first-public-availability timestamp: <https://www.sec.gov/about/webmaster-frequently-asked-questions>.

A causal future design would retain raw acceptance time and use a conservative dissemination lag before assigning the next eligible regular-session open. Filing date, daily-index build time, ownership threshold date, and activist transaction date are not substitutes.

## Source verdict

`AUTHORITATIVE_RECONSTRUCTION_SPECIFIED_BUT_LOCAL_ACCESSION_CORPUS_ABSENT`

The official census is reconstructible. Full classification and mapping were intentionally not fabricated from index rows.
