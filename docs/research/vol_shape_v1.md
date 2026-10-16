# vol_shape_v1 — exposure-shape replication at matched average weight (I2 of `idea_survey_v5`)

> Evidence: branch `research/vol_shape_v1`; `PREREGISTRATION.md` (committed before any non-control
> arm was simulated on real data), `screen.py`, `shapes.py`, `boot_shape.py`, `RESULTS.json`,
> `SUMMARY.txt`, `DATA_MANIFEST.json`. Engine: `research/fast_engine` (tag
> `archive/research/fast-engine-v1-2026-10`), parity PASS.

**Verdict: REJECTED — none of the four exponent arms is adopted; K stays.** The shape of the
vol-target schedule **does** matter in-sample and the effect is *not* a leverage effect: at a
matched time-average weight (drift ≤ 0.49 pp) `p = 1.25 / 1.5 / 2` raise the main-window pre-tax
CAGR by +0.46 / +0.77 / +1.19 pp and *cut* max DD by 3.8 / 6.8 / 8.0 pp, and they beat K after tax
in all three windows and both clean bootstraps. They are nevertheless rejected because the
**out-of-sample pooled bootstrap (G8) median is negative** for all three (−0.013 / −0.015 /
−0.029 pp on 10,000 paths, share > 0 47.6 / 48.5 / 48.0 %), and `p = 1.25` additionally fails G7
on the ^GSPC 1934–1956 window (−0.44 pp CAGR, +2.47 pp DD). The flatter schedule (`p = 0.75`) is
worse on every pre-tax axis (+4.5 pp DD, −0.54 pp CAGR) and fails the after-tax gate set outright.
This is exactly the in-sample/out-of-sample split Cederburg, O'Doherty, Wang & Yan (JFE 2020)
document, and the OOS guard the pre-registration put in place is what kills the arms.

## 1. Idea and design

`docs/research/idea_survey_v5.md` §4.2 (I2). K sizes as `vt(σ) = min(1, 0.35/σ20)`; the vol-target
*level* (`exposure_k_v1`: 0.375/0.40/0.45 all break the DD budget) and the vol *estimator*
(`vol_window_v1`, `ewma_band_v1`, `range_vol_v1–3`) are closed axes. The **exponent** never was,
and no arm in the record ever held the **time-average** exposure fixed — which is what makes this
the first test of the *shape* rather than of the amount of leverage.

Arms: `vt_p(σ) = min(1, c_p/σ20^p)`, everything else production K (E2 2-of-3 vote + R2 early
re-entry + 0.98 cap + 0.5 pp band + A1u + H2).

| Arm | p | `c_p` | mean(vt) on the calibration window |
|---|---|---|---|
| **C0 = K** | 1.0 | 0.35 | 0.8396819604417771 |
| **E1** | 0.75 | 0.42445881653676754 | 0.8396819604417769 |
| **E2** | 1.25 | 0.29196422627125695 | 0.8396819604417767 |
| **E3** | 1.5 | 0.24593188954326456 | 0.8396819604417776 |
| **E4** | 2.0 | 0.17706308553599898 | 0.8396819604417775 |

`c_p` solved **once**, by bisection on the monotone schedule mean, on **1985-10-01 → 2000-03-08**
(^NDX daily, 3,648 sessions, 3,628 with σ20; `data/historical/vol_shape_v1_ndx.csv`, the study's
only download), base financing, zero dividends. The window is excluded from every reported
statistic. `p = 1` is `0.35` by identity, not by solving. `--harness` re-solves and asserts all
five constants to 1e-12.

## 2. Method

Everything runs on the mandatory Numba engine; no engine file is edited. The arms are a new
*combination* (target weights × the unchanged K fill rule), which is the engine's documented
extension point; the only engine limitation is that `fe_boot._boot_chunk` feeds one target series
to every arm, so `boot_shape.py` adds `_boot_chunk_shape` (the same kernel with a per-arm target
panel) and `_boot_pt_shape` (its pre-tax twin for the pooled OOS bootstrap), plus `chunk_panels`
(a verbatim copy of `fe_boot.chunk_legs` with the shapes applied). Parity asserted by `--harness`:
`parity --quick` PASS; K's published anchors all reproduced within 0.01 pp; the shape option
**inert** at `(p = 1, c = 0.35)` (target series bit-identical to `fe_data.main_inputs`, panel
builder bit-identical to `fe_boot.chunk_legs`); and **`boot_shape` with the identity shape
reproduces `fe_boot.boot`'s K arm bit for bit** (5,000 pre-tax / 1,000 after-tax per-path arrays
and the medians). The per-arm kernels equal `fe_sim.simulate` on the same paths (max |diff| 0).

