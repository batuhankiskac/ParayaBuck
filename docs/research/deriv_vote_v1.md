# Derivative trend votes, pre-tax and after Turkish tax, v1 (2026-10-15)

**Verdict: FAIL — no arm is selected; production R2 stays unchanged.** Idea I2 of
`docs/research/idea_survey_v3.md` (replace the *form* of the E2 vote with derivative votes)
does what the survey predicted on the signal side — vote flips fall from 5.9/yr to 1.1–3.8/yr
and turnover falls 17–46 % in C1–C3 — but it does **not** shrink the Turkish tax base, and it
loses return exactly where the survey's risk list said it would.

- **All four arms fail G3 (2017+), G7 (out-of-sample) and the 21-instrument gate I.**
- **C3 (mixed 2-of-4) is the closest miss and the only arm with a real after-tax win**: it
  beats R2 after tax in 2000 (+0.94 pp) and 2010 (+1.34 pp), passes B1-recent (+1.88 pp on
  72.5 % of paths) and B2 (+1.70 pp on 73.6 %), passes G1, G2, G4, G5, G6, G8, G9 — and then
  loses 2017 by −0.76 pp after tax, fails gate I's drawdown spread (42.9 % against 80 %) and
  is out of sample by −2.14 pp CAGR with drawdowns 21.9 pp deeper.
- **The pre-tax win is real but it is a 2000–2002 effect plus 2010–2016**, and the 2017+
  failure is one year: **2025**. Excluding 2025, the 2017+ CAGR of C1/C2/C3 is 30.7 / 29.1 /
  30.4 % against R2's 29.5 % — all three would pass G3; with 2025 they lose 1.6–2.8 pp,
  because the crossover exits the 2025 selloff on **2025-04-04** where E2 exits on
  **2025-03-06** (−24.15 % against −5.34 % over 2025-03-05 → 2025-05-01) and C2 re-enters only
  on 2025-06-24 where E2 re-enters on 2025-05-01.
- **The tax hypothesis is falsified in this architecture.** Trims are 90.8–105.7 % of the net
  realised TRY gain in every arm (91.7 % for R2); rarer vote flips buy more *time in market*
  (76.8 % → 78.9–81.4 %), which buys **more** vol-driven resizes (56.0 → 61.3–63.4/yr) and
  **more** sells/yr (29.6 → 32.2–33.3). The survey's caveat 3 ("the trims are driven by vol20,
  not by the vote") is the result.
- C4 (risk-adjusted hurdle 0.25) is a near-copy of R2 (it is a strict subset of E2's vote:
  `tsv > 0.25` implies `TSM252 > 0`, so it never turns on while E2 is off) and is 1.1 pp
  *worse*: a hurdle that scales with noise but not with the trend's own strength costs return
  and buys nothing.

Evidence: `research/deriv_vote_v1/` (`PREREGISTRATION.md`, `screen.py`, `tax_engine.py`,
`DATA_MANIFEST.json`, `RESULTS.json`, `SUMMARY.txt`, `diagnostics.py`, `diagnostics.json`).
The pre-registration, `screen.py`, `tax_engine.py`, `DATA_MANIFEST.json` and the input copies
were committed in **`50ffef92`**, before any candidate arm was simulated on real data; only the
synthetic smoke test and the control-only `--harness` mode ran before that commit. The outputs
are `RESULTS.json` / `SUMMARY.txt` from `screen.py --run`; two consecutive runs gave
byte-identical files (SHA-256 `82abde058361131cf8902e909b304d1e9cb716fe3300d54828673d4a469a682e`
and `f298dc917c210ab18c53ea36b3fb3142d73fc1b0fa193822abe0019f74b06c8d`). The run is offline
and takes about 9 minutes. `diagnostics.py` (report-only, 1 s) explains the gate outcomes and
changes no arm, gate or parameter.

## Question

