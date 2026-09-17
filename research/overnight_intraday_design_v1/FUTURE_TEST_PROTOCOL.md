# Future economic test protocol

This document freezes a later experiment. It is not an instruction to run the
experiment during this design task. No economic result files belong in this
directory.

## Alternatives and controls

Run two separate alternative account configurations, never a combined sleeve:

* **Control A:** the current configured `equity_residual_momentum_v3`, with its
  existing 252/21/273/63 bars, 6/9/12 consensus, IVOL filter, `top_n: 8`,
  inverse-volatility sizing, weekly cadence, and all current production risk
  settings.
* **Candidate B:** the exact `equity_overnight_reversal_v1` specification in
  `CANDIDATE_DESIGN.md`.

Replace only the equity strategy identity and its declared parameters. Keep the
same historical package, normalized bar view, PIT lifecycle join, scanner
filters, SPY reference, target-shortlist rule, observation-capacity rule,
account cash, equity allocation ceiling `0.55`, global six-position limit,
position cap `0.10`, terminal lifecycle handling, order policy, execution model,
and equity costs. If the full product runner includes the existing crypto
configuration, replay that configuration identically in both alternatives; do
not give B an additional sleeve or any extra account capital.

The scanner may use each strategy's exact ranker to choose the maximum-30
observation set from the same eligible PIT pool, matching current live parity.
It may not broaden the pool, change sector or liquidity gates, or use future
bar volume. Record eligible-pool membership separately from the streamed
observation set.

## Frozen chronological windows

Use the local common-period endpoint dictated by SPY coverage:

| Window | Dates | Purpose |
| --- | --- | --- |
| Warmup | 2016-01-04 through 2016-12-30 | Build only the trailing histories and scanner state required before development; do not score as a test period. |
| Development | 2017-01-03 through 2021-12-31 | First chronological evaluation period; no parameter search. |
| Validation | 2022-01-03 through 2026-07-30 | Locked later-period evaluation; no changes after inspecting it. |

The split is fixed before outcomes and follows the requested 2017–2021 /
2022–2026 separation. The final local 2026-07-31 date lacks a SPY bar and is
therefore not a valid common-date decision for this candidate. If the dataset is
extended later, the extension must be a new frozen data snapshot and not a
silent append.

## Data and point-in-time protocol

Before creating any target:

1. Normalize `EQUITY`/`equity` to `equity` and `1Day`/`1d` to `1d`.
2. Parse timestamps as timezone-aware and derive the session date in
   `America/New_York`.
3. Fail closed on any canonical duplicate or OHLCV conflict.
4. Join each symbol to the immediately preceding common SPY/NYSE session and
   require both lifecycle-valid bars.
5. Apply the existing current-date PIT scanner and lifecycle rules; never use
   current status as a historical eligibility field.
6. Use the split-adjusted price series without applying split actions again.
   Apply explicit cash dividends only once in portfolio accounting, using the
   existing product convention.
7. End the common evaluation at 2026-07-30 unless SPY coverage is extended in
   the frozen input snapshot.

The run must assert the structural receipt in `DATA_FEASIBILITY.json` before
any strategy decision. A failed assertion is a data-validity failure, not a
reason to relax the universe or substitute close-to-close returns.

## Execution and cost protocol

Both alternatives use the current engine's completed-daily-bar decision and
next-tradable-open fill. A target created on session `d` cannot fill at
`Open(d)`; it fills at `Open(n(d))`, subject to the existing participation
constraint, known-volume cutoff, sell-before-buy ordering, rebalance band, and
minimum notional. Candidate B's signal is the latest completed `Close -> Open`
component available at the completed-bar decision, not the future fill open.

Evaluate three predeclared equity-cost scenarios:

| Scenario | Slippage | Spread | Regulatory fees |
| --- | ---: | ---: | --- |
| 1x | current `2` bps | current `2` bps | effective-dated SEC/TAF/CAT schedule |
| 2x | 4 bps | 4 bps | twice the same effective-dated variable fee schedule |
| 3x | 6 bps | 6 bps | three times the same effective-dated variable fee schedule |

Commission remains zero in every scenario. The later test must use a test-only
fee multiplier if the current convenience entry point scales only slippage and
spread; it must not silently label an unscaled fee run as 2x/3x all-in costs.

