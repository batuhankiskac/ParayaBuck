# The after-tax exposure frontier under K (exposure_k_v1, 2026-10-15): no arm fits the drawdown budget

> Evidence: `research/exposure_k_v1/` (code, PREREGISTRATION, DATA_MANIFEST, RESULTS.json,
> SUMMARY.txt) is not on `main`; it is preserved in tag `archive/research/exposure-k-v1-2026-10`. Control =
> production K, reproduced cell-for-cell against `research/band_h2_combo_v1/RESULTS.json`.

**Verdict: no candidate arm is selected; the exposure axis is closed with the frontier table and
is not re-run.** Raising K's vol target is a *real* after-tax gain on the realised history —
+0.365 / +0.580 / +0.914 pp at `vt = 0.375`, +0.597 / +1.018 / +1.685 pp at 0.40 and
+0.787 / +1.390 / +2.665 pp at 0.45 (2000 / 2010 / 2017 windows, $100k, liquidated) — but it is
paid for in drawdown, and the price is above the budget the owner set:

| Arm | vt | pre-tax CAGR base / cal. | pre-tax max DD base / cal. | Δ DD vs K (base) | after-tax Δ at $100k (2000/2010/2017) | clean bootstrap Δ B1 / B2 |
|---|---:|---:|---:|---:|---|---|
| **K** | 0.35 | 15.7649 % / 15.1785 % | 46.670 % / 46.795 % | — | — | — |
| **F1** | 0.375 | 16.0668 % / 15.4653 % | 49.034 % / 49.162 % | **+2.36 pp** | **+0.365 / +0.580 / +0.914** | **−0.013 / −0.039** (48.7 % / 46.6 %) |
| **F2** | 0.40 | 16.2455 % / 15.6369 % | 51.399 % / 51.529 % | **+4.73 pp** | **+0.597 / +1.018 / +1.685** | **−0.122 / −0.159** (43.1 % / 41.7 %) |
| **F3** | 0.45 | 16.1919 % / 15.5718 % | 55.790 % / 55.923 % | **+9.12 pp** | **+0.787 / +1.390 / +2.665** | **−0.405 / −0.464** (38.0 % / 37.0 %) |

Every candidate fails **G2** (max DD ≤ K + 1.0 pp) and **G5** (pre-tax block-20 bootstrap median
≥ 0), so the survey's pre-registered selection rule — *the highest after-tax CAGR among arms whose
pre-tax max DD ≤ K + 1.0 pp and whose G1–G9 all pass; if none, close the axis with the frontier
table* — selects **nothing**, exactly the outcome `idea_survey_v4.md` §4/I1 named as likely. The
common protocol's stricter rule (G1–G9 **and** all three after-tax windows **and** B1/B2 median ≥ 0
with share > 0 ≥ 55 %) also selects nothing. **The survey rule decides** (it is the idea's own
pre-registration); the protocol rule agrees here, so the disagreement clause is not exercised.

Three results are worth the owner's attention:

- **The drawdown, not the tax code, is the binding constraint — and it binds early.** F1
  (a 1/14 increase in the target) already costs **+2.36 pp** of pre-tax max DD; the budget is
  1.0 pp. Only F1 keeps `leverage_frontier_v1`'s tail gate at both capitals ($100k:
  P(DD > 70 %) 19.5 % vs K's 16.7 %, limit 21.7 %; $1M: 22.2 % vs 18.9 %, limit 23.9 %); F2 and F3
  fail it at every capital, F3 fails it pre-tax too (21.6 % base against a 16.1 % limit).
- **The after-tax windows and the bootstrap point in opposite directions, and the bootstrap
  wins.** The realised windows reward a higher target monotonically (F3 > F2 > F1 > K on all three),
  the same pattern `vt_level_tax_v1` found under E2, and the reason is visible in the trade
  counters: sells/yr fall 10.17 → 8.85 / 8.25 / 6.59 and the resizes that disappear are the
  gain-heavy ones A1u would have blocked anyway. But the clean bootstrap medians are **negative for
  every arm** and the share of paths beating K *falls* with the target (48.7 % → 43.1 % → 38.0 % on
  B1-recent). The realised gain is a property of this one path, not of the rule.
