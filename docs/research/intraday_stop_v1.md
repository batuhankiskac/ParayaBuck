# Intraday stop on QLD\*, pre-tax and after Turkish tax, v1 (2026-10-14)

> Evidence for this report (`research/intraday_stop_v1/` code, data inputs and results) is on
> branch `research/intraday_stop_v1`; it is not on `main`.

**FAIL — no arm passes the pre-registered rule, so production R2
(`equity_leveraged_trend_v1` 1.2.0) stays unchanged.** Four daily-re-armed stop-market
levels on QLD\* (8% and 12% below the prior close, with and without a 5-session cooldown
after a stop) were tested on top of R2. Every one of them fails, and two of them fail
badly:

- **T8 (8% stop, no cooldown)** is a whipsaw machine. It stops out **2.90 times a year**;
  **46 of its 77 stops lose money** and the net episode effect is **−2.05 pp** over the
  window. Max DD gets **worse** (51.67% against 46.76%, +4.91 pp) because being flat for
  the rest of the stop session and re-entering at the next open drops the arm into the
  following leg down, and the block-20 bootstrap median is **−0.48 pp** on only 39.7% of
  paths. It fails G1, G2, G4, G5, G6, G7′, G8′ and G10–G12.
- **T8c (8% + 5-session cooldown)** is the only arm that looks like a real candidate
  in-sample: **15.93% base / 15.37% calibrated CAGR** against R2's 15.48 / 14.90, **max DD
  42.80%** against 46.76%, Calmar 0.372 against 0.331, bootstrap **+1.12 pp** on 62.9% of
  paths, and it passes **both** clean after-tax bootstraps (B1-recent +0.91 pp at 63.0% of
  paths; B2 +1.02 pp at 65.2%). It still fails: **G3** (2017+ CAGR 30.48% against 31.71%)
  and **G10** (after tax it wins 2000 by +0.27 pp but loses 2010 by −0.33 pp and 2017 by
  −1.86 pp), and the reduced OOS is decisive against it — **median Δ CAGR −0.84 pp and only
  3 of the 21 ETF instruments positive**.
- **T12 / T12c (12% stops)** almost never fire (**0.38–0.49 stops/yr**): only the 2000 and
  2020 crashes reach −12% intraday. When they do, they sell the bottom — T12's 13 stops net
  **−9.63 pp** and cost −0.43 pp of CAGR; T12c's 10 stops net −4.86 pp. Both fail G1, G5, G6,
  G4, G7′, G8′ and G10–G12 (T12c does win the 2017 after-tax window, +0.76 pp).
- **The reduced OOS is what kills the family.** On the 21 instruments, Δ CAGR is negative in
  17–20 of 21 for every arm (median −0.31 to −0.97 pp, G7′ needs ≥ 60% positive) while the
  drawdown side is mildly better for the cooldown arms (Δ DD ≤ +3 pp in 14–18 of 21). The
  stop is a drawdown tool with a return cost, and the one arm whose return *gain* survives
  the block-20 bootstrap does not survive a different instrument set.
- The LINCHPIN caveat matches `fast_reentry_v1`'s: the equity curve is driven by a handful
  of sessions. T8c's largest single save (2015-08-21, +19.35 pp) is 21% of the sum of its 27
  saves, and T8's is 29% of its own while the arm's net effect is negative.

Evidence: `research/intraday_stop_v1/`. `PREREGISTRATION.md`, `screen.py` and
`DATA_MANIFEST.json` were committed in **`4db36e69`**, before any non-control arm was
simulated on real data (only `--smoke` and `--harness`, which touch the control only).
The outputs are `RESULTS.json` and `SUMMARY.txt` from `screen.py --run`; two consecutive
runs gave byte-identical `RESULTS.json`. One post-pre-registration bug fix is recorded in
Amendments.

## Question

Production **R2** decides once per session, at the close, and its position is a
2×-leveraged Nasdaq-100 synthetic (QLD\*). A close is not the worst price of a session:
within 2020-02/03, 2018-12, 2008-10 and 2000-04 QLD\* fell far below the prior close
intraday, and over a few sessions the trend votes can stay on while the fund loses a third
of its value. Does a resting **stop-market on QLD\***, re-armed at each close while
invested, improve R2 **after Turkish income tax** without materially worse drawdown? The
known risk is whipsaw: stopped out on a spike low, bought back higher at the next open,
paying two fees, a slippage spread and a taxable realisation.

