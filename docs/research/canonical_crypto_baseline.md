# Canonical crypto replay baseline v2

Status: **CRYPTO_CANONICAL_REPLAY_VALID**. Historical control: **SUPERSEDED_HISTORICAL_CONTROL**. Governing conclusion: **CRYPTO_HISTORICAL_BASELINE_SUPERSEDED**.

This is a candidate-neutral, isolated offline replay contract, not a strategy promotion or a PAPER/LIVE account claim. Production source is `754f4060b1eb38593201b2a269e9593d083eb534`. Parameters, allocation, tradable candidate pool, scanner configuration, costs and exits are unchanged.

## Authority and environment

Retain the established hourly scanner input and aggregate those same frozen bytes into complete UTC daily strategy bars. Do not replace this environment with the public helper's daily-scanner default: that is a distinct, separately measured adapter and changes historical eligibility. Environment authority was chosen to preserve the historical contract, not by comparing returns.

- Strategy: crypto_low_variance_hysteresis_v1 v1.0.0; 22 daily simple returns, sample standard deviation, ascending rank; top two, held-name retention through rank four; epoch-aligned 96-hour decision blocks; equal raw weights; 5% sleeve cap.
- Mode: CRYPTO_STRATEGY_ISOLATED_CANONICAL; $100,000 cash, no positions/pending orders. Equity does not occupy slots. This is not TOTAL_ACCOUNT_SHARED_CANONICAL.
- Window: 2021-04-01 00:00 UTC through 2026-07-31 23:59:59 UTC; earlier rows are warmup. Same historical endpoint, no appended-future-data comparison.
- Input: data/historical/crypto_expanded_1h.csv, SHA256 `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`; 705,541 hourly rows, 28,890 complete daily bars including warmup. Daily labels denote bucket starts; decisions consume the completed day's data and fill on a later bar's open.
- Costs: 2bps slippage, 2bps full spread (half applied each side), 25bps crypto taker fee, zero fixed commission, 1x cost multiplier. Volume participation uses strictly order-known positive-volume history; exact resolved settings are in the receipt.
- Offline drawdown enforcement is shadow-only; daily-loss restrictions still apply. Final holdings are forcibly liquidated and costs charged; final NAV timestamp is unique.
- Original provider-download receipt for the expanded file and historical broker tradability metadata are unavailable. Authority is reproducibility on these pinned bytes under the documented bar-availability proxy, not proof of a complete survivorship-free provider dataset.
- Current hourly adapter supplies hourly scanner history to a daily observation ranker, which falls back to scanner ordering; this behavior is explicit and frozen. Do not claim it reproduces the runtime's separately warmed daily observation ranking. Changing this environment requires a separately authorized contract change.

## Identity and verification

| Quantity | Value |
| --- | --- |
| Target decisions | 455 |
| Fills | 388 |
| NAV observations | 1946 |
| Ending equity | $105,481.51853584 |
| Target SHA256 | `ab52ec2e09586462504dbe631dec51585aff18603c03254c0a5102197dc325d4` |
| Fill SHA256 | `c8bf27f1d21846bf754a53a72e5da29416c79dd817162606cecc6ce19fe1e07b` |
| Final-state SHA256 | `e09c993fd46b81bfdf9878feb0e13079bc84e18aabbaa1b0bf7e533f980c7964` |
| Normalized daily input SHA256 | `92f9b745eb925e9994c184b712ac500e82be973be38839b8bd93aab7a60d54f9` |
| Full deterministic payload SHA256 | `016bc06a7381d19434e6d21b4bafc40f1890abee57b7ead71a9b684ab40fb8dd` |

Two fresh runs are byte-identical after deterministic gzip (mtime=0) and canonical JSON serialization. Independent code imports no production scoring, strategy, risk, execution or accounting functions. It reconstructs every decision, rank, retention, vacancy, target, order, fill, cost, cash/position/NAV point and final state. Separate independent eligibility verification covers all 2,037 scanner snapshots. See the receipts for tolerances and scanner ordering scope.

## Why the old control is superseded

The archived source `0d792dcd4468971701517b06dd8c10d4710f2cf3` exactly reproduces all six historical hashes and the original universe hash on the same input bytes. Its 453 decisions/454 fills are valid evidence of that old implementation, not today's contract. Commit ac58f34 restricts strategy history to effective observations; the old engine ranked symbols outside the stream. On 2023-05-11 the old path selected BTC+SHIB while the current path selected BTC+ETH, followed by different fills on May12. This is an upstream visibility change, not six independent alpha/execution/cost/NAV bugs. Later terminal-bookkeeping corrections also make legacy curve/final-state serialization inappropriate as a current equality oracle.

Full evidence: [maintenance audit](../../maintenance/crypto_replay_parity_v1/FINAL_PARITY_REPORT.md), [receipt](../../maintenance/crypto_replay_parity_v1/CANONICAL_REPLAY_RECEIPT.json), [first divergence](../../maintenance/crypto_replay_parity_v1/FIRST_DIVERGENCE_AUDIT.json).

Reproduce from repository root using the existing pinned dataset:

```sh
.venv/bin/python maintenance/crypto_replay_parity_v1/runner.py --universe-timeframe 1h --out maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY.json.gz
.venv/bin/python maintenance/crypto_replay_parity_v1/runner.py --universe-timeframe 1h --out maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY_B.json.gz
```

Run both independent verifier CLIs and comparison.py as documented in FINAL_PARITY_REPORT.md before establish_baseline.py. The baseline gate refuses unequal repeated outputs, unmatched historical reproduction, or failed/mismatched verifier receipts.

Handoff: **CRYPTO_GIVEBACK_AUDIT_READY_FOR_RERUN**. The previous quarantined current-code path agrees with this baseline's output, but its profit-retention analysis must be rerun and independently verified in a separate task. No exit recommendation follows from this maintenance work.
