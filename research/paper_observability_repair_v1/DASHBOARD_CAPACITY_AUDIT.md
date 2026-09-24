# Dashboard capacity audit

`risk.buy_capacity` receives `capacity_health()` in the existing dashboard route. The risk card now displays `effective_crypto_slot_claims / crypto_slot_cap` beside the existing global capacity. The payload includes position counts, global/crypto effective claims, caps, available counts, working opening count, sample timestamp and symbol-level claim sources. Synthetic D1-D7 verify position, reservation, alias, working BUY, SELL, terminal release and partial-fill dedup behavior. Unknown broker OPEN side remains conservatively counted for compatibility.