Every trend vote in this repo's record is a *level* comparison (`close vs SMA n`, `n`-session
return above a fixed hurdle, `close vs` the 126-session max/min midpoint). I2 asks whether
votes on the *derivative* — a moving-average crossover, the OLS slope of log price, a rising
channel midpoint, a vol-scaled trend hurdle — are better, pre-tax **and** after tax: they flip
only when the trend turns, so they should trade less, consume fewer FIFO lots and realise a
smaller progressive tax base, at a cost in lag. This study tests the survey's four arms exactly,
against **C0 = production R2** (`equity_leveraged_trend_v1` 1.2.0: E2 plus the half-weight
early re-entry path), with the early path unchanged in every arm.

## Method

- **C0 = production R2 exactly.** E2 = ≥ 2 of 3 votes on the QQQ price close (`close > SMA200`;
  252-session price return > 0; `close >` the 126-session max/min midpoint), sized
  `vt = min(1, 0.35/vol20)` of the held instrument, total cap 0.98; while the vote is off, an
  early position of `0.5 × vt` if `close > SMA50` **and** `vol20 <` its trailing 252-session
  median **and** `close > close[-20]`, exited below SMA50 and ended when the vote turns on.
- **Arms (4; all parameters fixed in the pre-registration).** Vote components on the QQQ close:
  `cross` = `SMA50 > SMA200`; `slope252` = OLS slope of `log(close)` over the last 252 sessions
  > 0; `midrise126` = the 126-session midpoint above its value 21 sessions earlier; `tsv` =
  `252-session price return / (annualised sample vol of the 252 daily log returns) > 0.25`;
  `sma200`, `tsm252`, `don126` are production's.
  - **C1** `cross`, `tsm252`, `don126` — 2 of 3 (golden cross).
  - **C2** `cross`, `slope252`, `midrise126` — 2 of 3 (all derivative).
  - **C3** `sma200`, `cross`, `slope252`, `midrise126` — 2 of 4 (mixed quorum).
  - **C4** `sma200`, `tsv`, `don126` — 2 of 3 (risk-adjusted hurdle).
  - The **early path is R2's in every arm** (SMA 50, same vol-median window, same half weight,
    same exit), hung off the arm's own vote: it is active while the arm's vote is off and ends
    when it turns on. (The alternative reading — gating the early path on production E2's vote
    while the main position follows the arm — is recorded as an unrun alternative in the
    pre-registration.)
- **Mechanics.** Synthetic daily-reset 2× QLD\* on QQQ total return, 0.95 %/yr expense plus
  DTB3 (base) or DTB3 + 0.75 pp (calibrated); decide at the completed close, fill at the next
  open; cost 4 bps × m; 0.5 pp resize band; idle cash earns zero. Main window 2000-03-09 →
  2026-09-29 (6,679 sessions); bootstrap seed 20261011, block 20, 5,000 ten-year paths; six OOS
  windows; the 21-instrument set of `fast_reentry_v2` (EWC excluded by the quality rule).
- **After tax.** The corrected `vt_level_tax_v1` engine (vendored as `tax_engine.py`), $100k,
  base financing, m = 2, FIFO lots, Yİ-ÜFE indexation with the 10 % threshold, 2026 progressive
  brackets, liquidation at the window end; plus the clean B1-recent (progressive) and B2 (flat
  20 %, losses carried forward) block-20 bootstraps of 1,000 ten-year paths on the fixed
  2015-09 → 2026-09 calendar with its real FX. The only changes to the vendored engine are
  reporting-only: a down-trade to a zero target is booked as `exit` instead of `resize`, and the
  number of sell events is returned.
- **Gates.** `ewma_band_v1`'s G1–G9 against C0 (both financing models where applicable), gate I
  of `fast_reentry_v2` (21 instruments, each against its *own* E2 + early-path control: median
  Δ CAGR ≥ 0, share Δ > 0 ≥ 60 %, share Δ DD ≤ +3 pp ≥ 80 %, pooled bootstrap median ≥ 0), and
  after tax: candidate above C0 in **all three** $100k windows, and B1/B2 median Δ ≥ 0 with
  share > 0 ≥ 55 %. No exposure-neutral relaxation is used, as decided in the pre-registration.
