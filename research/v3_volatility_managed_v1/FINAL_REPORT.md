# equity_v3_volatility_managed_v1 research report

## Decision

**RESEARCH_REJECT**

This is a research-only result. It does not change the configured strategy,
PAPER/LIVE behavior, promotion state, slots, instruments, or risk limits.

## Frozen identity and literature

- Candidate: `equity_v3_volatility_managed_v1`
- Base main SHA: `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`
- Research branch: `research/v3-volatility-managed-v1`
- Protocol SHA-256: `dbe944d3cbf0d9dd78aa256e1b280e13a96bcd4f276c5c3550a52ef24577f0cd`
- Hypothesis: a completed-bar SPY VOL21 scaler can reduce drawdown and improve
  risk-adjusted outcomes without changing V3 selection, ranks, or instruments.
- Mechanism references: [Moreira and Muir](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513),
  [Andersen, Bollerslev, Diebold and Labys](https://onlinelibrary.wiley.com/doi/pdf/10.1111/1468-0262.00418),
  [Daniel and Moskowitz](https://www.nber.org/papers/w20439).

## Data and PIT receipt

- Bars: `/Users/batuhankiskac/Desktop/ParayaBuck/data/historical/equity_pit_symmetric_2016_2026_v5.csv`; SHA-256 `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`;
  split-adjusted daily SIP package, lexical `EQUITY`/`1Day` normalization only.
- Lifecycles: `/Users/batuhankiskac/Desktop/ParayaBuck/data/historical/equity_entity_lifetimes_full_v5.csv`; SHA-256 `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`.
- Corporate actions: `/Users/batuhankiskac/Desktop/ParayaBuck/data/historical/equity_survivorship_full_corporate_actions_v5.csv`; SHA-256
  `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`; cash dividends only, splits
  and stock actions ignored because prices are split-adjusted.
- Windows: development `2017-01-03` to `2021-12-31`; validation `2022-01-03`
  to `2026-07-31`; warmup begins with all available prior bars.
- SPY volatility observations: 2637;
  scaler mean `0.8549`, median
  `1.0000`, scaled fraction
  `46.39%`.

## A/B/C results

| Arm | Period | Net return | CAGR | Max drawdown | Sharpe | Sortino | Calmar | Avg equity exposure |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A_control | development | 55.89% | 9.30% | 15.54% | 0.839 | 1.016 | 0.598 | 49.25% |
| B_treatment | development | 43.54% | 7.50% | 13.68% | 0.842 | 1.018 | 0.549 | 41.91% |
| C_static_comparator | development | 47.29% | 8.06% | 13.08% | 0.842 | 1.021 | 0.616 | 42.63% |
| A_control | validation | 24.83% | 4.96% | 16.72% | 0.508 | 0.702 | 0.297 | 51.05% |
| B_treatment | validation | 20.14% | 4.09% | 14.04% | 0.483 | 0.665 | 0.291 | 45.06% |
| C_static_comparator | validation | 20.34% | 4.12% | 14.44% | 0.484 | 0.673 | 0.286 | 44.32% |
| A_control | full | 94.60% | 7.20% | 20.13% | 0.686 | 0.882 | 0.358 | 50.11% |
| B_treatment | full | 72.44% | 5.86% | 19.33% | 0.670 | 0.862 | 0.303 | 43.41% |
| C_static_comparator | full | 77.25% | 6.16% | 17.47% | 0.676 | 0.872 | 0.353 | 43.44% |

## Cost stress

| Arm | Costs | Net return | Max drawdown | Sharpe | Sortino | Calmar | Transaction costs |
|---|---:|---:|---:|---:|---:|---:|---:|
| A_control | 1x | 94.60% | 20.13% | 0.686 | 0.882 | 0.358 | 7357.73 |
| B_treatment | 1x | 72.44% | 19.33% | 0.670 | 0.862 | 0.303 | 6287.53 |
| C_static_comparator | 1x | 77.25% | 17.47% | 0.676 | 0.872 | 0.353 | 6304.71 |
| A_control | 2x | 84.83% | 21.04% | 0.637 | 0.819 | 0.315 | 14310.26 |
| B_treatment | 2x | 64.69% | 20.12% | 0.617 | 0.794 | 0.266 | 12270.63 |
| C_static_comparator | 2x | 69.29% | 18.30% | 0.625 | 0.807 | 0.309 | 12303.68 |
| A_control | 3x | 75.60% | 21.91% | 0.589 | 0.757 | 0.276 | 20882.52 |
| B_treatment | 3x | 57.26% | 20.91% | 0.564 | 0.726 | 0.232 | 17962.59 |
| C_static_comparator | 3x | 61.69% | 19.12% | 0.575 | 0.742 | 0.269 | 18012.83 |

## Coverage and diagnostics

| Arm | Development coverage | Validation coverage | Longest unexplained gap | Pass |
|---|---:|---:|---:|---:|
| A_control | 0.981 | 1.000 | 5 | PASS |
| B_treatment | 0.981 | 1.000 | 5 | PASS |
| C_static_comparator | 0.981 | 1.000 | 5 | PASS |

- Drawdown episodes: `drawdown_diagnostics.json`.
- Exposure, up/down capture, and reduced-exposure timing: `exposure_diagnostics.json`.
- Full scaler series and statuses: `scaler_time_series.json` and `scaler_audit.json`.
- Static comparator freeze: `STATIC_COMPARATOR_FREEZE.json`.

## Mandatory gates

| Gate | Result |
|---|---|
| gate_1 | PASS |
| gate_2 | FAIL |
| gate_3 | FAIL |
| gate_4 | FAIL |
| gate_5 | FAIL |
| gate_6 | FAIL |
| gate_7 | FAIL |
| gate_8 | PASS |

## Independent verification

- Research run validity: **VALID**.
- Structural replay: **VALID**.
- The verifier independently checked repository/data/protocol hashes,
  completed-bar VOL21/no-lookahead behavior, V3 identity and selection parity,
  next-bar execution ordering, lifecycle/NAV accounting, caps, coverage, and
  gate inputs. Details are in `independent_verification.json`.

## Final interpretation

The verdict is determined only after validity checks and all eight frozen gates.
The candidate remains research-only and is not eligible for automatic or
implicit PAPER/LIVE promotion. The exact decision payload is in `decision.json`.
