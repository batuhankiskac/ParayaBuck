# Abnormal-volume design v1 — final report

## Design decision

`DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`

Exactly one candidate is frozen: `AV49_D1_NORMALRET_M1_LONG`. READY is conditional on building and passing the non-economic G1 harness checks; it does not mean economics are favorable or currently runnable through the ordinary CLI.

## Starting main

Repository `/Users/batuhankiskac/Desktop/ParayaBuck` began on clean `main`. Local `main`, `origin/main`, and branch base/HEAD were `86123374e41735d8d163c7fbed5bf5edfc43468d`. Work is on `research/abnormal-volume-design-v1`. PAPER equity remains `equity_residual_momentum_v3`; LIVE remains disabled. No production code or configuration changed.

## Primary literature

Gervais–Kaniel–Mingelgrin defines a daily high-volume event by ranking formation-day dollar volume with the preceding 49 sessions and classifying ranks 46–50 as high. Daily high and low legs both contributed; weekly evidence was more low-leg-driven. Lee–Swaminathan studies multi-month shares-outstanding turnover interacting with momentum and is not this signal. Later international and U.S. evidence supports further study but shows size, country, horizon, and cost dependence.

## High-volume premium mechanism

Investor recognition/visibility and attention are plausible. Information arrival, disagreement, liquidity demand, contemporaneous pressure, macro/fundamental information, and product flows remain competing explanations. This design makes no causal claim.

## Exact abnormal-volume definition

At calendar-month final close, compute `DV=split_adjusted_close*split_adjusted_volume`. Require 50 complete observations and rank the current observation within inclusive `t-49:t`; rank must be 46–50. Formation-day return must rank 16–35 within its inclusive 50-return history. Order qualified names by descending volume rank, descending `DV(t)/Q0.90(DV[t-49:t-1])`, then stable identity. The ratio is a frozen, selection-relevant six-slot engineering extension, not published GKM methodology; it remains part of this one candidate.

## Share volume vs turnover vs dollar volume

Turnover is rejected because PIT shares outstanding are unavailable and it represents Lee–Swaminathan, not GKM. Raw shares are split-scale-dependent. Split-consistent dollar volume matches GKM and is invariant under reciprocal price/volume adjustment.

## Split / volume semantics

The canonical SIP bars are provider `split` adjusted in both price and volume. Structural checks confirm reciprocal scaling and product invariance. Retrospective normalization makes absolute adjusted share counts noncausal, but an own-window rank/ratio is invariant to uniform later rescaling. Zero adjusted volume can be a reverse-split rounding artifact; any zero or missing value in the required window fails closed.

## Long-only evidence

Daily GKM shows high-volume outperformance against same-size references as well as low-volume underperformance, supporting one long-leg test. It does not establish modern after-cost performance in a concentrated liquid portfolio. The later test must pass long-leg gates without relying on a short leg.

## Distinctness from V3

V3 uses residual price momentum, multi-horizon consensus, IVOL filtering, inverse-volatility sizing, and weekly decisions. The candidate uses a one-day own-history activity shock, equal weights, and monthly decisions. The return-rank exclusion removes extremes and is not a momentum score. Outcome correlation and complementarity remain untested.

## Scanner / liquidity interaction

The scanner's trailing median dollar-volume minimum is a liquidity eligibility control, not an abnormal-volume signal. It removes the illiquid comparison set and may attenuate an effect known to be stronger in small/neglected firms. Because the candidate is high-leg-only and A/B arms share the scanner, this is acceptable for one test, but liquid-universe survival and cost feasibility are mandatory rather than assumed.

## Universe limitation

`UNIVERSE_LIMITATION_ACCEPTABLE_FOR_SHARED_A_B_TEST`. The broad `US_EQUITY` universe can contain ETFs, ETNs, leveraged/bond/structured products, ADRs, and other non-common-stock instruments. Own-history scaling reduces level bias but does not make firm-visibility interpretation portable to products. Reserved roles remain excluded and claims are restricted to the canonical screened universe.

## Data feasibility

`CONDITIONALLY_FEASIBLE`. Canonical v5 has 11,686,832 rows, 9,010 identities, 2,658 common-endpoint sessions through 2026-07-30, and 11,010,211 positive-volume rows. There are 676,621 zero-volume rows across 4,222 identities; every such row has nonzero OHLC and is excluded when inside a signal window. The final 2026-07-31 cross-section is partial and forbidden. Strict lifecycle behavior exists, but ordinary CLI wiring is incomplete; `EOSEW` is unresolved. A dedicated harness must resolve these G1 conditions.

