# Gain-scaled downsize band, confirmation on new evidence (gain_band_v3, 2026-10-15)

> Evidence: `research/gain_band_v3/` (code, PREREGISTRATION, DATA_MANIFEST, RESULTS.json, SUMMARY.txt)
> is not on `main`; it is preserved in tag `archive/research/gain-band-v3-2026-10`. Control = production R2
> (`equity_leveraged_trend_v1` 1.2.0). v2's stages are re-run here as a *reference* block only.

**Verdict: FAIL as pre-registered — on gate E3 only, and nothing is selected.** E1 (after-tax 21
ETFs under the real TRY calendar), E2 (21 rolling January starts) and the newly pre-registered
pre-tax non-inferiority gate E4 pass for **all three** arms. E3 (four fresh after-tax bootstraps,
seed 20261015, blocks 10 and 40) fails for all three arms **only on the block-10 flat-tax (B2)
run**: its share of paths with Δ > 0 is 53.4 % (A1) / 54.1 % (A2) / 53.7 % (A1u) against the
pre-registered 55 % floor, while its median Δ is ≥ 0 (+0.029 / +0.039 / +0.029 pp) and **every
other E3 cell clears both thresholds** (block-10 progressive 70.8–71.9 %, block-40 progressive
76.7–78.8 %, block-40 flat 63.7–67.0 %). Production R2 is unchanged; no arm is proposed.

The band's after-tax edge is therefore **confirmed on all of v2's blind spots** — the 21 ETFs
under R-INDEX (median +0.098 pp A1, +0.117 pp A1u, +0.119 pp A2; 19–20 of 21 ETFs positive for
A1/A1u), 21 rolling start dates (+0.44 to +2.25 pp, 21/21 positive), the $30k/$300k scales, and a
pre-tax cost of ≈ 0 pp (median +0.000 to −0.040 pp) with ≤ +0.70 pp of main-window drawdown — but
the **flat-20 % tax mode at a 10-session block length is the one new cell where the sign is not
majority-positive**, and the pre-registered gate demanded majority-positive there too. See
"Caveats" for why that outcome is a threshold miss rather than a sign flip.

## 1. Question and what is new versus v2

`gain_band_v2` failed only on its pre-tax clean bootstrap (G5: median Δ ≥ 0; got −0.4 bp A1 to
−4 bp A2) while every after-tax gate passed. The band is a **tax-timing device** — suppressing
downward resizes that would realise an embedded indexed TRY gain cuts taxable FIFO realisations
from ~31/yr to ~10/yr — so its pre-tax effect is expected to be ≈ 0 and G5 cannot see the tax
channel by construction. v3 re-asks the question on evidence v2 never looked at, with the
non-inferiority margin on the pre-tax leg **chosen after seeing v2's G5** (disclosed in
`PREREGISTRATION.md` §0) and the verdict resting on the new stages.

## 2. Method

Arms, thresholds and parameters are v2's, unchanged and untuned:

