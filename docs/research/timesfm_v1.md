# TimesFM v1 (2026-10-12)

**No TimesFM arm passes the pre-registered gate.** Google's time-series
foundation model TimesFM 2.5 does not improve VT35, either as a volatility
forecaster or as a direction signal.

- **V1 (TimesFM vol):** gives up 0.14 pp of CAGR. It fails G3, G5′ and G6.
  It also loses to a plain EWMA volatility (V0), so even its smaller
  drawdown is not specific to TimesFM.
- **D1 and D2 (TimesFM direction):** both are far worse than VT35, at about
  −5.5 to −6.1 pp CAGR with much deeper drawdowns.

The simple-baseline comparator **V0 passes every gate and is the selected arm
of this campaign.** V0 is VT35 with vol20 replaced by RiskMetrics EWMA vol
(λ 0.94). It gains +0.11 pp of CAGR and lowers max DD from 46.1% to 43.7%.
This is a finding about EWMA, not about TimesFM. The margin is small. Under
the pre-registration a passing arm is **not promoted** here; promotion needs a
separate pre-registered step. **VT35 is unchanged.**

This document is the only record kept on main. The evidence directory
`research/timesfm_v1/` was removed from main on 2026-10-12 and is preserved
in tag `archive/research/timesfm-v1-2026-10`:

- `PREREGISTRATION.md`, committed in `e0efb9f` before any market forecast
  was run or any arm was simulated;
- `generate_forecasts.py`, `forecasts.csv`, `forecasts.meta.json` and
  `g9_verify.json`;
- `screen.py`, `RESULTS.json` and `SUMMARY.txt`.

The only market inputs are the committed
`data/historical/stress_robustness_v1_{daily,dtb3}.csv`.

## Method

- **Model:** `google/timesfm-2.5-200m-pytorch`, revision `1d95242`, with
  Apache-2.0 weights. TimesFM 3.0 is excluded because its weights are
  non-commercial and non-production.
  - It runs from `timesfm==3.0.2` and torch 2.14.1 on CPU, in a separate venv
    outside the repo environment.
  - The configuration is the model card's: context 1024, horizon 64, a
    continuous quantile head, and `normalize_inputs`, flip invariance,
    `infer_is_positive` and quantile-crossing fix all on.
  - All forecasts use the median (q0.5).
- **Forecasts:** both series are strictly causal. Each forecast for session t
  uses data through the close of t only, with a context of min(history, 1024)
  values and at least 250.
  - **VOL:** the model forecasts the series of VT35's vol20 (QLD\*),
    and vol_tfm is the median at h = 20.
  - **DIR:** the model forecasts the log QQQ total-return close. The medians
    at h = 20 and h = 60 are stored.
  - The forecasts were generated once: 6,663 dates × 2 series in 442 s on CPU,
    cached to `forecasts.csv`.
  - `screen.py` reads only the cache. It is offline, takes about 1 s, and two
    runs gave byte-identical `RESULTS.json`.
- **Mechanics:** the same as `trend_robustness_v2`.
  - The instrument is synthetic QLD\*: daily-reset 2x on QQQ total return,
    with expense plus T-bill financing.
  - The signal is taken on the completed close and trades at the next open.
  - The rebalance band is 0.5 pp and the total weight is capped at 0.98.
  - Costs are 4 bps × m per unit traded. Idle cash earns zero.
  - The engine and metrics are imported from `trend_robustness_v2/screen.py`.
- **Checks:**
  - **Harness:** C0 on 2000-03-06 → 2026-09-29 reproduces
    `trend_robustness_v2` C0 exactly at both cost levels (m = 2: 13.3655%
    CAGR and 49.5643% max DD; difference 0.0000 pp).
  - **Cache:** the cached vol20 and log TR inputs equal the screen's
    recomputation exactly.
  - **G9:** 50 seeded dates regenerated in a fresh process at a different
    batch size match the cache bit for bit (max relative difference 0).
- **Common window:** 2000-04-03 → 2026-09-29, 6,662 sessions. It starts 20
  sessions later than `trend_robustness_v2`, because the VOL series needs 20
  returns plus 250 values of context. The pre-registration says "6,663
  sessions", but 6,663 is the number of forecast dates; there are 6,662
  measured sessions.
- **Arms:**
  - **C0:** VT35.
  - **V1:** vol20 replaced by the TimesFM vol forecast.
  - **V0:** vol20 replaced by EWMA vol.
  - **D1:** the 200-SMA trend replaced by "TimesFM h = 60 forecast >
    current log close".
  - **D2:** VT35 that also requires the h = 20 forecast to be above the
    current log close.

## Results (2x costs, gated)

P1 = 2000-04-03 → 2008-12-31, P2 = 2009 → 2016, P3 = 2017 → end (this is
also the 2017+ CAGR). The holdout runs 2025-09-15 → 2026-09-29 and comes after
the model's release; the holdout column is total return.

| Arm | CAGR | Max DD | Calmar | Sharpe | P1 | P2 | P3 (2017+) | Holdout | Turnover/yr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **C0 VT35** | **13.56%** | **46.1%** | **0.29** | **0.62** | **−2.45%** | **12.95%** | **30.73%** | **35.2%** | **6.5** |
| V1 TimesFM vol | 13.42% | 44.1% | 0.30 | 0.61 | −2.39% | 12.48% | 30.68% | 33.8% | 7.3 |
| V0 EWMA vol | 13.67% | 43.7% | 0.31 | 0.63 | −1.53% | 13.20% | 29.74% | 33.4% | 6.3 |
| D1 TimesFM trend | 8.07% | 74.5% | 0.11 | 0.42 | −13.16% | 17.45% | 22.84% | 39.1% | 13.7 |
| D2 trend + TimesFM filter | 7.44% | 62.7% | 0.12 | 0.42 | −9.51% | 6.24% | 26.51% | 31.7% | 16.1 |
| SPY hold | 8.22% | 55.2% | 0.15 | 0.51 | −4.04% | 14.36% | 15.23% | 17.8% | 0 |

