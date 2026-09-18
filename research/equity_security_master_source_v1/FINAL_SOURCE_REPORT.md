# Equity Security-Master Source Design v1

Audit date: 2026-09-18  
Repository: `/Users/batuhankiskac/Desktop/ParayaBuck`  
Scope: DATA / SOURCE DESIGN ONLY. No backtest, return, P&L, portfolio, V3, scanner, configuration, PAPER/LIVE, or production-universe change was made.

Starting local `main`, `origin/main`, and requested baseline: `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`.  
Research branch: `research/equity-security-master-source-v1`.

## Verdict

`SECURITY_MASTER_CURRENT_ONLY_HISTORICAL_BLOCKED`

Official, free current sources are sufficient to design a materially safer current classifier, provided that unknowns fail closed and the classifier is kept separate from the production scanner until reviewed. They are not sufficient to prove the requested 2016-01-04 through 2026-07-31 point-in-time taxonomy for all 9,010 historical symbols.

The decisive evidence is:

* Nasdaq's public current Symbol Directory files expose current security names, listing exchange, and an ETF flag. They do not provide a persistent security identity or a complete historical effective-dated product taxonomy.
* Nasdaq's Daily List specification contains the strongest requested fields — effective date, add/delete/name-symbol changes, issue type and subtype, leveraged-ETP flag, leverage ratio, and inverse-ETP flag — but the historical Daily List is a subscription/secured-access product.
* NYSE's Security Master specification contains security type, exchange, historical daily files, ETP segmentation, leverage, and symbol-change fields. Historical files are accessed through NYSE's managed transfer system rather than a free public bulk route.
* SEC series/class reports and EDGAR filings supply strong fund identity and filing provenance, but they are annual snapshots or issuer filings, do not constitute a complete exchange listing history, and explicitly exclude a comprehensive population of closed-end and certain unit investment trusts.
* Cboe provides useful current listed-symbol and product pages, including product objectives and some listing dates, but no demonstrated free historical bulk security-master path covering the full period.

This is a source-access and historical-PIT block, not evidence that the operator contract is wrong. No source was used to change the contract and no product was auto-promoted.

## Operator product contract

The contract is frozen for this investigation:

* Ordinary alpha eligibility requires direct equity ownership in one operating/company issuer: common stock, ordinary shares, appropriate company-equity ADR/ADS, or REIT common equity.
* Ordinary alpha excludes ETFs, ETNs, exchange-traded products/funds, leveraged or inverse ETFs, volatility-linked products, commodity-linked products, bond/fixed-income ETFs, crypto-linked ETPs, closed-end funds unless separately proven to be company equity, preferred stock, warrants, rights, units, and structured notes.
* Ambiguous categories remain `unknown`; they are not silently mapped to common stock.
* `SPY` is `reference_only`, never an ordinary-alpha candidate.
* `SH`, `PSQ`, and `DOG` are approved hedge/reference instruments only. They may have history and a dedicated strategy role, but they are never ordinary V3/company-equity alpha merely because they are observed.
* `maximum_per_sector` remains the existing fallback observation-diversity rule. It is not changed into a hard portfolio cap.

The data model must keep these layers separate:

```text
security_type   = factual source classification
alpha_eligible  = policy derived from security_type and the operator contract
role            = ParayaBuck configuration, such as reference_only or approved_hedge_only
```

## Current Alpaca limitations

The current production path requests active `AssetClass.US_EQUITY` assets and then filters symbol, slash, tradability, exchange, allowlist, and denylist. The source object is not persisted as an effective-dated security master, and the current path does not retain an authoritative issue subtype, issuer security identity, ETF/ETN distinction, leverage objective, inverse objective, or historical membership.

Relevant current source paths are `src/parayabuck/universe/preflight.py`, `src/parayabuck/universe/scanner.py`, `src/parayabuck/markets/sectors.py`, and `src/parayabuck/backtest/universe.py`. The current product guard is a finite symbol policy, not a complete security taxonomy. Current active status is therefore unsafe for historical replay.

