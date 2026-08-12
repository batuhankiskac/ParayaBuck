# Strategy Architecture — Stable Trading-Layer Contract

The product foundation (broker, execution, ownership, reconciliation,
persistence, safety, Web app, PAPER/LIVE separation, research/runtime
separation) is FROZEN. This document defines the STABLE contract of the
trading/strategy layer that may evolve: dynamic universe discovery,
eligibility vs alpha, candidate pool vs observation stream, timeframe
ownership, multi-market strategy independence, and the portfolio/risk
boundary.

## 1. Dynamic universe

Both markets are first-class product capabilities (`EQUITY + CRYPTO`). A
strategy never receives a hand-maintained universe: the scanner selects the
tradeable universe on a pre-session cadence from the broad eligible market.

- Equity: every active/tradable US equity on the supported exchanges
  (~13,000+) → Stage-A shortlist (trailing median dollar volume,
  `universe.equity.target_shortlist`, default 300) → scanner → streamed
  observation set (`maximum_stream_symbols`, default 30).
- Crypto: all Alpaca tradable USD pairs → scanner (liquidity, spread, ATR,
  BTC-correlation, category limits) → streamed observation set
  (`maximum_stream_symbols`, default 12).

Managed positions and open managed orders are pinned into the universe
regardless of ranking: the bot must always be able to observe, rebalance and
exit what it owns.

## 2. Eligibility vs alpha (scanner ≠ strategy)

The scanner answers ONE question: **"which assets are sufficiently tradeable
and observable?"** It scores tradability, liquidity, spread quality, data
completeness, reasonable volatility and market eligibility. It does NOT
answer "which asset will make money" — that is the strategy's job.

- Eligibility filtering happens entirely before alpha scoring.
- Scanner failure is fail-closed: no scanner result → no new trades (and no
  live authorization).
- Scanner metrics are NEVER an alpha input (volume is not a momentum signal).

## 3. Candidate pool vs observation stream

The stream limit is NOT the opportunity universe — this is implemented, not
only documented:

```
13,000+ equity / all crypto
        ↓ eligibility scanner (tradability-first)
broad candidate pool (equity ~300 shortlist)
        ↓ STRATEGY ALPHA RANKING on batch historical data
top observation candidates (<= maximum_stream_symbols)
        ↓ streamed (~30 equity / ~12 crypto)
positions
```

Mechanics:

- The scanner is eligibility-first: `selected_symbols` is the eligible POOL
  (every candidate that passes tradability filters, plus reserved symbols) —
  deliberately larger than the stream capacity. `observation_set` on the
  scanner result is the fail-safe quality-ranked stream set (with
  issuer/sector/category diversity), used when no strategy ranker exists.
- The strategy alpha ranks the pool: `PortfolioStrategy.rank_candidates`
  reuses the EXACT scoring code of `decide` (BACKTEST/PAPER/LIVE parity), so
  the live observation set is selected by the same alpha the strategy trades.
- `build_observation_set` (universe/observation_set.py) produces the streamed
  set: pinned symbols (references, inverse hedges, configured pins, managed
  positions) first, then the alpha-top candidates up to
  `maximum_stream_symbols`, filling any shortfall from the scanner's quality
  ranking (e.g. young symbols without the strategy's warmup history).
- The universe refresh fetches strategy-warmup batch history for the pool
  (`bars_to_calendar_days`), ranks it, and persists `observation_set` in the
  active universe file. `load_active_symbols` / `load_active_crypto_symbols`
  stream the OBSERVATION SET; validation enforces it ⊆ pool and ≤ capacity.
- In backtests the point-in-time universe snapshots carry the eligible POOL
  (so the strategy alpha-scores the broad pool) plus the snapshot's
  OBSERVATION SET. The engine's completeness gate follows the observation
  set (live parity): decisions require a closed bar for every STREAMED
  symbol, never for every pool member — a halted/absent pool member must
  not block the whole market.
- `maximum_stream_symbols` is a streaming-load limit, not a universe size.
  Do not raise it to widen the alpha universe; widen the candidate pool.

## 4. Timeframe ownership

There is NO global strategy timeframe. A strategy is not assumed valid on
every resolution. The independent cadences are:

| Cadence | Owner | Example |
|---|---|---|
| scanner timeframe | scanner config | equity 1d / crypto 1h |
| strategy signal timeframe | the strategy (`StrategySpec.primary_timeframe`) | equity 1d / crypto 1d |
| rebalance / decision cadence | the strategy (`StrategySpec.rebalance_policy`) | monthly / weekly |
| execution timeframe | `runtime.execution_timeframe` | 5m |
| account polling | `runtime` engine config | seconds |
| reconciliation | runtime | periodic |

A strategy declares its contract through `StrategySpec`:

```python
StrategySpec(
    name="equity_residual_momentum_v1",
    display_name="Residual Momentum v1",
    market=Market.EQUITY,
    primary_timeframe="1d",
    supported_timeframes=("1d",),   # ONLY validated variants
    required_timeframes=(),          # extra series the strategy needs
    rebalance_policy="monthly",
    minimum_history_bars=274,
)
```

Rules:

1. A strategy MUST NOT run on a timeframe outside its primary +
   `supported_timeframes`; configuration, runtime and backtest all reject it
   loudly (`validate_strategy_timeframe`, factory `timeframe=` selection).
2. A strategy may expose a timeframe choice ONLY when it explicitly
   validates multiple variants (`supported_timeframes`).
3. The Web UI offers no global strategy-timeframe dropdown; it surfaces each
   strategy's own metadata (signal timeframe, rebalance cadence, universe
   sizes). A restricted per-strategy choice appears only for strategies with
   multiple validated variants.
