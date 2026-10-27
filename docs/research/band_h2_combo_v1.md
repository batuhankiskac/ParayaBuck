# A1u + H2 (band_h2_combo_v1, 2026-10-15): the December deferral still adds on top of the band

> Evidence: `research/band_h2_combo_v1/` (code, PREREGISTRATION, DATA_MANIFEST, RESULTS.json,
> SUMMARY.txt) is not on `main`; it is preserved in tag `archive/research/band-h2-combo-v1-2026-10`. Control = production R2
> (`equity_leveraged_trend_v1` 1.2.0). Evidence of the two parent campaigns is reproduced
> cell-for-cell inside this run (see §3).

**Verdict: ADOPT K (A1u + H2).** Every pre-registered gate passes with room, and H2's measured
contribution is **fully preserved** on top of the band: after tax at $100k, `K − A1u` is
**+0.139 / +0.133 / +0.271 pp** on the 2000 / 2010 / 2017 windows (K1), the clean bootstrap gives
**+0.042 pp median on 61.0 %** of B1-recent paths and **+0.025 pp on 59.5 %** of B2-flat paths
(K2, floors: ≥ 0 pp / ≥ 50 %), K's pre-tax max DD is **0.52 pp *below*** A1u's (K3), and the 21
January rolling starts are **21/21 positive** for `K − A1u` (+0.115 to +0.395 pp, median
+0.153 pp — K4). Against the incumbent, K is *sound and better than either device alone*: pre-tax
CAGR 15.76 % base / 15.18 % calibrated (C0 15.48 / 14.90) with **max DD 46.67 % / 46.79 %**, i.e.
0.09 pp *below* C0's, and turnover 5.46/yr (0.911 × C0, gate S3). After tax at $100k K is
**10.75 / 15.24 / 23.97 %** versus C0's 10.16 / 14.71 / 22.89 % (+0.593 / +0.529 / +1.086 pp, for
reference A1u alone is +0.455 / +0.396 / +0.815).

Three further results are worth the owner's attention:

- **H2 alone is *worse* than A1u alone** (H2 − A1u after tax −0.311 / −0.263 / −0.540 pp; bootstrap
  median −0.143 pp on 35.8 % of B1 paths), yet **H2 adds on top of A1u**. The two devices touch
  almost disjoint trade sets (A1u suppresses ~1,300 downward resizes; K's December window defers
  71 of the resizes the band would otherwise let through), so the contributions are additive:
  H2's standalone edge vs C0 (+0.143/+0.134/+0.275 pp) transfers to the banded strategy
  essentially in full (+0.139/+0.133/+0.271 pp). Choosing between A1u and H2 is a false choice —
  K dominates both.
- **K repairs the one cell that killed the parent campaign.** `gain_band_v3`'s verdict rested on
  E3's block-10 flat-20 %-tax bootstrap, where A1u's share of positive paths was 53.7 % against a
  55 % floor (median +0.029 pp). This run reproduces that cell exactly for A1u, and **K gets
  +0.077 pp on 58.3 %** there; K also clears the 55 % floor in all four E3 runs (58.3–77.2 %)
  while its edge over A1u is itself majority-positive everywhere (58.8–61.6 %).
- **The pair is cheaper and calmer than either device on its own.** K's turnover is 5.46/yr
  (A1u 5.49, C0 5.99), its main-window drawdown is the *lowest* of the four arms (46.67 % base,
  47.19 % for A1u, 46.76 % for C0), and the m = 4 cost stress keeps every sign
  (+0.624/+0.591/+1.132 pp vs C0 at $100k).

Nothing was shipped; production R2 is unchanged and no production file was touched.

## 1. Question and what is new

`gain_band_v3` verified the USD gain-scaled downsize band (A1u) on new evidence and failed only
one pre-registered bootstrap cell; `tax_harvest_v2` verified the year-end vol-resize deferral (H2)
on top of R2 and failed only its plateau gate. Both are tax-timing devices operating on *which
downward resizes fill*, so they can overlap. This study asks the question the owner actually
faces: **does H2 add anything once the band is in place, and does the pair stay sound?** Nothing
in `docs/research/INDEX.md` measures that composition.

