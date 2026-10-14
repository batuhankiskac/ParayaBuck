# Fast re-entry v1 (2026-10-14)

> Evidence for this report (`research/fast_reentry_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/fast-reentry-v1-2026-10`.

**One arm passes: R2, the early re-entry path at half production weight.** It clears all
14 gate keys (G1–G9, both financing models) on top of production E2
(`equity_leveraged_trend_v1` 1.1.0). The other three arms fail.

- **R2** (R1's entry/exit rules, entered at **half** the production weight): +0.81 pp CAGR
  base / +0.80 pp calibrated, max DD 46.8% against 49.6%, bootstrap median Δ +0.68 pp,
  OOS median Δ +0.07 pp, turnover 5.99/yr against 5.97. It fails nothing.
- **R1** (the same path at full weight) is the stronger arm on every in-sample measure —
  +1.50 pp CAGR, DD −5.0 pp, bootstrap Δ +1.15 pp — and fails only **G7**: two of the six
  out-of-sample windows breach the +3 pp drawdown allowance (M1 ^IXIC +4.60 pp, M5 ^FTSE
  +5.13 pp). The gate's drawdown tolerance is absolute, so a full-size early position is
  too large out of sample even where it gains return.
- **R3** (Donchian-126 + SMA50, no vol filter) fails G4 and G7.
- **R4** (R1 restricted to 252 sessions after a ≥20% drawdown exit) fails G1, G2, G4 and
  G6: it deepens max DD to 52.3% and its early path nets +0.15 pp over 13 episodes, i.e.
  nothing.

The pass is narrow and concentrated, and the motivating case is **not** among the wins:

- The early path is active in only **2.46% of sessions** (164 of 6,679) and 32 episodes.
  Three episodes — 2001-10-31, 2002-11-04 and 2023-01-20 — produce **95%** of R2's total
  relative gain (+18.78 pp of +19.72 pp).
- **No arm had a single early episode in 2020.** E2's own 2-of-3 vote turned on
  2020-04-08, while SMA50 and Donchian-126 both turned on only 2020-04-14 and 20-session
  vol was 0.87 against a 252-session median of 0.155. In the fastest crash the fast signals
  lag *more* than SMA200 + TSM252, so there was no gap to fill.
- G7 passes with exactly 5 of 6 windows at Δ CAGR ≥ −0.5 pp and M5's Δ DD at +2.71 pp
  against the +3 pp limit.

Evidence: `research/fast_reentry_v1/` (`PREREGISTRATION.md`, `screen.py`,
`DATA_MANIFEST.json`, `RESULTS.json`, `SUMMARY.txt`). The pre-registration, `screen.py`,
`DATA_MANIFEST.json`, the input copies and `E2_INTEGRATION_DEVIATION.json` were committed
in `64ec38e5`, before any non-control arm was simulated on real data.

- The outputs are `RESULTS.json` and `SUMMARY.txt` from `screen.py --run`. The run is
  offline, takes about 5 minutes and is deterministic: three runs gave byte-identical
  `RESULTS.json` (SHA-256 `65a3bc1e941ca0bf3956651e19073ca0e5ceed59db1d7078869f75f354294f14`).

## Question

Production E2 needs 2 of 3 votes (SMA200, 252-session price return > 0, Donchian-126
midpoint) to re-enter. After a sharp V-shaped crash SMA200 and TSM252 lag, so E2 re-enters
late. Does an explicit **early re-entry path, active only while E2 is off**, recover the
early phase of a V-shaped rally without worsening drawdown? Exits are unchanged from
production in every arm, and the early path ends the moment E2's own vote turns on.

## Method

- **Control (C0).** Production E2 1.1.0: at least 2 of 3 votes on the QQQ price close
  (close > SMA200; 252-session price return > 0, zero hurdle; close > 126-session channel
  midpoint), sized `vt = min(1, 0.35/vol20)`, total weight capped at 0.98.
- **Mechanics.** As `ewma_band_v1` / `e2_integration_v1`: synthetic QLD\*, daily-reset 2x
  on QQQ total return, 0.95%/yr expense plus DTB3 (base) or DTB3 + 0.75% (calibrated);
  decide at the completed close, trade at the next open; cost 4 bps × m; 0.5 pp resize
  band; idle cash earns zero; window 2000-03-09 → 2026-09-29.
- **Arms (four; no others).** While E2 is off, an early position is sized with production
  vol sizing (except R2) and is dropped when E2's own vote turns on:
  - **R1:** enter if QQQ close > SMA50 **and** 20-session annualized close vol < its own
    trailing 252-session median **and** close > close 20 sessions ago; exit if close < SMA50.
  - **R2:** R1 at **half** the production weight until E2 turns on.
  - **R3:** enter if the Donchian-126 vote is on **and** close > SMA50; exit if either
    turns off.
  - **R4:** R1, but entries allowed only within 252 sessions after the latest E2 exit that
    followed a QQQ drawdown ≥ 20% from its 252-session high.
- **Harness.** C0 reproduces `e2_integration_v1`'s production-E2 row exactly (difference
  0.000000 pp) for CAGR and max DD under base and calibrated financing at m = 1 and m = 2,
  and the band simulator at 0.5 pp equals `trend_robustness_v2.simulate_multi` bit for bit.
- **Gates.** `ewma_band_v1`'s G1–G9, unchanged; all must pass. "Both" means base and
  calibrated financing.
  - **G1 (m = 2, both):** CAGR ≥ C0, or Calmar ≥ 1.1 × C0 with CAGR ≥ C0 − 0.5 pp.
  - **G2 (both):** max DD ≤ C0 + 1 pp.
  - **G3 (both):** 2017+ CAGR ≥ C0 − 1 pp.
  - **G4:** CAGR above C0 in ≥ 2 of 3 sub-periods: P1 2000–08, P2 2009–16, P3 2017 → end.
  - **G5 (both):** block-20 bootstrap (seed 20261011, 5,000 ten-year paths, draws identical
    to `signal_ensemble_v1`) median of arm − C0 ≥ 0.
  - **G6 (both):** G1 at m = 1.
  - **G7:** six out-of-sample windows — ^GSPC 1957–1999 and M1–M5 — calibrated, m = 2.
    Median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp in ≥ 5 of 6; Δ DD ≤ +3 pp in ≥ 5 of 6 and never
    > +8 pp.
  - **G8:** pooled out-of-sample bootstrap over M1–M5 (2,000 paths each) median Δ ≥ 0.
  - **G9:** turnover/yr ≤ 2 × C0 (no arm is a trading-more design; the limit is the
    pre-registered one for a 0.5 pp band).
- **Selection.** The passing candidate with the highest base m = 2 CAGR. Here R2 is the only
  passing arm.

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD | Calmar | 2017+ | P1 / P2 / P3 | m = 1 CAGR | Boot. median Δ | Turnover/yr |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| **C0 (prod. E2)** | **14.67%** | **14.10%** | **49.6%** | **0.296** | **32.44%** | −1.10 / 13.26 / 32.44 | **14.95%** | — | **5.97** |
| R1 | 16.17% | 15.58% | 44.6% | 0.363 | 32.91% | +0.78 / 15.31 / 32.91 | 16.45% | +1.147 pp | 6.04 |
| **R2** | **15.48%** | **14.90%** | **46.8%** | **0.331** | **32.71%** | −0.06 / 14.30 / 32.71 | **15.75%** | **+0.683 pp** | **5.99** |
| R3 | 15.01% | 14.43% | 48.8% | 0.308 | 31.61% | −1.37 / 15.58 / 31.61 | 15.27% | +0.338 pp | 5.85 |
| R4 | 14.64% | 14.07% | 52.3% | 0.280 | 32.44% | −1.72 / 13.95 / 32.44 | 14.91% | +0.054 pp | 5.92 |

Calibrated max DD: C0 49.7%, R1 44.7%, R2 46.9%, R3 48.9%, R4 52.4%. Sharpe (base):
C0 0.654, R1 0.694, R2 0.678, R3 0.661, R4 0.650. Time in market: C0 74.3%, R1/R2 76.8%,
R3 75.8%, R4 75.3%. Bootstrap medians (base): C0 7.16%, R1 8.36%, R2 7.89%, R3 7.57%,
R4 7.53%; R2 beats C0 on 68.9% of paths (R1 66.2%, R3 59.7%, R4 68.8%), with p25 −0.28 pp
and p75 +1.66 pp. Calibrated bootstrap median Δ: R1 +1.098, R2 +0.655, R3 +0.324,
R4 +0.045 pp. Over the Stage B window (2017-01-03 → 2026-07-30, base, m = 2): C0 31.42% /
32.5%, R2 31.70% / 31.1%, R1 31.90% / 31.1%, R3 30.58% / 37.7%, R4 31.42% / 32.5%.

Report-only cost stress (base, not gated): CAGR at m = 1 / m = 2 / m = 4 is
C0 14.95 / 14.67 / 14.13, R1 16.45 / 16.17 / 15.61, R2 15.75 / 15.48 / 14.92,
R3 15.27 / 15.01 / 14.47, R4 14.91 / 14.64 / 14.10. R2's edge survives doubled cost
(+0.79 pp against C0's).

### Out of sample (calibrated, m = 2)

| Window | C0 | R1 | R2 | R3 | R4 |
|---|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.98% / 39.0% | 9.75% / 40.4% | 9.89% / 39.6% | 11.00% / 38.0% | 9.98% / 39.0% |
| M1 ^IXIC 1975–1999 | 20.68% / 38.6% | 21.61% / 43.2% | 21.18% / 41.0% | 21.67% / 36.6% | 20.68% / 38.6% |
| M2 ^GSPC 1935–1956 | 11.26% / 62.5% | 12.04% / 56.6% | 11.70% / 58.3% | 11.58% / 60.9% | 11.63% / 62.5% |
| M3 ^N225 1966–2026 | 6.33% / 67.2% | 6.39% / 67.3% | 6.40% / 65.0% | 6.66% / 70.5% | 6.39% / 66.1% |
| M4 ^GDAXI 1989–2026 | 9.41% / 58.6% | 9.47% / 57.6% | 9.47% / 58.1% | 9.04% / 58.1% | 9.54% / 57.6% |
| M5 ^FTSE 1985–2026 | −0.05% / 82.7% | −1.16% / 87.9% | −0.59% / 85.5% | −0.57% / 86.4% | 0.26% / 80.8% |
| Median Δ CAGR | — | +0.06 pp | +0.07 pp | +0.32 pp | +0.10 pp |
| Pooled bootstrap median Δ (10,000 paths) | — | +0.613 pp | +0.360 pp | +0.309 pp | +0.000 pp |

Δ DD against C0, by window: R1 +1.4 / **+4.6** / −5.9 / +0.2 / −1.0 / **+5.1** pp
(M1 and M5 breach +3 pp → G7 fails); R2 +0.6 / +2.4 / −4.2 / −2.1 / −0.5 / +2.7 pp
(all six within +3 pp → G7 passes, M5 by 0.3 pp); R3 −1.0 / −2.1 / −1.6 / **+3.4** /
−0.5 / **+3.7** pp (two breaches); R4 0.0 / 0.0 / 0.0 / −1.1 / −1.0 / −2.0 pp.
Δ CAGR: R2 −0.09 / +0.50 / +0.44 / +0.07 / +0.06 / **−0.54** pp — five of six windows clear
the −0.5 pp bar, exactly the G7 minimum.

### Gates

| Gate | R1 | R2 | R3 | R4 |
|---|---|---|---|---|
| G1 return (base / cal.) | pass / pass | pass / pass | pass / pass | **fail / fail** |
| G2 DD ≤ C0 + 1 pp | pass | pass | pass | **fail** |
| G3 2017+ ≥ C0 − 1 pp | pass | pass | pass | pass |
| G4 sub-periods | pass (P1, P2, P3) | pass (P1, P2, P3) | **fail (P2)** | **fail (P2)** |
| G5 bootstrap Δ ≥ 0 | pass (+1.147 / +1.098) | pass (+0.683 / +0.655) | pass (+0.338 / +0.324) | pass (+0.054 / +0.045) |
| G6 G1 at m = 1 | pass | pass | pass | **fail** |
| G7 OOS windows | **fail** (Δ DD +4.6, +5.1) | **pass** | **fail** (Δ DD +3.4, +3.7) | pass |
| G8 OOS bootstrap | pass (+0.613) | pass (+0.360) | pass (+0.309) | pass (+0.000) |
| G9 turnover ≤ 2 × C0 | pass | pass | pass | pass |
| **Result** | **fail** | **pass** | **fail** | **fail** |

Selected arm: **R2**. (R4's pooled bootstrap median Δ is +0.000 pp: its early path adds
nothing net.)

## The early path: per-episode P&L (base, m = 2)

An episode runs from the close before the first early fill to the close of the
**reconciliation session** — the open at which the early-driven position is sold to cash or
merged into E2's position — so it carries the exit fill and the overnight gap the early
position earned. The product of (1 + delta) over episodes reproduces the arm's total
relative gain: R1 +41.63% against +41.09% realised, R2 +20.56% against +20.33%,
R3 +8.02% against +7.94%, R4 −0.70% against −0.78%.

**R2's 32 episodes** (delta = the arm's return minus C0's over the same span):

| Entry decision | Last held | Sessions | Arm | C0 | Δ |
|---|---|---|---:|---:|---:|
| 2001-02-01 | 2001-02-02 | 1 | −2.15% | 0.00% | −2.15 pp |
| 2001-05-16 | 2001-06-13 | 19 | −2.49% | 0.00% | −2.49 pp |
| 2001-08-02 | 2001-08-03 | 1 | −0.98% | 0.00% | −0.98 pp |
| 2001-10-23 | 2001-10-30 | 5 | −0.48% | 0.00% | −0.48 pp |
| **2001-10-31** | **2001-12-05** | **24** | **+8.97%** | +0.39% | **+8.58 pp** |
| 2001-12-13 | 2001-12-17 | 2 | +1.69% | +0.35% | +1.34 pp |
| 2001-12-20 | 2001-12-28 | 5 | −0.95% | −1.90% | +0.95 pp |
| 2002-01-02 | 2002-01-03 | 1 | +1.09% | −0.62% | +1.71 pp |
| 2002-01-14 | 2002-01-15 | 1 | −2.71% | −2.06% | −0.65 pp |
| 2002-03-08 | 2002-03-13 | 3 | −1.48% | 0.00% | −1.48 pp |
| **2002-11-04** | **2002-12-02** | **19** | +2.12% | −1.33% | **+3.45 pp** |
| 2002-12-03 | 2002-12-19 | 12 | −4.10% | −2.06% | −2.04 pp |
| 2003-01-06 | 2003-01-07 | 1 | −2.01% | −1.96% | −0.05 pp |
| 2003-01-08 | 2003-01-09 | 1 | +4.63% | +4.36% | +0.26 pp |
| 2004-09-02 | 2004-09-03 | 1 | −0.21% | 0.00% | −0.21 pp |
| 2004-09-09 | 2004-09-10 | 1 | +3.62% | +1.74% | +1.88 pp |
| 2004-09-22 | 2004-09-30 | 6 | +5.01% | +3.56% | +1.44 pp |
| 2005-05-13 | 2005-05-18 | 3 | +3.97% | +1.06% | +2.91 pp |
| 2006-09-01 | 2006-09-05 | 1 | −2.30% | −2.28% | −0.02 pp |
| 2008-08-21 | 2008-08-22 | 1 | −1.49% | −1.61% | +0.12 pp |
| 2008-08-25 | 2008-08-28 | 3 | −1.57% | −1.79% | +0.21 pp |
| 2008-08-29 | 2008-09-02 | 1 | +0.19% | +2.60% | −2.41 pp |
| 2009-04-21 | 2009-04-30 | 7 | +3.50% | +0.32% | +3.18 pp |
| 2009-05-13 | 2009-05-18 | 3 | +3.91% | +1.41% | +2.50 pp |
| 2010-08-18 | 2010-08-19 | 1 | −1.12% | 0.00% | −1.12 pp |
| 2012-12-17 | 2012-12-18 | 1 | −0.20% | −1.49% | +1.28 pp |
| 2016-03-21 | 2016-03-22 | 1 | −0.65% | −1.32% | +0.67 pp |
| 2016-03-23 | 2016-03-24 | 1 | −1.06% | −1.92% | +0.86 pp |
| 2022-07-25 | 2022-08-30 | 26 | +0.65% | 0.00% | +0.65 pp |
| 2022-12-13 | 2022-12-15 | 2 | −3.10% | 0.00% | −3.10 pp |
| 2023-01-17 | 2023-01-19 | 2 | −1.85% | 0.00% | −1.85 pp |
| **2023-01-20** | **2023-02-01** | **8** | **+8.74%** | +1.99% | **+6.75 pp** |

- 18 of 32 episodes are positive (56%); median +0.24 pp, mean +0.62 pp, sum +19.72 pp.
- **Three episodes are 95% of the total**: 2001-10-31 (+8.58 pp, 43%), 2023-01-20
  (+6.75 pp, 34%), 2002-11-04 (+3.45 pp, 18%). Dropping the largest of the three leaves
  +11.13 pp (about +0.44 pp CAGR); dropping all three leaves +0.94 pp.
- The other arms, same measurement: R1 32 episodes, sum +38.85 pp, best +17.58 pp
  (2001-10-31), worst −6.16 pp (2022-12-13); R3 33 episodes, sum +9.25 pp, best +7.60 pp
  (2009-04-02), worst −6.03 pp (2022-08-10); R4 13 episodes, sum +0.15 pp, median −0.10 pp.

### What the early path caught, and what it missed

| Case | E2 re-entry | Early entry (R1/R2) | Early entry (R3) | R2 episode Δ |
|---|---|---|---|---|
| 2001-10-31 → 2001-12-05 | 2001-12-05 | 2001-10-31 (24 sessions early) | 2001-11-14 | +8.58 pp |
| 2009-04-02 → 2009-04-30 | 2009-04-30 | 2009-04-21 (vol filter) | 2009-04-02 (19 sessions early) | +3.18 pp |
| 2023-01-20 → 2023-02-01 | 2023-02-01 | 2023-01-20 (8 sessions early) | none (Donchian late) | +6.75 pp |
| **2020-03-09 → 2020-04-08** | **2020-04-08** | **none** | **none** | **—** |
| 2022-08 → 2023-01 | — | 2022-07-25, 2022-12-13, 2023-01-17 | 2022-08-03, 2022-08-10 | −4.30 pp |

- **2020 is the counter-example to the thesis.** E2's majority vote turned on 2020-04-08;
  SMA50 and Donchian-126 turned on 2020-04-14, and 20-session vol was 0.87 (median 0.155),
  so R1/R2's vol filter and R3's Donchian gate were both shut. Nothing could enter early.
- **2009 is captured only after vol calms.** R1/R2 sat out 2009-04-02 → 2009-04-20 with
  20-session vol at 0.36–0.43 against a 252-session median of 0.34; R3 (no vol filter)
  entered on 2009-04-02 and earned +7.60 pp there, which is why R3's OOS drawdowns are
  larger.
- **The vol filter and the crash-only filter are self-defeating in fast crashes.** R4
  requires the *exit* to sit ≥ 20% below the 252-session high; E2's 2020 exit (2020-03-09)
  was only 18.3% below the high, so R4 was never eligible in 2020 even though it is the
  arm designed for exactly that.
- **The whipsaw pair is real.** R2 lost 3.10 pp on 2022-12-13 and 1.85 pp on 2023-01-17
  before gaining 6.75 pp on 2023-01-20; two of the three largest losses and the second
  largest gain sit in the same six-week window.

## Trading activity (report-only; relevant to Turkish taxation)

Base financing, m = 2. A round trip is a completed in-market episode; sells are exits plus
downward resizes.

| Arm | Round trips/yr | Resizes/yr | Sells/yr | Turnover/yr (entries+exits / resizes) | 2017+: round trips / sells / turnover per yr |
|---|---:|---:|---:|---|---|
| **C0** | **2.94** | **51.5** | **27.4** | **5.97 (4.27 / 1.70)** | **1.23 / 32.4 / 3.75** |
| R1 | 2.97 | 56.0 | 29.8 | 6.04 (4.23 / 1.80) | 1.54 / 34.6 / 4.28 |
| **R2** | **2.97** | **56.0** | **29.6** | **5.99 (3.84 / 2.15)** | **1.54 / 34.3 / 4.02** |
| R3 | 2.90 | 54.4 | 28.6 | 5.85 (4.08 / 1.77) | 1.64 / 33.2 / 4.28 |
| R4 | 2.90 | 53.6 | 28.3 | 5.92 (4.18 / 1.74) | 1.23 / 32.4 / 3.75 |

The early path adds 2.5 pp of time in market, 0.03 round trips/yr over the full
window (0.31/yr since 2017) and 1.9 more sells/yr since 2017 — R2's extra realisations are
small relative to the 27–32 sells/yr that production's vol resizes already generate. R4's
crash-only filter leaves its trade counts identical to C0 since 2017.

## Findings

- **A half-size early path is enough to pass; a full-size one is not.** R1 dominates R2 on
  every in-sample measure and still fails G7, because G7's drawdown tolerance is absolute
  (+3 pp per window) and R1's early position is twice R2's. The pass therefore rests on the
  *half weight*, not on a better signal.
- **The edge is a handful of episodes, not a property of the rule.** Three of 32 episodes
  produce 95% of R2's gain, and one of them (2001-10-31) is 43%. The bootstrap median Δ
  (+0.68 pp) is positive and 68.9% of paths beat C0, but the p25 is −0.28 pp: in a
  resampled world where those specific episodes do not recur, the arm gives back a little.
- **The premise holds in 2001, 2009 and 2023 and fails in 2020.** The early path's
  conditions (close > SMA50, close > close 20 sessions ago, calm vol) are satisfied
  *before* E2's vote in three of the four V-recoveries tested, by 7–24 sessions. In the
  fastest crash, E2's 2-of-3 vote is not the late signal — SMA50, Donchian-126 and low vol
  are.
- **Crash-conditioned re-entry (R4) is worthless here.** Restricting entries to
  post-drawdown windows removes 19 of 32 episodes and nets +0.15 pp (median −0.10 pp) while
  deepening max DD by 2.8 pp.
- **R3's two-fast-signal gate trades in-sample return for out-of-sample drawdown.** It is
  the best arm on ^GSPC 1957–1999 (+1.02 pp) and catches 2009-04-02, but it deepens M3 and
  M5 drawdowns by 3.4–3.7 pp and loses P1 and P3, so it fails G4 and G7.

## Amendments (post-pre-registration changes to `screen.py`)

The pre-registration (commit `64ec38e5`) fixed the arms, parameters, gates and selection
rule; none of them changed. The following changes were made to `screen.py` afterwards. The
first two were needed to make `--run` work at all (the committed file was the
`ewma_band_v1` harness copy with its band-selection tail still in place, which raised
`NameError` on `xstar`/`wb` at the end of the run) and to implement reporting the
pre-registration promised; items 3–5 are report-only additions. No gated metric, arm
parameter or gate changed, and the main-window and gate outputs before and after items 3–6
are identical.

1. Removed the `ewma_band_v1` leftovers: the band-selection tail of `main()` (`xstar`,
   `xrule`, `wb`), the unused `BANDS`/`GRID`/`NEIGHBOURS` constants, the EWMA vol helpers
   (`ewma_vol`, `ewma_vol_direct`, `LAMBDA`, `EWMA_N`, `EWMA_SEED`) and the harness's EWMA
   identity check — no arm in this study uses EWMA vol. `arm_spec` now returns the resize
   band alone. `--harness` still checks C0 against `e2_integration_v1` and the band
   simulator against `simulate_multi`.
2. Implemented the pre-registration's "per-episode P&L of the early positions"
   (`early_episodes`, `early_time_in_market`).
3. Episode span refined (report-only): it now ends at the reconciliation session rather
   than at the last session the early position is held, so the product of (1 + delta) over
   episodes reproduces the arm's total relative gain (residual ≤ 0.54 pp instead of up to
   10 pp). No gated metric uses it.
4. Added a report-only m = 4 cost stress to the main-window run. G1–G9 remain m = 1 / m = 2.
5. Cached `early_components` per price series (identical results; ~10× faster bootstrap)
   and silenced an all-NaN `nanmedian` warning in `_rolling_median_vol`.

## Caveats

- **Single history, in-sample selection.** All arms were designed on the same 2000–2026
  window and the bootstrap resamples it. R2's pass is 0.81 pp CAGR built from three
  episodes in 2001–2002 and 2023; nothing about the rule was tuned after the results were
  seen, but the rule itself was written knowing which recoveries are famous.
- **G7 is passed at the margin.** Five of six windows clear Δ CAGR ≥ −0.5 pp (M5 is
  −0.54 pp) and M5's Δ DD is +2.71 pp against a +3 pp allowance. R1 and R3 show that the
  same idea at full weight, or with Donchian instead of the vol filter, breaches it.
- **OOS data.** The out-of-sample windows are price-only, and many fills there are at the
  previous close (Yahoo's pre-1962 opens are placeholders), identically for every arm.
- **The pass is not a production proposal by itself.** Per the pre-registration, a pass
  makes a Stage B replay the next step; this campaign ran the screen only and the
  pre-registration did not fix a Stage B procedure.

## What a production change would require

None is proposed here.

- **R2 is expressible in the strategy layer** as a second, slower entry path: while the E2
  vote is off, a half-weight position on `close > SMA50` and `vol20 < its trailing
  252-session median` and `close > close[-20]`, dropped when `close < SMA50` or when the E2
  vote turns on. It needs SMA50, a 252-session rolling median of vol20 and a 20-session
  close lag — three inputs production does not currently compute — and therefore a
  strategy version bump with a golden backtest, unit tests against this screen's functions
  and a Stage B replay.
- **Half weight is load-bearing**, so the arm cannot be simplified to full weight: R1 fails
  G7.
- The pre-registered read is that a pass is a *candidate*, not a decision; the operator
  chooses whether a 0.81 pp in-sample, three-episode edge justifies the extra path.

## Decision

R2 passes the pre-registered gates and is the selected arm of this screen. Production E2
(`equity_leveraged_trend_v1` 1.1.0) is unchanged: no code, configuration or runtime was
touched. A Stage B replay is the next step if the operator wants the candidate taken
further.