## Method

- **Control (C0) = production R2 exactly** (`fast_reentry_v1`'s arm R2): E2's ≥ 2-of-3 vote
  on the QQQ price close (close > SMA200; 252-session price return > 0; close > midpoint of
  the 126-session max/min) plus the early re-entry path at half weight (while E2 is off:
  close > SMA50, 20-session vol < its trailing 252-session median, close > close 20 sessions
  ago; exit below SMA50; the path ends the moment E2 turns on). Sized `vt = min(1, 0.35/vol20)`
  on the QLD\* returns, total weight capped at 0.98, decide at the completed close, fill at
  the next open, 0.5 pp resize band, cost 4 bps × m per unit traded, idle cash earns zero.
- **Instrument.** Synthetic QLD\* = daily-reset 2× on QQQ total return, 0.95%/yr expense
  plus DTB3 (base) or DTB3 + 0.75 pp/yr (calibrated). Main window 2000-03-09 → 2026-09-29
  (6,679 sessions).
- **New: the modelled intraday path.** `low*_t = close*_{t-1} · (1 + 2·((low_t + div_t)/close_{t-1} − 1))`
  — the daily-reset 2× of the QQQ low's total return from the prior close; the session's gap
  open is the existing `synthetic_2x` open (`1 + 2·((open_t + div_t)/close_{t-1} − 1)`).
  Cross-check against the real QLD bars in the same file (2006-06-21 →, 5,099 sessions):
  **correlation 0.988**, mean absolute difference 97 bps of return, 88 modelled sessions below
  −8% against 86 real ones (−12%: 18 against 17).
- **Stop.** Level `S_i = (1 − X)·close*_{i-1}`, re-armed at every close while the arm holds
  (a daily-reset stop, not a ratchet; it applies to the whole held weight, production leg and
  early path alike). With the post-open-trade weight > 0: fill at `open*_i` if
  `open*_i ≤ S_i` (gap through the stop), else at `S_i` if `low*_i ≤ S_i`, else no trigger.
  The whole position is sold at `fill × (1 − slip)` with the usual `cost × |Δw|` on the exit
  notional and the weight is 0 for the rest of the session. After a stop the arm is flat for
  the rest of the session **and does not earn the close→next-open gap**; with cooldown `CD`
  the sessions `i+1 … i+CD` are forced flat, and from session `i+CD+1` the arm follows R2's
  target exactly. `slip = 10 bps` on stop fills (30 bps is report-only).
- **Arms (5, no others).** C0; **T8** (8%, CD 0); **T8c** (8%, CD 5); **T12** (12%, CD 0);
  **T12c** (12%, CD 5). Every arm uses the identical R2 target rule; only the engine differs.
- **Harness (all checks pass; `--run` stops otherwise).** C0 reproduces
  `fast_reentry_v1/RESULTS.json`'s R2 row (base/calibrated × m = 1/m = 2 CAGR, max DD,
  turnover; 12 checks, 0 outside 0.01 pp) and `r2_tbill_combo_v1/RESULTS.json`'s R2 after-tax
  anchors at $100k on all three windows plus max DD (max |diff| 0.00004 pp); the QQQ
  OHLC+dividend loader equals `trend_robustness_v2.load()` exactly; the stop engine with the
  stop disabled equals `fast_reentry_v1`'s band simulator **and**
  `trend_robustness_v2.simulate_multi` bit for bit; the single-book after-tax runner with the
  stop disabled reproduces `te.run_strategy`'s NAV path **exactly** on all three windows.
- **Main bootstrap.** Block-20 stationary, seed 20261011, 5,000 paths of 250 warm + 2,520
  measured sessions, m = 2; **each drawn session carries its QQQ (open, high, low, close,
  dividend) tuple and DTB3 jointly**, so the modelled low is drawn with the same index as the
  open and the close.
