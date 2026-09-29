# Crypto sleeve decision evidence v1 (2026-09-29)

Question: remove the 0.05 crypto sleeve (`crypto_low_variance_hysteresis_v1`), or keep it as a
"learning basket"? Research only; no production code or configuration changed.
Script and machine-readable results: `research/crypto_sleeve_decision_v1/account_sim.py`,
`research/crypto_sleeve_decision_v1/RESULTS.json`.

## 1. What the strategy is

- **Universe:** 22 configured USD pairs (`universe.crypto.trade_symbols`), BTC/ETH pinned. A
  dynamic scanner (`src/parayabuck/universe/crypto_scanner.py`, 14-day 1h lookback, liquidity,
  ATR, spread, BTC-correlation, category, meme and high-beta gates) writes
  `data/runtime/active_crypto_universe.json`. It refreshes every 24h and the file is valid for 12h.
- **Signal** (`portfolio/crypto_low_variance.py`, `portfolio/crypto_low_variance_hysteresis.py`):
  22-day realized volatility of daily returns. It ranks the lowest first and holds the top 2. A
  held name is kept while it is scanner-eligible and ranks 4th or better.
- **Sizing:** equal weight inside a 0.05 sleeve (`strategy.crypto.maximum_allocation`), with
  `risk.max_crypto_positions: 2`, `max_crypto_position_pct: 0.05` and
  `max_crypto_exposure_pct: 0.10`.
- **Cadence:** decisions come in epoch-aligned 96h blocks at 00:00 UTC. Orders are market orders,
  24/7.
- **Costs:** 25 bps taker fee (`backtest.crypto_taker_fee_bps`), 2 bps slippage, 2 bps spread.
- **Crypto-only engine keys:** `engine.crypto_stop_limit_offset_bps`,
  `crypto_emergency_exit_bps` and `crypto_stop_cancel_*`.

## 2. Backtest evidence: the "5.4% total" figure is verified, and its meaning matters

Source: `docs/research/canonical_crypto_baseline.md`. The authoritative artifact is
`maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz` at tag
`archive/maintenance/crypto-canonical-causal-execution-v1-2026-09`
(sha256 `8ec31d0a…2cae`). The window runs from 2021-04-01 to 2026-07-31, about 5.3 years.

| Replay metric (isolated $100k account, 5% sleeve, 95% idle cash) | Value |
|---|---|
| Total return / CAGR | 5.42% / 1.00% |
| Annual volatility / Sharpe / Sortino | 3.00% / 0.34 / 0.48 |
| Max DD / Calmar / turnover | 6.24% / 0.16 / 3.52 |
| Costs (fee / slippage / spread) | $920 / $74 / $37 |

The 5.42% is **account-level**. The replay runs a $100k account that holds only 5% crypto
(average exposure 4.84% of NAV, invested on 98.7% of days). So 5.4% total, or about +1.0%/yr, is
what the sleeve added to the whole account. It is not the return on the crypto capital.

If the same strategy ran as a 100%-crypto account, it would make:

- 15.0% total return (2.66% CAGR)
- 64% annual volatility
- **76% max drawdown**

The gap between the two framings is volatility drag. Holding the sleeve at 5% of NAV collects
roughly its arithmetic mean return, about 20%/yr on the sleeve, which is about 1%/yr of NAV.

The window starts near the 2021 BTC high. BTC went from about $58.6k to $64.7k over the window.

## 3. Account-level variants (close-to-close, offline)

Setup:

- **Equity:** VT35 on Alpaca SIP QQQ/QLD bars and dividends from tag
  `archive/research/profit-max-v1-2026-09`. It fills at the next open with a 0.005 band and
  3 bps per trade.
- **Crypto:** the replay's daily NAV returns, added as a fraction of account NAV.
- **BTC references:** BTC/USD daily closes from the same replay, 5% target, 28 bps per trade.
- Sharpe is computed from monthly returns with a zero risk-free rate.

Calibration of this model:

- **2017–2026, VT35 alone:** 27.65% CAGR and 26.4% max DD. The engine's Stage B gave 28.98% and
  29.2%.
- **Overlap window, variant A:** 25.07% CAGR. The Stage B VT35 run, which includes crypto, gave
  26.1% and 29.2% DD.

The model runs about 1 pp under the engine, so compare the differences between rows rather than
the levels.

**Window 2021-04-01 → 2026-07-30, idle cash at 0% (the PAPER/model reality):**

