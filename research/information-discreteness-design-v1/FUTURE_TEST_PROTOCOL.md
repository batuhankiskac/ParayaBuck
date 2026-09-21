# Future frozen economic-test protocol

This file designs a later experiment; it is not a run result. The experiment
must not be started from this design branch until the candidate and protocol
are accepted as frozen.

## Arms

### Control A

Fresh canonical `equity_residual_momentum_v3`, rebuilt from the corrected
main/data identity and its production PAPER parameters. Do not reuse a stale
performance report as the parity proof.

### Candidate B

`equity_information_discreteness_continuous_v1` exactly as frozen in
`CANDIDATE_DESIGN.md`. No V3 sleeve, blend, ID filter inside V3, coefficient,
threshold, cadence, or weighting change is permitted.

## Common contract

Both arms use the same corrected bars, PIT scanner, lifetimes, corporate-action
loader, reserved-role policy, account capital, maximum six positions, 0.55
equity budget, per-position cap, cash/risk policy, order-existence policy,
execution engine, and effective-dated equity-cost model. Candidate B replaces
only the equity alpha construction; it does not add a second equity sleeve or
change PAPER/LIVE configuration.

Warmup begins in 2016. The development window is `2017-01-03` through
`2021-12-31`. The validation window is `2022-01-03` through the canonical
endpoint `2026-07-30`. The chronology and endpoint are frozen before any
Candidate B economic output.

## Cost stress

Run the exact frozen arms at cost multipliers `1x`, `2x`, and `3x`. The
multiplier scales the existing slippage, spread, and effective-dated
regulatory-fee components. No new commission, spread, liquidity, or fee
assumption may be introduced after seeing an output.

## Gate order

The runner must stop at the first invalidity. Economic gates are evaluated only
after the data/protocol gates pass and all arm outputs are materialized.

### G1 — validity and causal implementation

- Data hashes, configuration identity, time windows, roles, capital, and
  execution semantics match this protocol.
- A fresh Control A 1x replay passes canonical parity before Candidate B output
  is interpreted.
- Candidate B uses only completed-bar/PIT information and the exact 274-close
  fail-closed history rule.
- An independent signal recomputation agrees with the recorded Candidate B
  signals and target order at every checked decision.

### G2 — positive Candidate B economics

At 1x, Candidate B must have positive net total return, positive CAGR,
positive Sharpe, positive Sortino, and positive Calmar in each of development,
validation, and full-period reports.

### G3 — full-period displacement versus fresh Control A

At 1x full period, at least two of Sharpe, Sortino, and Calmar must improve by
at least `0.10`. None of those three may decline by more than `0.05`, and
Candidate B CAGR may be at most one percentage point below Control A CAGR.

### G4 — independent validation

At 1x validation, Candidate B net total return and CAGR must be positive; at
least one of Sharpe, Sortino, or Calmar must improve by at least `0.10` versus
Control A; the worst of those three may not be more than `0.10` below Control A;
and Candidate B maximum drawdown may not exceed Control A by more than two
percentage points.

### G5 — drawdown and Calmar discipline

At 1x full period, Candidate B maximum drawdown must be no more than two
percentage points worse than Control A and Candidate B Calmar must be no more
than `0.05` below Control A.

Offline alpha maximum drawdown is used here. It is not compared with the 5%
operational PAPER/LIVE drawdown latch.

### G6 — cost robustness

At both `2x` and `3x` full-period stress, Candidate B net total return, CAGR,
and Sharpe must remain positive. Candidate B 3x CAGR must be at least 75% of
its own 1x CAGR. Candidate B 2x and 3x Sharpe may not be more than `0.05` below
the corresponding Control A stress Sharpe.

### G7 — turnover and execution feasibility

- Annualized one-way target turnover is at most `6.0x`.
- The 95th percentile scheduled monthly one-way turnover is at most `1.0x`.
- Unfilled plus rejected intended notional is below 5% overall.
- No calendar year has one-way target turnover of `10.0x` or more.

### G8 — concentration robustness

- At least five ordinary target names are present on at least 95% of scheduled
  decisions where six slots are available.
- No single entity supplies more than 35% of gross positive P&L contribution.
- An attribution-only removal of the largest entity still passes G2.
- Report unique filled names, sector coverage when available, and maximum
  realized concentration; do not use sector data to alter selection.

### G9 — capital and architecture parity

The experiment passes only if the two arms share scanner, roles, capital,
maximum slots, order policy, engine, cost model, and PIT semantics, and if no
production, PAPER, or LIVE setting changes. A candidate that requires a new
external dataset or a security-master conversion is not an accepted run of
this protocol.

## Freeze and reporting rule

The candidate, windows, gates, stress levels, and comparison direction are
locked before any economic output is read. A failed gate is recorded as a
research result; it does not authorize parameter rescue, cadence rescue,
unfrozen alternative candidates, or production promotion.