- **Harness (all pass, `--harness`).** C0 reproduces `e2_integration_v1`'s production-E2
  deviation row exactly (0.000000 pp on CAGR and max DD, base/calibrated × m = 1/2); C0
  reproduces R2's main-window rows from `research/fast_reentry_v1/RESULTS.json` (10 checks, 0
  outside 0.01 pp); the component machinery's C0 vote equals production `e2_up` and C0's targets
  equal production's where the vote is on and `0.5 × vt` where it is off; the band simulator at
  0.5 pp equals `trend_robustness_v2.simulate_multi` bit for bit; the fast rolling median equals
  the v1 loop reference bit for bit; all seven vote components equal direct recomputations at 64
  sampled sessions (including `np.polyfit` for the slope); C0's after-tax anchors reproduce to
  ≤ 0.00004 pp (10.16 / 14.71 / 22.89 %) and the clean bootstrap medians to ≤ 0.00004 pp
  (B1 3.5155 %, B2 3.9678 %).

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD base (cal.) | Calmar | 2017+ | P1 / P2 / P3 | m = 1 | m = 4 | Sharpe | Turnover/yr | Time in mkt |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| **C0 (prod. R2)** | **15.48 %** | **14.90 %** | **46.76 % (46.88)** | **0.331** | **32.71 %** | −0.06 / 14.30 / 32.71 | 15.75 % | 14.92 % | 0.678 | **5.99** | 76.8 % |
| C1 golden cross | 15.80 % | 15.19 % | 41.16 % (41.36) | 0.384 | 30.88 % | +0.44 / 16.68 / 30.88 | 16.03 % | 15.34 % | 0.672 | 4.95 | 81.4 % |
| C2 all derivative | 15.14 % | 14.57 % | 37.79 % (38.14) | 0.401 | 29.13 % | −0.15 / 17.15 / 29.13 | 15.29 % | 14.84 % | 0.661 | 3.26 | 79.0 % |
| C3 mixed 2 of 4 | **17.01 %** | **16.41 %** | **34.76 % (34.88)** | **0.489** | 31.28 % | +0.31 / 20.53 / 31.28 | 17.19 % | 16.66 % | 0.713 | 3.73 | 80.8 % |
| C4 risk-adjusted hurdle | 14.34 % | 13.77 % | 46.76 % (46.88) | 0.307 | 31.04 % | −0.59 / 12.98 / 31.04 | 14.66 % | 13.71 % | 0.643 | 6.91 | 76.3 % |

Block-20 bootstrap (median arm − C0): C1 +1.271 pp base / +1.252 pp calibrated (66.2 % / 66.0 %
of paths ≥ 0), C2 +2.030 / +2.022 (70.4 / 70.5), C3 +2.357 / +2.326 (74.4 / 74.2),
C4 −0.130 / −0.126 (45.6 / 45.7). C0's own medians are 7.89 % base and 7.43 % calibrated.

C3's in-sample profile (higher return *and* 12 pp less drawdown, Calmar 0.489 against 0.331) is
a 2000–2002 effect: over 2000-03-08 → 2002-12-31 C0 loses 38.28 % while C1/C2/C3 lose 20.39 %
and C4 38.28 % (`diagnostics.json`). The derivative votes leave the bear market on 2000-09-22 and
do not return until 2003-01-24 / 2003-04-10 / 2003-03-13, while E2 goes on and off 19 times in
the same window and holds through most of the crash. The same shape shows in P2 (C3 20.53 %
against 14.30 %, from 2010 +20.33 % against +7.21 %, 2012 +28.20 % against +6.37 %, 2015 +1.10 %
against −11.82 %).

Cost stress (report-only): at m = 4 the ranking is unchanged (C3 16.66 %, C1 15.34 %, C2 14.84 %,
C0 14.92 %, C4 13.71 %), and every arm's max DD at m = 4 is within 0.7 pp of its m = 2 value (C3 +0.08 pp, C2 +0.36, C1 +0.52, C0/C4 +0.68).

### Out of sample (calibrated, m = 2)