| Variant | CAGR | Max DD | Sharpe | Total |
|---|---:|---:|---:|---:|
| A: equity 0.93 + crypto strategy 0.05 (current) | 25.07% | 27.55% | 0.994 | 229.3% |
| B: equity 0.93, crypto removed, 0.05 in cash | 24.09% | 26.07% | 0.999 | 215.8% |
| C: equity 0.98, crypto removed | **25.34%** | 27.34% | 0.997 | 233.1% |
| R1: equity 0.93 + BTC buy-and-hold 0.05 | 24.72% | 28.70% | 0.984 | 224.4% |
| R2: equity 0.93 + BTC 200-day trend 0.05 | 24.97% | 26.55% | 1.002 | 227.9% |

**Sensitivity checks.** Rows are in the same order: A / B / C / R1 / R2.

| Check | CAGR | Max DD |
|---|---|---|
| Idle cash earns an approximate 3-month T-bill rate | 26.10 / 25.32 / 26.42 / 25.74 / 26.08% | 25.8 / 24.2 / 25.5 / 27.0 / 24.7% |
| 2021-07-20 start (the day the BTC SMA200 is defined), cash at 0 | 23.71 / 22.29 / 23.42 / 23.80 / 23.20% | — |

In the later-start check, A leads C by 0.3 pp. Over the full window, C leads A by 0.3 pp.

- **Statistical weight:** the monthly A−B difference is +0.09%/month (t = 0.80, 64 months). The
  A−C difference is −0.02%/month (t = −0.19). Neither is distinguishable from zero.
- **Calendar years:** the crypto replay's share of NAV was:

  | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 YTD |
  |---:|---:|---:|---:|---:|---:|
  | +0.7% | −2.5% | +4.5% | +4.3% | +0.8% | −2.2% |

  The sleeve lost in 2022, the same year VT35 lost 18.5%. It gives no hedge.
- **Correlation of the crypto sleeve:**

  | Against | Weekly | Monthly |
  |---|---:|---:|
  | VT35 account | 0.18 | 0.33 |
  | QQQ | 0.26 | 0.48 |
  | BTC | 0.88 | 0.87 |

  The "low-variance" selection is still about 0.9 correlated with BTC.

## 4. Operational burden

- **Code size:** 5 crypto-only source modules with 1,418 lines:
  - `execution/alpaca_crypto.py` (475)
  - `universe/crypto_scanner.py` (582)
  - `portfolio/crypto_low_variance.py` (205)
  - `portfolio/crypto_low_variance_hysteresis.py` (115)
  - `universe/crypto_metadata.py` (41)

  56 of the source files mention crypto, about 1,089 matching lines, spread across these areas:
  - runtime, config and CLI
  - ledger, reconciliation and protection
  - telemetry and the web layer
  - backtest engine and universe preflight
- **Tests:** 7 test files are crypto-specific (2,777 lines), and 76 test files mention crypto.
- **Commits:** 124 of 708 commits mention crypto (18%). 29 commits touch the four core crypto
  modules.
- **Incidents, recent ones only:**
  - `922fb5f`: a 75 bps protective stop was placed on every portfolio crypto buy. PAPER sleeve
    results before 2026-09-29 are invalid.
  - Crypto dust bookkeeping: `2bd38fe`, `4617559`, `56294c3`, `e5477c9`, `8c1df7d`.
  - Crypto slot-cap enforcement: `a167a49`, `7724a2b`.
  - Non-causal fills in the historical replay: fixed in `b135d73` and superseded the v2 baseline.
  - The web crypto-vol cache: `f31dc1d`.
- **Structural surface:** 24/7 scheduling (the sleeve decides on weekends and at 00:00 UTC), a
  scanner refresh with an active-universe file and digest match, crypto stop and emergency-exit
  config, and fractional-quantity dust.
- **Live evidence:** there is no clean live track record yet. The stop hook dates from about
  `51b661c`/`4ec1a7d` (2026-08-09/10). It therefore covered the whole T1 PAPER period from
  2026-08-27 to 2026-09-29.
- **Earlier incidents:**
  - `bc0a520`: gross-fill ownership drift halt.
  - `e09e89c`: stops cancelled every 60 seconds.
  - `93ab8a9`: BTC/USD vs BTCUSD lookup broke startup reconciliation.
  - `1abc678`: quantity mismatch blocked startup.
  - `930125a` and `a0c2fc5`: candidate-pool and metadata drift.
- **Config keys that do nothing:** `engine.crypto_stop_limit_offset_bps` and
  `crypto_emergency_exit_bps` are validated but never passed to the broker. Only the
  cancel-timeout and poll keys are wired (`runtime.py:372-373`).

## 5. Recommendation: remove

