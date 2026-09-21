# Abnormal-volume standalone economic experiment V1

## Final standalone decision

Formal frozen economic decision: `RESEARCH_REJECT`.

## Durable classification

`ALPHA_REJECTED`

## Run validity

`RESEARCH_RUN_VALID`. The independent verifier consumed the persisted runner artifact, replayed canonical raw inputs, independently recomputed signal/timing/lifecycle/cost/ledger/metric/gate evidence, reported `PASS`, and found no issues. The candidate was run once; post-run changes were limited to verifier false-positive repairs and did not change the persisted runner output.

## Frozen identity

- Production main: `980c6b90b7f316bcec6a9466dfc9b0e1756cc748`
- Design V2: `a39cf6dfc72a4a4aa92d95b3f760626e2ce216c6`
- Readiness V2: `ec37b0f1671191f1842f5f952977bb60c772fa4b`
- Economic branch at the authorized run: `research/abnormal-volume-economic-v1` at `a228b3982cb2fb5e5c6dff54df4c40d467a27f07`
- Final research branch SHA: resolve `git rev-parse HEAD` at handoff; the immutable handoff SHA is reported outside this self-referential file.

## Candidate definition

`AV49_D1_NORMALRET_M1_LONG / DESIGN_V2`: split-consistent dollar volume, exactly 49 prior observations, frozen non-interpolated `x_(44)` Q0.90, one-day formation, inclusive top-decile volume rank plus middle-40% formation-return rank, highest abnormal-volume rank first, monthly final-close decision, next-month first-tradable-open execution, long-only top six, equal weights, 55% sleeve cap and 10% name cap.

## Data identity

Canonical bars `data/historical/equity_pit_symmetric_2016_2026_v5.csv` at `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`; lifetimes `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`; corporate actions `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`. The shared endpoint is 2026-07-30; 5,602 later rows were excluded. Development is 2017-01-03 through 2021-12-31; validation is 2022-01-03 through the endpoint; the account state is continuous.

## Fresh V3 control parity

Fresh canonical `equity_residual_momentum_v3` replay passed exact target, fill, terminal-event, chronology, reserved-role, input, production-identity, and metric parity checks: `V3_PARITY_PASS`.

| Cost | Return | CAGR | Vol | Sharpe | Sortino | MaxDD | Calmar | Costs | Reported turnover | Fills |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0x | 97.55% | 7.37% | 11.26% | 0.688 | 0.884 | 20.44% | 0.361 | $7,529.57 | 168.49x | 2054 |
| 2.0x | 87.53% | 6.79% | 11.26% | 0.640 | 0.822 | 21.33% | 0.318 | $14,638.83 | 168.39x | 2055 |
| 3.0x | 78.05% | 6.21% | 11.26% | 0.592 | 0.761 | 22.17% | 0.280 | $21,353.00 | 168.28x | 2052 |

Control reports its native cumulative turnover field; Candidate B reports frozen annualized one-way target and realized turnover. Those differently defined values are retained and are not silently compared as equal units.

## Candidate B — 1x

Full-period ending equity was $141,724.81 from $100,000. Full return 41.72%, CAGR 3.70%, Sharpe 0.402, Sortino 0.520, maximum drawdown 15.07%, and costs $3,351.82.

## Candidate B — 2x

Full return 37.59%, CAGR 3.38%, Sharpe 0.373, maximum drawdown 15.52%, and costs $6,603.72.

## Candidate B — 3x

Full return 33.56%, CAGR 3.07%, Sharpe 0.343, maximum drawdown 15.97%, and costs $9,758.57.

Full-period cross-cost metrics:

| Cost | Return | CAGR | Vol | Sharpe | Sortino | MaxDD | Calmar | Costs | Target TO | Realized TO | Fills | Avg exp. | Avg pos. |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0x | 41.72% | 3.70% | 10.36% | 0.402 | 0.520 | 15.07% | 0.246 | $3,351.82 | 4.97x | 9.94x | 1032 | 42.41% | 4.59 |
| 2.0x | 37.59% | 3.38% | 10.37% | 0.373 | 0.481 | 15.52% | 0.218 | $6,603.72 | 4.97x | 9.94x | 1032 | 42.43% | 4.59 |
| 3.0x | 33.56% | 3.07% | 10.37% | 0.343 | 0.443 | 15.97% | 0.192 | $9,758.57 | 4.97x | 9.94x | 1032 | 42.44% | 4.59 |

| Cost | Worst month | Positive months | Profit factor | Scheduled/actual | Median exp. | Max target conc. | Max realized conc. |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0x | -5.81% | 55.65% | 1.198 | 114/114 | 54.62% | 10.00% | 19.76% |
| 2.0x | -5.84% | 55.65% | 1.174 | 114/114 | 54.64% | 10.00% | 19.77% |
| 3.0x | -5.87% | 55.65% | 1.151 | 114/114 | 54.66% | 10.00% | 19.78% |