## 2. Method

**Control (C0) = production R2, bit for bit**, through the single sequential engine (`tax_engine.py`,
a byte copy of `gain_band_v3/tax_engine.py`): R2's target series (E2 2-of-3 vote on the QQQ close +
the 0.5·vt early re-entry leg, sized `vt = min(1, 0.35/vol20)`, cap 0.98), decide on the completed
close, fill at the next open, symmetric 0.5 pp band, entries and exits to zero always fill, idle
cash zero, cost 4 bps × m × |Δw|, synthetic QLD* = daily-reset 2× on QQQ total return, 0.95 %/yr
expense, financing DTB3 + {base 0.0 %, calibrated 0.75 %}/yr.

| Arm | Rule |
|---|---|
| **C0** | production R2 (control). |
| **A1u** | `gain_band_v3`'s A1u verbatim: `g_frac` = max(0, Σ_FIFO take·(per_unit − lot USD cost)) / USD proceeds over the live lot book (no FX, no Yİ-ÜFE indexation), and `B_down` = 0.5 / 3 / 8 pp at `g_frac ≤ 1 % / ≤ 5 % / > 5 %`; a downward resize fills iff `drift > B_down`. Entries, exits to zero and up-resizes keep 0.5 pp. |
| **H2** | `tax_harvest_v2`'s H2 verbatim: a fill that would be a downward resize (`target > 0`, `target < held weight`) in one of the **last 10 December sessions** is postponed; exits to zero and up-resizes always fill; the ordinary rule is re-evaluated at every later close. |
| **K** | A1u + H2, composed as pre-registered: the **H2 December window is tested first** (if the fill session is in the last 10 December sessions and `0 < target < held weight`, postpone), **otherwise A1u's band decides**; exits to zero are never postponed. |

The two possible orderings block the same trades (H2's test is about the session, A1u's about the
size), so only the counter differs; implementing H2-first keeps th2's code path inside the window.

**Stages** (all fixed in `PREREGISTRATION.md` §5 before any non-control arm ran on real data):
pre-tax main window 2000-03-09 → 2026-09-29 (6,679 sessions, m = 1/2/4, base/calibrated, plus the
R-USD row); after tax at $100k (gate capital) and $10k/$30k/$300k/$1M on the three windows;
the clean bootstrap (seed 20261011, block 20, 1,000 after-tax / 5,000 pre-tax paths, $100k/$1M,
B1-recent and B2-flat, fixed 2015-09 → 2026-09 calendar with realised USD/TRY and Yİ-ÜFE); E1
(after-tax 21 ETFs under R-INDEX), E2 (21 January rolling starts), E3 (fresh bootstraps, seed
20261015, blocks 10/40), E4 (pre-tax leg + main-window DD), E5 (report-only decisions, extra
capitals, m = 4 stress), and the six `ewma_band_v1` OOS windows. E1–E4 and OOS are the parent
campaign's stages, used here as **reporting** cells for every arm against C0.

**Gates** (pre-registered, §6): `K1` after-tax $100k `K − A1u ≥ 0` in all three windows; `K2`
clean bootstrap B1-recent **and** B2-flat at $100k median `K − A1u ≥ 0` with share of positive
paths `≥ 50 %`; `K3` pre-tax main-window max DD `K ≤ A1u + 1.0 pp` (both financings); `K4`
`K − A1u ≥ 0` in `≥ 60 %` of the 21 January starts; plus soundness against the incumbent `S1`
pre-tax CAGR `≥ C0 − 0.10 pp`, `S2` max DD `≤ C0 + 1.0 pp`, `S3` turnover `≤ 2 × C0`.
**Adopt K iff K1 ∧ K2 ∧ K3 ∧ K4 ∧ S1 ∧ S2 ∧ S3, else A1u alone.** No gate, arm or parameter was
touched after the run.

## 3. Reproduction (must pass before any number is read)

