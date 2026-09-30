# ParayaBuck

Automated equity trading bot for Alpaca. One validated strategy, a strict risk
overlay and one execution path shared by backtest, PAPER and LIVE.

| | |
|---|---|
| Strategy | **VT35** — `equity_leveraged_trend_v1` (Leveraged Index Trend v1) |
| Rule | Hold QLD (2x Nasdaq-100) while QQQ's completed daily close is above its 200-session SMA, at weight `min(1, 0.35 / 20-session annualized QLD volatility)`; cash otherwise |
| Timing | Decided on the completed session bar after the 16:00 ET close; DAY market orders fill at the next regular open |
| Allocation | Equity 0.98 of account equity; 0.02 cash buffer |
| Universe | Static `[SPY, QQQ, QLD]` (QQQ signal, QLD holding, SPY benchmark) |
| Mode | PAPER on a GCP VM (systemd). **LIVE disabled.** |

The bot is equity-only. Crypto support was removed on 2026-09-30
(`docs/research/crypto_removal_v1.md`, tag `archive/code/crypto-sleeve-2026-09`);
crypto found in the shared account is never traded and raises a `risk_incident`
alert at startup. Earlier equity identities are recoverable from git history
only. Evidence for VT35: `docs/research/profit_max_v1.md`,
`docs/research/paper_config_v2.md`. Backtest returns do not imply live returns.

> **Never run the engine on the laptop.** The laptop and the VM share one
> Alpaca PAPER key; two runners corrupt the ledger (2026-09-25 incident). This
> is enforced: an engine start (auto or manual) requires
> `PARAYABUCK_TRADING_HOST=1`, which only the VM systemd unit sets, so local
> `parayabuck web` serves the console and backtests and refuses to trade. Local
> work is tests, backtests and research only.

## Architecture

```text
static equity symbols -> VT35 target portfolio -> risk overlay -> order planner
  -> historical broker (backtest) / Alpaca paper / explicitly authorized Alpaca live
```

- One `PortfolioStrategy` interface; strategy, risk and execution policy are
  identical in backtest, PAPER and LIVE.
- Risk: per-asset and gross caps 0.98, cash buffer 0.02, max 8 positions,
  0.20 daily-loss and 0.75 drawdown latches (catastrophe brakes; a drawdown
  halt persists until an operator risk-epoch reset), stale-account pause,
  ownership-drift halt, duplicate order/decision protection.
- Startup catch-up: a fresh deployment decides once on the latest completed
  bars instead of waiting for the next session; never re-enters after a
  prior decision.
- Unfinished equity daily bars (a session still trading) are dropped from the
  signal history, so a mid-session restart never decides on a partial bar.
- Operator visibility: loopback dashboard, `/readyz`, `parayabuck ops ...`,
  webhook alerts including a one-line daily summary and an intent watchdog.
- State: SQLite under `data/runtime/`, online verified backups.

## Install

Python 3.12+.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env   # credentials live only in .env, never in YAML or git
```

Configuration: `config/settings.yaml` (`runtime`, `universe`, `strategy.equity`,
`risk`, `execution`, `backtest`, `live`, `engine`, `backup`, `alerting`,
`intent_watchdog`). Old crypto keys are rejected by the loader.

## Backtest

```bash
.venv/bin/parayabuck backtest --market equity --data bars.csv --cost-stress
```

CSV columns `timestamp,symbol,open,high,low,close,volume` (optional
`market,timeframe`). `--start/--end` download `universe.equity.symbols` with
the configured Alpaca credentials. `--cost-stress` repeats at 1x/2x/3x costs;
regulatory fees follow the effective-dated schedule. Reports include CAGR,
Sharpe, Sortino, max drawdown, Calmar, costs, exposure and SPY comparison.

## Enabling live (not enabled)

Live needs all of: `runtime.mode: live`, `live.enabled: true`, a positive
`live.hard_dollar_cap`, live credentials, `PARAYABUCK_ENABLE_LIVE=YES`, a
short-lived HMAC approval (`parayabuck authorize-live --operator NAME`, secret
in `PARAYABUCK_OPERATOR_APPROVAL_KEY`) bound to account, strategy, universe,
risk and allocation digests and the dollar cap, and a fresh Web UI
account-suffix confirmation. Any config change invalidates the approval.

## Quality gates

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_paper_chaos.py   # offline fake-broker fault injection
```

Tests never touch the network, credentials or production paths. `tests/conftest.py`
enforces it: every test runs with the journal, snapshot, warmup cache, runtime
DB, receipts and the risk-peak sidecar redirected into `tmp_path`, and any test
that opens a file under the repo's `data/runtime/`, `data/cache/` or `logs/`,
or changes one, fails with an isolation error. A test that needs runtime state
must pass an explicit path (`runtime_state_dir=tmp_path`, or
`peak_state_path=tmp_path / "peak.json"`) — never rely on the cwd-relative
production default, which differs between a fresh checkout and the trading VM.

Commands that resolve their state paths from the config (`ops status`,
`ops doctor`, `ops timeline`, `ops bundle`, the web ops read model) take the
`isolated_config_path` fixture: the shipped config with its decision DB,
journal and snapshot inside `tmp_path`.

## Documentation

| Doc | Contents |
|---|---|
| `docs/deployment.md` | GCP VM, systemd units, deploy by git bundle, env/webhook, rollback |
| `docs/operations.md` | Daily cycle, health/readiness, alerts, watchdog, ops CLI, backup/restore, PAPER policy |
| `docs/operator_runbook.md` | Symptom-by-symptom incident playbooks |
| `docs/dashboard.md` | Web dashboard panels and APIs |
| `docs/validated_strategies.md` | Frozen VT35 spec and risk limits |
| `docs/strategy_architecture.md` | Universe, strategy, timeframe and risk contract |
| `docs/runtime_architecture.md` | Engine modules, startup, restart safety, persistence |
| `docs/execution_architecture.md` | Alpaca execution boundary and failure semantics |
| `docs/paper_incident_taxonomy.md` | Incident categories |
| `docs/product_foundation.md` | Frozen product principles |
| `docs/deferred_refactor_seams.md` | Deferred refactor seams |
| `docs/OPERATIONS_LOG.md` | Every deploy, config change and incident |
| `docs/research/INDEX.md` | Research evidence index |
