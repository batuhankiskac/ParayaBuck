# Public availability and revision audit

Retrieved 2026-09-22. For a causal decision, the controlling time is when a fact was demonstrably available to a public investor, not the date on the document or the later event date it describes.

## Evidence ledger contract

For each assertion store: `event_id`, `assertion_type`, literal value, expected/confirmed/corrected status, parent/child CIK where established, source issuer, form and accession (if EDGAR), exact document URL, official filing date, EDGAR acceptance datetime with timezone, report period, original issuer release publication datetime if independently archived, observed retrieval datetime, first defensible public-availability bound, superseded-by accession/URL, parser/extraction version, and a source-text excerpt/location. For CIK/ticker assertions also store share class and exchange. A backfilled observation cannot be assigned a public time merely from the current URL's existence.

The [SEC FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions) says the EDGAR header includes `<ACCEPTANCE-DATETIME>`, official `FILED AS OF DATE`, and distinct report period; critically, **SEC does not have a timestamp indicating when filing content first appeared on sec.gov**. Thus acceptance time is a necessary provenance marker and generally a lower bound on public availability, **not an exact proof of first-public visibility**. A conservative historical simulation should use the next normal decision/open after a documented accepted filing plus an explicit nonzero publication/ingestion lag, and exclude events whose timing cannot be bounded before that open. For issuer-only releases, require an original issuer/official archive with an independently defensible timestamp; a date-only press page is insufficient for same-day pre-open entry. If an EDGAR exhibit copies a release, its EDGAR acceptance can establish a conservative availability bound even if the issuer had published earlier.

The [Solventum Form 10 amendment index](https://www.sec.gov/Archives/edgar/data/1964738/000162828024009297/0001628280-24-009297-index.htm) concretely shows accepted **2024-03-05 21:27:09** and filed **2024-03-06**. Using official filing date as an exact first-public timestamp would lose the actual sequence. Conversely, the [Solventum completion 8-K](https://www.sec.gov/Archives/edgar/data/1964738/000162828024014887/solventum-closing8xk4324.htm) documents the April 1 03:30 ET effective event only in a later April 4 filing; one cannot use that 8-K as causal confirmation for an April 1 opening trade. Earlier 3M/Solventum expected schedule evidence exists, but whether completion was publicly confirmed before that open needs separately timed proof. The [GRAIL preliminary statement](https://www.sec.gov/Archives/edgar/data/1699031/000119312524132022/d556103dex991.htm) contains blank dates and explicit board cancellation conditions, so it cannot be promoted to a completed event. The [June 24 Illumina release as SEC exhibit](https://www.sec.gov/Archives/edgar/data/1110803/000119312524167025/d851030dex991.htm) confirms completion and June 25 regular-way start; its actual accession acceptance time must be checked before claiming June 25 open availability.

## Decision rule

For a contemplated entry, use only the latest fact version with defensible public availability strictly before the decision cutoff. Completion and regular-way availability must both be proved. If the required source is accepted after market open, after-hours, date-only, amended later, or cannot be located: move to the next eligible open or mark `UNKNOWN/INELIGIBLE`; never backdate. Historical first-public proof may require contemporaneous exchange notice or issuer release archive alongside EDGAR. No counts of missing publication times have yet been established, so the full-period capability gate remains unverified.

## Source record

| Title; issuer/author; date; sample period; access | Exact claim |
|---|---|
| [EDGAR FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions); SEC; maintained page viewed 2026-09-22; all EDGAR; full text | Acceptance and filing/report dates differ; no first-visible sec.gov timestamp. |
| [Solventum amendment filing index](https://www.sec.gov/Archives/edgar/data/1964738/000162828024009297/0001628280-24-009297-index.htm); SEC/Solventum; accepted 2024-03-05; 2024 event; full index | Acceptance evening before official filing date. |
| [Solventum completion 8-K](https://www.sec.gov/Archives/edgar/data/1964738/000162828024014887/solventum-closing8xk4324.htm); Solventum; 2024-04-04; 2024 event; full text | Later filing cannot justify April 1 pre-open knowledge. |
| [GRAIL information statement](https://www.sec.gov/Archives/edgar/data/1699031/000119312524132022/d556103dex991.htm); GRAIL; 2024-05-06; 2024 event; full text | Proposed/blank terms and cancellation conditions. |
| [Illumina completion release](https://www.sec.gov/Archives/edgar/data/1110803/000119312524167025/d851030dex991.htm); Illumina; 2024-06-24; 2024 event; full text | Confirmed completion and planned next regular-way date, subject to accession-time check for entry. |

All retrieved 2026-09-22. This audit does not assert that any particular opening trade passed the full timestamp gate.
