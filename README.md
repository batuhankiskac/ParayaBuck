# ParayaBuck

> **Project status — production-only surface**
>
> - **Equity:** `equity_leveraged_trend_v1` (Leveraged Index Trend v1 — holds QLD, the 2x Nasdaq-100 ETF, while QQQ's completed close is above its 200-session SMA, sized to a 35% volatility target) — 0.98 maximum allocation, decided every session, cash otherwise; **LIVE disabled**
> - **Crypto:** removed 2026-09-30 (equity-only; `docs/research/crypto_removal_v1.md`, tag `archive/code/crypto-sleeve-2026-09`)
> - **Dataset:** point-in-time historical manifests and corporate-action data remain under `data/historical/`; large provider datasets are local/ignored

Automated equity Alpaca trading bot: a static equity universe, a validated
portfolio strategy, strict risk overlay, and deterministic execution through the same pipeline in backtest,
paper, and live modes.

Live trading is disabled by default. Supplying Alpaca credentials alone cannot
enable it (see [Enabling live safely](#enabling-live-safely)).

## Current PAPER strategies

The PAPER product runs the current validated equity identity
(see `docs/validated_strategies.md` and the
[profit maximization v1 evidence](docs/research/profit_max_v1.md)):

| Market | Strategy | Signal | Rebalance | Allocation |
|---|---|---|---|---|
| Equity | Leveraged Index Trend v1 (`equity_leveraged_trend_v1`) — QQQ 200-session SMA filter, `min(1, 0.35 / 20-session realized QLD volatility)` | 1d | daily (`rebalance_frequency: hours:24`) | 0.98 |

The equity target is cash when QQQ is in a downtrend, when history is
insufficient, or when QLD is unavailable. The residual-momentum
v1/v2/v3 identities and the dividend-growth and gold sleeve composition were
removed on 2026-09-25; they are recoverable from git history (`5d61c8c` on
`profit-max-v1`, `ec4e2df` on `main`) and are documented as historical, not
selectable. The crypto sleeve was disabled by `paper_config_v2` and its code
deleted on 2026-09-30 (`docs/research/crypto_removal_v1.md`). Crypto
positions found in the shared account are left untouched and raise a
`risk_incident` alert at startup.

Backtest economics do not imply forward live returns; controlled PAPER
operation remains the gate before any live consideration.

## Architecture

```text
Alpaca assets/data -> static equity symbols -> active universe
  -> production strategy -> target portfolio -> risk overlay -> order planner
  -> historical broker / Alpaca paper / explicitly authorized Alpaca live
```

- One `PortfolioStrategy` interface and one target/risk meaning across
  backtest, paper, and live.
- The equity universe is static: `universe.equity.symbols` is `[SPY, QQQ,
  QLD]` streamed and tradable within `maximum_stream_symbols` (30, the Alpaca
  IEX limit). QQQ is the `equity_leveraged_trend_v1` trend signal, QLD its only
  holding, SPY the benchmark. There is no equity scanner and no equity universe
  artifact; managed positions outside those symbols (e.g. leftovers of the
  removed V3/sleeve identities) are pinned into the stream by the runtime
  (`_pin_managed_symbols`) at startup until sold. Backtests use
  `build_static_equity_point_in_time_universe` — each configured symbol joins
  at its first bar.
- Strict risk overlay: the equity allocation is 0.98. Equity limits are
  0.98 per asset, 0.98 gross and a 0.02 cash buffer; the
  0.20 daily-loss and 0.75 drawdown latches are catastrophe brakes for a 2x
  index ETF, and the drawdown halt persists until an operator risk-epoch reset.
  Max positions 8, stale-account pauses, ownership-drift halts, and duplicate
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
`strategy.equity`, `risk`, `backtest`, `live`, engine,
execution, backup, alerting). The current equity selection is
`equity_leveraged_trend_v1`; its parameters are `signal_symbol`, `hold_symbol`,
`sma_bars`, `vol_target`, `vol_window` and `rebalance_frequency`, with QQQ/QLD
as ordinary tradable members of `universe.equity.symbols`. The V3 and sleeve
identities, the
static-sleeve risk-overlay composition and `risk.slot_exempt_equity_symbols`
are gone. Rollback restores `config/settings.yaml` and the removed modules
from commit `ec4e2df` (main before the switch), e.g.
`git checkout ec4e2df -- <paths>`.

## Backtesting

```bash
.venv/bin/parayabuck backtest --market equity --data bars.csv --cost-stress
```

CSV columns are `timestamp,symbol,open,high,low,close,volume` plus optional
`market,timeframe`. Downloads (`--start/--end`) use the configured Alpaca
credentials; equity history downloads the configured `universe.equity.symbols`
only (no Alpaca asset listing). `--cost-stress` repeats at
1x/2x/3x costs. Equity regulatory fees follow an effective-dated schedule.
Reports include
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
   universe-policy, risk-policy and allocation digests, and dollar cap
7. a fresh Web UI account-suffix confirmation before start

Create the signed file with a 32+ character secret in
`PARAYABUCK_OPERATOR_APPROVAL_KEY`:

```bash
.venv/bin/parayabuck authorize-live --operator YOUR_NAME --expires-minutes 15
```

This performs read-only live account verification; it does not place an order.
Changing the account, strategy/universe/risk policy, allocation, or mode
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
- `docs/research/profit_max_v1.md` — current equity identity evidence
- `docs/research/INDEX.md` — index of research evidence (pre-VT35 research pruned 2026-09-30, tag `archive/research/pre-prune-2026-09-30`)
- `docs/OPERATIONS_LOG.md` — operations journal: every deploy and config change
- `docs/strategy_architecture.md` — universe/strategy/risk contract
- `docs/paper_operations.md` — PAPER runtime contract and forward rules
- `docs/operations.md` — state, backup/restore, Docker, incident reconciliation
- `docs/deployment.md` — systemd on Google Cloud, update + rollback
- `docs/dashboard.md` / `docs/alerting.md` — operator surfaces
- `benchmarks/parity_guard.json` — machine-readable freeze (head + SHAs + parity hashes)
- `docs/deferred_refactor_seams.md` — intentionally deferred architecture seams
- `docs/alpaca_broker_truth_matrix.md` — broker/execution truth matrix