- **Reduced OOS (replacing `ewma_band_v1`'s six index windows G7/G8).** The old Yahoo index
  file's intraday extremes are placeholders or bad ticks and a stop rule feeds directly on
  them, so the six windows are not runnable for a stop arm. In their place, the fixed
  **21-ETF** test: `XLB XLE XLF XLI XLK XLP XLU XLV XLY EWJ EWG EWU EWA EWH EWS EWL EFA EEM`
  + `DIA IWM SPY` (EWC pre-excluded, it fails the `range_vol_v2` quality rule at 5.62% bad
  bars; every one of the 21 passes, worst 3.55% for EWL). Each ETF carries its own adjusted
  OHLC (`open/high/low × adjclose/close`, `close = adjclose`), its own E2 votes and early
  path, its own 2× synthetic leg with the same low formula, calibrated financing, m = 2, over
  its own full history from session 253; plus a block-20 per-instrument bootstrap (2,000
  paths of 2,772 sessions, OHLC tuple drawn jointly, pooled = 42,000 paths).
- **Gates.** `ewma_band_v1`'s G1–G6/G9 against C0 (both financings where stated), **G7′**
  (21-ETF: median Δ CAGR ≥ 0, share Δ CAGR > 0 ≥ 60%, share Δ DD ≤ +3 pp ≥ 80%) and **G8′**
  (pooled ETF bootstrap median Δ ≥ 0) in place of G7/G8; after Turkish tax at $100k
  (base financing, m = 2, liquidated, progressive): **G10** = after-tax CAGR > R2 in all
  three windows, **G11/G12** = B1-recent/B2 clean bootstrap median Δ ≥ 0 with share > 0 ≥ 55%.
  All twelve must pass; selection = highest base m = 2 CAGR among passers.

## Results (main window, m = 2)

| Arm | Stop / CD | CAGR base | CAGR cal. | Max DD (base / cal.) | Calmar | 2017+ | P1 / P2 / P3 | m = 1 CAGR | Boot. median Δ base / cal. | Share ≥ C0 | Turnover/yr | Stops/yr |
|---|---|---:|---:|---|---:|---:|---|---:|---|---:|---:|---:|
| **C0 (prod. R2)** | — | **15.48%** | **14.90%** | **46.76% / 46.88%** | **0.331** | **32.71%** | −0.06 / 14.30 / 32.71 | **15.75%** | — | — | **5.99** | **0.00** |
| T8 | 8% / 0 | 15.23% | 14.66% | 51.67% / 51.76% | 0.295 | 32.00% | −1.12 / 15.60 / 32.00 | 15.60% | **−0.480 / −0.470 pp** | 0.397 | 8.07 | 2.90 |
| T8c | 8% / 5 | **15.93%** | **15.37%** | **42.80% / 42.87%** | **0.372** | 30.48% | 1.27 / 16.51 / 30.48 | **16.25%** | **+1.124 / +1.145 pp** | 0.629 | 6.80 | 1.81 |
| T12 | 12% / 0 | 15.05% | 14.47% | 47.43% / 47.55% | 0.317 | 32.11% | −0.20 / 13.70 / 32.11 | 15.34% | −0.547 / −0.542 pp | 0.255 | 6.26 | 0.49 |
| T12c | 12% / 5 | 15.23% | 14.65% | 49.23% / 49.33% | 0.309 | 33.68% | −0.60 / 13.16 / 33.68 | 15.51% | −0.441 / −0.436 pp | 0.387 | 6.11 | 0.38 |

Sharpe (base): C0 0.678, T8 0.675, T8c 0.711, T12 0.663, T12c 0.673. Bootstrap medians:
C0 7.89% / 7.44%, T8 7.51 / 7.04, T8c 9.01 / 8.58, T12 7.18 / 6.71, T12c 7.39 / 6.92.
Over the Stage B window (2017-01-03 → 2026-07-30, base, m = 2): C0 31.70% / 31.13%, T8
30.97 / 30.80, T8c 29.45 / 30.35, T12 31.09 / 31.13, T12c 32.67 / 31.13.
Report-only m = 4 cost stress (base CAGR): C0 14.92, T8 14.49, T8c 15.30, T12 14.47,
T12c 14.67; max DD 47.44 / 53.19 / 43.82 / 48.28 / 49.95.

### Stop activity and attribution (base, m = 2, report-only)

| Arm | Stops | /yr | Δ sum | Δ median | Saves / whipsaws | Σ saves / Σ whipsaws | Re-entered above fill | Π(1+Δ) vs actual |
|---|---:|---:|---:|---:|---|---:|---:|---|
| T8 | 77 | 2.90 | −2.05 pp | −0.17 pp | 31 / 46 | +66.39 / −68.44 pp | 50 / 77 | 0.945481 vs 0.944971 |
| T8c | 48 | 1.81 | +15.73 pp | +0.25 pp | 27 / 21 | +90.53 / −74.80 pp | 27 / 48 | 1.110158 vs 1.110577 |
| T12 | 13 | 0.49 | −9.63 pp | −0.95 pp | 4 / 9 | +5.71 / −15.33 pp | 9 / 13 | 0.905553 vs 0.905553 |
| T12c | 10 | 0.38 | −4.86 pp | +0.56 pp | 6 / 4 | +13.75 / −18.61 pp | 5 / 10 | 0.944411 vs 0.944549 |

The tiles are disjoint and start/end at the two arms' NAV closes, so Π(1+Δ) reproduces the
arm's total relative gain to 0.054% (residual: the band leaves the two arms' weights a few
bp apart after a re-entry). Turnover: C0 5.99/yr (entries+exits 3.84, resizes 2.15); T8
8.07 (4.14 / 1.99 / stops 1.49); T8c 6.80 (3.65 / 1.90 / 1.04); T12 6.26 (3.90 / 2.12 /
0.20); T12c 6.11 (3.82 / 2.10 / 0.17). Since 2017: T8 2.26 stops/yr, T8c 1.54, T12/T12c 0.21.

