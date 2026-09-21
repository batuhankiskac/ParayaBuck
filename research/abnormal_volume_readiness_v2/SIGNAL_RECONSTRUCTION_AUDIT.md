# Signal reconstruction audit

`RUNNER_OUTPUT.json` retains the full 115-boundary structural record. For every selected target it persists the exact 49 prior sessions, prior split-consistent dollar volumes, sorted multiset, `x_(44)` threshold, formation dollar volume, abnormal-volume score, formation return, inclusive-50 average ranks, candidate rank, stable-identity tie-break result, frozen target weight, intended first-open session, fill availability, and any lifecycle exclusion. It contains no post-formation return or economic field.

The binding estimator is software-independent integer arithmetic: zero-based index `9*(49-1)//10 = 43`, hence one-based `x_(44)`, without interpolation or deduplication. Focused fixtures distinguish it from `x_(45)` and cover distinct values, duplicates, heavy ties, an extreme outlier, and below/equal/above threshold relations. Quantile affects the secondary score; eligibility remains the separate inclusive-50 dollar-volume average-rank requirement of at least 46.

Runner coverage produced 1,730 eligible signals and 595 top-six targets. A separately implemented raw-data verifier reconstructed the same eligible scalars, ordering, targets, weights, schedule, fill availability, and terminal events for all 115 boundaries. Both normalized comparison hashes equal `8baed348f477f4f12746c1f27bc7c7a2b8214a2c35fe5271260a82a1e6515e33`.

The first verifier attempt correctly failed exact comparison because its fixed 50-digit Decimal context exposed the runner's ambient 28-digit serialization. No target identity, rank, fill, or event differed. Precision was then fixed at 50 digits inside the runner's signal and weight calculations, the runner was replayed, and the fresh verifier passed exact comparison.

Result: `PASS`.
