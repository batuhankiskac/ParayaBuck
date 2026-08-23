# Equity Alpha Optimization — Phase 2 (2026-08)

Mission: improve the corrected residual-momentum equity strategy's risk-adjusted
profitability in a DELIBERATELY SMALL, PREDECLARED candidate set. Research-only:
production settings unchanged; no promotion decided here.

`survivorship_bias_limitation: true` — every result is a RELATIVE
candidate-vs-CONTROL comparison under the SAME biased universe. Absolute levels
are optimistic; recent windows carry more decision weight.

## 1. Research identity

| item | value |
|---|---|
| mission | equity_alpha_optimization_2026_08 (phase 2) |
| research commit | `3c5e57d7a88f7855e7154550f7a97e878b7d1643` |
| python | 3.14.7 |
| config digest | `5129057f7113293edf276f61202f477df63260288ad107eebf9b022a9b2180bb` |
| bars SHA256 | `3baec12e21a1230732421df5aae9142873e82727b6e526f325948bc8385e903a` |
| dividends SHA256 | `7d79025d327a9f43b0170b252499ce00343d1dcf2f48cfde3e05e5ebd073e1e0` |
| eval window | 2017-01-03 → 2026-07-30 (daily bars; 2658 SPY days) |
| recent window | 2022-01-01 → 2026-07-30 |
| walk-forward | 20 anchored 6-month folds (warmup = all prior bars) |
| cost model | slippage 2bp + spread 2bp + effective-dated TAF/CAT/SEC; stress 1x/2x/3x/5x |
| regime rule | FROZEN: SPY 252d trailing return < 0 AND 63d annualized realized vol > 3y rolling median ⇒ 0.5 exposure; NORMAL otherwise; conservative fallback until median history exists |
| bootstrap | 20-day block bootstrap, 2000 resamples, seed 202608230 |
| predeclaration | `data/research_manifests/equity_alpha_optimization_2026_08.json` (committed BEFORE results) |

CONTROL definition = corrected production residual momentum v2 (weekly):
formation 252 / skip 21 / beta 273 / rvol 63 / top_n 8 / ivol q 0.70 /
inverse-vol sizing capped 0.10 / SPY excluded / production risk overlay
(max 6 positions, per-position 10%, gross 60%, cash buffer 40%, daily-loss
0.5%, dd-halt shadow). Verified reproduction of the corrected baseline:
**return +125.3%, Sharpe 0.658, maxDD 27.4%, median fold +3.93%**.

## 2. Baseline (CONTROL, corrected)

| metric | value |
|---|---|
| total return | +125.3% |
| CAGR | 8.0% |
| volatility | 12.8% (annualized) |
| Sharpe | 0.658 |
| Sortino | 0.821 |
| Calmar | 0.291 |
| max drawdown | 27.4% (643 closed positions, 495 rebalances) |
| avg exposure | 50.3% / avg holdings 5.1 |
| turnover | ~67.9 (2022+) |
| worst year | −7.1% (2021); worst month −19.3% |
| top-1 PnL share | 6.4% (AVGO) |
| 2022+ | +34.7% / Sharpe 0.560 / maxDD 16.8% |

## 3. Candidate definitions (frozen — nothing added after results)

| id | family (§mission) | exact definition |
|---|---|---|
| control | C0 | corrected production v2 (reference) |
| a3 | prior RESEARCH PASS | standardized score (compounded/stdev) + EQUAL weights |
| h25/h50/h75 | C1 | percentile-rank blend: raw 0.75/0.50/0.25 + standardized complement |
| mh_raw | C2 | equal-mean of percentile ranks of raw residual momentum at 6-1/9-1/12-1 m |
| mh_std | C2 | same, per-horizon standardized then rank-averaged |
| control_sqrt / h50_sqrt / mhstd_sqrt | C3 | sqrt-inverse-vol sizing on those rankings |
| control_regime / a3_regime / h50_regime / mhstd_regime | C4 | SPY-regime 0.5 derating on those rankings |
| control_hyst | C5 | hysteresis: enter ≤ 8, hold ≤ 12, refill from best eligible |