| Window | C0 | C1 | C2 | C3 | C4 |
|---|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.89 % / 39.63 % | 9.13 % / 53.67 % | 6.60 % / 51.77 % | 7.80 % / 52.11 % | 9.99 % / 39.63 % |
| M1 ^IXIC 1975–1999 | 21.18 % / 40.97 % | 17.38 % / 49.68 % | 13.76 % / 64.44 % | 15.91 % / 62.90 % | 21.05 % / 40.97 % |
| M2 ^GSPC 1935–1956 | 11.70 % / 58.33 % | 11.03 % / 62.54 % | 11.60 % / 55.69 % | 11.22 % / 63.33 % | 11.33 % / 61.37 % |
| M3 ^N225 1966–2026 | 6.40 % / 65.03 % | 5.60 % / 81.13 % | 3.70 % / 86.67 % | 4.22 % / 85.97 % | 6.41 % / 67.11 % |
| M4 ^GDAXI 1989–2026 | 9.47 % / 58.10 % | 6.36 % / 67.17 % | 6.95 % / 63.72 % | 7.07 % / 59.00 % | 9.35 % / 56.01 % |
| M5 ^FTSE 1985–2026 | −0.59 % / 85.45 % | 1.43 % / 64.33 % | 1.15 % / 75.59 % | 1.38 % / 71.13 % | −0.60 % / 85.55 % |
| Median Δ CAGR | — | −0.785 pp | −2.613 pp | −2.140 pp | −0.070 pp |
| Δ CAGR ≥ −0.5 pp | — | 1/6 | 2/6 | 2/6 | 6/6 |
| Δ DD ≤ +3 pp | — | 1/6 | 2/6 | 2/6 | 5/6 |
| Max Δ DD | — | +16.11 pp | +23.47 pp | +21.92 pp | +3.03 pp |
| Pooled bootstrap median Δ | — | −0.055 pp | −0.181 pp | +0.178 pp | −0.048 pp |

Out of sample the derivative votes are a different strategy: they win in the two long, slow
histories where a level vote whipsaws at the bottom (M5 ^FTSE +1.96 pp with −14.3 pp of
drawdown, and 2008 in-sample) and they lose badly where a slow exit is expensive (^GSPC
1957–1999, ^IXIC 1975–1999, ^N225). C4 is the exception in the other direction: it is
indistinguishable from C0 out of sample (median −0.070 pp, all six windows inside −0.5 pp of
CAGR) exactly because its vote is a strict subset of E2's.

### Gate I — 21 independent instruments (calibrated, m = 2, each vs its own control)

| Arm | Median Δ CAGR | Share Δ > 0 | Share Δ DD ≤ +3 pp | Pooled bootstrap median Δ | Bootstrap CAGR median |
|---|---:|---:|---:|---:|---:|
| C1 | +0.897 pp | 71.4 % | 66.7 % | +0.767 pp | 3.11 % |
| C2 | +0.756 pp | 57.1 % | 57.1 % | +1.380 pp | 3.73 % |
| C3 | +0.269 pp | 52.4 % | 42.9 % | +1.438 pp | 3.81 % |
| C4 | −0.162 pp | 38.1 % | 95.2 % | −0.090 pp | 2.22 % |

(C0's own instrument bootstrap median is 2.29 %; 42,000 paths per arm; the required bars are
median ≥ 0, share > 0 ≥ 60 %, share Δ DD ≤ +3 pp ≥ 80 %, pooled median ≥ 0.) The return side is
positive for C1–C3, but the drawdown spread is not: the derivative votes deepen drawdowns on 6–12
of 21 instruments (C2 +26.1 pp on EWS, C3 +21.7 pp on XLY, +18.5 pp on EEM). C4's drawdowns are
fine (95.2 %) and its returns are not.

### After tax ($100k, base financing, m = 2, progressive 2026 tariff, liquidated)

