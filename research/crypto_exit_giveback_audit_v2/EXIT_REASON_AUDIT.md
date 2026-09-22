# Exit reason audit

All 55 natural inventory closures are attributed to their actual final sell fill's source decision. This is separate from the decision that initiated the final uninterrupted zero-target liquidation sequence. If a zero-target instruction was later rescinded by a positive target, it is not treated as the initiator of a later closure. Partial reductions with a positive target are size rebalances, not completed exit decisions.

| Final source reason | Natural closures |
|---|---:|
| QUALIFIED_HISTORY_LOSS | 8 |
| RANK_ABOVE_RETENTION | 38 |
| SCANNER_INELIGIBLE | 5 |
| RETENTION_CAPACITY_DISPLACEMENT | 4 |

There are also two `TERMINAL_CENSORED` policy liquidations. They have no natural exit reason. Their source price/event date is 2026-07-30, while the final account snapshot/horizon is 2026-07-31.

`RANK_ABOVE_RETENTION` means rank >4; `QUALIFIED_HISTORY_LOSS` means scanner eligibility remains but the symbol is absent from the qualified score ranking; `SCANNER_INELIGIBLE` means it is absent from the scanner-eligible set. `RETENTION_CAPACITY_DISPLACEMENT` means the rank/eligibility retention predicate still passes, but the two-position retained selection cap excludes it. The four capacity cases have residual inventory alongside two other holdings; they must not be mislabeled as rank >4 exits.

## Four different initiating and final source decisions

| Episode | Initiating decision | Initiating reason | Final source decision | Final rank | Held symbols at final decision | Exit fill | Initiating to fill hours | Final source to fill hours |
|---|---|---|---|---:|---|---|---:|---:|
| LTC/USD:6 | 2024-08-05 | RETENTION_CAPACITY_DISPLACEMENT | 2024-08-09 | 3 | BTC/USD, DOT/USD, LTC/USD | 2024-08-11 | 144.00 | 48.00 |
| DOT/USD:1 | 2024-08-17 | SCANNER_INELIGIBLE | 2024-08-25 | None | BTC/USD, DOGE/USD, DOT/USD, LTC/USD | 2024-08-29 | 288.00 | 96.00 |
| LTC/USD:7 | 2024-10-08 | SCANNER_INELIGIBLE | 2024-10-20 | 3 | BTC/USD, ETH/USD, LTC/USD | 2024-10-23 | 360.00 | 72.00 |
| BCH/USD:4 | 2025-08-04 | RANK_ABOVE_RETENTION | 2025-08-08 | 8 | AAVE/USD, BCH/USD, BTC/USD | 2025-08-12 | 192.00 | 96.00 |

## All natural liquidation delays

The ledger uses the last uninterrupted zero-target run; the earlier first-ever zero target is separately retained in machine-readable diagnostics. “Peak to zero” can be negative when the final zero-target intent preceded the observed MFE while residual inventory remained. A fill delay reflects actual decision/execution separation, not an inferred holding-age lock. A 96-hour decision opportunity alone does not mandate exit.