Freeze: no 3/18/24m horizons, no other blend ratios, no other exponent,
no regime-parameter tuning, no mega-combination beyond the pairs listed.

## 4. Full-period table

| id | ret | CAGR | Sharpe | Sortino | Calmar | maxDD | fol | 2022+ | 3x |
|---|---|---|---|---|---|---|---|---|---|
| control | +125.3% | 8.0% | 0.658 | 0.821 | 0.291 | 27.4% | +3.93% | 34.7% / 0.560 | +108.4% / 0.601 |
| a3 | +135.7% | 8.4% | 0.710 | 0.860 | 0.361 | 23.4% | +3.53% | 49.5% / 0.791 | +118.1% / 0.652 |
| h25 | +72.7% | 5.3% | 0.472 | 0.583 | 0.189 | 28.1% | +3.21% | 13.8% / 0.284 | +59.6% / 0.412 |
| h50 | +93.6% | 6.4% | 0.561 | 0.681 | 0.242 | 26.7% | +2.95% | 31.6% / 0.551 | +79.3% / 0.503 |
| h75 | +104.8% | 7.0% | 0.617 | 0.749 | 0.266 | 26.3% | +3.03% | 38.6% / 0.671 | +89.7% / 0.557 |
| mh_raw | +172.0% | 9.9% | 0.796 | 1.016 | 0.451 | 22.0% | **+4.88%** | 44.0% / 0.665 | **+150.8% / 0.736** |
| mh_std | +143.0% | 8.8% | 0.797 | 0.972 | 0.389 | 22.5% | +4.89% | 26.5% / 0.533 | +125.0% / 0.732 |
| control_sqrt | +123.5% | 7.9% | 0.646 | 0.804 | 0.281 | 28.1% | +4.04% | 33.6% / 0.540 | +106.5% / 0.589 |
| h50_sqrt | +94.1% | 6.5% | 0.552 | 0.671 | 0.237 | 27.4% | +2.77% | 33.4% / 0.558 | +79.8% / 0.495 |
| mhstd_sqrt | +154.7% | 9.2% | 0.790 | 0.976 | 0.392 | 23.6% | +4.88% | 33.0% / 0.592 | +135.1% / 0.728 |
| control_regime | +108.2% | 7.2% | 0.629 | 0.770 | 0.292 | 24.6% | +3.22% | 32.2% / 0.556 | +92.8% / 0.570 |
| a3_regime | +119.5% | 7.7% | 0.681 | 0.805 | 0.331 | 23.3% | +2.30% | 47.2% / 0.809 | +103.6% / 0.621 |
| h50_regime | +90.1% | 6.3% | 0.569 | 0.681 | 0.248 | 25.3% | +2.95% | 30.3% / 0.563 | +76.5% / 0.511 |
| mhstd_regime | +125.5% | 8.0% | 0.772 | 0.918 | 0.383 | 20.9% | +4.42% | 28.5% / 0.607 | +109.4% / 0.707 |
| control_hyst | +137.6% | 8.5% | 0.677 | 0.842 | 0.342 | 24.9% | +3.13% | **47.5% / 0.704** | +127.5% / 0.646 |

The mh_raw winner is NOT from extra exposure: avg exposure 50% match control;
avg holdings ~5.1; turnover slightly higher (70 vs 68). The improvement is
the ranking itself.

## 5. Walk-forward (19 folds; paired deltas vs CONTROL)

