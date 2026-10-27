# hrp_sleeve_v1 -- HRP / HERC / inverse-vol diversifier sleeve on K (audit item 53)

## Verdict

**No.** None of the three sleeve allocators improves K after Turkish tax. All three lose to K in the
2000, 2010 and 2017 after-tax windows (−0.32 / −0.50 / −0.40 pp at $100k) and in both the pre-2020
and post-2020 splits (−0.04 / −0.84 pp), so arms D1-D3 are not candidates. The pre-tax result looks
better (+0.19 pp CAGR, max DD 42.9% vs 46.7%), but the sleeve's sales are taxed: total sells rise from
10.7 to 28.9 per year and the after-tax CAGR falls.

The mechanism is that the three allocators collapse onto the T-bill proxy BIL*. Its trailing 252-session
volatility is about 0.1%, against 6.7% for IEF* and 17.8% for GLD*, so inverse-vol and HRP/HERC put
99.6–100% of the sleeve in BIL* (D1 and D2: BIL 100%, IEF ≤ 0.02%, GLD 0; D3: BIL mean 99.6%, IEF
≤ 1.3%, GLD ≤ 0.6%). The arms therefore test "K's idle capital in T-bills", which is already
rejected in `r2_tbill_combo_v1` and `tbill_sweep_v1` (bill gains taxed early). The bond and gold
diversifiers that the question names receive almost no weight under this design.

## Method (as pre-registered, `research/hrp_sleeve_v1/PREREGISTRATION.md`)

- Control K: arm K of `band_h2_combo_v1` (QLD weight w_R2, 0.5 pp band, A1u gain band, H2 December
  deferral, zero idle cash). Reproduced through the new kernel: after-tax 10.7533 / 15.2354 / 23.972 %
  against the published 10.7533 / 15.2354 / 23.972 %; pre-tax 15.76 %; max DD 46.67 %.
- Sleeve: IEF* (IEF adjusted close from 2002-07-31; GS10 duration-7.5 model before), GLD* (GLD from
  2004-11-19; gold futures from 2000-08-31 before), BIL* (DTB3 accrual). Weights sum to 0.98 − w_QLD(t),
  re-estimated monthly from a trailing 252-session sample covariance, next-open fills.
- Multi-instrument after-tax engine: `fe3_sim`-based 4-leg kernel (one taxpayer, per-leg FIFO books,
  Yi-ÜFE indexation, progressive tariff, liquidation at end). The only kernel change is a per-leg resize
  band (2 pp for sleeve legs, 0.5 pp for QLD); bit-identical to `fe3_sim` when all legs use 0.5 pp
  (single-leg K and R2 and the four-leg D1 panels, harness PASS).

## Results ($100k, liquidated, progressive, base financing, m = 2)

| arm | pre-tax CAGR (main) | max DD (pre-tax) | after tax 2000 | 2010 | 2017 | Δ vs K (2000 / 2010 / 2017) | pre-2020 / post-2020 Δ | sells/yr | DSR (N = 375) |
|---|---|---|---|---|---|---|---|---|---|
| K (control) | 15.76 % | 46.67 % | 10.75 % | 15.24 % | 23.97 % | — | 6.81 / 22.89 % | 10.7 | — |
| R2 (ref.) | — | — | 10.16 % | — | — | — | — | — | — |
| D1 HRP | 15.95 % | 42.88 % | 10.43 % | 14.74 % | 23.57 % | −0.32 / −0.50 / −0.40 | −0.04 / −0.84 | 29.7 | 0.169 |
| D2 HERC | 15.95 % | 42.88 % | 10.43 % | 14.74 % | 23.57 % | −0.32 / −0.50 / −0.40 | −0.04 / −0.84 | 29.7 | 0.169 |
| D3 inverse vol | 15.95 % | 42.90 % | 10.43 % | 14.74 % | 23.57 % | −0.32 / −0.50 / −0.40 | −0.04 / −0.84 | 29.6 | 0.169 |

Sells per year are the kernel's total (strategy sells plus tax-funding sales), 2000 window. By leg:
QLD 10.3 (same as K), BIL* 18.6 (D1, D2 and D3); IEF* and GLD* effectively 0. Trades per year (pre-tax
main): K 33.1, arms 67.8–67.9.

DSR is the probability that the daily after-tax NAV Sharpe (main window) exceeds the expected maximum
Sharpe of N = 372 + 3 trials (`gates_v1/stats.py`). A value of 0.17 is not significant.

## Gates

| gate | result |
|---|---|
| A1 after-tax Δ > 0 in 2000, 2010, 2017 | FAIL, all arms (−0.32 / −0.50 / −0.40 pp) |
| A2 after-tax Δ > 0 pre-2020 and post-2020 | FAIL, all arms (−0.04 / −0.84 pp) |
| A3 pre-tax trades/yr ≤ 2 × K | FAIL, all arms (67.9 / 33.1 = 2.05×) |
| A4 clean bootstraps B1-recent and B2 | NOT RUN (declared to run only if A1–A3 pass) |
| Pre-tax G1–G8 (ewma_band_v1 definitions) | NOT RUN: the OOS markets have no sleeve proxies (reduced gate) |
| G9 turnover ≤ 2 × K | equivalent to A3, see above |
| PBO | not applicable (3 arms, grid below 4) |
| Sealed window | respected: no data after 2026-09-29 is used |

## Caveats

- Proxies: BIL* is synthetic (DTB3 accrual less 0.1354 %). IEF* before 2002-07-30 is a GS10 duration model
  with monthly-step yield changes, which understates daily variance. GLD* before 2004-11-19 is continuous gold
  futures (roll effects) and begins 2000-08-31, so gold is outside the universe until about 2001-08.
- Sleeve opens equal closes (the restored IEF and GLD files carry no open series).
- The 2 pp band on sleeve legs does not stop the churn: the sleeve total moves with every daily QLD
  target change, so each QLD trim is paired with BIL* trades. The BIL leg is the source of the extra sales.
- The OOS and pre-tax G1–G8 gates were not run, and no bootstrap was run. The verdict rests on the three
  after-tax windows and the pre/post-2020 split, which all point the same way.
- No arm, parameter or gate was changed after seeing results.

## Amendments (reporting and parity only; no arm, parameter, gate or input changed)

1. After the first `--run`: added DSR, weight statistics, the provenance stamp and SUMMARY.txt to `screen.py`
   (commit `9e760ae8`). Reruns after that were byte-identical (verified twice on the final commit).
2. Added a four-leg band-off parity cell to `--harness` (commit `7807a3bc`); it reproduces `fe3_sim`
   bit for bit.

## Reproduction

- `python -m research.fast_engine.parity --quick`: PASS (13.5 s).
- `python -m research.hrp_sleeve_v1.screen --harness`: K and R2 match `fe3_sim` bitwise; four-leg parity true.
- `python -m research.hrp_sleeve_v1.screen --run` twice: `RESULTS.json` byte-identical; wall clock about 1.3 s
  (`SUMMARY.txt`).
- Inputs and SHA-256: `research/hrp_sleeve_v1/DATA_MANIFEST.json`. No new download succeeded: a FRED gold
  request returned an HTML challenge and was discarded.

Commits: preregistration `b3494fd6`; amendments `9e760ae8`, `7807a3bc`; results and report in the final commit on
`research/hrp_sleeve_v1`.