| Window | Arm | Pre-tax CAGR | After-tax CAGR | Δ vs C0 | Max DD | Tax paid (USD) | Sells/yr |
|---|---|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | C0 | 15.47 % | **10.16 %** | — | 46.76 % | 766,160 | 30.4 |
| | C1 | 15.79 % | 10.10 % | −0.06 pp | 49.68 % | 758,771 | 33.9 |
| | C2 | 15.14 % | 9.69 % | −0.47 pp | 53.12 % | 658,013 | 33.0 |
| | C3 | 17.01 % | **11.10 %** | **+0.94 pp** | 49.15 % | 954,383 | 34.2 |
| | C4 | 14.34 % | 9.25 % | −0.91 pp | 46.76 % | 605,666 | 30.6 |
| 2010-01 → end | C0 | 22.16 % | **14.71 %** | — | 43.78 % | 553,536 | 30.9 |
| | C1 | 22.44 % | 14.57 % | −0.13 pp | 48.98 % | 540,965 | 34.9 |
| | C2 | 22.49 % | 14.72 % | +0.02 pp | 52.76 % | 536,190 | 33.1 |
| | C3 | 24.27 % | **16.05 %** | **+1.34 pp** | 48.52 % | 667,637 | 35.1 |
| | C4 | 20.59 % | 13.44 % | −1.27 pp | 45.32 % | 451,331 | 30.8 |
| 2017-01 → end | C0 | 32.53 % | **22.89 %** | — | 36.24 % | 360,297 | 35.2 |
| | C1 | 30.70 % | 21.73 % | −1.16 pp | 41.37 % | 310,344 | 39.6 |
| | C2 | 28.95 % | 20.66 % | −2.22 pp | 39.36 % | 265,393 | 39.9 |
| | C3 | 31.10 % | 22.13 % | −0.76 pp | 39.62 % | 313,610 | 40.9 |
| | C4 | 30.86 % | 21.62 % | −1.27 pp | 36.16 % | 317,530 | 35.5 |

Clean after-tax bootstrap (1,000 block-20 paths, fixed 2015-09 → 2026-09 calendar with real FX):

| Arm | B1-recent median | Δ vs C0 | share > 0 | B2 flat median | Δ vs C0 | share > 0 |
|---|---:|---:|---:|---:|---:|---:|
| C0 | 3.52 % | — | — | 3.97 % | — | — |
| C1 | 4.66 % | +0.957 pp | 65.6 % | 5.33 % | +0.860 pp | 65.7 % |
| C2 | 5.51 % | +1.756 pp | 69.5 % | 5.92 % | +1.560 pp | 69.5 % |
| C3 | 5.54 % | +1.878 pp | 72.5 % | 6.02 % | +1.701 pp | 73.6 % |
| C4 | 3.28 % | −0.132 pp | 45.3 % | 3.76 % | −0.108 pp | 44.9 % |

C3's after-tax numbers are the largest this repo has measured on a signal-form arm
(`gain_band_v2`'s A1 was +0.46/+0.44/+0.85 pp with a G5 failure). They still do not qualify: the
pre-registered rule needs all three windows, and 2017 is −0.76 pp.

### Trading activity, vote flips and where the tax base comes from

| Arm | Vote share | Vote flips/yr (2017+) | Round trips/yr | Resizes/yr | Sells/yr (2017+) | Turnover/yr | Resizes from | Trims TRY | Exits TRY | Trim share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 74.3 % | 5.87 (2.47) | 2.97 | 55.95 | 29.56 (34.3) | 5.99 | vol20 | 54.83 m | −3.23 m | 91.7 % |
| C1 | 77.7 % | 3.77 (1.85) | 2.30 | 63.18 | 33.06 (38.5) | 4.95 | vol20 | 54.43 m | −11.06 m | 105.7 % |
| C2 | 71.1 % | 1.13 (0.82) | 1.36 | 61.30 | 32.15 (39.0) | 3.26 | vol20 | 40.20 m | −2.92 m | 90.8 % |
| C3 | 74.9 % | 1.73 (1.03) | 1.58 | 63.44 | 33.32 (40.0) | 3.73 | vol20 | 60.89 m | −4.75 m | 91.9 % |
| C4 | 73.4 % | 7.30 (3.90) | 3.35 | 55.72 | 29.82 (34.6) | 6.91 | vol20 | 43.61 m | −2.51 m | 91.6 % |

"Vote flips/yr" counts sessions where the arm's vote differs from the previous session; trims and
exits are the realised TRY gains booked by down-trades on the 2000 window (`trim share` = trims
over the sum of all reasons, including the final liquidation and tax-funding sales, so a large
net-loss exit book can push it above 100 %).

This table is the study's central measurement. The intended causal chain (rarer flips → fewer
resizes → fewer FIFO lots consumed → smaller tax base) breaks at the second step: C2 flips 5.2×
less often than R2 and turns over 45.6 % less, but its sells/yr are 8.8 % *higher* and the tax base
is the same kind of object, because the derivative vote is off more often at *partial* weight
(the early path fills the gap), so the rebalance gap keeps crossing the 0.5 pp band in both
directions while vol20 moves. Across the five arms the resizes/yr is 55.7–63.4, i.e. almost
independent of the vote: **the tax base is the vol target, not the vote** — the same conclusion
`vt_level_tax_v1` reached from the other direction.