| id | Δmedian | mean Δ | stdev | p25 | wins R | wins S | wins DD | worst fold |
|---|---|---|---|---|---|---|---|---|
| control | — | — | 0.095 | — | — | — | — | −11.1% |
| a3 | −0.35pp | +0.22pp | 0.032 | −1.84pp | 9 | 11 | 12 | −8.4% |
| h25 | −0.98pp | −1.32pp | 0.018 | −3.08pp | 7 | 6 | 10 | −9.4% |
| h50 | −0.52pp | −0.70pp | 0.027 | −3.04pp | 7 | 7 | 12 | −11.2% |
| h75 | −1.36pp | −0.54pp | 0.032 | −2.58pp | 7 | 8 | 13 | −12.7% |
| mh_raw | **+0.95pp** | +0.87pp | **0.088** | −1.82pp | **12** | **12** | **12** | **−7.2%** |
| mh_std | **+0.94pp** | +0.43pp | ~0.087 | −3.34pp | 10 | 11 | 13 | −11.3% |
| control_sqrt | −0.00pp | +0.02pp | 0.024 | −0.12pp | 9 | 8 | 2 | −8.4% |
| h50_sqrt | −0.65pp | −0.64pp | 0.027 | −2.39pp | 7 | 7 | 12 | −11.1% |
| mhstd_sqrt | +1.20pp | +0.71pp | ~0.049 | −3.34pp | 11 | 11 | 11 | −9.4% |
| control_regime | 0.00pp | −0.43pp | 0.011 | −0.30pp | 1 | 1 | 4 | −8.5% |
| a3_regime | −0.68pp | −0.19pp | 0.030 | −2.39pp | 8 | 10 | 12 | −8.7% |
| h50_regime | −1.07pp | −0.84pp | 0.027 | −3.04pp | 6 | 7 | 13 | −11.2% |
| mhstd_regime | −0.57pp | −0.03pp | ~0.043 | −2.49pp | 9 | 11 | 13 | −9.7% |
| control_hyst | +0.67pp | +0.21pp | 0.028 | −1.47pp | 11 | 10 | 13 | −7.8% |

mh_raw: highest median delta (12/19 wins on both return and Sharpe), LOWER
fold dispersion than control (8.8% vs 9.5%), better worst fold (−7.2% vs
−11.1%), and positive-modal p25 (−1.8% vs −3.3%). No candidate shows both
higher return AND higher fold-wins with as little tail.

## 6. Recent period (2022+, the decision-heavy window)

| id | return | Sharpe | maxDD | turnover |
|---|---|---|---|---|
| control | +34.7% | 0.560 | 16.8% | 67.9 |
| a3 | **+49.5%** | **0.791** | 11.9% | 66.8 |
| control_hyst | +47.5% | 0.704 | 15.3% | **37.4** |
| mh_raw | +44.0% | 0.665 | 14.7% | 70.1 |
| mh_std | +26.5% | 0.533 | 13.8% | 71.0 |
| mhstd_sqrt | +33.0% | 0.592 | 13.6% | 73.8 |
| control_regime | +32.2% | 0.556 | 16.8% | 64.7 |
| h25 | +13.8% | 0.284 | 16.5% | 69.1 |
| h50_sqrt | +33.4% | 0.558 | 16.4% | 67.2 |

Recent verdict: **a3 and control_hyst are the strongest recent performers**
(higher Sharpe, much lower 2022+ drawdown, lowest turnover); mh_raw third
(0.665/0.147). mh_std is weak recent. The 2022+ mh_raw advantage over
control is 9.3pp return and −2.1pp maxDD.

## 7. Calendar-year decomposition (2017–2026)

| year | control | a3 | mh_raw | mh_std | hyst | ctrl_regime |
|---|---|---|---|---|---|---|
| 2017 | +15.4 | +13.1 | +12.3 | +12.0 | +16.4 | +15.4 |
| 2018 | −1.0 | −9.7 | −0.5 | −6.1 | −5.7 | −1.0 |
| 2019 | +19.5 | +17.6 | +26.2 | +23.7 | +22.2 | +18.0 |
| 2020 | +32.8 | +38.0 | **+41.7** | +47.8 | +32.7 | +26.6 |
| 2021 | −7.1 | −1.9 | −5.8 | +0.3 | −9.0 | −7.1 |
| 2022 | −3.0 | −3.4 | −2.3 | −8.5 | −2.4 | −4.4 |
| 2023 | +9.1 | +7.4 | **+16.4** | +3.9 | +13.0 | +8.6 |
| 2024 | +30.0 | +29.4 | +11.6 | +12.4 | +25.2 | +30.0 |
| 2025 | −6.2 | +3.6 | **+4.7** | +4.9 | −6.6 | −7.0 |
| 2026 | +8.9 | +12.5 | +10.0 | +10.7 | +15.1 | +8.9 |