**Five biggest saves** (stop session, effective fill, re-entry session and open, sessions
flat, Δ):

| Arm | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| T8 | 2015-08-21 fill 0.7237 → 2015-09-16 @0.7708 (17 flat) **+19.35 pp** | 2020-02-27 2.5358 → 2020-02-28 @2.3134 (1) +6.83 | 2000-04-14 2.1532 → 2000-04-17 @1.8225 (1) +6.54 | 2020-03-11 2.2138 → 2020-04-09 @2.0833 (21) +4.99 | 2020-09-08 3.9186 → 2020-09-09 @2.1576 (1) +3.76 |
| T8c | 2015-08-21 0.7237 → 2015-09-16 @0.7708 (17) **+19.35 pp** | 2020-09-03 4.4812 → 2020-09-14 @3.9747 (6) +9.10 | 2000-04-10 3.1992 → 2000-04-18 @2.3739 (6) +8.78 | 2000-03-29 3.7487 → 2000-04-06 @1.1596 (6) +5.38 | 2020-02-24 2.8769 → 2020-03-03 @1.8849 (6) +5.28 |
| T12 | 2000-04-14 2.0596 → 2000-04-17 @1.8225 (1) +3.72 | 2000-04-10 3.0601 → 2000-04-11 @2.8724 (1) +1.87 | 2000-05-02 2.3504 → 2000-05-03 @2.3325 (1) +0.12 | 2001-02-02 0.8014 → 2001-05-17 @2.2787 (72) +0.00 | 2000-04-03 3.2529 → 2000-04-04 @1.1919 (1) −0.06 |
| T12c | 2020-09-03 4.2864 → 2020-09-14 @2.2204 (6) +4.45 | 2000-04-03 3.2529 → 2000-04-11 @1.1584 (6) +3.22 | 2020-03-09 2.1680 → 2020-04-09 @2.2310 (23) +2.74 | 2000-05-02 2.3504 → 2000-05-10 @1.1063 (6) +2.22 | 2000-04-12 2.5581 → 2000-04-20 @1.1039 (6) +1.12 |

**Five biggest whipsaws** (same columns):

| Arm | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| T8 | 2010-05-07 fill 0.1491 → 2010-05-10 @0.1678 (1) **−7.72 pp** | 2000-04-27 2.0597 → 2000-04-28 @2.5629 (1) −5.26 | 2024-08-05 7.0697 → 2024-08-06 @7.5151 (1) −4.17 | 2000-06-02 1.9462 → 2000-06-05 @1.0285 (1) −3.97 | 2026-06-09 15.8320 → 2026-06-10 @10.0861 (1) −3.35 |
| T8c | 2024-08-05 7.0697 → 2024-08-13 @8.1483 (6) **−8.90 pp** | 2020-06-11 2.9977 → 2020-06-19 @3.2634 (6) −6.82 | 2000-04-24 2.0515 → 2000-05-02 @2.6316 (6) −6.80 | 2000-07-05 2.2619 → 2000-07-13 @1.3225 (6) −5.80 | 2026-06-05 16.9893 → 2026-06-15 @10.1154 (6) −5.10 |
| T12 | 2010-05-06 0.1528 → 2010-05-07 @0.1597 (1) −4.10 | 2020-03-09 2.1680 → 2020-03-11 @2.2964 (2) −3.42 | 2000-04-24 1.9782 → 2000-04-25 @2.2082 (1) −2.72 | 2000-07-28 1.9843 → 2000-08-08 @1.0630 (7) −1.31 | 2000-04-04 2.8621 → 2000-04-05 @1.1417 (1) −1.13 |
| T12c | 2010-05-06 0.1528 → 2010-05-14 @0.1680 (6) **−7.72 pp** | 2000-04-24 1.9782 → 2000-05-02 @2.6316 (6) −7.61 | 2000-05-10 1.8856 → 2000-05-18 @2.2650 (6) −1.96 | 2000-07-28 1.9843 → 2000-08-08 @1.0630 (7) −1.31 | 2001-02-02 0.8014 → 2001-05-17 @2.2787 (72) +0.00 |

