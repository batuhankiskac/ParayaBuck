# ParayaBuck

> **Project status — production-only surface**
>
> - **Equity:** `equity_residual_momentum_v3_dividend_sleeve_v1` (Residual Momentum v3 + dividend-growth sleeve) — 0.55 maximum allocation split internally into the unchanged weekly V3 3.0.0 core (`core_allocation: 0.40`) and a static SCHD/VIG/DGRO sleeve (`sleeve_allocation: 0.15`, 0.05 each), selected on the first weekly decision of each calendar month or while empty and otherwise held; **LIVE disabled**
> - **Crypto:** `crypto_low_variance_hysteresis_v1` — 1d signal, 96h rebalance, 0.05 allocation; validated low-variance hysteresis product identity
> - **Dataset:** point-in-time historical manifests and corporate-action data remain under `data/historical/`; large provider datasets are local/ignored

Automated multi-market Alpaca trading bot: dynamic universe discovery,
validated portfolio strategies, strict risk overlay, and deterministic
execution through the same pipeline in backtest, paper, and live modes.

Live trading is disabled by default. Supplying Alpaca credentials alone cannot
enable it (see [Enabling live safely](#enabling-live-safely)).

## Current PAPER strategies

The PAPER product runs the current validated equity and crypto identities
(see `docs/validated_strategies.md` and the
[dividend-sleeve production evidence](docs/research/dividend_sleeve_production_v1.md)):

| Market | Strategy | Signal | Rebalance | Allocation |
|---|---|---|---|---|
| Equity | Residual Momentum v3 + dividend-growth sleeve (`equity_residual_momentum_v3_dividend_sleeve_v1`) | 1d | weekly core; sleeve selected on the first weekly decision of each calendar month or while empty | 0.55 total = 0.40 core + 0.15 sleeve |
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
- Dynamic universes (equity ~300-candidate shortlist → 30-symbol IEX stream;
  crypto from supported USD pairs) are rebuilt point-in-time by the production
  scanner. `universe.equity.sleeve_trade_symbols` pins SCHD/VIG/DGRO in the
  equity stream; they are hidden from V3 scoring, leaving 23 ranked core slots
  instead of 26. Other static symbols remain fallback/allowlist/denylist inputs.
- Strict risk overlay: market allocations remain 0.55 equity / 0.05 crypto.
  Equity is split internally into the 0.40 V3 core and 0.15 dividend sleeve;
  `risk.slot_exempt_equity_symbols` keeps the sleeve outside the 8-position cap.
  Daily-loss, drawdown, entry-latch, and intermediate-reduction latches never
  grow the sleeve. Crypto limits remain
  0.10 exposure / 0.05 per position / 2 positions; gross cap 0.60, cash buffer
  0.40, stale-account pauses, ownership-drift halts, and duplicate
  order/decision protection remain unchanged.
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
execution, backup, alerting). The current equity selection is
`equity_residual_momentum_v3_dividend_sleeve_v1`; its sleeve uses
`core_allocation`, `sleeve_allocation`, `sleeve_symbols`,
`universe.equity.sleeve_trade_symbols`, and
`risk.slot_exempt_equity_symbols`. Rollback selects the still-registered
`equity_residual_momentum_v3` and removes the three sleeve parameters and both
sleeve symbol lists.

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
- `docs/validated_strategies.md` — frozen baselines and current validated identities
- `docs/research/dividend_sleeve_production_v1.md` — current dividend-growth sleeve evidence
- `docs/strategy_architecture.md` — universe/scanner/strategy/risk contract
- `docs/paper_operations.md` — PAPER runtime contract and forward rules
- `docs/operations.md` — state, backup/restore, Docker, incident reconciliation
- `docs/deployment.md` — systemd on Google Cloud, update + rollback
- `docs/dashboard.md` / `docs/alerting.md` — operator surfaces
- `benchmarks/parity_guard.json` — machine-readable freeze (head + SHAs + parity hashes)
- `docs/deferred_refactor_seams.md` — intentionally deferred architecture seams
- `docs/alpaca_broker_truth_matrix.md` — broker/execution truth matrix
