# put_hedge_v1 — protective-put overlays on production K (Turkish after tax)

> Evidence: tag `archive/research/put-hedge-v1-2026-10` (not on `main`).

**Verdict: no arm passes. K stays production.** A systematic protective-put overlay does not
improve the Turkish after-tax return and does not cut the drawdown that matters. The notional
arms (H1, H2, H4, H5) cost **3.8–4.7 pp/yr** and lose **2.3–4.2 pp** after tax in every window;
H4 shows that the insurance does **not** make vol target 0.45 affordable. The cheapest arm (H3,
a 1 %/yr premium budget in 3-month 15 % OTM puts) costs **0.85 pp/yr**, is the closest miss at
**−0.54 pp pre-tax / −0.64, −1.03, −1.09 pp after tax** on the three windows, and still fails
every return gate and the drawdown gate's spirit: its max DD is **+0.48 pp** *worse* than K's.

The pricing model was validated first: the same Black–Scholes pricer reproduces Cboe's PPUT to
**+0.68 / −0.43 pp/yr** with monthly-return correlation **0.995 / 0.984** in 1990–1999 and
2001–2026. The failure is economic, not a pricer artifact.

Evidence: `research/put_hedge_v1/` (`PREREGISTRATION.md`, `screen.py`, `overlay_sim.py`,
`RESULTS.json`, `SUMMARY.txt`, `DATA_MANIFEST.json`), `research/fast_engine/`,
`research/band_h2_combo_v1/`. Pre-registration commits `b5f4b294`, `cbf3c24c`; results commit
after this report. `--run` wall clock 23.9 s; two runs byte-identical (`RESULTS.json` SHA-256
`60ee19f7…8de19a2`).

## 1. Sanity check (run before any QQQ arm)

The pricer is Black–Scholes on the close, `σ(K) = VXN·(1 + 2.0·ln(S/K))`, constant dividend
yield, third-Friday monthly roll, strike 5 % OTM, intrinsic settlement. Applied to **SPX + VIX**
with the same rule as PPUT (5 % OTM 1-month put, total return, third Friday) and compared with
the published Cboe PPUT:

| window | synthetic | PPUT | diff | monthly corr | RMSE |
|---|---:|---:|---:|---:|---:|
| 1990–1999 | 12.65 % | 11.97 % | **+0.68 pp** | 0.9946 | 0.382 pp/mo |
| 2001–2026 | 6.37 % | 6.80 % | **−0.43 pp** | 0.9835 | 0.647 pp/mo |
| `s = 1.0` (−50 %) | | | +1.22 / −0.11 pp | | |
| `s = 3.0` (+50 %) | | | +0.11 / −0.79 pp | | |

Pass criteria (|diff| ≤ 1.5 pp in both windows, corr ≥ 0.95): **PASS**. The ±50 % rows are
inside the same band, so the conclusion is not a skew-slope artifact.

## 2. Method

- **Control K**: production R2 target series (E2 2-of-3 vote + half-weight early re-entry,
  `vt = min(1, 0.35/vol20)`, cap 0.98) with A1u's gain-scaled downsize band and H2's December
  deferral, the engine's Turkish after-tax account, re-simulated on the same reduced window.
- **Reduced main window** `2001-01-23 → 2026-09-29` (first VXN session; the full window starts
  2000-03-09 and is not VXN-covered). After-tax windows at $100k: 2001, 2010, 2017.
- **Overlay**: one option position at a time on **QQQ**; the notional is `2 · w_QLD · NAV · 1`
  (QLD beta 2), only while the QLD weight is positive; the premium is paid at the roll close,
  funded from cash or a QLD sale at that close; the option is marked to BS daily for the NAV;
  the tax base is the realised cash flow only. Rolls on the third Friday (preceding session if a
  holiday), first roll on/after the window start, held to expiry, intrinsic at the close.
- **Cost** 5 % of premium per side. **Skew** `s = 2.0`, `q = 0.006` (QQQ), OOS `q = 0.018`
  (SPX). **Tax** option net P&L per calendar year in TRY, progressive tariff, no Yİ-ÜFE
  indexation on the option book, option losses offset only option gains (conservative); the
  `offset = 1` sensitivity lets option losses offset equity gains (non-selectable).
- **Arms** (pre-registered, no tuning): H1 1-month 10 % OTM; H2 2-month 10 %/25 % put spread;
  H3 1 % NAV/yr premium budget in 3-month 15 % OTM puts; H4 H2 + vol target 0.45; H5 H2 only
  while the E2 vote is on (not on the early leg).
