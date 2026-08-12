# Crypto Cross-Sectional Reversal Validation — NOT VALIDATED (family closed)

Generated: 2026-08-12 · Research-only evaluation using the PRODUCTION strategy
class (`crypto_cross_sectional_reversal_v1`), the production historical
engine, the production point-in-time universe policy, the production cost
model and the production risk/execution policy. Drawdown halts are SHADOW
events (production would halt; the curve kept trading), identical to prior
research runs.

Artifacts: `data/research/strategy_validation_v3/reversal.json` ·
Harness: `scripts/validate_strategy_candidates.py reversal`
Dataset: `data/historical/crypto_18_1h_extended.csv` (hash
`aa88c99a561e29c84174cb2a3c7d81cb3571d17ac32d7af0e588aceec39f0ebb`) ·
1d: `crypto_18_1d_extended.csv` (hash
`75f820fc0d778df093bf3660f010bc87335b1429f58f153f8e8524c90af22387`)

## Data

| Market | Source | Bars | Window | Adjustment |
|---|---|---|---|---|
| Crypto | `crypto_18_1h_extended.csv` (18 Alpaca USD pairs, 1h) | 537,936 (1h) / 22,156 (1d complete days) | 2021-01-01 .. 2026-07-31 | raw |

Per-symbol feed floors (verified against the Alpaca API): 8 majors
(BTC/ETH/SOL/DOGE/LINK/LTC/BCH/UNI) 2021-01-01, AAVE 2021-07-15, AVAX
2021-11-18, DOT 2023-08-18, XRP 2024-01-01; ADA/ARB/FIL/ONDO/HYPE/RENDER
2026-02 (real listing floors, not download artifacts — the previous
2024-08-01 CSV start was a downloader window choice, not the Alpaca floor).
Known limitations (disclosed): the dataset contains only currently-listed
pairs (survivorship bias; delisted pairs cannot be reconstructed); SOL has
an in-feed 1h hole 2023-07-06..2024-08-26; XRP an in-feed 29h gap
2025-01-19/20; 25.9% of 1h rows have volume==0 concentrated in the Feb-2026
cohort; PIT scanner snapshots exist only from 2024-06 (earlier membership is
survivorship-biased 'currently-tradable').

## Frozen specification (recorded before final evaluation; NOT tuned after)

| Field | Value |
|---|---|
| name | `crypto_cross_sectional_reversal_v1` |
| hypothesis | 10-week cross-sectional reversal, long losers only |
| signal timeframe | 1d |
| formation horizon | 70 daily bars (10 weeks), trailing simple close-to-close return |
| skip | 0 (mechanical T+1 open execution only) |
| rebalance | weekly = `rebalance_hours` 168 (epoch-aligned 7-day blocks) |
| ranking | ascending trailing 70-bar return; symbol-name tie-break |
| qualification | trailing return < 0 (actual losers only; never the least-positive winner); ≥71 completed bars; pool member |
| positions | top 2, equal weight; unqualified slots → cash; zero qualifiers → 100% cash |
| weighting | equal weight inside strategy target; production overlay applies sleeve (10%) / per-asset (5%) / 2-position caps |
| warmup | 71 completed 1d bars |

## Results (eval window 2021-04-01 .. 2026-07-31; net of 28 bps/side costs)

| Metric | 1x | 2x | 3x |
|---|---|---|---|
| return | +3.56% | -0.24% | -4.70% |
| CAGR | 0.66% | -0.05% | -0.90% |
| Sharpe | 0.127 | 0.029 | -0.090 |
| Sortino | 0.177 | — | — |
| Calmar | 0.054 | — | — |
| max drawdown | 12.15% | 13.05% | 13.94% |
| profit factor | 1.073 | 0.995 | 0.908 |
| turnover | 14.26 | — | — |
| fees | $4,144 (52.7% of gross PnL) | — | — |
| trades / rebalances | 135 / 263 | — | — |
| avg holdings / exposure | 2.15 / ~7.7% | — | — |

Walk-forward (anchored, 10 calendar 6-month folds): 5 positive / 5 negative,
median fold return -0.05%, best +7.08% (2024-H1), worst -7.54% (2022-H1).

Benchmarks (same window): BTC buy-and-hold +10.24%, ETH -2.61%, BTC/ETH
50/50 +3.82%, old `crypto_time_series_momentum_v1` (1d) +0.20%, old
`crypto_cross_sectional_momentum_1h_v1` (1h) -6.54%, reversal +3.56%.

Contribution: ETH/USD = 90.1% of TOTAL net PnL (+$3,206 of +$3,560; drop ETH
→ flat); top-1 share of positive PnL 25.2% (DOGE), top-2 49.3%, top-3 67.0%.
Calendar half-years telescope to net PnL (asserted by the harness): positive
in 7/12 half-years, profits concentrated in bull half-years (2023, 2024-H1),
losses in bear half-years (2022, 2026-H1) — consistent with high-beta loser
exposure rather than a robust standalone edge.

Mechanism diagnostics (diagnostic only, never promoted to parameters):
forward 1-week return by formation depth — <-50% +2.28%/wk (win 54.8%),
-50..-30% +0.59%, -30..-15% -1.59%, -15..0% +0.63%; by 14d realized vol —
<50% +1.67%, 50-100% -0.35%, 100-200% +0.71%, >200% -4.89%; by 14d median
daily dollar volume — 1k-100k +0.19%, >=10M +1.01%; by 60d BTC correlation —
0.5-0.8 +0.25%, >=0.8 +0.21%. The deep-loser rebound exists but is small,
concentrated in the least liquid names, and below the 56 bps round-trip
cost hurdle at realistic turnover.

## Correctness

- Independent recomputation (Wave 2): ranking date, target portfolio, fill
  sequence, weekly return period, and fee calculation all match the
  production engine BIT-EXACTLY (independent pandas arithmetic vs engine).
- No P0/P1 measurement defects found in the final adversarial review;
  headline metrics internally consistent; calendar-half-year attribution
  bug (boundary-day anchoring) found by review and FIXED, artifact
  regenerated with a telescoping assertion.
- Forming-bar seeding defect (Alpaca feeds include the in-progress bar;
  a seeded partial close permanently blocked the true closed bar in
  decision history) was confirmed by probe and FIXED in
  `PortfolioRuntime.seed()` and the observation-set ranking fetch
  (`bar_is_closed` / `equity_day_bar_is_closed`).
- PIT universe built once from 1h bars (scanner calibration parity);
  rank_candidates == decide via shared scoring (observation-set parity);
  weekly = hours:168; same-bar execution impossible by construction.

## Decision (this mission)

- **CRYPTO: C — `crypto_cross_sectional_reversal_v1` NOT VALIDATED; the
  crypto cross-sectional reversal family is CLOSED.**
- Failing standards: 2x-cost net return negative (-0.24%); walk-forward
  majority positive FAILS (5/10); ETH = 90.1% of total PnL (concentration
  flag); fees consume 52.7% of gross PnL. No parameter tuning after seeing
  results; the deep-loser rebound diagnostic is recorded as a FUTURE
  HYPOTHESIS, not promoted.
- Config state unchanged: `strategy.crypto.enabled: false`, LIVE disabled,
  equity untouched (still `equity_residual_momentum_v1` enabled for PAPER).
- No PAPER orders were placed (no smoke opt-in present).
