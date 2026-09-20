# Future frozen economic test protocol

Status: designed only; **do not run while G1 readiness prerequisites are
unmet**.

## Arms and common contract

- **Control A:** fresh replay of canonical `equity_residual_momentum_v3` from
  current main; do not copy old V3 metrics into the comparison.
- **Candidate B:** `equity_52week_high_close_v1` exactly as frozen in
  `CANDIDATE_DESIGN.md`.

Both arms must use identical canonical bars, PIT scanner inputs, entity
lifetimes, reserved-role contract, starting capital, account-slot state,
equity maximum allocation, caps, risk overlay, execution model, order bands,
volume limits, and costs. They compete for the same six scarce account slots.
There is no combined V3-plus-candidate arm.

## Frozen chronology

- Warmup: 2016.
- Development: 2017-01-03 through 2021-12-31.
- Validation: 2022-01-03 through 2026-07-30, the canonical common endpoint.
- Full: 2017-01-03 through 2026-07-30.

The endpoint and the three canonical input hashes must be asserted before any
economic output. The validation boundary may not move afterward.

## Cost stresses

Run separate 1x, 2x, and 3x cells. The multiplier applies to adverse slippage,
full spread, and each effective-dated SEC/FINRA/CAT component. Commission stays
zero because scaling zero remains zero. Strategy, data, timing, allocation,
and execution ordering do not change across cells.

## Pre-frozen conjunctive acceptance gates

Every gate must pass. A missing or indeterminate gate is a failure, not a
waiver.

### G1 - data and implementation validity

- Canonical hashes, row counts, normalized sessions, role exclusions, and
  endpoint match this design audit.
- A tested canonical loader normalizes `1Day`/`1d`, timestamps, lifetimes, and
  dividend deduplication without discarding valid rows.
- Month-end decision and broad-pool-through-`t` ranking semantics pass the
  causal fixtures in `CAUSALITY_AUDIT.md`.
- Fresh Control A reproduces the canonical control within deterministic
  tolerances set before Candidate B is run: metric absolute difference at most
  `1e-8` and identical target/order event counts and hashes, except for a
  documented serializer-only difference.
- No future visibility, role, lifecycle, fee-scaling, or target/ranker parity
  assertion fails.

### G2 - positive standalone economics

At 1x, Candidate B must have positive net total return, CAGR, Sharpe, Sortino,
and Calmar in development, validation, and full periods.

### G3 - full-period risk-adjusted displacement

At full-period 1x, versus fresh Control A:

- at least two of Sharpe, Sortino, and Calmar improve by `>= 0.10` absolute;
- none of those three declines by more than `0.05` absolute; and
- Candidate CAGR is no more than `1.00` percentage point below Control CAGR.

### G4 - independent validation support

At validation 1x:

- Candidate net return and CAGR are positive;
- at least one of Sharpe, Sortino, or Calmar improves over Control by `>= 0.10`;
- none declines by more than `0.10`; and
- Candidate maximum drawdown is no more than `2.00` percentage points worse
  than Control.

### G5 - drawdown and Calmar discipline

At full-period 1x, Candidate maximum drawdown is no more than `2.00` percentage
points worse than Control and Candidate Calmar is not below Control by more
than `0.05`. This is an offline alpha comparison; the 5% PAPER/LIVE operational
latch is not an offline max-drawdown gate.

### G6 - cost robustness

- Full-period Candidate net return, CAGR, and Sharpe remain positive at 2x and
  3x.
- Candidate 3x CAGR retains at least `75%` of its own 1x CAGR.
- At 2x and 3x, Candidate Sharpe is not more than `0.05` below the corresponding
  fresh Control Sharpe.

### G7 - turnover and implementation feasibility

- Full-period annualized one-way target turnover is `<= 6.0x`.
- The 95th percentile scheduled monthly one-way turnover is `<= 1.0x`.
- Unfilled plus rejected intended notional is `< 5%` of total intended traded
  notional, with no single calendar year at or above `10%`.
- No acceptance claim may omit partial-fill, order-band, or volume-limit
  effects.

### G8 - concentration robustness

- At least five ordinary target names are held after risk overlay on `>= 95%`
  of scheduled decisions where six account slots are available.
- No entity contributes more than `35%` of Candidate gross positive P&L over
  the full 1x period.
- Removing the largest absolute P&L-contributing entity from attribution must
  not change G2 from pass to fail. This is a diagnostic only; it may not be used
  to retune or rerun the strategy.

### G9 - architecture and capital parity

- Both arms use identical scanner policy, role exclusions, capital, slots,
  allocation/caps, lifecycle, risk, execution, and cost configuration.
- Candidate exists only in the isolated research surface. PAPER/LIVE config,
  strategy registry, orders, broker APIs, and runtime state remain unchanged.

## Decision rule

Only one frozen run is authorized after G1 readiness is proven. Passing all
nine gates would support a later governance review, not automatic PAPER/LIVE
promotion. Any failed gate ends the experiment without tuning, a rescue
variant, or another candidate.
