# Gain-scaled no-trade band on R2's downward resizes (gain_band_v1, 2026-10-14)

> Evidence for this report (`research/gain_band_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/gain-band-v1-2026-10`.

> **Orchestrator note (2026-10-15):** this study's control C0, labelled "production R2", reproduces
> E2's numbers (14.67% pre-tax, 9.53% after tax), not R2's (15.48%, 10.16%); every Δ below is
> measured against E2. A corrected re-run `gain_band_v2` was pre-registered (branch
> `research/gain_band_v2`) and paused before running.

> Evidence for this report (`research/gain_band_v1/` code, inputs, results) is not on `main`; it
> lives on branch `research/gain_band_v1` in this worktree.

**Verdict: FAIL as pre-registered — every arm misses on one gate key, gate G5 (the pre-tax clean
bootstrap), by 4 bp to 42 bp of median CAGR.** The three arms pass the other 17 gate keys,
including the whole after-tax stage by wide margins (+0.65 to +2.01 pp on the three windows at
$100k, B1-recent median +0.23 to +0.28 pp on 73–76 % of paths, B2 flat +0.07 to +0.09 pp on
58–62 %), the six out-of-sample windows (median +0.16 pp), the pooled OOS bootstrap (+0.42 pp)
and the 21-ETF drawdown test (17/21 within +3 pp). Nothing is selected; production R2 is
unchanged.

The failure is informative rather than a knife-edge: the pre-tax bootstrap distribution of
A1 − R2 is **symmetric noise centred on zero** (mean +0.014 pp, median −0.004 pp, 49.5 % of
2,000 sampled paths positive, p05/p95 −0.46/+0.57 pp), so there is no "lucky cell" to blame —
the arm's pre-tax edge is not there to be resampled. The gain on the realised history is real
(+1.01 pp CAGR, −2.1 pp max DD, −64 % sells) but it is a **pre-tax exposure effect**, not the
tax-timing effect the idea was built on: at $100k on the 2000 window the tax drag actually falls
only 5.14 → 5.07 pp, i.e. 7 bp of the 108 bp after-tax gain is tax. Where the tax channel does
show is the recent-regime bootstrap (B1: +0.23 pp of which ~0.00 pp pre-tax), which is why the
after-tax stage passes so comfortably while the pre-tax one does not.

## Question

R2 resizes to its vol target on the first close that breaches a constant 0.5 pp band, and
~91 % of its realised Turkish taxable gain comes from those downward resizes under FIFO lot
matching. Does replacing that constant band, **on downward resizes only**, with a threshold that
grows with (a) the **embedded indexed taxable TRY gain of the lots the sale would consume** and
(b) the **year's accumulated realised TRY gain relative to the year's deflated bracket ceiling**
improve the after-Turkish-tax CAGR at $100k versus R2, without a materially worse drawdown?
(I3 of `research/idea_survey_v2`, §4.)

## Method

- **Control (C0) = production R2** (`equity_leveraged_trend_v1` 1.2.0): 2 of 3 votes on the QQQ
  price close (SMA200, 252-session price return > 0, 126-session channel midpoint), sized
  `vt = min(1, 0.35/vol20)` on synthetic QLD\*, cap 0.98, and the half-weight early re-entry leg
  while the vote is off (close > SMA50, vol20 < its 252-session median, close > close 20 sessions
  ago; exit below SMA50). Decide at the completed close, fill at the next open, 0.5 pp band, idle
  cash earns zero, cost 4 bps x m x |Δw|; synthetic QLD\* = daily-reset 2x on QQQ total return,
  0.95 %/yr, DTB3 + {base 0.0 %, calibrated 0.75 %}/yr.
- **Arms.** All four share R2's target series bit for bit; only the *fill decision* differs, and
  only a **downward resize** (`tgt > 0`, `tgt < held weight`) can be suppressed. Entries, exits
  and up-resizes keep the production 0.5 pp band.
  - **A1** — gain-scaled band: a downward resize fills iff `|tgt − w| > B_down`, where
    `B_down = 0.5 pp` if the sale's indexed taxable gain is ≤ 1 % of its proceeds, 3 pp if ≤ 5 %,
    8 pp above. The gain is the FIFO-front lots' net TRY gain with the engine's own indexation
    arithmetic (10 % Yİ-ÜFE floor, month-to-month, CBRT buying rate of the trade date).
  - **A2** — A1 plus a bracket budget: while the year's realised rule gain ≥ the year's deflated
    27 %-bracket ceiling (`1,000,000 x scale(year)` TRY), a discretionary downward resize is
    postponed (re-evaluated at every later close, so it fills in the next tax year at the latest);
    hard override at `|tgt − w| > 15 pp`, on the decision close where the E2 vote turns off, or on
    a target ≤ 0.
  - **A3** — A2 with the override at 10 pp (the plateau neighbour of A2's tail protection).
- **One engine.** `research/gain_band_v1/tax_engine.py` is a byte copy of
  `research/tax_harvest_v2/tax_engine.py` (= the corrected `vt_level_tax_v1` engine), driven by a
  single session loop (`screen.py:engine_run`) for every stage: pre-tax main window, after-tax
  windows, the six OOS windows, the 21 ETFs and both bootstraps. The rule's decision state is
  computed independently of the tax regime, so an arm's pre-tax and after-tax trade paths coincide.
- **Rule instantiations.** R-INDEX (real USD/TRY + Yİ-ÜFE) where a TRY calendar exists (main
  window, after-tax windows, both bootstraps); **R-USD** (fx = 1, no indexation, inert budget) for
  the six OOS windows and the 21 ETFs, which have no TRY calendar — so A2 ≡ A3 ≡ A1 there, by
  pre-registration. The pre-tax main window is also reported under R-USD.
- **Account scale.** The bracket budget is a nominal TRY threshold, so the pre-tax runs of the main
  window and of bootstrap E1 use the $100k gate capital (the pre-tax *return* statistics are
  scale-free); the OOS/instrument stages run at unit NAV.
- **After tax.** Per-calendar-year netting in TRY, trade-date years, Yİ-ÜFE indexation with the
  10 % floor, the 2026 tariff deflated by the previous October's Yİ-ÜFE, FIFO, instalments
  Mar 31/Jul 31 funded by selling, liquidated at the window end (bases financing, m = 2).
- **Bootstrap E1** (G5, B1, B2): block-20 stationary bootstrap, seed 20261011, fixed
  2015-09-01 → 2026-09-30 calendar with its realised USD/TRY and Yİ-ÜFE, QQQ market returns
  resampled; 5,000 ten-year paths pre-tax (base and calibrated) and 1,000 after-tax (progressive
  and flat) at $100k and $1M. **E2** (G8): pooled block-20 bootstrap over M1–M5, 2,000 paths each.
- **Harness (PASS, 0 checks outside tolerance).** C0/R2 pre-tax CAGR, max DD and turnover
  reproduce `fast_reentry_v1`/`e2_integration_v1` at m = 1/2 x base/calibrated (30 checks); the
  after-tax anchors at $100k reproduce `vt_level_tax_v1` within 0.00005 pp on all three windows;
  the control's bootstrap medians reproduce `tax_harvest_v2` exactly (B1 3.5155 %, B2 3.9678 %; the
  pure-E2 label 3.079 %/3.5201 %); the arm "C0" NAV path equals `simulate_band` to 1.3e-13; and
  `--smoke` (synthetic only) proves the rule-gain probe equals the engine's own sell gain, that
  the split state/sizing helpers equal the reference `arm_signals`, and that the budget branch is
  reachable (20 postponements at a $1e9 account scale).
- **Gates.** `ewma_band_v1`'s G1–G9 (G7 = ^GSPC 1957–99 + M1–M5, G8 = pooled M1–M5 bootstrap);
  **AT** = after-tax CAGR > R2 in all three windows at $100k; **B1/B2** = median Δ ≥ 0 and share
  of paths > 0 ≥ 55 %; **F3** = Δ max DD ≤ +3 pp on ≥ 80 % of the 21 ETFs; **P** = the selected
  arm's neighbours each pass AT. Selection = highest after-tax CAGR at $100k on the 2000 window
  among arms passing everything.

## Pre-tax main window (2000-03-09 → 2026-09-29, 6,679 sessions, m = 2)

| Arm | CAGR base | CAGR cal. | max DD base | Calmar | 2017+ | P1 / P2 / P3 | m = 1 base | turn/yr | sells/yr | blocked band / budget |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| **C0 (R2)** | **14.67 %** | **14.10 %** | **49.56 %** | 0.296 | 32.44 % | −1.10 / 13.26 / 32.44 | 14.95 % | 5.97 | 27.4 | 0 / 0 |
| **A1** | **15.68 %** | 15.06 % | 47.46 % | 0.330 | 33.18 % | +0.07 / 14.31 / 33.18 | 15.94 % | 5.48 | 10.4 | 1,293 / 0 |
| **A2** | **15.90 %** | 15.29 % | 47.46 % | 0.335 | 33.91 % | +0.07 / 14.27 / 33.91 | 16.20 % | 5.34 | 8.6 | 1,253 / 190 |
| **A3** | **15.74 %** | 15.13 % | 47.46 % | 0.332 | 33.31 % | +0.07 / 14.36 / 33.31 | 15.99 % | 5.41 | 9.5 | 1,306 / 88 |

Calibrated max DD: C0 49.65 %, A1/A2/A3 47.58 %. m = 4 cost stress (base): C0 14.12 %,
A1 15.17 %, A2 15.17 %, A3 15.17 %. Turnover barely moves (5.97 → 5.34–5.48/yr) because entries and exits
dominate it; what collapses is the number of *sells*
(728 → 224–277 over 26.5 years, i.e. 27.4 → 8.6–10.4 per year), all of it downward resizes.
Under the R-USD rule (fx = 1, no indexation) the same arms gain +1.05 / +1.09 / +1.05 pp, so the
edge is not a property of the TRY indexation arithmetic.

A1's band postpones resizes in **all 27 calendar years** (top years 2017 192, 2020 113, 2003 111,
2011 75, 2013 77, 2023 70, 2024 65), not in one episode. A2's budget adds 190 (A3: 88)
postponements on top; the band is the binding constraint — the budget only sees resizes small
enough to pass the band, i.e. sales with a ≤ 1 % embedded gain.