Cost: 4 bps × m per unit of traded notional, `m = 2` unless stated; idle cash 0 %; next-open fill;
0.5 pp band; base (0.0 %) and calibrated (0.75 %) financing; synthetic daily-reset 2× QLD* with
0.95 %/yr expense. After tax: 2026 progressive tariff deflated by the previous October's Yİ-ÜFE,
CBRT USD/TRY at the trade date, FIFO, liquidated at the window end.

The **calibration worked as designed**: the realised mean target weight over the measured window
(2000-03-09 → 2026-09-29) drifts from K's 67.31 % by at most 0.49 pp (table below), so all four
arms are **tier N** (exposure-neutral) under the pre-registered rule and the exposure-normalised
CAGR is reported alongside.

## 3. Main window (pre-tax)

| Arm | mean w | drift pp | mean \|Δw\| | CAGR base | CAGR cal. | max DD base | max DD cal. | 2017+ | turn/yr | m=1 base | m=1 cal. | exp.-norm. CAGR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **C0 = K** | 67.310 % | +0.000 | 0.02371 | **15.7649 %** | **15.1785 %** | **46.6698 %** | **46.7948 %** | 33.2306 % | 5.4561 | 16.0173 % | 15.4303 % | 15.7649 % |
| E1 p=0.75 | 67.105 % | −0.205 | 0.02403 | 15.2207 % | 14.6375 % | 51.2141 % | 51.3459 % | 32.9484 % | 5.4067 | 15.4702 % | 14.8858 % | 15.2705 % |
| E2 p=1.25 | 67.446 % | +0.136 | 0.02359 | 16.2264 % | 15.6273 % | 42.8927 % | 43.0106 % | 33.3428 % | 5.4420 | 16.4700 % | 15.8796 % | 16.1911 % |
| E3 p=1.5 | 67.593 % | +0.283 | 0.02353 | 16.5333 % | 15.9416 % | 39.8756 % | 39.9865 % | 33.6967 % | 5.4847 | 16.7883 % | 16.1963 % | 16.4586 % |
| E4 p=2.0 | 67.795 % | +0.485 | 0.02333 | 16.9591 % | 16.3633 % | 38.6677 % | 38.9031 % | 33.6371 % | 5.4970 | 17.2167 % | 16.6196 % | 16.8282 % |

Sub-periods (CAGR / max DD, base, m = 2): P1 2000–08 / P2 2009–16 / P3 2017→

| Arm | P1 | P2 | P3 |
|---|---|---|---|
| C0 | +0.2858 % / 46.67 % | 14.2662 % / 38.46 % | 33.2306 % / 31.12 % |
| E1 | −0.6280 % / 51.21 % | 13.9268 % / 38.58 % | 32.9484 % / 31.40 % |
| E2 | +0.8263 % / 42.89 % | 14.9839 % / 38.41 % | 33.3428 % / 31.57 % |
| E3 | +1.1639 % / 39.88 % | 15.1961 % / 38.44 % | 33.6967 % / 31.21 % |
| E4 | +1.8389 % / 35.52 % | 15.8112 % / 38.67 % | 33.6371 % / 31.94 % |

The whole effect is in the 2000–2008 bear (P1): variance-scaling cuts exposure hardest in the
high-σ states that P1 is made of. The exposure-normalised CAGR confirms the gain is not leverage:
E4 still gains +1.06 pp after scaling its return back to K's average weight.

