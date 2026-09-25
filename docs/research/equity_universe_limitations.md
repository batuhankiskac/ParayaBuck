# Equity-universe limitations and operator contract

Status: current limitation; commercial security-master path deferred 2026-09-22
(`DEFERRED_EXTERNAL_DATA_NOT_WORTH_PROCUREMENT_BURDEN`). Superseded in
production on 2026-09-25: the equity universe became static
(`universe.equity.symbols: [SPY, QQQ, QLD]`, no equity scanner), so the
broad-`US_EQUITY` concerns below now describe the archived research dataset
and the removed V3 baseline, not the shipped universe. See the security-master
closure in
[`research_register.md`](research_register.md); evidence preserved behind the
three `archive/research/security-master-*-2026-09` tags.

## Current operator contract

Ordinary company-equity alpha should eventually target instruments representing
direct ownership in an operating/company issuer: common stock, appropriate
ordinary shares or company-equity ADR/ADS, and REIT common equity. ETFs, ETNs,
other exchange-traded products, leveraged/inverse products, volatility-linked
products, commodity-linked products, bond/fixed-income funds, crypto-linked
ETPs, closed-end funds unless separately proven, preferreds, warrants, rights,
units, and structured notes are not ordinary company-equity alpha by this
policy. Ambiguous records remain `unknown` rather than being promoted by
default.

## Current implementable reality

Production streams exactly `universe.equity.symbols` — SPY (benchmark), QQQ
(`equity_leveraged_trend_v1` trend signal) and QLD (its only holding) — inside
`maximum_stream_symbols` (30, the Alpaca IEX limit). There is no equity scanner,
no candidate scoring, no sector/issuer diversity rule, and no persisted equity
universe artifact, so nothing is discovered at runtime and no product
classification is consulted. Managed positions outside that list stay pinned
until sold.

Because membership is now explicit configuration, the classification gap
described below no longer has a production surface: adding a symbol is a
reviewed `config/settings.yaml` change. It still binds the archived 2016–2026
PIT dataset and the removed V3 baseline, whose bars carry no security type,
stable exchange identity, or effective-dated product metadata capable of
proving a common-stock-only conversion.

## Reserved roles

- `SPY` is `reference_only`: benchmark context, never a traded target.
- `SH`, `PSQ`, and `DOG` are no longer configured, streamed, or ranked by
  anything. The approved hedge-only role and its ordinary-alpha exclusion
  plumbing (`inverse_trade_symbols`, `ordinary_alpha_excluded_symbols`,
  `is_ordinary_alpha_eligible`) were removed with the equity scanner.
- The corrected canonical V3 replay recorded zero ordinary target eligibility
  for all four reserved symbols. That evidence stands as a record of the
  removed baseline, not of current behaviour.

This role boundary does not claim that every ETF, ETN, ETP, ADR, preferred,
warrant, unit, or structured product has been classified historically.

## Known product-taxonomy limitation

The finite static leveraged/inverse symbol list that used to gate ordinary
alpha is gone with the scanner, so that protection no longer exists in either
form. Any future dynamic equity universe must not reintroduce it as the
mechanism: a finite symbol list is not a security-master lookup, and products
missing from such a list would appear as ordinary `equity`. Current Nasdaq
directories and issuer/SEC evidence can identify many current products, but a
current symbol flag is not point-in-time membership or identity.

The security-master source study concluded
`SECURITY_MASTER_CURRENT_ONLY_HISTORICAL_BLOCKED`: Nasdaq Daily List history
and NYSE Security Master history provide the strongest demonstrated fields but
require secured/licensed access for the historical data needed here. Current
free source snapshots are suitable for a research-only current classifier, not
for proving complete 2016–2026 PIT coverage of all 9,010 entities.

## Sector semantics

There are none. `maximum_per_sector`, `markets/sectors.py` and
`UniverseSnapshot.sector_metadata_coverage` were removed with the equity
scanner; the crypto scanner keeps its own `maximum_per_category`,
`maximum_meme_symbols` and `maximum_high_beta_symbols` limits. Do not
reintroduce a sector cap as a hard portfolio constraint — when an equity
universe was last dynamic it was explicitly an observation-set diversity rule
only, never a cap on the target portfolio.

## Future resolution gate

The commercial NYSE Group Security Master path was technically promising but
intentionally deferred: original-vintage semantics stayed unproven from public
documentation and further proof required vendor contact and commercial
negotiation. `NO_VENDOR_CONTACT_MADE`; nothing is pending with NYSE.
Public/free research continues under
[`public_data_research_policy_v1.md`](public_data_research_policy_v1.md).

A future dynamic equity universe requires an operator-approved policy and an
effective-dated source, at minimum covering the relevant Nasdaq and NYSE/Arca
listing events, with Cboe/BZX coverage or an equivalent source and SEC
Series/Class/EDGAR evidence as supplements. The implementation gate should
include:

1. counts-only mapping of all 9,010 historical symbols;
2. date-interval and listing-event coverage;
3. stable-identity and ticker-reuse ambiguity reports;
4. unknown/product-type fail-closed fixtures; and
5. a fresh structural universe replay followed by one frozen V3 incumbent
   revalidation.

Until that gate is met, do not describe the archived dataset as
common-stock-only and do not silently replace it with a current-only taxonomy.
The shipped static universe makes neither claim.

## Provenance

The source conclusion is preserved at
`archive/research/equity-security-master-source-v1-2026-09`; the preceding
structural audit is preserved at
`archive/research/equity-universe-integrity-v1-2026-09`. Their compact
decisions are indexed in [`research_register.md`](research_register.md).
