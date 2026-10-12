# TimesFM v1 — pre-registration (2026-10-12)

Written before any arm below was simulated and before any forecast of market
data was generated.

**Seen before writing:**

- `research/trend_robustness_v2/` (`PREREGISTRATION.md`, `screen.py`,
  `RESULTS.json`, `SUMMARY.txt`) and `docs/research/trend_robustness_v2.md`.
  This includes the C0 (VT35) results on 2000-03-06 → 2026-09-29.
- The coverage and session calendar (dates only) of
  `data/historical/stress_robustness_v1_daily.csv`. These fixed the common
  window below.
- The TimesFM 2.5 model card at the pinned revision and the `timesfm` 3.0.2
  library source.
- A smoke test on one synthetic sine-plus-noise series, and a self-test of
  `generate_forecasts.py` on synthetic random-walk bars. Neither used market
  data.

No TimesFM forecast of market data, no arm signal, and no return, drawdown or
forecast-accuracy statistic of any arm was computed.

## Question

Does the time-series foundation model TimesFM 2.5 improve VT35, either as a
volatility forecaster for sizing or as a direction signal for timing? The
owner's goal is maximum long-run profit above index funds and interest.

## Model (fixed)

- **Checkpoint:** `google/timesfm-2.5-200m-pytorch`, revision
  `1d952420fba87f3c6dee4f240de0f1a0fbc790e3`, `model.safetensors`
  SHA-256 `2f776efe6245e42b24bc4153ffdf61810140210e4bd3b01fb21f7aa779ab6ce8`.
  The weights are Apache-2.0 and have 231.3M parameters including the
  optional 30M quantile head. TimesFM 3.0 is excluded: its self-hosted
  weights carry a non-commercial, non-production license, so a 3.0 arm could
  never be deployed.
- **Software:** `timesfm==3.0.2` from PyPI, which ships the 2.5 API as
  `timesfm.TimesFM_2p5_200M_torch`. It runs with `torch==2.14.1`,
  `numpy==2.5.4`, `huggingface_hub==2.2.0` and Python 3.13.12. Everything
  lives in a separate venv, outside the repo's environment.
- **Device:** CPU (Apple Silicon) with `torch.set_num_threads(4)` and
  `torch_compile=False`. MPS is not used, because the library's decode path
  cannot run on MPS unmodified.
- **Forecast configuration:** the model card's documented configuration,
  except that `max_horizon` is 64:

  `ForecastConfig(max_context=1024, max_horizon=64, normalize_inputs=True,
  use_continuous_quantile_head=True, force_flip_invariance=True,
  infer_is_positive=True, fix_quantile_crossing=True, per_core_batch_size=32)`

  The library rounds `max_horizon` up to the 128-step output patch. The
  effective value is therefore 128, which is a single output patch with no
  autoregressive steps. `forecast(horizon=64)` is called once per input.
- **Median:** the median forecast is quantile index 5 (q0.5) of the quantile
  output, which the library also returns as its point forecast.

## Forecast series (generated once, cached)

`research/timesfm_v1/generate_forecasts.py` is the only code that runs the
model. It computes both series for every session t from index 269
(2000-03-31) to the last bar (2026-09-29). That is 6,663 dates × 2 series. It
writes `forecasts.csv` with full-precision `repr` floats and
`forecasts.meta.json` (versions, effective configuration, input and output
SHA-256). `screen.py` reads only the cache, so it is offline, deterministic
and fast.

Both series are strictly causal. The forecast for session t uses only data
through the close of t. The context is the last min(available, 1024) values
of the series ending at t, and at least 250 values are required.

- **VOL:** the input series is vol20(t), the annualized sample volatility
  (ddof 1, × √252) of the 20 QLD\* daily returns ending at close t. This is
  VT35's vol20, computed by `trend_robustness_v2/screen.py`'s `vol_n` on its
  `synthetic_2x` QLD\*. The series is defined from index 20, so 250 values
  first exist at index 269. The forecast is **vol_tfm(t)** = the median
  forecast at horizon h = 20, i.e. the forecast of vol20(t+20), which covers
  exactly the next 20 returns and none already known at t. (Changed by the
  orchestrator before any market forecast was run: the mean over h = 1..20
  would blend known past returns into the forecast.)
