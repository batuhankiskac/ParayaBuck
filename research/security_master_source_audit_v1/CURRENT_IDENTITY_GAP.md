# Current security identity gap

Audit date: 2026-09-22. Scope: source and entitlement audit only.

## Frozen local authority

- Starting local `main` and `origin/main`: `91adb80a6157a10457e1b526e4a96b7fb8f293f3`.
- Canonical bars: `data/historical/equity_pit_symmetric_2016_2026_v5.csv`, SHA-256 `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`, 11,686,832 rows, 9,010 symbols/entities, 2,659 calendar-session dates from 2016-01-04 through 2026-07-31.
- Canonical lifetimes: `data/historical/equity_entity_lifetimes_full_v5.csv`, SHA-256 `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`, 9,010 rows: 6,858 active and 2,152 inactive.
- Canonical corporate actions: `data/historical/equity_survivorship_full_corporate_actions_v5.csv`, SHA-256 `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`, 143,446 rows.
- Lifetime venue labels: NASDAQ 3,783; NYSE 2,279; ARCA 1,783; BATS 937; AMEX 228.
- Research window: 2016 warm-up; development 2017-01-03 through 2021-12-31; validation 2022-01-03 through 2026-07-30. The 2026-07-31 rows are a partial endpoint outside the common reference/hedge endpoint.

## What the repository can prove

The lifetime table records `entity_id`, provider asset UUID, canonical and historical symbols, validity and first/last-observed timestamps, active/inactive status, one exchange label, rename chain, evidence class, PIT quality tier, terminal-event class, and notes. Provider UUIDs prevent the local table from being purely symbol keyed. Bars record timestamp, symbol, OHLCV, timeframe and the broad `equity` market class. Corporate actions record dividends and split-related events.

The scanner uses current candidate attributes including tradability, active status, exchange, price, liquidity, ATR, completeness and a supplied leveraged/inverse flag. It does not query an effective-dated issue taxonomy. `SPY` is reference-only; `SH`, `PSQ`, and `DOG` are hedge-only. That boundary is a ParayaBuck role rule, not proof that all other rows are ordinary company equity.

## What it cannot prove

The canonical history has no effective-dated security type, product subtype, CIK, issuer-level identifier, CUSIP, ISIN, fund Series/Class ID, listing-event record, or source-publication vintage. It therefore cannot prove that historical `US_EQUITY` meant ordinary/common company equity.

Only one lifetime row has a non-empty rename chain. Ticker reuse, relisting, class conversion, merger successor, exchange transfer and same-issuer/new-class continuity cannot be resolved comprehensively. `entity_id == provider_asset_id` is a provider identity, not an independently proven exchange issue identity. The exchange column is not accompanied by dated listing-transfer events. `TIER_A_VERIFIED_PIT` describes the existing lifetime construction; it does not certify product taxonomy.

The corporate-action file is an accounting complement, not an identity master: it contains dividends and split categories but zero recorded stock mergers, cash mergers, spinoffs or name changes. A future issue master must not duplicate dividend/split cash and price accounting.

## Required future contract

A future shadow master needs immutable internal `issuer_id`, `security_id`, and `listing_id`; source identifiers and aliases with inclusive `effective_from`, exclusive `effective_to`, and separate `observed_at`; raw source values and hashes; explicit predecessor/successor links; and first-class `UNKNOWN`/conflict states. CIK is issuer-level only and must never collapse multiple classes.

This audit does not modify the lifetime, bars, scanner, roles, corporate actions, configuration, PAPER, or LIVE state.
