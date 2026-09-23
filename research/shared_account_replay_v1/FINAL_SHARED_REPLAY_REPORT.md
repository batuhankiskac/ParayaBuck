# Shared-account replay decision

`SHARED_ACCOUNT_REPLAY_BLOCKED_BY_GENERIC_CORRECTNESS`

# Migration V2 closure

## Archive tag

`archive/research/account-capacity-migration-8-v2-inconclusive-2026-09`

## No outcomes confirmation

V2 remains inconclusive. No frozen specification or cap-8 outcome was created.
See [MIGRATION_V2_CLOSURE_RECEIPT.json](MIGRATION_V2_CLOSURE_RECEIPT.json).

## Branch deletion

The local V2 work branch was deleted after its closure receipt was archived.
This replay was started from the new main commit below.

# Starting main SHA

`7b0a1063ba5a8ca8ed6bf93cf06a0b684da94b3a`

# Replay architecture

Research-only coordinator over the canonical equity and crypto portfolio
engines. It runs exactly `equity_residual_momentum_v3` and
`crypto_low_variance_hysteresis_v1`, with one cash balance, asset-aware
`(market, canonical symbol)` positions, one current global position cap of 6,
and no cap-8 arm. It makes no broker calls and does not change runtime or
production configuration.

# Starting account state

Flat and all cash: NAV `$100,000`, cash `$100,000`, zero positions, and zero
pending orders. The first shared event label is `2017-01-03T05:00:00+00:00`.

# Cross-market event clock

The coordinator sorts the union of observed market timestamps in UTC. Crypto
is processed before equity only for identical timestamps. Equity daily labels
are New York midnight; crypto daily labels are UTC midnight. These labels are
not actual close-availability timestamps, so the replay preserves canonical
bar labels and isolated parity rather than claiming intraday availability
proof.

# Same-timestamp ordering

The replay ingests bars and corporate actions, attempts all account sells,
blocks BUY fills while a SELL remains active, marks available bars, updates
shared risk state, then decides crypto before equity. Same-cycle BUY orders,
active working orders, and deferred BUY intents from the other market reserve
slots. BUY intents held behind a sell phase activate only after all sells are
terminal and can fill no earlier than a later bar.

# Shared cash/ledger model

Both engines share one cash ledger and one position inventory. Canonical fill,
fee, and equity corporate-action methods update that ledger. Cash reconciles
within `-8.73e-11`; position quantity reconciliation error is `0`.

# Global slot semantics

Current configured cap is 6. A slot is an asset-aware market/symbol pair;
duplicate position and active-order claims for the same instrument deduplicate.
The combined smoke did **not** preserve the cap: it observed 7 live positions
and 7 slot claims, with 21 violating timestamps.

# Pending-order reservations

Active replay order symbols reach the shared overlay. Deferred BUY intents are
unsubmitted and excluded from the active position-plus-working-order count,
but reserve future capacity against decisions in the other market. This closes
the first observed cross-timestamp gap. A separate same-market slot-accounting
failure remains, documented below.

# Equity execution semantics

The equity-only replay uses the canonical V3 historical bars, point-in-time
universe, lifetimes, corporate actions, fill, and cost path. Its isolated
parity receipt passes all recorded chronology, count, input-identity, and
control checks. Data timestamps retain their supplied New York-midnight labels.

# Crypto execution semantics

The crypto-only replay aggregates the supplied complete hourly bars to daily
bars and uses the canonical crypto hysteresis strategy, fills, and costs.
Its isolated parity receipt passes all recorded decision, snapshot, fill,
terminal-event, state, curve, and accounting checks.

# Mixed mark-to-market

NAV uses the latest available mark per market/symbol at each merged timestamp.
It does not fabricate equity bars while the equity market is closed. The
timestamp labels remain the source-data labels described above.

# Corporate actions

Equity splits, dividends, and terminal lifecycles use the canonical equity
methods; crypto is excluded from equity corporate-action processing. The
combined smoke recorded 83 dividend cash events and balanced cash accounting.

# Equity-only parity

`PASS`; see [EQUITY_ISOLATED_PARITY.json](EQUITY_ISOLATED_PARITY.json).

