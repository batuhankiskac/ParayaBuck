# Crypto queue causal adapter protocol — 2026-09-08

This is a research-only protocol for the new K01, K03, K04, K09 and K11 data
paths. It does not change configuration, strategy registration, PAPER/LIVE,
execution, allocation, or any frozen candidate artifact.

## Frozen scope and cutoff

The asset pilot is BTC and ETH for Coin Metrics K01, K03 and K04. K09 uses
Binance USD-M perpetual BTCUSDT and ETHUSDT observations joined to explicitly
identified spot, futures, mark and index bars. K11 uses Binance USD-M BTCUSDT
aggregate trades. The historical cutoff is `2026-07-31T23:59:59Z`; rows after
it are quarantined from any research result.

Every row carries `event_time`, source file and source SHA-256. A row is
causally consumable only when `available_at` is present, UTC, and no later than
the decision timestamp. The supplied normalized rows leave this field blank.
Coin Metrics has no captured publication field; Binance archive receipts may
provide a separate server publication bound as described below. A blank field
remains `PIT_PENDING` until that bound is explicitly materialized by the
adapter.

The frozen manifest records raw SHA-256 values for each declared artifact and
input byte stream. Its `manifest_sha256` is a separate canonical JSON hash that
excludes only its own field; changing any other declaration or any referenced
file therefore fails byte verification before the canonical hash is accepted.

For Binance Vision archive rows, the receipt's server `Last-Modified` value is
retained as an archive-publication lower bound. It is distinct from trade or
funding event time and from local retrieval time. This is sufficient for the
one-day K11 causal adapter pilot when the bound is before the evaluation cutoff;
it does not create a longer historical vintage or make a current API
reconstruction point-in-time. It is recorded as archive evidence only and
never substitutes for a row's explicit timezone-aware `available_at`; blank or
late row availability keeps K11 `PIT_PENDING`.

## Native definitions

K01 consumes Coin Metrics `CapMVRVCur` directly. The retained market-cap field
is an audit field; no cross-asset level ranking is performed. K03 uses the
native `FeeTotNtv` series and computes within-asset 30-day log growth only when
all 31 daily observations are present and consecutive. Active-address and
transaction-count observations remain descriptive and are never labeled
Sybil-adjusted economic usage.

K04 retains native `IssTotNtv` and `SplyCur` separately. It computes gross
issuance divided by prior current supply as an audit ratio. The ratio is not
net dilution: no burn series is present in the Community sample. A net supply
candidate remains blocked until issuance, burns, supply policy, and causal
availability are all supplied.

K09 preserves venue, contract type, instrument, metric, frequency and event
time. Funding `event_time_utc` is the realized funding interval settlement or
calculation time. The archive does not provide an announcement or publication
time, and the adapter never infers one. OI keeps both native contract units
and USD value. Spot-only signal output is permitted only after a future
publication/availability source is joined; no derivative execution or funding
P&L is modeled. K11 signs notional as `+price*quantity` when
`is_buyer_maker=false` (buyer aggressor) and negative when true. This is signed
trade flow, never order-book depth imbalance. A K11 signal may select only the
first strictly later causal spot-bar open; same-time, missing, late-available
or ambiguous bars block execution. For K11, completeness is checked against
the external full-day archive expectation frozen in
`data/research_manifests/k11_signed_flow_archive_expectations_2026_09_08.json`.
That expectation is pinned to the raw ZIP SHA-256, member name, CRC and size,
row count, and either an explicit aggregate-trade ID set (synthetic fixtures)
or a strict contiguous first/last ID range (the BTCUSDT pilot). It is an
external archive contract established before signal calculation, not a count
inferred from nonempty hourly buckets. Its scope is one symbol/day and it
cannot establish completeness for omitted rows outside that immutable archive.

## Validation and stop conditions

Adapters reject duplicate primary keys, malformed UTC timestamps, non-finite or
non-positive prices, mismatched source hashes, post-cutoff rows and ambiguous
instrument identity. A missing `available_at` is retained as a data-gate
finding and cannot be silently replaced with retrieval time. No parameter,
direction, threshold, window or holdout is selected from returns. Economic
diagnostics remain out of scope until the causal gate is green and a separate
candidate brief freezes those choices.

The first deliverable is therefore normalized, hashed, schema-tested research
evidence and an explicit blocker report. No candidate is promoted or integrated
automatically.