- **Bootstraps** B1-recent (progressive) and B2 (flat 20 %), seed 20261011, block 20,
  5,000 pre-tax / 1,000 after-tax paths, $100k, fixed 2015-09 → 2026-09 calendar.
- **OOS (reduced, pre-registered)**: SPX + VIX, measured from 1990-06 (after the 253-session
  warm-up) to 1999-12-29, pre-tax, calibrated 2× leg, m = 2. G7 is this one window; G8 is n/a.

## 3. Main-window results (reduced window, m = 2)

| arm | pre CAGR base | pre CAGR cal. | max DD base | max DD cal. | after tax $100k | AT max DD | Δ AT vs K | boot B1 Δ (share) | boot B2 Δ (share) | turnover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **K** | **17.90 %** | **17.29 %** | **38.46 %** | **38.70 %** | **12.42 %** | **45.24 %** | — | — | — | 5.41 |
| H1 | 14.92 % | 14.33 % | 40.86 % | 41.09 % | 9.45 % | 46.87 % | −2.97 | −6.45 (1 %) | −6.42 (1 %) | 5.43 |
| H2 | 13.97 % | 13.37 % | 43.33 % | 43.55 % | 8.20 % | 49.34 % | −4.22 | −3.69 (10 %) | −3.63 (12 %) | 5.43 |
| H3 | 17.36 % | 16.75 % | 38.94 % | 39.18 % | 11.78 % | 45.67 % | −0.64 | −0.52 (23 %) | −0.52 (24 %) | 5.42 |
| H4 | 14.85 % | 14.21 % | 43.81 % | 44.03 % | 9.07 % | 49.78 % | −3.35 | −4.39 (6 %) | −4.36 (8 %) | 5.33 |
| H5 | 14.29 % | 13.70 % | 40.88 % | 41.11 % | 8.50 % | 47.07 % | −3.92 | −3.39 (12 %) | −3.27 (14 %) | 5.43 |

After-tax windows (Δ vs K, pp):

| arm | 2001–2026 | 2010–2026 | 2017–2026 |
|---|---:|---:|---:|
| H1 | −2.97 | −2.28 | −2.47 |
| H2 | −4.22 | −3.83 | −3.81 |
| H3 | −0.64 | −1.03 | −1.09 |
| H4 | −3.35 | −2.91 | −1.62 |
| H5 | −3.92 | −3.44 | −3.44 |

H4 is the decisive arm for the "insurance makes leverage affordable" question: raising the vol
target from 0.35 to 0.45 while carrying the H2 spread gives 14.85 % pre-tax (K 17.90 %) and a
**43.81 %** max DD (K 38.46 %); the higher exposure adds less than the extra hedge cost. vt 0.45
is not made affordable.

## 4. Gates

G1–G9 are `ewma_band_v1`'s on the reduced window; AT1/AT2 are the after-tax gates. G7 is the
one-window reduced OOS; G8 is n/a by pre-registration.

| arm | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G9 | AT1 | AT2 | all |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 | fail | fail | fail | fail | fail | fail | fail | pass | fail | fail | **fail** |
| H2 | fail | fail | fail | fail | fail | fail | fail | pass | fail | fail | **fail** |
| H3 | fail | **pass** | fail | fail | fail | fail | fail | pass | fail | fail | **fail** |
| H4 | fail | fail | fail | fail | fail | fail | fail | pass | fail | fail | **fail** |
| H5 | fail | fail | fail | fail | fail | fail | fail | pass | fail | fail | **fail** |

H3 is the closest: G1 fails on both limbs (17.36 % vs 17.90 %, Calmar 0.446 vs 0.512); G3 by
1.11 pp in 2017+ (32.12 vs 33.23); G5 by 0.49 pp; G6 by 0.54 pp; G7 by 1.03 pp; AT1 in all three
windows; AT2 at 23–24 % of paths. Nothing is a candidate, so the selection rule returns **none**.

## 5. Drawdown, hedge cost and crash behaviour

