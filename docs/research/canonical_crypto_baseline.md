# Canonical crypto replay baseline

## Current authority: causal execution repair, 2026-09-23

Decision: **CRYPTO_CANONICAL_BASELINE_CAUSAL_REPAIR_ACCEPTED**. The v2 control below is **CRYPTO_CANONICAL_BASELINE_V2_SUPERSEDED_BY_CAUSAL_EXECUTION_REPAIR**. Its same-boundary 2021-04-02 00:00 UTC BTC/ETH opening fills could not follow a decision using the complete 2021-04-01 daily bar. Its record is retained below and under `archive/maintenance/crypto-replay-parity-v1-2026-09` as superseded evidence.

The authoritative isolated control is `maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz`; B is byte-identical. It uses the same pinned hourly input (SHA256 `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`), complete daily bars (SHA256 `92f9b745eb925e9994c184b712ac500e82be973be38839b8bd93aab7a60d54f9`), universe (SHA256 `e3692166c51a1e84aba7662e3c6156cdc1961989df8f46564caff70d898529c6`), 22-day signal, top two and rank-four retention, 96-hour label cadence, 5% sleeve, 0.005 rebalance band, 20-positive-completed-daily-volume mean, 10% participation, 2bps slippage, 2bps full spread, 25bps crypto taker fee and zero fixed commission. No strategy or production configuration changed.

Each start-labeled hour is complete no earlier than its end. A complete 24-hour UTC daily bar is available at next midnight. The physical decision and order creation are at that availability boundary; a new order uses the first same-symbol pinned hourly **open strictly after** order creation and that day's midnight, with at most one participation-limited attempt per UTC day. Missing hours wait for the next actually observed eligible hour. The source-common precommitted terminal hour is `2026-07-31T00:00:00Z`; terminal positions close at that actual hourly open, and no later daily signal is consumed. See `maintenance/crypto_canonical_causal_execution_v1/FROZEN_CRYPTO_EXECUTION_CONTRACT.md` and its pre-outcome freeze commit `2b483b1e5022a23a0c34cc3ab15145da342c89d6`.

| Quantity | Corrected authority |
| --- | --- |
| Decisions / orders / fills | 454 / 206 / 406 |
| NAV observations / terminal events | 1,945 / 2 |
| Ending NAV | $105,423.13343608 |
| Total return / CAGR | 5.42313344% / 0.99548483% |
| Annualized volatility / Sharpe / Sortino | 3.00349987% / 0.34481718 / 0.48295649 |
| Max drawdown / Calmar / turnover | 6.23570332% / 0.15964275 / 3.52352964 |
| Fee / slippage / spread cost | $919.65403033 / $73.57270962 / $36.78635481 |
| Decisions SHA256 | `e2919b031944c1a08a8fbecc4019007c21c34218840d3c1ffa10feb420f7f58a` |
| Fills SHA256 | `645d444ff3c95ba2f5eab1f0162acc903275e4642c70c79eb03b8248829e1fba` |
| Snapshots / equity curve SHA256 | `6a71d86a6f6ac35d18ebf5a47de615cffe4eb45e43c03b8b69d3a389e3087c13` / `e2a89843121f43fe3fe642dc1c3140761efccca246e0bef6e900322908a069c3` |
| Final state / terminal events SHA256 | `7689e62a2e80a0452e42f7359c9722d49e25b21cfe38632901bb29bd953ad5ef` / `7ad9de1ae9bc69eca5794436e0e45868066a49960de3d8d9d86183192d51fba` |
| Full JSON / deterministic gzip SHA256 | `cdc3004dbbfe1e13e8f9f9deb5ec80ecbe628236210649a68a8f47aa55f33697` / `8ec31d0aa7fd350bf011c5a00ac25cd64fb9be20240a001496bb6e8353792cae` |

The independent CSV and ledger verifier checks source components, decision availability, first eligible hourly opens, every fill's strict causal inequality, quantity/participation, fees, cash, positions, NAV and terminal liquidation. The two outputs are byte-identical. The strategy formula and frozen parameters are unchanged; execution-dependent holdings alter six common alpha target outputs and three later observation/rank contexts. `SIGNAL_SEMANTICS_CHANGED` describes these realized outputs, not a change to the alpha algorithm. This remains a historical fill proxy on pinned hourly data, with the original provider-download and historical tradability limitations stated below. PAPER/LIVE behavior was not modified. Downstream shared-account and crypto exit economics require fresh separate runs.

## Superseded v2 record

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

Full evidence: [maintenance audit](https://github.com/batuhankiskac/ParayaBuck/blob/4b7eb4020896453a599c876092ff550216183d07/maintenance/crypto_replay_parity_v1/FINAL_PARITY_REPORT.md), [receipt](https://github.com/batuhankiskac/ParayaBuck/blob/4b7eb4020896453a599c876092ff550216183d07/maintenance/crypto_replay_parity_v1/CANONICAL_REPLAY_RECEIPT.json), [first divergence](https://github.com/batuhankiskac/ParayaBuck/blob/4b7eb4020896453a599c876092ff550216183d07/maintenance/crypto_replay_parity_v1/FIRST_DIVERGENCE_AUDIT.json).

Maintenance code and large replay artifacts are preserved by the annotated tag
`archive/maintenance/crypto-replay-parity-v1-2026-09`, pointing to
`4b7eb4020896453a599c876092ff550216183d07`. They are intentionally absent
from production `main`. Fetch the tag and extract its maintenance subtree into
an isolated checkout with access to the existing pinned dataset before using
the commands below. All evidence links above are pinned to the immutable commit.

Reproduce from that checkout root:

```sh
.venv/bin/python maintenance/crypto_replay_parity_v1/runner.py --universe-timeframe 1h --out maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY.json.gz
.venv/bin/python maintenance/crypto_replay_parity_v1/runner.py --universe-timeframe 1h --out maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY_B.json.gz
```

Run both independent verifier CLIs and comparison.py as documented in FINAL_PARITY_REPORT.md before establish_baseline.py. The baseline gate refuses unequal repeated outputs, unmatched historical reproduction, or failed/mismatched verifier receipts.

Handoff: **CRYPTO_GIVEBACK_AUDIT_READY_FOR_RERUN**. The previous quarantined current-code path agrees with this baseline's output, but its profit-retention analysis must be rerun and independently verified in a separate task. No exit recommendation follows from this maintenance work.