The stop-outs cluster where expected: 2000–02 (the Nasdaq bust), 2008–09, 2010-05-06, 2015-08,
2020-02/03, 2022, 2024-08, 2025–26. The biggest save of **both** 8% arms is the same stop —
2015-08-21, the Friday before the 2015-08-24 flash crash — where R2 was flat for the following
17 sessions; it is 21–29% of each arm's sum of saves. 2015-08-24 is also the day the modelled
QLD\* low (−34.5%) overstates the real one (−17.3%) because the *QQQ print* was a bad tick, but
the arm's fill that day was the genuine −16.0% gap open (`open* ≤ S`), not the low.

### Out of sample: 21 instruments (calibrated, m = 2)

| Instrument | C0 CAGR / DD | T8 ΔCAGR / ΔDD | T8c ΔCAGR / ΔDD | T12 ΔCAGR / ΔDD | T12c ΔCAGR / ΔDD |
|---|---|---|---|---|---|
| XLB | 1.22% / 56.0% | −0.77 / +0.1 | +1.12 / −2.2 | −0.86 / +0.0 | −0.24 / +0.0 |
| XLE | 6.75% / 72.4% | −1.61 / −1.6 | −2.88 / +4.6 | −0.41 / −2.4 | +0.07 / −6.4 |
| XLF | 3.07% / 57.5% | +0.48 / −3.2 | −0.72 / −2.4 | −0.09 / +0.0 | −0.48 / +0.0 |
| XLI | 6.21% / 58.5% | −0.59 / −1.5 | −2.18 / +2.2 | −0.24 / +0.5 | −0.55 / +2.3 |
| XLK | 14.13% / 52.7% | −1.44 / +2.3 | −1.87 / −1.4 | −1.08 / +4.1 | −0.95 / +3.6 |
| XLP | 2.71% / 58.0% | +0.74 / −2.5 | +0.55 / −2.9 | −0.02 / +0.0 | −0.20 / +0.0 |
| XLU | 5.60% / 50.0% | −0.62 / +1.4 | +0.50 / −6.6 | −0.08 / +0.2 | +0.11 / +0.8 |
| XLV | −0.08% / 77.1% | −0.46 / +3.0 | −0.18 / +0.9 | −0.43 / +0.1 | −0.18 / +0.1 |
| XLY | 6.43% / 54.9% | −0.75 / +5.0 | −1.54 / +6.1 | −0.08 / +0.0 | −0.36 / +0.6 |
| EWJ | 0.15% / 74.9% | −1.30 / +2.9 | −0.84 / −0.4 | −0.31 / −1.8 | +0.15 / −4.1 |
| EWG | 5.26% / 66.6% | −1.43 / +3.3 | −1.96 / +1.3 | −0.58 / +1.2 | −0.61 / −1.5 |
| EWU | 2.77% / 60.5% | −0.97 / −2.1 | −0.62 / −1.4 | +0.05 / −2.5 | +0.05 / −2.5 |
| EWA | 2.51% / 70.6% | −1.57 / +5.9 | −0.84 / +3.5 | −0.97 / +0.3 | −0.54 / +0.3 |
| EWH | 5.45% / 52.2% | −5.41 / +10.5 | −4.36 / +6.1 | −2.09 / +3.7 | −1.89 / +2.5 |
| EWS | 8.58% / 45.2% | −4.30 / −1.0 | −5.47 / +8.0 | −3.09 / +4.4 | −3.05 / +0.0 |
| EWL | 4.17% / 74.3% | −2.36 / +1.3 | −0.63 / +0.3 | −1.27 / +0.0 | −1.62 / +0.0 |
| EFA | 8.76% / 45.6% | −1.34 / +1.2 | −1.64 / +0.7 | −0.04 / +0.0 | −0.22 / +0.0 |
| EEM | 4.48% / 57.2% | −2.90 / +8.4 | −2.91 / +12.3 | −1.64 / +3.6 | −2.09 / +5.9 |
| DIA | 5.50% / 71.7% | +0.03 / +0.2 | −0.63 / +2.0 | −0.31 / +0.2 | −0.94 / +0.9 |
| IWM | 3.98% / 50.4% | −0.91 / +4.2 | −1.71 / +3.5 | −0.17 / +0.9 | −0.35 / +0.3 |
| SPY | 11.25% / 57.9% | +0.57 / −0.9 | −0.37 / +1.4 | −0.04 / +2.0 | −0.75 / +4.1 |
| **Median Δ CAGR** | — | **−0.97** | **−0.84** | **−0.31** | **−0.48** |
| **Instruments Δ CAGR > 0** | — | 4/21 | 3/21 | 1/21 | 4/21 |
| **Δ DD ≤ +3 pp** | — | 14/21 | 14/21 | 17/21 | 18/21 |
| **Max Δ DD** | — | +10.46 | +12.34 | +4.41 | +5.86 |
| **Pooled bootstrap median Δ** | — | −0.877 | −0.744 | −0.492 | −0.461 |

