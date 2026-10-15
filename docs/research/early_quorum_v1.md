# Early-path trigger quorum (early_quorum_v1, 2026-10-15): the return is real, the drawdown is not

> Evidence: `research/early_quorum_v1/` (code, PREREGISTRATION, DATA_MANIFEST, RESULTS.json,
> SUMMARY.txt) on branch `research/early_quorum_v1`. Control = production **K**
> (`equity_leveraged_trend_v1` 1.2.0 = E2 + R2's early re-entry + A1u + H2). The parent campaigns'
> published cells are reproduced inside this run (§3). This study tests idea **I2** of
> `idea_survey_v4.md` §4. Nothing is downloaded; nothing outside `research/` is touched.

**Verdict: FAIL — no candidate passes every gate; production keeps K unchanged.** Loosening the
early re-entry leg's AND-of-3 entry to a **2-of-3 quorum** does exactly what the survey predicted
on *return*, and it survives Turkish tax better than the survey's own rule of thumb: Q1 and Q3 add
**+0.364 / +0.378 pp** of pre-tax CAGR (m = 2, base) and **+0.26 / +0.57 / +0.52 pp** and
**+0.27 / +0.57 / +0.54 pp** after tax at $100k on the 2000 / 2010 / 2017 windows, confirmed by the
clean bootstraps (B1-recent **+0.311 / +0.291 pp** median on **61.3 / 60.8 %** of paths) and by
**21/21** rolling January starts. **But they pay +7.07 / +6.99 pp of pre-tax max drawdown** — 7×
the pre-registered +1 pp allowance and ~2× the survey's own worst case (+1 … +4 pp). That single
fact decides the study: **G2** and **PL** fail outright for Q1/Q3, the drawdown cost reappears out
of sample (**G7**: M3 ^N225 ΔDD **+9.78 pp**, past the +8 pp hard cap) and on the 21 instruments
(**EI**: share ΔDD ≤ +3 pp **66.7 / 71.4 %** against the 80 % floor). The third configuration, Q2
(the momentum condition dropped), is the only one inside the drawdown budget (−0.45 pp) — and it
gives the return away entirely (**+0.007 pp** bootstrap median on 50.5 % of paths, negative in all
three after-tax windows, 0/21 rolling starts). The device's sign is not stable across its own three
pre-registered configurations, which is precisely what the survey's honest test (the plateau) was
pre-registered to detect.

Three further readings the owner should keep:

- **The whole drawdown cost is the dot-com decade.** Sub-period max DD 2000–08: K 46.67 % → Q1
  **53.74 %**; 2009–16: 38.46 → 38.35 %; 2017→: 31.12 → **29.81 %** (Q1 is *better* after 2009, and
  its after-tax DD in the 2010 and 2017 windows is *below* K's: 44.54 vs 44.63, 33.72 vs 34.98).
  The quorum buys marginal recoveries; the ones it bought in 2000–02 were not recoveries.
- **The three-episode concentration the survey warns about does not apply to this device.** The
  pre-registered leave-one-episode-out is a **no-op for every arm and every one of the three named
  episodes** (lost CAGR 0.000 pp in all nine cells): inside the published spans the quorum's early
  decisions are *identical* to K's, because a 2-of-3 quorum shares the production exit rule and can
  only add sessions — and it adds none there. So the entire ΔCAGR comes from the *many small*
  marginal recoveries outside those episodes, not from a handful of lucky ones.
- **The device is tax-cheap, as argued, and cheap in trading terms**: sells/yr 9.54 → 9.75 in the
  2017 window, turnover 1.036 × K (G9 passes), and the after-tax edge survives a doubled cost
  assumption (m = 4: +0.239 / +0.563 / +0.517 pp). The failure is *risk*, not tax and not cost.

## 1. Question and what is new

`fast_reentry_v2` varied the early leg's **parameters** (SMA 40/50/60, vol-median 126/252/504,
weight 1/3, 1/2, 2/3) — six one-parameter neighbours of an **AND** — and `ladder_v1` varied its
*size* on the unmodified trigger. No study had ever loosened the **conjunction itself**. Idea I2
proposes a 2-of-3 quorum on the same three conditions, with a structural tax argument: the leg is
built by *buys*, and the sales that end an episode consume days-to-months-old units, which sit in
A1u's 0.5 pp tier (`g_frac` ≤ 1 %) — the tier that fills immediately. So the marginal exposure is
the tax-cheapest in the system.

Three arms, every other parameter fixed (early weight stays 0.5, `vt`, cap 0.98, the 0.5 pp band,
A1u, H2, the close-decision/next-open fill and the instrument are untouched):

| Arm | Early-leg rule |
|---|---|
| **Q1** | enter when **≥ 2 of 3** conditions hold; exit at or below SMA50 (production's exit) |
| **Q2** | drop the 20-session momentum condition: `sma ∧ vol`, same SMA50 exit |
| **Q3** | Q1's entry with a hysteretic exit: leave only when **≥ 2 conditions fail** |

The survey's two stated reporting requirements — the plateau comparison and leave-one-episode-out —
are pre-registered gates/stages here (§5, §6).

## 2. Method

Control **K**, bit for bit, through `tax_engine.py` (a byte copy of `band_h2_combo_v1`'s, itself the
corrected `vt_level_tax_v1` engine): production R2's target series (E2's 2-of-3 vote on the QQQ
close + the `0.5·vt` early leg) with K's execution layer (decide on the completed close, fill at the
next open, 0.5 pp resize band, entries/exits-to-zero always fill, cost 4 bps × m × |Δw|, idle cash
zero, synthetic QLD\* = daily-reset 2× on the QQQ total return, 0.95 %/yr expense, financing DTB3 +
{0 / 0.75 %}/yr) plus **A1u** (gain-scaled downsize band 0.5 / 3 / 8 pp at USD FIFO `g_frac`
≤ 1 % / ≤ 5 % / > 5 %) plus **H2** (a non-exit downsize whose *fill* session is one of the last 10
December sessions defers to January; the December window is tested first). The report-only reference
row **C0** is production R2 (same early leg, band and deferral off).