## After tax (progressive 2026 tariff, liquidated, base financing, m = 2)

At **$100k** — the gate capital:

| Window | Arm | Pre-tax | After-tax | Δ vs R2 | unliquid. | max DD | tax paid USD | terminal USD | sells/yr |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | R2 | 14.67 % | **9.53 %** | — | 10.15 % | 49.56 % | 646,780 | 1,123,105 | 28.5 |
| | A1 | 15.68 % | **10.62 %** | **+1.083** | 11.29 % | 47.46 % | 816,720 | 1,458,459 | 10.9 |
| | A2 | 15.68 % | **10.95 %** | **+1.417** | 11.62 % | 47.46 % | 844,247 | 1,579,937 | 8.9 |
| | A3 | 15.68 % | **10.63 %** | **+1.096** | 11.31 % | 47.46 % | 814,609 | 1,462,972 | 9.6 |
| 2010-01 → end | R2 | 21.89 % | **14.50 %** | — | 15.51 % | 44.69 % | 534,273 | — | 30.2 |
| | A1 | 22.34 % | **15.14 %** | **+0.645** | 16.23 % | 44.21 % | 564,644 | — | 10.7 |
| | A2 | 22.34 % | **15.64 %** | **+1.142** | 16.73 % | 44.36 % | 580,924 | — | 7.8 |
| | A3 | 22.34 % | **15.16 %** | **+0.664** | 16.27 % | 43.95 % | 562,856 | — | 8.8 |
| 2017-01 → end | R2 | 32.26 % | **22.69 %** | — | 24.53 % | 37.61 % | 352,698 | — | 33.7 |
| | A1 | 32.97 % | **23.74 %** | **+1.045** | 25.73 % | 35.88 % | 370,681 | — | 10.0 |
| | A2 | 32.97 % | **24.70 %** | **+2.007** | 26.69 % | 35.91 % | 384,707 | — | 6.1 |
| | A3 | 32.97 % | **23.78 %** | **+1.086** | 25.80 % | 36.29 % | 369,082 | — | 8.3 |