## Required reports

For each of A and B, each period, and each cost scenario, report net total
return, CAGR, Sharpe, Sortino, Calmar, maximum drawdown, turnover, target/order
fill completion, exposure, position count, and concentration diagnostics. Report
the same metrics for A and B under identical date and cost definitions. These
are future outputs only; none are calculated by this design audit.

The validation report must include full-calendar-year results for 2022, 2023,
2024, and 2025 separately. The partial 2026 period is reported but is not
treated as a full calendar year for the yearly-consistency gate.

## Pre-frozen acceptance gates

All gates are conjunctive. A single failure means B does not earn a promotion
recommendation. Passing them would only make B eligible for a separately
reviewed research decision; it would not auto-promote B to PAPER or LIVE.

### G1 — Experiment and data validity

Pass only if:

* the normalized row count is exactly `11,686,832`, the pinned bars/lifetime
  hashes match `DATA_FEASIBILITY.json`, and canonical duplicate/conflict,
  non-finite, nonpositive-open, and nonpositive-close counts are all zero;
* at least `99%` of dataset session dates in each evaluation window have SPY;
* at least `95%` of otherwise scanner-eligible symbol-decision observations
  have a valid current/prior common-session pair, with every exclusion labeled
  as a session gap, lifecycle boundary, or missing provider observation; and
* zero orders use a future fill-bar field, a post-date lifecycle status, or a
  same-session open after observing that session's close.

### G2 — Positive net candidate economics

At 1x cost, Candidate B must have positive net CAGR and positive net total
return in the full sample, the development period, and the validation period.
Positive full-sample economics alone is insufficient.

### G3 — Risk-adjusted displacement of V3

At 1x cost, in the full sample B must exceed A by at least `0.05` in at least
two of Sharpe, Sortino, and Calmar, while not being worse than A by more than
`0.05` on the remaining metric. In validation, B must exceed A by at least
`0.03` on at least one of those three metrics and may not be worse by more than
`0.05` on either other metric.

### G4 — Independent validation support

At 1x cost, B must be net-positive in at least three of the four complete
validation calendar years 2022–2025, and no one of those full years may have a
net total return below `-8%`. B's validation net CAGR must remain positive
after applying the same rule to the locked validation window as a whole.

### G5 — Drawdown constraint

At 1x cost, B's maximum drawdown must be no greater than `10%` and no more than
`1.00` percentage point worse than A in both the full sample and validation.
Risk-overlay-triggered reductions must be counted as executed policy, not
removed from the calculation.

### G6 — Cost robustness

In validation, B must have positive net CAGR at 2x cost. At 3x cost its net
total return must be greater than `-2%`, and its maximum drawdown may not exceed
A's 3x maximum drawdown by more than `2.00` percentage points. The B result must
not be rescued by dropping partial fills, changing the scanner, or changing the
holding rule across cost scenarios.

### G7 — Turnover and implementation feasibility

At 1x cost, B's annualized one-way target turnover must be no more than `20x`
and its 2x turnover must be no more than `1.50x` its 1x turnover. At least `95%`
of scheduled target notional must be filled within two tradable sessions or be
explicitly recorded as an outstanding order; no more than `5%` of scheduled
decisions may be skipped for unexplained data/completeness failures. All target
orders must remain within the six-slot and participation policies.

### G8 — Concentration robustness

At every point, no target or realized position may exceed the current `0.10`
cap. At 1x cost, no single symbol may account for more than `20%` of B's
cumulative absolute net P&L contribution, and removing that symbol's realized
contribution must leave validation net CAGR positive. The report must also show
that sector membership remains within the existing maximum-five rule.

### G9 — Capital and architecture parity

Neither alternative may use more than `0.55` equity allocation, more than six
account positions, a new data feed, a new fundamental/event dataset, a
candidate-specific liquidity exception, or a combined V3+B sleeve. Any
difference in realized exposure must be attributable to the frozen signal,
current risk overlay, or documented fill availability; it may not come from
different cash, lifecycle, scanner, or cost policy.

## Stopping rule

Do not search a second horizon, cadence, rank direction, weighting method,
market adjustment, or selection count after seeing any result. If B fails, the
correct action is to record the failed gate and keep current V3 unchanged; do
not auto-promote, retune, or create a replacement candidate inside the same
experiment.
