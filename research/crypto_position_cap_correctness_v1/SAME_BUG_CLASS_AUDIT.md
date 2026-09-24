# Same bug class audit

`reserve_opening_order_slots` has three production runtime call sites: pre-plan pure BUY, final immediate BUY, and final deferred BUY. All three now pass `maximum_crypto_positions` through an explicit market limit map. The local ledger, broker working symbols, and same-cycle reservations feed the final helper and deduplicate aliases. An opening BUY never treats a SELL request or terminal status as slot release. Equity has no new market-specific cap and continues to consume the global count.

The research physical scheduler also called the helper with only a global cap; it is classified `ADDITIONAL_BUG` for the replay and is repaired in the subsequent research continuation. No other production helper that admits portfolio opening BUYs after the overlay was found.