Over the only window with data (5.3 years), the sleeve adds about +1 pp/yr of CAGR against idle
cash. That gain is not statistically significant (t ≈ 0.8). It also comes with a higher max
drawdown (+1.5 pp) and no hedge, since the sleeve lost in 2022 together with equity.

Moving the same 0.05 into VT35 (0.98, which respects the 0.02 buffer) gives the same or better
return: 25.34% vs 25.07% CAGR, with a similar drawdown and Sharpe. So the crypto sleeve earns no
return premium over a simpler, already-validated use of that capital. Meanwhile it carries 18% of
commit history, a 24/7 execution path and a recent production-level bug.

As a "learning basket" it teaches little. A 5% market-order sleeve that is 0.88 correlated with
BTC is effectively a small BTC beta position. Its live record is already contaminated, and a clean
multi-cycle sample would take years.

Recommended path:

1. **Config-only disable first.** This is reversible and needs no code deletion.
2. **Deletion as a separate task** after a quiet period.

If the owner wants crypto exposure anyway, R2 (BTC 200-day trend at 5%) matched A with a lower
drawdown and is a far simpler rule. It is not validated in the engine.

### Exact change set if removal is approved (not applied)

**Config, `config/settings.yaml`:**

- `strategy.crypto.enabled: false`
- `universe.crypto.enabled: false`
- `strategy.equity.maximum_allocation: 0.93 → 0.98`. The capital invariant in
  `config.py:577-593` then holds: 0.98 ≤ `max_gross` 0.98 ≤ 1 − 0.02.
- Keep the `strategy.crypto` block and its registered name, with `enabled: false`. The config
  loader still resolves it.
- Leave `risk.max_crypto_*` unchanged. These keys are in the equity runtime's configuration
  digest (`portfolio/runtime_models.py:174-196`) and cannot be set to 0.
- Leave `engine.crypto_*` and `backtest.crypto_taker_fee_bps` alone until full deletion.
- `scripts/audit_paper_soak.py` (`EXPECTED` and gates S2/S9/S11) hardcodes crypto evidence. It
  must be updated or it will fail.

Before the switch:

- Validate the allocation change with a Stage B VT35 run at 0.98 without crypto.
- Liquidate the existing PAPER crypto positions through the strategy's own sell path, or by an
  operator action. With `engine.manage_external_positions: false`, leftover positions would
  otherwise be orphaned. A disabled market stops producing decisions.
- Also update the `max_position_pct: 0.93` cap to 0.98. Otherwise QLD stays capped at 0.93.

**Code, only for a later full deletion:**

- The 5 crypto-only modules above.
- Crypto branches in:
  - `config.py`
  - `runtime.py`
  - `runtime_universe.py`
  - `cli.py`
  - `universe/preflight.py`
  - `execution/alpaca.py`
  - `execution/alpaca_reconciliation.py`
  - `execution/alpaca_protection.py`
  - `execution/ledger.py`
  - `portfolio/runtime_service.py`
  - `backtest/portfolio_engine.py`
  - `web/*`

**Tests:**

- **Break under a config-only disable:**
  - `tests/test_strategy_spec_freeze.py:101`
  - `tests/test_dual_market_startup.py:134-135`, the frozen-config assertion
  - `tests/test_config.py:146-167`, if the allocation changes
  - `tests/test_combined_account_risk.py:106-149`, which asserts 0.93 + 0.05 = 0.98
  - `tests/test_paper_soak_auditor.py`
- **Build a runtime from the shipped config (review needed):**
  - `test_buy_admission_correctness.py`
  - `test_crypto_foundation_parity.py`
  - `test_t1_promotion_transition.py`
  - `test_web_g9_g7.py`
- **Crypto-specific, deleted only in the full code deletion:**
  - `tests/test_crypto_scanner.py`
  - `tests/test_crypto_low_variance.py`
  - `tests/test_crypto_universe.py`
  - `tests/test_crypto_t1_freeze.py`
  - `tests/test_crypto_causal_execution_contract.py`
  - `tests/test_crypto_foundation_parity.py`
  - `tests/execution/test_crypto_exit_dust.py`
- **Allocation and config assertions:**
  - `tests/test_config.py`
  - `tests/test_combined_account_risk.py`
  - `tests/test_global_slot_accounting.py`
  - `tests/test_dual_market_startup.py`
  - `tests/test_product_boundary.py`
  - `tests/test_strategy_spec_freeze.py`
  - `tests/test_release_v2_integration.py`

## Limitations

- Close-to-close model rather than the engine. It runs about 1 pp CAGR under Stage B.
- One 5.3-year crypto window that starts near a cycle high.
- The T-bill rates are approximate annual averages, not downloaded.
- BTC references use the replay's pinned daily bars. Nothing was downloaded.
