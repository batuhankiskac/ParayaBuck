# Information-discreteness design report

## Design decision

`DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`

Exactly one candidate is frozen:
`equity_information_discreteness_continuous_v1`.

This is a pre-outcome design decision. No candidate economics were inspected
or calculated.

## Starting main

- Repository: `/Users/batuhankiskac/Desktop/ParayaBuck`
- Clean current `main`: `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de`
- `origin/main`: `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de`
- PAPER equity identity: `equity_residual_momentum_v3`
- Runtime mode: `paper`
- LIVE: disabled; hard dollar cap `0`
- Design branch: `research/information-discreteness-design-v1`

The production branch was not modified.

## Literature evidence

Da, Gurun, and Warachka’s primary paper defines ID from signed daily returns
over a 12-1 interval and finds that continuous information (low ID for positive
winners) is the slower-diffusing direction. Later time-series, linked-firm,
international, and post-cost evidence supports testing the mechanism while
warning about conventional-momentum overlap, liquidity, and post-publication
decay. The full source-by-source record is in `LITERATURE_REVIEW.md`.

## Canonical information-discreteness formula

For 252 simple close-to-close returns after a 21-return skip:

```text
ID = sign(PRET) * (N_negative / 252 - N_positive / 252)
```

Zero returns remain in the denominator and are neither positive nor negative.

## Direction / interpretation

For `PRET > 0`, lower/more-negative ID is more continuous. A continuous winner
has many positive sessions; a discrete winner has fewer positive jumps and more
negative or zero sessions. Candidate B ranks ascending ID.

## Distinctness from V3

The candidate is a separate raw-price alternative, not a V3 feature or filter.
It removes V3’s residualization, 6/9/12 consensus, IVOL rejection, inverse-vol
sizing, and weekly cadence from the candidate arm, while preserving all PIT,
capital, role, cost, and execution mechanics. This gives the path-quality
hypothesis clean attribution and forces a meaningful six-slot displacement
test. See `V3_DISTINCTNESS_AUDIT.md`.

## Raw versus residual return decision

Use raw split-adjusted stock closes and simple close-to-close returns. This is
the published ID object. The paper’s residual-ID check is not V3-style
SPY-return residualization; using residual returns here would be a novel,
confounded hypothesis.

## Formation and skip

Use exactly 12-1: 252 formation returns and 21 most-recent skipped returns,
requiring 274 aligned closes. The skip applies to both `PRET` and the sign
counts. History is never dynamically shortened.

## Data feasibility

The corrected canonical dataset has normalized regular-session `1d` bars,
strict PIT lifetimes, split-adjusted signal prices, apply-once cash-dividend
semantics, and sufficient structural history for the exact window. The
structural counts and hashes are recorded in `DATA_FEASIBILITY.json`.

## Universe limitation

The broad historical `US_EQUITY` universe is not a proven common-stock-only
taxonomy. Because ID is a price-path property, the signal can technically be
formed for an ETP, but the behavioral interpretation remains limited. The
limitation is acceptable for a same-universe A/B test and is not hidden or
resolved by a new security-master source.

## Causality

The causal path is completed decision bar -> PIT eligibility -> exact prior
closes -> signal -> rank -> six targets -> next tradable open. Missing sessions,
ambiguous identities, and insufficient history fail closed. No future terminal
status or future bar enters the score. See `CAUSALITY_AUDIT.md`.

## Exact candidate

`equity_information_discreteness_continuous_v1`:

- raw split-adjusted simple close-to-close returns;
- `PRET = C[t-21] / C[t-273] - 1`;
- 252 formation returns, 21 skipped returns, 274 exact aligned closes;
- `ID = sign(PRET) × (N_negative/252 - N_positive/252)`;
- zero days remain in the denominator;
- eligibility requires PIT scanner eligibility and `PRET > 0`;
- rank the PIT pool by ID ascending, then canonical symbol ascending, and use
  that rank for the current 30-symbol observation cap;
- select at most six names;
- equal weights with `min(0.55/n, 0.10)` per selected name;
- monthly last completed regular NYSE session;
- next tradable open execution proxy;
- fail closed on missing/ambiguous/insufficient data;
- `SPY` reference-only and `SH`/`PSQ`/`DOG` hedge-only.

## Portfolio construction

Candidate B is an alternative equity alpha, not a sleeve combined with V3. It
uses the current six-position architecture, 0.55 equity budget, 0.10
per-position cap, equal weighting, existing cash/risk policy, and no fallback
or arbitrary blend.

## Cadence

Monthly, at the last completed regular NYSE session. The current engine’s next
tradable-open fill proxy is retained and documented; there is no same-close
execution.

## Future test design

Control A is a fresh canonical V3 replay. Candidate B is the exact frozen
candidate above. Warmup is 2016, development is `2017-01-03` to `2021-12-31`,
and validation is `2022-01-03` to `2026-07-30`. Both arms use 1x/2x/3x current
cost stress. The protocol is in `FUTURE_TEST_PROTOCOL.md` and must be frozen
before any economic output is read.

## Frozen gates

G1 validity/causality; G2 positive candidate economics; G3 full-period
risk-adjusted displacement; G4 independent validation; G5 drawdown/Calmar;
G6 2x/3x cost robustness; G7 turnover/execution; G8 concentration robustness;
G9 capital/architecture/PIT parity and no production change. Offline alpha
maximum drawdown is not compared with the 5% PAPER/LIVE latch.

## Red-team findings

The candidate can be momentum repackaging, can be weaker in a six-name
long-only translation, can be exposed to microcap/cost and broad-universe
taxonomy risk, and can overlap with V3. Those risks are explicit in
`DESIGN_RED_TEAM.md` and are addressed by separate architecture, same-account
comparison, liquidity/cost gates, validation, and no auto-promotion. They do
not invalidate one sealed design experiment.

## Confirmation of no local economic peek

No candidate P&L, NAV, forward-return table, rank IC, hit rate, future-return
regression, candidate/V3 correlation, Sharpe, CAGR, drawdown, or optimization
was calculated or inspected for this design. Structural counts only are
recorded in `NO_ECONOMIC_PEEK_RECEIPT.json`.

## Files

- `CURRENT_CAPABILITY_AUDIT.md`
- `LITERATURE_REVIEW.md`
- `DATA_FEASIBILITY.json`
- `V3_DISTINCTNESS_AUDIT.md`
- `CAUSALITY_AUDIT.md`
- `CANDIDATE_DESIGN.md`
- `FUTURE_TEST_PROTOCOL.md`
- `DESIGN_RED_TEAM.md`
- `NO_ECONOMIC_PEEK_RECEIPT.json`
- `DESIGN_DECISION.json`
- `FINAL_DESIGN_REPORT.md`

## Branch SHA

The branch was created from clean corrected main at
`839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de`. The final design-branch tip SHA
is emitted in the handoff after the artifact commit; no production SHA is
changed.