### The early path

| Arm | Early share of sessions | Episodes | Sum of episode Δ | Best episode | Worst episode |
|---|---:|---:|---:|---|---|
| C0 (R2, from `fast_reentry_v1`) | 2.46 % | 32 | +19.72 pp | 2001-10-31 +8.58 pp | 2022-12-13 −3.10 pp |
| C1 | 3.70 % | 26 | +19.60 pp | 2001-10-31 +9.23 pp | 2009-04-21 −1.06 pp |
| C2 | 7.88 % | 32 | −2.28 pp | 2001-10-31 +9.23 pp | 2019-02-04 −10.49 pp |
| C3 | 5.85 % | 24 | +18.02 pp | 2001-10-31 +9.23 pp | 2019-02-04 −8.00 pp |
| C4 | 2.95 % | 43 | −7.27 pp | 2006-09-01 +4.18 pp | 2019-03-07 −3.29 pp |

The early path is where C1 and C3 earn their in-sample edge (+19.6 pp and +18.0 pp over 24–26
episodes, two of them 2001-10-31 and 2023-01-20 — the same two that carry R2's edge), and where
C2 and C4 lose (a −10.49 pp and a −8.00 pp episode on 2019-02-04, and −7.27 pp net for C4).
Leave-one-episode-out (base, m = 2): flattening 2001-10-31 leaves C1 at 15.39 %, C2 at 14.74 %,
C3 at 16.60 % and C4 at 14.16 % against R2's 15.48 %.

## Gates

| Gate | C1 | C2 | C3 | C4 |
|---|---|---|---|---|
| G1 return (base / cal.) | pass / pass | pass / pass | pass / pass | **fail / fail** |
| G2 DD ≤ C0 + 1 pp | pass / pass | pass / pass | pass / pass | pass / pass |
| G3 2017+ ≥ C0 − 1 pp | **fail** (−1.83) | **fail** (−3.58) | **fail** (−1.44) | **fail** (−1.68) |
| G4 sub-periods | pass (P1, P2) | **fail** (P2 only) | pass (P1, P2) | **fail** (0/3) |
| G5 bootstrap Δ ≥ 0 | pass (+1.27 / +1.25) | pass (+2.03 / +2.02) | pass (+2.36 / +2.33) | **fail** (−0.13 / −0.13) |
| G6 G1 at m = 1 | pass / pass | pass / pass | pass / pass | **fail / fail** |
| G7 six OOS windows | **fail** (1/6, max Δ DD +16.1) | **fail** (2/6, max +23.5) | **fail** (2/6, max +21.9) | **fail** (median −0.070 pp) |
| G8 pooled OOS bootstrap | **fail** (−0.055) | **fail** (−0.181) | pass (+0.178) | **fail** (−0.048) |
| G9 turnover ≤ 2 × C0 | pass (4.95) | pass (3.26) | pass (3.73) | pass (6.91) |
| G_I 21 instruments | **fail** (DD 66.7 %) | **fail** (57.1 %) | **fail** (42.9 %) | **fail** (share > 0 38.1 %) |
| T-2000 / T-2010 / T-2017 | **fail** / **fail** / **fail** | **fail** / pass / **fail** | pass / pass / **fail** | **fail** / **fail** / **fail** |
| B1-recent | pass (+0.96, 65.6 %) | pass (+1.76, 69.5 %) | pass (+1.88, 72.5 %) | **fail** (−0.13, 45.3 %) |
| B2 flat 20 % | pass (+0.86, 65.7 %) | pass (+1.56, 69.5 %) | pass (+1.70, 73.6 %) | **fail** (−0.11, 44.9 %) |
| **Result** | **fail** | **fail** | **fail** | **fail** |

Selected: **none** (`eligible = []`). Production R2 remains the production rule.

Audits: all five arms pass the look-ahead truncation probe (64 sessions), the trailing-window
recomputation, the fill-timing probe, the cost-linearity probe (drag ratio 1.86–1.93) and the
state-machine invariants (the early path is never active while the arm's vote is on; targets
inside [0, 0.98]).

## Why it fails, in one year and one mechanism

- **2017+ is 2025.** Excluding 2025's return (year count kept), the 2017+ CAGR is 29.54 % (C0),
  30.69 % (C1), 29.06 % (C2), 30.43 % (C3), 27.90 % (C4) — C1, C2 and C3 would all clear G3's
  −1 pp bar. With 2025 they lose 1.4–3.6 pp: 2025 returns are +26.65 % (C0), +1.47 % (C1),
  +0.52 % (C2), +6.53 % (C3), +26.65 % (C4).
- **The 2025 loss is the late exit and the late re-entry.** Over 2025-03-05 → 2025-05-01 the
  arms return −5.34 % (C0, C4), −24.15 % (C1), −16.13 % (C2, C3); the mean target weight over
  those 41 sessions is 0.03 for C0/C4 against 0.39 (C1) and 0.42 (C2, C3). The vote timelines
  (`diagnostics.json`) show why: E2's level votes turn the position off on 2025-03-06 (it is
  flat on 95 % of those sessions), the crossover-based arms stay long until 2025-04-04 (C1,
  which then whipsaws through 04-09/04-14) or 2025-04-14 (C2, C3), and C2 does not re-enter
  until 2025-06-24 against E2's 2025-05-01 — missing +14.9 pp of the rebound
  (+28.04 % after re-entry against +42.93 % for C0).