- **DIR:** the input series is logtr(t) = log of the QQQ total-return close.
  It is built as tr[0] = close[0] and tr[t] = tr[t−1] · (close[t] +
  div[t]) / close[t−1]. The cache stores the median forecasts at h = 20
  (**dir20(t)**) and h = 60 (**dir60(t)**), together with logtr(t).

## Data, instruments, mechanics (unchanged from `trend_robustness_v2`)

- **Data:** only the committed
  `data/historical/stress_robustness_v1_{daily,dtb3}.csv`. No download, no
  network.
- **Instrument:** QLD\* is a synthetic daily-reset 2x on QQQ total return
  with expense 0.95%/yr + DTB3 financing (`synthetic_2x`). All arms trade
  QLD\* only.
- **Mechanics:** the signal is taken on the completed close and trades at the
  next open. All legs rebalance to target when the weight drifts more than
  0.5 pp. The total weight is capped at 0.98, cost is `4 bps × m` per unit of
  traded notional, and idle cash earns zero.
- **Code reuse:** `screen.py` imports `load`, `synthetic_2x`, `sma_up`,
  `vol_n`, `vt_of`, `build_wants` (for C0), `simulate_multi`,
  `curve_stats`, `sharpe_daily` and `calmar` from
  `research/trend_robustness_v2/screen.py` and does not reimplement them.

## Arms (five, no others)

`vt(v) = min(1, 0.35 / v)`, using `vt_of`'s edge convention (v ≤ 0 gives 1).
`trend_200` = 1 when QQQ's completed close is above its 200-session SMA. All
signals are evaluated each session on the completed close; there is no state.

- **C0 — VT35 control:** QLD\* at `trend_200 · vt(vol20)`.
- **V1 — TimesFM vol:** QLD\* at `trend_200 · vt(vol_tfm)`.
- **V0 — EWMA vol (simple-baseline comparator):** QLD\* at
  `trend_200 · vt(vol_ewma)`. vol_ewma uses RiskMetrics λ = 0.94 on QLD\*
  daily simple returns: σ²(t) = λ σ²(t−1) + (1 − λ) r(t)². It is seeded at
  index 20 with the mean of r(1..20)², annualized as √(252 σ²). The seed
  weight at the first signal is 0.94²⁴⁹ ≈ 2·10⁻⁷.
- **D1 — TimesFM direction replaces trend:** QLD\* at
  `1[dir60 > logtr] · vt(vol20)`.
- **D2 — TimesFM direction as an extra filter:** QLD\* at
  `trend_200 · 1[dir20 > logtr] · vt(vol20)`. Inside uptrends, the model can
  only take the position to cash.

**Common window:** 2000-04-03 → 2026-09-29, 6,663 sessions with signal closes
2000-03-31 → 2026-09-28. It starts 20 sessions later than in
`trend_robustness_v2` (2000-03-06), because the VOL series needs 20 returns
plus a 250-value context. The DIR series and the 200-SMA are ready earlier.
All arms, including C0 and the benchmark, are measured on this window.

**Harness check:** C0 is first run on `trend_robustness_v2`'s window
(2000-03-06 → 2026-09-29). It must reproduce that file's C0 CAGR and max DD at
both m = 1 and m = 2 to 0.01 pp before any arm result is read. If it does not,
the run stops.

**Cache sanity check (before any arm is read):** the cached vol20 and logtr
must equal `screen.py`'s recomputation to 1e-12 relative on every date.

## Statistics

The precedent's statistics, all at m = 1 and m = 2 on the common window, are:

- full-window CAGR, max drawdown, Calmar, and Sharpe (daily, annualized,
  rf = 0);
- annual turnover and number of trades;
- worst calendar year;
- 2000–2002 and 2007–2009 total return and max DD;
- longest underwater period;
- 2017-01-03 → end CAGR.

New for this campaign are sub-period CAGRs for P1 2000-04-03 → 2008-12-31,
P2 2009-01-02 → 2016-12-30 and P3 2017-01-03 → end, and the holdout total
return for 2025-09-15 → 2026-09-29. Each runs from the last close before the
period to its last close. SPY total-return buy-and-hold at 1.0 is the
benchmark. There is no block bootstrap.

**Report-only forecast diagnostics.** These do not enter any gate. Evaluation
dates are the common-window signal dates t with t + h ≤ last bar.

