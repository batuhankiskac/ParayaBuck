# Free official source audit

| Source | Can prove | Cannot prove | Vintage assessment |
|---|---|---|---|
| [SEC EDGAR submissions/APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and original filings | Permanent CIK issuer identity; filing/accession/acceptance time; filed class title, trading symbol and exchange where tagged; current/former registrant names | Complete exchange listing lifecycle, stable issue identity, daily taxonomy or all foreign/fund coverage | Original filed document: `ORIGINAL_VINTAGE_CONFIRMED` at filing level; mutable submissions metadata is a current view |
| [SEC current ticker maps](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) | Current CIK/name/ticker/exchange and fund Series/Class associations | Guaranteed accuracy/scope, historical intervals, class continuity | Current snapshot; `VINTAGE_SEMANTICS_UNCLEAR` |
| [SEC Series/Class](https://www.sec.gov/data-research/sec-markets-data/investment-company-series-class-information) | Annual releases from 2010; CIK, stable SEC Series ID/Class ID and ticker for registered investment-company classes | Comprehensive closed-end N-2 or non-insurance S-6 population; daily listing status; registrant-field accuracy | Dated annual snapshots; immutability policy unclear |
| Nasdaq public symbol directories | Current name, market/status and ETF flag | Historical type, issue ID, ticker reuse, corrections | Current only |
| NYSE/Cboe public current files/pages | Current listing/trading corroboration and selected product descriptions | Complete 2016-2026 issue master | Current/sample only |
| [FINRA OTC directory/daily list](https://otce.finra.org/otce/symbol-directory) | OTC events and transitions | Exchange-listed NYSE/Nasdaq/BZX master | Historical capture semantics unclear; outside current canonical venue scope |

No honest union of these free sources becomes a complete daily U.S. listed-security master. They remain evidence rows behind an exchange master, never substitutes for missing issue intervals.

## CIK bridge

Maintain separate `issuer_id`, `security_id` and `listing_id`. Build a many-to-many effective-dated bridge from original EDGAR evidence: CIK, accession, acceptance timestamp, class title, TradingSymbol, SecurityExchangeName and, for funds, Series/Class IDs. Match only on time-overlapping symbol + venue + compatible class, strengthened by CUSIP or explicit event/prospectus evidence. Store `knowledge_at`, method and ambiguity reason. A CIK can own several classes; corporate succession can connect one issue chain to more than one CIK. Conflicts remain unmapped.

SEC automated access must use a declared User-Agent, moderation and caching. No user contact value belongs in the repository.
