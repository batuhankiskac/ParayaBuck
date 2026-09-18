# Corrected V3 Baseline Protocol

Status: frozen before the corrected replay's economic outputs were inspected.

## Scope and identity

- Production code SHA: `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53`.
- Production branch at freeze: `main`; replay branch: `research/v3-reserved-role-corrected-baseline-v1`.
- Production strategy: `equity_residual_momentum_v3`, resolved version `3.0.0`.
- Configuration source: `config/settings.yaml`, with no parameter overrides.
- The current production PAPER/LIVE boundaries remain unchanged: `runtime.mode=paper`, `live.enabled=false`, and no live authorization is created.
- The role boundary is configuration-derived: `SPY` is reference-only and `SH`, `PSQ`, and `DOG` are approved hedge-only symbols. They remain observable and available to explicitly authorized hedge paths, but have zero ordinary V3 target eligibility.

The frozen V3 parameters are formation `252`, skip `21`, beta window `273`, residual-volatility window `63`, top `8`, maximum single weight `0.10`, idiosyncratic-volatility rejection quantile `0.70`, positive 12-1 residual gate, raw 6/9/12 rank consensus, inverse-volatility sizing, and weekly decision cadence. The allocation is `0.55`.

Risk and execution are the current production settings: maximum six positions, 10% per-position cap, 60% maximum gross exposure, 40% cash buffer, 5% drawdown limit, 0.5% daily loss limit, 0.5% rebalance band, $10 minimum order notional, next-open fills, configured participation limits, and effective-dated equity regulatory fees. No V3 parameter, risk limit, allocation, cadence, fill, or cost-model change is permitted in this baseline.

## Data contract

The replay consumes the existing canonical files; it does not rebuild or download data:

- Bars: `data/historical/equity_pit_symmetric_2016_2026_v5.csv`.
- Expected bars SHA-256: `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`.
- Expected bars manifest: 11,686,832 rows, 9,010 symbols/entities, 6,858 active and 2,152 inactive histories, SIP feed, split-adjusted prices.
- Entity-lifetime table: `data/historical/equity_entity_lifetimes_full_v5.csv`.
- Expected lifetime-table SHA-256: `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`.
- Corporate actions: `data/historical/equity_survivorship_full_corporate_actions_v5.csv`.
- Expected corporate-action SHA-256: `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`.

The bar dataset's broad manifest contains some symbols through 2026-07-31, but the common production reference/hedge endpoint is 2026-07-30: `SPY`, `SH`, `PSQ`, and `DOG` all have their final available bar at `2026-07-30T04:00:00+00:00`. The replay therefore uses an inclusive calendar end bound of 2026-07-30 and records the actual final curve timestamp. Extending to 2026-07-31 would leave the reference and hedge role series without a common bar.

Warmup is the available 2016 history. Economic evaluation starts 2017-01-03. Development is 2017-01-03 through 2021-12-31; validation is 2022-01-03 through the common endpoint. The full result is one continuous replay from the development start through the common endpoint, with development and validation metrics sliced from that same state path; no re-optimization or parameter reset is allowed.

The lifetime table is consumed as point-in-time identity evidence. Every inactive entity's `last_observed_bar` and `terminal_event_class` are passed to the engine as a `TerminalLifecycle`; active entities have no synthetic terminal event. The replay must verify unique canonical symbols, active/inactive counts, final-bar equality to the lifetime table, and terminal-bar processing for any managed inactive entity. A terminal event closes a held position only on its real final bar. No future lifecycle label may enter a decision before that bar.

Corporate-action input is the existing all-symbol action file. Only its `cash_dividends` rows are applied as explicit dividend cash flows because the bars are already split-adjusted; split/stock-dividend rows are not re-applied. The action-file hash and row/action counts are verified before replay.

## Frozen gates

The gates are evaluated only after structural comparison and independent verification. The final decision is exactly one of `CORRECTED_V3_BASELINE_VALID`, `CORRECTED_V3_BASELINE_FAILED`, or `CORRECTED_V3_BASELINE_INCONCLUSIVE`.

### G1 — validity and parity

Pass requires the three expected hashes, lifecycle counts and semantics, common endpoint, canonical V3/config identity, no reserved-role ordinary targets, no lookahead violations, valid next-open execution, and an independent verifier that agrees on targets, coverage, lifecycle handling, costs, NAV/cash, and role diagnostics. Any missing or unverifiable input is a failure of validity, not an economic pass.

### G2 — full 1x economics

At 1x current production costs, full-period net total return and CAGR must both be strictly greater than zero.

### G3 — validation 1x economics

At 1x current production costs, validation net total return and CAGR must both be strictly greater than zero.

### G4 — full 1x risk-adjusted economics

At 1x, full-period Sharpe, Sortino, and Calmar must each be strictly greater than zero.

### G5 — drawdown discipline

Full-period 1x maximum drawdown must be no greater than `5.0%`, the current production `risk.max_drawdown_pct`. This is a predeclared production-policy threshold, not a threshold fitted to the corrected result. Offline replay records drawdown-halt shadow events and does not silently convert them into a different alpha rule.

### G6 — cost robustness

At 2x costs, full-period net total return must be strictly greater than zero. At 3x costs, “no catastrophic collapse” is frozen as both full-period net total return greater than `-25.0%` and maximum drawdown less than `50.0%`. These are fixed stress thresholds, not outcome-tuned promotion criteria.

### G7 — portfolio and role controls

Every corrected target must contain at most six positions, each at or below 10%, with cash at or above 40% and gross exposure at or below 60%. The exact ordinary-target count for each of `SPY`, `SH`, `PSQ`, and `DOG` must be zero. The same symbols must remain present when required by the observation/pinned role path, and an explicitly authorized empty role boundary must still permit a hedge-path strategy to opt in.

## Reported outputs

For 1x, 2x, and 3x, report development, validation, and full periods: net total return, CAGR, annualized volatility, Sharpe, Sortino, maximum drawdown, Calmar, turnover, costs, fills, average exposure, average positions, and concentration. Report the exact cost decomposition, decision-fill count, terminal liquidations, rebalance count, and shadow drawdown events alongside the requested metrics.

Before reading those economic metrics, persist an old-vs-corrected structural comparison from the same data, universe, prepared inputs, lifecycle map, and execution path. It must include decision count, candidate-pool/observation/target role context, decisions with role targets, target changes, affected roles, removed reserved targets, ordinary replacements, reserved target weights, and evidence that the remaining ordinary raw scores/order are unchanged. The pre-fix receipt and focused tests are part of that proof.

No failed or inconclusive baseline may restore a reserved symbol to ordinary V3 alpha targeting for performance. No research result may alter `main`, PAPER, LIVE, the frozen V3 parameters, or the broad current US_EQUITY universe.