- **K is not the same well as a higher target.** K beat R2 after tax on the same windows
  (+0.593 / +0.529 / +1.086 pp) *while cutting* max DD by 0.09 pp and *raising* its bootstrap median
  by +0.29 pp; raising `vt` buys +0.37 … +2.67 pp on the same windows but *costs* 2.4 … 9.1 pp of
  DD and *lowers* the bootstrap median. The two levers are not substitutable at the margin the
  owner's DD budget allows.

No production file was touched; the PAPER identity stays as it is.

## 1. Question and what is new

`docs/research/idea_survey_v4.md` §3 establishes that under K the taxed base is the strategy's own
sells (88 % of realised gain; the terminal liquidation is 11 %), that indexation relief is nearly
saturated (22.6 m of ~49 m TRY gross gain), and that the *effective* tax rate on realised gain is
unchanged from R2 (17.3 % vs 17.1 %) — but that the marginal tax cost of one more unit of exposure
has fallen, because A1u already defers exactly the gain-heavy resizes a higher vol target generates.
K holds *more* exposure than R2 at *lower* max DD (46.670 % vs 46.758 %), so the DD budget that
constrains every candidate (`max DD ≤ K + 1.0 pp`) is unspent. I1 (rank 1) asks what it costs, in
drawdown, to buy the after-tax-optimal exposure once K's tax logic is in place.

Both nearest studies are pre-K: `leverage_frontier_v1` has no tax model at all (plain SMA200 signal,
synthetic Lx cells, every cell clearing +1 pp CAGR fails its tail gate);
`vt_level_tax_v1`'s control is production **E2** (no early path, no band), where 0.40 won the
realised after-tax path by +0.61 / +0.96 / +1.72 pp but lost the clean bootstrap
(−0.088 / −0.153 pp median). Neither measures the after-tax frontier with K's execution layer, and
neither applies K's DD budget. That is this study's only new content.

## 2. Method

**Control = production K, bit for bit.** `research/band_h2_combo_v1/screen.py` and `tax_engine.py`
were copied into `research/exposure_k_v1/` and adapted to run **one target series per arm**; the
control's own cells are unchanged and are asserted two ways (§3). Mechanics: the E2 2-of-3 vote on
the QQQ price close (close > SMA200; 252-session price return > 0; close > the 126-session max/min
midpoint) + R2's 0.5 × vt early re-entry leg (close > SMA50, `vol20` below its 252-session median,
close > close 20 sessions ago; exit below SMA50), size `min(1, VT/vol20)`, cap 0.98, decide on the
completed close, fill at the next open, 0.5 pp resize band, cost 4 bps × m, synthetic QLD* =
daily-reset 2× on QQQ total return with 0.95 %/yr expense and DTB3 + {base 0.0 %, calibrated
0.75 %}/yr financing. The execution layer is K's: **A1u** (a downward resize fills only if the drift
exceeds 0.5 / 3 / 8 pp at a USD FIFO gain fraction ≤ 1 % / ≤ 5 % / > 5 %) + **H2** (a non-exit
downward resize whose fill session is one of the last 10 December sessions is postponed); entries,
exits to zero and up-resizes keep 0.5 pp.