Trading activity: sells in the main-window trace 270 (K) / 262 / 255 / 256 / 248 (E1–E4); the
targets differ by more than 0.5 pp on 26–33 % of sessions but only **3.6–6.8 %** of sessions have a
different *fill decision* (A1u's band and the 0.5 pp band absorb the rest). G9 turnover ratios:
0.991 / 0.997 / 1.005 / 1.008 — the shape barely moves the trading rate.

## 4. After tax (progressive, liquidated, m = 2), Δ vs K in pp

| Arm | $100k 2000 | $100k 2010 | $100k 2017 | $10k 2000 | $1M 2000 | B1 med/shape | B2 med/share | after-tax gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| E1 | **−0.186** | +0.082 | +0.281 | −0.269 | −0.170 | −0.205 / 36.4 % | −0.263 / 32.0 % | **FAIL** (2000 window, B1, B2) |
| E2 | +0.280 | +0.216 | +0.028 | +0.302 | +0.253 | +0.158 / 66.6 % | +0.200 / 70.3 % | pass |
| E3 | +0.491 | +0.440 | +0.203 | +0.540 | +0.491 | +0.251 / 63.5 % | +0.331 / 68.3 % | pass |
| E4 | +0.805 | +0.522 | +0.010 | +0.843 | +0.861 | +0.343 / 62.2 % | +0.462 / 67.1 % | pass |

K's own anchors: $100k 10.7533 / 15.2354 / 23.9720 %; bootstrap medians 3.8577 % (B1) / 4.0376 %
(B2) at $100k. Tax paid at $100k on the 2000 window: K 830,903 USD, E1 735,932, E2 910,138,
E3 973,234, E4 1,076,625 USD — the p > 1 arms pay *more* absolute tax because they hold more in
the states where the (taxed) gains are made, yet they still win after tax; indexation relief
22.61 m (K) / 23.98 / 22.78 / 23.19 / 24.69 m TRY. Sells/yr on the 2000 window: 10.655 (K) /
10.392 / 10.241 / 10.166 / 10.053. `m = 4` cost stress after tax at $100k: K 10.3539 %, E1
10.1900, E2 10.6123, E3 10.8402, E4 11.1468 — the ordering is unchanged.

## 5. Gates vs K (as `ewma_band_v1` defines them), and the OOS evidence

| Arm | G1 b/c | G2 b/c | G3 b/c | G4 | G5 b/c | G6 b/c | G7 | G8 | G9 | strict pass |
|---|---|---|---|---|---|---|---|---|---|---|
| E1 | F / F | F / F | P / P | F (0/3) | F / F | F / F | P | P | P | **FAIL** |
| E2 | P / P | P / P | P / P | P (3/3) | P / P | P / P | **F** | **F** | P | **FAIL** |
| E3 | P / P | P / P | P / P | P (3/3) | P / P | P / P | P | **F** | P | **FAIL** |
| E4 | P / P | P / P | P / P | P (3/3) | P / P | P / P | P | **F** | P | **FAIL** |

G5 medians (pre-tax, base / calibrated, 5,000 paths): E1 −0.329 / −0.332 pp; E2 +0.247 / +0.247;
E3 +0.423 / +0.424; E4 +0.618 / +0.623. E2–E4 pass every gate that looks at the measured window
(2000–2026) and fail the two that look outside it:

- **G7**, six OOS windows (pre-tax, calibrated, m = 2, Δ CAGR pp / Δ max-DD pp):

| Window | K CAGR / DD | E1 | E2 | E3 | E4 |
|---|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.9674 / 39.65 | −0.163 / −0.213 | +0.072 / +0.044 | +0.097 / +0.104 | +0.119 / +0.256 |
| M1 ^IXIC 1975–1999 | 21.2731 / 41.22 | +0.091 / −0.075 | +0.032 / −0.019 | +0.110 / −0.034 | +0.013 / −0.055 |
| M2 ^GSPC 1934–1956 | 11.7257 / 58.11 | +0.440 / −1.517 | **−0.442 / +2.468** | −0.526 / +3.418 | **−0.816 / +5.136** |
| M3 ^N225 1966–2026 | 6.4568 / 65.18 | +0.138 / −1.684 | −0.046 / −0.520 | −0.048 / −1.203 | −0.011 / −0.192 |
| M4 ^GDAXI 1989–2026 | 9.7067 / 58.15 | −0.026 / +1.073 | −0.051 / +0.008 | −0.177 / −0.530 | −0.143 / −2.474 |
| M5 ^FTSE 1985–2026 | −0.6233 / 85.65 | +0.084 / −0.473 | +0.033 / −0.009 | +0.106 / −0.231 | +0.111 / −0.107 |
| median Δ CAGR | — | +0.088 | −0.007 | +0.025 | +0.001 |

  The 1934–1956 US window is where p > 1 loses: a high-vol, strongly mean-reverting era in which
  cutting exposure in the turbulent states is exactly wrong (+5.14 pp DD for E4). E2's median
  (−0.007 pp) fails the G7 bar; E3/E4 pass G7 only because the bar is "median ≥ 0 **and** ≥ 5 of 6
  windows within −0.5 pp CAGR / +3 pp DD", and their M2 cells (−0.526, −0.816 pp) plus M2's DD
  (+3.42, +5.14 pp) leave exactly 5 of 6 — the narrowest possible margins.
- **G8**, the pooled OOS bootstrap (M1–M5, 2,000 paths each, seed 20261011, block 20):

| Arm | pooled median Δ | share > 0 | per-market medians (M1…M5) |
|---|---:|---:|---|
| E1 | **+0.035** | 55.2 % | −0.040 / +0.132 / +0.087 / +0.002 / +0.008 |
| E2 | **−0.013** | 47.6 % | +0.072 / −0.107 / −0.057 / −0.007 / +0.020 |
| E3 | **−0.015** | 48.5 % | +0.124 / −0.140 / −0.114 / −0.062 / +0.046 |
| E4 | **−0.029** | 48.0 % | +0.163 / −0.242 / −0.168 / −0.118 / +0.079 |

  The OOS bootstrap is monotone in the wrong direction: the larger the exponent, the *worse* the
  pooled median, and the Nasdaq-heavy M1 (1975–1999) is the only market where p > 1 helps. That is
  a clean rejection of the Moreira–Muir mechanism on this asset, and it agrees with Cederburg et
  al. (103 vol-managed strategies mostly fail out of sample, the market factor being the partial
  exception). E1's positive pooled median does not rescue it: it fails G1, G2, G4, G5, G6 and the
  after-tax set.

**Non-inferiority reading (tier N, pre-declared).** All four arms are exposure-neutral
(|drift| ≤ 0.49 pp), so the survey's two-tier rule applies as a *reading*: E2/E3/E4 are
"tier-N non-inferior" (G5 margin −0.05 pp and G9 hold, after-tax gates pass). They are **not**
adopted, because the pre-registration made the strict G1–G9 set + the after-tax set the adoption
gate — a relaxation that can only help a candidate is a data-mining device. E1 is neither
non-inferior pre-tax nor after tax.

## 6. Support and multiple testing

The share of sessions whose fill decision differs from K's is 5.20 % (E1), 3.61 % (E2), 5.35 %
(E3), 6.77 % (E4) — all above the pre-declared 1 % support floor, so the results are not driven by
a handful of sessions. Four exponent arms, one constant each solved by a deterministic monotone
rule (nothing searched); the ±10 % `c_p` rows are report-only and were not used to select
(calibration-window mean(vt) shifts of −0.024 … −0.056 pp at `c − 10 %` and +0.020 … +0.043 pp at
`c + 10 %`). The p-ordering of the main-window results is monotone (CAGR and DD both improve as p
rises) while the OOS ordering is monotone in the opposite direction — a pattern that is hard to
explain by noise, and therefore a *substantive* rejection, not a power problem.

## 7. What this closes

The `idea_survey_v5` §4.2 axis is closed: at matched average exposure, the sizing *shape* matters
materially in the 2000–2026 sample but does not survive the out-of-sample windows or the pooled
OOS bootstrap, and the flatter schedule is dominated outright. K's `min(1, 0.35/σ20)` stays. A
reader who wants to pursue p > 1 anyway should note that its whole in-sample edge is the
2000–2008 bear, and that the two cells that would have to be explained away are M2's
+5.14 pp DD (E4) and the negative pooled OOS medians.

## 8. Caveats

One history per cell; the calibration window (1985–2000, ^NDX price index, zero dividends, base
financing) is a different regime from the measured one, so `c_p` matches the pre-2000 mean
*by construction*, not the 2000–2026 mean (the drift is reported: ≤ 0.49 pp); the after-tax
history starts 2000 because the TRY/Yİ-ÜFE calendar does; the fixed 2015-09 → 2026-09 bootstrap
calendar prices only one FX/inflation regime; the liquidated convention charges deferred tax at the
window end; FIFO with per-year netting and no loss carry-forward; the 2026 tariff deflated by the
previous October's Yİ-ÜFE; G8 uses production K's execution layer (H2 needs a December calendar)
on a fixed per-market calendar of that market's own last 2,772 sessions, which differs from
`ewma_band_v1`'s calendar-free pre-tax simulator in those two disclosed ways. After-tax deltas
below ~0.1 pp are inside modelling uncertainty (§6.2 of the survey); E2's +0.028 pp 2017 window and
E4's +0.010 pp are in that band, but their B1/B2 medians (+0.16/+0.20 and +0.34/+0.46 pp) are not.
Not tax advice.

## 9. Amendments

Code fixes made **after the first complete run** (all in the reporting/aggregation layer; no arm,
`c_p`, stage, threshold, gate or decision rule was touched, and no numeric cell moved):

1. the realised-mean-weight / `mean |Δw|` slice read axis 0 of a 2-D array and returned NaN, which
   also forced every arm into tier F. Fixed to slice the time axis. The first run's printed arm
   cells (CAGR/DD/OOS/bootstrap) are identical to the final ones, so no gate outcome changed.
2. the harness's two nested checks (`calibration_frozen`, `target_identity_main`) did not
   contribute to the harness verdict (no top-level `pass`); they were passing and are now gating.
3. the verdict label now names the failed gates instead of "no gain"; the tier-N label is a
   reading, not an adoption.
4. `RESULTS.json` no longer records the parity run's wall clock (nor its "in 12.0s" tail line), so
   two `--run`s are byte-identical; the study's own wall clock lives in `SUMMARY.txt` only.
5. a kernel-wrapper bug found by `--smoke *before* the pre-registration commit (the panel builder
   emitted all five arms' panels while the output buffers were sized for the arms actually
   requested — heap corruption, non-deterministic crashes) is fixed by passing the arm list to
   `chunk_panels`; disclosed in `PREREGISTRATION.md` §10 as pre-commit work.
