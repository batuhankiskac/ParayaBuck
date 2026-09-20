# Current capability audit

Audit date: 2026-09-20
Repository: `/Users/batuhankiskac/Desktop/ParayaBuck`
Design branch: `research/52week-high-design-v1`
Starting `main` and fetched `origin/main`: `db1a6e9e4af4a5000dca0a54a971ce99577c676c`
Starting worktree: clean

No production code or configuration was changed. The product remains in PAPER
with `equity_residual_momentum_v3`; LIVE is disabled with a zero dollar cap.

## Canonical control

The control is not redefined. It is the `equity_residual_momentum_v3` contract
in `docs/research/canonical_v3_baseline.md`: 1d signal, weekly decisions,
positive 12-1 residual gate, equal-weight 6/9/12 residual-momentum rank
consensus, inverse-volatility sizing, maximum equity allocation `0.55`,
per-position cap `0.10`, and the repaired offline-drawdown semantics. The
account has six total slots, gross exposure at most `0.60`, and cash at least
`0.40`.

SPY remains reference-only. SH, PSQ, and DOG remain hedge-only. All four can be
observed but are excluded from ordinary alpha targets. The unresolved broad
historical `US_EQUITY` product-taxonomy limitation remains canonical.

## Current decision path

The inspected path is:

`PIT scanner pool -> strategy ranker -> bounded observation set -> strategy target -> risk overlay -> next-tradable-open order execution`

Relevant implementation surfaces are:

- `src/parayabuck/backtest/universe.py`: Stage-A liquidity shortlist and PIT
  scanner construction.
- `src/parayabuck/universe/observation_set.py`: strategy ranking, history and
  freshness checks, pins, and the maximum-30 stream set.
- `src/parayabuck/backtest/portfolio_engine.py`: completed-bar decisions,
  next-bar open fills, lifecycle actions, order bands, and risk accounting.
- `src/parayabuck/execution/portfolio.py`: six-slot, allocation, cap, gross,
  cash, and rank-retention enforcement.
- `src/parayabuck/backtest/regulatory_fees.py`: effective-dated equity fees.

## Supported capabilities

- Daily OHLCV bars support either close- or high-based reference prices.
- A strategy may rank the eligible pool with its own alpha and emit alpha rank
  for six-slot retention.
- Exactly six long names and equal target weights are supported. Six equal
  sleeve targets become `0.55 / 6 = 0.091666...` each, below the `0.10` cap.
- Completed-bar decisions cannot fill at the same close; the first attempt is
  the next tradable bar open.
- Split-adjusted price history and separate cash-dividend accounting support a
  price-only signal without double-applying corporate actions.
- Offline maximum drawdown remains a diagnostic. It is not compared directly
  with the 5% PAPER/LIVE high-water entry latch.

## Blocking capability gaps

1. **Canonical-loader normalization.** The canonical bar file contains
   7,771,468 `1Day` rows. The generic loader does not normalize `1Day` to `1d`
   before its session check, so the full file is not directly loadable. The
   corporate-action loader also does not accept the canonical action schema.
2. **Month-end schedule.** Current `monthly` means first completed session of
   a new month, followed by the next session's open. The frozen candidate
   requires last completed session of the month, followed by the first
   tradable open of the next month.
3. **Broad-pool decision-bar rank.** Current observation ranking uses broad-pool
   history through `t-1`; the completed `t` bar is ranked only within the
   bounded observation set. The frozen formula requires broad eligible-pool
   ranking through the completed month-end bar `t` before truncation.
4. **All-in cost stress.** `cost_multiplier` scales slippage and spread, but
   effective-dated regulatory fees are currently read directly and are not
   multiplied. Thus generic 2x/3x runs are not full all-in cost stresses.
5. **Lifecycle normalization.** The future runner must explicitly load entity
   lifetimes and fail closed on ambiguous identities; one inactive entity
   (`EOSEW`) lacks a terminal boundary, and complete historical rename-chain
   coverage is not proven.
6. **Research registration.** The candidate is intentionally absent from the
   product strategy factory. A later experiment needs an isolated research-only
   implementation and must not register or activate it in PAPER/LIVE.

These are execution/data blockers, not invitations to change production in
this design task.