# Crypto-only parity

`PASS`; 455 decisions, 388 fills, and 1,946 NAV observations; see
[CRYPTO_ISOLATED_PARITY.json](CRYPTO_ISOLATED_PARITY.json).

# Production-semantics parity

The mapping is in
[PRODUCTION_SEMANTICS_MAPPING.md](PRODUCTION_SEMANTICS_MAPPING.md). Shared cash,
market-scoped positions, canonical fills, risk limits, and modeled orders are
represented. Broker acknowledgement timing, rejects, buying-power refresh,
and actual intraday bar availability are not modeled.

# Combined current-cap smoke replay

**FAIL.** Both full-history runs were deterministic, with identical event
SHA-256 `adcf6a164719e4c9ad22df959d4724e9413427ab6ad6b17d1b87baea5786b989`.
The current-cap smoke observed 7 positions against cap 6 at 21 timestamps.
Combined performance metrics were not interpreted. See
[COMBINED_SMOKE_INVARIANTS.json](COMBINED_SMOKE_INVARIANTS.json) and
[DETERMINISM_RECEIPT.json](DETERMINISM_RECEIPT.json).

# Accounting invariants

Passing checks include cash conservation, NAV/ledger reconciliation, position
quantity reconciliation, no unowned or duplicate fills, 2,115 reservation
creations matched by 2,115 releases, target gross no greater than 0.55, per-name
target weight no greater than 0.10, and 4,352 future-timestamp checks. The
global position and slot-claim limit checks fail: the observed maximum is 7.
Realized gross reached 0.5898 during mark drift, below the configured 0.60
gross ceiling.

# Determinism

The two event hashes, NAV paths, cash paths, final cash, and final positions
match. The determinism receipt is nevertheless `FAIL` because it carries the
failed global slot-limit checks.

# Known limitations

- Daily equity and crypto timestamps preserve source labels, not actual
  exchange close-availability times.
- Broker acknowledgement latency, queue position, rejects, buying-power
  refresh, and live terminal-order timing are not simulated.
- The replay starts flat and has no manual or broker-only positions/orders.
- No combined return, CAGR, Sharpe, or drawdown result is used as research.

# Generic correctness findings

The June 14, 2024 trace began with six positions: four equities plus
`BCH/USD` and `LTC/USD`. The shared crypto overlay targeted `BCH/USD` and
`BTC/USD`; the LTC holding remained live, no LTC SELL was created, and BUYs for
BCH and BTC produced seven distinct slot claims. Its current mark was about
`$79.21` and quantity was `1.1548` LTC. This is consistent with the configured
`0.005` rebalance band suppressing a sub-band exit while the risk overlay treats
the managed current-market holding omitted from the target as replaceable.

That overlay and order-existence policy are shared with the production
portfolio path. The behavior could affect PAPER/LIVE slot enforcement. Per the
generic-correctness stop rule, production behavior was not changed and further
replay work stops here. This finding needs a separate production-correctness
campaign before replay validation can resume.

# Validation status

The focused shared-replay suite passes (`12 passed`), the open-order
pagination regression suite passed (`18 passed` in the preceding validation
run), targeted Ruff lint and strict mypy pass, and the two combined runs have
identical event hashes. The full pytest suite and repository-wide static
checks were not run after the generic-correctness stop condition was reached.

# Migration readiness

`MIGRATION_REPLAY_NOT_READY`

# Production state

Production code and configuration are unchanged. `maximum_positions = 6`;
V3 and crypto strategies are unchanged; PAPER/LIVE mode and orders are
unchanged. No broker API calls or broker orders were made.

# Final main status

No replay changes were merged to main. Main remains at starting SHA
`7b0a1063ba5a8ca8ed6bf93cf06a0b684da94b3a`.

# Final branch SHA

The delivery message reports the branch tip after committing this research
record.

# Exact next action

Isolate and resolve the production slot-accounting versus rebalance-band
behavior in a separate production-correctness campaign. Then rerun the
current-cap shared replay. Do not start
`account-capacity-migration-8-v3` until that replay passes its global slot
invariant; no cap-8 comparison is part of this run.
