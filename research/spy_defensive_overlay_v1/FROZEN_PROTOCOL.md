# equity_spy_defensive_overlay_v1 — Frozen Protocol

Frozen before any candidate economic outcome was calculated on 2026-09-17.

## Identity and scope

- Clean production base: `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`.
- Research branch: `research/spy-defensive-overlay-v1`.
- Candidate: `equity_spy_defensive_overlay_v1`.
- Research only. It is not registered in production, PAPER remains unchanged, LIVE remains disabled, and no broker/API order is permitted.
- One frozen candidate only. No parameter sweep, alternate inverse product, signal, cadence, confirmation filter, or post-outcome retuning is allowed.

## Mechanism and exact signal

The candidate tests whether a negative long-horizon broad-market trend can improve the incumbent equity sleeve's drawdown and risk-adjusted portfolio contribution by reallocating a fixed part of that sleeve from unchanged V3 to a real daily-reset inverse S&P 500 ETF.

At each scheduled weekly V3 decision timestamp `t`, after the completed daily bars for that timestamp have been ingested:

```text
trend_return = SPY_close[t] / SPY_close[t-252] - 1
NORMAL       = trend_return > 0
DEFENSIVE    = trend_return <= 0
```

Exactly 253 completed SPY observations are therefore required. The signal uses the supplied split-adjusted SPY closing-price series exactly as the formula specifies. Dividends are not folded into this signal; cash dividends are credited once in portfolio NAV accounting. The current bar must be complete, orders created at `t` may fill only on a later tradable bar, and the regime is not evaluated between weekly decisions.

## Arms and allocation

All arms call the production `equity_residual_momentum_v3` implementation unchanged.

| Arm | NORMAL | DEFENSIVE |
|---|---|---|
| A — incumbent | V3 0.55 | V3 0.55 |
| B — defensive cash | V3 0.55 | V3 0.45; 0.10 additional cash |
| C — defensive SH | V3 0.55 | V3 0.45; SH overlay 0.10 |

No extra capital is introduced. Gross, cash, per-position, equity-sleeve, and six-account-position limits remain production values. SH consumes one real account position. In defensive treatment decisions no more than five non-SH V3 positions can coexist with SH.

V3 currently may rank SH itself. Changing its eligible inputs would violate incumbent identity, so the following overlap rule is frozen:

- preserve the exact V3 target in all arms before sleeve scaling;
- separately record `v3_owned_sh` and `overlay_owned_sh`;
- merge them into one account position;
- cap total SH weight at the production 0.10 per-position limit;
- overlay allocation equals at most the unused SH capacity;
- record any shortfall as `sh_overlap_per_asset_cap` and leave unused allocation in cash;
- NORMAL and Arm B prohibit overlay-owned SH, not SH independently selected by unchanged V3.

## Instrument and accounting

- Instrument: SH only. No PSQ, DOG, SQQQ, leveraged inverse ETF, or synthetic `-SPY` series.
- Use actual SH bars from the validated local SIP panel.
- SH is a daily-reset product; multi-day performance is its actual path-dependent return, including tracking, financing, fees, and compounding embedded in its bars.
- The price panel is split-adjusted. Do not apply split actions again.
- Apply cash distributions exactly once as explicit portfolio cash flows.
- Use the production equity execution model and effective-dated regulatory fees. Cost stress multiplies configured slippage and spread by 1x, 2x, and 3x; statutory fees are not multiplied.

## Frozen data identity

