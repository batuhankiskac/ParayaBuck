# Regression coverage

`tests/test_global_slot_accounting.py` covers six-held opening rejection,
five-held competing openings in planner order, adds to held names, one
pending opening reservation, pending and partial exits, confirmed zero exit,
terminal opening cancellation/rejection modeled by source removal, same-cycle
equity/crypto competition, canonical broker open-order symbols, the exact
synthetic LTC/BCH/BTC band-suppressed replacement, executed SELL without
optimistic release, deterministic reruns, and a table of equity/crypto
holdings, pending BUY/SELL claims, partial exits, and multiple new intents.
The table asserts the cap after each admission transition. Archived replay
synthetic tests and combined cap-six smoke are separate validation gates.