- **Scale.** At $10k the same cells are +2.52/+2.62/+2.44 (2000), +2.88/+3.06/+2.89 (2010) and
  +4.01/+4.37/+4.04 (2017) — the deflated brackets bind harder at a smaller account, so the
  deferral is worth more. At $1M A1 falls to +0.18/−0.41/−0.07 pp, A3 to +0.30/−0.33/+0.07, and
  only A2 (whose budget is more aggressive: 291/228/166 postponements) stays positive
  (+0.65/+0.09/+1.01). The arm is capital-dependent by construction (the Turkish brackets are
  fixed TRY amounts).
- **Cost.** At doubled cost (m = 4, $100k) every sign and roughly every magnitude survives:
  A1 +1.11/+0.67/+1.07 pp, A2 +1.45/+1.21/+2.00, A3 +1.13/+0.72/+1.12.
- **Tax identity.** A1 pays **more** nominal tax than R2 (816,720 vs 646,780 USD over 2000–2026)
  and still ends 30 % wealthier (1,458,459 vs 1,123,105 USD); its realised resize gain is *larger*
  in TRY (56.2m vs 43.9m) because each surviving trim sells older, more appreciated lots, and its
  indexation relief is larger too (21.9m vs 15.9m TRY). The drag falls only 5.14 → 5.07 pp, i.e.
  the after-tax gain is the pre-tax gain, not a tax saving.