| Arm | Rule |
|---|---|
| **K** | production K, `vt = 0.35` (control). |
| **R2** | production R2 (K's layer with the band off and no deferral, `vt = 0.35`) — reference row only. |
| **F1 / F2 / F3** | K with `vt = 0.375 / 0.40 / 0.45`. **Nothing else changes.** |

**Stages** (all fixed in `PREREGISTRATION.md` before any non-control arm ran on real data): pre-tax
main window 2000-03-09 → 2026-09-29 (6,679 sessions, m = 1/2/4, base/calibrated, plus the R-USD
row); after tax at $100k (gate capital) and $10k/$30k/$300k/$1M on the three windows; the clean
bootstrap (seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths, $100k/$1M, B1-recent and
B2-flat on a fixed 2015-09 → 2026-09 calendar with realised USD/TRY and Yİ-ÜFE); E1 (after tax on the
21 ETFs under R-INDEX); E2 (21 January rolling starts); E3 (fresh bootstraps, seed 20261015, blocks
10/40); E4 (the pre-tax bootstrap leg + main-window DD); E5 (report-only decisions, extra capitals,
m = 4); and the six `ewma_band_v1` OOS windows with the pooled M1–M5 bootstrap.

**Gates** (pre-registered, §5 of the pre-registration): ewma_band_v1's G1–G9 with C0 → K and
G9 = turnover ≤ 2 × K; `leverage_frontier_v1`'s tail gate P(DD > 70 %) ≤ K + 5 pp on the ten-year
(2,520-session) block-20 paths, pre-tax base/calibrated **and** after tax at $100k/$1M, plus its
65 %-level leg; the after-tax $100k windows (Δ ≥ 0 in all three); and the clean bootstrap B1/B2
(median Δ ≥ 0, share > 0 ≥ 55 % protocol / ≥ 50 % survey). **Selection:** the survey's I1 rule
decides; the common protocol's rule is reported.

## 3. Reproduction (must pass before any number is read)

`--harness` PASS, 0 checks outside tolerance. The control's own anchors: pre-tax CAGR
16.0173 / 15.7649 % (m = 1 / m = 2, base) and 15.4303 / 15.1785 % (calibrated), max DD
46.3529 / 46.6698 % and 46.4786 / 46.7948 %, turnover 5.4568 / 5.4561; after tax at $100k
10.7533 / 15.2354 / 23.9720 % with max DD 46.6698 / 44.6272 / 34.9774 %; K's counters 1,239 band
blocks / 71 December deferrals. The R2 reference row reproduces the R2 anchors (15.7538 / 15.4766 %,
DD 46.4143 / 46.7584 %, after tax 10.160 / 14.706 / 22.886 %) and the pure-E2 series reproduces
`e2_integration_v1` (14.9491 / 14.6749 %, DD 49.2471 / 49.5643 %). The R2 arm's NAV path equals the
reference `simulate_band` to 1.3e-13.

The **deep reference block** then compares every numeric leaf of every **K** cell
`band_h2_combo_v1` published against its stored `RESULTS.json`: **2,270 cells, 0 mismatches**
(tolerance 2e-6, max |diff| 5.0e-7 — the rounded stored precision), covering the pre-tax main window
(m = 1/2/4 × base/calibrated), the R-USD row, the after-tax windows at all five capitals, the m = 4
cost stress, the seed-20261011 bootstrap (pre-tax and after-tax, $100k/$1M), the 21 R-USD
instruments, E1/E2/E3 and the six OOS windows, with R2's cells (that campaign's "C0") mapped
label-for-label. The bootstrap median anchors also reproduce exactly: K 3.8577 % (B1) / 4.0376 %
(B2), R2 3.5155 / 3.9678, pure-E2 3.079 / 3.520, and K's pre-tax legs 7.7761 / 7.3137 %.

## 4. Results

### Pre-tax main window, m = 2 (2000-03-09 → 2026-09-29)

| Arm | vt | CAGR base | CAGR cal. | max DD base | max DD cal. | m = 1 base | m = 4 base | turnover/yr | sells/yr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **K** | 0.35 | 15.7649 % | 15.1785 % | 46.670 % | 46.795 % | 16.0173 % | 15.2541 % | 5.456 | 10.17 |
| R2 | 0.35 | 15.4766 % | 14.8961 % | 46.758 % | 46.880 % | 15.7538 % | 14.9241 % | 5.992 | 29.56 |
| **F1** | 0.375 | 16.0668 % | 15.4653 % | 49.034 % | 49.162 % | 16.3192 % | 15.5571 % | 5.430 | 8.85 |
| **F2** | 0.40 | 16.2455 % | 15.6369 % | 51.399 % | 51.529 % | 16.4991 % | 15.7363 % | 5.446 | 8.25 |
| **F3** | 0.45 | 16.1919 % | 15.5718 % | 55.790 % | 55.923 % | 16.4439 % | 15.6896 % | 5.413 | 6.59 |

