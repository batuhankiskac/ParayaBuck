# Event timing audit

Retrieved 2026-09-22. No candidate return series, event window, or P&L was read.

## Eight clocks that must not be collapsed

| Stage | Meaning and admissible proof | Earliest economic claim |
|---|---|---|
| 1. Announcement | Parent board/issuer release or 8-K announces an intended separation. | Parent reaction only; no child regular-way security yet. |
| 2. Registration/information statement | Child Form 10-12B or amendment, commonly Exhibit 99.1, describes business and proposed distribution; terms can remain blank or change. | Disclosure only. |
| 3. Effectiveness | SEC effectiveness of the child Exchange Act registration, proven by dated SEC notice/issuer 8-K. | Does not prove the separation occurred or shares trade. |
| 4. Record date | Holder cut-off; entitlement trading can differ from parent ordinary ticker trading. | Does not identify a regular-way child execution. |
| 5. Distribution/effective time | Shares are released/credited and event completed, proven after the fact contemporaneously. | Distinct from record and exchange opening. |
| 6. When-issued child trading | Conditional/pre-distribution market, often `WI` suffix; may end on a different session. | Excluded from proposed strategy. |
| 7. First child regular-way session | Exchange/issuer states the actual regular-way child ticker/date; historical daily bars alone do not label market regime. | First possible normal listed-share session. |
| 8. First ParayaBuck-executable open | First regular-way open **after** all required evidence was publicly available and the decision process could have ingested it. If confirmation arrived after that open, defer to the next eligible open. | Only possible strategy entry; any earlier return belongs outside this design. |

The [GRAIL preliminary information statement](https://www.sec.gov/Archives/edgar/data/1699031/000119312524132022/d556103dex991.htm) explicitly distinguishes when-issued child trading, parent regular-way trading with distribution entitlement, parent ex-distribution trading without the entitlement, and later child regular-way trading. Its preliminary fields contain blanks, so an early Form 10 is not final-date authority. Illumina's [June 24, 2024 completion release filed as Exhibit 99.1](https://www.sec.gov/Archives/edgar/data/1110803/000119312524167025/d851030dex991.htm) states June 13 record date, June 24 distribution, and June 25 GRAL regular-way trading. By contrast, [3M's March 2024 release archived in EDGAR](https://www.sec.gov/Archives/edgar/data/66740/000006674024000023/pressrelease3-8x24.htm) says SOLV regular-way began **on** the April 1 distribution date; [Solventum's completion 8-K](https://www.sec.gov/Archives/edgar/data/1964738/000162828024014887/solventum-closing8xk4324.htm) says the distribution was effective April 1 at 3:30 a.m. ET. The rule `distribution date + 1 calendar day` is therefore false even in two 2024 cases; holidays and exchange-specific schedules further require explicit per-event evidence.

An SEC [DERA spin-off paper's timeline](https://www.sec.gov/files/dera-wp-differences-opinion-evidence-spinoffs.pdf) distinguishes announcement, final information statement, declaration, record, ex, payment and when-issued periods. Its Table 4's 10 post-ex-day outcome is a **value-weighted parent-plus-child combination**, not a child long leg. Its ex-date result is not automatically attainable from a next-regular-way-open child purchase. Every external return claim needs its own population, clock start, traded share class, and executable price checked by the literature audit. No return finding from this timing paper is imported into the design.

## Causal clock implementation requirement

Store the issuer's **expected** dates and later **confirmed** dates as separate versioned assertions, each with source acceptance/publication time. Preserve the first evidence of completion, the first credible exchange/issuer regular-way schedule, and any later correction. A valid `first_executable_open` must be calculated from a verified exchange calendar and actual market status, after the maximum availability time of required facts plus configured ingestion/decision latency. A known date in a retrospective filing is not sufficient if that filing was accepted after the proposed open. On ambiguous, delayed, halted, or changed listing dates: `UNKNOWN/INELIGIBLE` until resolved, then enter no earlier than the next causally available open. No post hoc backdating.

## Source record

| Title; issuer/author; date; sample period; access | Exact claim |
|---|---|
| [GRAIL preliminary information statement, Exhibit 99.1](https://www.sec.gov/Archives/edgar/data/1699031/000119312524132022/d556103dex991.htm); GRAIL/Illumina; 2024-05-06 filing; 2024 event; full text | Four distinct trading entitlements/regimes; dates then expected and partly blank. |
| [Illumina completion release](https://www.sec.gov/Archives/edgar/data/1110803/000119312524167025/d851030dex991.htm); Illumina; 2024-06-24; 2024 event; full text | GRAL first regular-way 2024-06-25 after distribution 2024-06-24. |
| [3M Solventum release](https://www.sec.gov/Archives/edgar/data/66740/000006674024000023/pressrelease3-8x24.htm); 3M; 2024-03-08; 2024 event; full text | SOLV when-issued from around March 26, regular-way 2024-04-01. |
| [Solventum completion 8-K](https://www.sec.gov/Archives/edgar/data/1964738/000162828024014887/solventum-closing8xk4324.htm); Solventum; 2024-04-04; 2024 event; full text | Confirmed effective time and listed child ticker; a **post-open** filing cannot support an April 1 pre-open decision by itself. |
| [Differences of Opinion and Stock Prices: Evidence from Spin-Offs and Mergers](https://www.sec.gov/files/dera-wp-differences-opinion-evidence-spinoffs.pdf); Tara Bhandari, SEC DERA working paper; November 2013; 1988–2012; full text | Distinct event stages and joint parent+child post-ex result. |

All retrieved 2026-09-22. Publication-time limitation: the archived SEC copies prove filing provenance, while original issuer release dispatch time and EDGAR first-visible time require separate evidence.
