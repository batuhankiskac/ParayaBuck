# Equity-universe limitations and operator contract

Status: current limitation; no universe policy change was made by the archive
consolidation.

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

Production currently requests the broad active/tradable Alpaca
`AssetClass.US_EQUITY` universe on supported exchanges. That class is not a
complete common-stock/product taxonomy. The historical PIT bars contain no
security type, stable exchange identity, or effective-dated product metadata
that can prove a full 2016–2026 common-stock-only conversion.

Therefore production and research retain the broad historical `US_EQUITY`
universe, with explicit role isolation, until an effective-dated security
master is acquired and validated. This is a known limitation, not a solved
classification result.

## Reserved roles

- `SPY` is `reference_only` and is never ordinary V3 alpha.
- `SH`, `PSQ`, and `DOG` are approved hedge-only instruments. They remain
  available for history and explicitly authorized hedge paths, but are excluded
  from ordinary V3 alpha targeting.
- The corrected canonical V3 replay recorded zero ordinary target eligibility
  for all four reserved symbols.

This role boundary does not claim that every ETF, ETN, ETP, ADR, preferred,
warrant, unit, or structured product has been classified historically.

## Known product-taxonomy limitation

The current implementation's leveraged/inverse protection is a finite static
symbol policy, not a security-master lookup. Products can be missing from that
list and appear as ordinary `equity` if their trading metrics pass. Current
Nasdaq directories and issuer/SEC evidence can identify many current products,
but a current symbol flag is not point-in-time membership or identity.

The security-master source study concluded
`SECURITY_MASTER_CURRENT_ONLY_HISTORICAL_BLOCKED`: Nasdaq Daily List history
and NYSE Security Master history provide the strongest demonstrated fields but
require secured/licensed access for the historical data needed here. Current
free source snapshots are suitable for a research-only current classifier, not
for proving complete 2016–2026 PIT coverage of all 9,010 entities.

## Sector semantics

`maximum_per_sector` remains a fallback observation-set diversity rule. It is
not a hard cap on the eligible opportunity pool, the strategy-ranked
observation set, or the final target portfolio. The alpha-ranked observation
path intentionally shares strategy scoring with target construction and does
not inherit the scanner fallback's sector-diversity behavior.

No sector rule was added during this cleanup.

## Future resolution gate

A future universe correction requires an operator-approved policy and an
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

Until that gate is met, do not describe the broad universe as common-stock-only
and do not silently replace `US_EQUITY` with a current-only taxonomy.

## Provenance

The source conclusion is preserved at
`archive/research/equity-security-master-source-v1-2026-09`; the preceding
structural audit is preserved at
`archive/research/equity-universe-integrity-v1-2026-09`. Their compact
decisions are indexed in [`research_register.md`](research_register.md).
