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
| Stream / execution timeframe | `runtime.execution_timeframe` | 5m |
| Account polling | `engine.account_poll_interval_seconds` | 15 s |
| Reconciliation | runtime | 60 s |

VT35's spec: `name="equity_leveraged_trend_v1"`, `display_name="Leveraged index
trend v1"`, market equity, `supported_timeframes=("1d",)`, `hours:24`,
`minimum_history_bars = max(sma_bars, vol_window + 1)` = 200.

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
5. Incomplete bar batches skip the decision; a timed-out partial batch never
   silently delays it.

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

`supported_portfolio_strategies()` = `{"equity_leveraged_trend_v1"}`. Any
other name fails with `Unsupported portfolio strategy: <name>`. Strategies are
immutable once validated: a signal change is a new named strategy
(`docs/validated_strategies.md`).

## 5. Offline tooling

Backtest, optimization and research reuse the production strategy, universe,
cost and risk code but never write runtime state or authorize PAPER/LIVE. The
runtime never reads generated reports; `config/settings.yaml` and the product
factory select the strategy.
