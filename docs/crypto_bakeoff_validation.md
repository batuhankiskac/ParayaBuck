# Crypto Strategy Bake-Off Validation — 2026-08-12

Generated: 2026-08-12 · Four predeclared crypto alpha families, one production
engine, one decision. Specifications were frozen by independent literature
research BEFORE any final performance table was opened (no parameter
tournaments, no optimizer, no post-hoc re-specification).

Artifacts: `data/research/strategy_validation_v4/frozen_specs.json`,
`bakeoff.json`, `candidate_{A,B,C}.json` ·
Harness: `scripts/validate_strategy_candidates.py bakeoff` (retired
2026-08-18; evidence frozen in this document) ·
Dataset: `data/historical/crypto_18_1h_extended.csv` (hash
`aa88c99a561e29c84174cb2a3c7d81cb3571d17ac32d7af0e588aceec39f0ebb`) ·
1d: `crypto_18_1d_extended.csv` (hash
`75f820fc0d778df093bf3660f010bc87335b1429f58f153f8e8524c90af22387`)

## Frozen candidates (recorded before final evaluation; NOT tuned after)

| Key | Name | Hypothesis | Frozen signal |
|---|---|---|---|
| A | `crypto_high_proximity_v1` | Anchoring / nearness-to-high (G&H 2004; Jia et al. 2026) | `close_t / max(close_{t-363..t})`, 364-bar window, weekly 168h, top-2 equal weight |
| B | `crypto_low_variance_v1` | Low realized variance / defensive (AHXZ 2006) | sample std of 22 daily returns (23 consecutive complete 1d bars), weekly 168h, top-2 equal weight |
| C | `crypto_low_disagreement_v1` | Abnormal volume / disagreement, low side (GKM 2001 interval) | mean(vol t-5..t-1, nonzero) / mean(vol t-50..t-6, nonzero), zero-volume gates, weekly 168h, top-2 equal weight |
| D | `crypto_signed_jump_v1` | Realized signed-jump variation (Patton & Sheppard 2015) | **D — FEATURE INFEASIBLE WITH CURRENT ALPACA DATA** |

D infeasibility: signed-jump variation is an in-fill asymptotic construct
requiring ~78–288 intraday observations/day (5-min); with 24 hourly
observations/day the continuous-vs-jump separation fails (BV small-sample
bias, ~3.5× noise floor, minimum visible jump ~0.84 daily-vol), compounded by
the SOL 417-day 1h hole, XRP 29h gap and 25.9% zero-volume hours that still
carry price movement. No simplified "big candle" proxy was substituted.

## Evaluation (identical for every candidate)

Window 2021-04-01..2026-07-31 (warmup 2021-01-01..03-31), PIT universe built
once from the 1h bars (2,037 daily scanner snapshots, avg eligible pool
9.6/18, BTC/ETH pinned), 1d signal bars (22,156, `require_complete=True`),
production cost model 28 bps/side at 1x (25 taker + 2 slippage + 1
half-spread; 2x/3x scale linearly), T+1 fills at next bar open, rebalance
band 0.5%, min notional $10, risk overlay (sleeve 10%, per-asset 5%, max 2
positions, cash 40%, gross 60%), drawdown 5% shadow semantics.

## Results (full period, net of costs)

| Metric | A high_proximity | B low_variance | C low_disagreement |
|---|---|---|---|
| return 1x | -11.14% | **+4.61%** | +5.51% |
| CAGR 1x | -2.19% | **+0.85%** | +1.01% |
| Sharpe 1x | -0.385 | **+0.172** | +0.171 |
| Sortino 1x | -0.493 | **+0.231** | +0.228 |
| maxDD 1x | 12.63% | 13.17% | 13.28% |
| Calmar 1x | -0.173 | **+0.064** | +0.076 |
| profit factor 1x | 0.636 | **1.109** | 1.072 |
| return 2x | -12.90% | **+0.86%** | -1.53% |
| return 3x | -14.61% | -2.86% | -9.61% |
| trades / rebalances | 64 / 223 | 124 / 263 | 256 / 263 |
| turnover (total / annualized) | 6.91 / 1.30x | 13.28 / 2.49x | 26.10 / 4.90x |
| fees (1x) | $1,841 (19.6% gross) | $3,757 (43.4% gross) | $8,132 (58.8% gross) |
| avg holding (per position) | ~58d | ~33d | ~23d |
| avg crypto exposure | 8.4% | 9.4% | 9.5% |

## Walk-forward (anchored, 10 calendar 6-month folds from 2022-01-01, 1x)

| Candidate | positive | negative | median | mean | worst | best |
|---|---|---|---|---|---|---|
| A | 5/10 | 5 | -0.26% | -1.37% | -7.56% (2022H1) | +1.05% (2024H2) |
| B | **8/10** | 2 | **+1.11%** | **+0.32%** | -9.05% (2022H1) | +4.38% (2024H2) |
| C | 4/10 | 6 | -0.16% | -1.10% | -6.25% (2022H1) | +2.04% (2023H2) |

