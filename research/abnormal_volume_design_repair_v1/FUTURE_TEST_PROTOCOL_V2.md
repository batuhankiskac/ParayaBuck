# Future test protocol V2

The next stage may implement and run exactly `AV49_D1_NORMALRET_M1_LONG / DESIGN_V2`; it may not redesign it. Control is the canonical V3 baseline on the same immutable artifact, chronology, point-in-time universe, capital/risk architecture, fill convention, and cost model. Report the long leg separately. No parameter-performance sweep is permitted.

## Chronology and execution

- Warmup: 2016-01-04 through 2016-12-30.
- Development: 2017-01-03 through 2021-12-31.
- Validation: 2022-01-03 through 2026-07-30.
- Full: 2017-01-03 through 2026-07-30.
- Signal after calendar-month final close; fill at the next month's first tradable open; hold to the next month-first-open.
- Apply the canonical adverse spread, slippage, regulatory fee, and partial-fill model at 1x, 2x, and 3x. Missing prices/fills fail closed; no favorable fill substitution.
- Use a dedicated research harness and the candidate-neutral `month_end_first_open` / `require_first_open_bar` contract available at main `980c6b90b7f316bcec6a9466dfc9b0e1756cc748`. The legacy `RebalancePolicy("monthly")` path is forbidden because it decides on the first monthly bar and fills a bar later. The ordinary CLI is forbidden because it does not pass v5 terminal lifecycles to the engine.
- Reconstruct `Q0.90` only as `x_(44)` under `j=1+floor(9*(n-1)/10)` for the 49 prior observations. Library-default quantiles are forbidden.

## Exact gates

`G1 — integrity`, must pass before economics:

- pinned hashes and row counts reconcile; no outcome-bearing archive is read;
- deterministic month-end/next-open/hold schedule tests pass, including holidays and validation-boundary continuity;
- the harness explicitly loads the v5 lifetime artifact; `EOSEW` and any unresolved lifecycle are ineligible; terminal liquidation behavior is tested;
- raw CSV prevalidation proves no blank/nonnumeric/nonfinite/negative volume before normal loading, and every zero-containing signal window is rejected;
- both arms stop at the common 2026-07-30 endpoint and never consume the partial 2026-07-31 cross-section;
- synthetic forward/reverse split transformations leave eligibility/order unchanged;
- every admitted input is available by the decision timestamp; every excluded row has a reason;
- the exact `x_(44)` quantile boundary, duplicate-value handling, score, and final tie-break reproduce independently on deterministic fixtures where other estimators disagree;
- reserved roles never become targets; control and candidate share all non-signal settings;
- an independent reviewer confirms no economic peek.

`G2 — full 1x`: net total return > 0 and CAGR > 0.

`G3 — development 1x`: net total return > 0 and CAGR > 0.

`G4 — validation 1x`: net total return > 0, CAGR > 0, Sharpe > 0, and maximum drawdown <= 30%.

`G5 — full risk`: Sharpe >= 0.35, Sortino >= 0.45, Calmar >= 0.20, and maximum drawdown <= 30%.

`G6 — cost stress`: full and validation net total return and CAGR remain > 0 at 2x; full net total return, CAGR, and Sharpe remain > 0 at 3x.

`G7 — implementation`: annualized one-way turnover <= 12.0; filled target notional >= 95%; unfilled/partial-fill target notional <= 5%; and 1x total costs <= 35% of positive gross profit. Nonpositive gross profit fails the gate.

`G8 — breadth/concentration`: median holdings >= 5; at least 90% of decisions hold >= 4 names; no name target exceeds 10%; no name contributes > 35% of absolute full-period gross P&L; removing the largest positive contributor leaves full and validation 1x net return and CAGR > 0. This is a prespecified robustness test, not a parameter choice.

`G9 — architecture parity`: equity allocation <= 0.55, gross <= 0.60, cash >= 0.40, account-wide positions <= 6, and the candidate receives no extra capital, slots, execution privilege, or altered risk exit relative to the control.

All gates must pass. Any failure yields `ALPHA_REJECTED` without tuning. All passing yields `ALPHA_VALID_COMBINATION_UNTESTED`, not PAPER/LIVE eligibility. Combination with V3 is a later, separately frozen stage; `5+1` is merely a future architecture hypothesis.

Required reports include gross/net metrics by chronology and cost, order/fill audit, occupancy, turnover, costs, exclusions, product-type counts, concentration, and side-by-side V3 diagnostics. Candidate-minus-V3 performance is descriptive, never a gate.