The return frontier is **flat-topped**: F2 (16.2455 %) is the best pre-tax CAGR and F3 (16.1919 %) is
*worse* in return at 4.4 pp more drawdown — the vol target has run past its optimum, the same shape
`leverage_frontier_v1` and `vt_level_tax_v1` found from 0.40/0.45 upward. Turnover barely moves
(5.41–5.46 vs K's 5.456) and the arms trade *fewer*, larger slices: sells/yr fall from 10.17 to
8.85 / 8.25 / 6.59.

### After tax at $100k (progressive 2026 tariff, base financing, m = 2, liquidated)

| Window | Arm | After tax | Δ vs K | max DD | tax paid (USD) | sells/yr |
|---|---|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | K | 10.7533 % | — | 46.670 % | 830,903 | 10.66 |
| | F1 | **11.1179 %** | **+0.365 pp** | 49.034 % | 897,000 | 9.38 |
| | F2 | **11.3502 %** | **+0.597 pp** | 51.399 % | 944,017 | 8.89 |
| | F3 | **11.5401 %** | **+0.787 pp** | 55.790 % | 941,361 | 7.42 |
| 2010-01 → end | K | 15.2354 % | — | 44.627 % | | |
| | F1 / F2 / F3 | 15.8156 / 16.2536 / 16.6252 % | **+0.580 / +1.018 / +1.390 pp** | | | |
| 2017-01 → end | K | 23.9720 % | — | 34.977 % | | |
| | F1 / F2 / F3 | 24.8864 / 25.6569 / 26.6370 % | **+0.914 / +1.685 / +2.665 pp** | | | |

At $1M the same picture: F1 +0.39 / +0.65 / +1.03 pp, F2 +0.68 / +1.11 / +1.85 pp,
F3 +0.86 / +1.48 / +2.85 pp. The m = 4 cost stress keeps every sign (F1 +0.358 / +0.548 / +0.924,
F2 +0.593 / +0.984 / +1.691, F3 +0.781 / +1.351 / +2.660 vs K at $100k). Tax paid *rises* in
absolute terms (830,903 → 897,000 / 944,017 / 941,361): the deferred base grows with the position,
and the window-end liquidation charges it.

### Clean after-tax bootstrap at $100k (seed 20261011, block 20, 1,000 paths)

| Mode | Arm | median CAGR | Δ vs K (share > 0) | p05 / p95 of Δ |
|---|---|---:|---|---|
| B1-recent | K | 3.8577 % | — | — |
| | F1 | 3.9058 % | **−0.013 pp (48.7 %)** | −0.70 / +0.93 |
| | F2 | 3.7985 % | **−0.122 pp (43.1 %)** | −1.34 / +1.64 |
| | F3 | 3.3159 % | **−0.405 pp (38.0 %)** | −2.54 / +2.85 |
| B2-flat | K | 4.0376 % | — | — |
| | F1 | 4.0661 % | **−0.039 pp (46.6 %)** | −0.73 / +0.94 |
| | F2 | 3.9432 % | **−0.159 pp (41.7 %)** | −1.34 / +1.63 |
| | F3 | 3.6133 % | **−0.464 pp (37.0 %)** | −2.61 / +2.77 |

Pre-tax bootstrap (5,000 paths, both financings, G5): median Δ is −0.042 / −0.053 pp (F1),
−0.170 / −0.186 (F2), −0.503 / −0.546 (F3), with 37–47 % of paths above K. E3's fresh bootstraps
(seed 20261015) agree: F1 −0.047 / −0.023 / +0.017 / +0.043 pp over blocks 10/40 × flat/progressive
(45.6–53.7 % positive), F2 −0.181 … −0.016, F3 −0.387 … −0.232.

### The tail gate (`leverage_frontier_v1`), ten-year block-20 paths

| Cell | K | F1 (0.375) | F2 (0.40) | F3 (0.45) | limit (K + 5 pp) |
|---|---:|---:|---:|---:|---:|
| P(DD > 70 %) pre-tax base (5,000 paths) | 11.1 % | **13.2 %** | 16.0 % | **21.6 %** | 16.1 % |
| P(DD > 70 %) pre-tax calibrated | 11.9 % | **14.2 %** | **17.2 %** | **22.6 %** | 16.9 % |
| P(DD > 70 %) after tax $100k (1,000 paths) | 16.7 % | **19.5 %** | **22.9 %** | **29.6 %** | 21.7 % |
| P(DD > 70 %) after tax $1M | 18.9 % | **22.2 %** | **26.4 %** | **31.7 %** | 23.9 % |
| median max DD pre-tax base | 53.4 % | 55.0 % | 56.5 % | 59.0 % | 65 % level leg |

Bold = passes. **F1 is the only arm inside the tail gate everywhere** (its four cells sit 1.7–2.9 pp
inside their limits, i.e. within about half the allowance); F2 fails at the calibrated pre-tax cell
and both after-tax capitals, F3 fails all four. The median-max-DD ≤ 65 % leg passes for every arm.

### Gates

| Gate | Requirement | F1 | F2 | F3 |
|---|---|---|---|---|
| G1 return (base / cal.) | CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp | pass (+0.302 / +0.287 pp) | pass (+0.481 / +0.458) | pass (+0.427 / +0.393) |
| **G2 DD** | max DD ≤ K + 1.0 pp (base and cal.) | **fail** (+2.36 / +2.37) | **fail** (+4.73 / +4.73) | **fail** (+9.12 / +9.13) |
| G3 2017+ | CAGR ≥ K − 1.0 pp | pass (+0.800 / +0.771) | pass (+1.520 / +1.483) | pass (+1.933 / +1.878) |
| G4 sub-periods | above K in ≥ 2 of P1/P2/P3 | pass (2/3; P1 −0.30) | pass (2/3; P1 −0.76) | pass (2/3; P1 −1.29) |
| **G5 bootstrap** | block-20 pre-tax median Δ ≥ 0 (base and cal.) | **fail** (−0.042 / −0.053) | **fail** (−0.170 / −0.186) | **fail** (−0.503 / −0.546) |
| G6 G1 at m = 1 | pass | pass | pass | pass |
| G7 OOS windows | median Δ ≥ 0; ≥5/6 within −0.5 pp / +3 pp | pass (+0.091, 6/6, max DD +2.32) | pass (+0.314, 5/6, max DD +3.26) | pass (+0.559, 5/6, max DD +5.49) |
| G8 pooled OOS bootstrap | median Δ ≥ 0 | pass (+0.047) | pass (+0.090) | pass (+0.143) |
| G9 turnover | ≤ 2 × K | pass (0.995) | pass (0.998) | pass (0.992) |
| TAIL | P(DD > 70 %) ≤ K + 5 pp at every cell | pass (4/4) | fail (2/4) | fail (0/4) |
| AT $100k | Δ ≥ 0 in all three windows | pass (min +0.365) | pass (min +0.597) | pass (min +0.787) |
| BOOT (protocol) | B1/B2 median ≥ 0, share > 0 ≥ 55 % | fail | fail | fail |
| **Result** | **G1–G9 and the DD budget** | **fail** | **fail** | **fail** |

E1 (after-tax on the 21 quality-passing ETFs, R-INDEX, $100k) fails for all three arms on its share
requirement — median Δ +0.049 / +0.045 / +0.131 pp but only 12 / 12 / 13 of 21 instruments positive
(floor 60 %); the pre-tax 21-instrument R-USD block is mildly positive on return (median Δ CAGR
+0.053 / +0.068 / +0.189 pp) with worst Δ DD +2.09 / +4.73 / +8.93 pp. E2 (21 January rolling
starts, after tax at $100k) is positive in **21/21** starts for every arm (median +0.714 / +1.233 /
+1.766 pp) — the sharpest disagreement with the bootstrap in the whole run, and the reason the
window evidence cannot be read alone.

### The frontier table (the study's headline output)

| Arm | vt | after tax $100k 2000 / 2010 / 2017 | pre-tax max DD base / cal. | Δ DD base / cal. | P(DD>70%) pre / $100k / $1M | in budget | G1–G9 |
|---|---:|---|---:|---:|---|---|---|
| **K** | 0.35 | 10.753 / 15.235 / 23.972 % | 46.67 / 46.79 % | — | 11.1 / 16.7 / 18.9 % | (origin) | — |
| R2 | 0.35 | 10.160 / 14.706 / 22.886 % | 46.76 / 46.88 % | +0.09 / +0.09 | 10.4 / 16.0 / 18.2 % | (reference row) | — |
| **F1** | 0.375 | 11.118 / 15.816 / 24.886 % | 49.03 / 49.16 % | +2.36 / +2.37 | 13.2 / 19.5 / 22.2 % | **no** | fail |
| **F2** | 0.40 | 11.350 / 16.254 / 25.657 % | 51.40 / 51.53 % | +4.73 / +4.73 | 16.0 / 22.9 / 26.4 % | **no** | fail |
| **F3** | 0.45 | 11.540 / 16.625 / 26.637 % | 55.79 / 55.92 % | +9.12 / +9.13 | 21.6 / 29.6 / 31.7 % | **no** | fail |

The price of the after-tax-optimal exposure is therefore **above the budget at the first step**:
the smallest pre-registered increase (0.375) already spends 2.36 pp of the 1.0 pp allowance, and the
after-tax frontier is monotone in `vt` while the pre-tax return frontier is not.

## 5. Findings

1. **The exposure axis is closed.** No `vt` above production's 0.35 clears the pre-registered
   selection rule; the drawdown budget (G2) and the pre-tax bootstrap (G5) both bind at every
   candidate, and the tail gate binds from 0.40 upward. Per the pre-registration this is a
   *measurement of the price*, not a bug, and the axis is not to be re-run.
2. **The after-tax windows are not evidence of an edge here.** F1/F2/F3 beat K on all three
   windows (+0.37 to +2.67 pp) and in all 21 rolling starts, yet their clean-bootstrap medians are
   negative and their share of winning paths falls with `vt` (48.7 % → 38.0 %). This is the same
   failure pattern `vt_level_tax_v1` reported under E2 (−0.088 pp at 0.40), now re-measured with
   K's band and H2 in place: **K's tax-deferral layer does not fix the higher-target channel.**
   The mechanism is visible in the counters — the extra return is *more deferred exposure*, the
   tax bill grows in absolute terms, and the bootstrap, which resamples market blocks while keeping
   the FX/inflation calendar real, does not reward it.
3. **K ≠ "more exposure".** K's own after-tax edge over R2 (+0.593 / +0.529 / +1.086 pp) came with
   *lower* max DD and a *higher* bootstrap median (+0.286 pp). Raising `vt` produces a larger
   after-tax window gain but the opposite DD and bootstrap signs. Anyone reading K's attribution
   ("the biggest lever is what and how long you hold") should read this table as its boundary
   condition: the lever that pays without spending drawdown is the tax-timing one; the lever that
   buys return with drawdown is the classic vol-target/leverage trade, and it does not become
   cheaper because the tax code defers some of its realisations.
4. **The pre-tax return frontier tops out at 0.40.** F2 is the best CAGR both pre-tax (16.2455 %)
   and after tax (11.3502 / 16.2536 / 25.6569 %), and F3 gives back 0.05 pp of pre-tax CAGR for
   4.4 pp more drawdown. If the owner were willing to accept ~51 % drawdown, 0.40 would be the
   choice; the budget says no, and the bootstrap says the 0.40 gain is inside the noise
   (−0.122 pp median, 43.1 % of paths).
5. **Turnover and the realisation count both improve, which is why the axis looked promising.**
   Sells/yr fall 10.17 → 8.85 / 8.25 / 6.59 with turnover flat at ~5.42–5.45 (G9 passes easily at
   0.99 × K), and A1u's band blocks *more* resizes (1,966 / 2,237 / 2,492 vs K's 1,836) — the
   tax-cheap exposure mechanism I1 describes is real. It is simply not big enough, on this
   evidence, to pay for the extra drawdown.