`--harness` PASS, 0 checks outside tolerance. The control reproduces the R2 anchors: pre-tax
15.4766 / 14.8961 % CAGR (m = 2) and 15.7538 / 15.1719 % (m = 1), max DD 46.7584 / 46.8797 %,
turnover 5.9915; after tax at $100k 10.160 / 14.706 / 22.886 % with max DD 46.758 / 43.784 /
36.243 %; B1/B2 bootstrap medians 3.5155 / 3.9678 %; `C0 engine == simulate_band` to 1.3e-13.

The **deep reference block** then reproduces the two parent campaigns with the *same* code:
**1,395 cells, 0 mismatches at 2e-6, max|diff| 5.0e-07** (`RESULTS.json` stores `round(x, 6)`, so
the stored max diff is 0.0). Cells compared: every numeric leaf of every A1u cell of
`research/gain_band_v3/RESULTS.json` that this run produces (pre-tax main window base/calibrated
m = 1/2/4 + the R-USD row, after-tax windows at all five capitals, the m = 4 cost stress, the
seed-20261011 clean bootstrap, the six OOS windows, the 21 R-USD instruments, E1/E2/E3 —
**including the failing E3 block-10 flat cell, reproduced as 53.7 % / +0.029 pp**), and H2's
published cells from `H2_ANCHORS.json` (pre-tax main window m = 1/2 base/calibrated, the three
after-tax $100k windows, the m = 4 cost stress, the seed-20261011 bootstrap at $100k/$1M) — all
exact. Disclosed convention differences: th2 publishes its pre-tax main window **liquidated**
(compared against `cagr_liquidated` here; the nav-based convention sits 0.003 pp higher at
m = 2), and the postponement counter is split into `blocked_band` + `deferred` (their sum is th2's
`deferrals` for the H2-only arm).

## 4. Per-arm results

### Pre-tax main window, m = 2, R-INDEX (2000-03-09 → 2026-09-29)

| Arm | CAGR base | CAGR cal. | max DD base | max DD cal. | m = 1 base | turnover/yr | sells/yr | band blocks | Dec. deferrals |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **C0 (R2)** | 15.4766 % | 14.8961 % | 46.758 % | 46.880 % | 15.7538 % | 5.9915 | 29.56 | 0 | 0 |
| **A1u** | 15.7213 % | 15.1192 % | 47.188 % | 47.310 % | 15.9750 % | 5.4863 | 10.43 | 1,289 | 0 |
| **H2** | 15.5171 % | 14.9359 % | 46.254 % | 46.379 % | 15.7924 % | 5.9482 | 28.65 | 0 | 51 |
| **K** | **15.7649 %** | **15.1785 %** | **46.670 %** | **46.795 %** | **16.0173 %** | **5.4561** | **10.17** | 1,239 | 71 |

m = 4 (base): C0 14.9241, A1u 15.1916, H2 14.9684, **K 15.2541** %. K is the highest-CAGR *and*
lowest-drawdown, lowest-turnover arm at both cost levels.

### After tax at $100k (progressive 2026 tariff, base financing, m = 2, liquidated)

| Window | Arm | After tax | Δ vs C0 | Δ vs A1u | max DD | tax paid (USD) | postponements (band / Dec.) |
|---|---|---:|---:|---:|---:|---:|---|
| 2000-03 → 2026-09 | C0 | 10.160 % | — | −0.455 | 46.758 % | 766,160 | 0 (0/0) |
| | A1u | 10.615 % | +0.455 | — | 47.188 % | 819,008 | 1,896 (1,896/0) |
| | H2 | 10.303 % | +0.143 | −0.311 | 46.254 % | 775,620 | 53 (0/53) |
| | **K** | **10.753 %** | **+0.593** | **+0.139** | **46.670 %** | 830,903 | 1,917 (1,836/81) |
| 2010-01 → end | C0 | 14.706 % | — | −0.396 | 43.784 % | 553,536 | 0 (0/0) |
| | A1u | 15.102 % | +0.396 | — | 44.414 % | 562,805 | 1,462 (1,462/0) |
| | H2 | 14.840 % | +0.134 | −0.263 | 44.072 % | 551,338 | 32 (0/32) |
| | **K** | **15.235 %** | **+0.529** | **+0.133** | **44.627 %** | 562,873 | 1,473 (1,413/60) |
| 2017-01 → end | C0 | 22.886 % | — | −0.815 | 36.243 % | 360,297 | 0 (0/0) |
| | A1u | 23.701 % | +0.815 | — | 35.823 % | 370,231 | 810 (810/0) |
| | H2 | 23.161 % | +0.275 | −0.540 | 34.863 % | 360,065 | 19 (0/19) |
| | **K** | **23.972 %** | **+1.086** | **+0.271** | **34.977 %** | 371,487 | 816 (787/29) |

