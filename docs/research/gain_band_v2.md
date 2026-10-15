# Gain-scaled no-trade band on R2's downward resizes, corrected control (gain_band_v2, 2026-10-14)

> Correction re-run of `gain_band_v1` (same arms, same gates, control = production R2). Evidence
> (`research/gain_band_v2/` code, PREREGISTRATION, results) is not on `main`; it lives on branch
> `research/gain_band_v2` in the `gain_band_v2` worktree. v1 is not superseded as an archive; its
> arm rows are numerically identical to v2's and remain valid, but **every "Δ vs R2" column in
> `docs/research/gain_band_v1.md` that came from the main window, the R-USD row, the after-tax
> windows, the cost stress, the OOS windows, the 21 ETFs or the pooled OOS bootstrap is really
> "Δ vs E2"** and is replaced by this report.

**Verdict: FAIL as pre-registered — all three arms miss on gate G5 only (the pre-tax clean
bootstrap), exactly as in v1, and nothing is selected.** A1 misses by 0.4 bp of median CAGR on
the calibrated leg, A2 by 3.4/4.0 bp, A3 by 1.0/1.6 bp. Every other gate key passes for all three
arms with the corrected control: G1–G4, G6–G9, AT (+0.46 / +0.44 / +0.85 pp after tax at $100k
for A1), B1 (+0.228 pp on 75.5 % of paths), B2 (+0.093 pp on 61.9 %), F3 (21/21 ETFs within
+3 pp), and the plateau P. Production R2 is unchanged.