Stops/yr per instrument for T8 average 1.5 (0.37 XLP … 3.25 XLE) against 2.90 for QQQ: the
Nasdaq-100 is far more stop-prone than the average single-country or sector ETF.

### Gates

| Gate | T8 | T8c | T12 | T12c |
|---|---|---|---|---|
| G1 return (base / cal.) | fail / fail | pass / pass | fail / fail | fail / fail |
| G2 DD ≤ C0 + 1 pp (both) | **fail** (51.67) | pass | pass | **fail** (49.23) |
| G3 2017+ ≥ C0 − 1 pp (both) | pass | **fail** (−2.23) | pass | pass |
| G4 sub-periods | fail (P2) | pass (P1, P2) | fail (none) | fail (P3) |
| G5 bootstrap Δ ≥ 0 (both) | **fail** (−0.480 / −0.470) | pass (+1.124 / +1.145) | **fail** (−0.547 / −0.542) | **fail** (−0.441 / −0.436) |
| G6 G1 at m = 1 (both) | **fail** | pass | **fail** | **fail** |
| G7′ 21-ETF reduced OOS | **fail** (median −0.97 pp, 4/21 positive, 14/21 DD) | **fail** (−0.84, 3/21, 14/21) | **fail** (−0.31, 1/21, 17/21) | **fail** (−0.48, 4/21, 18/21) |
| G8′ pooled ETF bootstrap | **fail** (−0.877) | **fail** (−0.744) | **fail** (−0.492) | **fail** (−0.461) |
| G9 turnover ≤ 2 × C0 (11.98) | pass (8.07) | pass (6.80) | pass (6.26) | pass (6.11) |
| G10 after tax beats C0, all 3 windows | **fail** (0/3) | **fail** (1/3) | **fail** (0/3) | **fail** (1/3) |
| G11 B1-recent median Δ ≥ 0, share > 0 ≥ 55% | **fail** (−0.432, 0.390) | pass (+0.907, 0.630) | **fail** (−0.513, 0.262) | **fail** (−0.391, 0.368) |
| G12 B2 median Δ ≥ 0, share > 0 ≥ 55% | **fail** (−0.289, 0.429) | pass (+1.019, 0.652) | **fail** (−0.505, 0.260) | **fail** (−0.388, 0.365) |
| **Result** | **fail** | **fail** | **fail** | **fail** |

### After tax at $100k (base financing, m = 2, liquidated, progressive Turkish tariff)

