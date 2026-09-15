# ParayaBuck

> **Project status — production-only surface**
>
> - **Equity:** `equity_residual_momentum_v3` — 1d signal, weekly rebalance, 0.55 allocation; positive 12-1 gate, multi-horizon consensus, inverse-vol sizing, **LIVE disabled**
> - **Crypto:** `crypto_low_variance_hysteresis_v1` — 1d signal, 96h rebalance, 0.05 allocation; validated low-variance hysteresis product identity
> - **Dataset:** point-in-time historical manifests and corporate-action data remain under `data/historical/`; large provider datasets are local/ignored

Automated multi-market Alpaca trading bot: dynamic universe discovery,
validated portfolio strategies, strict risk overlay, and deterministic
execution through the same pipeline in backtest, paper, and live modes.

Live trading is disabled by default. Supplying Alpaca credentials alone cannot
enable it (see [Enabling live safely](#enabling-live-safely)).

## Current PAPER strategies (2026-08-26)

The PAPER product runs two validated identities (see `docs/validated_strategies.md`):

| Market | Strategy | Signal | Rebalance | Allocation |
|---|---|---|---|---|
| Equity | Residual Momentum v3 (`equity_residual_momentum_v3`) | 1d | weekly | 0.55 |
| Crypto | Low Realized Variance Hysteresis v1 (`crypto_low_variance_hysteresis_v1`) — retention rank 4, lookback 22, top2, 96h | 1d | 96h | 0.05 |

Historical baselines `equity_residual_momentum_v1` (monthly),
`equity_residual_momentum_v2` (weekly), and `crypto_low_variance_v1` (168h)
remain constructible for controlled backtests.

Backtest economics do not imply forward live returns; controlled PAPER
operation remains the gate before any live consideration.

## Architecture

```text
Alpaca assets/data -> eligibility filters -> dynamic scanner -> active universe
  -> production strategy -> target portfolio -> risk overlay -> order planner
  -> historical broker / Alpaca paper / explicitly authorized Alpaca live
```

- One `PortfolioStrategy` interface and one target/risk meaning across
  backtest, paper, and live.
- Dynamic universes (equity ~300-candidate shortlist → ~30 streamed; crypto
  from supported USD pairs) rebuilt point-in-time by the production scanner;
  static symbols are fallback/allowlist/denylist inputs only.
- Strict risk overlay: allocation sleeves (0.55 equity / 0.05 crypto), risk caps 0.10 crypto exposure / 0.05 crypto position / max 2 crypto positions, gross cap 0.60, cash buffer 0.40, per-position and count caps, daily-loss 0.5% and drawdown 5% latches, stale-account pauses, ownership-drift halts, duplicate order/decision protection.
- Startup catch-up: a fresh mid-period deployment decides immediately on the
  latest closed warmup bars (`decision_origin: startup_catch_up`) instead of
  sitting in cash until the next cadence boundary. Restart-safe — never
  re-enters after any prior decision.
- Operator dashboard (PAPER/LIVE badge, strategy cards, positions, orders,
  fills, risk state, catch-up status) served loopback-only.
- Persisted SQLite state under `data/runtime/` (sessions, decisions, orders,
  fills, high-water, reconciliation) with online backups
  (`parayabuck backup`).

## Installation

Python 3.12 or newer is required.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Put paper-account credentials in `.env`; never put keys in YAML or commit `.env`.

## Running (paper)

```bash
.venv/bin/parayabuck web
```

Open `http://127.0.0.1:8000`. The engine auto-starts when `runtime.mode` is
`paper` and paper credentials are present. The control console stays bound to
loopback; see `docs/operations.md` (Docker, backup, incident recovery) and
`docs/deployment.md` (systemd on Google Cloud, update and rollback
procedures).

Configuration lives in `config/settings.yaml` (`runtime.mode`, universe,
`strategy.equity` / `strategy.crypto`, `risk`, `backtest`, `live`, engine,
execution, backup, alerting). The strategy block selects the current PAPER
identity and cadence — see the file header comments.

## Backtesting

```bash
.venv/bin/parayabuck backtest --market equity --data bars.csv
.venv/bin/parayabuck backtest --market crypto --data bars.csv --cost-stress
```

CSV columns are `timestamp,symbol,open,high,low,close,volume` plus optional
`market,timeframe`. Downloads (`--start/--end`) use the configured Alpaca
credentials; equity history defaults to the broad eligible universe so
delisted names are still evaluated point-in-time. `--cost-stress` repeats at
1x/2x/3x costs. Equity regulatory fees follow an effective-dated schedule;
crypto backtests use Alpaca's tier-1 taker rate (25 bps). Reports include
return, CAGR, Sharpe, Sortino, max drawdown, Calmar, profit factor, costs,
exposure, and SPY comparison. Survivorship limits are disclosed in reports.

## Enabling live safely

Live requires ALL of the following at the same time:

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
invalidates the approval. Live activation is never performed automatically.

## Verification and quality gates

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/python -m pytest -q
.venv/bin/python -m build
.venv/bin/python scripts/run_paper_chaos.py   # offline fake-broker fault injection
```

Offline paper-safety is proven by the paper-chaos gate (fake broker, local
SQLite, no network). A real Alpaca PAPER lifecycle smoke
(`scripts/paper_smoke.py`) requires the explicit opt-in
`PARAYABUCK_ALLOW_PAPER_SMOKE=YES` and is structurally incapable of touching
live.

## Documentation

- `docs/product_foundation.md` — frozen product principles
- `docs/validated_strategies.md` — validated specs: v1 baselines + v2/v3 identities
- `docs/strategy_architecture.md` — universe/scanner/strategy/risk contract
- `docs/paper_operations.md` — PAPER runtime contract and forward rules
- `docs/operations.md` — state, backup/restore, Docker, incident reconciliation
- `docs/deployment.md` — systemd on Google Cloud, update + rollback
- `docs/dashboard.md` / `docs/alerting.md` — operator surfaces
- `benchmarks/parity_guard.json` — machine-readable freeze (head + SHAs + parity hashes)
- `docs/deferred_refactor_seams.md` — intentionally deferred architecture seams
- `docs/alpaca_broker_truth_matrix.md` — broker/execution truth matrix
