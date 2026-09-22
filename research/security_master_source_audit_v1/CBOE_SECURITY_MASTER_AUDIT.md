# Cboe / BZX security-reference audit

## Repository need and venue boundary

The canonical lifetimes contain 937 `BATS` rows, 843 open-ended, beginning 2016-01-04. The label does not prove primary-listing semantics, but makes a Cboe reconciliation material.

Cboe's qualifying product covers BZX-listed issues. BYX, EDGA and EDGX are trading books for U.S. NMS securities, not additional primary-listing masters. No canonical lifetime is labelled BYX/EDGA/EDGX/IEX/LTSE.

## Product

[BZX Listings, Distributions and Corporate Actions](https://datashop.cboe.com/listings-distributions-and-corporate-actions) provides history from 2012-05-01. The [v1.0.26 specification](https://www.cboe.com/document/tech-spec/document/technical-specifications/cboe-bzx-exchange-u.s.-listing-corporate-actions-specification), dated 2026-03-31, documents three pipe-delimited daily reports around 7 p.m. ET:

- Daily Listed Securities: full BZX-listed snapshot.
- Daily Distributions: announced-through-completed distribution events.
- Daily Corporate Actions: other announced-through-effective events.

Events include unique event IDs, declared/updated/effective dates and Added/Updated/Cancelled/Unchanged states. There is no documented full/delta pair and no permanent issue ID.

## Taxonomy and identity

The full report distinguishes many ETP/fund/structured types plus preferreds, warrants, rights, units, secondary common classes and closed-end funds. It does not explicitly distinguish ADR/ADS, REIT, leveraged/inverse, volatility/crypto linkage or every bond ETP. `Primary Equity` must remain `UNKNOWN` without supplemental class evidence.

Symbols, first-traded date, lifecycle, venue and optional CUSIP can form a conservative issue chain. CUSIP can change and requires separate entitlement. Corporate-action ID is event-level. There is no CIK, ISIN, stable issuer ID, merger-successor key or relisting continuity key.

## Vintage, licensing and price

Daily dated archives and update/cancel statuses are promising, but documentation does not guarantee immutable as-first-distributed bytes, old versions after replacement, or complete correction-arrival reconstruction. Classification: `VINTAGE_SEMANTICS_UNCLEAR`.

The product requires an order form and signed data agreement. Historical archive and ongoing subscriptions are separately selectable; CUSIP files require CGS entitlement. The [DataShop license](https://datashop.cboe.com/documents/Cboe_LiveVol_DataShop_License_Agreement.pdf) and [historical policies](https://datashop.cboe.com/documents/DataShop_Policies_for_Historical_Data_Services.pdf) indicate internal use and perpetual retention for purchased non-index historical file data, while Snowflake copies must generally be destroyed when that service ends. Exact research/backtest, derived-publication, multi-user and CUSIP rights remain unresolved. `COMMERCIAL_QUOTE_REQUIRED`.

Disposition: licensed BZX-specific fallback/validation component if the NYSE cross-market lead does not prove coverage.
