# review_calendar_v1: K's sizing reviewed on a fixed calendar, after Turkish tax (audit item 56)

**Verdict: FAIL as pre-registered. No arm passes every gate, so K stays. No production change.**

The closest arm is **R1** (one resize review per calendar month, first NYSE session). It beats K after tax
in all three windows (+0.92 / +0.65 / +1.64 pp), in both halves (pre-2020 +0.66 pp, post-2020 +1.40 pp), and
it cuts max DD from 46.67 % to 42.32 % pre-tax while cutting sells from 10.2 to 4.1 a year. It fails the
pre-tax bootstrap (G5: median -0.88 pp, K wins 66 % of paths) and both after-tax bootstraps (A2: B1-recent
median -0.39 pp, share 40 %; A3: B2 flat-20 % median -0.60 pp, share 38 %). The after-tax gain on the single
historical path is not robust across resampled 10-year paths.

Pre-registration: `research/review_calendar_v1/PREREGISTRATION.md` (commit 7aaabe83, before any non-control run).
Driver `research/review_calendar_v1/screen.py`; engine option in `research/review_calendar_v1/engine/`.
Outputs `research/review_calendar_v1/RESULTS.json` (provenance stamp) and `SUMMARY.txt`.

## Question and arms

K resizes its vol-target weight daily inside the A1u gain band and the H2 December deferral. Arms change only the
fill decision of a resize between two positive weights (held > 0 and target > 0, |drift| > 0.5 pp). Entries from
zero and exits to zero stay daily. All arms use K's target series bit for bit.

| arm | review sessions for resizes | escape |
|---|---|---|
| K | daily (control) | - |
| R2ref | daily, C0 rule (production R2 reference) | - |
| **R1** | first NYSE session of each calendar month | none |
| R2Q | first session of Jan / Apr / Jul / Oct | none |
| R3 | first session of Jan / Apr / Jul / Oct | a resize of more than 15 pp executes at once |

R1 is not the failed `monthly_defer_v1` study (`docs/research/monthly_defer_v1.md`): that study suppressed only
downward resizes in a window of the last 10 or 21 sessions of each month, and failed the pre-tax bootstrap
by holding above-target weight. R1 gates both directions, allows one session per month, and its review is the
first session, so H2 (last 10 December sessions) can never bind on an R1 or R2Q review day. December trims
therefore fill on the first December session. This is stated in the pre-registration and holds here.

## Results (main window 2000-03-09 to 2026-09-29, m = 2, progressive tax, $100k liquidated)

| arm | pre CAGR base | pre CAGR cal | max DD pre | Calmar | sells/yr | after tax 2000 / 2010 / 2017 | max DD after tax | DSR pre / after tax (N = 378) |
|---|---|---|---|---|---|---|---|---|
| K | 15.76 % | 15.18 % | 46.67 % | 0.338 | 10.2 | 10.75 / 15.24 / 23.97 % | 46.67 % | 0.707 / 0.420 |
| R2ref | 15.48 % | 14.90 % | 46.76 % | 0.331 | 29.6 | 10.16 / 14.71 / 22.89 % | 46.76 % | 0.697 / 0.383 |
| **R1** | **16.55 %** | 15.97 % | **42.32 %** | **0.391** | **4.1** | **11.68 / 15.89 / 25.61 %** | **44.78 %** | 0.749 / 0.486 |
| R2Q | 15.04 % | 14.47 % | 40.19 % | 0.374 | 3.5 | 10.80 / 14.34 / 22.43 % | 44.52 % | 0.672 / 0.421 |
| R3 | 15.68 % | 15.10 % | 48.63 % | 0.322 | 5.2 | 10.91 / 15.52 / 24.53 % | 48.63 % | 0.709 / 0.434 |

Deltas vs K (pp, after tax at $100k): 

| arm | 2000 window | 2010 window | 2017 window | pre-2020 | post-2020 |
|---|---|---|---|---|---|
| R1 | +0.92 | +0.65 | +1.64 | +0.66 | +1.40 |
| R2Q | +0.05 | -0.89 | -1.54 | +0.80 | -3.01 |
| R3 | +0.16 | +0.28 | +0.55 | -0.14 | +1.01 |

Pre-tax CAGR deltas by half (base): R1 +0.89 / +0.35 (pre / post 2020); R2Q +0.88 / -6.22; R3 -0.26 / +0.41.

## Bootstraps (Delta vs K, median in pp, share of paths with Delta > 0)

| arm | pre-tax base | pre-tax cal | B1-recent (progressive) | B2 (flat 20 %) |
|---|---|---|---|---|
| R2ref | +0.01 (51 %) | +0.01 | -0.29 (25 %) | -0.13 (36 %) |
| R1 | **-0.88 (34 %)** | -0.86 | **-0.39 (40 %)** | **-0.60 (38 %)** |
| R2Q | -1.45 (31 %) | -1.42 | -0.58 (41 %) | -0.83 (36 %) |
| R3 | -0.14 (42 %) | -0.14 | +0.07 (54 %) | -0.03 (48 %) |

