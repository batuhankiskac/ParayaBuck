# ParayaBuck

ParayaBuck is a production-oriented Alpaca trading bot for US equities and
crypto. It discovers assets dynamically, builds a small active universe, emits
portfolio target weights, applies risk constraints, and routes deterministic
orders through the same pipeline in backtest, paper, and live modes.

Live trading is disabled by default. Supplying Alpaca credentials alone cannot
enable it.

## Architecture

```text
Alpaca assets/data -> eligibility filters -> dynamic scanner -> active universe
  -> production strategy -> target portfolio -> risk overlay -> order planner
  -> historical broker / Alpaca paper / explicitly authorized Alpaca live
```

There is one strategy interface (`PortfolioStrategy.decide`) and one shared
target/risk meaning. Historical fills use a deterministic simulator while
paper/live orders use the broker planner. Strategy code never receives
credentials or submits orders. Operational, market-state observation, and
strategy timeframes have explicit roles; an entry-risk latch (daily loss,
drawdown, account freshness) blocks entries while risk-reducing exits keep
flowing.

The shipped strategies are deliberately limited to two:

- `equity_dynamic_trend_1d_v1`: daily absolute trend, trailing momentum rank,
  top-N selection, inverse-volatility sizing, and a conservative positive-
  correlation-floor portfolio volatility target.
- `crypto_cross_sectional_momentum_1h_v1`: hourly cross-sectional momentum,
  BTC regime filter, volatility filtering/sizing, and configurable 24/7 rebalance.

## Installation

Python 3.11 or newer is required.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Put paper-account credentials in `.env`; never put keys in YAML or commit `.env`.

## Configuration

The main file is `config/settings.yaml`. Its product-level sections are:

- `runtime.mode`: `disabled`, `paper`, or `live` (`backtest` is CLI-only)
- `universe.equity` / `universe.crypto`: discovery, filters, capacities, snapshots
- `strategy.equity` / `strategy.crypto`: selected strategy and all parameters
- `risk`: gross, position, cash, daily-loss, drawdown, and count limits
- `backtest`: costs, initial cash, scanner cadence, and output
- `live`: disabled flag, hard dollar cap, and signed-approval settings

The default is paper mode with the equity strategy enabled. Crypto discovery is
enabled, while crypto trading is disabled until its strategy is explicitly
enabled.

## Universe selection

Equity discovery starts from Alpaca's active, tradable US assets and supported
exchanges—not from a repository symbol list. Cheap eligibility and historical
liquidity filters reduce thousands of assets to a configurable ~300-candidate
shortlist; the scanner selects up to the stream capacity (30 by default).

Crypto discovery starts from Alpaca-supported USD pairs, excludes stablecoins,
inactive/non-tradable assets and poor data, then applies liquidity, spread,
volatility, BTC-correlation, and category-concentration checks.

Static symbols are fallback/allowlist/denylist inputs only. Static fallback
trading during a dynamic-scanner failure is disabled unless explicitly enabled.
Every refresh writes
timestamps, candidates, selections, score breakdowns, elimination reasons,
and config digest to both the latest snapshot and append-only history. The
runtime separately records managed pins and warming members. Open positions and
open-order symbols are retained even when the latest rank excludes them.

## Alpaca paper setup and operation

Keep this safe default:

```yaml
runtime:
  mode: paper
live:
  enabled: false
```

Then run:

```bash
.venv/bin/parayabuck web
```

Open `http://127.0.0.1:8000`. The operations console displays mode, connection
health, configured parameters, active universes and scores, positions, pending
orders, recent fills, account PnL/exposure, risk state, last decision, scanner
refresh, and reconciliation time. Paper and live are visually distinct.

Paper startup requires valid paper credentials, config, scanner/universe data,
and operational state. It does not require a research manifest or calendar wait.

## Backtesting

Use a CSV offline or let the CLI download Alpaca bars with configured credentials.
CSV columns are `timestamp,symbol,open,high,low,close,volume` plus optional
`market,timeframe`.

```bash
.venv/bin/parayabuck backtest \
  --market equity --start 2022-01-01 --end 2025-12-31 \
  --param top_n=6 --param trend_window=150 --cost-stress
```

Offline:

```bash
.venv/bin/parayabuck backtest --market equity --data bars.csv
```

Equity downloads default to the broad eligible universe (every active tradable
US equity on the configured exchanges) rather than today's shortlist, so a
symbol that was liquid in 2022 but absent from the current universe is still
evaluated. Each historical snapshot then applies the same Stage-A shortlist
(trailing median-dollar-volume leaders up to `universe.equity.target_shortlist`)
before the production scanner runs, matching the live pipeline. Use
`--candidate-limit N` to cap the download set for resource-limited runs, or
`--symbols AAPL,MSFT` to download an explicit list. When `--start` is given,
history before that date is downloaded automatically to cover strategy warmup
and the trailing scanner lookback; evaluation starts exactly at `--start`.

The historical universe is rebuilt only from information available at each
snapshot. Signals use finalized closes and orders fill no earlier than the next
bar open. Cash, fractional rules, fees, spreads, slippage, partial fills, market
basic equity session validity (weekends and regular intraday hours), risk limits,
max positions, exposure, and strategy-declared warmup are modeled. Exchange
holiday correctness depends on the supplied/provider data calendar.

