# Crypto BTC-Residual Low-Variance — Pre-Registered Research Plan

**Freeze date:** 2026-08-26 22:15 +03:00  
**Starting HEAD:** `973eff8e2f3536c024ba7885123f9d5ba355c27b`  
**Research identity (candidate family):** `crypto_residual_low_variance_v1` — only the winning frozen spec may become the class; do not promote to PAPER in this mission  
**Status:** `PRE-REGISTERED — no performance results computed`

## 0. Pre-Registration Contract

This plan and `data/research/crypto_residual_low_variance_2026_08/research_plan.json` are committed **before** computing candidate performance. Gates, windows, costs, and candidate formulas below are frozen and must not be altered after seeing results.

## 1. Dataset

- **Primary:** `data/historical/crypto_expanded_1h.csv`
- **SHA-256:** `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`
- **Audit:** 705,541 rows, 25 symbols: `AAVE, ADA, ARB, AVAX, BCH, BTC, CRV, DOGE, DOT, ETH, FIL, GRT, HYPE, LINK, LTC, MKR, NEAR, ONDO, PEPE, RENDER, SHIB, SOL, TRX, UNI, XRP`
- **Configured 22-symbol pool (authoritative):** `BTC/USD, ETH/USD, SOL/USD, XRP/USD, ADA/USD, DOGE/USD, AVAX/USD, LINK/USD, LTC/USD, BCH/USD, DOT/USD, UNI/USD, AAVE/USD, ARB/USD, FIL/USD, RENDER/USD, ONDO/USD, HYPE/USD, SHIB/USD, GRT/USD, CRV/USD, PEPE/USD`
- **Trade-symbols SHA-256 (sorted-join):** `744fa9c610a73b0d1afefb1024ef8117894995b7b49483a02c7178219d8ab37e`
- **Dataset-symbols SHA-256:** `40c4a6f4e1738956db35c44a30e59bac44add78db49a8ffd597c4e0bc9c06942`
- **Dataset-only (must be excluded from canonical v3 candidate evaluation):** `MKR/USD, NEAR/USD, TRX/USD`
- **No redownload, no forward-fill, no interpolation.** Missing data = unavailable. `1h → 1d` with `require_complete=True`; strategy uses only COMPLETE daily bars.

## 2. Research / PAPER Universe Parity

Historical universe uses **SAME** PIT semantics as foundation mission:

```
configured 22-symbol pool ∩ bars available at date t
  → CryptoUniverseScanner (production config, 14d lookback, 1h bars)
  → eligible PIT pool (selected_symbols)
  → candidate strategy ranking
```

- Use `parayabuck.backtest.universe.build_trailing_point_in_time_universe` with `crypto_config=load_config().universe.crypto`.
- Do NOT feed all CSV symbols directly; do NOT let `MKR/NEAR/TRX` enter because they exist in the file.
- Persist `universe hash` (via `hash_point_in_time_universe`), `candidate-pool history hash`, `selected-symbol history hash`.

## 3. Control (R0)

**Exactly** `crypto_low_variance_v2` (`CryptoLowVariance1dV2` v2.0.0):

- `variance_lookback_days = 22`, `top_n = 2`, `rebalance_hours = 96`, `maximum_allocation = 0.05`
- Signal: `r_i,t = P_t / P_{t-1} - 1`; `sigma_i = sample_std(r_i)` over last 22 daily returns; require 23 consecutive completed daily bars spanning exactly 22 calendar days; rank ascending `sigma_i`; select top 2 equal pre-risk weight.
- CONTROL must reproduce expected economics/parity before candidate research proceeds. If not, STOP and fix harness; do not evaluate variants on broken baseline.

## 4. BTC Factor Model

Daily simple returns `r_i,t`, `r_BTC,t`:

```
r_i,t = alpha_i + beta_i * r_BTC,t + epsilon_i,t   (OLS, intercept, trailing window)
epsilon_i,t = r_i,t - alpha_hat - beta_hat * r_BTC,t
sigma_resid_i = sample_std(epsilon_i)  (n-1)
rank ascending sigma_resid_i
```

- At decision time `t` all regression observations end at `t` (completed bars only; no lookahead; timestamp-aligned; no interpolation/forward-fill; coin must have complete required observations; deterministic tie-break by symbol).
- **BTC special handling — Option A chosen (preferred default):** `BTC/USD` remains eligible; `BTC` residual score = BTC total realized volatility (`sample_std` of 22 daily BTC returns). Regressing BTC on itself is prohibited (would produce near-zero residual and force BTC first). This is pre-declared.

## 5. Pre-Declared Candidates Only — Exactly R0–R3

