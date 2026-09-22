# Earnings event source audit

## Finding

`SEC_ITEM_2_02_EVENT_IDENTIFICATION_FEASIBLE_BUT_RDQ_REPLACEMENT_NOT_YET_VALIDATED`

EDGAR can supply a causal, issuer-authenticated record of many U.S. quarterly earnings releases from 2016-2026. It cannot, by itself, reproduce the Compustat `RDQ` event population or the original public-release clock. It is suitable as a high-integrity event-identification and document-provenance layer; using its acceptance time as the event itself is a source adaptation that needs a coverage and timing validation against RDQ or another timestamped release archive before economics.

## What SEC establishes

The official Form 8-K says Item 2.02 is triggered when a registrant publicly announces material information about results for a completed quarter or year. The report identifies the announcement date and includes the announcement as an exhibit. EDGAR exposes CIK, accession, form, items, filing date, exhibit inventory, and a raw submission-header `ACCEPTANCE-DATETIME`. The SEC states that filings are often available on sec.gov one to three minutes after the EDGAR timestamp, but it does not retain a timestamp for first website availability.

Sources: [current Form 8-K, Item 2.02](https://www.sec.gov/files/form8-k.pdf), [EDGAR timestamp and availability FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions), and [EDGAR access/data structure](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

The raw complete-submission SGML header is the timestamp authority for this design. Store the 14-digit value verbatim and its SEC-described Eastern-time convention. Do not infer a release time from the filing date, exhibit date, `periodOfReport`, search-engine publication time, or a current earnings calendar.

## Why Item 2.02 is not the RDQ population

- Form 8-K generally permits filing within four business days after the event. An acceptance timestamp can therefore follow the price-moving issuer release.
- Item 2.02 does not apply when the disclosure is made in a filed 10-Q or 10-K.
- Complementary oral/webcast disclosure can fall within the rule's stated exception when a qualifying written release was already furnished and other conditions hold.
- Foreign private issuers report through Form 6-K rather than the domestic Item 2.02 taxonomy.
- An Item 2.02 filing can mix earnings with guidance, capital allocation, management changes, or other items. `periodOfReport` can belong to another item in the same filing.
- Additional or updated material information triggers another Item 2.02; 8-K/A and later releases can therefore describe the same fiscal-quarter episode.
- The SEC's ticker/CIK file is periodically updated and explicitly not guaranteed for accuracy or scope. Event mapping must use CIK plus dated, fail-closed security history.

CRSP's public Compustat descriptor calls `RDQ` the “Report Date of Quarterly Earnings”; Brandt uses that Compustat date, not an SEC accession time. Source: [CRSP Compustat quarterly descriptor](https://www.crsp.org/wp-content/uploads/ccm_files/PeriodDescriptorQuarterly.html).

## Required event record

Each acquired record should persist at least:

```text
cik, accession, form, items, acceptance_datetime_raw,
filing_date, period_of_report_raw, stated_release_date,
fiscal_period_end, exhibit_url, exhibit_sha256,
is_amendment, supersedes_accession, source_retrieved_at
```

The event identity is not the accession. Deduplicate on the issuer, completed fiscal period, and original stated release, then retain all accessions in an amendment chain. Keep the first public episode as the candidate event; retain later updates as provenance and contamination flags. Never overwrite the first record with revised values.

## Purposive primary-source specimens

The companion [PRIMARY_EARNINGS_EVENT_SAMPLE.json](./PRIMARY_EARNINGS_EVENT_SAMPLE.json) records eight SEC specimens across 2016, 2018, 2020, 2022, 2024, and 2026. Every timestamp is from the SEC filing index or raw complete-submission header; every row has Item 2.02 and an earnings exhibit.

The specimen set deliberately includes:

- Banner: release dated 2016-07-26, accepted 2016-07-27 at 11:21:27, demonstrating that acceptance can lag the stated public event date;
- Edwards Lifesciences: an 8-K/A accepted 2018-02-07 at 17:06:17 that revises the 2018-02-01 release, demonstrating update-chain/duplicate risk;
- JPMorgan and American Airlines: pre-open acceptance examples;
- Lam Research, Microsoft, Post, and Booking: after-close acceptance examples; and
- Post: a mixed-item filing whose `periodOfReport` is 2026-05-05 while its earnings exhibit is explicitly dated 2026-05-07, demonstrating why that field is not an earnings-event timestamp.

This is a purposive source-behavior sample, not a coverage estimate. Its eight rows cannot support population-level annual event, missingness, pre-market, weekend, revision, or issuer counts.

## Acquisition verdict

An SEC-only acquisition can be accepted for a bounded future source validation if it:

1. searches 8-K/8-K-A Item 2.02, qualifying 10-Q/10-K disclosures, and a separately declared 6-K policy;
2. extracts the issuer-stated release date and fiscal period from the exhibit;
3. preserves raw acceptance time and all revisions;
4. flags any first Item 2.02 accepted after its stated release date as `release_precedes_sec_clock`;
5. excludes that flagged record from a paper-faithful EAR until an independently archived original release timestamp is acquired; and
6. measures coverage against a licensed RDQ or independent historical earnings-event reference.

Without that comparison, SEC event sourcing is causally safe only for what was actually observed through EDGAR, and incomplete as a replacement for Brandt's event universe.