Bootstrap calibration: the K and R2ref clean-bootstrap medians reproduce `band_h2_combo_v1`'s published
`bootstrap_clean_seed20261011` values exactly (K pre-tax base 7.7761 %, K B2 4.0376 %, R2 pre-tax base 7.8106 %,
R2 B2 3.9678 %). The bootstrap engine path is therefore the published one.

## OOS windows (calibrated, pre-tax, m = 2; CAGR % / max DD %)

| window | K | R2ref | R1 | R2Q | R3 |
|---|---|---|---|---|---|
| ^GSPC 1957-1999 | 9.97 / 39.65 | 9.89 / 39.63 | 9.62 / 38.37 | 9.55 / 38.37 | 9.81 / 39.66 |
| M1 | 21.27 / 41.22 | 21.18 / 40.97 | 21.41 / 40.82 | 19.90 / 40.82 | 20.96 / 41.21 |
| M2 | 11.73 / 58.11 | 11.70 / 58.33 | 13.73 / 53.35 | 13.91 / 49.58 | 11.80 / 56.60 |
| M3 | 6.46 / 65.18 | 6.40 / 65.03 | 6.87 / 65.09 | 6.26 / 63.52 | 6.53 / 64.25 |
| M4 | 9.71 / 58.15 | 9.47 / 58.10 | 9.44 / 62.27 | 9.12 / 59.42 | 9.15 / 57.50 |
| M5 | -0.62 / 85.65 | -0.59 / 85.45 | -0.50 / 84.77 | -0.31 / 84.12 | -0.62 / 85.10 |

R1's OOS median CAGR delta is +0.13 pp (G7 passes); its reduced G8 (M1-M5 block resampling) is +0.39 pp (passes).

## Gates

| gate | R1 | R2Q | R3 |
|---|---|---|---|
| G1 CAGR or Calmar (both financings) | pass | **fail** | **fail** |
| G2 max DD <= K + 1 pp | pass | pass | **fail** (+1.96 pp) |
| G3 2017-end CAGR >= K - 1 pp | pass | **fail** | pass |
| G4 sub-periods beating K (base) >= 2 of 3 | pass | pass | pass |
| G5 pre-tax bootstrap median >= 0 (base and cal) | **fail** (-0.88 / -0.86) | fail | fail |
| G6 G1 at m = 1 | pass | **fail** | **fail** |
| G7 OOS median >= 0, >= 5 of 6, DD rule | pass | fail | fail |
| G8 reduced OOS bootstrap median >= 0 | pass (+0.39) | fail | fail |
| G9 turnover <= 2 x K | pass | pass | pass |
| A1 after tax beats K in all windows | pass | fail | pass |
| A2 B1 median >= 0 and share >= 55 % | **fail** (-0.39, 40 %) | fail | fail |
| A3 B2 median >= 0 and share >= 55 % | **fail** (-0.60, 38 %) | fail | fail |
| A4 pre- and post-2020 after-tax delta > 0 | pass | fail | fail |
| **Overall** | **FAIL (G5, A2, A3)** | FAIL | FAIL |

## Caveats

- **H2 is inert on R1 and R2Q** (see above). R3's escapes can still be deferred by H2 in December. The arms
  therefore test the calendar in place of H2's December window, not on top of it.
- **Exposure was not measured on held weights.** The `exposure` field in RESULTS.json and SUMMARY.txt is the
  mean of the target weight on vote-on days; it is identical across arms by construction (1.754) and says nothing
  about drift. The held-weight drift cost of R1 (up to 21 sessions between reviews) is therefore unmeasured. Its
  pre-tax bootstrap failure is the observed consequence, not a diagnosed mechanism.
- **PBO not computed.** Three fixed arms plus two controls, no parameter grid; `gates_v1/pbo.py` was not run.
- **DSR** uses N = 375 + 3 = 378, the registry count given in the task plus this study's arms. slow_vol_blend_v1
  counted 369 + 3 = 372; the DSRs here are reported at N = 378 as instructed. All DSRs are below the 0.95 bar.
- **Single price path and overlapping windows.** 2000 and 2010 share history; the bootstraps and OOS windows are
  the robustness checks, and they are the ones R1 fails.
- **Harness.** K and R2ref reproduce band_h2_combo_v1's anchors within 0.01 pp; the gate-off engine option is
  bit-identical to research/fast_engine in pre-tax, progressive and flat modes; an all-ones gate reproduces K exactly;
  published `parity --quick` PASS. `--run` wall clock 4.9 s (SUMMARY.txt). Two runs produce byte-identical RESULTS.json.
- **Sealed window.** No input contains data after 2026-09-29; the harness asserts the last session.

## Reproduce

```
/Users/batuhankiskac/.venvs/pb-research/bin/python -m research.review_calendar_v1.screen --harness
/Users/batuhankiskac/.venvs/pb-research/bin/python -m research.review_calendar_v1.screen --run
```
