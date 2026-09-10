# Crypto secondary causal gate protocol — 2026-09-08

This is a research-only acquisition and schema gate for K02, K05, K06, K07,
K08, K10 and K12. It does not change production, configuration, strategy
registration, V3, PAPER, LIVE or any economic result.

## Frozen common boundary

The target cutoff is `2026-07-31T23:59:59Z`. The common economic windows are
`full=2022-01-01..2026-07-31` and `recent=2024-01-01..2026-07-31`, each at
1x/2x/3x costs. The unchanged account controls are the existing crypto
incumbent, an isolated candidate, a passive/cash control, a fixed or
matched-exposure control where the candidate contract permits it, and a
shared-capital incumbent-plus-candidate diagnostic. No return cell is run by
this gate. A post-2024 holdout is required for K06; its exact dates are not
specified by the authoritative queue review and therefore remain
`UNSPECIFIED`, never inferred from results.

The native execution boundary is a completed observation, one decision
interval of lag where the card specifies it, and the next tradable spot open.
Every causal row must retain event time, publication or vintage time when the
provider supplies one, explicit timezone-aware `available_at`, provider/venue,
asset or contract identity, units, revision/version and source SHA-256.

## Frozen candidate contracts

| Card | Native field and meaning | Universe and cadence | Signal contract | Gate minimum |
|---|---|---|---|---|
| K02 | Coin Metrics adjusted transfer value; NVT is market value divided by adjusted transfer value | Configured BTC/ETH assets; daily completed | `NVTAdj = market_value / adjusted_transfer_value`; one-period lag; within-asset only; long/cash; smoothing, threshold and direction unspecified in the authoritative review | adjusted-transfer version, self-churn/early-spend policy, chain coverage, denominator-zero rule, PIT availability |
| K05 | Token-specific issuer/governance announcement plus scheduled vesting and actual on-chain unlock | Frozen configured 22-pair pool; each asset requires primary event evidence; event-only | Published-before-decision event; exact direction and event window remain unspecified until a manifest freezes them; long/cash | canonical URL, immutable capture, publication time, scheduled time, units/percent, beneficiary class, actual transfer, amendments and migration map |
| K06 | Issuer-published spot BTC/ETH ETF net creations/redemptions, aggregated by underlying | BTC/ETH; daily; short post-2024 history | Consume after issuer publication and at least one decision interval later; long/cash; ETF set and event window frozen before economics | issuer/SEC source, correction policy, cash/in-kind treatment, exchange calendar, ETF mapping, publication time, untouched post-2024 holdout |
| K07 | Labeled centralized-exchange wallet inflow minus outflow; native units and USD-normalized variants remain separate | BTC/ETH pilot; daily or hourly only with provider availability | One-period lag; long/cash; sign is frozen with metric version; no universal sign assumption | PIT endpoint or immutable snapshot, label-set hash per vintage, chain confirmations, exchange scope, internal-transfer exclusions, missingness and latency |
| K08 | Stablecoin net exchange flow, separately for USDT and USDC; mint/burn/bridge movements excluded | BTC/ETH spot target; stablecoin contracts and exchange labels frozen; completed interval | One-period lag; long/cash; interval and direction frozen before economics; no minting-as-buying shortcut | contract identity, mint/burn/bridge filters, label vintages, PIT time, cross-venue coverage and one-hour latency evidence |
| K10 | Venue-tagged liquidation volume with side/quantity, OI delta, mark/index and spot context | BTC/ETH spot target; venue aggregation frozen | Trigger only after available liquidation event; one fixed wait and hold interval selected before results; long/cash spot; no derivatives | full versus incremental event semantics, duplicate handling, OI units, venue aggregation, latency and shock fill cost |
| K12 | SOPR: spent-output value divided by creation value; entity-adjusted variant must retain clustering policy | BTC first, then only same-semantics assets; daily completed | Single level or fixed deviation from one must be frozen before economics; one-period lag; long/cash | immutable PIT metric, entity-cluster version, UTXO/account treatment, internal transfers, chain coverage and publication lag |

## Evidence rules

Current or mutable labels, aggregator pages, bounded probes and provider
methodology pages are acquisition evidence only. OHLCV cannot substitute for
on-chain transfer, stablecoin, ETF-flow, liquidation, OI or SOPR fields.
Missing publication/vintage or `available_at` leaves a candidate
`PIT_PENDING`; no retrieval time or current label is silently substituted.
Unavailable, licensed, credentialed or unsupported endpoints are recorded as
data/capability blockers with sanitized receipts and hashes. No economic output
is considered canonical until an independent review of this protocol,
manifests, raw inputs, tests and gate receipt.

Successful liquidation ZIP probes must retain the archive SHA-256, member name,
CRC, byte size, exact header, row count, and first/last event identifiers. The
gate reopens each archive and rejects any receipt whose member identity or
content metadata differs. A blank `available_at` remains zero admitted rows;
non-tabular responses use `row_count: null` with
`count_semantics: not_applicable`, JSON `data` arrays use
`json_data_array`, CSV data rows use `csv_data_rows`, and ZIP member rows use
`zip_member_data_rows`; numeric counts on non-tabular bodies fail closed.
retrieval time and archive `Last-Modified` do not replace row-level PIT
availability.