## Out of sample and across instruments (pre-tax, calibrated, m = 2, R-USD)

| Window | C0 CAGR / max DD | Δ CAGR | Δ max DD |
|---|---:|---:|---:|
| ^GSPC 1957–1999 | 9.98 % / 38.99 % | −0.01 pp | +0.66 pp |
| M1 ^IXIC 1975–1999 | 20.68 % / 38.62 % | +0.55 pp | +2.60 pp |
| M2 ^GSPC 1935–1956 | 11.26 % / 62.52 % | +0.42 pp | −4.41 pp |
| M3 ^N225 1966–2026 | 6.33 % / 67.16 % | +0.11 pp | −1.52 pp |
| M4 ^GDAXI 1989–2026 | 9.41 % / 58.61 % | +0.22 pp | −0.46 pp |
| M5 ^FTSE 1985–2026 | −0.05 % / 82.74 % | −0.58 pp | +2.87 pp |

Median Δ CAGR **+0.162 pp**, 5/6 windows at Δ ≥ −0.5 pp (M5 misses by 0.08 pp), 6/6 windows
within +3 pp of the control's drawdown → **G7 passes**. Pooled OOS bootstrap median Δ **+0.424 pp**
→ **G8 passes**. A1/A2/A3 are identical here by construction (the budget is inert without a TRY
calendar).

**21 ETFs (gate F3):** median Δ CAGR **+0.275 pp**, 15/21 positive (71.4 %), Δ max DD ≤ +3 pp on
**17/21 (81.0 %)** — the pre-registered bar is 80 %, so **F3 passes**, but the four breaches are
real: EWL +3.66 pp, SPY +3.63, EWU +3.62, EWS +3.19. Best Δ CAGR EWH +1.27 pp, IWM +1.23,
XLP +1.14, XLK +1.02; worst EWU −0.84, EWS −0.22, SPY −0.22, EEM −0.20. `EWC` is excluded by
`range_vol_v2`'s quality rule (5.6 % invalid/flat bars).

## Bootstraps

**E1 pre-tax (5,000 paths, fixed 2015-09 → 2026-09 calendar, real FX/Yİ-ÜFE):**

| Arm | base median CAGR | median Δ vs R2 | mean Δ | share > 0 | cal. median Δ |
|---|---:|---:|---:|---:|---:|
| C0 | 7.81 % | — | — | — | — |
| A1 | 7.79 % | **+0.0002 pp** | +0.015 pp | 50.0 % | **−0.004 pp** |
| A2 | 7.74 % | **−0.034 pp** | +0.027 pp | 47.1 % | **−0.040 pp** |
| A3 | 7.76 % | **−0.010 pp** | +0.023 pp | 48.8 % | **−0.016 pp** |

