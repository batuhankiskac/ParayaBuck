# Strategy Architecture — Stable Trading-Layer Contract

The product foundation (broker, execution, ownership, reconciliation,
persistence, safety, Web app, PAPER/LIVE separation, offline/runtime
separation) is FROZEN. This document defines the STABLE contract of the
trading/strategy layer that may evolve: universe selection, eligibility vs
alpha, observation stream vs positions, timeframe
ownership, multi-market strategy independence, and the portfolio/risk
boundary.

## 1. Universe

Both markets are first-class product capabilities (`EQUITY + CRYPTO`), but
they build their universe differently.

- Equity: STATIC. `universe.equity.symbols` is `[SPY, QQQ, QLD]`, streamed and
  tradable within `maximum_stream_symbols` (30, the Alpaca IEX limit). QQQ is
  the `equity_leveraged_trend_v1` trend signal, QLD its only holding, SPY the
  benchmark. There is no equity scanner, no shortlist, no candidate scoring and
  no persisted equity universe artifact.
- Crypto: all Alpaca tradable USD pairs → scanner (liquidity, spread, ATR,
  BTC-correlation, category limits) → streamed observation set
  (`universe.crypto.maximum_stream_symbols`, default 12), refreshed on the
  `runtime.scanner_refresh_hours` cadence.

The equity policy identity is `parayabuck.universe.equity.equity_universe_digest`
(version `equity-static-v1`), derived from the configured symbols and the
stream limit; `ensure_active_universes` reports `equity_status="static"` and
`parayabuck doctor` prints `universe equity static`. Backtests build the equity
point-in-time universe with
`build_static_equity_point_in_time_universe` — each configured symbol joins at
its first bar. `build_trailing_point_in_time_universe` is crypto-only.

Managed positions and open managed orders are pinned into the universe
regardless of configuration: the bot must always be able to observe, rebalance
and exit what it owns. `_pin_managed_symbols` does this at startup, which is how
leftovers of the removed V3/sleeve identities stay observable until sold.

## 2. Eligibility vs alpha (scanner ≠ strategy)

The crypto scanner answers ONE question: **"which assets are sufficiently
tradeable and observable?"** It scores tradability, liquidity, spread quality,
data completeness, reasonable volatility and market eligibility. It does NOT
answer "which asset will make money" — that is the strategy's job. For equity
there is no eligibility stage: membership is configuration, reviewed in git.

- Eligibility filtering happens entirely before alpha scoring.
- Crypto scanner failure is fail-closed: no scanner result → no new trades
  (and no live authorization) unless `universe.crypto.allow_static_fallback_trading`
  is set.
- Scanner metrics are NEVER an alpha input (volume is not a momentum signal).

## 3. Observation stream vs positions

The stream limit is NOT the opportunity universe — this is implemented, not
only documented:

```
crypto: all supported USD pairs
        ↓ eligibility scanner (tradability-first)
crypto candidate pool
        ↓ pinned symbols + the strategy's own alpha ranking
streamed (~12 crypto)

equity: configured universe.equity.symbols
        ↓ runtime managed-position pins
streamed (≤30 equity)
positions
```

Mechanics:

- The crypto scanner is eligibility-first: `selected_symbols` is the eligible
  POOL (every candidate that passes tradability filters, plus pinned symbols) —
  deliberately larger than the stream capacity. `observation_set` on the
  scanner result is the quality-ranked stream set (with
  category/diversity limits), used when no strategy ranker exists.
- A strategy that ranks the pool ranks it through
  `PortfolioStrategy.rank_candidates`, which reuses the EXACT scoring code of
  `decide` (BACKTEST/PAPER/LIVE parity), so its live observation set is
  selected by the same alpha it trades. The current equity identity trades a
  single holding (QLD) and does not rank anything.
- `build_observation_set` (universe/observation_set.py) produces the streamed
  set: pinned symbols (configured equity symbols, crypto pins, managed
  positions) first, then the alpha-top candidates up to
  `maximum_stream_symbols`. For crypto, scanner-quality fallback names fill a
  shortfall only when they meet the strategy minimum-history window and align
  with the benchmark's latest closed timestamp; young or stale names leave
  stream capacity empty so one fallback cannot wedge the whole decision batch.
- The crypto universe refresh fetches strategy-warmup batch history for the
  pool (`bars_to_calendar_days`), ranks it, and persists `observation_set` in
  `data/runtime/active_crypto_universe.json`. `load_active_crypto_symbols`
  streams the OBSERVATION SET; `validate_active_crypto_universe` enforces it ⊆
  pool, ≤ capacity, freshness and `scanner_config_digest` match.
- In backtests the crypto point-in-time universe snapshots carry the eligible
  POOL plus the snapshot's OBSERVATION SET. The engine's completeness gate
  follows the observation set (live parity): decisions require a closed bar for
  every STREAMED symbol, never for every pool member — a halted/absent pool
  member must not block the whole market.