At $10k / $30k / $300k / $1M the signs and ordering are identical: K − C0 is
+0.491/+0.438/+0.878, +0.543/+0.501/+0.993, +0.638/+0.543/+1.125 and +0.682/+0.541/+1.133 pp on
the three windows, and K − A1u stays positive everywhere. The m = 4 cost stress at $100k: K
+0.624/+0.591/+1.132 pp vs C0 (A1u +0.476/+0.445/+0.862, H2 +0.145/+0.134/+0.276).

### Clean after-tax bootstrap at $100k (seed 20261011, block 20, 1,000 paths)

| Mode | Arm | median CAGR | Δ vs C0 (share > 0) | Δ vs A1u (share > 0) | p05 / p95 vs A1u |
|---|---|---:|---|---|---|
| B1-recent | C0 | 3.5155 % | — | — | — |
| | A1u | 3.7784 % | +0.226 pp (75.4 %) | — | — |
| | H2 | 3.6013 % | +0.055 pp (67.4 %) | −0.143 pp (35.8 %) | −0.84 / +0.50 |
| | **K** | **3.8577 %** | **+0.286 pp (75.0 %)** | **+0.042 pp (61.0 %)** | −0.29 / +0.59 |
| B2-flat | C0 | 3.9678 % | — | — | — |
| | A1u | 4.0369 % | +0.095 pp (62.4 %) | — | — |
| | H2 | 3.9682 % | +0.045 pp (64.4 %) | −0.029 pp (46.2 %) | −0.66 / +0.56 |
| | **K** | **4.0376 %** | **+0.131 pp (64.0 %)** | **+0.025 pp (59.5 %)** | −0.35 / +0.54 |

At $1M: K − A1u +0.045 pp (63.0 %) progressive, +0.025 pp (59.5 %) flat; K − C0 +0.349 (78.2 %) /
+0.131 (64.0 %).

### E1–E4 for every arm against C0 (parent campaign's stages, reporting only)

| Arm | pre-tax CAGR base / cal. | pre-tax max DD base / cal. | E1: 21 ETFs median Δ (share > 0) | E2: 21 January starts median Δ (share > 0) | E3: 4 fresh bootstraps, median Δ (share > 0) b10-prog / b10-flat / b40-prog / b40-flat | E4: pre-tax boot median base / cal. (Δ max DD) | OOS median Δ |
|---|---:|---:|---|---|---|---|---:|
| A1u | 15.72 / 15.12 % | 47.19 / 47.31 % | +0.117 (90.5 %, 19/21) | +0.543 (100 %, 21/21) | +0.186 (71.3 %) / **+0.029 (53.7 %)** / +0.265 (76.8 %) / +0.134 (66.7 %) | +0.001 / −0.005 (+0.43 pp) | +0.040 |
| H2 | 15.52 / 14.94 % | 46.25 / 46.38 % | +0.035 (81.0 %, 17/21) | +0.151 (100 %, 21/21) | +0.053 (62.8 %) / +0.040 (62.6 %) / +0.064 (69.9 %) / +0.054 (70.2 %) | +0.001 / +0.001 (−0.50 pp) | +0.028 |
| **K** | **15.76 / 15.18 %** | **46.67 / 46.79 %** | **+0.158 (90.5 %, 19/21)** | **+0.697 (100 %, 21/21)** | **+0.241 (70.6 %) / +0.077 (58.3 %) / +0.317 (77.2 %) / +0.173 (68.9 %)** | −0.007 / −0.012 (−0.09 pp) | **+0.065** |

