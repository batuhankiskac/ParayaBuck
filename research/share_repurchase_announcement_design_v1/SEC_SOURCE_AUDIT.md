# SEC source audit — 2026-09-22

`REPURCHASE_EVENT_SOURCE_BLOCKED` for the requested adequately covered 2016–2026 first-announcement corpus. This is a current evidence/capability verdict, not a claim SEC records are inherently unreconstructible. A reproducible SEC-disclosed subset is technically plausible; completeness, extraction recall, original publication timing and eligible supply have not been established.

## Direct primary sample

The accompanying `PRIMARY_EVENT_SAMPLE.json` contains four purposively selected issuer records. They are source-path counterexamples, not four approved trading events. No frequencies, occupancy or investability can be inferred from them.

| Issuer | Primary path | What it demonstrates |
|---|---|---|
| Microsoft 2016 | [10-Q](https://www.sec.gov/Archives/edgar/data/789019/000119312516742796/d245252d10q.htm), [index](https://www.sec.gov/Archives/edgar/data/789019/000119312516742796/0001193125-16-742796-index.html) | September authorization repeated in October; open-market/10b5-1 language; successor program. |
| Bank of America 2019 | [8-K](https://www.sec.gov/Archives/edgar/data/70858/000007085819000006/bac27198-k.htm) | Item 8.01; incremental $2.5bn authorization; open-market and private alternatives. |
| Apple 2024 | [exhibit 99.1](https://www.sec.gov/Archives/edgar/data/320193/000032019324000067/a8-kex991q2202403302024.htm), [index](https://www.sec.gov/Archives/edgar/data/320193/000032019324000067/0000320193-24-000067-index.htm) | $110bn additional authorization inside earnings; index lists 2.02 and 9.01, not 8.01. Mechanism unresolved from exhibit. |
| Lands’ End 2026 | [8-K](https://www.sec.gov/Archives/edgar/data/799288/000110465926038276/tm2610714d3_8k.htm) | Item 8.01; $100m authorization through March 2029; open-market/private/other means. |

The [Form 8-K instructions](https://www.sec.gov/files/form8-k.pdf) distinguish results disclosures (2.02), Regulation FD (7.01), other events (8.01), and exhibits. Furnished status does not make a document nonpublic. The sample directly disproves an Item-8.01-only collector. Search all relevant primary documents and exhibits, not just an item label. Periodic 10-Q/10-K narratives are reconciliation leads; their execution tables are not first-announcement feeds. Other forms such as 6-K require separate scope and foreign-issuer mapping treatment, not silent inclusion.

## Reproducible acquisition path and missing proof

[SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) provides submission histories and bulk structures. [SEC access documentation](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) describes quarterly/daily indexes, headers, correction handling and fair access. A future corpus build would enumerate historical filing indexes independently of today's ticker universe, retain documents/exhibits and acceptance headers, hash raw bytes, and version the extraction manifest. Respect identified User-Agent and fair-access limits. None of that whole-period acquisition has been executed here.

Required before upgrading: enumerate all covered filings through an exact cutoff; record fetch failures; archive raw evidence; deduplicate program mentions; manually adjudicate stratified positive and negative samples; reconcile authorizations in periodic reports against earlier releases; document inaccessible primary releases and quantify lag; retain delisted and renamed issuers. Search-engine hits are discovery aids, not a coverage denominator. The four records have no raw-byte snapshots and no canonical historical security mapping. Two filing-index requests failed; their timestamps remain null. 2026 is year-to-date, never a full calendar year.

No single mandatory contemporaneous buyback-announcement form or structured field was established. A complete SEC filing collection still would not prove complete first-public-release coverage. Modern routine renewals, broad mixed-method mandates and earnings bundles may leave far fewer clean events than headline buyback prevalence suggests.

## Disclosure and tax regime

The [SEC March 2024 technical amendments](https://www.sec.gov/rules-regulations/2024/03/s7-21-21) confirm the May 2023 repurchase-disclosure rule was vacated effective December 19, 2023, reverting to prior rules/forms. Do not assume its intended daily repurchase and structured disclosures created a durable 2023–2026 historical panel. The [original rule](https://www.sec.gov/files/rules/final/2023/34-97424.pdf) documents the attempted reform, not current operative coverage.

[IRS final regulations TD 10037](https://www.irs.gov/irb/2025-51_IRB) address repurchases after December 31, 2022 and became effective November 24, 2025; earlier procedural regulations were published July 2024. [IRS Publication 510, December 2025](https://www.irs.gov/publications/p510) states a 1% excise tax for covered repurchases. Netting and exceptions mean a headline authorization is not itself the tax base or an immediate tax payment. These changes motivate regime sensitivity; they do not establish stationarity or any return effect.
