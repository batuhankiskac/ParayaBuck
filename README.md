# ParayaBuck

Automated equity trading bot for Alpaca. One validated strategy, a strict risk
overlay and one execution path shared by backtest, PAPER and LIVE.

**In plain words:** once a day, after the US market closes, the bot checks
whether the Nasdaq-100 (QQQ) is in an uptrend. If it is, it holds QLD, a 2x
leveraged Nasdaq-100 fund, sized down when markets are volatile. If not, it
holds cash. It runs on a paper (simulated) Alpaca account on one cloud VM;
real money is disabled. The aim is to beat buying and holding the index over
years, accepting deep drawdowns along the way (about 47 % in the 2000–2026
backtest).

**Start here.** New to the project: read this page, then
[`docs/GLOSSARY.md`](docs/GLOSSARY.md) for the short names (VT35, E2, R2,
A1u, H2, K, pp, Stage B). Then, depending on what you need:

- *What does the strategy do, exactly?* `docs/validated_strategies.md`
- *How is it run and watched?* `docs/operations.md`, then `docs/deployment.md`
- *Something is wrong on the VM:* `docs/operator_runbook.md`
- *Why this rule and not another?* `docs/research/INDEX.md`; for the
  short-term drawdown question, `docs/research/short_term_summary.md`
- *How the code is laid out:* `docs/runtime_architecture.md`,
  `docs/execution_architecture.md`

| | |
|---|---|
| Strategy | **VT35** with the E2 trend vote, the R2 early re-entry path, the A1u gain-scaled downsize band and the H2 December deferral — `equity_leveraged_trend_v1` 1.2.0 (Leveraged Index Trend v1) |
| Rule | Hold QLD (2x Nasdaq-100) while at least two of three QQQ trend votes hold on the completed daily close (close > 200-session SMA; 252-session return > 0; close > midpoint of the 126-session max/min), at weight `min(1, 0.35 / 20-session annualized QLD volatility)`; while the vote is off, hold half that weight when QQQ > SMA50, its 20-session annualized vol is under its 252-session median and QQQ > its close 20 sessions back; cash otherwise |
| Execution | A downward resize between two positive weights fills only above 0.5 / 3 / 8 pp of drift at an embedded USD FIFO gain fraction of ≤ 1 % / ≤ 5 % / > 5 % (`execution.downsize_gain_band`); entries, exits to zero, up-resizes and any decision without a consistent lot book keep the 0.5 pp band. A downsize whose fill session is one of the last 10 NYSE sessions of December is postponed to the first January decision (`execution.december_downsize_deferral_sessions`); exits, entries, up-resizes and risk-driven reductions are never deferred |
| Timing | Decided on the completed session bar after the NYSE close (16:00 ET; 13:00 ET on early-close days); DAY market orders fill at the next regular open |
| Allocation | Equity 0.98 of account equity; 0.02 cash buffer |
| Universe | Static `[SPY, QQQ, QLD]` (QQQ signal, QLD holding, SPY benchmark) |
| Mode | PAPER on a GCP VM (systemd). **LIVE disabled.** |

The bot is equity-only. Crypto support was removed on 2026-09-30
(`docs/research/crypto_removal_v1.md`, tag `archive/code/crypto-sleeve-2026-09`);
crypto found in the shared account is never traded and raises a `risk_incident`
alert at startup. Earlier equity identities are recoverable from git history
only. Evidence for VT35: `docs/research/profit_max_v1.md`,
`docs/research/paper_config_v2.md`; for the E2 trend vote:
`docs/research/e2_integration_v1.md`; for the R2 early re-entry path:
`docs/research/fast_reentry_integration_v1.md`; for the A1u downsize band and
the H2 December deferral: `docs/research/gain_band_v3.md` and
`docs/research/band_h2_combo_v1.md` (research) and
`docs/research/gain_band_integration_v1.md` (what shipped, and what is still
missing: no Stage B replay). Backtest returns do not imply
live returns.

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
  local alerts (log + event journal + health surfaces) including a one-line
  daily summary and an intent watchdog; opt-in e-mail delivery of the
  important alerts (`alerting.email`, off in the shipped config).
- State: SQLite under `data/runtime/`, online verified backups.

## Install

Python 3.12+.

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock   # runtime + dev tools
.venv/bin/python -m pip install -e . --no-deps
cp .env.example .env   # credentials live only in .env, never in YAML or git
```

`uv.lock` is the single dependency source. `requirements.lock` (runtime, used
by the VM and the Dockerfile) and `requirements-dev.lock` (the same runtime
pins plus test/lint tools, used by CI) are exports of it; CI fails when they
drift. After changing `pyproject.toml` dependencies:

```bash
uv lock
uv export --locked --no-hashes --no-emit-project --no-dev --format requirements.txt -o requirements.lock
uv export --locked --no-hashes --no-emit-project --no-dev --extra dev --format requirements.txt -o requirements-dev.lock
```

Configuration: `config/settings.yaml` (`runtime`, `universe`, `strategy.equity`,
`risk`, `execution`, `backtest`, `live`, `engine`, `backup`, `alerting`,
`intent_watchdog`). Old crypto keys are rejected by the loader.

## Backtest

```bash
.venv/bin/parayabuck backtest --market equity --data bars.csv --cost-stress
```

CSV columns `timestamp,symbol,open,high,low,close,volume` (optional
`market,timeframe`); `volume` is required because fills are capped at 10% of
the trailing average volume. A naive daily `timestamp` (e.g. `2024-01-02`) is
that New York session date. Without `--data` the run downloads
`universe.equity.symbols` with the configured Alpaca credentials: `--start`
plus the strategy warmup before it, or the last 5 years when `--start` is
omitted. Without `--start` the evaluation window opens on the first session on
which every configured symbol has the strategy's minimum history (397 sessions
for the shipped identity, 200 for VT35 without the vote); the earlier bars are
warmup and do not count toward CAGR/Sharpe.
`--cost-stress` repeats at 1x/2x/3x costs; regulatory fees follow the
effective-dated schedule. Reports include CAGR, Sharpe, Sortino, max drawdown,
Calmar, costs, exposure, SPY comparison and `rejected_notional` per decision
(orders blocked by unknown volume or limited by cash at a gap-up open).

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
| `docs/deployment.md` | GCP VM, systemd units, deploy by git bundle, env file, rollback |
| `docs/operations.md` | Daily cycle, health/readiness, local alerts, intent watchdog, ops CLI, backup/restore, PAPER policy |
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
| `docs/GLOSSARY.md` | Short names used across the docs |
| `docs/research/INDEX.md` | Research evidence index |
| `docs/research/short_term_summary.md` | Short-term drawdown question: what was tested and what is left |
