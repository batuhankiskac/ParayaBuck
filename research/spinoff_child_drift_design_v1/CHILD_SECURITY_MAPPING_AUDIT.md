# Child security mapping audit

Retrieved 2026-09-22. Mapping design is demonstrated for examples, not verified across the canonical research period.

## Point-in-time chain

`distribution event → child legal issuer → child CIK → registered share class → exchange/regular-way symbol → canonical historical price entity`.

Each arrow needs a contemporaneous source and effective date. Parent disclosure names the child and distribution terms; the **child's** Form 10 filing header supplies its own CIK; Form 10 cover, information statement and final issuer/exchange notice specify the class, exchange and regular-way ticker; an event-dated canonical market-data mapping must then bind that ticker/class to the historical bars. Store the interval `[first_regular_way_session, last_verified_session]`, class attributes, source accessions, accepted times, exchange, and unresolved status. Parent ticker, child CIK, and current child ticker are never interchangeable keys. Do not infer the child CIK from an accession prefix because [SEC says](https://www.sec.gov/about/webmaster-frequently-asked-questions) it can identify a filing agent.

### Two directly checked chains

| Event → child | CIK/class → regular-way listing | PIT caveat |
|---|---|---|
| 3M pro-rata partial distribution → Solventum Corporation | Child CIK **1964738** on [initial Form 10 index](https://www.sec.gov/Archives/edgar/data/1964738/000162828024005591/0001628280-24-005591-index.htm); ordinary common stock, NYSE `SOLV` from April 1 according to [3M archived release](https://www.sec.gov/Archives/edgar/data/66740/000006674024000023/pressrelease3-8x24.htm) and [Solventum 8-K](https://www.sec.gov/Archives/edgar/data/1964738/000162828024010988/solventum-effectiveness8xk.htm). | `SOLV WI` is a different when-issued regime. The child-only CIK/ticker proof does not prove the local canonical bar entity or complete regular-way-open availability. |
| Illumina pro-rata partial distribution → GRAIL LLC converting to GRAIL Inc. | Child CIK **1699031** in [GRAIL's 2024 SEC filing path](https://www.sec.gov/Archives/edgar/data/1699031/000119312524132022/d556103dex991.htm); common stock, Nasdaq `GRAL` regular-way June 25 in [GRAIL 8-K/A](https://www.sec.gov/Archives/edgar/data/1699031/000119312524155278/d833704d8ka.htm). | The amendment identifies the **when-issued symbol `GRALV`**, showing suffix assumptions fail. Current name/metadata must not be used as an old-vintage assertion. |

The GRAIL 8-K/A explicitly corrects/clarifies earlier trading dates. It is material proof that a versioned event ledger is required. [SEC's submissions API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) describes exchanges/tickers and names as **current** entity metadata. Current `company_tickers.json` or a current broker universe cannot reconstruct an unverified historical class or ticker interval.

## Share class and lifecycle rules

- Require the exact registered security class (`Common Stock` and relevant par value/class designation) and listing venue. If child issues Class A and B, each is a distinct instrument; CIK alone does not select either. Separate common shares from voting/non-voting shares, depositary receipts, REIT common, preferred, rights, warrants and units. An ADR/ADS or foreign ordinary may be issuer-linked but remains outside the strict U.S. child-common cohort unless separately authorized.
- Treat `WI`, ex-distribution parent tickers and regular-way child ticker as distinct trading regimes. Parent `MMM WI` represents the parent ex-distribution market; `SOLV WI` the conditional child market; `SOLV` the normal child shares. [3M release](https://www.sec.gov/Archives/edgar/data/66740/000006674024000023/pressrelease3-8x24.htm).
- Maintain effective-dated aliases and reorganizations. A former child legal name may become a corporation at separation (GRAIL LLC → GRAIL Inc.); later ticker reuse, mergers, delisting or name changes cannot rewrite earlier mapping. Use no future survival test for initial inclusion.
- Require an event-dated join to ParayaBuck canonical OHLCV, listing calendar, and any corporate-action/entity-lifetime table. If multiple price entities fit the same ticker/date, if the canonical first bar is WI or a stale reused ticker, or if class is unclear: `UNKNOWN/INELIGIBLE`. A zero/single example mapping result is insufficient to claim period-wide capability.

## Gate result

**Example mapping feasible; historical hard gate remains unproven.** A deterministic, public issuer-linked chain is plausible for well-disclosed cases. No counts-only full-period audit of mapping success, unknown share classes, ticker reuse, or canonical entity joins was performed in this source audit. Do not mark reliable historical mapping as passed merely because two 2024 cases can be hand-mapped.

## Source record

| Title; issuer/author; date; sample period; access | Exact claim |
|---|---|
| [Solventum initial Form 10 index](https://www.sec.gov/Archives/edgar/data/1964738/000162828024005591/0001628280-24-005591-index.htm); SEC/Solventum; 2024-02-20; 2024 event; full index | Child CIK 1964738 and Exchange Act filing accession. |
| [3M Solventum trading release](https://www.sec.gov/Archives/edgar/data/66740/000006674024000023/pressrelease3-8x24.htm); 3M; 2024-03-08; 2024 event; full text | SOLV WI versus SOLV; parent MMM WI versus MMM. |
| [GRAIL information statement](https://www.sec.gov/Archives/edgar/data/1699031/000119312524132022/d556103dex991.htm); GRAIL; 2024-05-06; 2024 event; full text | Legal child and intended common class/Nasdaq GRAL. |
| [GRAIL amended 8-K](https://www.sec.gov/Archives/edgar/data/1699031/000119312524155278/d833704d8ka.htm); GRAIL; 2024-06-05; 2024 event; full text | Corrected WI `GRALV` and RW `GRAL` schedule. |
| [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces); SEC; page reviewed 2025-04-08; current API; full text | Submissions metadata include current company/exchange/ticker, not a historical class interval master. |
| [SEC EDGAR FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions); SEC; maintained page viewed 2026-09-22; EDGAR; full text | Accession prefix caveat. |

All retrieved 2026-09-22; source access full text/index. No paid master or post-event price/return data used.