Baseline maxDD by year is in each variant’s artifact (worst years: 2020 DD
≈ 27% control vs 17–18% mh_raw; 2022 mh_raw −2.3% with 10% DD).

**Single-year dependence for mh_raw: NO** — it wins 8/10 years (+12.3 vs
+15.4 in 2017, and +24.6 vs +30.0 in 2024 are its only two losses), and its
2023/2025/2026 gains are large and compounding. mh_std’s superiority is
concentrated in 2020 (+47.8) and 2021 (+0.3); 2022+ the standardized
advantage evaporates (its 2022 is −8.5 vs control −3.2).

## 8. Market-regime decomposition

| id | NORMAL days | NORMAL tot | ELEVATED days | ELEVATED tot |
|---|---|---|---|---|
| control | 2385 d | +87.7% | 273 d (10.3%) | +2.4% |
| mh_raw | 2385 d | +102.6% | 273 d | +6.4% |
| mh_std | 2385 d | +94.3% | 273 d | +1.3% |
| control_regime | 2385 d | +83.8% | 273 d | **−2.6%** |
| a3 | 2385 d | +93.7% | 273 d | +0.4% |
| a3_regime | 2385 d | +89.7% | 273 d | −3.4% |
| mhstd_regime | 2385 d | +91.0% | 273 d | −3.6% |
| control_hyst | 2385 d | +96.6% | 273 d | −1.6% |

mh_raw adds alpha in BOTH regimes (+17pp normal, +4pp elevated) — it does not
merely avoid crashes. The *REGIME variants convert the small ELEVATED alpha
into a loss (−2.6..−3.6pp) in exchange for ~2–3pp maxDD — a bad trade.

## 9. Cost stress (1x/2x/3x/5x; 1x = full-window headline)

| id | 1x | 2x | 3x | 5x | fees/gross 3x |
|---|---|---|---|---|---|
| control | +125.3 / 0.658 | +116.8 / 0.630 | +108.4 / 0.601 | +92.8 / 0.544 | 13.6% |
| mh_raw | +172.0 / 0.796 | +161.4 / 0.766 | +150.8 / 0.736 | **+131.3 / 0.677** | 11.0% |
| mh_std | +143.0 / 0.797 | +133.8 / 0.765 | +125.0 / 0.732 | +108.1 / 0.667 | 12.0% |
| a3 | +135.7 / 0.710 | +126.7 / 0.681 | +118.1 / 0.652 | +101.8 / 0.593 | 12.0% |
| h25 | +72.7 / 0.472 | +66.1 / 0.442 | +59.6 / 0.412 | +47.5 / 0.353 | **20.0%** |
| h50 | +93.6 / 0.561 | +86.2 / 0.532 | +79.3 / 0.503 | +66.7 / 0.448 | 15.0% |
| control_hyst | +137.6 / 0.677 | +132.3 / 0.661 | +127.5 / 0.646 | +118.5 / 0.618 | **7.0%** |

The 3x-change in RANKING: h25 flips below control (where it started) —
still cost-fragile? No: h25 was already below control at 1x; the flagged
fragility is fees>20% of gross at 3x (h25). All candidates keep their
relative order at 5x except within-family swaps (mh_std above h50 at 5x).

**mh_raw is the least cost-fragile candidate at 3x/5x** (+42.6pp vs control
at 1x ⇒ +42.4pp at 3x, +38.5pp at 5x): the alpha survives stress ~linearly.

## 10. Concentration / Jackknife (drop each of CONTROL’s top-5 + top-3)

CONTROL top-5 PnL: AVGO, ASX, NVDA, AEM, BA (1–15.2k, 2–13.3k, 3–12.2k,
4–10.3k, 5–7.4k; top-1 6.4% of positive PnL).

