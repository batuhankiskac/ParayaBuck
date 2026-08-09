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

There is one strategy interface (`PortfolioStrategy.decide`) and one target-to-
order pipeline. Strategy code never receives credentials or submits orders.
Scanner, strategy, execution, and risk cadences are independently configured.

The shipped strategies are deliberately limited to two:

- `equity_dynamic_trend_1d_v1`: daily absolute trend, trailing momentum rank,
  top-N selection, inverse-volatility sizing, and portfolio volatility target.
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

Static symbols are fallback/allowlist/denylist inputs only. Every refresh writes
timestamps, candidates, selections, score breakdowns, elimination reasons,
config digest, and managed-position pins. Open positions with portfolio order
lineage are retained even when the latest rank excludes them.

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

The historical universe is rebuilt only from information available at each
snapshot. Signals use finalized closes and orders fill no earlier than the next
bar open. Cash, fractional rules, fees, spreads, slippage, partial fills, market
sessions, risk limits, max positions, exposure, and warmup are modeled.

Each run writes `report.json`, summary/rebalance/fill/equity/drawdown CSVs, a
historical-universe snapshot, and a SQLite run record. Reports include period,
initial/final equity, return, CAGR, Sharpe, Sortino, max drawdown, Calmar, trade
statistics, holding time, costs, exposure/position statistics, and SPY comparison
when benchmark bars exist. `--cost-stress` repeats at 1x/2x/3x costs.

Current Alpaca asset membership cannot reconstruct every delisted historical
security. Reports explicitly disclose this survivorship limitation.

## Optimizer and out-of-sample evaluation

Grid search ranks risk-adjusted performance rather than raw return:

```bash
.venv/bin/parayabuck optimize --market equity --data bars.csv \
  --grid trend_window=100,150,200 --grid top_n=4,6,8 \
  --train-start 2020-01-01 --train-end 2024-12-31 \
  --oos-start 2025-01-01 --oos-end 2026-07-31
```

The selected TRAIN parameters are evaluated once on OOS. Optimization JSON lists
return, CAGR, Sharpe, Sortino, drawdown, profit factor, trades, ranking score, and
the separate OOS result.

## Risk controls

Risk has final authority over strategy targets. Controls include maximum account
allocation, per-position and crypto caps, maximum gross exposure, cash buffer,
sector exposure, maximum positions, daily loss, high-water drawdown, hard live
dollar cap, duplicate decision/order protection, approved-universe enforcement,
stale/gapped data halts, broker reconciliation, and persistent high-water state.

## Enabling live safely

Live requires all of the following at the same time:

1. `runtime.mode: live`
2. `live.enabled: true`
3. a positive `live.hard_dollar_cap`
4. live Alpaca credentials
5. `PARAYABUCK_ENABLE_LIVE=YES`
6. a short-lived HMAC approval bound to the live account, exact strategy/universe
   configuration digests, and dollar cap
7. a fresh Web UI account-suffix confirmation before start

Create the signed file with a 32+ character secret in
`PARAYABUCK_OPERATOR_APPROVAL_KEY`:

```bash
.venv/bin/parayabuck authorize-live --operator YOUR_NAME --expires-minutes 15
```

This performs read-only live account verification; it does not place an order.
Changing the account, strategy parameters, active universe, allocation, or mode
invalidates the approval. Live activation is never performed automatically.

## State, recovery, and incident accounting

SQLite under `data/runtime/` persists sessions, decisions, orders, fills,
high-water equity, and reconciliation state. Universe snapshots live under
`data/runtime/` and `data/universe/`; reports live under `reports/`.

See [operations documentation](docs/operations.md) for backup/restore, Docker,
restart recovery, and the paper incident command. Incident reports reconcile:

```text
starting equity + realized PnL + unrealized PnL - fees + net transfers
= expected ending equity
actual ending equity - expected ending equity = residual
```

## Docker

```bash
docker build -t parayabuck .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 \
  -v "$PWD/data:/app/data" -v "$PWD/reports:/app/reports" parayabuck
```

The image runs non-root, stores state on mounts, handles SIGTERM cleanly, and has
a `/healthz` health check. Keep the control console bound to loopback.

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