- **Volatility accuracy:** the target is realized next-20-session vol, rv(t) =
  vol20(t + 20), the returns t+1..t+20. The forecasts compared are vol20(t)
  (naive, VT35), vol_ewma(t) and vol_tfm(t). The loss functions are on
  annualized variance:
  - MSE = mean((rv² − f²)²);
  - QLIKE = mean(rv²/f² − ln(rv²/f²) − 1).

  Forecasts ≤ 1e-4 are floored at 1e-4 for QLIKE, and the count of floored
  values is reported. The comparison is reported on the full window and on
  the holdout.
- **Direction accuracy:** for h = 20 and h = 60 the following are reported:
  - the hit rate, the share of dates with 1[dir_h > logtr(t)] =
    1[logtr(t+h) > logtr(t)];
  - the unconditional up-rate, mean 1[logtr(t+h) > logtr(t)], which is the
    hit rate of "always up";
  - the share of up-calls;
  - the precision of up-calls.

## Decision rule (fixed now)

At **2x costs (m = 2)** on the common window, an arm passes iff all of these
hold:

- **G1** CAGR ≥ C0 CAGR − 0.5 pp.
- **G2** max DD ≤ C0 max DD.
- **G3** CAGR > C0 CAGR, **or** Calmar ≥ 1.10 × C0 Calmar.
- **G4** 2017-01-03 → end CAGR ≥ C0's − 2.0 pp.
- **G5′** (replaces the bootstrap, which would need TimesFM over 5,000
  synthetic paths and is infeasible) the arm's CAGR ≥ C0's CAGR in at least
  2 of the 3 sub-periods P1, P2, P3.
- **G6** G1–G3 also hold at 1x costs (m = 1).
- **G7** annual turnover ≤ 2 × C0's.
- **G8 contamination holdout:** TimesFM 2.5 was publicly released on
  2025-09-15. Its pretraining corpus is GiftEvalPretrain, Wikimedia
  pageviews, Google Trends, and synthetic and augmented data, and it may
  contain historical market series, so full-history results may be
  optimistic. On 2025-09-15 → 2026-09-29 the arm's total return must be ≥
  C0's total return − 2.0 pp. This test has low power and acts only as a
  veto. G8 applies to every arm, including V0. The pinned revision is the
  2025-10-02 re-upload with fused QKV matrices, which the model card says
  leaves results unchanged.
- **G9 reproducibility (arms using TimesFM: V1, D1, D2):** run
  `generate_forecasts.py --verify` in a fresh process with batch size 8
  instead of 32. It regenerates 50 dates drawn with
  `numpy.random.default_rng(20261012).choice(n_dates, 50, replace=False)`.
  vol_tfm, dir20 and dir60 must match the cache to 1e-6 relative on every
  one of these dates, else no TimesFM arm can pass. This check does not apply
  to V0.

**TimesFM specificity:** V1 counts as beating V0 iff, at m = 2, V1's CAGR >
V0's CAGR or V1's Calmar ≥ 1.10 × V0's Calmar (G3 with V0 as the control).
If V1 passes G1–G9 but does not beat V0, the conclusion is that the gain is
not TimesFM-specific, and V1 is not eligible for selection.

**Selection:** among eligible passing arms, select the highest full-window
CAGR at m = 2 (tie: higher Calmar). A selected V0 is recorded as a finding
that is not about TimesFM. If no arm passes, **VT35 stays unchanged** and the
campaign closes.

A passing arm is **not** promoted by this campaign. Promotion would need a
separate pre-registered step:

- a production implementation;
- a Stage B replay on the production engine against VT35;
- parity tests;
- for a TimesFM arm, a check that the GCP VM can host torch and the 0.9 GB
  checkpoint and run the daily forecast within the decision window.

No parameter, setting or window in this file is tuned after results are seen.
No sixth arm is added.

## Execution order (phase 2)

1. Write `screen.py`. It will read only `forecasts.csv` and
   `forecasts.meta.json`, plus `g9_verify.json` once that file exists.
2. Run `generate_forecasts.py` once in the TimesFM venv with
   `HF_HUB_OFFLINE=1` (about 5–6 min on CPU), then `--verify` (G9).
3. Run `screen.py`, which does the harness check and the cache sanity check
   first, then everything else. It writes `RESULTS.json` and `SUMMARY.txt`.
4. Run `screen.py` twice and confirm the two outputs are byte-identical.