## 6. Selection

`survey_rule.selected = none`; `protocol_rule.selected = none`; the deciding rule is the survey's
(as pre-registered), and the two agree, so no conflict clause applies. Full per-arm reason codes are
in `RESULTS.json` (`gates.survey_rule.detail`, `gates.protocol_rule.detail`). Production K is
unchanged; no Stage B replay was run, because no candidate qualified.

## 7. Caveats

- **One history per cell.** The after-tax window deltas are single paths; the 1,000-path clean
  bootstrap (B1-recent, B2-flat) is the counterweight and it is negative for every arm. The four
  window/rolling-start stages that favour them and the two bootstrap families that do not are
  reported side by side rather than averaged.
- **No true after-tax out-of-sample**: the TRY/Yİ-ÜFE calendar starts in 2000, so the after-tax
  stages cannot reach the pre-2000 OOS windows; E2's rolling starts and E3's seed/block variations
  are the substitutes, and they disagree (E2 21/21 positive, E3 negative for F2/F3).
- **The pre-tax DD cost is measured on the same 26.5 years** that produced K's own DD
  (2000-03 → 2003-03, fully recovered 2007-05) — a different sequence could rank the arms
  differently, which is exactly what the bootstrap explores.
- **Tail-gate path sets differ by cell.** Pre-tax cells use the 5,000 ten-year block-20 pre-tax
  paths (the same construction `leverage_frontier_v1` used, no tax); the $100k/$1M cells use the
  1,000 after-tax liquidated NAV paths of the same calendar. Both are reported; both are evaluated.
