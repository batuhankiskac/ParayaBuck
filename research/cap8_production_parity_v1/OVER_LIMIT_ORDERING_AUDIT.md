# Over-limit order admission

`tests/test_cap8_production_parity_audit.py` uses a synthetic $100,000 account, six managed equities at $45,000 total and one manual crypto lot at $15,500; realized gross is 0.605 and cash is 0.395. The production-like limits are gross 0.60, equity managed allocation 0.55, crypto cap 2, global cap 8, rebalance band 0.005. No broker network is used.

| Case | Observed PAPER runtime behavior | Assessment |
|---|---|---|
| New-symbol BUY | Immediate TSLA BUY for $1,900, while overlay reports target gross 0.600 | Violates fresh gross headroom contract |
| Add-to-held BUY | Immediate AAPL BUY for $1,500 | Same violation |
| SELL | Immediate AAPL SELL | Risk reducing; allowed |
| Replacement SELL + BUY | SELL submitted; BUY remains deferred until sell terminal and fresh sizing | Safe sequencing in this fixture |
| External/manual inventory | The manual crypto consumes a slot and gross; at cap 6 a new opening is denied | Correct slot/external accounting |

Small intended reductions to the other six names are each below the 0.005 rebalance band. Therefore the planner emits only the BUY even though the account is above gross. The overlay subtracts immutable external exposure from its target budget, but does not reserve headroom for retained managed exposure that the planner leaves untouched. Pure BUY submission then calls `_submit_orders` without `_size_buys_to_safe_capacity`. The test records existing behavior and is not a fix. The order can increase gross to approximately 0.624 or 0.620 before market movement/fees in the two BUY cases.

The frozen research trace records 128 BUY reservations while the most recently observed account gross was above 0.60. Its account-mark cadence cannot prove exact gross at each order creation or isolate queued earlier buys, so this count is a screening signal, not 128 proven production violations.