The correction shrinks every "vs R2" number v1 published, because R2 is itself the better
strategy in the pre-tax main window (15.48 % vs E2's 14.67 % base CAGR). Δ CAGR on the main
window falls from +1.01/+1.23/+1.06 pp (v1, vs E2) to **+0.21/+0.43/+0.26 pp** (v2, vs R2);
after-tax Δ at $100k on the 2000 window from +1.08 pp to **+0.46 pp**; the pooled OOS bootstrap
median from +0.42 pp to **+0.03 pp**. The failing gate, and the verdict, do not move: G5 was
already measured against the correct R2 control in v1 (see Diagnosis) and returns identical
numbers to the last digit.

## Diagnosis of `gain_band_v1`

The v1 code bound its **control label "C0" to the pure-E2 target series** while driving the arms
from production R2's series, so the control was never R2:

| Site (`research/gain_band_v1/screen.py`) | Lines | What it did |
|---|---|---|
| `arm_signals` | 305–313 (`if arm == "R2" … else`) | every label but `"R2"` — `"C0"` included — fell through to the pure-E2 series (`factor = 1`, no early leg) |
| `main_inputs` | 604, 606 | `w_c0 = arm_signals(…, "C0", …)` = **E2** wants; `w_r2` = R2 wants |
| `run()` | 1149, 1156 | `WANTS = {"C0": w_c0, A1/A2/A3: w_r2}` and the per-financing `W = {"C0": wc_, …}` → the control was E2 in the pre-tax main window, the R-USD row, the three after-tax windows and the m = 4 cost stress |
| `oos_window` | 735, 740 | `w_c0 if arm == "C0" else w_r2` → the six OOS windows' control was E2 (gate G7) |
| `instrument_window` | 784, 789 | same → the 21-ETF control was E2 (gate F3) |
| `boot_oos_market` | 935, 937 | same → the pooled OOS bootstrap's control was E2 (gate G8) |

`boot_e1` was **not** affected: it drives every label from `w_r2` (`legs = {"base": (so[0], sc[0],
w_r2), …}`, line 851, used for all `arm in ARMS`), so v1's gate **G5** and the after-tax
bootstraps **B1/B2** were already measured against production R2 — their v1 numbers are correct
and are reproduced in v2 unchanged. The `--harness` did not catch the mislabel because it pinned
the label "C0" to `e2_integration_v1`'s **E2** anchors (`REF_DEV`, line 1092) and the label "R2"
to the R2 anchors: both statements were true, and the run simply never used the R2 label for its
control. Symptom in v1's own tables: its control row reads pre-tax 14.67 % / 14.10 %, max DD
49.56 % (= E2 exactly) and after tax 9.53 / 14.50 / 22.69 % (= `TAX_ANCHORS["C0"]`, E2).

**What v2 changes.** Only the control binding and the harness assertions: arm definitions,
parameters, data, mechanics, gates, thresholds and the selection rule are v1's, unchanged; the
PREREGISTRATION was committed before the run (`b6d2c0e1`). The arm code path is untouched — an AST
comparison of `engine_run` (body minus docstring), `rule_sale`, `r2_state`, `sized_wants`,
`early_components` between v1 and v2 is hash-identical — and, as predicted in the
pre-registration, **the A1/A2/A3 cells in `RESULTS.json` are numerically identical to v1's**
(programmatic diff of every arm cell across the pre-tax table, the after-tax windows at
$10k/$100k/$1M, the OOS windows, the 21 instruments, the E1 bootstraps and the pooled OOS
bootstrap: 45 differing cells, every one of them the control or the control-derived pooled OOS
bootstrap).

## v1 vs v2 numbers (the same arms, the right control)

Control identity — which series the run labelled "R2":

| Quantity (control = "C0") | v1's row | v2's row | production R2 reference | E2 reference |
|---|---:|---:|---:|---:|
| pre-tax CAGR base, m = 2 | 14.67 % | **15.48 %** | 15.48 % | 14.67 % |
| pre-tax CAGR base, m = 1 | 14.95 % | **15.75 %** | 15.75 % | 14.95 % |
| pre-tax CAGR calibrated, m = 2 / m = 1 | 14.10 / 14.38 % | **14.90 / 15.17 %** | 14.90 / 15.17 % | 14.10 / 14.38 % |
| pre-tax max DD base / calibrated, m = 2 | 49.56 / 49.65 % | **46.76 / 46.88 %** | 46.76 / 46.88 % | 49.56 / 49.65 % |
| after-tax CAGR $100k, windows 2000 / 2010 / 2017 | 9.53 / 14.50 / 22.69 % | **10.16 / 14.71 / 22.89 %** | 10.16 / 14.71 / 22.89 % | 9.53 / 14.50 / 22.69 % |
| after-tax max DD $100k, 2000 / 2010 / 2017 | 49.56 / 44.69 / 37.61 % | **46.76 / 43.78 / 36.24 %** | 46.76 / 43.78 / 36.24 % | 49.56 / 44.69 / 37.61 % |

Δ against the control, v1 (vs E2) → v2 (vs R2):

| Quantity | A1 v1 → v2 | A2 v1 → v2 | A3 v1 → v2 |
|---|---|---|---|
| pre-tax main CAGR base, m = 2 | +1.01 → **+0.21** pp | +1.23 → **+0.43** | +1.06 → **+0.26** |
| pre-tax main CAGR calibrated, m = 2 | +0.96 → **+0.17** | +1.19 → **+0.39** | +1.02 → **+0.23** |
| pre-tax max DD (both financings) | −2.10 → **+0.70** pp | −2.10 → **+0.70** | −2.10 → **+0.70** |
| after-tax CAGR $100k, 2000 / 2010 / 2017 | +1.08/+0.65/+1.05 → **+0.46/+0.44/+0.85** | +1.42/+1.14/+2.01 → **+0.79/+0.93/+1.81** | +1.10/+0.66/+1.09 → **+0.47/+0.45/+0.89** |
| OOS six windows, median Δ CAGR | +0.162 → **+0.040** pp | = A1 | = A1 |
| pooled OOS bootstrap G8 median Δ | +0.424 → **+0.032** pp | = A1 | = A1 |
| 21 ETFs, Δ max DD ≤ +3 pp | 17/21 → **21/21** | = A1 | = A1 |
| E1 pre-tax median Δ (base / calibrated) | +0.0002 / −0.004 → **identical** | −0.034 / −0.040 → **identical** | −0.010 / −0.016 → **identical** |
| B1 / B2 median Δ, $100k | +0.228 / +0.093 → **identical** | +0.283 / +0.085 → **identical** | +0.237 / +0.074 → **identical** |

The last three rows are the verification that the arm path is untouched: the gates G5/B1/B2 and
the pre-tax bootstrap already used R2 in v1, so their numbers must not (and do not) move.

## Question

R2 resizes to its vol target on the first close that breaches a constant 0.5 pp band, and ~91 %
of its realised Turkish taxable gain comes from downward resizes under FIFO lot matching. Does
replacing that constant band, **on downward resizes only**, with a threshold that grows with (a)
the embedded indexed taxable TRY gain of the lots the sale would consume and (b) the year's
accumulated realised TRY gain relative to the year's deflated bracket ceiling improve the
after-Turkish-tax CAGR at $100k versus production R2, without a materially worse drawdown?

## Method

Unchanged from `gain_band_v1` §4–§5, re-stated in `research/gain_band_v2/PREREGISTRATION.md` §3–§5:
control = production R2 (`equity_leveraged_trend_v1` 1.2.0) exactly; arms share R2's target series
bit for bit, only the fill decision differs; only a downward resize with a positive target can be
suppressed (including the transfer between R2's full weight and its half-weight early-re-entry
leg); entries, exits to zero and up-resizes keep the 0.5 pp production band.

- **A1** — gain-scaled band: downsize fills iff `|tgt − w| > B_down`, `B_down` = 0.5 pp / 3 pp /
  8 pp for an embedded FIFO indexed TRY gain of ≤ 1 % / ≤ 5 % / more, `g_frac` = rule gain /
  proceeds of the units the sale would consume, engine-own indexation (10 % Yİ-ÜFE floor,
  month-to-month) at the CBRT buying rate of the trade date.
- **A2** — A1 + bracket budget: a discretionary downsize is postponed while the year's realised
  rule gain ≥ `1,000,000 × scale(year)` TRY (the year's deflated 27 % ceiling); override at
  `|tgt − w| > 15 pp`, on the E2-off decision close, or a target ≤ 0.
- **A3** — A2 with the override at 10 pp (plateau neighbour).
- One engine (`tax_engine.py`, byte copy of `tax_harvest_v2/tax_engine.py`) drives every stage;
  R-INDEX (real USD/TRY + Yİ-ÜFE) where a TRY calendar exists, R-USD (fx = 1, budget inert, so
  A2 ≡ A3 ≡ A1) for the six OOS windows and the 21 ETFs.
- **Harness (PASS, 0 checks outside tolerance).** The label the run uses for the control
  reproduces the R2 anchors within 1e-4: pre-tax 15.48 / 14.90 % CAGR (15.75 / 15.17 % at m = 1),
  max DD 46.76 / 46.88 %, turnover 5.9915; after tax at $100k 10.160 / 14.706 / 22.886 % CAGR with
  max DD 46.758 / 43.784 / 36.243 % — reported as the `r2_anchors` block and gated (`pass`), with
  the pure-E2 series asserted separately under the label `E2` (14.67 / 14.10 %, 49.56 %) and a
  check that the control's NAV path differs from it. The control's NAV path equals
  `simulate_band` to 1.3e-13; the B1/B2 bootstrap medians (3.5155 % / 3.9678 %) reproduce
  `tax_harvest_v2`'s R2 anchors and the E2 anchor 3.0790 % / 3.5201 %.

## Pre-tax main window (2000-03-09 → 2026-09-29, 6,679 sessions, m = 2, $100k account)

| Arm | CAGR base | CAGR cal. | max DD base | max DD cal. | Calmar | 2017+ | P1 / P2 / P3 | m = 1 base | turn/yr | sells/yr | blocked band / budget |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| **C0 (production R2)** | 15.48 % | 14.90 % | 46.76 % | 46.88 % | 0.331 | 32.71 % | −0.06 / 14.30 / 32.71 | 15.75 % | 5.99 | 29.6 | 0 / 0 |
| **A1** | **15.68 %** | 15.07 % | 47.46 % | 47.58 % | 0.330 | 33.18 % | +0.07 / 14.31 / 33.18 | 15.94 % | 5.48 | 10.4 | 1,293 / 0 |
| **A2** | **15.90 %** | 15.29 % | 47.46 % | 47.58 % | 0.335 | 33.91 % | +0.07 / 14.27 / 33.91 | 16.20 % | 5.34 | 8.4 | 1,253 / 190 |
| **A3** | **15.74 %** | 15.13 % | 47.46 % | 47.58 % | 0.332 | 33.31 % | +0.07 / 14.36 / 33.31 | 15.99 % | 5.41 | 9.1 | 1,306 / 88 |

m = 4 cost stress (base): C0 14.92 %, A1 15.18 %, A2 15.41 %, A3 15.24 %. Turnover is 5.34–5.48/yr
against R2's 5.99 (entries and exits dominate it; the *sells* fall from 29.6 to 8.4–10.4/yr, all
of it downward resizes). Under the R-USD rule (fx = 1, no indexation) C0 is unchanged at 15.48 %
and A1/A2/A3 read 15.72 / 15.77 / 15.72 %, so the +0.21–0.43 pp edge is not a property of the TRY
indexation arithmetic. A1 postpones resizes in all 27 calendar years (top: 2017 251, 2013 192,
2007 149, 2015 125, 2020 114), not in one episode; A2's budget adds 190 postponements (A3: 88).

## After tax (progressive 2026 tariff, liquidated, base financing, m = 2)

At **$100k** — the gate capital:

| Window | Arm | Pre-tax | After-tax | Δ vs R2 | unliquid. | max DD | terminal USD | tax paid USD | sells/yr |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | R2 | 15.47 % | **10.160 %** | — | 10.78 % | 46.76 % | 1,306,627 | 766,160 | 30.7 |
| | A1 | 15.68 % | **10.617 %** | **+0.457** | 11.29 % | 47.46 % | 1,458,459 | 816,720 | 10.9 |
| | A2 | 15.68 % | **10.951 %** | **+0.791** | 11.62 % | 47.46 % | 1,579,937 | 844,247 | 8.9 |
| | A3 | 15.68 % | **10.630 %** | **+0.470** | 11.31 % | 47.46 % | 1,462,972 | 814,609 | 9.6 |
| 2010-01 → end | R2 | 22.16 % | **14.706 %** | — | 15.72 % | 43.78 % | 994,825 | 553,536 | 31.4 |
| | A1 | 22.34 % | **15.142 %** | **+0.436** | 16.23 % | 44.21 % | 1,060,055 | 564,644 | 10.7 |
| | A2 | 22.34 % | **15.639 %** | **+0.933** | 16.73 % | 44.36 % | 1,139,298 | 580,924 | 7.8 |
| | A3 | 22.34 % | **15.160 %** | **+0.454** | 16.27 % | 43.95 % | 1,062,915 | 562,856 | 8.8 |
| 2017-01 → end | R2 | 32.53 % | **22.886 %** | — | 24.73 % | 36.24 % | 745,349 | 360,297 | 35.6 |
| | A1 | 32.98 % | **23.739 %** | **+0.853** | 25.73 % | 35.88 % | 797,312 | 370,681 | 10.0 |
| | A2 | 32.98 % | **24.700 %** | **+1.814** | 26.69 % | 35.91 % | 859,778 | 384,707 | 6.1 |
| | A3 | 32.98 % | **23.780 %** | **+0.893** | 25.80 % | 36.29 % | 799,884 | 369,082 | 8.3 |

- **Scale.** Δ at $10k: +0.36/+0.46/+0.28 (2000), +0.35/+0.52/+0.36 (2010), +0.67/+1.03/+0.69
  (2017). At $1M: +0.52/+0.99/+0.63, +0.46/+0.96/+0.54, +0.90/+1.98/+1.04. Every sign is
  positive and A2 is always the largest; the advantage is now fairly flat in capital (v1 reported
  +2.4 to +4.4 pp at $10k and ~0 at $1M against E2 — that spread was the E2 comparison, not the
  capital dependence of the rule).
- **Cost.** At m = 4 ($100k) every sign survives: A1 +0.49/+0.47/+0.90 pp, A2 +0.83/+1.01/+1.84,
  A3 +0.50/+0.52/+0.96.
- **Tax channel.** A1 pays *more* nominal tax than R2 (816,720 vs 766,160 USD over 2000–2026;
  564,644 vs 553,536 over 2010+; 370,681 vs 360,297 over 2017+) and still ends 12 % wealthier on
  the 2000 window (1,458,459 vs 1,306,627 USD). Its realised resize gain is larger because each
  surviving trim sells older, more appreciated lots (indexation relief 21.9m vs 18.6m TRY). The
  tax drag falls 15.477 → 10.160 = **5.32 pp** for R2 and 15.683 → 10.617 = **5.07 pp** for A1, so
  ~0.25 pp of the 0.46 pp after-tax gain is tax timing and the rest is the higher average
  exposure the band produces. (v1 measured the same 5.07 pp but against E2's 5.14 pp.)

## Out of sample and across instruments (pre-tax, calibrated, m = 2, R-USD)

| Window | C0 (R2) CAGR / max DD | Δ CAGR | Δ max DD |
|---|---:|---:|---:|
| ^GSPC 1957–1999 | 9.89 % / 39.63 % | +0.08 pp | +0.03 pp |
| M1 ^IXIC 1975–1999 | 21.18 % / 40.97 % | +0.05 pp | +0.24 pp |
| M2 ^GSPC 1935–1956 | 11.70 % / 58.33 % | −0.01 pp | −0.22 pp |
| M3 ^N225 1966–2026 | 6.40 % / 65.03 % | +0.03 pp | +0.61 pp |
| M4 ^GDAXI 1989–2026 | 9.47 % / 58.10 % | +0.15 pp | +0.04 pp |
| M5 ^FTSE 1985–2026 | −0.59 % / 85.45 % | −0.04 pp | +0.16 pp |

Median Δ CAGR **+0.040 pp**, 6/6 windows at Δ ≥ −0.5 pp, 6/6 within +3 pp of the control's
drawdown → **G7 passes** (v1: +0.162 pp vs E2). Pooled OOS bootstrap (M1–M5, 10,000 paths) median
Δ **+0.032 pp**, share > 0 57.5 %, per-market +0.16 pp → **G8 passes** (v1: +0.424 pp vs E2).
A1/A2/A3 are identical here by construction (the budget is inert without a TRY calendar).

**21 ETFs (gate F3):** median Δ CAGR **+0.075 pp**, 15/21 positive (71.4 %), Δ max DD ≤ +3 pp on
**21/21**, worst +0.91 pp (XLE) → **F3 passes comfortably**. Against E2 (v1) the same cells read
17/21 with worst +3.66 pp (EWL) — on these instrument histories R2's own drawdown is *deeper* than
E2's, so the E2 comparison was pessimistic there and the G7/G8 comparisons were optimistic.

## Bootstraps

**E1 pre-tax** (5,000 paths, fixed 2015-09 → 2026-09 calendar, real FX/Yİ-ÜFE) — **the failing
gate, and identical to v1's numbers**:

| Arm | base median CAGR | median Δ vs R2 | mean Δ | share > 0 | cal. median Δ |
|---|---:|---:|---:|---:|---:|
| C0 | 7.811 % | — | — | — | — |
| A1 | 7.788 % | **+0.0002 pp** | +0.015 | 50.0 % | **−0.004 pp** |
| A2 | 7.744 % | **−0.034 pp** | +0.027 | 47.1 % | **−0.040 pp** |
| A3 | 7.762 % | **−0.010 pp** | +0.023 | 48.8 % | **−0.016 pp** |

A1 fails on the calibrated leg, A2/A3 on both. The distribution is symmetric noise (A1 − R2 on
2,000 re-sampled paths: mean +0.014 pp, median −0.004 pp, 49.5 % positive), i.e. there is no lucky
cell — the arm's pre-tax edge over R2 simply is not there to be resampled.

**After tax (E1, 1,000 paths)** — also identical to v1 (the control was already R2 there):

| Mode | Arm | median Δ | mean Δ | share > 0 | p05 / p95 |
|---|---|---:|---:|---:|---|
| B1-recent (progressive) | A1 | **+0.228 pp** | +0.260 | 75.5 % | −0.27 / +0.93 |
| | A2 | **+0.283 pp** | +0.386 | 73.1 % | −0.40 / +1.56 |
| | A3 | **+0.237 pp** | +0.302 | 74.7 % | −0.30 / +1.12 |
| B2 flat 20 % | A1 | **+0.093 pp** | +0.111 | 61.9 % | −0.35 / +0.65 |
| | A2 | **+0.085 pp** | +0.163 | 57.8 % | −0.56 / +1.15 |
| | A3 | **+0.074 pp** | +0.127 | 60.6 % | −0.40 / +0.80 |

Both modes pass (median ≥ 0, share ≥ 55 %) at $100k; at $1M B1 reads +0.275/79.6 %,
+0.424/75.5 %, +0.321/79.2 %. The contrast with the pre-tax bootstrap on the *same* paths is the
tax channel: B1's median (+0.23 pp) is two orders of magnitude above the pre-tax median (≈ 0 pp).

## Gates

| Arm | G1 | G2 | G3 | G4 | **G5** | G6 | G7 | G8 | G9 | AT | B1 | B2 | F3 | P | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | P | P | P | P | **F (cal. −0.004 pp)** | P | P | P | P | P | P | P | P | P | **FAIL** |
| A2 | P | P | P | P | **F (base −0.034, cal. −0.040)** | P | P | P | P | P | P | P | P | P | **FAIL** |
| A3 | P | P | P | P | **F (base −0.010, cal. −0.016)** | P | P | P | P | P | P | P | P | P | **FAIL** |

G1: A1 15.683 ≥ 15.477 base, 15.065 ≥ 14.896 calibrated (A2/A3 likewise). G2: Δ max DD +0.70 pp
≤ +1 pp. G3: 2017+ 33.18–33.91 % vs 32.71 %. G4: A1/A3 above R2 in all three sub-periods, A2 in
P1 and P3. G6: m = 1 15.94/16.20/15.99 % vs 15.75 %. G9: 5.34–5.48 vs 5.99 ≤ 2×. AT, B1, B2, F3,
P as above. Selected arm: **none**; the plateau condition holds for every arm, so the verdict is
not a plateau artefact.

## Findings

- **The corrected verdict is v1's verdict, for the right reason.** The only failure is the
  pre-tax clean bootstrap, and it is a genuine no-effect result: the arm's pre-tax edge over R2 on
  the realised history is +0.21 to +0.43 pp CAGR (not +1.0 to +1.2 pp), and on resampled paths the
  difference is centred on zero with ~50 % of paths positive. Everything the after-tax stage
  measures at $100k survives the correction (+0.44 to +1.81 pp on the three windows, +0.23 pp
  B1-recent, +0.09 pp B2) and F3/G7/G8 still pass, but the margins are now honest and small:
  +0.04 pp median in both the six windows and the pooled OOS bootstrap.
- **The E2-vs-R2 gap dominated v1's Δ columns.** In the main window R2 beats E2 by 0.80 pp CAGR;
  in the after-tax 2000 window by 0.63 pp; in the six OOS windows and the 21 ETFs the control moved
  by up to 3 pp of drawdown, so all of v1's Δ **CAGR** columns (G7, G8, F3) were optimistic while
  its Δ **max DD** columns were pessimistic — the same sign convention, opposite errors. Any
  "vs R2" statement in `docs/research/gain_band_v1.md` outside G5/B1/B2 should be read as "vs E2".
- **The mechanism, sized honestly.** Suppressing downward resizes that would sell lots with an
  embedded indexed gain remains worth something after Turkish tax at every scale tested
  (+0.36–1.98 pp on the three windows across $10k/$100k/$1M, all positive), cuts sells/yr by
  ~65 %, and does not worsen the drawdown (main window +0.70 pp; 21/21 ETFs within +3 pp;
  M5/^FTSE +0.16 pp). About half of the realised after-tax gain on the 2000 window is tax timing
  (drag 5.32 → 5.07 pp) and the rest is the higher average exposure the deferred trims produce.
- **The bracket budget (A2) is the largest arm on the realised history at every scale and
  window** (+0.79/+0.93/+1.81 pp at $100k), but it is the arm the bootstrap punishes hardest
  (G5 base −0.034 pp) and its incremental effect over A1 is parameter-sensitive: A3 keeps half the
  postponements but only about half the increment (A2's extra after-tax gain over A1 is 0.33/0.50/
  0.96 pp on the three windows; A3's is 0.01/0.02/0.04 pp).
- **Nothing is shipped.** Production R2 is unchanged; no production file was touched. If the rule
  is wanted, the natural next step is a pre-registered *after-tax-only* gate structure at the
  account scale the owner actually runs, because the pre-tax clean bootstrap is what decides this
  study and it cannot see the tax channel by construction — but that is a new pre-registration,
  not a relaxed gate.

## Caveats

- **The correction fixes a labelling error, not the study's design**: one history per cell, no
  true after-tax out-of-sample (TRY/Yİ-ÜFE start in 2000), B1 holds the TRY calendar fixed at
  2015-09 → 2026-09, B2 charges no inflation, the liquidated convention charges deferred tax at
  the window end, the rule reads the live FIFO lot book, and the model assumptions (FIFO, per-year
  netting without loss carry-forward, the 2026 tariff deflated by the previous October's Yİ-ÜFE,
  the 10 % indexation floor, instalments funded by selling) are unchanged from `after_tax_tr_v1`
  and are not tax advice.
- **A2/A3 ≡ A1 in the OOS and instrument stages** (R-USD instantiation): the F3/G7/G8 evidence
  measures the band only, by pre-registration.
- **Multiple testing.** Four arms, three gain tiers and two override levels were fixed in v1
  before its run and are unchanged here; the pre-registration of this re-run
  (`research/gain_band_v2/PREREGISTRATION.md`) fixed the control, the anchors, the gates and the
  expectations (including "the arm cells must be identical to v1's") before any arm was simulated
  on real data on this branch.
- **v1 is left in place as an archive.** Its arm numbers are valid; its control rows and all
  Δ-vs-R2 columns derived from them are not, and this report is the corrected record.

## Reproduction

```
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/gain_band_v2/screen.py --smoke
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/gain_band_v2/screen.py --harness
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/gain_band_v2/screen.py --run
```

Offline; `--run` takes about 14 minutes and writes `RESULTS.json` and `SUMMARY.txt`. Two runs gave
byte-identical `RESULTS.json` (SHA-256 `__SHA__`). Inputs are existing committed research files
only (no download), byte-identical to v1's; SHA-256 and provenance are in
`research/gain_band_v2/DATA_MANIFEST.json`. The pre-registration, `screen.py`, `tax_engine.py` and
the manifest were committed in `b6d2c0e1`, before any non-control arm was simulated on real data
on this branch.
