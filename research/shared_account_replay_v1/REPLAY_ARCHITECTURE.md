# Replay architecture

This package is research-only. `replay.py` lives under `research/` and is not
imported by production runtime code. It wraps the existing market backtest
engine's strategy, risk-overlay, fill, fee, terminal-lifecycle, and corporate
action methods with one shared synthetic ledger.

One `_SharedEngine` is created for each enabled incumbent market. Its position
and mark views are scoped to `(market, canonical symbol)` while cash and NAV
resolve to the single `SharedAccount`. Pending orders remain market-owned, but
the overlay sees all markets' working-order symbols and same-cycle BUY slot
reservations. The shared coordinator merges actual execution-bar timestamps;
it does not fabricate bars for a closed market.

Supported historical product identities are exactly:

| Market | Strategy |
| --- | --- |
| Equity | `equity_residual_momentum_v3` v3.0.0 |
| Crypto | `crypto_low_variance_hysteresis_v1` v1.0.0 |

The production research runner uses the configured starting capital of $100,000,
one cash balance, flat positions, empty working orders, global cap 6, and current
strategy allocations. Equity replay begins at the first eligible session-date
label, `2017-01-03T05:00:00+00:00`; crypto replay begins at `2021-04-01T00:00:00+00:00`
after its supplied history is loaded causally. The combined timeline begins
with the first equity evaluation event. Crypto receives no separate cash
sleeve. The daily equity label is a canonical session-date timestamp, not the
actual close availability time; see the event-clock approximation.

The runner loads the existing frozen inputs and product universes. It never
reads broker state, credentials, runtime databases, or production ledgers. The
only output intended for combined replay is event/state accounting evidence;
combined return metrics are not emitted as research results.

## Starting account state

`cash == starting_nav`, `positions == {}`, and no pending orders for both
markets. `SharedAccountReplay.add_market` rejects non-flat startup on the first
market registration. No historical broker activities or persisted
`PortfolioState` are imported.

## Data and identity

The shared account ledger keys inventory and marks by `(Market, canonical
symbol)`. Risk snapshots retain each position's market. Equity and crypto
symbols therefore cannot alias through a string-only inventory key.

## Lifecycle

The coordinator runs market engine methods in the phases frozen in
`EVENT_CLOCK_SPEC.md`. Existing canonical engine methods remain authoritative
for strategy decisions, order creation, fills, fees, terminal actions, and
equity corporate actions. The adapter adds account sharing, cross-market
reservations, one risk-state update per merged timestamp, and deterministic
event receipts.

The engine's historical fill model is a deterministic bar model, not a broker
simulator. Production's asynchronous order acknowledgements, partial-fill
timing, and deferred-buy account refresh remain limitations listed in
`REPLAY_LIMITATIONS.md`.
