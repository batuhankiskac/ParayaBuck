# Strategy architecture

The product foundation (broker, execution, ownership, reconciliation,
persistence, safety, web app, PAPER/LIVE separation) is frozen
(`docs/product_foundation.md`). This is the contract of the layer that may
evolve: universe, strategy, timeframes and the portfolio/risk boundary.

## 1. Universe

Static. `universe.equity.symbols` = `[SPY, QQQ, QLD]`, all streamed (IEX) and
tradable within `maximum_stream_symbols` (30, the Alpaca IEX limit). QQQ is the
VT35 signal, QLD its only holding, SPY the benchmark. There is no scanner, no
candidate scoring and no universe artifact; membership is configuration,
reviewed in git. The only way to widen it is to add symbols to that list.

- Policy identity: `parayabuck.universe.equity.equity_universe_digest`
  (version `equity-static-v1`), from the symbols and the stream limit.
  `parayabuck doctor` prints `universe equity static`.
- Managed pins: positions and open managed orders outside the configured
  symbols are pinned into the stream at startup (`_pin_managed_symbols`) so the
  bot can always observe and exit what it owns. Configured symbols plus pins
  above the stream limit fail startup.
- Crypto positions in the shared account are never pinned, streamed or
  traded; startup emits a `risk_incident` alert and continues.
- Backtests use `build_static_equity_point_in_time_universe`: each configured
  symbol joins at its first bar.

## 2. Timeframes

There is no global strategy timeframe; each strategy declares its own.

| Cadence | Owner | Value |
|---|---|---|
| Signal timeframe | strategy (`primary_timeframe`) | 1d |
| Decision cadence | strategy (`rebalance_policy`) | `hours:24` (every session) |
| Stream / execution timeframe | strategy signal cadence (VT35: 1d bars) | 1d |
| Account polling | `engine.account_poll_interval_seconds` | 15 s |
| Reconciliation | runtime | 60 s |

VT35's spec: `name="equity_leveraged_trend_v1"`, `display_name="Leveraged index
trend v1"`, market equity, `supported_timeframes=("1d",)`, `hours:24`,
`minimum_history_bars = max(sma_bars, vol_window + 1)` = 200 under
`trend_rule: sma`; the E2 vote (`trend_rule: majority_sma_tsm_don`, strategy
version 1.1.0) adds `tsm_bars + 1` and `donchian_bars`, so 253, and the shipped
R2 early re-entry path (`early_reentry_weight: 0.5`, strategy version 1.2.0)
needs the replay's first 252-session vol median, so 397.

Rules:

1. A strategy never runs outside its declared timeframes; config, runtime and
   backtest reject it (`validate_strategy_timeframe`).
2. `strategy.*.timeframe` in YAML is deprecated: validated against the
   strategy and rejected on mismatch; when absent, the strategy's primary
   timeframe applies.
3. Backtest, PAPER and LIVE share the class, the spec, bar finalization
   (decide on completed bars, fill on the next tradable bar — no same-bar
   lookahead) and the warmup length.
4. An equity daily bar is complete only after 16:00 ET of its session
   (`equity_day_bar_is_closed`). Provider daily bars for a session still
   trading are dropped from the signal history
   (`prune_unfinished_signal_sessions`), so a restart mid-session never decides
   on a partial bar.
5. One decision per NYSE session. Daily bars are matched by New York session
   date, not label: the stream labels a session 09:30 ET, the provider (and
   the SIP session-close fallback) 00:00 ET, and the runtime gives every tail
   of one session a single label (the newest present) before deciding. A
   decision already persisted for the session under either label is a
   duplicate.
6. Only the symbols the strategy reads (`strategy.decision_symbols`; VT35:
   QQQ and QLD) must be present for the session. SPY never blocks a decision
   and a stale SPY close is not a context price. An incomplete batch decides
   nothing and does NOT consume the session: the late symbol completes it.
   A failure before the decision is persisted does not consume it either.
7. An unverified signal basis (`signal_adjustment_unverified`) holds the
   current position for the session: no BUY and no SELL.

## 3. Portfolio / risk boundary

Strategies return long-only `TargetPortfolio` weights in [0, 1]; they see only
`equity`, `holdings` and `prices` in `PortfolioContext` and never place orders.
The portfolio/risk layer alone:

- caps the market allocation (`strategy.equity.maximum_allocation`, 0.98),
  per-asset weight, position count, gross exposure and the cash buffer against
  the current full account;
- sells before it buys and sizes buys against a fresh account snapshot;
- latches entries on daily-loss / drawdown breaches (production; backtests
  record shadow events and keep trading); a drawdown halt persists until an
  operator risk-epoch reset;
- persists decisions, orders and rebalance phases so restarts resume exactly
  (idempotent decisions, two-phase rebalance, no duplicate orders).

## 4. Registry

`PRODUCT_STRATEGY_NAMES` = `("equity_leveraged_trend_v1",)`. Any
other name fails with `Unsupported portfolio strategy: <name>`. Strategies are
immutable once validated: a signal change is a new named strategy
(`docs/validated_strategies.md`).

## 5. Offline tooling

Backtest, optimization and research reuse the production strategy, universe,
cost and risk code but never write runtime state or authorize PAPER/LIVE. The
runtime never reads generated reports; `config/settings.yaml` and the product
factory select the strategy.