| Episode | Peak date | Final-run zero target | Final fill source | Exit fill | Peak to zero hours | Zero to fill hours | Final source to fill hours |
|---|---|---|---|---|---:|---:|---:|
| BTC/USD:1 | 2021-04-13 | 2021-06-23 | 2021-06-23 | 2021-06-24 | 1704.00 | 24.00 | 24.00 |
| ETH/USD:1 | 2021-05-11 | 2021-06-23 | 2021-06-23 | 2021-06-24 | 1032.00 | 24.00 | 24.00 |
| LTC/USD:1 | 2021-08-23 | 2021-08-29 | 2021-08-29 | 2021-08-30 | 144.00 | 24.00 | 24.00 |
| ETH/USD:2 | 2021-09-05 | 2021-09-30 | 2021-09-30 | 2021-10-01 | 600.00 | 24.00 | 24.00 |
| LTC/USD:2 | 2021-10-20 | 2021-10-24 | 2021-10-24 | 2021-10-25 | 96.00 | 24.00 | 24.00 |
| AAVE/USD:1 | 2021-10-28 | 2021-12-11 | 2021-12-11 | 2021-12-12 | 1056.00 | 24.00 | 24.00 |
| DOGE/USD:1 | 2021-12-14 | 2021-12-15 | 2021-12-15 | 2021-12-16 | 24.00 | 24.00 | 24.00 |
| ETH/USD:3 | 2021-12-23 | 2022-01-28 | 2022-01-28 | 2022-01-29 | 864.00 | 24.00 | 24.00 |
| BTC/USD:2 | 2021-11-08 | 2022-03-09 | 2022-03-09 | 2022-03-10 | 2904.00 | 24.00 | 24.00 |
| BCH/USD:1 | 2022-03-22 | 2022-03-25 | 2022-03-25 | 2022-03-26 | 72.00 | 24.00 | 24.00 |
| DOGE/USD:2 | 2022-03-27 | 2022-04-02 | 2022-04-02 | 2022-04-03 | 144.00 | 24.00 | 24.00 |
| ETH/USD:4 | 2022-04-04 | 2022-07-07 | 2022-07-07 | 2022-07-08 | 2256.00 | 24.00 | 24.00 |
| GRT/USD:1 | 2022-07-09 | 2022-07-11 | 2022-07-11 | 2022-07-12 | 48.00 | 24.00 | 24.00 |
| LINK/USD:1 | 2022-08-12 | 2022-09-01 | 2022-09-01 | 2022-09-02 | 480.00 | 24.00 | 24.00 |
| LTC/USD:3 | 2022-09-09 | 2022-09-09 | 2022-09-09 | 2022-09-10 | 0.00 | 24.00 | 24.00 |
| DOGE/USD:3 | 2022-10-04 | 2022-10-07 | 2022-10-07 | 2022-10-08 | 72.00 | 24.00 | 24.00 |
| LTC/USD:4 | 2022-11-23 | 2022-11-24 | 2022-11-24 | 2022-11-25 | 24.00 | 24.00 | 24.00 |
| ETH/USD:5 | 2023-02-07 | 2023-02-08 | 2023-02-08 | 2023-02-09 | 24.00 | 24.00 | 24.00 |
| ETH/USD:6 | 2023-04-16 | 2023-04-17 | 2023-04-17 | 2023-04-18 | 24.00 | 24.00 | 24.00 |
| BCH/USD:2 | 2023-04-18 | 2023-05-11 | 2023-05-11 | 2023-05-12 | 552.00 | 24.00 | 24.00 |
| BTC/USD:3 | 2022-04-04 | 2023-06-04 | 2023-06-04 | 2023-06-05 | 10224.00 | 24.00 | 24.00 |
| UNI/USD:1 | 2023-06-06 | 2023-06-16 | 2023-06-16 | 2023-06-17 | 240.00 | 24.00 | 24.00 |
| ETH/USD:7 | 2024-02-19 | 2024-02-19 | 2024-02-19 | 2024-02-20 | 0.00 | 24.00 | 24.00 |
| LTC/USD:5 | 2024-03-11 | 2024-03-22 | 2024-03-22 | 2024-03-24 | 264.00 | 48.00 | 48.00 |
| ETH/USD:8 | 2024-03-31 | 2024-04-07 | 2024-04-07 | 2024-04-08 | 168.00 | 24.00 | 24.00 |
| XRP/USD:1 | 2024-04-10 | 2024-05-13 | 2024-05-13 | 2024-05-14 | 792.00 | 24.00 | 24.00 |
| ETH/USD:9 | 2024-05-21 | 2024-05-21 | 2024-05-21 | 2024-05-22 | 0.00 | 24.00 | 24.00 |
| BTC/USD:4 | 2024-03-13 | 2024-06-22 | 2024-06-22 | 2024-06-23 | 2424.00 | 24.00 | 24.00 |
| ETH/USD:10 | 2024-06-21 | 2024-06-22 | 2024-06-22 | 2024-06-23 | 24.00 | 24.00 | 24.00 |
| LTC/USD:6 | 2024-05-22 | 2024-08-05 | 2024-08-09 | 2024-08-11 | 1800.00 | 144.00 | 48.00 |
| DOT/USD:1 | 2024-06-05 | 2024-08-17 | 2024-08-25 | 2024-08-29 | 1752.00 | 288.00 | 96.00 |
| DOGE/USD:4 | 2024-08-23 | 2024-08-29 | 2024-08-29 | 2024-09-01 | 144.00 | 72.00 | 72.00 |
| XRP/USD:2 | 2024-09-21 | 2024-09-26 | 2024-09-26 | 2024-09-27 | 120.00 | 24.00 | 24.00 |
| BCH/USD:3 | 2024-10-14 | 2024-10-16 | 2024-10-16 | 2024-10-18 | 48.00 | 48.00 | 48.00 |
| LTC/USD:7 | 2024-10-19 | 2024-10-08 | 2024-10-20 | 2024-10-23 | -264.00 | 360.00 | 72.00 |
| ETH/USD:11 | 2024-12-06 | 2025-04-10 | 2025-04-10 | 2025-04-11 | 3000.00 | 24.00 | 24.00 |
| LTC/USD:8 | 2025-05-10 | 2025-05-20 | 2025-05-20 | 2025-05-24 | 240.00 | 96.00 | 96.00 |
| BTC/USD:5 | 2025-05-22 | 2025-06-29 | 2025-06-29 | 2025-06-30 | 912.00 | 24.00 | 24.00 |
| XRP/USD:3 | 2025-07-17 | 2025-07-19 | 2025-07-19 | 2025-07-20 | 48.00 | 24.00 | 24.00 |
| LTC/USD:9 | 2025-07-20 | 2025-07-19 | 2025-07-19 | 2025-07-22 | -24.00 | 72.00 | 72.00 |
| SOL/USD:1 | 2025-07-22 | 2025-07-23 | 2025-07-23 | 2025-07-24 | 24.00 | 24.00 | 24.00 |
| BCH/USD:4 | 2025-07-27 | 2025-08-04 | 2025-08-08 | 2025-08-12 | 192.00 | 192.00 | 96.00 |
| AAVE/USD:2 | 2025-08-13 | 2025-08-12 | 2025-08-12 | 2025-08-16 | -24.00 | 96.00 | 96.00 |
| ETH/USD:12 | 2025-08-13 | 2025-08-20 | 2025-08-20 | 2025-08-21 | 168.00 | 24.00 | 24.00 |
| XRP/USD:4 | 2025-09-17 | 2025-09-25 | 2025-09-25 | 2025-09-26 | 192.00 | 24.00 | 24.00 |
| ETH/USD:13 | 2025-12-10 | 2025-12-14 | 2025-12-14 | 2025-12-15 | 96.00 | 24.00 | 24.00 |
| LTC/USD:10 | 2026-01-06 | 2026-01-19 | 2026-01-19 | 2026-01-23 | 312.00 | 96.00 | 96.00 |
| BCH/USD:5 | 2025-10-03 | 2026-02-08 | 2026-02-08 | 2026-02-09 | 3072.00 | 24.00 | 24.00 |
| LTC/USD:11 | 2026-02-14 | 2026-02-20 | 2026-02-20 | 2026-02-21 | 144.00 | 24.00 | 24.00 |
| BTC/USD:6 | 2025-10-06 | 2026-03-13 | 2026-03-13 | 2026-03-14 | 3792.00 | 24.00 | 24.00 |
| XRP/USD:5 | 2026-03-04 | 2026-03-13 | 2026-03-13 | 2026-03-14 | 216.00 | 24.00 | 24.00 |
| LTC/USD:12 | 2026-05-10 | 2026-05-31 | 2026-05-31 | 2026-06-04 | 504.00 | 96.00 | 96.00 |
| AVAX/USD:1 | 2026-02-25 | 2026-06-04 | 2026-06-04 | 2026-06-07 | 2376.00 | 72.00 | 72.00 |
| ETH/USD:14 | 2026-06-07 | 2026-06-08 | 2026-06-08 | 2026-06-09 | 24.00 | 24.00 | 24.00 |
| LTC/USD:13 | 2026-06-15 | 2026-06-20 | 2026-06-20 | 2026-06-24 | 120.00 | 96.00 | 96.00 |

Attribution describes the implemented trade lifecycle. It does not establish that a different exit would have retained the observed peak profit, or that hysteresis caused any particular amount of giveback.

## Economics by verified final reason

MFE/final are normalized returns; giveback is USD. Retention/giveback medians condition on positive MFE; other medians use all category episodes. Censored rows are not strategy exits.

| Reason | N | Median MFE | Median final | Median giveback USD | Median retention | Median peak-to-exit days |
|---|---:|---:|---:|---:|---:|---:|
| QUALIFIED_HISTORY_LOSS | 8 | 21.33% | 6.96% | 384.09 | 24.73% | 41.50 |
| RANK_ABOVE_RETENTION | 38 | 12.66% | 5.93% | 236.69 | 47.52% | 7.50 |
| RETENTION_CAPACITY_DISPLACEMENT | 4 | -0.17% | -7.09% | 40.98 | -940.61% | 7.50 |
| SCANNER_INELIGIBLE | 5 | 3.69% | -10.91% | 504.45 | -101.39% | 25.00 |
| TERMINAL_CENSORED | 2 | 9.20% | -9.60% | 683.50 | -35.90% | 60.50 |