- **The tax model is not tax advice**: FIFO, per-calendar-year netting without loss carry-forward,
  the 2026 tariff deflated by the previous October's Yİ-ÜFE, a 10 % indexation floor, instalments
  funded by selling, liquidation at the window end, one CBRT buying-rate series. The fixed
  2015-09 → 2026-09 bootstrap calendar cannot price another FX/inflation regime.
- **The control is validated hard, the candidates are not.** 2,270 cells of K's published record
  reproduce exactly; the candidate arms inherit K's code path with one changed constant, and their
  structural effect on the target series is checked in `--smoke` (monotone mean weight in `vt`,
  differing NAV paths, band and deferral both firing).
- **Report-only stages** (E3, E5, the extra capitals, the instrument tables) do not gate; they are
  recorded so the owner can see the whole shape.

## 8. Run protocol

`--smoke` PASS (synthetic prices only) → pre-registration commit `c333da35` (before any non-control
arm ran on real data) → `--harness` PASS (0 outside tolerance) → `--run` twice, byte-identical.
Runtime ≈ 18.5 minutes per run, offline and deterministic. `RESULTS.json` and `SUMMARY.txt` are in
`research/exposure_k_v1/`; `DATA_MANIFEST.json` records the source and SHA-256 of every input.

## 9. Amendments and honesty notes

No arm, parameter, stage, seed, threshold, gate or selection rule was changed after seeing data;
the gates that failed (G2, G5, the tail gate from 0.40, E1's share) are reported as they fell.
`PREREGISTRATION.md` §9 carries the full disclosure. In brief: the pre-registration was committed
in `c333da35` **before any candidate arm was simulated on real data**; the `--harness` run that
preceded the commit executed only the control, the reference row and the pure-E2 anchor series plus
a structural check that the candidate target series differ from K's; four plumbing bugs in this
study's harness adaptation (`targets_of`'s path axis, the bootstrap delta list, the frontier table's
arm set, one array shape) were then fixed, and a `run()` probe with deliberately tiny path counts
(therefore meaningless bootstrap cells, but real deterministic ones) was executed with output
redirected to `/tmp`. That probe did display the candidates' deterministic cells before the recorded
runs; nothing was changed as a result. The two recorded `--run`s are byte-identical
(`RESULTS.json` SHA-256 `5ef4a09ec94391e27e42196531e30b5a67fb3a89ff801af388766518533f00e0`,
`SUMMARY.txt` `d5a2d1bc8ce79be73304965b28de79da5440c97716e83a01ee69cc69da6722f7`).

Not tax advice. Nothing was shipped; production K is untouched.
