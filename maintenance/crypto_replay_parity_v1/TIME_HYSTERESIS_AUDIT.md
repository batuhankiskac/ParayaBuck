# Time and hysteresis audit

`RebalancePolicy.is_period_start` compares floor(epoch_seconds/(96*3600)). It is a UTC epoch-block rule, not96 elapsed hours from entry or previous decision. First observation is scheduled. From an exact boundary,24/48/72h are not due,96h and120h cross the block; from24h before a boundary the next daily bar is due after24 elapsed hours. Tests explicitly cover these cases.

Engine `_decide` updates previous timestamp on every decision-bar cycle before testing schedule. Warmup and replay start therefore affect first-decision availability. Strategy hysteresis has no separate clock; it is evaluated when the engine calls it. Scanner eligibility, completed history and current held weights at that point determine retention.

CSV loader defaults absent crypto timeframe to1h and naive timestamp toUTC. Expanded source timestamps carryZ. `aggregate_historical_bars(...,'1d')` anchors daily buckets at00:00 in the timestamp timezone, requires24 contiguous hourly bars by default, and discards partial buckets; OHLC uses first/max/min/last, volume sums. No synthetic filling occurs. Zero-volume hourly bars count toward completeness. Daily labels are bucket starts although OHLC contains the completed day's values. Engine fills pending orders only on strictly later timestamps, at the next eligible daily bar open; labels must not be confused with actual observation wall time.

`run_product_backtest` does not silently aggregate hourly data: it filters to the strategy timeframe. The runner must explicitly supply daily bars. Replaying a different prebuilt daily file is a data transformation change.

Engine dedup canonicalizes symbols and collapses identical duplicates; conflicting duplicates raise. Raw expanded file has no duplicate keys. The23-close volatility window requires an exact22-day endpoint span; missing daily history blocks qualification.

## Scanner timeframe authority and independent verification

The historical replay explicitly builds its universe from1h bars, while the public product adapter defaults to the strategy1d timeframe. Preserve the historical1h scanner convention for the parity maintenance baseline; the daily-default replay is a diagnostic comparator, not a universe promotion. Choosing between them using returns would violate the unchanged-universe boundary.

The independent `universe_verify.py` uses only the Python standard library. Hourly mode reads the raw SHA-pinned CSV directly and reconstructs each daily boundary's strictly prior14-day input window. It verifies every pool member, candidate count, elimination reason and available pin. It never calls the production universe/scanner/candidate functions. Receipt:2037 hourly snapshots and27162 candidate checks match, with308 pin eligibility bypasses. Daily comparator:2033 snapshots and27099 checks match, with37 bypasses.

Pins bypass scanner metric failures when trailing history exists. The broad eligible pool is not category- or stream-capacity-truncated; those limits affect fallback observation. BTC correlation affects quality score, not hard eligibility. Daily14-day windows cannot meet the24-return correlation minimum; hourly windows can. Spread is unavailable and has no effective spread exclusion. The historical adapter supplies active/tradable flags from bar availability, not historical broker metadata. These are explicit limitations; successful replay verifies the adapter contract, not real historical broker eligibility. This verifier does not independently recompute scanner score/correlation quality ordering or observation-set alpha ranking; those must not be inferred from its eligibility receipt.
