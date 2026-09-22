# Crypto giveback report

`AUDIT_INCONCLUSIVE` / `AUDIT_INCONCLUSIVE_REPLAY_MISMATCH`.

The exact frozen input bytes were recovered and verified. Two fresh current-production runs (one instrumented, one uninstrumented) match exactly. However, the historical canonical universe hash and all six stored output hashes disagree. Fresh fill count 388 vs historical 454; decisions 455 vs 453. Historical ending equity $110,539.90 differs from fresh $105,481.52. These are parity evidence, not a giveback comparison or an alternative-exit backtest.

The dataset is crypto_expanded_1h.csv, aggregated into complete daily bars, with hourly PIT universe snapshots, 2021-04-01 through 2026-07-31. Frozen dataset SHA: 73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e. See CRYPTO_REPLAY_PARITY.json and crypto_replay.py for source commit and reconstruction. The present output is quarantined; episode count, holding time, MFE, MAE, retention, round trips, exit-reason proportions, +20/+30/+40% outcomes and right-tail attribution are N/A, not zero. No shifted path is interpreted as the historical production control.

Code confirms retention of eligible holdings through rank four, new vacancies filled from the best-ranked candidates, equal weights and a 96-hour cadence. It intentionally delays replacement in return for lower turnover. Vacancies do not rescue a holder that fails the rule; trade bands, minimum notional and next-bar execution can delay actual flattening. How often hysteresis extends holdings beyond eventual peaks cannot be stated on an accepted replay in this audit.

Historical half-year walk-forward folds overlap the promotion dataset; they are not untouched post-promotion validation. Repairing/explaining canonical reproducibility is necessary before an empirical crypto exit campaign can be justified. No strategy was changed to force parity.