- **In-sample, the same slowness is what pays.** The derivative votes exit the 2000–2002 bear on
  2000-09-22 and stay out for two and a half years, while E2 goes on and off 30 times inside it; that is
  the whole of C3's +1.5 pp CAGR and −12 pp of max DD. It is not a property of derivative votes,
  it is a bet on 2000–2002 that the six out-of-sample windows do not reproduce (median Δ CAGR
  −0.07 to −2.61 pp, drawdowns up to +23 pp deeper).
- **The tax channel is closed by measurement, not by assumption.** Rarer flips ⇒ more time in
  market ⇒ more resizes ⇒ *more* resulting sells; and trims remain 91–106 % of the realised TRY
  gain. C3 wins two of three after-tax windows anyway (+0.94, +1.34 pp) — the after-tax gain is
  the pre-tax gain in the two windows where C3 also wins pre-tax (17.01 % against 15.47 %, 24.27 %
  against 22.16 %), not a tax artefact. The 2017 window shows the pre-tax loss (−1.43 pp) plus a
  slightly smaller tax bill (−46.7 k USD) adding up to −0.76 pp.
- **C4 is the null result inside the study.** Its vote is a strict subset of E2's (0 sessions
  "arm on, E2 off"; 64 "arm off, E2 on"), so it is R2 plus a filter: 0.99 agreement, 7.30 flips/yr
  against 5.87 (the vol-scaled hurdle is *noisier* than the raw return sign), G1/G5/G6 fail, and
  nothing improves. A trend hurdle scaled by noise, but not by the trend's own magnitude, is a
  drag here — the opposite of the Barroso–Santa-Clara mechanism the survey cited.

## Findings

- **I2 is closed: derivative-vote forms do not beat R2 pre-tax or after tax.** All four arms fail
  the pre-registered rule; C3 is the best arm and still fails G3, G7 and gate I. C1 and C3 pass
  G1, G2, G4, G5, G6 and G9, C2 fails G4 as well, and C4 fails G1, G5 and G6.
- **The signal-side prediction was right and irrelevant to tax.** In C1–C3 flips fall 1.6–5.2×
  and turnover 17–46 % (C4 flips 24 % *more* and turns over 15 % more), but the taxed object is `Σ|Δw|` against a vol target, and the same vote that
  trades less also spends more sessions at a partial weight, so sells/yr rise 8.8–12.7 % in C1–C3
  (C4 +0.9 %) and the
  realised TRY gains are unchanged in kind (still ~91 % trims).