| Arm | Rule |
|---|---|
| **C0** | production R2: constant 0.5 pp band, no other change (control) |
| **A1** | gain-scaled band on downward resizes: `B_down` = 0.5 / 3 / 8 pp at an embedded FIFO indexed TRY gain fraction `g_frac` ≤ 1 % / ≤ 5 % / > 5 %; entries, exits to zero and up-resizes keep 0.5 pp |
| **A2** | A1 + bracket budget: while YTD realised rule gain ≥ 1,000,000 × scale(year) TRY (the year's deflated 27 %-bracket floor), a discretionary downward resize is postponed; hard override at 15 pp, on the E2-off decision close, or target ≤ 0 |
| **A1u** | A1's tiers and thresholds with `g_frac` in **USD only** (FIFO lots, USD cost basis, no FX, no Yİ-ÜFE indexation, no per-lot floor other than gain ≥ 0) — computable from the bot's own fill ledger |

Every arm runs **production R2's target series**; only the fill decision differs. One sequential
engine (`tax_engine.py`, a byte copy of `tax_harvest_v2/tax_engine.py`) drives every stage:
progressive 2026 tariff deflated by the previous October's Yİ-ÜFE, FIFO lot matching with the
GVK mük. 81 indexation relief (10 % floor), instalments funded by selling, liquidated at the
window end, cost 4 bps × m × |Δw|, m = 2, synthetic QLD* (daily-reset 2× on QQQ total return,
0.95 %/yr), financing DTB3 + 0.0 % (base) / +0.75 % (calibrated).

**Harness (PASS, 0 checks outside tolerance).** The control label the run uses reproduces the R2
anchors: pre-tax 15.48 / 14.90 % CAGR (15.75 / 15.17 % at m = 1), max DD 46.76 / 46.88 %,
turnover 5.9915; after tax at $100k 10.160 / 14.706 / 22.886 % CAGR with max DD 46.758 / 43.784 /
36.243 %; the B1/B2 bootstrap medians 3.5155 % / 3.9678 %; `C0 engine == simulate_band` to
1.3e-13; and the control's NAV path differs from the pure-E2 anchor (14.67 %/14.10 %).

**v2 reference block (PASS).** All of v2's stages were re-run here with v2's seeds and paths, and
**1,608 published cells for C0/A1/A2/A3 match `research/gain_band_v2/RESULTS.json` exactly**
(0 mismatches at 2e-6, max |diff| 5e-7): the pre-tax main window, the R-USD row, the after-tax
windows at $10k/$100k/$1M, the m = 4 stress, the six OOS windows, the 21 R-USD instruments, the
clean bootstrap and the pooled OOS bootstrap. That block is a reproduction check, not evidence.

## 3. Per-arm results, E1–E5

| Arm | Pre-tax CAGR base / cal. | max DD base / cal. | E1: 21 ETFs, R-INDEX, median Δ (share > 0) | E2: 21 January starts, median Δ (share > 0) | E3: 4 fresh bootstraps, median Δ / share > 0 | E4: pre-tax boot median Δ base / cal. (Δ max DD) | E5: A1u ≈ A1? |
|---|---:|---:|---:|---:|---|---:|---|
| **C0 (R2)** | 15.48 % / 14.90 % | 46.76 % / 46.88 % | — | — | — | — | — |
| **A1** | 15.68 % / 15.06 % | 47.46 % / 47.58 % | **+0.098 pp** (95.2 %, 20/21) | **+0.595 pp** (100 %, 21/21) | +0.184/71.9 %, **+0.029/53.4 %**, +0.268/78.8 %, +0.130/67.0 % | **+0.000 / −0.004** (+0.70 pp) | — |
| **A2** | 15.90 % / 15.29 % | 47.46 % / 47.58 % | **+0.119 pp** (81.0 %, 17/21) | **+1.116 pp** (100 %, 21/21) | +0.244/70.8 %, **+0.039/54.1 %**, +0.377/76.7 %, +0.164/63.7 % | **−0.034 / −0.040** (+0.70 pp) | — |
| **A1u** | 15.72 % / 15.12 % | 47.19 % / 47.31 % | **+0.117 pp** (90.5 %, 19/21) | **+0.543 pp** (100 %, 21/21) | +0.186/71.3 %, **+0.029/53.7 %**, +0.265/76.8 %, +0.134/66.7 % | **+0.001 / −0.005** (+0.43 pp) | 52/6,679 sessions differ (0.78 %) |

E3 cells are ordered block-10 progressive, **block-10 flat**, block-40 progressive, block-40 flat;
the bold cell is the one that fails.

### E1 — after tax on the 21 quality-passing ETFs under R-INDEX

$100k, calibrated, m = 2, liquidated; own E2 + own R2 early path, own synthetic QLD* leg on each
ETF's own history, real CBRT USD/TRY + Yİ-ÜFE market, start = max(warm-up, 2000-03-09).
**Gate: median Δ > 0 and share Δ > 0 ≥ 60 % → PASS for all three arms.** A1: 20/21 positive
(only XLV −0.124 pp); A1u: 19/21 (XLV −0.093; DIA −0.035); A2: 17/21 (DIA, XLP, XLU, XLV
negative, XLV −0.110). Median Δ across the universe runs +0.098 (A1) / +0.117 (A1u) / +0.119 pp
(A2), against a **median pre-tax** Δ of +0.063 / +0.078 / +0.081 pp — i.e. the after-tax effect
is ~1.5× the pre-tax effect on the same paths, the tax-timing channel showing up. The Δ max DD is
small (A1u: median +0.22 pp, worst +0.64 pp, best −0.85 pp), so the gate's CAGR evidence is not a
drawdown trade.

### E2 — 21 rolling January starts

After tax at $100k, base, m = 2, each start running to 2026-09-29. **Gate: Δ > 0 in ≥ 70 % of
starts and median Δ > 0 → PASS, 21/21 positive for every arm.** Per-start Δ ranges +0.436 to
+1.035 pp (A1), +0.396 to +0.991 pp (A1u), +0.830 to +2.252 pp (A2). The effect is monotone in
the start year (largest for late starts, where the tax-free-allowance/deflation asymmetry has
more compounding room) but has **no negative start**, which is the stringest form of the
robustness question v2 could not answer.

### E3 — fresh after-tax bootstraps (the failing gate)

Seed 20261015, 1,000 paths, fixed 2015-09-01 → 2026-09-30 calendar with its realised FX/Yİ-ÜFE,
QQQ market returns block-resampled, $100k. **Gate: each of the four runs has median Δ ≥ 0 AND
share > 0 ≥ 55 %.**

| Run | A1 median Δ (share > 0) | A2 | A1u | Gate |
|---|---:|---:|---:|---|
| block 10, B1-recent (progressive) | +0.184 pp (71.9 %) | +0.244 pp (70.8 %) | +0.186 pp (71.3 %) | PASS |
| block 10, **B2-flat (20 %)** | **+0.029 pp (53.4 %)** | **+0.039 pp (54.1 %)** | **+0.029 pp (53.7 %)** | **FAIL** |
| block 40, B1-recent (progressive) | +0.268 pp (78.8 %) | +0.377 pp (76.7 %) | +0.265 pp (76.8 %) | PASS |
| block 40, B2-flat (20 %) | +0.130 pp (67.0 %) | +0.164 pp (63.7 %) | +0.134 pp (66.7 %) | PASS |

### E4 — pre-tax non-inferiority (margin chosen after v2's G5; disclosed)

Clean bootstrap of v2 (seed 20261011, block 20, 5,000 pre-tax paths, $100k so the budget is
active) **and** the main-window pre-tax max DD. **Gate: median Δ ≥ −0.10 pp base and calibrated,
and Δ max DD ≤ +1.0 pp base and calibrated → PASS for all three arms.** A1 +0.000 / −0.004 pp,
A1u +0.001 / −0.005, A2 −0.034 / −0.040 — the same values v2's failing G5 produced, now inside
the margin. Δ max DD +0.70 pp (A1, A2), **+0.43 pp (A1u)**.

### E5 — report only

After tax, liquidated, base, m = 2:

| Window | $30k C0 | A1 Δ | A2 Δ | A1u Δ | $300k C0 | A1 Δ | A2 Δ | A1u Δ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | 10.98 % | +0.408 | +0.755 | +0.413 | 9.55 % | +0.497 | +0.894 | +0.488 |
| 2010-01 → end | 15.90 % | +0.399 | +0.817 | +0.374 | 14.01 % | +0.456 | +1.019 | +0.406 |
| 2017-01 → end | 24.36 % | +0.787 | +1.321 | +0.754 | 22.07 % | +0.889 | +2.012 | +0.850 |

**A1u vs A1 fill decisions** on the main path (6,679 sessions, base, m = 2, $100k, R-INDEX,
pre-tax): **52 sessions differ (0.78 %)** — 16 where A1 sells and A1u holds, 16 where A1 holds and
A1u sells, and 20 in the entry/up-resize/exit decisions. Both arms make 277 resizes. The USD-only
rule and the indexed TRY rule are therefore **behaviourally near-identical on this history** (the
indexation relief rarely moves a lot across a 1 %/5 % tier boundary), which is the practical
argument for shipping A1u rather than A1 if either is ever shipped.

## 4. Gates and selection

| Arm | E1 | E2 | E3 | E4 | Result |
|---|---|---|---|---|---|
| A1 | P | P | **F** (block-10 flat share 53.4 % < 55 %) | P | **FAIL** |
| A2 | P | P | **F** (54.1 % < 55 %) | P | **FAIL** |
| A1u | P | P | **F** (53.7 % < 55 %) | P | **FAIL** |

Selected arm: **none** — the selection rule (prefer A1u if its 2000-window $100k after-tax Δ is
≥ 70 % of A1's, else the highest 2000-window after-tax CAGR among passing arms) never runs,
because no arm passes E1–E4. For the record, A1u's 2000-window Δ (+0.455 pp) is 99.5 % of A1's
(+0.457 pp), so it would have been preferred had it passed; A2's is +0.791 pp.

### Pre-registered expectations, checked

1. *The reference block reproduces v2 exactly* — verified: 1,608 cells, 0 mismatches.
2. *E4's A1/A2 medians land where v2's G5 landed* — confirmed (+0.000/−0.004 and −0.034/−0.040).
3. *A1u resembles A1, deferring slightly less* — confirmed: after-tax Δ at $100k 2000 window
   +0.455 vs +0.457 pp; 1,289 vs 1,293 postponements; and A1u's drawdown cost is **lower**
   (+0.43 vs +0.70 pp), i.e. the USD-only rule is not a degraded A1 on this evidence.
4. *A2 is the largest arm on the realised history but the bootstrap punishes it most* — confirmed
   (+0.791/+0.933/+1.814 pp at $100k; E4 −0.034/−0.040 pp; E3 the weakest shares at block 10).

## 5. Findings

- **The after-tax gain is real on every new evidence family.** E1 (a 21-instrument, real-TRY-calendar
  panel), E2 (21 non-overlapping-ish start dates) and the $30k/$300k scales all show a positive
  median and a large majority of positive cells, with the after-tax Δ roughly 1.5× the pre-tax Δ
  on the same paths. The pre-tax cost is ≈ 0 pp (E4), i.e. the device is **tax-neutral before tax
  and positive after tax**, exactly the mechanism v2 described.
- **The one failing cell is the flat-20 % tax mode at the shortest block length.** With no
  indexation relief and a flat 20 % rate, the tax-timing benefit is structurally smaller — v2's
  own B2-flat at block 20 was also the weakest cell (+0.093 pp, 61.9 %). At block 10 the
  distribution is centred at +0.029/+0.039 pp with **53.4–54.1 %** of paths positive: the median
  is on the right side of zero, the majority is 1–1.6 pp short of the pre-registered 55 % floor.
  This is a threshold miss on a majority-vote statistic, not a sign reversal, and it is reported as
  a FAIL rather than re-interpreted.
- **A1u is the implementable arm and behaves like A1** (52/6,679 differing fill decisions, equal
  resize counts, lower drawdown cost, essentially equal after-tax Δ). Nothing is selected because
  the gate is all-or-nothing, but A1u is the natural arm to re-test under a fresh
  pre-registration should one be wanted.
- **The bracket budget (A2) remains the largest arm on realised history** (+0.79/+0.93/+1.81 pp at
  $100k; +2.01 pp at $300k on the 2017 window) and the one the bootstrap likes least — the same
  asymmetry v2 found.

## 6. Implementation notes (what the selected arm would need)

**No arm is selected and nothing is shipped**; production R2 is unchanged and no production file
was touched. For completeness, the state the implementable arm (A1u) needs if it is ever
re-proposed:

- **A lot ledger** over the QLD position, FIFO-ordered, each lot carrying `units` and its **USD
  cost basis per unit** (buy price including fees). No purchase-date FX rate, no Yİ-ÜFE month and
  no indexation table are needed — that is the whole point of A1u.
- **Decision-time gain fraction**: on a candidate downward resize of `Δunits` at the next open
  `p` with the trade's fee `f`, `per_unit = (Δunits·p − f)/Δunits`,
  `gain = Σ_lots min(take, lot_units)·(per_unit − lot_cost_per_unit)` over the FIFO prefix,
  `g_frac = max(0, gain)/proceeds`, `proceeds = Δunits·per_unit`.
- **Thresholds**: `B_down` = 0.5 pp if `g_frac ≤ 1 %`, 3 pp if `1 % < g_frac ≤ 5 %`, 8 pp if
  `g_frac > 5 %`; `B_up` = 0.5 pp for entries, exits to zero and up-resizes. A downward resize is
  suppressed iff `|target − held| ≤ B_down`; the target is re-evaluated at every close, so a
  suppressed resize is attempted again the next day.
- **What it must not need**: a TRY calendar, a CBRT rate feed or a Yİ-ÜFE series. A2's budget (a
  TRY-bracket device) would need the year's Yİ-ÜFE-deflated ceiling and a YTD realised-gain
  accumulator, and would be strictly more state than A1u.

## 7. Caveats

- **One history per cell**; no true after-tax out-of-sample (the TRY/Yİ-ÜFE calendar starts 2000,
  so the after-tax stages cannot be pushed into the pre-2000 OOS windows at all). E2's rolling
  starts are the substitute for a start-date OOS, E3's block/seed variations for path OOS, E1 for
  instrument OOS.
- **E3's flat mode charges no inflation** and holds the TRY calendar fixed at 2015-09 → 2026-09,
  so the flat-tax runs are the least favourable to a device whose benefit is indexation relief.
  The block-10 flat failure should be read with that in mind (and it is still reported as a FAIL).
- **The E4 margin (−0.10 pp) was chosen after seeing v2's G5**, i.e. after v2's data existed. It is
  disclosed in `PREREGISTRATION.md` §0 and the gate table; E1–E3 are the deciding evidence and are
  all new.
- **The liquidated convention** charges deferred tax at the window end; the unliquidated view is
  reported in RESULTS.json and is higher throughout. FIFO, per-year netting without loss
  carry-forward, the 2026 tariff deflated by the previous October's Yİ-ÜFE and the 10 %
  indexation floor are the model's assumptions, not tax advice.
- **In the reference block**, the pre-tax reporting row of the after-tax windows and of the m = 4
  cost stress runs at capital 1.0 (v2's convention), so A2/A3's bracket budget is inert there;
  those fields are reproduction cells only and no v3 gate reads them.
- **A2 ≡ A1 and A1u ≡ A1 in the R-USD stages** (OOS windows, pre-tax ETF reference) by
  construction, so those stages measure the band, not the USD simplification.

## 8. Reproduction

```
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/gain_band_v3/screen.py --smoke
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/gain_band_v3/screen.py --harness
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/gain_band_v3/screen.py --run
```

Run them from the worktree root, on branch `research/gain_band_v3`. `--run` was executed twice with
byte-identical `RESULTS.json` and `SUMMARY.txt`; the reference block's 1,608-cell comparison
against `research/gain_band_v2/RESULTS.json` is part of `RESULTS.json`
(`v2_reference_check`).