| Window | Arm | Pre-tax CAGR | After-tax CAGR | Δ vs C0 | Max DD | Tax paid | Trades/yr | Sells/yr | Stops/yr |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | C0 | 15.47% | 10.16% | — | 46.76% | 766,160 | 62.7 | 30.4 | 0.00 |
| 2000-03 → 2026-09 | T8 | 15.23% | 9.90% | −0.26 pp | 51.65% | 737,419 | 62.5 | 31.4 | 2.90 |
| 2000-03 → 2026-09 | T8c | 15.93% | **10.43%** | **+0.27 pp** | 42.79% | 856,479 | 57.5 | 28.2 | 1.81 |
| 2000-03 → 2026-09 | T12 | 15.04% | 9.85% | −0.31 pp | 47.43% | 705,242 | 62.5 | 30.5 | 0.49 |
| 2000-03 → 2026-09 | T12c | 15.23% | 9.99% | −0.17 pp | 49.22% | 726,439 | 61.4 | 29.9 | 0.38 |
| 2010-01 → 2026-09 | C0 | 22.16% | 14.71% | — | 43.78% | 553,536 | 62.2 | 30.9 | 0.00 |
| 2010-01 → 2026-09 | T8 | 22.43% | 14.67% | −0.04 pp | 37.73% | 571,769 | 61.9 | 31.2 | 1.67 |
| 2010-01 → 2026-09 | T8c | 22.07% | 14.38% | −0.33 pp | 35.71% | 547,534 | 58.7 | 29.0 | 1.19 |
| 2010-01 → 2026-09 | T12 | 21.53% | 14.20% | −0.51 pp | 43.75% | 509,960 | 62.1 | 30.9 | 0.18 |
| 2010-01 → 2026-09 | T12c | 22.09% | 14.63% | −0.08 pp | 43.72% | 543,846 | 61.6 | 30.5 | 0.18 |
| 2017-01 → 2026-09 | C0 | 32.53% | 22.89% | — | 36.24% | 360,297 | 70.9 | 35.2 | 0.00 |
| 2017-01 → 2026-09 | T8 | 31.82% | 22.19% | −0.70 pp | 35.97% | 349,386 | 70.9 | 35.7 | 2.26 |
| 2017-01 → 2026-09 | T8c | 30.31% | 21.02% | −1.86 pp | 35.51% | 310,701 | 65.9 | 32.3 | 1.54 |
| 2017-01 → 2026-09 | T12 | 31.93% | 22.43% | −0.45 pp | 36.21% | 344,450 | 70.8 | 35.2 | 0.21 |
| 2017-01 → 2026-09 | T12c | 33.50% | **23.64%** | **+0.76 pp** | 36.30% | 386,401 | 70.2 | 34.9 | 0.21 |

The tariff costs every arm 5.2–5.5 pp of the full-window CAGR (C0 5.31, T8c 5.50, T12c 5.23).
No arm beats C0 in all three windows, so G10 fails for all; the cooldown arms win one window
each (T8c the 2000 one, T12c the 2017 one) and lose the others.

### Clean after-tax bootstrap (block-20, 1,000 paths, fixed 2015-09 → 2026-09 calendar, $100k)

C0's median after-tax CAGR on these paths is 3.52% (B1 progressive) and 3.97% (B2 flat) —
byte-identical to the `r2_tbill_combo_v1` reference (3.5155% / 3.9678%).

| Arm | B1 median Δ | B1 share > 0 | B1 p05 / p95 | B2 median Δ | B2 share > 0 | B2 p05 / p95 | Stops/yr |
|---|---:|---:|---|---:|---:|---|---:|
| T8 | −0.432 pp | 39.0% | −3.25 / +2.04 | −0.289 pp | 42.9% | −3.00 / +2.21 | 4.39 |
| **T8c** | **+0.907 pp** | **63.0%** | −3.99 / +5.16 | **+1.019 pp** | **65.2%** | −4.03 / +5.49 | 2.89 |
| T12 | −0.513 pp | 26.2% | −2.23 / +0.67 | −0.505 pp | 26.0% | −2.30 / +0.63 | 0.90 |
| T12c | −0.391 pp | 36.8% | −3.27 / +1.86 | −0.388 pp | 36.5% | −3.40 / +1.80 | 0.70 |

The 30 bp slippage stress (report-only, median Δ CAGR against the 10 bp run over the 21 ETFs)
costs T8 −0.172 pp, T8c −0.152, T12 −0.026, T12c −0.026. Slippage is not what kills the arms.

## Findings

- **A tight stop without a cooldown makes R2 worse, including its drawdown.** T8 stops 2.90
  times a year, loses on 46 of 77 stops, and its max DD *rises* 4.9 pp to 51.7%: selling at
  −8% and re-buying at the next open converts one slow trend exit into two trades and a
  missed rebound, and the arm is still exposed when the fall continues.