## Causality / holding convention

The signal completes after the prior month's final close, fills at the next month's first tradable open, and holds until the following month-first-open with no signal rebalance. This is nonoverlapping and auditable. It is a disclosed translation from GKM's 20-session hold using Wang's later month-end/monthly precedent. Same-close execution is forbidden.

## Exact candidate

- Name: `AV49_D1_NORMALRET_M1_LONG`
- Measure: split-consistent daily dollar volume
- Formula: inclusive-50 own-history rank 46–50
- Reference: 49 prior exchange sessions plus one-day formation
- Return condition: formation return rank 16–35 of inclusive 50
- Direction: descending/high only
- Minimum data: 50 valid close/volume observations plus the preceding close for returns
- Zero/missing: security ineligible; no imputation or epsilon
- Split policy: reciprocal split-adjusted price/volume; scale-invariance test required
- Cadence/holding: calendar month-end decision, next-month-first-open entry, hold to following month-first-open
- Selection: at most six, frozen rank/ratio/identity ordering
- Weighting/allocation: equal weight, `min(0.10,0.55/N)`, unused capital cash
- Architecture: gross <= 0.60, cash >= 0.40, global positions <= 6
- Universe: PIT canonical scanner, lifecycle eligible, reserved roles excluded

## Future standalone economic test

Fresh V3 is Control A and the frozen candidate is B. They share dataset, dates, PIT scanner, lifetimes, corporate actions, reserved roles, capital, slots, risk, costs, and next-open execution. Use warmup 2016, development 2017-01-03–2021-12-31, validation 2022-01-03–2026-07-30, and full 2017-01-03–2026-07-30. Run 1x/2x/3x only after G1 passes.

## Frozen standalone gates

G1 validates hashes, raw input, causality, split invariance, lifecycle wiring, schedule, role isolation, endpoint, parity, and independent no-peek review. G2 requires positive full 1x return/CAGR. G3 requires positive development 1x return/CAGR. G4 requires positive validation 1x return/CAGR/Sharpe and <=30% drawdown. G5 requires full Sharpe >=0.35, Sortino >=0.45, Calmar >=0.20, drawdown <=30%. G6 requires full/validation return and CAGR positive at 2x and full return/CAGR/Sharpe positive at 3x. G7 limits annualized one-way turnover to 12, requires >=95% fill, <=5% unfilled, and costs <=35% of positive gross profit. G8 requires median five holdings, >=4 at 90% of decisions, name cap, contributor cap, and positive leave-one-out full/validation economics. G9 proves capital/architecture parity. The detailed binding definitions are in `FUTURE_TEST_PROTOCOL.md`.

## V3 comparison semantics

Report candidate-minus-V3 CAGR, Sharpe, Sortino, and Calmar descriptively. Beating V3 is not a standalone gate. A candidate that passes G1–G9 but trails V3 is still `ALPHA_VALID_COMBINATION_UNTESTED`.

## Future combination trigger

All G1–G9 passing automatically permits a separate frozen `5+1` core-satellite complementarity test. It does not authorize configuration changes, PAPER, LIVE, or automatic promotion. Any mandatory gate failure closes this candidate as `ALPHA_REJECTED` without tuning.

## Red-team findings

The main threats are weak large/liquid and long-only evidence, cost erosion, six-name concentration, broad-product contamination, split-rounded zeros, engineered secondary ordering, schedule translation, identity/lifecycle gaps, and diagnostic-driven multiple testing. They are addressed by exclusions and gates, not presumed away.

## No-economic-peek verification

No backtest, future-return join, high-minus-low result, P&L, NAV, CAGR, Sharpe, Sortino, Calmar, drawdown, IC, bucket return, hit rate, candidate/V3 outcome correlation, or parameter-performance surface was computed or read. Only source/config semantics, structural counts, contemporaneous missingness/adjustment checks, and literature were used. Independent review is recorded in `NO_ECONOMIC_PEEK_RECEIPT.json`.

## Files

This directory contains the capability, literature, semantics, split, V3, universe, feasibility, causality, candidate, protocol, red-team, receipt, decision, and final-report artifacts requested. It contains no candidate economic result artifact.

## Branch SHA

Branch base and current uncommitted-tree HEAD: `86123374e41735d8d163c7fbed5bf5edfc43468d`. The design artifacts are working-tree additions on `research/abnormal-volume-design-v1` and were not committed.