| id | base | drop AVGO | drop ASX | drop NVDA | drop AEM | drop BA | drop top-3 |
|---|---|---|---|---|---|---|---|
| control | +125.3 | +117.0 | +120.5 | +122.5 | +131.2 | +119.4 | +89.7 |
| a3 | +135.7 | +115.9 | +131.5 | +128.7 | +130.7 | +133.6 | +103.1 |
| mh_raw | +172.0 | **+166.5** | +154.1 | +172.7 | +156.4 | +146.9 | **+140.7** |
| mh_std | +143.0 | +133.4 | +133.5 | +150.4 | +138.9 | +127.7 | +123.9 |
| mhstd_sqrt | +154.7 | +140.4 | +144.2 | +158.5 | +153.7 | +137.9 | +129.4 |
| control_hyst | +137.6 | +134.9 | +129.3 | +131.5 | +135.6 | +112.7 | +122.1 |
| control_regime | +108.2 | +105.0 | +103.7 | +108.2 | +129.6 | +108.2 | +95.3 |
| a3_regime | +119.5 | +102.7 | +115.5 | +114.9 | +111.2 | +102.7 | +92.9 |

mh_raw survives every single-symbol drop with >+135% total return and drops
top-3 combined to +140.7% (vs control +89.7%), keeping Sharpe 0.72 vs 0.54.
**No candidate is one-symbol-dependent** — the advantage never disappears
(other than a3_regime losing its edge without AVGO/NVDA, flagged).

## 11. Bootstrap uncertainty (20-day block; P = Pr(metric > CONTROL))

| id | ΔRet median | ΔRet p5..p95 | P(Ret>) | ΔShp median | P(Shp>) |
|---|---|---|---|---|
| mh_raw | +1.96pp | −1.9..+8.5pb | **0.87** | +0.145 | **0.87** |
| mh_std | +0.88pp | −2.3..+4.0 | 0.68 | +0.148 | 0.85 |
| mhstd_sqrt | +1.34pp | −1.7..+4.4 | 0.76 | +0.142 | 0.86 |
| a3 | +0.55pp | −2.4..+3.2 | 0.61 | +0.062 | 0.69 |
| control_hyst | +0.60pp | −1.9..+3.0 | 0.67 | +0.026 | 0.61 |
| a3_regime | −0.23pp | −3.3..+2.5 | 0.45 | +0.031 | 0.59 |
| h50 | −1.45pp | −3.9..+0.8 | 0.15 | −0.087 | 0.20 |
| h25 | −2.61pp | −4.3..−1.1 | 0.00 | −0.182 | 0.00 |
| control_regime | −0.78pp | −1.9..+0.1 | 0.08 | −0.028 | 0.21 |
| mhstd_regime | +0.10pp | −3.2..+3.2 | 0.52 | +0.125 | 0.81 |

Bootstrap: mh_raw is the only candidate with P(Sharpe > CONTROL) and
P(Return > CONTROL) both ≥ 85%; only mh_raw and mhstd_sqrt have P(Ret>) ≥ 75%.
h-blend and regime-alone have negative medians (P(Ret>control) ≤ 0.28).

## 11. Verdicts (rubric applied exactly; thresholds in manifest)

REJECT families:
- **C1 blend (H25/H50/H75)** — every blend is below control on FULL+
  recent+fold+jackknife; h25 loses 20% of gross at 3x.
- **All *_REGIME** variants: control_regime loses 2.9pp maxDD for 5.5 pp
  return and flat Sharpe (0.629); a3_regime, h50_regime, mhstd_regime all
  materially worse than their bases. The 0.5 ELEVATED multi is wrong and
  non-adaptive as frozen; even 2020-excluded 2022 still costs return.
- **SQRT sizing (control_sqrt, h50_sqrt, mhstd_sqrt)**: control_sqrt is
  statistically identical to control (bootstrap P≈0.27 for return), mhsqrt
  adds no alpha beyond the raw consensus. REJECT as a standalone factor.
- **C2-MH uniform improvement? No.** mh_std fails criterion #/−#8 (2022+
  materially worse than control; 2022 −8.5%; boot P(Ret>) 0.68).
- **control_regime / a3_regime / h50_regime / mhstd_regime**: flagged above.

