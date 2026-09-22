# Nasdaq security-master audit

## Product path

Nasdaq has two relevant licensed products, not one public historical master.

1. [Daily List](https://nasdaqtrader.com/Trader.aspx?id=DailyListPD) is an event/delta feed with historical corporate-action information back to 1999. The [official specification](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/dlcompletespec.pdf) provides Daily List Date/distribution time, Effective Date, adds/deletes/suspensions, name/symbol/status/market changes, first trade, issue type/subtype, old/new CUSIP, leveraged flag/ratio and inverse flag.
2. [Fundamental Data](https://nasdaqtrader.com/Trader.aspx?id=FD) is a T+1/start-of-day full Nasdaq-listed snapshot with issue name, symbol, description, type/class/subtype, tier, first-trade date, expiration/separation, financial status and optional CUSIP. Public delivery documentation promises only the current business month.

Daily List initial files are normally posted around 1-2 p.m. ET and may update about every 15 minutes from 7 a.m. to 10 p.m. Taxonomy following `Downgrade Reason` is as of Daily List Date, not necessarily the future event Effective Date. A correct model therefore retains both `observed_at` and `effective_on`.

## Coverage and taxonomy

The qualifying scope is Nasdaq Global Select, Global Market and Capital Market listings. Codes for other primary markets and the public `otherlisted.txt` do not make Daily List a complete historical master for other venues.

Issue type distinguishes ADS, depositary receipts, common, ordinary, preferred, rights, units, warrants, shares of beneficial interest, partnerships, notes and bonds. Subtypes distinguish ETF families, managed funds, ETNs, commodity/currency trusts, linked products, REITs and closed-end funds. Leveraged and inverse fields predate the canonical 2016 start. The taxonomy can support the target categories if historical population and version completeness are proven.

## Identity and lifecycle

No permanent Nasdaq issue ID, issuer ID, CIK or ISIN is documented. Symbol, new symbol, optional old/new CUSIP, type/class/subtype, first-trade date, tier and event dates can build chains, but cannot guarantee ticker-reuse or missing-CUSIP resolution. ParayaBuck must issue its own immutable ID and fail closed on ambiguous continuity. CUSIP-bearing service requires separate CUSIP licensing.

Daily List covers listings, suspensions, deletions, symbol/name/tier/status changes, mergers/ADR terminations/redemptions/liquidations, distributions, splits, spin-offs, rights and warrants. It complements rather than replaces the repository's dividend/split accounting.

## Public versus licensed

Public [`nasdaqlisted.txt`](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) and [`otherlisted.txt`](https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt) are current snapshots. ETF flags and current names/exchanges do not establish historical identity. No legitimate historical Daily List or Fundamental Data sample was found publicly; no historical sample was tested.

## Vintage, entitlement and price

Intraday updates and distribution timestamps are documented, but retention of every version, immutable historical bytes, correction arrival, retroactive replacement and contemporaneous schema preservation are not. Both products are `VINTAGE_SEMANTICS_UNCLEAR`.

Nasdaq's official 2024 fee notice and [SEC-filed approval](https://listingcenter.nasdaq.com/assets/rulebook/nasdaq/filings/SR-NASDAQ-2024-027_Notice_Approval.pdf) price Daily List including Fundamental Data at **$3,500/month/organization**. A Global Data Agreement, request/application and approval are required. Bulk 2016-2026 delivery, backtesting, derived tables, post-termination retention, contractors/users and CUSIP deletion/retention remain contract questions.

Disposition: credible Nasdaq-specific component/validator; not accepted without a vendor historical sample and written vintage/correction/retention answers.