B's two negative folds are the two distinct bear regimes (2022H1, 2026H1);
the seven folds between are positive (2022H2..2025H2) — not one-regime
dependence. C's positives are all inside the 2023-24 bull (textbook
one-regime dependence).

## Concentration

| Candidate | top1 | top2 | top3 | BTC | ETH | jackknife |
|---|---|---|---|---|---|---|
| A | BTC 100% pos | SOL (neg) | LTC (neg) | +$2,450 | -$3,942 | drop-BTC -14.04% (worst) |
| B | XRP 33.8% pos | LTC 31.9% | ETH 31.2% | +$331 (7.2%) | +$4,588 | drop-XRP **+4.06%**, drop-ETH +2.68%, drop-BTC -1.96% |
| C | SOL 38.8% pos | AAVE 20.5% | BTC 19.0% | +$4,412 (80.1% tot) | +$38 | drop-SOL **-2.41%** (profitability vanishes) |

B survives the drop-one-symbol test for its top contributor (drop-XRP keeps
+4.06%). The drop-BTC flip is real displacement (BTC is the structural
low-variance parking spot — held in 223/263 rebalances at 45% of deployed
weight, contributing only +$331): independent subwindow experiment 2023-2024
keeps +8.98% without BTC vs +13.44% with it. Flagged fragility, not a
disqualifier.

## BTC beta / regime diagnostics

| Candidate | beta | r2 | fwd 1w (BTC-up wk) | fwd 1w (BTC-flat/down wk) |
|---|---|---|---|---|
| A | 0.073 | 0.57 | -0.39% (win 46%) | -0.15% (win 51%) |
| B | 0.093 | 0.75 | +0.60% (win 50%) | +0.01% (win 54%) |
| C | 0.100 | 0.54 | +0.82% (win 46%) | -0.55% (win 48%) |

A and C show no edge (or negative edge) outside BTC bull weeks; B's edge is
concentrated in bull weeks but remains non-negative with a higher win rate in
flat/down weeks.

## Benchmarks (same window, 1x)

BTC buy-and-hold +10.24% (maxDD 76.7%), ETH -2.61%, BTC/ETH 50/50 +3.82%,
old `crypto_time_series_momentum_v1` +0.20%, old
`crypto_cross_sectional_momentum_1h_v1` -6.54%, closed reversal +3.56%.
Candidate B +4.61% on the full account (9.4% average sleeve exposure) — on an
invested-capital basis the sleeve earned ~+49% over the window, far ahead of
a scaled BTC buy-and-hold.

## Correctness

- Independent recomputation (Agent E, Wave 2): frozen-formula arithmetic,
  rank_candidates==decide parity, T+1 fill timing and 28 bps/side fee math
  verified for all three implementations; 60 unit tests pass.
- Adversarial review (Agent F, Wave 4): no lookahead, no future universe
  membership, no same-bar execution, no zero-volume→buy-signal path,
  observation-set parity confirmed; B's 1x full-period rerun via the engine
  API is BIT-IDENTICAL to the artifact (return 0.046073, sharpe 0.171791,
  maxDD 0.131654, per-symbol contributions exact).
- Shared-data audit (Agent E, Wave 1): no P0 defects; P1-A universe timeframe
  trap found and FIXED in the harness (all crypto universe builds now pass
  `timeframe="1h"` explicitly — the trap would have silently emptied the
  universe once the config strategy became 1d); P1-B volume regime and P1-C
  completeness-gate skips are bounded and shared across candidates.

## Verdicts

- **A — Anchoring: C — FAMILY NOT VALIDATED.** Net negative at 1x/2x/3x
  (-11.1%/-12.9%/-14.6%), walk-forward 5/10 (coin flip), 100% of positive
  PnL from BTC with ETH the largest loss, negative edge in both BTC regimes.
- **B — Low Variance: A — VALIDATED → PAPER candidate.** Passes every
  minimum PAPER gate: 1x +4.61% (>0), Sharpe 0.172 (>0), profit factor 1.109
  (>1), 2x +0.86% (>0), walk-forward 8/10 (>50%), top-1-drop robustness.
  Fragility flags recorded: 3x net negative (-2.86%), fees 43.4% of gross
  PnL, edge concentrated in BTC bull weeks, BTC-jackknife displacement flip.
  Validation margin is thin (2x at +0.86%).
- **C — Low Disagreement: C — FAMILY NOT VALIDATED.** 2x net negative
  (-1.53%), walk-forward 4/10 with all positives in the 2023-24 bull, SOL
  concentration (drop-SOL flips +5.51% → -2.41%), edge negative outside BTC
  bull weeks, fees 58.8% of gross.
- **D — Signed Jump: D — DATA/FEATURE INFEASIBLE.**

## Config / safety

- `strategy.crypto`: enabled `true`, name `crypto_low_variance_v1`,
  parameters `{variance_lookback_days: 22, top_n: 2, rebalance_hours: 168}`
  (PAPER selection; LIVE remains `enabled: false`).
- `strategy.equity`: untouched — `equity_residual_momentum_v1` unchanged
  (economics frozen; regression subset 50 passed; full suite 546 passed).
- Equity regression tests pass; no PAPER orders placed (no smoke opt-in);
  LIVE orders none.