The distribution is symmetric noise: on 2,000 re-sampled paths A1 − R2 has mean +0.014 pp,
median −0.004 pp, 49.5 % positive, 50.4 % negative, p05/p95 −0.46/+0.57 pp. **This is the gate
that fails** (both financing models): A1 on the calibrated leg (−0.004 pp) and A2/A3 on both
legs. The full 5,000-path medians are inside `RESULTS.json`.

**After tax (E1, 1,000 paths, $100k):**

| Mode | Arm | median Δ | mean Δ | share > 0 | p05 / p95 |
|---|---|---:|---:|---:|---|
| B1-recent (progressive) | A1 | **+0.228 pp** | +0.260 | 75.5 % | −0.27 / +0.93 |
| | A2 | **+0.283 pp** | +0.386 | 73.1 % | −0.40 / +1.56 |
| | A3 | **+0.237 pp** | +0.302 | 74.7 % | −0.30 / +1.12 |
| B2 flat 20 % | A1 | **+0.093 pp** | +0.111 | 61.9 % | −0.35 / +0.65 |
| | A2 | **+0.085 pp** | +0.163 | 57.8 % | −0.56 / +1.15 |
| | A3 | **+0.074 pp** | +0.127 | 60.6 % | −0.40 / +0.80 |

B1/B2 pass (median ≥ 0, share ≥ 55 %) for all three arms, at $100k and $1M (at $1M, B1:
A1 +0.275/79.6 %, A2 +0.424/75.5 %, A3 +0.321/79.2 %). The contrast between the two modes is the
indexation channel: B2 charges a flat 20 % on non-indexed USD gains and still shows +0.07 to
+0.09 pp. B1's median sits far above its pre-tax counterpart on the very same paths (≈ 0 pp,
−0.004 pp in the 2,000-path sample) — **the resampled recent-regime tax saving (+0.23 pp) is
larger than the whole pre-tax effect**.

## Gates

| Arm | G1 | G2 | G3 | G4 | **G5** | G6 | G7 | G8 | G9 | AT | B1 | B2 | F3 | P | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | P | P | P | P | **F (cal. −0.004 pp)** | P | P | P | P | P | P | P | P | P | **FAIL** |
| A2 | P | P | P | P | **F (base −0.034, cal. −0.040)** | P | P | P | P | P | P | P | P | P | **FAIL** |
| A3 | P | P | P | P | **F (base −0.010, cal. −0.016)** | P | P | P | P | P | P | P | P | P | **FAIL** |

