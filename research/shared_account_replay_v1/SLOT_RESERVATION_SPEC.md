# Position slots and reservations

## Occupancy

A live ledger position with quantity greater than zero occupies one global slot
identified by `(market, canonical symbol)`. Tiny positive positions still
occupy a slot. A zero or negative quantity is not an active slot; the canonical
fill path removes a position when its quantity reaches zero. An exit-pending
position remains occupied until the sell fill closes it.

A working-order symbol is included in account slot claims. A partially filled
BUY therefore has one occupied position and one remaining working order, which
deduplicate to one composite slot. A BUY with no fill reserves its slot from
order creation until fill, cancellation, supersession, or terminal cleanup.
SELL orders on an already-held instrument do not add a second slot.

In combined mode, BUY intents produced by a rebalance that also has working
SELLs remain unsubmitted, so they are not actual position-plus-working-order
claims. They do reserve future capacity from later cross-market risk decisions:
the shared overlay receives their symbols while they are deferred. After every
account SELL is terminal, an intent becomes a working BUY at the current replay
timestamp; the canonical next-bar fill rule prevents it from filling on the
same bar that completed the sell phase. Its quantity remains the
decision-time quantity, so the replay does not emulate production's
fresh-account BUY resize.

## Same-cycle coordination

At an exact shared timestamp the crypto decision runs first. After target
construction, each actual BUY order created by that decision is entered in the
same-cycle reservation set. The following equity risk-overlay call receives
those `(market, symbol)` claims, account-wide working-order symbols, and any
deferred BUY-intent symbols. The overlay then applies its existing rank-aware
global cap and market-specific cap without granting the later decision a slot
already claimed by the earlier one.

The reservation is created only after the strategy target has passed the
canonical risk overlay and an order exists. A target name that was removed by
the overlay does not reserve a slot. Duplicate names collapse to one canonical
key.

## Release

The event ledger records each order reservation once and one release when the
order disappears. Full fill, decision supersession/cancellation, terminal
lifecycle, and end-of-history cleanup are recorded separately. Partial fills
keep the same order reservation active. The position and remaining order
continue to consume one composite slot. Reservation create/release counts and
active terminal reservations are reconciled at replay end.

## Capital and order phases

Slot reservations do not earmark notional. The global cash account is debited
only by fills. A BUY that cannot be paid from the remaining cash stays working
under the canonical offline fill policy. In combined mode, all account SELL
fills are attempted before BUY fills at a timestamp; any remaining working
SELL defers account BUY fills to a later event. Single-market parity controls
retain the canonical historical engine's fill cycle. Production's asynchronous
terminal-status checks and fresh-account resize after sells are explicitly not
modeled.