The bold E3 cell is the one that failed `gain_band_v3`: A1u's 53.7 % (median +0.029 pp) is
reproduced exactly, and K clears it (58.3 %, +0.077 pp). K is also positive against **A1u** in all
four E3 runs (+0.032 to +0.034 pp, 58.8–61.6 % of paths) — an out-of-sample-style robustness check
this campaign did not have to run but reports.

### What the two devices actually do (E5, main path, pre-tax, $100k)

| Pair | sessions differ | A sells / B holds | A holds / B sells |
|---|---:|---:|---:|
| K vs A1u | 19 (0.28 %) | 2 | 9 |
| H2 vs C0 | 45 (0.67 %) | 3 | 27 |
| A1u vs C0 | 777 (11.63 %) | 9 | 517 |

Downward resizes + exits per arm: C0 785, H2 761, A1u 277, **K 270**. The composition is almost
invisible at the decision level (19 sessions in 26.5 years) yet worth ~0.13–0.27 pp of after-tax
CAGR — the deferral only has to move a handful of December fills, and each one carries a large
deferred indexation-aware gain.

## 5. Gates

| Gate | Requirement | Result | Value |
|---|---|---|---|
| **K1** | after-tax $100k `K − A1u ≥ 0` in all three windows | **PASS** | +0.139 / +0.133 / +0.271 pp (min +0.133) |
| **K2** | B1-recent and B2-flat at $100k: median `≥ 0` and share > 0 `≥ 50 %` | **PASS** | +0.042 pp / 61.0 % (B1), +0.025 pp / 59.5 % (B2) |
| **K3** | pre-tax main max DD `K ≤ A1u + 1.0 pp` (base and cal.) | **PASS** | −0.52 / −0.51 pp (K *better*) |
| **K4** | `K − A1u ≥ 0` in `≥ 60 %` of the 21 January starts | **PASS** | 21/21 (100 %), median +0.153, min +0.115, max +0.395 pp |
| **S1** | pre-tax CAGR `K ≥ C0 − 0.10 pp` (base and cal.) | **PASS** | +0.288 / +0.282 pp |
| **S2** | pre-tax max DD `K ≤ C0 + 1.0 pp` (base and cal.) | **PASS** | −0.089 / −0.085 pp |
| **S3** | turnover `K ≤ 2 × C0` | **PASS** | ratio 0.911 |

**Adopted: K.** Reported for completeness: A1u alone vs C0 keeps its parent-campaign profile
(E1 90.5 %, E2 21/21, E3 with the 53.7 % cell, E4 +0.001/−0.005 pp); H2 alone vs C0 passes the
same E1–E4 block (E1 81.0 %, E2 21/21, E3 62.6–70.2 %, E4 +0.001/+0.001) but is *dominated by
A1u* on every after-tax window and on both bootstrap modes.

## 6. Findings

1. **H2's contribution survives the band, at full size.** `K − A1u` on the three windows
   (+0.139/+0.133/+0.271 pp) matches H2's standalone `H2 − C0` (+0.143/+0.134/+0.275 pp) to within
   0.005 pp, and its bootstrap medians (+0.042/+0.025 pp) are the same order as H2's standalone
   ones (+0.055/+0.045 pp). The devices are complementary, not substitutes: the ~1,300 resizes the
   band suppresses are mostly *not* December resizes, and the December resizes the band lets
   through (71 over 26.5 years) are exactly what H2 defers.
2. **But H2 alone loses to A1u alone.** On the bootstrap H2 − A1u is −0.143 pp (35.8 % of B1
   paths) and in the windows −0.31 to −0.54 pp, because the band is the far stronger tax-timing
   device (A1u cuts sells from 30.7 to 10.9/yr, H2 only to 29.8). So the earlier campaigns'
   question was the wrong one to ask in isolation: the right comparison is A1u vs A1u + H2, and
   that one K wins.
3. **K removes the parent campaign's only failing cell.** `gain_band_v3` failed on E3 block-10
   flat (A1u 53.7 % < 55 %); K scores 58.3 % (median +0.077 pp) there and ≥ 58.3 % in all four E3
   runs, with `K − A1u` itself positive in 58.8–61.6 % of paths in that stage. On the evidence of
   this run, the earlier FAIL would not have recurred for K.
