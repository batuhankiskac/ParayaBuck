# NYSE Group Security Master audit

## Provenance

- Publisher: NYSE / Intercontinental Exchange.
- Current specification: v4.1, dated 2026-06-24, effective 2026-07-06; retrieved 2026-09-22 from the [official PDF](https://www.nyse.com/publicdocs/nyse/data/NYSE_Group_Security_Master_Client_Specification_v4.1.pdf). Downloaded PDF SHA-256: `6e47095da3a3fe5ec3b0b270577ba55467ace66fb574f3d3f8c1465b9f040894`.
- Public product and sample pages: [catalog](https://www.nyse.com/data-products/catalog/nyse-group-security-master), [sample directory](https://ftp.nyse.com/Reference%20Data%20Samples/NYSE%20GROUP%20SECURITY%20MASTER/).
- Access class: public documentation/public samples; entitled production and historical delivery.

## History, delivery and effective semantics

The product is start-of-day: multiple files are delivered in the evening for the next trading session. Historical files are documented from 2015-12-16 onward. Equity full and delta files are daily for every trading session, currently scheduled for midnight U.S. ET, in TXT/XLS/XML over SFTP or S3. Equity deltas contain `A`, `M`, and `D` relative to the prior generated file and list modified non-daily fields; closing prices are omitted from deltas.

The filename date is a generation date. The file has no universal row-level `observed_at`, `valid_from`, `valid_to`, correction timestamp or announcement timestamp. `Listing Date` means the initial NYSE Group listing date, but for a non-NYSE listing may instead be its first NYSE Group trade date. Old symbol/SIP/CUSIP appears only on the effective day of a change. Those semantics require daily-file replay, not a current snapshot join.

## Venues: listing versus trading

The package names NYSE, NYSE American, NYSE Arca, NYSE Texas, NYSE National, NYSE American Options, NYSE Arca Options and NYSE Bonds. The equity master is broader: it covers all U.S. NMS securities approved to trade on NYSE Group platforms. `Primary Market` identifies listing venue, while separate traded flags identify NYSE Group trading venues. Nasdaq, Cboe-family, IEX, LTSE and FINRA primary-market codes are present.

Thus NYSE is a plausible single-feed path for the repository's NASDAQ/NYSE/ARCA/BATS/AMEX labels, but that is a trading-universe claim, not yet a measured 9,010-interval mapping. The v4.1 introduction uses NYSE Texas while legacy field names still say NYSE Chicago; preserve raw values until the vendor confirms the effective transition.

## Identifiers

`Stock Number` is the strongest field: NYSE states that each security receives a unique Pillar identifier at listing, common across NYSE Group listing/trading markets and never reused after delisting. Certain corporate actions can nevertheless create a new Stock Number. Other fields are stock symbol, SIP symbol, CUSIP when assigned, ISIN when available, issuer name and security name. No CIK or stable issuer ID is published.

Stock Number plus the daily sequence can distinguish ticker reuse and symbol changes; predecessor/successor continuity for mergers, relistings, class conversions and identifier changes still depends on event/corporate-action evidence. The documented `Symbol Reuse Flag` is only a future placeholder.

## Taxonomy

`Security Type` distinguishes common, preferred, warrants, rights, corporate/Treasury bonds, ETF, ETN, single-security ETF, ETV, structured product, ADR common/preferred/warrants/rights/bonds, NY/global registered shares, fund, basket, unit, liquidating trust and unknown. `Issuer Type` includes operating company, trust, closed-end fund, REIT, limited partnership, SPAC and BDC in the public samples. NYSE Arca fields add asset-class segmentation, `Active`/`Inverse`/`Leveraged`, and an index-leverage multiplier.

This supports the requested broad taxonomy, including ADR common and REIT common as intersections of `Security Type` and `Issuer Type`. It does not provide a dedicated volatility-linked or crypto-related flag; benchmark description/ETP segmentation and supplemental prospectus evidence are needed for that finer granularity. Non-Arca ETPs may lack Arca-only segmentation. Unknown remains explicit.

## Lifecycle and corrections

Daily deltas capture adds, modifications and deletes; other fields expose IPO/new product, add-or-symbol-change, split and ex-corporate-action states. Detailed corporate actions are a separate NYSE product. The public specification does not state whether historical files can be replaced, whether a revision ledger/checksum history exists, or whether original and later-corrected versions remain retrievable.

Strict vintage result: `VINTAGE_SEMANTICS_UNCLEAR`. Generation-dated archives are positive evidence, but are not proof of immutable files as originally distributed.

## Licensing, price and suitability

SFTP/S3 access requires entitlement. Public terms do not resolve internal backtest rights, raw/derived retention after termination, backups, redistribution, contractor/user limits, or CUSIP/ISIN/FTSE downstream restrictions. Public fixed pricing was not found: `COMMERCIAL_QUOTE_REQUIRED`. Public samples exist; a reference-data trial is not publicly guaranteed.

Suitability: strongest single-source lead, technically promising and sample-parseable, but not accepted until entitlement, immutable-vintage/correction semantics, historical backfill and 9,010-interval coverage are proven.