| ID | Label | Beta window | Residual window | Top N | Rebalance | BTC score | Purpose |
|---|---|---:|---:|---:|---:|---|---|
| R0 | CONTROL | — | — (total vol 22d) | 2 | 96h | — | Baseline |
| R1 | RESIDUAL 22 | 22 | 22 | 2 | 96h | total vol | Short-horizon residual risk ranking |
| R2 | RESIDUAL 63 | 63 | 22 most recent residuals | 2 | 96h | total vol | More stable beta, same 22d residual horizon |
| R3 | RESIDUAL CONSENSUS | both 22 & 63 | 22 each | 2 | 96h | total vol each leg | `mean(percentile_rank_22, percentile_rank_63)` lower=better; equal weights, no optimization |

No learned weights, no optimization. **No other variants** (no beta 30/45/90/126, no top_n 3/4, no 72/120/168h, no momentum/BTC-regime/RSI/SMA/volume/funding/sentiment/vol-target/leverage/inverse-vol/stops/meme/category tuning).

## 6. Evaluation Windows

- **Full window:** `2021-04-01T00:00:00Z` → `2026-07-31T23:59:59Z` (warmup `2021-01-01..2021-03-31` ≥63d for R2 stable beta; earliest viable decision ~`2021-03-04`, buffered to `04-01` including 14d scanner lookback).
- **Walk-forward:** independent calendar 6-month folds, each fresh engine/PIT universe/warmup/beta-only-trailing:
  - `2022-H1` 2022-01-01→06-30, `2022-H2` 07-01→12-31,
  - `2023-H1`, `2023-H2`, `2024-H1`, `2024-H2`, `2025-H1`, `2025-H2`, `2026-H1` (9 comparable folds for gates),
  - `2026-H2-partial` 2026-07-01→07-31 truncated to dataset end — reported but excluded from the `≥60%` beat denominator.
  - Comparable N for gate #4 = 9 (`2022-H1`..`2026-H1`).

## 7. Signal Implementation Contract

- **Research code first**, preferred `scripts/research_crypto_residual_low_variance.py`; do NOT register a production strategy yet.
- Requirements: sample std `n-1`, OLS with intercept, timestamp-aligned, no interpolation/forward-fill/lookahead, only completed daily bars, deterministic tie-break, fail-safe on invalid regression, independent hand-computable arithmetic unit tests.

## 8. Cost Model

Production-parity (authoritative harness, not hard-coded):

- `taker_fee_bps = 25`, `slippage_bps = 2`, `spread_bps = 2` (from `config/settings.yaml` `backtest.*`).
- Record actual effective cost assumptions in every result.
- **Cost stress:** `1x, 2x, 3x` multiplier on slippage+spread+fee (or harness equivalent). Candidate must not depend on optimistic execution.

## 9. Metrics

**Full-window per R0–R3:** total return, CAGR, ann. vol, Sharpe, Sortino, Calmar, maxDD, maxDD duration, turnover, fees, fills, avg/max exposure, avg holdings, positive-period ratio, plus avg selected beta, avg residual vol, avg total vol, avg BTC correlation (why candidate differs).

**Walk-forward per candidate:** positive fold count, median/mean fold return, median fold Sharpe, worst/best fold, pairwise `beats CONTROL in X/N folds`.

## 10. Regime Robustness

- **BTC 126d trailing return:** `> +15% → BULL`, `-15..+15% → NEUTRAL`, `< -15% → BEAR` (frozen before results; use project canonical if one exists, documenting which was used). Report candidate performance in BULL/NEUTRAL/BEAR. Valid strategy must not derive essentially all value from one isolated regime.

## 11. Concentration / Jackknife (mandatory, small universe)

- PnL attribution by symbol: top contributor share of positive PnL, top-2, top-3.
- **Leave-one-symbol-out** at minimum: `BTC`, `ETH`, `SOL`, plus top candidate-specific positive contributor if different (full 22 LOO only if cheap).
- **Question:** Does candidate remain directionally positive without its largest contributor? If one coin explains most edge → `FRAGILE`.

## 12. Turnover Analysis

Selection turnover, weekly/96h Jaccard overlap, average rank displacement, holding duration, replacement frequency — R1–R3 vs CONTROL. Modest gross improvement is not attractive if turnover explodes and net improvement disappears.

## 13. Beta / Residual Diagnostics (R1–R3)

Beta distribution, median, p10/p90, residual vol distribution, fraction with insufficient aligned BTC history, fraction rejected for missing consecutive data, BTC correlation of selected portfolio. Check pathological regressions (huge beta, near-zero BTC variance, singular alignment, insufficient samples, non-finite residuals) — fail safely, no silent zero-beta substitution.