4. **The pair is a little cheaper and calmer than either device.** Turnover 5.46/yr (both devices
   alone sit at 5.49 and 5.95), main-window max DD 46.67 % — the lowest of the four arms and
   0.09 pp below the incumbent's — and the breadth blocks keep C0-like drawdowns (worst E1
   instrument Δ max DD +0.67 pp on EWL, worst OOS Δ max DD +0.24 pp on M1, worst OOS Δ CAGR
   −0.038 pp on M5).
5. **The breadth evidence is unchanged or better.** 19/21 ETFs positive (median +0.158 pp, up
   from A1u's +0.117), 21/21 rolling starts positive (K − A1u positive in all 21 as well), six OOS
   windows with a median Δ of +0.065 pp (A1u +0.040, H2 +0.028), and the m = 4 stress keeping
   every sign.
6. **The one cell where H2 alone is not majority-positive vs C0** is unchanged by adding it to the
   band: H2's own E3 block-10 flat share is 62.6 % (positive), so this is not an issue for K.

## 7. Caveats

- **One history per cell.** The after-tax window deltas are single paths; the counterweight is the
  1,000-path bootstrap (K − A1u +0.042 pp on 61.0 % of B1 paths, +0.025 pp on 59.5 % of B2 paths),
  which is positive but small. The K − A1u p05 is −0.29 pp (B1) / −0.35 pp (B2).
- **H2's January-gap risk is inherited unchanged in kind.** th2 measured the two loss episodes
  (2015-12 → 2016-01 −0.70 pp, 2024-12 → 2025-01 −0.32 pp) on the R2 path; K defers a slightly
  different set (71 vs 51 fills pre-tax) so the episodes are not identical in size, but the
  mechanism (holding weight into an early-January drawdown) is the same. The gains are ~0.03–0.27 pp
  per cell; the individual episode losses are of the same order.
- **No true after-tax out-of-sample**: the TRY/Yİ-ÜFE calendar starts 2000, so the after-tax stages
  cannot reach the pre-2000 OOS windows; E2's rolling starts and E3's seed/block variations are the
  substitutes (and both are positive for K − A1u).
- **The tax model is not tax advice**: FIFO, per-calendar-year netting without loss carry-forward,
  the 2026 tariff deflated by the previous October's Yİ-ÜFE, 10 % indexation floor, instalments
  funded by selling, liquidation at the window end, one CBRT buying-rate series. The fixed
  2015-09 → 2026-09 bootstrap calendar cannot price another FX/inflation regime.
- **A1u's `g_frac` is USD-only by construction** (the implementable rule); the indexed-TRY version
  (A1, not run here) is behaviourally near-identical on this history (v3: 52/6,679 differing
  decisions), so the K conclusion should carry over, but it was not measured for K.
- **The H2 anchors verify H2's code path, not this study's conclusion**, and the deep reference
  block reproduces v3's A1u cells only where v3 published them (52 of this run's A1u-related keys
  are new: `deferred`, `deferrals_by_year`, `cagr_liquidated`; 44 of v3's are absent: the A2/A3
  `blocked_budget`).

## 8. Implementation notes (what shipping K would need)

Both devices are cheap state on top of the existing rebalance decision:

- **A1u** (already being implemented): a FIFO lot ledger with `units` and a **USD cost basis per
  unit**; at a candidate downward resize, `per_unit = (Δunits·p − f)/Δunits`,
  `gain = Σ_lots min(take, lot_units)·(per_unit − lot_cost_per_unit)`,
  `g_frac = max(0, gain)/proceeds`, and `B_down` = 0.5 / 3 / 8 pp at `g_frac ≤ 1 % / ≤ 5 % / > 5 %`.
  No TRY calendar, FX feed or Yİ-ÜFE series is needed.
- **H2**: one extra condition on the same decision — "if the fill session is one of the last 10
  December sessions and `0 < target < held weight`, skip the fill" — with the ordinary rule
  re-evaluated at the next close. Nothing else: exits to zero and up-resizes always fill, and no
  new state is carried between sessions beyond what the rebalance layer already has.