4. `strategy.*.timeframe` in YAML is a deprecated backward-compatibility
   key: it is validated against the selected strategy and rejected on
   mismatch — never silently reinterpreted. WITHOUT the key, the loader
   resolves the timeframe FROM THE STRATEGY's primary (or a validated
   alternative), so a configured name never inherits another market's
   default. Example: selecting `crypto_time_series_momentum_v1` with no
   timeframe resolves `1d`, and `timeframe: 4h` selects the validated 4h
   variant while `timeframe: 1h` fails at load time.
5. BACKTEST, PAPER and LIVE use identical strategy timeframe semantics: the
   same `PortfolioStrategy` class, the same `spec()`, the same bar
   finalization rule (decisions fire only on completed bars, orders fill on
   the next tradable bar — no same-bar lookahead), and the same warmup
   (`minimum_history_bars` in the strategy's own timeframe).

Data/aggregation contract for every timeframe used: source data (provider
feed), aggregation path (streaming `TimeframeAggregator` buckets + historical
`aggregate_historical_bars`), bar-finalization rule, warmup span
(`bars_to_calendar_days`, session-aware), decision timestamp (last closed
bar), next tradable execution point, and gap behavior (incomplete batches
skip the decision; a timed-out partial batch never silently delays).

## 5. Multi-market strategy independence

Equity and crypto strategies produce target portfolios independently, on
their own timeframes and cadences, from their own universe states. The
runtime aggregates each market's required timeframe simultaneously
(`runtime.aggregators` keyed by timeframe; one `PortfolioRuntime` per
enabled market).

What is SHARED is the account: one authoritative cash/equity ledger, one
risk layer, one order repository. The `PortfolioRiskOverlay` applies
per-market sleeve caps (`maximum_allocation`, `maximum_crypto`,
`maximum_equity`), per-asset caps, position limits, the cash buffer and the
gross-exposure cap on every decision against the CURRENT full account — so
one market's target can never push the combined account outside its limits,
and one surviving crypto asset cannot absorb the sleeve (per-asset cap binds
before renormalization).

Scanner isolation: equity and crypto universes refresh independently; a
failed equity scan does not block crypto decisions and vice versa.

## 6. Portfolio / risk boundary

Strategies output `TargetPortfolio` weights in [0,1] (long-only). The
portfolio/risk layer is the ONLY authority that:

- enforces account-level gross exposure and the cash buffer;
- enforces per-market sleeve and per-asset caps;
- enforces position counts (equity `max_positions`, crypto
  `max_crypto_positions`);
- orders SELL before BUY and sizes against the fresh account;
- halts entries on daily-loss / drawdown breaches (production; backtest
  records shadow events and keeps trading);
- persists decisions, orders and rebalance phases so restart recovery
  resumes exactly (idempotency, two-phase rebalance, no duplicate orders).

Strategies never see the account beyond `equity`/`holdings`/`prices` in the
`PortfolioContext`; they never place orders.

## 7. Research / runtime separation

Research tooling (`scripts/validate_strategy_candidates.py`,
`scripts/strategy_foundation.py`, `scripts/strategy_diagnose.py`) reuses the
production strategy classes, the production historical engine, the
production universe policy, the production cost model and the production
risk policy. The runtime NEVER reads research reports or manifests. A human
selects a strategy by normal configuration after reviewing evidence. There is
no experiment registry, trial ledger, promotion database, holdout lock or
GREEN/RED runtime gate.

## 8. Strategy registry (as of this contract)

| name | market | primary timeframe | supported | rebalance | status |
|---|---|---|---|---|---|
| `equity_dynamic_trend_1d_v1` | equity | 1d | 1d | weekly (param) | frozen reference |
| `equity_long_horizon_momentum_1d_v2` | equity | 1d | 1d | monthly | frozen reference |
| `equity_residual_momentum_v1` | equity | 1d | 1d | monthly | validated candidate (broad-pool alpha) |
| `crypto_cross_sectional_momentum_1h_v1` | crypto | 1h | 1h | hours:24 | frozen reference |
| `crypto_time_series_momentum_v1` | crypto | 1d | 4h, 1d | hours:168 | NOT validated (family verdict C) |
| `crypto_cross_sectional_reversal_v1` | crypto | 1d | 1d | hours:168 | NOT validated (family verdict C) |
| `crypto_high_proximity_v1` | crypto | 1d | 1d | hours:168 | NOT validated (family verdict C, bake-off 2026-08-12) |
| `crypto_low_variance_v1` | crypto | 1d | 1d | hours:168 | validated candidate (bake-off 2026-08-12, PAPER selection) |
| `crypto_low_disagreement_v1` | crypto | 1d | 1d | hours:168 | NOT validated (family verdict C, bake-off 2026-08-12) |

Versioning: strategies are immutable once validated; a change to the signal
specification is a NEW named strategy, never a silent parameter mutation.