Selected arm: **none** (the pre-registered rule requires every gate key). The plateau condition
is satisfied for every arm (each one's neighbours pass AT), so the verdict is not a plateau
artefact.

## Findings

- **The mechanism works, the effect does not clear the bar.** Suppressing downward resizes that
  would sell lots with an embedded indexed gain is worth +1.0 to +1.2 pp pre-tax and +0.65 to
  +2.01 pp after tax on the three windows, cuts sells/yr by 60–75 %, and *lowers* the measured
  drawdown (49.56 % → 47.46 %). Six out-of-sample windows, 21 instruments, a doubled cost stress
  and two after-tax bootstraps all agree in sign. It fails only the pre-tax clean bootstrap, and
  it fails it in both financing models and for all three arms.
- **The realised-history gain is an exposure effect, not a tax-timing effect.** At $100k on
  2000–2026 the tax drag moves only 5.14 → 5.07 pp while the pre-tax CAGR moves +1.01 pp: the
  position simply spends more time above its vol target (fewer trims, higher weight, longer holds)
  and that paid over this history. The resampled pre-tax distribution (−0.46/+0.57 pp, centred on
  zero) says the same effect is a coin flip in a 10-year draw.
- **Where the tax story does hold is the recent TRY regime.** On the fixed 2015–2026 calendar, the
  after-tax B1 median (+0.23 pp) is an order of magnitude above the pre-tax median (≈ 0 pp), and
  B2 (no indexation, flat 20 %) still shows +0.09 pp. So the arm is a tax-deferral device in a high-inflation,
  high-bracket calendar and a tracking-error bet elsewhere — the opposite of what the pre-tax
  main window suggested.
- **The bracket budget (A2/A3) is a small add-on and is parameter-sensitive.** A2's extra
  187/183/153 postponements buy +0.33/+0.50/+0.96 pp after tax over A1 at $100k (and keep the only
  positive $1M cells), at no pre-tax cost (15.90 % vs 15.68 % base CAGR). Tightening the override
  from 15 pp to 10 pp (A3) keeps about half of A2's postponements (87/86/45) but almost none of its
  after-tax gain (+0.01/+0.02/+0.04 pp over A1), so the budget's effect is a plateau in sign, not
  in size — the pre-registered plateau gate (the neighbours' AT) does not catch that. And because
  the band blocks the large-gain sales first, the budget only ever sees ≤ 1 %-gain sales (190
  events in 26.5 years): it is the band, not the budget, that does the work.
- **The price of the mechanism is a stale weight, and it is capital-dependent.** Holding above the
  vol target is what creates both the pre-tax gain and the tax deferral; it is also what breaks
  the +3 pp drawdown allowance on 4 of the 21 ETFs and moves the $1M results to ~0 (the deflated
  TRY brackets are small relative to a $1M account, so the budget fires 291 times and the arm
  becomes a different strategy).
- **Even at $10k, where the after-tax cells are +2.4 to +4.4 pp, the pre-tax gate still decides.**
  The study's ordering is unambiguous: the after-tax stage loves this rule, and the pre-tax
  bootstrap does not believe it.

## Caveats

- **A pre-registration deviation is disclosed** (Amendments A1 in `PREREGISTRATION.md`): a
  pre-commit sanity probe of the pre-tax main window (all four arms, unit capital) showed
  C0 14.675 % / A1 = A2 = A3 15.683 % before I noticed that the budget needs the $100k account
  scale. No arm, parameter, gate or threshold was changed afterwards; the probe's values are
  recorded there and in `RESULTS.json`'s `harness.reproduction_checks` neighbourhood.
- **One history, in-sample selection.** Four arms, three gain tiers and two override levels were
  fixed before the run, and the gain tiers were copied from the survey; but the arm was still
  designed knowing that trims are R2's tax cost. The plateau gate covers the band's tier
  thresholds only indirectly (through the override neighbours).
- **The R-USD instantiation is not the same rule.** A2/A3 ≡ A1 in the OOS and instrument stages:
  the bracket ceiling is a TRY amount and cannot be transplanted to a unit-NAV, USD-numbered
  history, and the OOS DD/drawdown evidence therefore measures the band only.
- **Model limits** (unchanged from `after_tax_tr_v1`/`tax_harvest_v2`): FIFO, per-year netting
  without loss carry-forward, the 2026 tariff deflated by the previous October's Yİ-ÜFE, the 10 %
  indexation floor, instalments funded by selling, liquidation at the window end. The liquidated
  convention charges the deferred tax at the end, so deferral is judged on its time value and
  indexation only. Not tax advice.
- **No true after-tax out-of-sample**: the TRY/Yİ-ÜFE calendar starts in 2000; B1 holds it fixed at
  2015–2026 and B2 charges no inflation (so B2 understates indexation).
- Nothing was shipped. No `src/`, `config/`, runtime or production change of any kind.

## Reproduction

```
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/gain_band_v1/screen.py --smoke
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/gain_band_v1/screen.py --harness
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/gain_band_v1/screen.py --run
```

Offline; `--run` takes about 15 minutes and writes `RESULTS.json` and `SUMMARY.txt`. Two runs gave
byte-identical `RESULTS.json` (SHA-256
`fcca5d68620db5efcf712f586552d045e40d8004355e084026df927f9fce1c93`). Inputs are existing committed
research files only (no download); SHA-256 and provenance are in `DATA_MANIFEST.json`. The
pre-registration (`PREREGISTRATION.md`), `screen.py`, `tax_engine.py` and the manifest were
committed in `f1ecd40d`, before any non-control arm was simulated on real data; the capital fix of
Amendment A1/A2 is `bf433854` and the state/sizing split is the commit that precedes the results.