This report does not modify those paths.

## Nasdaq Trader findings

### Public current files

Nasdaq's [Symbol Directory definitions](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs) state that the files are updated throughout the day and expose these routes:

* [`nasdaqlisted.txt`](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt): Nasdaq-listed symbol, security name, market category, test issue, financial status, round lot, ETF, and NextShares.
* [`otherlisted.txt`](https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt): ACT symbol, security name, exchange, CQS symbol, ETF, round lot, test issue, and Nasdaq symbol. The exchange codes cover NYSE MKT, NYSE, NYSE Arca, BATS, and IEX in the current specification.
* [`nasdaqtraded.txt`](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt): consolidated current Nasdaq-traded view with listing exchange, security name, ETF, CQS symbol, Nasdaq symbol, and NextShares.
* [`TradingSystemAddsDeletes.txt`](https://www.nasdaqtrader.com/dynamic/SymDir/TradingSystemAddsDeletes.txt): current Nasdaq/BX/PSX add/delete events with an effective date and primary listing market.

The current snapshot retrieved at `2026-09-18T18:17:47Z` had:

| File | Security rows | ETF=Y | ETF=N | Snapshot SHA-256 |
|---|---:|---:|---:|---|
| `nasdaqlisted.txt` | 5,623 | 1,275 | 4,348 | `72510b4ee023ac225319dbba88bd76a6c657ee22a1f02c0a22551ce86480cd58` |
| `otherlisted.txt` | 7,635 | 4,433 | 3,202 | `a0a9a0e1c612d54a9080ab6b325c7d185ff7a882e3bcf78b61e0a009e8e9d4d3` |
| `nasdaqtraded.txt` | 13,258 | 5,708 | 7,550 | `8f94769637af194ce6fed7e7c6f2e6f6c8769fc005c00436c8d774a8f33d8aff` |

The file-creation rows reported `0918202614:01` for the first two files and `0918202614:03` for the consolidated file. The current directory is strong for current ETF detection and useful for current security-name review, but the consolidated file has no issue-type/subtype, CIK, Series ID, Class ID, CUSIP, FIGI, leverage ratio, or inverse-objective field. The ETF flag also does not mean “ordinary equity”: an ETF is only one nonordinary category, and an ETN can appear with `ETF=N`.

### Daily List and security-type semantics

The [Nasdaq Daily List product description](https://beta.nasdaqtrader.com/Trader.aspx?id=DailyListPD) says that Daily List corporate-action data includes new listings, delistings, name changes, symbol changes, and historical data dating back to 1999. It also states that the historical product is a monthly subscription, with secured FTP and secured website access.

The [Daily List file specification](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/dlcompletespec.pdf) is the best single source found for the required taxonomy. Its fields include:

* Daily List date and effective date.
* Security additions, deletions, name/symbol changes, market-class changes, and financial-status changes.
* Listing center and first date traded.
* Issue type: American Depositary Shares, bond, common stock, depository receipt, notes, ordinary shares, preferred, rights, units, warrants, and other values.
* Issue subtype: ETF portfolio/index shares, ETNs, commodity-linked securities, closed-end funds, managed fund shares, trust units, REIT, trust-issued receipts, and other specific values.
* Leveraged ETP flag, leveraged ETP ratio, and inverse ETP flag.

This is sufficient to define an implementation target if licensed historical files are acquired. It is not evidence that the free current files alone can reproduce the historical taxonomy. The specification also warns that the data fields in a future-effective event can describe the current day rather than the post-event state; the loader must apply event-specific effective-date logic, not blindly treat every row as an as-of record.

The [2011 Nasdaq enhancement notice](https://www.nasdaqtrader.com/TraderNews.aspx?id=dtn2011-017) says historical Daily List files were not rewritten to add later data elements. Therefore, even licensed access requires a format-version audit before treating leverage/inverse fields as complete for every 2016-era event.

### ETF and non-Nasdaq coverage

`otherlisted.txt` and `nasdaqtraded.txt` demonstrate that the Nasdaq public directory covers current securities listed on Nasdaq and other venues including NYSE, NYSE Arca, BATS, and IEX. This is useful for current cross-exchange discovery. It does not supply a complete historical archive for those venues, nor does it turn the `ETF` boolean into a complete ETN, fund, preferred, warrant, unit, structured-product, or company-equity taxonomy.

The current add/delete file is a current daily event file. It is not a demonstrated archive from which the entire 2016–2026 history can be reconstructed without the Daily List product.

## SEC findings

### Current ticker maps

The SEC [EDGAR data-access page](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) identifies these free JSON maps:

* [`company_tickers.json`](https://www.sec.gov/files/company_tickers.json): current ticker, CIK, and conformed company name associations.
* [`company_tickers_exchange.json`](https://www.sec.gov/files/company_tickers_exchange.json): current ticker, CIK, conformed name, and exchange associations.
* [`company_tickers_mf.json`](https://www.sec.gov/files/company_tickers_mf.json): current fund CIK, Series ID, Class ID, and ticker.

The SEC explicitly says these maps are periodically updated for EDGAR search functionality and does not guarantee their accuracy or scope. They are current crosswalks, not effective-dated listing masters.

### Series/class report

The [SEC Investment Company Series and Class Information](https://www.sec.gov/data-research/sec-markets-data/investment-company-series-class-information) page provides annual downloads from January 2010 through June 2026. The report includes CIK, reporting file number, organization type, Series ID, Class ID, class ticker, names, and addresses. Series and Class IDs are materially better fund identities than ticker strings.

The same SEC page states that the report is not a comprehensive listing of closed-end N-2 registrants or non-insurance S-6 unit investment trusts, that some fields are registrant-maintained, and that the report may contain classes that have ceased operation but have not yet been reclassified inactive. It also includes entities that have received IDs but have not begun selling shares. These limitations prevent treating the report as a complete active historical exchange universe.

### EDGAR filings and forms

The SEC [EDGAR index and access documentation](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) provides free filing indexes from 1994 onward, permanent non-recycled CIKs, filing dates, form types, and accession paths. Relevant forms include N-1A, N-2, N-PORT, N-CEN, 485APOS, 485BPOS, 497, and 497K. The [SEC Data Library](https://www.sec.gov/data-research/sec-markets-data) describes N-PORT and N-CEN structured datasets.

These filings can supplement classification and provide dated prospectus/objective evidence. They do not, by themselves, provide a complete, exchange-effective add/delete/security-type table for all listed instruments. N-PORT is also not a 2016-complete source; the SEC's reporting guidance describes the later compliance/publication schedule.

SEC access constraints are operationally important: the SEC asks automated clients to declare a User-Agent, limits request rate, and asks clients to download only what they need. A research loader must cache raw responses, record accession numbers, and respect the SEC access policy.

## Other official-source findings

### NYSE Group Security Master

The [NYSE reference-data page](https://www.nyse.com/market-data/reference) lists the NYSE Group Security Master, NYSE Group ADR Master, and NYSE Arca EOD ETF Report. The [NYSE Group Security Master specification](https://www.nyse.com/publicdocs/nyse/data/NYSE_Group_Security_Master_Spec_v4.0.1.pdf) is the strongest exchange-side complement found:

* It covers NYSE, NYSE American, NYSE Arca, NYSE Chicago, NYSE National, and instruments traded on NYSE Group platforms, including Nasdaq and BATS coverage in the product description.
* Historical files are specified from 2015-12-16 onward, which covers the requested 2016-01-04 start date.
* Daily files and delta files are specified with generation dates and effective daily delivery.
* Security Type includes common, preferred, warrant, right, corporate bond, ETF, ETN, ETV, structured product, ADR common/preferred, fund, basket, unit, and unknown.
* The specification includes primary market, fund manager, underlying index leverage, ETP segmentation (commodities/futures, currency, equity, fixed income, hybrid, options), and active/inverse/leveraged benchmark segmentation.
* It includes an old-stock-symbol field and a symbol-reuse flag placeholder, which are directly relevant to ticker changes and reuse.

The same specification requires access through NYSE's managed file-transfer system and an MFT form. This is an excellent licensed-source candidate, not a demonstrated free source.

The [public NYSE Arca symbol-mapping directory](https://ftp.nyse.com/ARCASymbolMapping/) exposes current daily mapping files. It is useful for current exchange/symbol checks but does not substitute for the historical Security Master package.

### Cboe / BATS

The [Cboe listed-symbol page](https://www.cboe.com/us/equities/market_statistics/listed_symbols/) exposes a current BZX/BYX/EDGX/EDGA symbol view. Cboe's [symbol-file URL specification](https://cdn.cboe.com/resources/release_notes/2024/Cboe-Updates-Options-Symbol-Reference-and-Equities-Symbols-Traded-File-URLs.pdf) documents current CSV/XML symbols-traded routes. These are useful current discovery routes, but the symbols-traded file is not a historical product taxonomy.

Cboe's [listed-product pages](https://www.cboe.com/us/equities/listings/listed_products/) provide issuer/product descriptions and, for individual products, listing dates. The [suspension/delisting page](https://www.cboe.com/us/equities/listings/listed_products/suspensions_delistings/) is a current pending-event list, not a demonstrated 2016–2026 archive. A complete historical Cboe/BATS security master would still need an exchange data product or a separately verified archive.

### Issuer-maintained official product data

Issuer pages and dated prospectuses are useful for objective and leverage confirmation but are not a complete exchange listing master. Examples checked include [ProShares](https://www.proshares.com/our-etfs/leveraged-and-inverse/psq), [Direxion TSLL/TSLS](https://www.direxion.com/product/daily-tsla-bull-and-bear-leveraged-single-stock-etfs), [Direxion TNA/TZA](https://www.direxion.com/product/daily-small-cap-bull-bear-3x-etfs), and [Cboe's VXX page](https://www.cboe.com/us/equities/listings/listed_products/symbols/VXX/).

## Historical availability

| Source | Free public bulk path | Effective-dated content | Historical reach | Assessment |
|---|---|---|---|---|
| Nasdaq current directories | Yes | Current snapshot/file timestamp | Current only | Strong current ETF/name/exchange discovery; no PIT identity or full subtype |
| Nasdaq Daily List | No; secured subscription route | Yes: adds, deletes, effective dates, symbol/name changes, issue type/subtype, leverage/inverse fields | Product description says back to 1999 | Best Nasdaq historical route, but not free; historical field-version completeness must be audited |
| NYSE Group Security Master | No demonstrated free route; MFT | Yes: daily and delta files, type, exchange, symbol-change fields | Spec says 2015-12-16 onward | Best NYSE/Arca licensed route; nearly exact period coverage |
| Cboe/BATS current files/pages | Yes for current pages/files | Current listed status and selected product/listing dates | No complete free bulk archive demonstrated | Supplement only |
| SEC Series/Class reports | Yes | Annual snapshot/update date, not daily listing effective date | 2010–2026 annual downloads | Strong fund identity; incomplete closed-end/S-6 and not exchange membership history |
| SEC EDGAR filings | Yes, with fair-access controls | Filing dates and document/effectiveness dates | 1994 onward | Supplement; too heterogeneous to be the sole 9,010-symbol listing master |
| Issuer pages/prospectuses | Generally public | Document dates; product objective | Product-specific | Good objective/leverage evidence; not complete or stable enough as sole master |
| OpenFIGI API | Free API with rate limits | No proven PIT event history in this investigation | Current mapping service | Optional secondary crosswalk, never primary classification authority |

The free official evidence therefore supports a current classifier and a research prototype, but not a complete historical PIT classifier for the requested window.

## Stable-identifier design

Ticker alone is not a security identity. The normalized model should use an immutable internal `security_id` and preserve source identities and listing aliases as dated facts.

Recommended identity precedence:

1. Use the official exchange listing/security identity plus venue and effective dates when available. Retain source-specific identifiers, symbol, CQS/Nasdaq symbol, MIC/listing market, and old/new-symbol event links.
2. For registered investment-company shares, use `CIK + Series ID + Class ID` as the fund share-class identity. Treat class ticker as a dated alias, not the key.
3. Use SEC CIK as issuer/filer identity only. The SEC says CIKs are permanent and not recycled, but a CIK can have multiple historical names and does not identify a particular listed class.
4. Use licensed CUSIP only if the required license permits the intended storage and redistribution. Nasdaq's Daily List specification expressly distinguishes files with and without CUSIP data.
5. Use FIGI/Share Class FIGI/Composite FIGI as a secondary crosswalk after provenance review. [OpenFIGI](https://www.openfigi.com/api/documentation) is public and free with rate limits, but it has no demonstrated effective-dated 2016–2026 listing-event archive in this investigation and does not provide bulk flat files.
6. Keep Alpaca's asset UUID/provider identity in an adapter table only. It must not become the canonical historical identity without a PIT crosswalk.

Ticker-change rules:

* Store `effective_from` inclusive and `effective_to` exclusive.
* Apply exchange add/delete/name-symbol events using the event's effective date, not download date.
* A pure symbol change keeps the same `security_id` and creates a new symbol-alias interval.
* A merger, spin-off, fund reorganization, redemption, or new security creates a new identity with explicit `predecessor_id`/`successor_id` links where the source supports them.
* A relist or ticker reuse is ambiguous until a stable source identity or corporate-action link proves continuity. The row remains `unknown`/ambiguous instead of being joined by ticker folklore.

## Leveraged/inverse classification

The factual layer should use source fields, not ticker lists or performance behavior:

```text
product_family
is_etf
is_etn
is_fund
is_structured_product
is_leveraged
is_inverse
leverage_ratio
inverse_benchmark
objective_text
source_record_date
effective_from
effective_to
```

The strongest fields found are Nasdaq Daily List `Leveraged ETP Flag`, `Leveraged ETP Ratio`, and `Inverse ETP Flag`, plus NYSE's ETP benchmark leverage and active/inverse/leveraged segmentation. Issuer pages and SEC prospectuses should be evidence supplements and should be captured with document date, accession/document URL, and hash.

Current fixture evidence from the public Nasdaq consolidated file and official product pages:

| Symbol | Current Nasdaq ETF flag | Current official product evidence | Contract result |
|---|---|---|---|
| `AAPL` | N | Common Stock | Ordinary-alpha candidate if other gates pass |
| `ARKK` | Y | ARK Innovation ETF | Nonordinary ETF |
| `BITO` | Y | ProShares Bitcoin ETF | Nonordinary crypto-linked ETF |
| `BND` | Y | Vanguard Total Bond Market ETF | Nonordinary fixed-income ETF |
| `BOIL` | Y | ProShares 2x natural-gas-futures ETF | Nonordinary leveraged commodity-linked ETF |
| `DOG` | Y | ProShares -1x Dow ETF | `approved_hedge_only`; never ordinary alpha |
| `GDX` | Y | VanEck gold-miners ETF | Nonordinary ETF unless a separate policy says otherwise |
| `PSQ` | Y | ProShares -1x Nasdaq-100 ETF | `approved_hedge_only`; never ordinary alpha |
| `SH` | Y | ProShares -1x S&P 500 ETF | `approved_hedge_only`; never ordinary alpha |
| `SPY` | Y | SPDR S&P 500 ETF Trust | `reference_only`; never ordinary alpha |
| `SQQQ` | Y | ProShares -3x Nasdaq-100 ETF | Nonordinary leveraged/inverse ETF |
| `TQQQ` | Y | ProShares 3x Nasdaq-100 ETF | Nonordinary leveraged ETF |
| `TSLL` | Y | Direxion +2x daily TSLA ETF | Nonordinary leveraged single-stock ETF |
| `TZA` | Y | Direxion -3x daily Russell 2000 ETF | Nonordinary leveraged/inverse ETF |
| `UVXY` | Y | ProShares leveraged VIX-futures ETF | Nonordinary volatility-linked ETF |
| `VXX` | N | Cboe identifies it as an ETN linked to VIX futures | Nonordinary ETN |

This table is evidence of product distinctions, not a production blacklist. The next implementation task must obtain dated records before deriving any historical membership.

## Current-production coverage

The stale local candidate cache contains 13,098 symbols and was generated at `2026-08-31T12:43:02.127310+00:00`. Against the public Nasdaq consolidated snapshot retrieved on 2026-09-18:

| Measure | Count |
|---|---:|
| Cached candidate symbols | 13,098 |
| Exact current Nasdaq-symbol matches | 12,610 |
| Current exact-symbol misses | 488 |
| Matched rows with `ETF=Y` | 5,641 |
| Matched rows with `ETF=N` | 6,969 |
| Exact current Nasdaq match rate | 96.27% |

The cache and the source snapshot are not same-day data, so the 488 misses are not a definitive active-status or identity failure count. The result does show that a current exchange adapter can cover most of the current cached universe, while the 5,641 ETF rows demonstrate why Alpaca `US_EQUITY` alone is not an ordinary-company-equity taxonomy.

Additional current crosswalk counts against the same cached symbols were 4,480 exact matches in SEC `company_tickers_mf.json` and 7,092 in SEC `company_tickers_exchange.json`. These are current crosswalk counts only.

## Historical 2016–2026 coverage

The local canonical file is [`data/historical/equity_pit_symmetric_2016_2026_v5.csv`](../../data/historical/equity_pit_symmetric_2016_2026_v5.csv), with 11,686,832 data rows, 9,010 unique symbols, and coverage from 2016-01-04 through 2026-07-31. The recorded manifest SHA-256 is `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`. It contains no security type, CIK, Series ID, Class ID, FIGI, CUSIP, or exchange-event identity.

No historical PIT source was successfully joined to each symbol/date interval. Consequently, full-period historical classification coverage is **not proven**. The following are current/annual source match diagnostics, not historical coverage claims.

## Local 9,010-symbol match counts

| Source snapshot | Exact canonical-symbol matches | Unmatched | Source ambiguity | Meaning |
|---|---:|---:|---:|---|
| Nasdaq `nasdaqtraded.txt`, current 2026-09-18 | 6,555 | 2,455 | 0 duplicate consolidated rows | Current symbol presence only; not historical membership |
| Nasdaq current matches with `ETF=Y` | 2,926 | — | — | Current ETF flag only |
| Nasdaq current matches with `ETF=N` | 3,629 | — | — | Contains common, ADR/ADS, preferred, notes, warrants, units, and other non-ETF products |
| SEC Series/Class 2026 snapshot | 2,246 | 6,764 | 20 ticker keys; 2,266 matched rows | Current/annual registered-investment-company identity evidence |
| SEC Series/Class 2016 snapshot | 702 | 8,308 | 1 ticker key; 703 matched rows | 2016 annual snapshot evidence, not daily listing membership |
| SEC `company_tickers_mf.json`, current | 2,290 | 6,720 | 0 duplicate ticker keys in the current file | Current fund crosswalk |
| SEC `company_tickers_exchange.json`, current | 3,696 | 5,314 | 0 duplicate ticker keys in the current file | Current CIK/exchange crosswalk |
| Nasdaq current union SEC Series/Class 2026 | 6,594 | 2,416 | Source-specific | Current/annual match union only |

The current Nasdaq exact-match rate is 72.7525% of the historical distinct-symbol set. It must not be reported as 2016–2026 PIT coverage: a current absence can mean delisting, ticker change, relist, symbol reuse, provider normalization, or a product that existed historically but not on the retrieval date.

Ticker reuse cannot be counted honestly from this canonical file: it has only `timestamp` and `symbol`, not issuer/security identity. The current public Nasdaq snapshot also has no historical identity interval. The correct result is `NOT_MEASURABLE_WITH_SYMBOL_ONLY_DATA`, not zero reuse.

## Unknown / ambiguous population

The source-design unknown population is explicit:

* 2,455 of the 9,010 canonical symbols are absent from the current Nasdaq consolidated snapshot. This is a current-vs-historical mismatch, not automatically an unknown product type.
* 20 canonical symbols have more than one 2026 SEC Series/Class row for the same class-ticker key; 1 has more than one 2016 row. These are source-level ticker-key ambiguities.
* All 3,629 current Nasdaq `ETF=N` canonical matches remain heterogeneous until issue type/subtype or equivalent evidence is available.
* Historical ticker reuse, merger/spin-off continuity, and delist/relist continuity are unmeasurable without dated identity events.
* Unknowns must be excluded from ordinary alpha, retained for audit/research visibility, and reported separately. They must not be converted to `common_stock` to maximize coverage.

Consequences:

* Excluding unknowns yields a conservative but incomplete historical research universe and blocks a claim of full V3 comparability.
* Retaining unknowns in a non-trading audit set preserves evidence but still blocks ordinary-alpha eligibility.
* Promoting unknowns into ordinary alpha would violate the operator contract and invalidate the intended historical comparison.

## PIT safety table

| Field | Preferred source | Effective-dated? | PIT-safe under free current route? | Limitation / required control |
|---|---|---|---|---|
| Product type | Nasdaq Daily List issue type/subtype; NYSE Security Master Security Type | Yes in licensed historical files | No | Current Nasdaq directory has ETF boolean but not full type/subtype |
| ETF flag | Nasdaq current directories; Daily List; NYSE type | Current yes; historical if archived feed acquired | No for 2016–2026 | ETF flag is not an all-ETP taxonomy |
| Leverage/inverse objective | Nasdaq Daily List flags/ratio; NYSE ETP segmentation; dated issuer/SEC documents | Yes in exchange event/master records | No | Current issuer pages can change; old Daily List formats need field-version audit |
| Issuer / CIK | SEC CIK and filings | CIK is stable; names change | Partly | CIK identifies filer/issuer, not listed class or venue |
| Series/Class ID | SEC Series/Class report and filings | IDs are strong fund identities; reports are snapshots | Partly | Report is not complete for closed-end/S-6 and not listing membership |
| Sector | Dated issuer/SEC/exchange classification, if required | Provider-dependent | No | Sector is not a product-type proof and may be missing or best-effort |
| Exchange / listing market | Exchange security master and add/delete events | Yes in licensed historical sources | No | Current `otherlisted`/`nasdaqtraded` is current-only |
| Active/listed status | Effective add/delete/suspension/delisting events | Yes in event history | No | Current active status is lookahead for historical dates |
| Ticker | Dated symbol-change/add/delete events | Yes if archives exist | No | Ticker is an alias; cannot be the identity key |
| Security name | Dated exchange records and filing/issuer documents | Source-date dependent | No | Name strings are evidence, not a complete structured taxonomy |

## Proposed schema

The next implementation task should normalize factual source records without embedding ParayaBuck policy in the raw table:

```text
security_id
listing_id
symbol
effective_from
effective_to
issuer_id
issuer_cik
series_id
class_id
exchange
mic
security_name
security_type              # factual
product_family             # factual
is_company_equity
is_etf
is_etn
is_fund
is_leveraged
is_inverse
is_structured_product
leverage_ratio
inverse_benchmark
source
source_record_date
source_event
confidence
predecessor_id
successor_id
alpha_eligible             # derived policy, not raw source
role                       # configuration, not raw source
```

The source table should allow multiple evidence rows per field, preserve raw values, and select an as-of record only when `source_record_date <= decision_date` and the event's effective interval contains the decision date. A classification conflict should produce `ambiguous`, not an arbitrary winner.

## Source licensing / usage constraints

* Nasdaq current directory routes are public and useful for an internal current snapshot. The Daily List historical product requires subscription/secured access, and its CUSIP-bearing form has separate CUSIP licensing implications.
* NYSE historical Security Master access is specified through NYSE managed file transfer and a data-product agreement/process. Do not assume the public specification grants redistribution rights.
* Cboe public pages and current symbol files are useful for current discovery. Confirm Cboe terms before storing or redistributing bulk data.
* SEC EDGAR data is free to access, but the SEC requires declared User-Agent headers, rate moderation, and efficient downloads. Cache raw responses and accession metadata.
* OpenFIGI is free with rate limits and is suitable as a secondary crosswalk. Its API is not a demonstrated historical PIT exchange archive, and its documentation notes rate limits and no bulk flat-file route.
* Issuer pages and prospectuses are public evidence sources, but the stored record must include the document URL, publication/effective date, retrieval date, and content hash. Current web pages must not be treated as historical facts.

## Implementation feasibility

### Current classifier

Feasible as a research-only adapter:

1. Snapshot Nasdaq current files with file-creation timestamp and SHA-256.
2. Join SEC current fund/issuer maps and, where needed, dated EDGAR/issuer evidence.
3. Apply exchange-name/type rules only when the factual source is explicit.
4. Keep unresolved rows as `unknown` and fail closed for ordinary-alpha eligibility.
5. Store role mapping separately so reference and hedge instruments cannot enter ordinary V3 alpha.

This has not been integrated into production in this task.

### Historical PIT classifier

Not feasible for the requested completeness from the demonstrated free sources. It becomes feasible only after acquiring and validating the historical exchange records, at minimum:

* Nasdaq Daily List history for Nasdaq-listed events and type fields.
* NYSE Group Security Master history for NYSE/NYSE American/NYSE Arca and its covered NMS venues.
* Cboe/BZX historical listing/security events or a licensed equivalent.
* SEC Series/Class and EDGAR evidence as fund identity/objective supplements.

The implementation gate should require a fresh counts-only 9,010-symbol mapping, date-interval coverage report, identity ambiguity report, and fixture verification before any production integration or V3 revalidation.

## Recommended next action

Do not change production from this report. Resolve the source-access decision first:

* **A.** Change current production now and accept that historical V3 evidence is no longer directly comparable.
* **B.** Keep the current broad universe until a historical source is acquired.
* **C.** Acquire a paid/licensed security master and historical exchange event feeds, then complete the PIT mapping.

The data-governance recommendation is **B while pursuing C**. This is a source/provenance recommendation, not a performance recommendation. Do not use alpha results to choose among A/B/C.

The next task, after a source decision and a successful historical coverage gate, may implement the security master, enforce ordinary-alpha and role isolation, structurally replay the universe, mark the V3 baseline stale, and rerun the frozen V3 validation once. None of those actions is authorized or performed here.

## Files created

* `research/equity_security_master_source_v1/FINAL_SOURCE_REPORT.md`

No production files, configuration, universe snapshots, or strategy code were changed. The downloaded source snapshots used for counts were stored outside the repository under `/private/tmp` and are not committed.

## Branch SHA

* Base branch SHA: `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`
* Report artifact commit SHA: `e08029e7c8136741a7d7825c8cd3abf0daf0f6d8`
* Final research-branch tip SHA is reported in the task handoff after this provenance update.

No new alpha candidate is proposed.