Equity downloads use split-adjusted prices. Optional explicit dividend cash
flows can be supplied with `--corporate-actions`; split actions are rejected in
that mode to prevent double counting. Equity regulatory fees (SEC Section 31,
FINRA TAF, CAT NMS Plan) follow an effective-dated schedule by trade date by
default (fixed 2026-rate fallbacks are configurable under `backtest`); the
schedule is an approximation, never exact historical fees, and reports say so.
Crypto backtests default to Alpaca's tier-1 taker rate (25 bps) because crypto
execution uses market orders.

Data feed terminology: `alpaca.stock_historical_feed` (default `sip`) drives
historical downloads and falls back to `iex` gracefully when the account lacks
the SIP entitlement; `alpaca.stock_stream_feed` (default `iex`) drives live
Basic streaming. IEX prints reflect one exchange, not the consolidated
SIP/NBBO tape, so liquidity/spread proxies derived from IEX understate
full-market conditions; backtest and scanner reports label the feed used.

Each run writes `report.json`, summary/rebalance/fill/equity/drawdown CSVs, a
historical-universe snapshot, and a SQLite run record. Reports include period,
initial/final equity, return, CAGR, Sharpe, Sortino, max drawdown, Calmar, trade
statistics, holding time, costs, exposure/position statistics, and SPY comparison
when benchmark bars exist. `--cost-stress` repeats at 1x/2x/3x costs.

Current Alpaca asset membership cannot reconstruct every delisted historical
security. Reports explicitly disclose this survivorship limitation.

## Optimizer and out-of-sample evaluation

Grid search uses `backtest.ranking_metric` (`risk_adjusted`, `sharpe`, `sortino`,
or `calmar`) rather than raw return:

```bash
.venv/bin/parayabuck optimize --market equity --data bars.csv \
  --grid trend_window=100,150,200 --grid top_n=4,6,8 \
  --train-start 2020-01-01 --train-end 2024-12-31 \
  --oos-start 2025-01-01 --oos-end 2026-07-31
```

TRAIN and OOS windows must be ordered and non-overlapping. Trial runs stay
in-memory without full report directories, grids are capped at 256 combinations,
and the selected TRAIN parameters are evaluated once on OOS. Optimization JSON lists
return, CAGR, Sharpe, Sortino, drawdown, profit factor, trades, ranking score, and
the separate OOS result.

## Risk controls

Risk has final authority over strategy targets. Controls include maximum account
allocation, per-position and crypto caps, maximum gross exposure, cash buffer,
maximum positions, daily loss, high-water drawdown, hard live
dollar cap, duplicate decision/order protection, approved-universe enforcement,
stale/gapped data halts, broker reconciliation, and persistent high-water state.

## Enabling live safely

Live requires all of the following at the same time:

1. `runtime.mode: live`
2. `live.enabled: true`
3. a positive `live.hard_dollar_cap`
4. live Alpaca credentials
5. `PARAYABUCK_ENABLE_LIVE=YES`
6. a short-lived HMAC approval bound to the live account, exact strategy,
   scanner-policy, risk-policy and allocation digests, and dollar cap
7. a fresh Web UI account-suffix confirmation before start

Create the signed file with a 32+ character secret in
`PARAYABUCK_OPERATOR_APPROVAL_KEY`:

```bash
.venv/bin/parayabuck authorize-live --operator YOUR_NAME --expires-minutes 15
```

This performs read-only live account verification; it does not place an order.
Changing the account, strategy/scanner/risk policy, allocation, or mode
invalidates the approval. Ordinary scanner membership changes do not. Live
activation is never performed automatically.

## State, recovery, and incident accounting

SQLite under `data/runtime/` persists sessions, decisions, orders, fills,
high-water equity, and reconciliation state. Universe snapshots live under
`data/runtime/` and `data/universe/`; reports live under `reports/`.

See [operations documentation](docs/operations.md) for backup/restore, Docker,
restart recovery, and the paper incident command. Incident reports reconcile:

```text
starting equity + realized PnL + change in unrealized PnL - fees + net transfers
= expected ending equity
actual ending equity - expected ending equity = residual
```

## Docker

```bash
docker build -t parayabuck .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 \
  -v "$PWD/config:/app/config" -v "$PWD/data:/app/data" \
  -v "$PWD/reports:/app/reports" parayabuck
```

The image installs the same locked dependencies CI tests against
(`requirements.lock`, then `pip install . --no-deps`) and runs non-root.
`/app/config`, `/app/data` and `/app/reports` are volumes: settings saved from
the operator UI, decisions/orders/fills in SQLite, and backtest reports all
survive container recreation (an empty config volume is seeded from the
image's default settings on first start). The container handles SIGTERM
cleanly and exposes a `/healthz` liveness check and `/readyz` readiness
check. Keep the control console bound to loopback.

## Testing

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/python -m pytest -q
.venv/bin/python -m build
```

CI runs these gates across Python 3.11–3.14, audits dependencies, scans secrets,
builds the wheel, and imports the installed artifact.