## Development

| Cost | Return | CAGR | Vol | Sharpe | Sortino | MaxDD | Calmar | Costs | Target TO | Realized TO | Fills | Avg exp. | Avg pos. |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0x | 17.42% | 3.26% | 10.79% | 0.351 | 0.441 | 14.62% | 0.223 | $1,571.76 | 4.69x | 9.38x | 509 | 40.03% | 4.32 |
| 2.0x | 15.73% | 2.96% | 10.79% | 0.324 | 0.407 | 14.88% | 0.199 | $3,121.30 | 4.69x | 9.38x | 509 | 40.04% | 4.32 |
| 3.0x | 14.06% | 2.66% | 10.79% | 0.297 | 0.373 | 15.13% | 0.176 | $4,648.92 | 4.69x | 9.38x | 509 | 40.05% | 4.32 |

| Cost | Worst month | Positive months | Profit factor | Scheduled/actual | Median exp. | Max target conc. | Max realized conc. |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0x | -5.47% | 56.67% | 1.193 | 60/60 | 54.59% | 10.00% | 19.76% |
| 2.0x | -5.49% | 56.67% | 1.168 | 60/60 | 54.61% | 10.00% | 19.77% |
| 3.0x | -5.52% | 56.67% | 1.143 | 60/60 | 54.63% | 10.00% | 19.78% |

At 1x, worst month was -5.47%, positive-month ratio 56.67%, profit factor 1.193, median exposure 54.59%, maximum target concentration 10.00%, and maximum realized concentration 19.76%. Scheduled/actual decisions were 60/60.

## Validation

| Cost | Return | CAGR | Vol | Sharpe | Sortino | MaxDD | Calmar | Costs | Target TO | Realized TO | Fills | Avg exp. | Avg pos. |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0x | 20.70% | 4.19% | 9.88% | 0.465 | 0.618 | 13.85% | 0.303 | $1,780.06 | 5.27x | 10.55x | 523 | 45.03% | 4.87 |
| 2.0x | 18.89% | 3.85% | 9.89% | 0.431 | 0.574 | 14.16% | 0.272 | $3,482.42 | 5.28x | 10.55x | 523 | 45.04% | 4.87 |
| 3.0x | 17.10% | 3.51% | 9.90% | 0.398 | 0.529 | 14.47% | 0.242 | $5,109.66 | 5.28x | 10.55x | 523 | 45.06% | 4.87 |

| Cost | Worst month | Positive months | Profit factor | Scheduled/actual | Median exp. | Max target conc. | Max realized conc. |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0x | -5.81% | 54.55% | 1.202 | 54/54 | 54.67% | 10.00% | 15.64% |
| 2.0x | -5.84% | 54.55% | 1.179 | 54/54 | 54.69% | 10.00% | 15.64% |
| 3.0x | -5.87% | 54.55% | 1.156 | 54/54 | 54.70% | 10.00% | 15.65% |

At 1x, worst month was -5.81%, positive-month ratio 54.55%, profit factor 1.202, median exposure 54.67%, maximum target concentration 10.00%, and maximum realized concentration 15.64%. Scheduled/actual decisions were 54/54.

## Validation calendar years

| Year | Scope | Return | CAGR | Sharpe | MaxDD | Costs |
|---:|:---|---:|---:|---:|---:|---:|
| 2022 | complete | -4.59% | -4.60% | -0.371 | 12.36% | $319.08 |
| 2023 | complete | -0.73% | -0.73% | -0.040 | 7.41% | $396.81 |
| 2024 | complete | 5.10% | 5.06% | 0.754 | 4.25% | $413.81 |
| 2025 | complete | 8.96% | 8.96% | 0.855 | 8.62% | $363.69 |
| 2026 | partial through 07-30 | 11.27% | 20.30% | 1.561 | 8.08% | $286.67 |

2026 is explicitly partial, not a complete calendar year.

## Abnormal-volume diagnostics

There were 115 scheduled monthly decisions and 115 valid PIT cross-sections. Average PIT pool was 287.50; average volume-eligible count before the middle-40 condition was 55.25; average final middle-40 conditioned count was 15.04. Selected-count distribution was `{'0': 8, '1': 1, '2': 4, '3': 4, '4': 5, '5': 4, '6': 89}`; 22.61% of decisions selected fewer than six. Full distributions, threshold equality, and duplicate-threshold diagnostics are in `SIGNAL_DIAGNOSTICS.json`; none became a post-hoc filter.

## Turnover / execution