Main window 2000-03-09 → 2026-09-29 (6,679 measured sessions), m = 2, base financing unless stated.
After tax: progressive 2026 tariff deflated by the previous October's Yİ-ÜFE, **liquidated** at the
window end, $100k. Clean bootstraps: seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax
paths on the fixed 2015-09 → 2026-09 calendar with its realised USD/TRY and Yİ-ÜFE (B1-recent
progressive, B2 flat 20 %). OOS: the six `ewma_band_v1` windows (^GSPC 1957–1999, M1–M5),
calibrated, m = 2, R-USD. Instruments: the 21 quality-passing ETFs (`EWC` excluded by
`range_vol_v2`'s fixed quality rule), each with its own E2, its own early path and its own synthetic
2× leg. Everything is in `PREREGISTRATION.md` §3–§7, committed in `af40c6f3` before any non-control
arm was simulated.

## 3. Reproduction (must pass before any number is read)

`--run` (which runs the harness first) exits 0 only if the harness passes.

- **Deep block, 379 cells** (the parent campaigns' pre-tax main window, after-tax windows at $100k,
  cost stress and clean bootstrap for A1u/H2/C0/K): **0 mismatches**, max|diff| **4.99e-07**
  (tolerance 2e-06) — A1u against `gain_band_v3/RESULTS.json`, H2 against the tagged
  `H2_ANCHORS.json`.
- **Full block, 1,015 cells** (adds the parents' E1/E2/OOS/instrument stages): **0 mismatches**,
  max|diff| **5.00e-07**. (Both figures are the runs' own printed statements; the stored
  `max_abs_diff` fields are rounded to 0.0 by the writer's rounding step.)
- **Control anchors**: K pre-tax 15.7649 / 15.1785 % (m = 2, base/calibrated), max DD 46.6698 /
  46.7948 %, turnover 5.4561; m = 1 16.0173 / 15.4303 %; after tax at $100k **10.7533 / 15.2354 /
  23.9720 %** with max DD 46.6698 / 44.6272 / 34.9774 % — all reproduced to ≤ 0.00004 pp.
- **C0 anchors** (production R2): 15.4766 / 14.8961 %, max DD 46.7584 / 46.8797 %, after tax
  10.160 / 14.706 / 22.886 % — reproduced exactly.
- **Engine identities**: `early_state(prod) == r2_state` bit for bit (on the QQQ close); the H2
  engine == `simulate_band_overlay` with the tail-10 December window (max|diff| 9.9e-14); K and C0
  share the target series and differ only in the fill rule; the inclusions
  production ⊆ Q2 ⊆ Q1 hold (Q2 strictly tighter than Q1).

The run is deterministic: two runs of the frozen code produced **byte-identical** `RESULTS.json`
and `SUMMARY.txt` (§9).

## 4. Per-arm results

### Pre-tax main window, m = 2 (R-INDEX; the R-USD row is identical, the pre-tax path is scale-free)

| arm | CAGR base | CAGR cal | max DD base | max DD cal | Calmar base | turnover/yr |
|---|---|---|---|---|---|---|
| **K** (control) | 15.7649 % | 15.1785 % | 46.67 % | 46.79 % | 0.338 | 5.456 |
| C0 (R2 reference) | 15.4766 % | 14.8961 % | 46.76 % | 46.88 % | 0.331 | 5.992 |
| **Q1** | **16.1284 %** | **15.5303 %** | **53.74 %** | **53.87 %** | 0.300 | 5.653 |
| Q2 | 15.7058 % | 15.1190 % | 46.22 % | 46.35 % | 0.340 | 5.485 |
| Q3 | 16.1427 % | 15.5447 % | 53.66 % | 53.78 % | 0.301 | 5.670 |

m = 1: K 16.0173 / 15.4303 % (DD 46.35 / 46.48); Q1 16.3908 / 15.7919 % (DD 53.37 / 53.50);
Q2 15.9594 / 15.3719 % (DD 45.90 / 46.03); Q3 16.4059 / 15.8072 % (DD 53.28 / 53.41).

Sub-periods (base, m = 2; CAGR % / max DD %):

| arm | P1 2000–08 | P2 2009–16 | P3 2017→ |
|---|---|---|---|
| K | 0.286 / 46.67 | 14.266 / 38.46 | 33.231 / 31.12 |
| Q1 | 0.109 / **53.74** | 14.982 / 38.35 | 33.900 / 29.81 |
| Q2 | 0.349 / 46.22 | 14.077 / 39.27 | 33.151 / 31.52 |
| Q3 | 0.129 / **53.66** | 14.982 / 38.35 | 33.921 / 29.71 |

### After tax at $100k (progressive, base, m = 2, liquidated)

| arm | 2000-03→2026-09 | 2010-01→ | 2017-01→ | Δ vs K (pp) | max DD AT | tax paid (2017) | sells/yr (2017) |
|---|---|---|---|---|---|---|---|
| **K** | 10.7533 % | 15.2354 % | 23.9720 % | — | 46.67 / 44.63 / 34.98 | $371,487 | 9.54 |
| C0 | 10.1600 % | 14.7059 % | 22.8861 % | −0.593 / −0.529 / −1.086 | 46.76 / 43.78 / 36.24 | $360,297 | 35.60 |
| **Q1** | **11.0096 %** | **15.7932 %** | **24.4920 %** | **+0.256 / +0.558 / +0.520** | 53.74 / 44.54 / 33.72 | $389,804 | 9.75 |
| Q2 | 10.6778 % | 15.0805 % | 23.8808 % | −0.076 / −0.155 / −0.091 | 46.22 / 45.36 / 35.36 | $370,290 | 9.64 |
| Q3 | 11.0222 % | 15.8035 % | 24.5109 % | +0.269 / +0.568 / +0.539 | 53.66 / 44.54 / 33.62 | $390,270 | 9.64 |

m = 4 cost stress, after tax at $100k (Δ vs K): Q1 **+0.239 / +0.563 / +0.517 pp**; Q2
−0.077 / −0.157 / −0.098; Q3 +0.250 / +0.573 / +0.536. The after-tax edge is not a cost artefact.

### Clean bootstrap at $100k (seed 20261011, block 20)

| arm | pre-tax leg, 5,000 paths (base / cal) | B1-recent, 1,000 paths | B2 flat-20 % |
|---|---|---|---|
| Q1 | **+0.485 pp** (65.8 %) / **+0.469 pp** (65.1 %) | **+0.311 pp** (61.3 %) | **+0.317 pp** (62.4 %) |
| Q2 | +0.007 pp (50.5 %) / +0.006 pp (50.4 %) | +0.000 pp (49.6 %) | +0.002 pp (50.7 %) |
| Q3 | +0.442 pp (64.6 %) / +0.426 pp (64.0 %) | +0.291 pp (60.8 %) | +0.264 pp (61.1 %) |

Medians of (arm − K) per path; the share in brackets is the fraction of paths ahead. Q1/Q3 pass both
after-tax bootstrap gates with room; Q2 sits exactly on the coin flip.

### Six OOS windows (pre-tax, calibrated, m = 2, R-USD)

| window | K CAGR | K max DD | C0 Δ | Q1 Δ CAGR / ΔDD | Q2 | Q3 |
|---|---|---|---|---|---|---|
| ^GSPC 1957–99 | 9.97 % | 39.65 % | −0.08 | +0.80 / +2.32 | +0.22 / +0.09 | +0.90 / +2.15 |
| M1 ^IXIC | 21.27 % | 41.22 % | −0.09 | +1.72 / −1.02 | −0.23 / +0.75 | +1.83 / −1.02 |
| M2 ^GSPC | 11.73 % | 58.11 % | −0.02 | +0.20 / −2.38 | −0.41 / +1.99 | +0.18 / −2.23 |
| M3 ^N225 | 6.46 % | 65.18 % | −0.05 | −0.12 / **+9.78** | −0.32 / +4.83 | +0.03 / **+8.90** |
| M4 ^GDAXI | 9.71 % | 58.15 % | −0.23 | −0.12 / −0.86 | −0.50 / −0.00 | −0.00 / −1.64 |
| M5 ^FTSE | −0.62 % | 85.65 % | +0.04 | +0.12 / +0.54 | −0.02 / −0.35 | +0.30 / −0.50 |

Pooled OOS block-20 bootstrap (2,000 paths × 5 markets, 10,000 paths): Q1 +0.333 pp (62.5 %),
Q2 +0.022 pp (52.9 %), Q3 +0.305 pp (61.9 %). The *return* generalises out of sample; the
drawdown does not (M3 breaches the +8 pp hard cap).

### The 21 quality-passing ETFs (pre-tax, calibrated, m = 2, R-USD)

| arm | median Δ CAGR | share Δ > 0 | share ΔDD ≤ +3 pp | worst ΔDD | pooled boot (10,500 paths) |
|---|---|---|---|---|---|
| Q1 | +0.163 pp | 52.4 % (11/21) | **66.7 % (14/21)** | **+9.49 pp** | +0.285 pp |
| Q2 | +0.048 pp | 61.9 % (13/21) | 95.2 % (20/21) | +3.92 pp | +0.002 pp |
| Q3 | +0.354 pp | 52.4 % (11/21) | **71.4 % (15/21)** | **+8.76 pp** | +0.247 pp |

Report-only blocks: **E1** (after tax on the same ETFs, R-INDEX, $100k) Q1 median −0.036 pp
(47.6 % > 0), Q2 +0.063 (57.1 %), Q3 +0.135 (52.4 %); **E2** (21 rolling January starts, after tax
$100k) Q1 **21/21 ≥ 0**, median +0.558 pp (min +0.266, max +0.916), Q2 **0/21**, median −0.134,
Q3 21/21, median +0.568.

### Plateau comparison block (main window, base, m = 2, pre-tax; Δ vs K)

| arm | early-leg spec | early sessions | CAGR | ΔCAGR | max DD | ΔDD |
|---|---|---|---|---|---|---|
| **K** | prod (need 3, exit SMA50) | 164 | 15.7649 % | — | 46.67 % | — |
| **Q1** | need 2, exit SMA50 | **425** | 16.1284 % | **+0.364** | 53.74 % | **+7.07** |
| **Q2** | `sma∧vol`, exit SMA50 | 178 | 15.7058 % | −0.059 | 46.22 % | −0.45 |
| **Q3** | need 2, exit majority | 419 | 16.1427 % | +0.378 | 53.66 % | +6.99 |
| N40 | SMA 40 | 171 | 15.3189 % | −0.446 | 51.75 % | +5.08 |
| N60 | SMA 60 | 141 | 15.3244 % | −0.440 | 48.90 % | +2.23 |
| N126 | vol-median 126 | 275 | 15.9858 % | +0.221 | 49.25 % | +2.58 |
| N504 | vol-median 504 | 115 | 15.6300 % | −0.135 | 46.78 % | +0.11 |
| W13 | weight 1/3 | 164 | 15.5302 % | −0.235 | 47.35 % | +0.68 |
| W23 | weight 2/3 | 164 | 15.9874 % | +0.223 | 46.00 % | −0.67 |

The six neighbours are the `fast_reentry_v2` one-parameter variants of the **production** early path
placed on **K's** execution layer (report-only; they are not candidates). Note that only 2 of 6 beat
K on CAGR here, against 6 of 6 beating C0 in `fast_reentry_v2` — under the band the early leg's
extra exposure is worth much less, and N40 (the survey's named neighbour) *loses* 0.446 pp with
+5.08 pp of drawdown. The device's own three configurations do not share a sign: **Q2 is negative**
on return while Q1/Q3 are positive, and Q1/Q3 breach the +1 pp drawdown allowance by 7 pp.

### Leave-one-episode-out (the survey's required report)

Published spans (from `fast_reentry_v1`'s per-episode table, mapped to sessions by date) and the
control's early sessions inside each:

| episode | span | sessions | K early sessions inside |
|---|---|---|---|
| 2001-10 | 2001-10-31 → 2001-12-05 | 25 | 24 |
| 2002-11 | 2002-11-04 → 2002-12-02 | 20 | 19 |
| 2023-01 | 2023-01-20 → 2023-02-01 | 9 | 8 |

Replacing the candidate's early-leg decisions by K's over each span changes **nothing**: for every
arm and every episode the candidate's sessions inside the span are *identical* to K's (24 / 19 / 8),
so lost CAGR = **0.000 pp in all nine cells**. Full-window ΔCAGR vs K: Q1 +0.364 pp, Q2 −0.059 pp,
Q3 +0.378 pp. This is a property of the device, not an accident: a 2-of-3 quorum shares the
production exit rule and can only *add* early sessions, and inside K's own episodes it adds none.
The quorum's whole edge therefore comes from the marginal recoveries **outside** the three episodes
the survey flagged as carrying the incumbent's gain — 425 / 178 / 419 early sessions against
production's 164.

### Device structure on the QQQ close

Early sessions: production 164 (production's own count — `fast_reentry_v1` reports 164 of 6,679,
2.46 %), Q1 **425**, Q2 **178**, Q3 **419**. The inclusions production ⊆ Q2 ⊆ Q1 hold and Q2 is
strictly tighter than Q1. Q1-only sessions (active in Q1, not Q3) **6**; Q3-only sessions **0**.
Hold-count histogram over the full 6,932-session series: 0 conditions 1,442; 1 → 1,145; 2 → 1,836;
3 → 2,509.

## 5. Gates

Every gate must pass; there is no "most gates" reading. Q1/Q3's numbers in brackets.

| Gate | Requirement | Q1 | Q2 | Q3 |
|---|---|---|---|---|
| **G1** | m = 2: CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp (base and calibrated) | PASS (+0.364 / +0.352 pp) | **FAIL** (−0.059 / −0.060) | PASS (+0.378 / +0.366) |
| **G2** | max DD ≤ K + 1 pp (base and calibrated) | **FAIL (+7.07 / +7.07)** | PASS (−0.45 / −0.45) | **FAIL (+6.99 / +6.99)** |
| **G3** | 2017+ CAGR ≥ K − 1 pp (base and calibrated) | PASS (+0.669 / +0.660) | PASS (−0.080 / −0.080) | PASS (+0.690 / +0.681) |
| **G4** | CAGR above K in ≥ 2 of 3 sub-periods | PASS (2/3) | **FAIL (1/3)** | PASS (2/3) |
| **G5** | pre-tax block-20 bootstrap median ≥ 0 (base and calibrated) | PASS (+0.485 / +0.469) | PASS (+0.007 / +0.006) | PASS (+0.442 / +0.426) |
| **G6** | G1 at m = 1 | PASS | **FAIL** | PASS |
| **G7** | six OOS windows: median Δ ≥ 0; ≥ 5/6 with ΔCAGR ≥ −0.5 and ΔDD ≤ +3; ΔDD never > +8 | **FAIL** (M3 +9.78) | **FAIL** (median −0.274, 4/6) | **FAIL** (M3 +8.90) |
| **G8** | pooled OOS bootstrap median ≥ 0 | PASS (+0.333) | PASS (+0.022) | PASS (+0.305) |
| **G9** | turnover ≤ 2 × K | PASS (1.036 ×) | PASS (1.005 ×) | PASS (1.039 ×) |
| **EI** | 21 ETFs: median Δ ≥ 0; share > 0 ≥ 60 %; share ΔDD ≤ +3 pp ≥ 80 %; pooled boot ≥ 0 | **FAIL** (52.4 %, 66.7 %) | PASS (61.9 %, 95.2 %) | **FAIL** (52.4 %, 71.4 %) |
| **T1** | after tax at $100k: Δ ≥ 0 in all three windows | PASS (+0.256 / +0.558 / +0.520) | **FAIL** (−0.076 / −0.155 / −0.091) | PASS (+0.269 / +0.568 / +0.539) |
| **T2** | B1-recent: median ≥ 0 and share > 0 ≥ 55 % | PASS (+0.311, 61.3 %) | **FAIL** (+0.000, 49.6 %) | PASS (+0.291, 60.8 %) |
| **T3** | B2 flat-20 %: median ≥ 0 and share > 0 ≥ 55 % | PASS (+0.317, 62.4 %) | **FAIL** (+0.002, 50.7 %) | PASS (+0.264, 61.1 %) |
| **PL** | every Q arm: base-m2 ΔCAGR > 0 **and** ΔDD ≤ +1 pp | **FAIL** (+7.07 pp) | **FAIL** (−0.059 pp) | **FAIL** (+6.99 pp) |

**Selection rule** (pre-registered): `adopted` = the candidate(s) passing every gate; ties by mean
after-tax $100k delta, then the B1 median; else production keeps K. **No candidate passes →
`adopted = K`**, production unchanged. Q1 fails G2, G7, EI, PL; Q2 fails G1, G4, G6, G7, T1, T2,
T3, PL; Q3 fails G2, G7, EI, PL.

## 6. Findings

1. **I2's mechanism is confirmed; its price is not.** The trigger can be loosened profitably — and
   profitably *after Turkish tax*, at roughly the survey's predicted size (+0.26 … +0.57 pp against
   its +0.20 … +0.60 pp band) with bootstrap confirmation and 21/21 rolling starts — but the
   drawdown cost (+7 pp) is twice the survey's own worst case and 7× the pre-registered allowance.
   Since the stated objective is maximum long-run return *without materially worse drawdown*, the
   device fails on its own terms.
2. **The drawdown is a 2000–2008 phenomenon, not a general one.** P2 and P3 drawdowns are flat or
   better (38.46 → 38.35; 31.12 → 29.81) and Q1's after-tax DD is *below* K's in both later
   windows. An owner who reads risk per-decade rather than as a single 26-year maximum would see a
   different trade; the pre-registered rule is the whole window, so the verdict stands.
3. **The edge is not episode-concentrated** (LOEO is a no-op in all nine cells). The survey's
   concentration risk is a property of the incumbent's gain vs E2, not of the quorum's delta.
4. **The momentum condition carries the entire edge.** Dropping it (Q2) leaves a bootstrap median
   of exactly 0.000 pp on 49.6 % of paths and 0/21 rolling starts — i.e. `sma ∧ vol` alone is a
   coin flip, and the 20-session momentum filter is what makes the quorum pay. That is a mechanism
   finding worth keeping: the value is in *entering only when price is already turning*.
5. **Under K's execution layer the early leg's neighbourhood is much weaker than under C0's.**
   2 of 6 one-parameter neighbours beat K (+0.221 / +0.223 pp), against 6 of 6 beating C0 in
   `fast_reentry_v2`; N40, the survey's named neighbour, now loses 0.446 pp with +5.08 pp DD. The
   band removes most of the trims that made extra early exposure pay, so "more early exposure" is
   no longer free on K — the same conclusion from a different direction.
6. **The device is cheap where the survey said it would be**: sells/yr 9.54 → 9.75, turnover
   1.036 × K (G9), and the after-tax delta survives m = 4 (+0.239 / +0.563 / +0.517 pp). Whatever
   fails here is risk, not tax and not cost.

## 7. Caveats

- One history per cell. The 2000–02 path is a single observation; the entire DD verdict rests on
  it. There is no true after-tax out-of-sample (the CBRT/Yİ-ÜFE calendar starts 2000).
- The clean bootstraps resample *market returns* on a fixed 2015-09 → 2026-09 TRY/Yİ-ÜFE calendar,
  so they price the tax system but not another FX/inflation regime; B2 (flat 20 %) carries losses
  forward and so is a different tax system.
- The after-tax figures use the liquidated convention (deferred tax charged at the window end),
  FIFO with per-year netting and no loss carry-forward, and the 2026 tariff deflated by the
  previous October's Yİ-ÜFE.
- `range_vol_v2`'s quality rule excludes `EWC` (bad-fraction 5.6 %), so the instrument gate is 21,
  not 22; the G1–G9 thresholds are `ewma_band_v1`'s and the instrument gate's are
  `fast_reentry_v2`'s, unchanged.
- The instrument bootstrap uses 500 paths per instrument (10,500 pooled) instead of 2,000, to stay
  inside the compute budget; the threshold is unchanged.
- The plateau neighbours are measured on K's execution layer, whereas `fast_reentry_v2` measured
  them on C0's; the two tables are not directly comparable, and only the Q rows are gated.
- G5 (pre-tax bootstrap) and G2 (pre-tax DD) can disagree in sign for a device that trades return
  for risk; here they do not — Q1/Q3 win on return in both financing models and lose on DD.
- **Not tax advice.**

## 8. Amendments and honesty notes

Full detail in `PREREGISTRATION.md` §10–§11. Nothing in §4–§6 was changed after data was seen: the
arms, parameters, stages, seeds, thresholds, gates and the selection rule are as pre-registered, and
the gate booleans and `ADOPTED` are identical before and after every post-run fix (checked cell by
cell).

- **A2** (pre-run): harness/writer plumbing and the amended structural claim, all before the first
  real `--run`.
- **A3** (post-run, report-only): (i) the three reporting stages computed the early flag on the
  synthetic 2× close instead of the QQQ close — 126 vs production's 164 sessions — which corrupted
  the plateau/E5 session counts and the whole LOEO block; fixed with a cached `qqq_price()`
  accessor. (ii) the full reproduction block compared arm-minus-K deltas against the parents'
  published arm-minus-C0 cells (138 of 1,015 cells flagged); the new `_parent_convention` helper
  converts, and the block now reports 1,015 cells / 0 mismatches / max|diff| 5.0e-07. (iii) two
  SUMMARY-writer crashes (a `C0_vs_K` key that the tax block does not publish, and a
  `win[window]` vs `ctrl[capital]` index). (iv) `device_structure`, which the pre-registration
  promises as the Q1/Q3 measurement, was computed but not exported; it now is. No gate reads any
  affected cell.
- The run before A3 produced the same gate booleans and the same `ADOPTED`; the only cells that
  moved are the report-only ones listed above.

## 9. Reproduction

`/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/early_quorum_v1/screen.py --run`
from the worktree root. Offline, deterministic, about 10–17 minutes depending on machine load.
`--smoke` (synthetic prices only) and
`--harness` (deep reproduction block) run before it. Two consecutive runs of the frozen code gave
byte-identical `RESULTS.json` (SHA-256
`32037decf41d539233109729a43cc05aef59a576bdfde86519a2dc0cc861b143`) and `SUMMARY.txt` (SHA-256
`762f03ec413cd6f1b9748139cc39363def7f4624ed6839e66523fa1b3d29bf6d`).

Evidence: `research/early_quorum_v1/` — `PREREGISTRATION.md` (committed `af40c6f3`, before any
non-control arm was simulated), `screen.py`, `tax_engine.py`, `DATA_MANIFEST.json`, `H2_ANCHORS.json`,
`RESULTS.json`, `SUMMARY.txt`.
