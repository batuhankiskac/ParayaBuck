# Superseded versus causal crypto control

The frozen v2 authority is the archived `CURRENT_HOURLY_REPLAY.json.gz` under `archive/maintenance/crypto-replay-parity-v1-2026-09`. Its daily input and PIT universe hashes match the causal replay exactly. It is retained as a superseded control, not an acceptance target for prices or NAV.

| Metric | Old superseded | Corrected |
| --- | ---: | ---: |
| Decisions | 455 | 454 |
| Fills | 388 | 406 |
| NAV points | 1,946 | 1,945 |
| Total return | 5.48151854% | 5.42313344% |
| CAGR | 1.00545423% | 0.99548483% |
| Annualized volatility | not in archived metric payload | 3.00349987% |
| Sharpe | 0.34693442 | 0.34481718 |
| Sortino | not in archived metric payload | 0.48295649 |
| Max drawdown | 6.19186948% | 6.23570332% |
| Calmar | not in archived metric payload | 0.15964275 |
| Ending NAV | $105,481.51853584 | $105,423.13343608 |
| Turnover | not in archived metric payload | 3.52352964 |
| Fee | $899.97243833 | $919.65403033 |
| Slippage | $71.99818444 | $73.57270962 |
| Spread | $35.99909222 | $36.78635481 |

On the 2021-04-01 signal, the daily signal is available at 2021-04-02 00:00 UTC. The old BTC/ETH orders are timestamped one day too early and receive the Apr 2 00:00 daily opening prices ($58,714.99/$1,968.40). Corrected physical order creation is Apr 2 00:00; first admissible source hourly opens are Apr 2 01:00 ($58,625.59/$1,958.13). Fills include the unchanged adverse 3bps execution adjustment and taker fee. The exact orders, fills and source events are in `FIRST_EXECUTION_DIVERGENCE_AUDIT.json`.

The 22-day score formula, rank-four hysteresis, input, universe snapshots, 96-hour label cadence, risk and cost parameters were unchanged. All 454 common decision labels match. Raw rank lists match at 451; first difference at 2024-09-26 arises because execution-dependent managed position pins alter the observed context. Alpha target weights match at 448; first difference on 2024-08-29 is an execution-dependent retention choice (old LTC/BTC, corrected LTC/DOT). The risk-adjusted target matches at 446. Therefore realized signal outputs are classified `SIGNAL_SEMANTICS_CHANGED`, while the alpha algorithm itself is unchanged. The terminal source-common boundary eliminates the last old decision. The comparison is an audit of the mechanical effects of a precommitted timing rule, not a performance-based rule selection.

Corrected full payload SHA256: `cdc3004dbbfe1e13e8f9f9deb5ec80ecbe628236210649a68a8f47aa55f33697`; deterministic gzip SHA256: `8ec31d0aa7fd350bf011c5a00ac25cd64fb9be20240a001496bb6e8353792cae`. All section hashes and independent ledger checks are recorded in `INDEPENDENT_CAUSALITY_VERIFICATION.json`.
