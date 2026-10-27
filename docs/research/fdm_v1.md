# fdm_v1: Carver's forecast diversification multiplier on the K rules does not beat K after tax

Branch `research/fdm_v1`. Code `research/fdm_v1/screen.py`, preregistration `research/fdm_v1/PREREGISTRATION.md`,
inputs `research/fdm_v1/DATA_MANIFEST.json`, outputs `research/fdm_v1/RESULTS.json` and `research/fdm_v1/SUMMARY.txt`
(same directory). Engine: `research/fast_engine` (`fe_sim.simulate`, `fe_tax`, `fe_boot.boot`). Main window
2000-03-09 to 2026-09-29, m = 2, $100,000 liquidated, progressive Turkish tax. Wall clock 20.4 s on the run that
wrote `SUMMARY.txt` (28.2 s on the first run); two runs gave byte-identical `RESULTS.json` (cmp). Audit item 54.

## Verdict

**No.** Neither arm beats K after Turkish tax under the preregistered gates, and nothing is promoted. K stays.

- **F1 (carver's C3 × FDM)** is clearly worse than K: −2.6 / −3.2 / −4.9 pp after tax in the 2000 / 2010 / 2017
  windows. It cuts max DD to 33 % (K 46.7 %), but at much lower CAGR.
- **F2 (vote count × FDM, K sizing)** is close to K on return and worse on risk. It is +1.5 pp in the 2010 window
  and bootstrap-positive, but −0.2 pp in the 2000 window, −2.5 pp in 2017, and its max DD rises to 54 % (K 47 %).
  It fails the "beats K in all three windows" gate by 0.21 pp on the full history and fails G2 (drawdown), G3, G4, G7
  and G8.
- **FDM itself is near 1.** Forecast correlations of the three K rules are ρ12 ≈ 0.75 to 0.80, ρ13 ≈ 0.87 and
  ρ23 ≈ 0.52 to 0.6. The in-force FDM is 1.09 to 1.16 (mean 1.10) from 2002 on. Against the carver study's C3
  (same engine and K), FDM adds about 0.7 to 0.8 pp/yr after tax, but it does not change the verdict.

## 1. Forecast correlation and FDM on an expanding yearly window (no look-ahead)

Estimated at each calendar year-end from all sessions to that date where the three forecasts are finite
(`n` = joint sample). FDM = min(2.5, 1/sqrt(wᵀρw)), w = 1/3, FDM = 1 while n < 252. Applied from the next session.

| checkpoint | n | ρ12 | ρ13 | ρ23 | wᵀρw | FDM in force (next session on) |
|---|---:|---:|---:|---:|---:|---:|
| 2000-12-29 | 188 | 0.936 | 0.930 | 0.836 | 0.934 | 1.000 (n < 252) |
| 2001-12-31 | 436 | 0.799 | 0.852 | 0.475 | 0.806 | 1.114 |
| 2002-12-31 | 688 | 0.714 | 0.803 | 0.324 | 0.742 | 1.161 |
| 2003-12-31 | 940 | 0.761 | 0.912 | 0.544 | 0.826 | 1.100 |
| 2004-12-31 | 1192 | 0.776 | 0.890 | 0.531 | 0.822 | 1.103 |
| 2005-12-30 | 1444 | 0.775 | 0.869 | 0.526 | 0.816 | 1.107 |
| 2006-12-29 | 1695 | 0.772 | 0.863 | 0.516 | 0.811 | 1.110 |
| 2007-12-31 | 1946 | 0.788 | 0.874 | 0.555 | 0.826 | 1.100 |
| 2008-12-31 | 2199 | 0.817 | 0.870 | 0.579 | 0.837 | 1.093 |
| 2009-12-31 | 2451 | 0.731 | 0.860 | 0.520 | 0.802 | 1.116 |
| 2010-12-31 | 2703 | 0.735 | 0.862 | 0.518 | 0.803 | 1.116 |
| 2011-12-30 | 2955 | 0.734 | 0.861 | 0.517 | 0.803 | 1.116 |
| 2012-12-31 | 3205 | 0.732 | 0.863 | 0.519 | 0.803 | 1.116 |
| 2013-12-31 | 3457 | 0.740 | 0.868 | 0.533 | 0.809 | 1.112 |
| 2014-12-31 | 3709 | 0.752 | 0.872 | 0.556 | 0.818 | 1.106 |
| 2015-12-31 | 3961 | 0.753 | 0.872 | 0.560 | 0.819 | 1.105 |
| 2016-12-30 | 4213 | 0.750 | 0.872 | 0.554 | 0.817 | 1.106 |
| 2017-12-29 | 4464 | 0.764 | 0.877 | 0.576 | 0.826 | 1.100 |
| 2018-12-31 | 4715 | 0.769 | 0.876 | 0.576 | 0.827 | 1.100 |
| 2019-12-31 | 4967 | 0.766 | 0.878 | 0.577 | 0.827 | 1.100 |
| 2020-12-31 | 5220 | 0.793 | 0.861 | 0.581 | 0.830 | 1.098 |
| 2021-12-31 | 5472 | 0.800 | 0.860 | 0.584 | 0.832 | 1.096 |
| 2022-12-30 | 5723 | 0.807 | 0.869 | 0.605 | 0.840 | 1.091 |
| 2023-12-29 | 5973 | 0.794 | 0.866 | 0.599 | 0.836 | 1.094 |
| 2024-12-31 | 6225 | 0.799 | 0.866 | 0.605 | 0.838 | 1.092 |
| 2025-12-31 | 6475 | 0.798 | 0.867 | 0.606 | 0.838 | 1.092 |

Main-window FDM in force: mean 1.099, max 1.161. Full-sample in-sample value (look-ahead, information only, not
used by any arm): ρ12 0.80, ρ13 0.87, ρ23 0.60, FDM 1.093.

Reading: the rules are not as collinear as "all three read QQQ" suggests. The 252-session momentum and the 126-session
channel are the least correlated pair (ρ23 ≈ 0.5 to 0.6), so FDM sits at about 1.1. The multiplier scales the
forecast by about 10 %.

## 2. Arms and results (main window, fill rule K for both arms)

Arms are as preregistered. F1 = C3 on comb × FDM, cap ±20. F2 = vote count × 20/3 × FDM, weight min(1, f/10) × vt
while any vote holds, K's early re-entry otherwise.

| row | pre CAGR base / cal | pre max DD | AT 2000 / 2010 / 2017 (CAGR) | Δ vs K (pp) 2000 / 2010 / 2017 | pre-2020 / post-2020 Δ | B1 / B2 median Δ (share > 0) | G5 pre-tax boot Δ (base) | G8 pooled OOS Δ | DSR (N = 377) | sells/yr (all, 2000 / 2010 / 2017) |
|---|---|---|---|---|---|---|---|---|---|---|
| K (control) | 15.76 / 15.18 % | 46.7 % | 10.75 / 15.24 / 23.97 % | — | — | — | — | — | 0.708 | 10.7 / 10.6 / 9.5 |
| R2 (reference) | 15.48 / 14.90 % | 46.8 % | 10.16 / 14.71 / 22.89 % | −0.59 / −0.53 / −1.08 | −0.32 / −1.34 | — | — | — | 0.698 | 30.7 / 31.4 / 35.6 |
| F1 (C3 × FDM) | 12.14 / 11.68 % | 33.2 % | 8.14 / 12.03 / 19.11 % | **−2.62 / −3.21 / −4.86** | −2.40 / −2.80 | −0.34 (46 %) / −0.53 (45 %) | −1.06 (40 %) | −1.51 | 0.612 | 34.5 / 32.9 / 25.2 |
| F2 (votes × FDM) | 15.96 / 15.32 % | 54.2 % | 10.54 / 16.75 / 21.49 % | **−0.21** / +1.51 / **−2.48** | +0.73 / **−3.17** | +1.35 (69 %) / +1.30 (69 %) | +2.00 (73 %) | −0.57 | 0.684 | 16.3 / 15.3 / 13.3 |

Pre-tax 2017-end CAGR: K 33.2 %, F1 26.8 %, F2 29.9 %. Sub-period CAGR (P1 / P2 / P3): K 0.3 / 14.3 / 33.2 %, F1
−0.9 / 10.6 / 26.8 %, F2 −3.2 / 23.2 / 29.9 %.

Calibrated financing (0.75 %): pre CAGR K 15.18 %, F1 11.68 %, F2 15.32 %; max DD K 46.8 %, F1 34.2 %, F2 54.4 %.
m = 1 base CAGR: K 16.02 %, F1 12.32 %, F2 16.19 %.

### Exposure (QLD target weight from the first measured session; QQQ-equivalent = 2 × QLD)

"Vote-on" means K's E2 vote is on (at least 2 of 3 votes, v ≥ 2).

| row | mean QLD weight, all days | mean QLD weight, vote-on days | QQQ-equiv., vote-on days | mean QLD weight, vote-off days | share of days invested |
|---|---:|---:|---:|---:|---:|
| K | 0.673 | 0.897 | 1.79 | 0.027 | 0.77 |
| R2 | 0.673 | 0.897 | 1.79 | 0.027 | 0.77 |
| F1 | 0.545 | 0.729 | 1.46 | 0.012 | 1.00 |
| F2 | 0.723 | 0.897 | 1.79 | 0.222 | 0.87 |

F2 matches K's exposure on vote-on days by construction (identity checked in the harness). F1 holds about 19 % less
QLD than K on vote-on days, the carver under-investment (the forecast is usually below 10, so min(1, f/10) < 1).
F2's extra exposure comes from one-vote days (weight ≈ 0.6 to 0.7 × vt), which K does not hold. Those days are
where most of F2's extra drawdown sits.

## 3. Gates (F1 and F2 against K; carver_forecast_v1 gate definitions, unchanged)

| gate | F1 | F2 |
|---|---|---|
| G1 CAGR ≥ K or Calmar ≥ 1.1 K (m = 2, both) | fail | **pass** |
| G2 max DD ≤ K + 1 pp (both) | **pass** (33 vs 47 %) | fail (54 vs 47 %) |
| G3 2017-end CAGR ≥ K − 1 pp (both) | fail | fail (29.9 vs 33.2 %) |
| G4 CAGR above K in ≥ 2 of 3 sub-periods (both) | fail (0 of 3) | fail (1 of 3) |
| G5 pre-tax bootstrap median Δ ≥ 0 (base and cal) | fail (−1.06 / −0.95) | **pass** (+2.00 / +1.94) |
| G6 G1 at m = 1 | fail | **pass** |
| G7 six OOS windows, calibrated | fail | fail |
| G8 pooled OOS bootstrap Δ ≥ 0 | fail (−1.51) | fail (−0.57) |
| G9 turnover ≤ 2 × K | pass (3.9 vs 5.5) | pass (4.9 vs 5.5) |
| After tax: beats K in all three windows | fail (−2.6, −3.2, −4.9) | fail (−0.21, +1.51, −2.48) |
| After tax: B1 and B2 median Δ ≥ 0 with share ≥ 55 % | fail (45–46 %) | **pass** (+1.35 / +1.30, 69 %) |
| Reported: pre-2020 and post-2020 after-tax Δ both > 0 | fail (−2.4 / −2.8) | fail (+0.73 / −3.17) |
| **All gated criteria** | **fail** | **fail** |

G4 for F1 has no P1 or P2 win (its base CAGR is below K in all three sub-periods; the gate needs 2 of 3), so it is
0 of 3 in both financing cases and fails.

F2 passes the bootstrap gates (G5, G6, B1/B2) and the G1 return gate, and fails the drawdown, the 2017-end,
sub-period, OOS and the "all three windows" gates. The bootstrap says F2 is better than K on a resampled ten-year
path; the one historical path it is tested on (the three windows) does not show a consistent edge, and the
post-2020 half is 3.2 pp worse. The clean bootstrap and the single path disagree, and the gate rule requires both.

## 4. Turnover and sells

Sells per year (all sells, including tax-funding sales): K 10.7 / 10.6 / 9.5 (2000 / 2010 / 2017 windows); F1 34.5 /
32.9 / 25.2; F2 16.3 / 15.3 / 13.3. Pre-tax QLD sells per year: K 10.2, F1 34.3, F2 15.8, R2 29.6. Turnover per year: K 5.46, F1 3.90, F2 4.90.
F1 has the lowest turnover of the three but about three times K's sells. The likely reason is that its small,
continuous weight changes cross K's gain-band and 0.5 pp thresholds often, and each crossing is a taxable trim. This
was not tested separately. F2 sells about 50 % more than K.

## 5. Deflated Sharpe (N = 377)

K 0.708, R2 0.698, F1 0.612, F2 0.684. None reaches 0.95; K itself does not under this deflation. The trial-variance
floor 1/(T−1) makes these an upper bound.

## 6. What FDM changes (against the carver study, same engine and K)

The carver study ran C3 without FDM. Same K, same engine, same gates:

| | C3 (carver) | F1 = C3 × FDM | change |
|---|---|---|---|
| pre-tax CAGR base | 11.19 % | 12.14 % | +0.95 pp |
| max DD | 32.0 % | 33.2 % | +1.2 pp |
| after tax 2000 / 2010 / 2017 | 7.43 / 11.23 / 18.30 % | 8.14 / 12.03 / 19.11 % | +0.7 / +0.8 / +0.8 pp |
| mean QLD weight, all days | 0.53 | 0.545 | +0.02 |

FDM does what it should: it scales the forecast by about 1.1, which adds a little exposure and a little return. It
does not change the ranking. Both forecast arms remain worse than K on return after tax, and the scale of the
forecast (the 10 % step) is too small to fix the exposure gap the carver study identified.

## 7. Caveats

1. **Exposure is still not matched on F1.** F1 is 19 % under K on vote-on days. The carver map (full weight at a
   forecast of 10) is preregistered and was not tuned. A higher full-weight point would change F1's return and risk
   together; that would be a new arm and was not run.
2. **F2 is K plus one-vote exposure.** F2 holds about 0.6 to 0.7 × vt on one-vote days, which K does not. This is the
   only thing separating F2 from K on vote-on-matched exposure, and it is where F2's drawdown comes from. Its bootstrap
   result and its single-path result disagree, which is the gate's reason to fail it.
3. **FDM is estimated from forecast correlations** (the question's definition). Carver's own tables estimate FDM from
   subsystem return correlations, which were not computed here.
4. **Single historical path.** The three after-tax windows and the two halves are one history. The clean bootstraps
   use a fixed 2015 to 2026 calendar (B1, B2) and resampled ten-year paths (G5).
5. **Sealed window.** The data loaded end on 2026-09-29 (`last_session_loaded` in RESULTS.json). The screen asserts
   no session on or after 2026-10-30 (`docs/research/sealed_window_v1.md`, commit 4c40d114 on `main`), so the
   forward window is untouched.

## Amendments

- **Amendment 1 (text only, before any result was written).** PREREGISTRATION.md says "The first such checkpoint is
  the 2002 year-end on the main series". That is wrong. The first checkpoint with n ≥ 252 joint sessions is the 2001
  year-end (n = 436). Its FDM (1.114) is in force from 2002. The code implements the rule as written (n ≥ 252 at the
  checkpoint, applied from the next session), so no result depends on the wording. Recorded here; the arms, gates,
  parameters and data are unchanged.

## Commits

- `ccd1cdca` restore of carver harness, fast engine, K harness, gates inputs and `data/historical`.
- `8360dfde` preregistration, screen and data manifest (before any non-control real-data run).
- Results (`RESULTS.json`, `SUMMARY.txt`) and this report: see the branch head.