- **Nothing else changes**: same target series, same band for entries/exits/up-resizes, same
  counter semantics (`band` and `December` postponements logged separately, which the run's
  E5/stage counters already do for A1u).

A production change would still need the usual strategy-version bump, a new golden backtest, a
Stage B replay and a fresh config/live-authorization digest. None of that was done here.

### 8.1 Shipped-code notes (audit 2026-10-28, T2-10-1)

- **The deferral is sign-blind, on purpose.** `in_december_deferral_window`
  does not look at the gain. A loss-realising trim in the window is postponed
  to the first January decision, so the loss is booked in the next tax year and
  cannot offset this year's gains. The owner kept this behaviour (decision 8.3
  of `docs/audit/FINDINGS-2026-10-27.md`) because it is the pre-registered K
  composition. A sign-aware deferral is a research candidate to judge after the
  Turkish tax review, not a fix.
- **Calendar failure.** If the NYSE calendar cannot be read, the window is empty
  and the ordinary rule executes (see the planner docstring in
  `execution/portfolio.py`).

## 9. Amendments and honesty notes

**A1. Four implementation fixes, all made before the first complete run and none touching an arm,
parameter, stage, seed, threshold or gate** (each was a crash with a non-zero exit and **no**
`RESULTS.json` written):

1. `reference_checks`' R-USD pre-tax comparison iterated over the candidate arms and asked v3's
   file for an `H2`/`K` cell that does not exist (`KeyError: 'H2'`) — now only arms present in
   both are compared.
2. The same function's after-tax window comparison iterated all five capitals, but the deep
   `--harness` part runs the gate capital only (`KeyError: '10k'`) — capitals absent from the
   partial stage dict are skipped.
3. `gates_combo`'s "vs A1u" reporting block asked for an `A1u_vs_A1u` bootstrap cell
   (`KeyError: 'A1u_vs_A1u'`) — the baseline arm now reports a note instead.
4. Three SUMMARY/`boot_ref` lookups assumed a `{arm}_vs_C0` cell exists for the control arm
   (`KeyError: 'C0_vs_C0'`) — the control's delta columns print `-`.

**A2. Pre-run state.** Before the pre-registration commit `cfd24f83`, on this branch: (a) one
**timing probe** executed `engine_run` (imported from `research/gain_band_v3/screen.py`) once for
C0 (progressive), once for C0 (pre-tax) and once for **A1u** (progressive) on the main window to
size the compute; A1u's value reproduced v3's published cell (0.106146), so no information about
this study's question existed; (b) `--smoke` on synthetic random-walk prices only. After the
commit: `--harness` was run twice (it failed on A1's crash, then passed with **0** mismatches over
1,395 cells), a **code-path probe** with deliberately tiny bootstrap path counts (40/12/12 instead
of 5,000/1,000/1,000, writing only to `/tmp`) was used to exercise `run()`, the gates and the
SUMMARY writer before the real run, and the real `--run` was executed **twice**, producing
byte-identical `RESULTS.json` (SHA-256 `5924ea58ed56c83526ffb9ae015708ce225bcafb4f209afcf9398623412d5997`)
and `SUMMARY.txt` (`eee0843ffb086ff433d1ee55dd335f463f91400e4fe46672880a29cee27cf145`).
**No gate, threshold, arm, parameter or selection rule was changed after seeing any stage result**;
A1's four fixes were all KeyErrors hit *before* a single gate outcome had been computed.

## 10. Reproduction

```
cd /Users/batuhankiskac/Desktop/ParayaBuck-wt/band_h2_combo_v1
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/band_h2_combo_v1/screen.py --smoke     # synthetic only
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/band_h2_combo_v1/screen.py --harness   # control + deep reproduction (~4 min)
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/band_h2_combo_v1/screen.py --run       # ~9 min, writes RESULTS.json/SUMMARY.txt
```

Run from the worktree root on branch `research/band_h2_combo_v1`. `--run` was executed twice with
byte-identical outputs. Inputs, sources and SHA-256 are in `DATA_MANIFEST.json`; the H2 anchors
(and the SHA-256 of the tagged file they were copied from) are in `H2_ANCHORS.json`.