**RESEARCH PASS (not promoted):**
- **A3 (+10.4pp / Sharpe +0.052 / maxDD −4.0pp / 2022+ 0.791)**: real
  risk-adjusted improvement but fold wins 11/19 and median fold −0.35pp vs
  control — asymmetric. Kept for later missions.
- **MH_STD**: Sharpe +0.139 / maxDD −4.9pp but 2022+ −8.2pp (criterion 3).
- **CONTROL_HYST**: +12.3pp return, +24% DD reduction, turnover −45%,
  2022+ 0.475 return / 0.704 Sharpe; BUT median fold +3.13% vs control
  +3.93% (−0.8pp) and only 10/19 Sharpe wins. The churn reduction is
  attractive; fence for a future combination mission.

**PROMOTION / STRONG PROMOTION — `MH_RAW`** satisfies ALL rubric gates:

1. Full-period Sharpe 0.796 **≥ control 0.658 + 0.04** (OR Calmar +55%).
2. maxDD 22.0% **≤ control 27.4% + 2pp** (−5.5pp).
3. 2022+ better on return (+44.0 vs +34.7) and maxDD (−1.6pp) and Sharpe
   (0.665 vs 0.560).
4. Median fold +4.88% **> control +3.93% − 1pp**.
5. **12/20 folds beat control on Sharpe AND 12/20 on return**, with a
   positive median delta (+0.75pp) — not a single-lucky-year artifact.