- `maximum_stream_symbols` is a streaming-load limit, not a universe size. For
  equity the only way to widen the universe is to add to
  `universe.equity.symbols`.

## 4. Timeframe ownership

There is NO global strategy timeframe. A strategy is not assumed valid on
every resolution. The independent cadences are:

| Cadence | Owner | Example |
|---|---|---|
| scanner timeframe | crypto scanner config | crypto 1h |
| strategy signal timeframe | the strategy (`StrategySpec.primary_timeframe`) | equity 1d / crypto 1d |
| rebalance / decision cadence | the strategy (`StrategySpec.rebalance_policy`) | monthly / weekly |
| execution timeframe | `runtime.execution_timeframe` | 5m |
| account polling | `runtime` engine config | seconds |
| reconciliation | runtime | periodic |

A strategy declares its contract through `StrategySpec`:

```python
StrategySpec(
    name="equity_leveraged_trend_v1",
    display_name="Leveraged index trend v1",
    market=Market.EQUITY,
    primary_timeframe="1d",
    supported_timeframes=("1d",),  # ONLY validated variants
    required_timeframes=(),  # extra series the strategy needs
    rebalance_policy="hours:24",
    minimum_history_bars=200,
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
   default. Example: selecting `crypto_low_variance_v1` with no timeframe
   resolves `1d`; `timeframe: 4h` fails at load time (low-variance
   validates only 1d).
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

For the current PAPER equity identity, `maximum_allocation=0.93` is a single
market allocation with no internal composition; crypto remains
`maximum_allocation=0.05`.

Universe isolation: the equity universe is static and never fails a refresh;
only the crypto scanner can fail, and a failed crypto scan does not block
equity decisions.

## 6. Portfolio / risk boundary

Strategies output `TargetPortfolio` weights in [0,1] (long-only). The
portfolio/risk layer is the ONLY authority that:

- enforces account-level gross exposure and the cash buffer;
- enforces per-market sleeve and per-asset caps;
- enforces the account-wide `max_positions` count across equity and crypto,
  plus the crypto-specific `max_crypto_positions` count;
- orders SELL before BUY and sizes against the fresh account;
- halts entries on daily-loss / drawdown breaches (production; backtest
  records shadow events and keeps trading); a drawdown halt persists until an
  operator risk-epoch reset;
- persists decisions, orders and rebalance phases so restart recovery
  resumes exactly (idempotency, two-phase rebalance, no duplicate orders).

Strategies never see the account beyond `equity`/`holdings`/`prices` in the
`PortfolioContext`; they never place orders.

## 7. Offline tooling boundary

Backtest, optimization and diagnostics are finite, optional workflows. They
reuse the production strategy, universe, cost and risk code but never write
runtime state or authorize PAPER/LIVE. The runtime does not read generated
reports or benchmark output; `config/settings.yaml` and the product factory
are the strategy-selection authorities.

## 8. Strategy registry (current PAPER leveraged-trend release)

**The complete registry** — `_PORTFOLIO_STRATEGY_FACTORIES` and
`supported_portfolio_strategies()` (config-selectable):

| name | market | primary timeframe | supported | rebalance | status |
|---|---|---|---|---|---|
| `equity_leveraged_trend_v1` | equity | 1d | 1d | hours:24 (daily; QQQ 200-session SMA filter, 35% vol target) | PAPER production selection (2026-09-25) |
| `crypto_low_variance_v1` | crypto | 1d | 1d | hours:168 | frozen historical baseline |
| `crypto_low_variance_v2` | crypto | 1d | 1d | hours:96 | frozen prior PAPER selection (superseded 2026-08-26) |
| `crypto_low_variance_hysteresis_v1` | crypto | 1d | 1d | hours:96 (retention rank 4, lookback 22, top2, alloc 0.05) | PAPER production selection (2026-08-26) |

Unknown strategy names are rejected with
`Unsupported portfolio strategy: <name>`. The current equity identity's
display name is **Leveraged index trend v1**: it holds QLD while QQQ's
completed close is above its 200-session SMA, sized to a 35% volatility
target, and holds cash otherwise. Its module is
`src/parayabuck/portfolio/equity_leveraged_trend.py`; see the
[profit maximization v1 evidence](research/profit_max_v1.md).

Removed 2026-09-25 and recoverable from git history (`5d61c8c` on
`profit-max-v1`, `ec4e2df` on `main`): `equity_residual_momentum_v1/v2/v3`,
`equity_residual_momentum_v3_dividend_sleeve_v1`, the static-sleeve
composition in the risk overlay, `risk.slot_exempt_equity_symbols`, and
`universe.equity.sleeve_trade_symbols`. Rollback restores
`config/settings.yaml` and the removed modules from `ec4e2df`.

Versioning: strategies are immutable once validated; a change to the signal
specification is a NEW named strategy, never a silent parameter mutation.
