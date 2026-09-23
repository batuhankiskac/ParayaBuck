# Isolated execution parity V2

Equity uses the existing completed daily-bar V3 strategy and canonical PIT universe, weekly decision cadence, next actual bar open fill, effective-dated regulatory fees, split/dividend and terminal lifecycle methods. The V2 runner compares seven frozen V3 control categories: input identity, target/fill/terminal hashes, counts, chronology, reserved-role observability, and development/validation/full metrics at 1e-12 relative and absolute tolerance.

Crypto uses configured 22-day variance, top two, 96-hour hysteresis strategy and daily signals built from complete hourly bars. It retains the configured 0.005 rebalance band and canonical next-bar pricing, taker fee and costs. The V2 runner compares normalized exact numeric hashes of decisions, snapshots, fills, terminal events, final state and curve plus counts against the repaired hourly control. Both isolated receipts must PASS before any combined run.