- **The in-sample edge is a 2000–2002 bear-market bet.** C3's +1.53 pp CAGR and −12 pp max DD
  come from leaving that bear on 2000-09-22 and staying out until 2003; they do not survive on any
  other history (gate I median +0.27 pp with 43 % of instruments inside +3 pp of drawdown).
- **Lag costs are concentrated and large.** One episode (2025-03-06 → 2025-06-24) decides G3 for
  all three derivative arms. The E2 vote's Donchian/midpoint component is what exits the 2025
  selloff six weeks before the crossover; removing it (C2) is the worst arm in the 2017+ window.
- **Every arm keeps the untouched parts of production**: cap, vol target, band, early path, and
  the early path's own contribution (+18…+19.6 pp for C1/C3 over 24–26 episodes).

## Caveats

- **Single history, in-sample selection.** The arms come from a survey written with 2000–2026 in
  view; the four forms were fixed before the run, but the record shows how much of a
  derivative-vote edge would come from 2000–2002 and 2025, i.e. from two episodes.
- **Diagnostics are post-hoc.** `diagnostics.py`/`diagnostics.json` (year-by-year returns,
  sub-period returns, the 2017-excluding-2025 CAGR, vote timelines, disagreement counts,
  exposure over the 2025 exit window) were written **after** the pre-registered run. They change
  no arm, gate or parameter, and the gate outcomes do not depend on them.
- **Synthetic instrument.** Signals run on the QQQ price close and the position on the synthetic
  daily-reset 2× QLD\* leg, as in every study on this harness; `instrument_cost_v1` showed that
  real QLD bars move levels by a few tenths of a pp.
- **After-tax model.** Same assumptions and open items as `after_tax_tr_v1` / `tax_asym_v1–v3`:
  Yİ-ÜFE indexation from the acquisition month to the sale month, the assessment year anchored at
  the previous October, the 2026 tariff, the 22,000 TRY dividend threshold, liquidation at the end
  of the window, one CBRT buying-rate series. The B1/B2 bootstraps keep the calendar (and so FX and
  inflation) real on 2015–2026, so they cannot explore other FX or inflation regimes; deltas below
  ~0.1 pp are inside modelling uncertainty.
- **Early-path gating interpretation.** The early path hangs off each arm's own vote. The
  alternative (gating it on production E2's vote) is documented in the pre-registration and was
  not run; it would have changed every arm's exposure, including C0's neighbours' — it is the one
  structural choice this study does not measure.
- **No two-tier relaxation was used.** The survey's §6 caveat 1 offers an exposure-neutral bar for
  arms whose effect is measured in realizations; C1–C4 change exposure, so the full G1–G9 bar
  applied. `gain_band_v2`'s A1 remains the only arm on record that wins after tax in all three
  windows and dies only on G5 — C3 here is stronger after tax in two windows and weaker pre-tax.

## Amendments

No change was made to `screen.py` after the pre-registration commit `50ffef92`: the file that
produced `RESULTS.json` is byte-identical to the committed one (SHA-256
`89366621464cfdd7530e399b85a76bc3ce2e434f48fd3002be8db7cf5bb09c65`), as is `tax_engine.py`
(`7a5d54aa0eca110d94c367113ac13c909407d34af54567703714ab7060f62776`). The arms, parameters,
gates, tolerances and selection rule are those of the pre-registration; no gate was changed after
seeing data.

Report-only additions, all after the run and none touching a gated metric:

1. `diagnostics.py` / `diagnostics.json` (year-by-year returns, sub-period returns, the
   2017-excluding-2025 CAGR, vote timelines, vote disagreement vs C0, per-year in-market share,
   exposure over the 2025 exit window) — written to explain the gate outcomes.
2. In `--run`'s `SUMMARY.txt`: the vote-flip table, the gain-by-reason table (trims vs exits vs
   tax-funding sales vs liquidation), the per-arm audit block and the after-tax gate detail rows.
3. Before the pre-registration commit (i.e. part of the pre-registered code): the reporting-only
   `exit`/`resize` reason split and the `strategy_sells` counter in `tax_engine.py`, and the
   `arms=` parameter of `after_tax_bootstrap` used by `--harness` to anchor C0 alone.