Full 1x annualized target/realized one-way turnover was 4.967x/9.938x. The engine persisted 1032 orders and 1032 fills; target-notional completion was 99.99%, unfilled/partial notional was 0.01%, and no first-open residual expired into a later-session fallback. Seven lifecycle/final-bar exits were persisted. Average realized exposure was 42.41%; average cash was 57.58%. Unused allocation came from the frozen 55% sleeve, per-name cap with fewer selections, canonical risk-overlay halts, and ordinary between-rebalance drift.

## Concentration

The run traded 269 symbols/entities. Largest positive contributor was `LITE` ($9,915.66); largest negative was `GLW` ($-5,557.54). Largest absolute entity contribution share was 4.05%; maximum realized name concentration was 19.76%. The prespecified LOO removed `LITE` and remained positive full/validation, so LOO passed. G8 nevertheless failed because only 74.78% of decisions held at least four names versus 90% required.

## Risk-adjusted viability

G5 passed: full 1x Sharpe 0.402 >= 0.35, Sortino 0.520 >= 0.45, Calmar 0.246 >= 0.20, and maximum drawdown 15.07% <= 30%.

## Cost robustness

G6 passed: full and validation return/CAGR stayed positive at 2x, and full return/CAGR/Sharpe stayed positive at 3x. Target generation and chronology were unchanged across cost cells.

## V3 comparison — diagnostic only

Candidate B underperformed fresh V3 on full 1x return (41.72% vs 97.55%), CAGR (3.70% vs 7.37%), Sharpe (0.402 vs 0.688), Sortino (0.520 vs 0.884), and Calmar (0.246 vs 0.361). Candidate B had lower maximum drawdown (15.07% vs 20.44%), lower cost ($3,351.82 vs $7,529.57), and higher maximum realized concentration (19.76% vs 12.34%). V3 displacement is descriptive only and did not determine classification.

## Independent verification

Status `PASS` with `RESEARCH_RUN_VALID` and zero issues. Coverage independently reconstructed 595 targets including 128 boundary targets, checked all 115 scheduled decisions across three cost cells, reconciled ledger/cost/metric evidence, and agreed on every gate.

## Frozen G1–G9 table

| Gate | Result | Decisive observation |
|:---:|:---:|:---|
| G1 | PASS | All frozen clauses passed. |
| G2 | PASS | All frozen clauses passed. |
| G3 | PASS | All frozen clauses passed. |
| G4 | PASS | All frozen clauses passed. |
| G5 | PASS | All frozen clauses passed. |
| G6 | PASS | All frozen clauses passed. |
| G7 | PASS | All frozen clauses passed. |
| G8 | FAIL | 74.7826% of decisions had >=4 names; required >=90%. |
| G9 | FAIL | Realized gross peaked at 61.1449% and cash fell to 38.8551%; limits were 60% and 40%. |

## Final reasoning

The run is valid and shows positive standalone economics, risk-adjusted viability, cost robustness, and implementable turnover. Classification is nevertheless determined by all mandatory frozen gates. G8 fails its breadth clause: 74.78% is below 90%. G9 fails realized capital bounds: gross exposure reached 61.14%, above 60%, and cash reached 38.86%, below 40%. Near misses fail under the frozen protocol. Therefore the formal result is `RESEARCH_REJECT` and the durable classification is `ALPHA_REJECTED`.

## Future eligibility

Candidate is closed; no retuning. No portfolio-combination experiment was run or authorized here.

## Production state

No production promotion was performed. Main, PAPER strategy, LIVE state, V3 parameters, crypto, risk limits, allocation configuration, and the production strategy registry remain outside the research changes and are verified at handoff.

## Final validation

Economic runner/verifier tests: 18 passed. Focused month-end, quantile, lifecycle, cost, ledger, and verifier tests: 108 passed. Full sandbox suite: 1,132 passed and one skipped, with six localhost-binding tests blocked only by sandbox `PermissionError`; all six passed when rerun with loopback permission. Ruff, Ruff format, mypy on `src`, research-harness `py_compile`, JSON parsing, `git diff --check`, and production-isolation checks passed. No candidate rerun occurred during validation.

## Files

The compact decision package is in `research/abnormal_volume_economic_v1/`: frozen receipt, pre-run validation, runner/verifier and tests, fresh V3 control/parity, persisted runner output, compact Candidate B/cost/year/signal/turnover/concentration/gate artifacts, independent verification, and final decision.

## Final research branch SHA

The immutable final SHA is reported in the handoff after the commit is created. The authorized run itself was executed at `a228b3982cb2fb5e5c6dff54df4c40d467a27f07` and its persisted runner artifact SHA-256 is `365174fd0c805110bd18519149d095c19ef1414e0f11ef6d16fdbefda8a40629`.