- **The 5-session cooldown is the whole idea.** It removes 29 of the 77 stops (48 left, 27
  winners), lifts CAGR 0.45 pp, cuts max DD 3.96 pp, lifts Calmar from 0.331 to 0.372 and
  passes both clean after-tax bootstraps — the only arm here to do so. But its return gain is
  a 2000–2015 phenomenon: in the 2017+ window it gives up 2.23 pp, and after tax it wins only
  the 2000 window.
- **The reduced OOS rejects the family, not just the parameter choice.** 2017+ aside, on the
  21 instruments Δ CAGR is negative in 17–20 of 21 for every arm. The cooldown arms are
  mildly *better* on drawdown there (14–18 of 21 within +3 pp) but never better on return.
  A stop that pays a return cost for a drawdown benefit is not an improvement on R2, which
  already sits at 46.8% DD; the whole point of the exercise was return.
- **The 12% stops are mostly dormant, and what they do catch is a bottom.** 0.38–0.49
  stops/yr; the ones in 2010-05-06, 2020-03-09 and 2000-04 are followed by sharp rebounds
  (e.g. T12's 2020-03-09 stop at fill 2.1680 re-enters two sessions later at 2.2964 and
  gives back 3.42 pp), so their net episode effect is negative (−9.63 pp for T12).
- **Slippage, cost and turnover are not the binding constraints.** All arms pass G9 with room
  (8.07 vs an 11.98 limit for T8), the 30 bp stress moves the ETF median by ≤ 0.17 pp, and
  the extra taxable realisations are small next to production's 30 sells/yr from vol resizes.
- **The result is concentrated, as in `fast_reentry_v1`.** T8's biggest save (2015-08-21,
  +19.35 pp) outweighs its whole net effect; T8c's five biggest saves are +51.0 pp of its
  +90.5 pp of saves. The block-20 bootstrap's positive median for T8c is therefore a
  statement about how often those specific sessions recur, not about a robust rule.

## Amendments

The pre-registration (`4db36e69`) fixed the arms, parameters, gates and selection rule; none
of them changed. Two changes to `screen.py` followed:

1. **Report-only bug fix (`c13f121d`).** `stop_episodes` read `opens[rk + 1]` (a 1999–2000
   price) instead of the re-entry session's own open `opens[START + rk]`, so the
   `reentry_open` and `reentered_above` fields (and the "re-entered above fill" counts, which
   are 50/77, 27/48, 9/13, 5/10 in the final tables) were wrong in the first full run. No
   gated metric, arm parameter, gate or selection input changed, and every gated number in
   this report is identical before and after the fix; the second full run was made on the
   fixed code.
2. The report-only per-stop attribution uses disjoint tiles (`[k, min(k + CD + 2, next stop))`)
   rather than overlapping spans, so that Π(1 + Δ) reproduces the arm's total relative gain;
   the first draft's overlapping spans double-counted consecutive stops (T8's save sum read
   +8.34 pp against a −5.66 pp actual relative gain). Report-only; no gate reads it.

## Caveats

- **All arms were designed and gated on one history.** 6,679 main-window sessions, and the
  stop levels 8%/12% were fixed a priori as round numbers, not fitted.
- **The stop's fill model is optimistic in one direction and pessimistic in another.** The
  gap case fills exactly at the open (no gap slippage beyond the 10 bps), while a same-session
  entry that gaps below its own stop is stopped out immediately at that open, which a real
  broker might fill differently. Both are documented in the pre-registration.
- **The modelled low is the 2× of the QQQ low, not QLD's own low.** It is 0.988-correlated
  with the real thing over the 2006+ overlap and matches the ≤ −8% / ≤ −12% counts (88/86,
  18/17), but the worst single bar (2015-08-24) is a QQQ bad tick; the arm's fill that day was
  a genuine gap open.
- **Index OHLC is not usable out of sample**, which is why G7/G8 are replaced by the ETF
  set. The ETF sample is independent by instrument, not by time, and the ETF stop count is
  much lower than QQQ's (1.6/yr average against 2.90), so the ETF test is a *weaker* exercise
  of the same rule — it can reject, as it does, but it cannot confirm a QQQ-specific edge.
- The after-tax figures assume zero idle-cash return, the research 4 bps × m cost model and
  the 2026 tariff with Yi-UFE indexation; liquidation at the window end is what makes the
  final year's gains taxable.
- No `src/`, `config/`, `tests/` or production-path change; R2 stays the production arm.
