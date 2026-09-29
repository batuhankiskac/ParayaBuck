# Crypto removal v1 (2026-09-30)

ParayaBuck is now **equity-only**. The crypto sleeve was disabled in
`paper_config_v2` (config-level); this change deletes the crypto code, config
and tests, and the failed BIL idle-cash sweep. The operator wants no code kept
"just in case": everything removed is recoverable from the local annotated tag
**`archive/code/crypto-sleeve-2026-09`** (commit `1d55ef2`).

Decision evidence: [`crypto_sleeve_decision_v1.md`](crypto_sleeve_decision_v1.md)
and [`paper_config_v2.md`](paper_config_v2.md). The crypto replay authority
`canonical_crypto_baseline.md`, `maintenance/crypto_canonical_causal_execution_v1/`
and `data/historical/crypto_expanded_1h.csv` were deleted with the code; read
them from the tag, e.g.
`git show archive/code/crypto-sleeve-2026-09:docs/research/canonical_crypto_baseline.md`.

## What was removed

- **Config.** `strategy.crypto`, `universe.crypto` (scanner), `risk.max_crypto_*`,
  `engine.crypto_*`, `backtest.crypto_taker_fee_bps`, `backtest.scanner_rebalance`,
  `runtime.scanner_refresh_hours`, `intent_watchdog.crypto_*`. `load_config`
  **rejects** any of these keys (and a crypto pair in the legacy `symbols:` list)
  with `Crypto support was removed ... remove these settings: ...`, so a stale
  config cannot load with its crypto intent silently dropped. The capital
  invariant covers the equity allocation only.
- **Modules deleted.** `universe/{crypto_scanner, crypto_metadata, preflight,
  observation_set, history}.py`, `portfolio/crypto_low_variance{,_hysteresis}.py`,
  `execution/alpaca_crypto.py`, `scripts/run_crypto_universe_scan.py`.
- **Crypto branches** in the runtime (crypto stream worker, universe preflight
  and scheduled scanner refresh loop, crypto protection watcher and halts, slot
  diagnostics, receipt provenance, data-gap repair, which only crypto produced),
  the portfolio runtime services (BTC benchmark gate, crypto dust bookkeeping,
  fee-net fills, crypto slot limits), execution (GTC orders, taker-fee reserve,
  protective stops and emergency exits, crypto exit reconciliation, crypto
  risk limits), market data (crypto stream and historical client, crypto
  daily-grid cache filter, stream hot-swap), backtest (crypto point-in-time
  universes, taker fees), monitoring (crypto intent-watchdog checks, universe
  health check, `scanner_recovered` and universe lifecycle events, `data_gap`
  metric), web (dashboard crypto card, `/api/crypto`, crypto settings toggle and
  universe/scanner status, `--market crypto`, the no-op backtest
  `max_symbols` / `--candidate-limit`), `scripts/audit_paper_soak.py` (gates S6
  and S11 retired) and benchmarks.
- **BIL idle-cash sweep.** `cash_sweep_symbol` in
  `portfolio/equity_leveraged_trend.py`, its config validation and tests. It
  failed the paper_config_v2 2x-cost gate and shipped off; with it unset, the
  parameter tuple and decisions are unchanged.
- **Tests.** Crypto-only tests deleted; mixed tests trimmed or rewritten with
  equity fixtures. Size of the change against `main`: `src` +750 / −6,435
  lines, `tests` +2,155 / −10,178, scripts/benchmarks/config/research
  +49 / −530, plus 706,818 lines of crypto data and maintenance evidence.

`scanner_refresh_failure` stays as an alert kind: `monitoring/paper_incident.py`
still emits it for provider-unavailable warnings, and it is in the shipped
`alerting.events`.

## What was kept on purpose

`Market.CRYPTO`, `parayabuck.utils.is_crypto` and crypto-pair parsing in
`markets/symbols.py` remain, only to **classify** foreign holdings, orders and
historical ledger rows in the shared brokerage account. No code path trades,
sizes, streams, protects, scans or backtests crypto:

- Old rows with `market='crypto'` (portfolio orders, ownership adjustments,
  managed positions) still load. Ownership reads skip them, so a crypto
  holding is never "managed" and can never cause an ownership-drift halt.
- A foreign crypto position still counts toward account slots (cap 8) and
  gross exposure exactly as before; reconciliation never places a stop on it;
  portfolio orders and stops refuse non-equity markets.
- **Startup safety.** If the broker account holds any crypto position (or a
  legacy crypto ledger row) at startup, the runtime logs a WARNING and emits
  one `risk_incident` alert (`market="crypto"`, `symbols=[...]`) saying crypto
  support was removed and the positions are left untouched for the operator.
  It starts equity-only and never crashes on them
  (`tests/test_foreign_crypto_positions_startup.py`).

## Verification

**Stage B replay** of the adopted config (`config/settings.yaml`, equivalent to
paper_config_v2 S1) with `research/paper_config_v2/run_stage_b.py`, 1x costs:

| Run | Ending NAV | Fills | Decision hash | Fill hash (no client ids) |
|---|---|---|---|---|
| paper_config_v2 S1 (original) | 1,173,537.61 | 615 | `c9adec34…4829` | `8c00d14f…a9f0` |
| `1d55ef2` (pre-deletion, current config) | 1,173,537.61 | 615 | `c9adec34…4829` | `8c00d14f…a9f0` |
| after deletion | 1,173,537.61 | 615 | `c9adec34…4829` | `8c00d14f…a9f0` |

Session NAV series, fees (193.53) and decision count (2,406) are identical: the
equity decisions are byte-for-byte unchanged (29.35% CAGR at 1x costs, as in
paper_config_v2).

The harness now guards its crypto branch and runs the equity-only config on
current code. The S0/S1/S2 arm configs in `research/paper_config_v2/configs/`
still carry crypto keys, so rerunning the arms needs tag
`archive/code/crypto-sleeve-2026-09`. The archived harness modules the replay
imports (from `archive/research/sleeve-candidates-v2-v3-2026-09`) needed three
one-line adaptations in the scratch root: no top-level import of the crypto
strategy class, no `crypto_taker_fee_bps` engine argument, and
`session_date_for(timestamp)` without a market.

**Tests.** Full normal suite: 1,421 passed, 3 skipped, 0 failed (before:
1,652 passed, 3 skipped). ruff clean, and mypy (strict) reports no issues in
`src`.

## Configuration digest change

The equity runtime's configuration digest (`portfolio/runtime_models.configuration_digest`)
hashes `asdict(PortfolioRiskLimits)`, which lost `maximum_crypto`,
`maximum_crypto_asset` and `maximum_crypto_positions`. For the shipped config
the digest changes from `994149801b5a…21bfe` to `9f01417a3619…9734`. Strategy,
parameters, allocation, universe and every other limit are unchanged. On deploy
the runtime opens a **new session** under the new digest (a new soak window),
as it already would for paper_config_v2. The warmup cache digest also changes
(`signal_adjustment_basis` lost its crypto entry), so the first start refetches
warmup history once. Neither affects decisions.

## Deploy notes (VM)

- **The new `config/settings.yaml` is mandatory.** The old file has
  `strategy.crypto`, `universe.crypto`, `risk.max_crypto_*`, `engine.crypto_*`,
  `backtest.crypto_taker_fee_bps`, `backtest.scanner_rebalance`,
  `runtime.scanner_refresh_hours` and `intent_watchdog.crypto_*`. The new code
  refuses to start with any of them, so deploy the bundle's settings file
  together with the code (and remove those keys from any operator override).
- `data/runtime/active_crypto_universe.json` and its `.history.jsonl` on the
  VM are no longer read; they can be deleted by hand.
- If the PAPER account still holds crypto, expect one `risk_incident` alert at
  startup listing the symbols; liquidate them manually in Alpaca. The bot will
  not touch them.
- Dashboard: the crypto card, `/api/crypto` and the crypto settings toggle are
  gone; asset version bumped to `20260930-1`.