| Input | SHA-256 | Role |
|---|---|---|
| `data/historical/equity_pit_symmetric_2016_2026_v5.csv` | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` | split-adjusted SIP equity bars, including real SPY and SH |
| `data/historical/equity_entity_lifetimes_full_v5.csv` | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` | PIT entity terminal/lifetime evidence |
| `data/historical/equity_survivorship_full_corporate_actions_v5.csv` | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` | cash distributions and split audit data |

The data package must match these hashes before execution. SPY has 2,658 daily bars and SH 2,606 from 2016-01-04 through 2026-07-30. SH's 52 SPY-session gaps are confined to 2016-04-13 through 2016-06-24, before the development window. Both instruments require active `TIER_A_VERIFIED_PIT` lifetime evidence. No data substitution is allowed.

## Frozen chronology

- Warmup: 2016-01-04 through 2016-12-30.
- Development: 2017-01-03 through 2021-12-31.
- Validation: 2022-01-03 through 2026-07-31 (the final actual data date is 2026-07-30).
- Full scored period: 2017-01-03 through 2026-07-31.

Boundaries may not move after outcomes. All economic tables report full, development, and validation windows.

## Required evidence

For A, B, and C at every cost level persist net return, CAGR, annualized volatility, Sharpe, Sortino, max drawdown, Calmar, worst month, turnover, costs, average exposure, max concentration, fills, and rebalances. Persist scheduled weekly opportunities, decisions, skips and reasons, first/last decision, longest decision gap, regime counts/episodes/transitions, separate NORMAL/DEFENSIVE performance, and per-defensive-decision intended/realized V3 and SH exposure, V3 name count, total positions, unused allocation, and capacity-loss reason.

Independent verification must reconstruct the signal and allocation without importing candidate helper functions, and must verify hashes, completed-bar indexing, no lookahead, next-bar execution, actual SH bars, lifecycle behavior, coverage, slot/cap/gross/cash limits, unchanged V3 identity, NAV, costs, and A/B/C semantics.

## Frozen acceptance gates

All thresholds are conjunctive unless a gate explicitly says “at least one.” Percentage-point (`pp`) comparisons use absolute decimal return differences multiplied by 100.

1. **Replay/data validity.** All three input hashes match; all supplied bars/actions/lifetimes validate; no synthetic SH observation exists; `scheduled = decisions + skips`; every skip has a reason; development and validation each contain at least one active decision; no overdue managed terminal symbol, unresolved pending terminal order, lookahead, NAV mismatch greater than `1e-8`, or allocation/cost reconciliation error greater than `1e-8` exists.
2. **Drawdown objective.** At 1x full period, C reduces max-drawdown magnitude versus A by at least `2.00 pp` and at least `10%` relatively.
3. **Risk-adjusted improvement.** At 1x full period, C improves at least one of Sharpe, Sortino, or Calmar versus A by `>= 0.10`; neither of the other two may deteriorate by more than `0.05`.
4. **Return cost.** C's CAGR drag versus A is at most `2.00 pp` full-period and at most `3.00 pp` in validation.
5. **SH incremental value.** At 1x full period, C versus B must improve either max-drawdown magnitude by `>= 1.00 pp` or one of Sharpe/Sortino/Calmar by `>= 0.05`; C's CAGR may not trail B by more than `1.00 pp`. Validation must show the same chosen benefit direction with at least `0.50 pp` drawdown improvement or `0.03` risk-adjusted improvement.
6. **Validation.** At 1x validation, C versus A reduces max-drawdown magnitude by `>= 1.00 pp`, improves at least one of Sharpe/Sortino/Calmar by `>= 0.05`, and satisfies Gates 4 and 5. Development and validation effects must not have opposite signs on both drawdown and the qualifying risk-adjusted metric.
7. **Cost robustness.** The 2x run must still satisfy Gates 2–6. At 3x, C must not have worse max drawdown than A, must not trail A CAGR by more than `4.00 pp`, and must not trail B on all three risk-adjusted metrics simultaneously.
8. **Deployability.** Every decision respects six total positions, 0.10 per-position cap, 0.55 equity sleeve, 0.60 gross cap, and 0.40 cash floor. During defensive C decisions the median realized overlay-SH/intended-overlay-SH ratio must be `>= 0.75`, at least `80%` of decisions with positive intended overlay must realize `>= 0.075` overlay SH, and average equity under-utilization attributable to slot/cap loss must be `<= 0.05`. No unused amount is silently reallocated.

## Decision rule

- `RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION` only if every gate passes and independent verification is valid.
- `RESEARCH_REJECT` if valid evidence fails any economic or deployability gate.
- `RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT` only for a genuine data/accounting/validation blocker that prevents an honest gate decision.

Acceptance authorizes only a later, separate forward shadow/PAPER evaluation. This campaign never changes PAPER or LIVE.
