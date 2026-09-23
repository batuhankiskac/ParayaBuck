# Deterministic physical event order

At one UTC timestamp, the scheduler applies:

1. Corporate actions effective before an XNYS open, using pre-open inventory.
2. Previously existing sell orders against the current price event.
3. Previously existing buy orders against the current price event only after required sells have completed; active reservations persist through partial fills.
4. Precommitted terminal exits at their source price event.
5. Newly completed daily bars, marks and account risk refresh.
6. Strategy decisions in fixed market order (crypto, then equity) only for newly completed bars, using the source label for strategy cadence.
7. Sequential global slot admission, reservation and new order creation.

No order born in steps 6–7 may consume the price event from steps 2–4 at the same timestamp. Every fill stores and checks `order_created_at < source_price_event_time`. Market order within a tied decision instant is explicit, deterministic and evaluated for slot impact in the reordering audit.