## 14. Promotion Gates — ALL 12 Must Pass to Label VALIDATED

1. Full-window net Sharpe > CONTROL
2. Full-window CAGR > CONTROL **or** materially lower maxDD with comparable CAGR
3. Walk-forward: positive folds ≥ CONTROL positive folds **and** median fold return ≥ CONTROL median
4. Beats CONTROL in ≥60% of comparable folds (≥6/9)
5. 2× cost stress remains net positive
6. 3× cost stress is not catastrophic (maxDD not > 2× control maxDD and CAGR not < −5% ann.; or wipes <75% of 1× gross edge)
7. No single symbol explains >50% of positive PnL unless leave-one-out remains attractive (Sharpe & CAGR still > control ex-that-symbol)
8. Remove-largest-contributor test remains directionally positive (return >0 and Sharpe >0)
9. No single 6-month fold explains >50% of total excess PnL
10. Turnover increase is economically justified (net Sharpe after costs still > control, and <2× turnover unless >20% Sharpe uplift)
11. Performance is not exclusively one BTC regime (positive net return in ≥2 of BULL/NEUTRAL/BEAR)
12. No research/PAPER universe parity violation

**Classification:** `STRONG` (clear full+WF+robustness+costs), `MODERATE` (smaller but consistent), `WEAK` (marginal/concentrated/regime-sensitive → NOT PROMOTED), `REJECTED` (fails gates).

## 15. Comparison Table

Final `|Metric|R0|R1|R2|R3|` with Return, CAGR, Sharpe, Sortino, Calmar, maxDD, Turnover, Fees, WF positive, WF median, Beats control, 2×/3× cost, Top contributor, Ex-top contributor.

## 16. PAPER Config Freeze

Throughout research, `config/settings.yaml` and runtime must keep:

- `strategy.crypto.name == crypto_low_variance_v2`
- `strategy.equity.name == equity_residual_momentum_v3`
- `runtime.mode == paper`
- `live.enabled == false`

No promotion to candidate during this mission.

## 17. If Nothing Wins / If One Wins

- **If R1–R3 fail:** emit exactly `CRYPTO BTC-RESIDUAL LOW-VARIANCE FAMILY REJECTED`; do not run more beta windows or residual variants; next hypothesis must be genuinely different.
- **If one candidate clearly passes gates:** freeze implementation+tests as `crypto_residual_low_variance_v1` (only the winning spec becomes the class; do NOT expose R1/R2/R3 as three production strategies); still keep PAPER on `crypto_low_variance_v2` (promotion is a SEPARATE mission).

## 18. Commit Structure

- **A** — this plan freeze (no performance results) ← **this commit**
- **B** — research harness + arithmetic tests (no promotion)
- **C** — full-window + walk-forward results
- **D** — cost/regime/concentration/jackknife/turnover/diagnostics
- **E** — final verdict
- **F** — (if applicable) freeze winning strategy implementation, still not PAPER-selected

## 19. Quality Gates

`pytest -q`, `ruff check .`, `ruff format --check .`, `mypy src` + focused tests (OLS arithmetic, residual, alignment, missing BTC, insufficient window, no lookahead, BTC special, tie-break, PIT exclusion, control parity).

## 20. Required Artifacts

Under `data/research/crypto_residual_low_variance_2026_08/`: `research_plan.json`, `control_parity.json`, `full_window_results.json`, `walk_forward.json`, `cost_stress.json`, `regime_analysis.json`, `pnl_concentration.json`, `jackknife.json`, `turnover_analysis.json`, `beta_diagnostics.json`, `final_verdict.json`; plus `docs/crypto_residual_low_variance_validation.md`.

## 21. Final Verdict — Exactly One

`CRYPTO BTC-RESIDUAL LOW-VARIANCE VALIDATED — STRONG` / `VALIDATED — MODERATE` / `WEAK — NOT PROMOTED` / `FAMILY REJECTED` / `VALIDATION INCOMPLETE`.

## 22. Final Response Checklist

Starting HEAD, Final HEAD, Commits, Research-plan freeze SHA, Dataset SHA, Configured crypto universe hash, CONTROL parity result, R0–R3 full-window table, Walk-forward table, Cost-stress table, Regime table, PnL concentration, Leave-one-out, Turnover, Beta/residual diagnostics, Promotion-gate PASS/FAIL matrix, Tests/ruff/mypy, Whether new strategy class was frozen, confirm `EQUITY V3 UNCHANGED`, `CURRENT CRYPTO V2 PAPER SELECTION UNCHANGED`, `PAPER MODE UNCHANGED`, `LIVE DISABLED`, Final verdict.

---

*Do not alter gates after seeing results. This file is the frozen pre-registration.*