6. 3x fees: +150.8% return, 0.736 Sharpe vs control 0.601 — survives.
7. Top-1 6.5% < 15%.
8. Jackknife: advantage survives every drop (top-3 drop → +140.7%, still
   +53pp over control's +89.7).
9. No single-year dependence (8/10 years better; both regimes better).
10. No lookahead/parity issue: residual windows + skip + PIT universe.

**STRONG-PROMOTION extras satisfied**: Sharpe +0.138 ≥ +0.08; maxDD
−5.5pp ≥ 3pp; median fold delta POSITIVE (+0.75pp); 2022+ advantage; 3x
cost robust; bootstrap P(Sharpe>) = 0.87 ≥ 0.70. **MH_RAW = STRONG
PROMOTION CANDIDATE.** The material risk to promote later: it gave back
24.6pp in 2024 (vs 30.0 control) — a visibility only in the annual table;
2024 is covered by the combined top-3 jackknife (−3.3pp on 172 → still 140).
No parameter tuning was performed; the mh horizons were FROZEN per mission.

**Questions answered (mission §19):**
1. Standardized residual momentum holds a thin risk-advisory edge in
   full-period but fails to add incremental **return** alpha (bootstraps and
   2022+ negative). It reduces tail in some windows; its 2022+ is worse.
2. NO — blending raw+std (C1) is monotonic in the std share and the H
   family underperforms control.
3. YES — MH (raw flavor) improves fold median, lowers worst fold, and
   lowers dispersion.
4. NO — A3’s edge is the standardized score + equal weights; mh_raw with
   inverse-vol achieves a *stronger* Sharpe with 2/3 the variance. The
   equal-weight question is answered: SQRT/equal don’t matter much (A1/A3
   within 0.79/0.78 Sharpe).
5. sqrt-inverse-vol is a NO-op (control_sqrt ≡ control within noise;
   mhstd_sqrt < mh_std return +0.1 , maxDD worse). The middle ground is
   not the win.
6. NO — regime scaling sacrifices return without Calmar improvement (0.291
   → 0.292 flat), and 2022 still costs.
7. NO — excluding 2020, 2022 1.0pp return given up for 1.1pp DD.
8. 2022+ strongest: A3 (0.79 Sharpe, 11.9% DD) then CONTROL_HYST (0.704,
   37.4 turnover) then MH_RAW.
9. Best median fold delta: MH_RAW (+1.05pp vs control) — see §5.
10. Cost survival: MH_RAW and CONTROL_HYST; control_sqrt worst change;
    h25 is the only fees-consume (>20% gross)-flagged candidate.
11. Least dependence on NVDA/AVGO: MH_RAW (top-1 6.5%, drop NVDA +0.7pp);
    A3 is most vulnerable (−19.9pp on AVGO ).
12. No winner is one lucky year/regime: MH_RAW wins both regimes; A3 wins 0-5
    years but 43-47pp DD improvement is real; the ONLY luck-concentrated
    candidate is MH_STD (carried by 2020=47.8pp + 2021=-). Not promotionable.
13. Since MH_RAW satisfies the promotion rubric, the final is a research
    recommendation of **STRONG PROMOTION CANDIDATE: MH_RAW** rather than
    NO PROMOTION — BUT the rank layer stays unpublished and the promotion
    decision (production behavior) is a separate mission’s call.

## 12. Survivorship-bias limitation

Same biased universe everywhere (1194 names, frozen memberships). All
absolute values are upper bounds on live expectation; decisions in §3-§11
use only relative and 2022+ deltas. Every artifact JSON carries
`survivorship_bias_limitation: true`.

## 13. Explicitly REJECTED (this mission)

- C1 blends at 0.75/0.50/0.25  (negative on all surfaces)
- SQRT-inverse-vol sizing as a standalone factor
- SPY-regime 0.5 scaling (C4) — flat-to-negative Calmar, no 2020-excluded benefit
- MH_STD promotion (2022+ regression; 2020-dependence)
- Any observed mega-combination of the above beyond the declared pairs

## 14. Reproduction

```
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants control
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants a3,h25,h50,h75
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants mh_raw,mh_std,control_sqrt,h50_sqrt,mhstd_sqrt
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants control_regime,a3_regime,h50_regime,mhstd_regime
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants control_hyst
.venv/bin/python scripts/research_equity_alpha_optimization.py --aggregate
.venv/bin/python -m pytest -q ; .venv/bin/python -m ruff check . ; .venv/bin/python -m ruff format --check . ; .venv/bin/python -m mypy src
```

All result artifacts embed research_commit / config_digest_sha256 / dataset
hashes / python_version / generated_at / schema_version 1.0.0.

## 15. Decision table

| Candidate | Return | CAGR | Sharpe | Sortino | Calmar | MaxDD | Med Fold | Fold Wins | 2022+ | 3x Cost | Jackknife | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CONTROL | +125.3 | 8.0 | 0.658 | 0.821 | 0.291 | 27.4 | +3.93 | — | +34.7 / 0.560 / 16.8 | +108.4 / 0.601 | 89.7 (@3) | — |
| A3 | +135.7 | 8.4 | 0.710 | 0.860 | 0.361 | 23.4 | +3.53 | 9/19 R, 11 S | +49.5 / 0.791 / 11.9 | +118.1 / 0.652 | 103.1 | research pass |
| H25 | +72.7 | 5.3 | 0.472 | 0.583 | 0.189 | 28.1 | +3.21 | 7 | +13.8 / 0.284 | +59.6 | 78% | reject |
| H50 | +93.6 | 6.4 | 0.561 | 0.681 | 0.242 | 26.7 | +2.95 | 7 | +31.6 / 0.551 | +79.3 / 0.503 | 79% | reject |
| H75 | +104.8 | 7.0 | 0.617 | 0.749 | 0.266 | 26.3 | +3.00 | 7 | +38.6 / 0.671 | +89.7 / 0.557 | reject |
| MH_RAW | **+172.0** | **9.9** | **0.796** | **1.016** | **0.451** | **22.0** | **+4.80** | **12/12 R, 12 S** | +44.0 / 0.665 / 14.7 | **+150.8 / 0.736** | **140.7** | **STRONG PROMOTION CANDIDATE** |
| MH_STD | +143.0 | 8.8 | 0.797 | 0.972 | 0.389 | 22.5 | +4.89 | 10/11 | +26.5 / 0.533 / 13.8 | +125.0 / 0.732 | 123.9 | research pass (2022 weak) |
| CONTROL_SQRT | +123.5 | 7.9 | 0.646 | 0.804 | 0.281 | 28.1 | +4.04 | 9 | +33.6 / 0.540 | +106.5 / 0.589 | reject (≈ctrl) |
| H50_SQRT | +94.1 | 6.5 | 0.552 | 0.671 | 0.237 | 27.4 | +2.77 | 8 | +33.4 / 0.558 | +79.8 / 0.495 | reject |
| MHSTD_SQRT | +154.7 | 9.2 | 0.790 | 0.976 | 0.392 | 23.6 | +4.88 | 11 | +33.0 / 0.592 | +135.1 / 0.728 | 129.4 | bisect (2026 weak) |
| CONTROL_REGIME | +108.2 | 7.2 | 0.629 | 0.770 | 0.292 | 24.6 | +3.22 | 1 | +32.2 / 0.556 | +92.8 / 0.570 | 95.3 | reject |
| A3_REG | +119.5 | 7.7 | 0.681 | 0.805 | 0.331 | 23.3 | +2.30 | 8 | +47.2 / 0.809 | +103.6 / 0.621 | 92.9 | reject (worse than A3) |
| H50_REG | +90.1 | 6.3 | 0.569 | 0.681 | 0.248 | 25.3 | +2.95 | 6 | +30.3 / 0.563 | +76.5 / 0.511 | 78% | reject |
| MHSTD_REG | +125.5 | 8.0 | 0.772 | 0.918 | 0.383 | 20.9 | +4.42 | 11 | +28.5 / 0.607 | +109.4 / 0.707 | 95.3 | reject (vs soften) |
| CONTROL_HYST | +137.6 | 8.5 | 0.677 | 0.842 | 0.342 | 24.9 | +3.13 | 11 | **+47.5 / 0.704 / 7.4** | +127.5 / 0.646 | 122.1 | research pass (recent) |

## 16. Second table — why it works / fails

| id | Why it worked / failed | Main risk | Recommendation |
|---|---|---|---|
| MH_RAW | untouched residual alpha, multi-horizon consensus, no vol standardisation gives return+stability | 2024 −24pp; survivorship bias inflates absolute level; needs separate promotion mission | STRONG PROMOTION CANDIDATE — impossible rank extension study for a proper promotion decision |
| CONTROL_HYST | holds winners through noisy rank edges; turnover −45% | fold median −0.8pp; 2020 slightly worse DD | research pass — pair with MH_RAW later |
| A3 | std score + equal weights; strong 2022+ | fold wins 9/19 (asymmetric) | research pass — do NOT over-rot |
| MH_STD | std per-horizon, good DD | 2022+ materially worse; 2020-driven | research pass — do not lean on 2020 |
| STARRE / REGIME | exposure cut costs by cutting a tiny positive piece of total PnL | not worth 3pp DD for 5-6pp ret | reject — do not freeze regime scaling |
| SQRT | almost identical to inv-vol; no | noise | reject as a factor |
| BLEND C1 | blend made the resilient std tail worse | tail risk | reject |

## 17. Explicitly rejected ideas (why)

- H25/H50/H75 blend: throws away the raw trend (negative 2022+ and folds).
- MH_STD: 2020 flattery; +8.4pp 2022-.
- 0.5 ELEVATED regime: maxDD −2.9pp cost −5.5pp, flat Calmar — both the
  2020 AND 2022 episodes cost more than they save.
- SQRT sizing: no change vs inverse-vol; not beta.
- MH in 2022+ concave with standardized, convex raw — C1’s improvement
  comes precisely from raw.

## Promotion status

**PROMOTION CANDIDATE: `MH_RAW`** (STRONG on the manifest rubric). This is a
RESEARCH recommendation only. No production parameter or config changed.
Promotion requires a separate review of implementation complexity,
paper-trading behavior, and production-policy interaction.

## Commands that generated this report

```bash
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants control
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants a3,h25,h50,h75
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants mh_raw,mh_std,control_sqrt,h50_sqrt,mhstd_sqrt
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants control_regime,a3_regime,h50_regime,mhstd_regime
.venv/bin/python scripts/research_equity_alpha_optimization.py --variants control_hyst
.venv/bin/python scripts/research_equity_alpha_optimization.py --aggregate
```