| arm | max DD | >10 % | >20 % | >30 % | worst 21s | worst 63s | premium pp/yr | payoff pp/yr | net cost pp/yr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| K | 38.46 % | 32 | 14 | 6 | −29.90 % | −31.95 % | — | — | — |
| H1 | 40.86 % | 28 | 14 | 7 | −31.18 % | −33.68 % | 5.35 | 1.56 | **3.79** |
| H2 | 43.33 % | 27 | 13 | 3 | −31.06 % | −34.54 % | 6.71 | 2.43 | **4.28** |
| H3 | 38.94 % | 31 | 14 | 5 | −30.12 % | −32.30 % | 0.91 | 0.06 | **0.85** |
| H4 | 43.81 % | 28 | 12 | 6 | −31.08 % | −34.62 % | 7.37 | 2.70 | **4.67** |
| H5 | 40.88 % | 29 | 13 | 4 | −30.35 % | −32.46 % | 6.51 | 2.17 | **4.33** |

No arm cuts the maximum drawdown. H2/H5 cut the *number* of >30 % episodes (3–4 vs 6) but deepen
the worst one. One nuance: in the **2017+ after-tax** window H2 and H5 do cut the drawdown
(31.8 % vs K's 35.0 %), but they give up 3.8 pp/yr of return; the full-window maximum is still the
2015–16 grind, which the puts do not catch. The reason is K's own timing: K is already in cash
through most of the fast crashes, so the put is redundant there, while the largest drawdown on the
reduced window is the 2015–16 grind — a slow decline that a third-Friday 1- or 2-month roll does
not catch (the August 2015 roll expired on 21 August, before the 24 August low). The premium is
paid every month regardless.

Option cash flows in the four major stress windows (net = payoff − premium, $k, on a $100k start;
NAV return is the arm's over the window):

| window | QQQ | K NAV | H1 net / NAV | H2 net / NAV | H3 net / NAV | H4 net / NAV | H5 net / NAV |
|---|---:|---:|---|---|---|---|---|
| 2001–02 | −64.0 % | −13.0 % | −6.0 / −18.5 % | −2.2 / −15.0 % | −0.5 / −13.3 % | −2.7 / −19.1 % | 0.0 / −13.0 % |
| 2008-09→2009-03 | −33.4 % | −0.2 % | +1.5 / +0.2 % | +27.1 / +21.1 % | +10.2 / +6.7 % | +34.1 / +27.4 % | +28.2 / +21.1 % |
| 2020-02→2020-04 | −1.6 % | −14.7 % | +211.8 / **+18.9 %** | −20.5 / −16.5 % | −3.0 / −14.8 % | −25.0 / −21.7 % | −22.6 / −16.5 % |
| 2022 | −33.7 % | −24.3 % | −1.1 / −24.3 % | +45.0 / −20.8 % | −1.7 / −24.4 % | +64.7 / −26.1 % | +22.7 / −22.9 % |

The insurance *works* in the episodes it is designed for: H1 turns the Feb–Apr 2020 window from
−14.7 % to +18.9 % (a $223k payoff), and H2/H4/H5 add 21–27 % in the 2008 window. But the
premiums in the calm years are larger in present-value terms: H1's long-run net cost is 3.79 pp/yr
(and 5.4 pp/yr of gross premium). The 2020 H1 payoff is the single best episode; on the bootstrap
it is not enough to make H1 beat K in more than **1 %** of paths.

The 2-month spread missed the 2020 crash entirely: the February roll's 10 %/25 % spread expired
on 17 April, after QQQ had recovered above the strike, so the payoff was zero; H1's 1-month
**February** roll expired on 20 March at the low and paid.

## 6. Out-of-sample (SPX + VIX, 1990-06 → 1999-12-29, pre-tax, calibrated, m = 2)

| arm | CAGR | max DD | Δ CAGR | Δ DD |
|---|---:|---:|---:|---:|
| K | 16.03 % | 25.49 % | — | — |
| H1 | 13.01 % | 27.04 % | −3.01 | +1.55 |
| H2 | 11.30 % | 27.77 % | −4.72 | +2.28 |
| H3 | 14.99 % | 25.57 % | −1.03 | +0.08 |
| H4 | 11.89 % | 30.01 % | −4.13 | +4.52 |
| H5 | 11.41 % | 27.77 % | −4.61 | +2.28 |

All arms lose out of sample; H3 is the least bad. No after-tax OOS exists (the TRY calendar
starts in 2000), so this is the pre-registered reduced G7.

## 7. Sensitivity: option losses offset equity gains

If option losses were allowed to offset equity gains (non-selectable, and not the conservative
reading), the after-tax numbers improve by:

| arm | 2001 | 2010 | 2017 |
|---|---:|---:|---:|
| H1 | +0.87 | +0.89 | +1.30 |
| H2 | +1.25 | +1.32 | +1.77 |
| H3 | +0.20 | +0.22 | +0.26 |
| H4 | +1.23 | +1.31 | +1.71 |
| H5 | +1.20 | +1.24 | +1.65 |

Even the most favourable tax reading leaves every arm behind K (−2.5 to −3.0 pp for the notional
arms; −0.4 to −0.9 pp for H3). The failure is the pre-tax premium, not the tax asymmetry.

## 8. Findings

1. **Insurance is dominated by prediction in this strategy.** K already exits to cash in the
   fast crashes; the put's payoff is largest exactly when K is flat, so it adds little to the
   NAV. The premium is paid in all the calm months. The net cost is 3.8–4.7 pp/yr for a full
   notional hedge and 0.85 pp/yr for the 1 % budget.
2. **The drawdown that matters is not a crash.** The reduced window's largest drawdown is the
   2015–16 grind (K 38.46 %); the monthly put roll did not catch it, and every notional arm is
   deeper (40.9–43.8 %). H2/H5 cut the count of >30 % episodes but not the maximum.
3. **Vol target 0.45 is not made affordable.** H4 (H2 + vt 0.45) is 14.85 % pre-tax with a 43.81 %
   DD; K is 17.90 % / 38.46 %. The extra exposure adds less than the extra hedge cost. This
   closes the mechanism `exposure_k_v1` left open.
4. **The 1 %/yr budget is the only sensible version, and it is still a loser.** H3's 0.85 pp/yr
   buys 0.06 pp/yr of payoff; it is almost pure cost, fails G1 by 0.54 pp and G7 by 1.03 pp.
5. **Timing matters more than structure.** H1's 1-month put captured the COVID crash (+223k
   payoff, +18.9 % vs K −14.7 %); H2's 2-month spread expired after the recovery and paid zero.
   The same H1 still lost 6.5 pp/yr in the bootstrap, so the episode does not generalize.

## 9. Caveats

- **Model prices, not traded prices.** Black–Scholes with one VXN level, a constant multiplicative
  skew (s = 2.0) and no term structure; 5 % of premium per side; the roll is at the close while
  the published PPUT uses the SOQ and a VWAP. The PPUT check bounds the aggregate error on SPX
  at ±0.7 pp/yr, but the QQQ option market is not the SPX market.
- **The reduced window flatters K.** It starts in January 2001, after the first leg of the
  dot-com bust; K's full-window CAGR is 15.76 % versus 17.90 % here. The overlay loses on the
  2010 and 2017 windows and in the bootstrap too, so the conclusion is not a window artifact.
- **Option marks drive the drawdown path.** The NAV marks the put daily at BS; the tax base uses
  only realised flows. A real account marking at bid/offer would be slightly worse.
- **Tax.** The option treatment is pre-registered and conservative, not adviser-confirmed. The
  `offset` row is a sensitivity, not a recommendation. **Not tax advice.**
- **One history per cell; the bootstrap calendar is 2015-09 → 2026-09.** It cannot price another
  FX/inflation regime. G8 (OOS bootstrap) is not run on the reduced OOS.

## 10. Reproduction and evidence

- `PREREGISTRATION.md` fixed every parameter, arm, gate and the selection rule before the formal
  run (commits `b5f4b294`, `cbf3c24c`). The pre-registration discloses the exploratory probes
  made before the commit; no parameter, arm, gate or selection rule changed after them.
- `--harness`: parity `--quick` PASS; K anchors PASS (pre-tax 15.7649 / 15.1785 %, max DD
  46.670 / 46.795 %, m1 16.0173 / 15.4303 %; after tax 10.7533 / 15.2354 / 23.9720 %, max DD
  46.670 / 44.627 / 34.977 %; R2 15.4766 / 14.8961 %, after tax 10.1600 / 14.7059 / 22.8861 %);
  zero-flow parity **exact** on 12 cells; bootstrap parity **exact** on 64 paths × 2 financing ×
  2 tax modes; `main_inputs_vt(0.35)` bit-identical; PPUT sanity PASS.
- `--run` 23.9 s; two runs byte-identical (`RESULTS.json` SHA-256
  `60ee19f76f54800f667e48d012430895cfc1c3ee277a4991f0a55a6c68de19a2`).
- The K bootstrap cells reproduce the published `band_h2_combo_v1` cells exactly: pre-tax base
  7.7761 %, calibrated 7.3137 %, after tax progressive 3.8577 %, flat 4.0376 %, pure-E2 anchor
  3.0790 % / 3.5201 %.