At 1x costs the CAGR / max DD figures are:

| Arm | CAGR (1x) | Max DD (1x) |
|---|---:|---:|
| C0 | 13.85% | 45.8% |
| V1 | 13.75% | 43.7% |
| V0 | 13.95% | 43.4% |
| D1 | 8.66% | 72.6% |
| D2 | 8.13% | 59.1% |

| Gate | V1 | V0 | D1 | D2 |
|---|---|---|---|---|
| G1 CAGR ≥ C0 − 0.5 pp | pass | pass | fail | fail |
| G2 DD ≤ C0 | pass | pass | fail | fail |
| G3 CAGR > C0 or Calmar ≥ 1.1 × C0 | fail | pass | fail | fail |
| G4 2017+ ≥ C0 − 2 pp | pass | pass | fail | fail |
| G5′ beats C0 in ≥ 2 of 3 sub-periods | fail (P1 only) | pass (P1, P2) | fail | fail |
| G6 G1–G3 at 1x | fail | pass | fail | fail |
| G7 turnover ≤ 2 × C0 | pass | pass | fail | fail |
| G8 holdout ≥ C0 − 2 pp | pass | pass | pass | fail |
| G9 reproducibility | pass | n/a | pass | pass |
| **Result** | **fail** | **pass** | **fail** | **fail** |

**V1 vs V0:** V0 wins on both CAGR (13.67% against 13.42%) and Calmar (0.313
against 0.304), so V1 does not beat V0. Even if V1 had passed the gates, it
would not have been eligible for selection.

## Forecast diagnostics (report only)

**Volatility.** The target is realized vol over the next 20 sessions. The
losses below are on annualized variance, lower is better, over n = 6,643
dates.

| Forecast | MSE | QLIKE | Holdout QLIKE |
|---|---:|---:|---:|
| vol20 (VT35, naive) | 0.1416 | 0.400 | 0.421 |
| EWMA 0.94 | 0.1157 | 0.318 | 0.273 |
| TimesFM h = 20 | 0.1219 | 0.348 | 0.308 |

TimesFM is a better volatility forecaster than VT35's trailing vol20, but
worse than a one-line EWMA, on the full window and on the holdout.

**Direction.** The table compares the hit rate of "forecast up" with the
unconditional up-rate, which is the hit rate of "always up".

| Horizon | Hit rate | Always-up | Up-calls | Precision of up-calls | Holdout hit / always-up |
|---|---:|---:|---:|---:|---:|
| h = 20 | 58.4% | 62.3% | 79.3% | 63.1% | 57.0% / 57.9% |
| h = 60 | 61.6% | 67.9% | 77.6% | 69.0% | 68.8% / 68.8% |

The direction forecasts lose to "always up" at both horizons. On the holdout
they call "up" on 97–100% of dates.

## Findings beyond the gate

- **No market-timing skill in the TimesFM direction forecasts.** As a trend
  replacement (D1), TimesFM lost far more than the 200-SMA in 2008 (−48.1%
  that year, against VT35's −18.3%) and 2022 (−52.3% against −20.2%).
  - D1's max DD is 74.5%, with 14.9 years underwater.
  - As an extra exit filter (D2), it mostly takes the position to cash at
    the wrong times. D2 is in the market on 62% of sessions against VT35's
    73%, and it has 2.5× the turnover.
  - Both arms' sub-period results are mixed: D1 wins P2, which a lucky
    stretch can produce. Neither arm comes close on the full window.
- **The volatility gain is from smoothing, not from TimesFM.** Both forecast
  arms (V1 and V0) cut max DD by about 2–2.5 pp against vol20, in the
  2000–2003 drawdown that sets max DD for C0, V1 and V0. EWMA does this with better CAGR, Calmar and
  forecast losses. V1's turnover is 12% higher than C0's, while V0's is
  3% lower.
- **Contamination is not the explanation.** On the post-release holdout,
  V1, V0 and D1 are within 2 pp of C0 (D1 is ahead). D2 is vetoed by G8
  (−3.4 pp). The full-history picture holds on the holdout: no TimesFM
  edge.
- **About V0.** EWMA beats C0 by only 0.11 pp of CAGR at 2x costs, and it
  loses 1.0 pp/yr since 2017 (29.74% against 30.73%). It passes on a lower
  drawdown (43.7% against 46.1%), a better Calmar and two of the three
  sub-periods. This is a small, single-window advantage. It is recorded as
  a candidate for a separate pre-registered campaign and is not adopted.
- **Practicality.** Even a passing TimesFM arm would need torch plus a
  0.9 GB checkpoint on the GCP VM, and about 0.03 s per forecast on CPU (batched). EWMA
  needs neither.

## Decision

VT35 stays the PAPER identity with no parameter change. No TimesFM arm
passes, so the TimesFM question is closed: TimesFM 2.5 adds nothing to VT35.
V0 (EWMA vol sizing) passed this screen as the non-TimesFM comparator. Under
the pre-registration it is not promoted. Adopting it would require its own
pre-registered step:

- a production implementation;
- a Stage B replay against VT35;
- parity tests.

No code, configuration or runtime changed.
