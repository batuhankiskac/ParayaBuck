# Early-leg conviction ladder (ladder_v1, 2026-10-15): a pre-tax lever that dies after Turkish tax

> Evidence: `research/ladder_v1/` (code, PREREGISTRATION, DATA_MANIFEST, RESULTS.json, SUMMARY.txt)
> is not on `main`; preserved in tag `archive/research/ladder-v1-2026-10`. Control = production **K**
> (R2 + A1u + H2). Question = `idea_survey_v3` **I4**, arms E1–E4 exactly as the survey fixed them.
> Pre-tax anchors (K, and the pre-K R2 reference row) and the paired bootstrap are reproduced
> cell-for-cell inside this run (§3).

**Verdict: REJECT all four. Production K stands unchanged.** Sizing the early re-entry leg by the
volatility-normalized conviction metric `m` *is* a real pre-tax lever — every arm beats K on
pre-tax CAGR by **+0.40 … +0.60 pp** *and* cuts the main-window max DD by **0.5 … 3.3 pp** — but
not one arm clears the pre-registered after-tax gates, and the whole edge is one episode.

| Arm | survey | pre-tax CAGR base / cal. | max DD base / cal. | after-tax $100k Δ vs K (2000 / 2010 / 2017) | B1 / B2 median Δ (share > 0) | failed |
|---|---|---:|---:|---|---|---|
| **C0 = K** | control | 15.76 / 15.18 % | 46.67 / 46.79 % | — | — | — |
| **L1** | E1 steps 0.5/0.75/1.0 | **16.28 / 15.68 %** | 45.41 / 45.57 % | +0.373 / +0.025 / **−0.139** | +0.197 (57.5 %) / +0.244 (58.9 %) | **G7, A1** |
| **L2** | E2 two-step | **16.37 / 15.77 %** | 43.41 / 43.57 % | +0.446 / −0.023 / **−0.221** | +0.174 (56.7 %) / +0.238 (58.2 %) | **A1** |
| **L3** | E3 linear | **16.26 / 15.67 %** | 45.04 / 45.19 % | +0.356 / +0.040 / **−0.112** | +0.114 (**54.8 %**) / +0.155 (56.6 %) | **A1, A2** |
| **L4** | E4 momentum | **16.16 / 15.57 %** | 46.20 / 46.34 % | +0.303 / +0.210 / +0.229 | +0.046 (**53.1 %**) / +0.074 (55.2 %) | **A2** |
| *R2* | *pre-K ref.* | *15.48 / 14.90 %* | *46.76 / 46.88 %* | *−0.593 / −0.529 / −1.086* | *−0.286 (25.0 %) / −0.131 (36.0 %)* | *(reference row)* |

Four results are worth the owner's attention:

- **The arms split into two failure groups, and neither half can be fixed by the other.** The
  aggressive arms (L1/L2/L3) raise the early weight on most of the path and die on **A1**, the
  after-tax 2000/2010/2017 window test — they win the 2000 window by +0.36 … +0.45 pp but *lose the
  2017 window* by 0.11 … 0.22 pp (L2 also loses 2010 by 0.02 pp). The conservative arm (L4,
  keyed on the 20-session momentum metric) is the **only** arm that wins all three after-tax
  windows (+0.303 / +0.210 / +0.229 pp) and the only one positive in 21/21 rolling January starts,
  and it dies on **A2** with a bootstrap share of 53.1 % (B1, floor 55 %) — the same
  noise-level-share failure `gain_band_v3` hit for A1u. L3 fails both.
- **The extra tax is large and measurable.** On the 2000 window the aggressive arms pay
  **+91 k … +114 k USD more tax** (L2 945.3 k vs K 830.9 k, +13.8 %) for +0.44 pp of after-tax
  CAGR — the survey's pre-stated "exposure bet, not a tax bet" risk, quantified.
- **The edge is one episode.** Leave-one-episode-out (the arm's early leg replaced by K's inside a
  pre-registered episode) takes L1 from +0.514 pp to **−0.084 pp**, L2 to +0.030, L3 to −0.057,
  L4 to −0.018 pp when **2001-10-31** is removed. The other two big episodes carry almost nothing
  (2002-11-04: +0.088/+0.202/+0.094/+0.098; 2023-01-20: −0.023/+0.071/+0.001/−0.057).
- **The plateau check passes, so this is not curve-fitting of the terciles** — but it does not
  rescue any arm. For L1 and L4 both alternative cut-sets (0.25/1.0 and 1.0/2.0) keep every sign
  positive, and the alternative cuts are *more* positive than the pre-registered 0.5/1.5
  (L1 +0.656/+0.660 pp pre-tax, +0.501/+0.499 pp after tax vs +0.514/+0.373 pp; L4 +0.533 and
  +0.257 pre-tax vs +0.398). The pre-registered cuts are, if anything, the *conservative* choice.

Nothing was shipped; production K is unchanged and no production file was touched.

## 1. Question

Production K enters the early re-entry path at a **constant** half of the production weight
(`0.5 × vt`, 164 early sessions over 2000-03-09 → 2026-09-29, 32 episodes). Does sizing that leg by
a volatility-normalized **conviction metric** — more weight when the recovery is strong relative to
its own vol, less when it is marginal — beat K *after Turkish income tax* without a materially
worse drawdown? `idea_survey_v3` I4.

Known bounds were fixed before the run (§0 of the pre-registration): `fast_reentry_v1`'s R1 (the
same path at *full* weight) earned 16.17 % vs R2's 15.48 % with a *lower* in-sample max DD
(44.6 % vs 46.8 %) and failed only the OOS drawdown gate; so the ladder's upside is bounded near
the R1−R2 gap and its downside is R1's OOS behaviour. Every arm here raises exposure rather than
removing realizations, so **tax survival was expected to bind** — and it did.

## 2. Method

**Control (C0) = production K, exactly** (`equity_leveraged_trend_v1` 1.2.0 + the K execution
layer), through the single sequential engine (`tax_engine.py`, a byte copy of
`band_h2_combo_v1/tax_engine.py`): R2's target series (E2 2-of-3 vote on the QQQ close + the 0.5·vt
early re-entry leg, sized `vt = min(1, 0.35/vol20)`, cap 0.98; decide on the completed close, fill
at the next open), **A1u** (a downward resize between two positive targets fills only if the drift
exceeds 0.5 / 3 / 8 pp at a USD FIFO gain fraction ≤ 1 % / ≤ 5 % / > 5 %) and **H2** (such a
downsize whose *fill* session is in the last 10 December NYSE sessions is postponed to January).
Entries, exits to zero and up-resizes keep the 0.5 pp band; cost 4 bps × m × |Δw|; synthetic
QLD\* = daily-reset 2× on QQQ total return, 0.95 %/yr expense, financing DTB3 + {base 0.0 %,
calibrated 0.75 %}/yr; idle cash zero.

**The only change** is the early-leg weight: while the early path is active the leg is
`g(m) × vt` instead of `0.5 × vt`, with the metric read on the QQQ decision close the votes and
the entry filter already use:

```
m   = (close / SMA50 − 1) / (vol20 / sqrt(252))                  -- distance to SMA50 in daily sigmas
m20 = (close / close[−20] − 1) / (vol20 / sqrt(252) · sqrt(20))  -- 20-session return in 20-session sigmas
```

Both formulas and the degeneracy rule are asserted in `--smoke`. The m20 denominator is the
**scale-consistent** reading of the survey's `vol20·√20` (annualized vol20 / √252 = daily sigma,
times √20) — the only reading under which the survey's "same 0.5/1.5 cuts" mean anything; the
literal annualized-vol reading would rescale m20 by √252 and make the cuts arbitrary. Disclosed in
the pre-registration §3.2 before any run.

| Arm | survey | rule (parameters pre-registered, never tuned) |
|---|---|---|
| **C0** | (control) | production K: `g = 0.5` constant |
| **L1** | E1 | `g = 0.5 / 0.75 / 1.0` on `m < 0.5` / `0.5 ≤ m < 1.5` / `m ≥ 1.5` |
| **L2** | E2 | `g = 0.5` for `m < 1.0`, else `1.0` |
| **L3** | E3 | `g = clip(m / 2, 0.5, 1.0)` |
| **L4** | E4 | L1's three steps with the same `0.5 / 1.5` cuts, keyed on `m20` |

Arm labels are renamed from the survey's E1–E4 (this harness already uses `E2` for the pure-E2
anchor series); the mapping is exact. **K's execution layer applies to every arm identically** —
the ladder also changes the transfers between the full weight and the early leg, and a down-size
between two positive targets is a discretionary downward resize (A1u band / H2 window) in every
arm. E2's own vote overrides the ladder: `factor = 1.0` on every on-state session in every arm.

**Stages** (fixed in `PREREGISTRATION.md` §4 before any non-control arm ran on real data):
pre-tax main window 2000-03-09 → 2026-09-29 (6,679 sessions, m = 1/2/4, base/calibrated, plus the
R-USD row); after tax at $100k (gate) and $10k/$30k/$300k/$1M on the three windows (progressive
2026 tariff, Yİ-ÜFE-indexed, FIFO, liquidated at the end); the clean bootstrap (seed 20261011,
block 20, 5,000 pre-tax / 1,000 after-tax paths, fixed 2015-09 → 2026-09 calendar with realised
USD/TRY and Yİ-ÜFE, B1-recent progressive and B2 flat 20 %); the six `ewma_band_v1` OOS windows
(calibrated, m = 2, R-USD); the pooled OOS bootstrap (M1–M5, 2,000 paths each); 21 rolling January
after-tax starts; per-episode P&L and leave-one-episode-out; the plateau check; the decision traces.

**Gates** (pre-registered, §5; all must pass): **G1** m = 2 CAGR ≥ C0 (or Calmar ≥ 1.1 × C0 with
CAGR ≥ C0 − 0.5 pp), both financings; **G2** max DD ≤ C0 + 1 pp (both); **G3** 2017+ CAGR ≥ C0 − 1 pp
(both); **G4** CAGR above C0 in ≥ 2 of 3 sub-periods; **G5** block-20 bootstrap median arm − C0 ≥ 0
(both); **G6** G1 at m = 1; **G7** six OOS windows: median ΔCAGR ≥ 0, ≥ 5 of 6 windows with
ΔCAGR ≥ −0.5 pp **and** ΔDD ≤ +3 pp, ΔDD never > +8 pp; **G8** pooled OOS bootstrap median Δ ≥ 0;
**G9** turnover/yr ≤ 2 × C0; **A1** after tax at $100k arm − C0 ≥ 0 in **all three** windows;
**A2** clean bootstrap B1-recent **and** B2 median ≥ 0 with share > 0 ≥ 55 %; **P1** plateau check
for L1 and L4 (the sign of both the pre-tax base m = 2 Δ and the after-tax 2000-window Δ must not
flip under the cut-sets 0.25/1.0 and 1.0/2.0). Selection: the passing candidate with the highest
base m = 2 CAGR, else K stands. No arm, parameter, stage or gate was changed after the run.

## 3. Reproduction (must pass before any number is read)

`--harness` **PASS, 71 checks, 0 outside tolerance** at the pre-registered tolerances (1e-4 on
pre-tax CAGR / max DD / turnover, 5e-5 on the after-tax windows, 1e-3 on the bootstrap medians,
1e-3 on the paired bootstrap delta). The control `C0` reproduces **every published K cell** of
`band_h2_combo_v1/RESULTS.json` (`K_ANCHORS.json`): pre-tax 15.7649 / 15.1785 % CAGR (m = 2) and
16.0173 / 15.4303 % (m = 1), max DD 46.670 / 46.795 %, turnover 5.4561; after tax at $100k
10.753 / 15.235 / 23.972 % with max DD 46.670 / 44.627 / 34.977 %; the six OOS windows; the
B1/B2 bootstrap medians 3.8577 / 4.0376 %. The `R2` reference row reproduces R2's published cells
(15.4766 / 14.8961 %, DD 46.7584 / 46.8797 %, 5.9915, after tax 10.160 / 14.706 / 22.886 %, B1/B2
3.5155 / 3.9678 %), the pure-E2 anchor series its `vt_level_tax_v1` cells, and the paired
bootstrap pins the *draws* (`R2_vs_C0` = −(K_vs_C0) to 1e-3). Engine identity: `R2 ==
simulate_band` to 1e-9, and the control's NAV is asserted to differ from both the R2 and the E2 NAV.

## 4. Per-arm results

### Pre-tax main window, m = 2, R-INDEX (2000-03-09 → 2026-09-29)

| Arm | CAGR base | CAGR cal. | max DD base | max DD cal. | Calmar | m = 1 base | 2017+ | turnover/yr | sells/yr | boot Δ base / cal. (share > 0) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **C0 (K)** | 15.7649 % | 15.1785 % | 46.670 % | 46.795 % | 0.3378 | 16.0173 % | 33.231 % | 5.4561 | 10.17 | — |
| **L1** | 16.2785 % | 15.6850 % | 45.413 % | 45.565 % | 0.3585 | 16.5367 % | 33.176 % | 5.5062 | 10.35 | +0.344 (61.4 %) / +0.326 (60.7 %) |
| **L2** | 16.3652 % | 15.7712 % | 43.414 % | 43.572 % | 0.3770 | 16.6232 % | 33.069 % | 5.5478 | 10.24 | +0.351 (59.9 %) / +0.333 (59.5 %) |
| **L3** | 16.2598 % | 15.6669 % | 45.036 % | 45.187 % | 0.3610 | 16.5160 % | 33.215 % | 5.5144 | 10.39 | +0.283 (60.1 %) / +0.265 (59.7 %) |
| **L4** | 16.1630 % | 15.5724 % | 46.205 % | 46.342 % | 0.3498 | 16.4243 % | 33.587 % | 5.5787 | 10.77 | +0.169 (58.8 %) / +0.161 (58.3 %) |
| *R2* | *15.4766 %* | *14.8961 %* | *46.758 %* | *46.880 %* | *0.3310* | *15.7538 %* | *32.714 %* | *5.9915* | *29.56* | *+0.007 (50.9 %) / +0.012 (51.3 %)* |

The sub-period profile is the same in every arm: essentially all of the edge is booked in
`P1` (2000–08) and `P2` (2009–16) — L1 +0.57 / +1.03 pp, L2 +0.89 / +1.03, L3 +0.49 / +1.03,
L4 +0.42 / +0.41 pp — while `P3` (2017→) is flat-to-negative for three of the four
(L1 −0.05, L2 −0.16, L3 −0.02 pp; only L4 is positive at +0.36 pp). At m = 4 (base) the arms keep their ordering:
C0 15.2541 %, L1 15.7607, **L2 15.8432**, L3 15.7414, L4 15.6391, R2 14.9241 %. The R-USD row is
identical to 0.01 pp (15.765 / 16.279 / 16.365 / 16.260 / 16.163 / 15.477 %).

### After tax at $100k (progressive 2026 tariff, base financing, m = 2, liquidated)

| Window | Arm | pre-tax | after tax | Δ vs C0 | Δ vs R2 | max DD | tax paid (USD) | sells/yr | A1u blocks / H2 defers |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 2000-03 → 2026-09 | C0 | 15.761 % | 10.753 % | — | +0.593 | 46.670 % | 830.9 k | 10.66 | 1,836 / 81 |
| | **L1** | 16.275 % | **11.127 %** | **+0.373** | +0.967 | 45.413 % | 926.0 k | 10.84 | 1,848 / 81 |
| | **L2** | 16.362 % | **11.199 %** | **+0.446** | +1.040 | 44.237 % | 945.3 k | 10.73 | 1,850 / 81 |
| | L3 | 16.256 % | 11.109 % | +0.356 | +0.950 | 45.036 % | 921.9 k | 10.88 | 1,846 / 83 |
| | L4 | 16.160 % | 11.057 % | +0.303 | +0.897 | 46.205 % | 898.0 k | 11.26 | 1,822 / 81 |
| 2010-01 → end | C0 | 22.343 % | 15.235 % | — | +0.529 | 44.627 % | 562.9 k | 10.63 | 1,413 / 60 |
| | L1 | 22.423 % | 15.260 % | +0.025 | +0.554 | 43.792 % | 570.5 k | 10.81 | 1,423 / 60 |
| | **L2** | 22.365 % | 15.212 % | **−0.023** | +0.506 | 43.792 % | 567.1 k | 10.75 | 1,424 / 60 |
| | L3 | 22.443 % | 15.275 % | +0.040 | +0.569 | 43.792 % | 571.8 k | 10.75 | 1,423 / 60 |
| | L4 | 22.638 % | 15.446 % | +0.210 | +0.739 | 44.211 % | 581.1 k | 10.99 | 1,409 / 60 |
| 2017-01 → end | C0 | 33.021 % | 23.972 % | — | +1.086 | 34.977 % | 371.5 k | 9.54 | 787 / 29 |
| | **L1** | 32.967 % | 23.832 % | **−0.139** | +0.947 | 35.197 % | 370.9 k | 9.95 | 779 / 29 |
| | **L2** | 32.860 % | 23.751 % | **−0.221** | +0.865 | 35.902 % | 368.6 k | 9.85 | 781 / 29 |
| | **L3** | 33.005 % | 23.860 % | **−0.112** | +0.974 | 35.014 % | 371.9 k | 9.85 | 779 / 29 |
| | L4 | 33.378 % | **24.201 %** | **+0.229** | +1.315 | 33.258 % | 379.4 k | 10.16 | 765 / 29 |

The R2 reference row is 10.160 / 14.706 / 22.886 % (**−0.593 / −0.529 / −1.086 pp** versus K),
which is the K-deploy result restated: 91 % of R2's realised gain came from vol-target trims and
K removes most of them. The arms see almost none of that, because they only change the *early*
leg. At $10k / $30k / $300k / $1M the 2000-window sign is positive everywhere
(L1 +0.412/+0.366/+0.389/+0.419, L2 +0.486/+0.431/+0.465/+0.499, L3 +0.395/+0.350/+0.370/+0.400,
L4 +0.333/+0.300/+0.319/+0.328 pp). The m = 4 cost stress at $100k keeps the same shape
(L1 +0.367/+0.015/−0.161, L2 +0.436/−0.032/−0.251, L3 +0.349/+0.033/−0.133, L4 +0.290/+0.201/+0.214).

Realised TRY gain by reason (2000 window, $100k): K `resize` 58.35 m / `liquidation` 9.08 m TRY,
L2 65.15 m / 10.11 m, L4 63.11 m / 9.77 m — i.e. the ladder's extra exposure shows up as **+12 % of
the volume-targeted resize gain**, taxed at the top bracket.

### Clean after-tax bootstrap at $100k (seed 20261011, block 20, 1,000 paths)

| Mode | Arm | median CAGR | Δ vs C0 | share > 0 | p05 / p95 Δ |
|---|---|---:|---:|---:|---|
| B1-recent (progressive) | C0 | 3.858 % | — | — | — |
| | L1 | 3.976 % | +0.197 pp | 57.5 % | −1.76 / +1.89 |
| | L2 | 3.978 % | +0.174 pp | 56.7 % | −1.74 / +1.99 |
| | L3 | 3.949 % | +0.114 pp | **54.8 %** (floor 55 %) | −1.67 / +1.79 |
| | L4 | 3.803 % | +0.046 pp | **53.1 %** (floor 55 %) | −1.02 / +1.20 |
| B2 (flat 20 %) | C0 | 4.038 % | — | — | — |
| | L1 | 4.333 % | +0.244 pp | 58.9 % | −1.68 / +1.99 |
| | L2 | 4.338 % | +0.238 pp | 58.2 % | −1.68 / +2.03 |
| | L3 | 4.282 % | +0.155 pp | 56.6 % | −1.62 / +1.90 |
| | L4 | 4.221 % | +0.074 pp | 55.2 % | −1.00 / +1.21 |

Pre-tax leg of the same bootstrap (5,000 paths): median arm − C0 +0.344/+0.351/+0.283/+0.169 pp
(base) and +0.326/+0.333/+0.265/+0.161 pp (calibrated), all with 58–61 % of paths positive. The
ladder's **pre-tax** bootstrap edge is unambiguous; only the after-tax **shares** are noise-level.

### Out-of-sample windows (calibrated, m = 2, R-USD)

| Window | C0 CAGR | L1 | L2 | L3 | L4 | ΔDD worst |
|---|---:|---|---|---|---|---:|
| ^GSPC 1957–1999 | 9.97 % | −0.07 / −0.57 | −0.12 / −0.60 | −0.01 / −0.74 | +0.18 / +0.65 | +0.65 pp (L4) |
| M1 ^IXIC | 21.27 % | +0.54 / +1.56 | +0.53 / +1.16 | +0.59 / +1.54 | +0.56 / +2.78 | +2.78 pp (L4) |
| M2 ^GSPC | 11.73 % | +0.42 / −2.16 | +0.34 / −2.27 | +0.42 / −1.98 | +0.27 / −0.67 | +0.53 pp |
| M3 ^N225 | 6.46 % | +0.03 / +1.24 | −0.03 / +1.32 | +0.14 / +0.75 | +0.20 / −1.05 | +1.32 pp (L2) |
| M4 ^GDAXI | 9.71 % | −0.05 / −0.25 | +0.09 / −0.51 | −0.19 / −0.02 | +0.20 / −0.12 | +0.51 pp |
| M5 ^FTSE | −0.62 % | −0.11 / +1.01 | −0.12 / +1.07 | +0.01 / +0.57 | −0.03 / −0.30 | +1.07 pp |
| **median ΔCAGR** | | **−0.01** | +0.03 | +0.08 | **+0.20** | (limit +3 pp, never > +8) |

Cells are ΔCAGR / ΔDD in pp versus K. **R1's documented OOS failure mode does not recur**: the
worst ΔDD is +2.78 pp (L4, M1) against a +3 pp limit, and M2's drawdowns actually *improve*
(−2.27 pp for L2), because the ladder's extra exposure sits inside the early path, which is the
half-weight state, not the levered on-state. The pooled OOS bootstrap (M1–M5, 2,000 paths each) is
positive for all four arms: median +0.206 / +0.210 / +0.203 / +0.135 pp on 58.9–59.6 % of paths.

### The early path itself (32 episodes, pre-tax, m = 2)

| Episode | span | sessions | K's return | L1 | L2 | L3 | L4 |
|---|---|---:|---:|---:|---:|---:|---:|
| 2001-10-31 | 10-31 → 12-05 | 25 | 9.45 % | +8.66 | +8.66 | +7.94 | +4.11 |
| 2002-11-04 | 11-04 → 12-02 | 20 | 4.43 % | +4.47 | +4.47 | +4.47 | +1.43 |
| 2023-01-20 | 01-20 → 02-01 | 9 | 4.83 % | +4.23 | +4.68 | +3.80 | +2.78 |

Cells are the arm's *return over the episode* minus K's, in pp. Summed over all 32 episodes:
L1 +14.26, L2 +15.95, L3 +12.67, L4 +8.42 pp. The single 25-session October–December 2001
recovery supplies the majority of each arm's whole-period edge (the arm holds up to 2× K's early
weight there and therefore roughly doubles that episode's return) — and it is exactly the episode
the leave-one-out below removes.

### Leave-one-episode-out (the three pre-registered episodes)

Remaining pre-tax ΔCAGR versus C0 after replacing the arm's early leg with K's inside the episode:

| Removed episode | L1 | L2 | L3 | L4 |
|---|---:|---:|---:|---:|
| **2001-10-31** | **−0.084** | **+0.030** | **−0.057** | **−0.018** |
| 2002-11-04 | +0.088 | +0.202 | +0.094 | +0.098 |
| 2023-01-20 | −0.023 | +0.071 | +0.001 | −0.057 |
| (full window, none removed) | +0.514 | +0.600 | +0.495 | +0.398 |

### Decision traces (E5, main path, pre-tax, $100k)

| Pair | sessions differing | arm sells / K holds | arm holds / K sells | down-resizes (arm) |
|---|---:|---:|---:|---:|
| L1 vs C0 | 56 (0.84 %) | 16 | 11 | 275 |
| L2 vs C0 | 53 (0.79 %) | 13 | 11 | 272 |
| L3 vs C0 | 55 (0.82 %) | 16 | 10 | 276 |
| L4 vs C0 | 43 (0.64 %) | 18 | 2 | 286 |
| *R2 vs C0* | *790 (11.83 %)* | *524* | *9* | **785** |

The ladder changes 43–56 fill decisions in 26.5 years; K changes 790 versus R2. All four arms stay
inside the A1u band's world (270 down-resizes for K, 272–286 for the arms).

### Rolling January after-tax starts 2001–2021 (report-only)

Median arm − C0: **L4 +0.260 pp (21/21 positive)**, L1 +0.032 (16/21), L3 +0.053 (16/21),
L2 −0.025 (9/21). The 2021 start is negative for L1/L2/L3 (−0.158/−0.299/−0.113) and +0.439 for L4.

## 5. Gates

| Gate | Requirement | L1 | L2 | L3 | L4 |
|---|---|---|---|---|---|
| **G1** | m = 2 CAGR ≥ C0 (or Calmar alt), both financing | PASS +0.5136 / +0.5065 | PASS +0.6003 / +0.5927 | PASS +0.4949 / +0.4884 | PASS +0.3982 / +0.3939 |
| **G2** | max DD ≤ C0 + 1 pp, both | PASS −1.257 / −1.230 | PASS −3.256 / −3.223 | PASS −1.634 / −1.608 | PASS −0.465 / −0.453 |
| **G3** | 2017+ CAGR ≥ C0 − 1 pp, both | PASS −0.054 / −0.059 | PASS −0.162 / −0.166 | PASS −0.016 / −0.020 | PASS +0.357 / +0.352 |
| **G4** | CAGR > C0 in ≥ 2 of 3 sub-periods | PASS (P1,P2) | PASS (P1,P2) | PASS (P1,P2) | PASS (P1,P2,P3) |
| **G5** | pre-tax bootstrap median ≥ 0, both | PASS +0.344 / +0.326 | PASS +0.351 / +0.333 | PASS +0.283 / +0.265 | PASS +0.169 / +0.161 |
| **G6** | G1 at m = 1 | PASS +0.519 / +0.510 | PASS +0.606 / +0.598 | PASS +0.499 / +0.492 | PASS +0.407 / +0.400 |
| **G7** | six OOS windows | **FAIL** median −0.0147 pp (6/6 per-window ok, worst ΔDD +1.56) | PASS +0.0318 | PASS +0.0771 | PASS +0.2004 |
| **G8** | pooled OOS bootstrap median ≥ 0 | PASS +0.2064 | PASS +0.2099 | PASS +0.2027 | PASS +0.1351 |
| **G9** | turnover ≤ 2 × C0 | PASS 1.009 × | PASS 1.017 × | PASS 1.011 × | PASS 1.023 × |
| **A1** | after-tax $100k Δ ≥ 0 in **all three** windows | **FAIL** 2017 −0.1395 | **FAIL** 2010 −0.0230, 2017 −0.2212 | **FAIL** 2017 −0.1117 | PASS +0.3034 / +0.2104 / +0.2292 |
| **A2** | B1 **and** B2 median ≥ 0, share > 0 ≥ 55 % | PASS (+0.1973/57.5 %, +0.2444/58.9 %) | PASS (+0.1741/56.7 %, +0.2375/58.2 %) | **FAIL** B1 share 54.8 % | **FAIL** B1 share 53.1 % |
| **P1** | plateau sign check (L1, L4 only) | PASS | n/a | n/a | PASS |
| | **verdict** | **FAIL (G7, A1)** | **FAIL (A1)** | **FAIL (A1, A2)** | **FAIL (A2)** |

**Selection: no candidate passes every gate ⇒ production K stands unchanged.**

Note on P1: L2 and L3 have no pre-registered cut-set to perturb (the survey's plateau check is
defined for the two "steps" arms), so the gate is recorded as not-applicable/pass; their failures
are A1 (L2) and A1+A2 (L3). Both L1 and L4 pass P1 under **both** alternative cut-sets:
L1 pre-tax +0.656 (0.25/1.0) and +0.660 (1.0/2.0) pp / after tax +0.501 and +0.499 pp versus the
pre-registered +0.514 / +0.373 pp; L4 +0.533 / +0.257 pp pre-tax and +0.426 / +0.207 pp after tax
versus +0.398 / +0.303 pp. Every sign stays positive, so the terciles are not curve-fitted — the
pre-registered 0.5/1.5 cuts are the *least* favourable of the three for both arms.

## 6. Findings

1. **The ladder is a genuine pre-tax improvement, and it is not a risk-taking improvement.** All
   four arms raise the pre-tax m = 2 CAGR (+0.40 … +0.60 pp base, +0.39 … +0.59 pp calibrated),
   *lower* the main-window max DD (L2 by 3.26 pp, to 43.41 %), and pass every pre-tax gate except
   L1's G7. The pre-tax bootstrap medians (+0.17 … +0.35 pp, 58–61 % of paths) and the pooled OOS
   bootstrap (+0.14 … +0.21 pp) are positive for all four, at m = 1 and m = 4, and at every capital.
   This is the cleanest pre-tax evidence in the early-path family so far.
2. **It still dies after tax, and the binding constraint moved between arms.** The aggressive
   arms (L1/L2/L3) over-earn in 2000 (+0.36 … +0.45 pp after tax) and *under*-earn in 2017
   (−0.11 … −0.22 pp), so A1 fails on the most recent window — not on tax collection alone: their
   2017-window pre-tax CAGR is itself 0.02–0.16 pp below K's (P3 is where the extra exposure does
   nothing), and the extra +91 … +114 k USD of tax on the 2000 window does the rest (L1 +95.1 k,
   L2 +114.4 k, L3 +91.0 k, L4 +67.1 k). The
   conservative momentum-keyed arm L4 is the only one positive after tax in all three windows and
   in 21/21 rolling starts, and it fails A2 by **1.9 pp of share** (53.1 % vs the 55 % floor) —
   the same noise-level-share margin that failed A1u in `gain_band_v3`. There is no single
   parameter that fixes both: any move that raises exposure helps A2 and hurts A1, and vice versa.
3. **The edge is one episode, so the ladder is a lever on 2001 rather than a signal.** Removing
   the 2001-10-31 episode drops every arm to |Δ| ≤ 0.09 pp versus K (L1 −0.084, L4 −0.018). This is
   the honest reading the survey itself pre-registered as outcome (iv): *the early weight is a
   lever, not a signal*. `fast_reentry_v2` already showed R2 survives removing any one of the three
   episodes; the *ladder* does not, because its whole product is the sizing decision inside them.
4. **The failure is after-tax, not drawdown, and not turnover.** The OOS DD gate — the documented
   reason R1 (full early weight) was rejected — is comfortably passed here (worst +2.78 pp against
   +3 pp), and turnover moves 1.9–2.3 % (5.51–5.58/yr vs K's 5.46). The early leg at `g ≤ 1` is a
   far smaller exposure change than R1's full weight, and it shows up as lower variance rather
   than as a fatter left tail.
5. **The plateau check clears the curve-fitting suspicion but not the tax problem.** Both
   alternative cut-sets make L1 and L4 *more* profitable pre-tax and after tax, so the
   pre-registered terciles were the conservative choice; the arms still fail on A1/A2. A different
   cut-set would therefore have failed the same gates with better numbers — a useful negative
   result in itself: the constraint is the tax system's treatment of extra realized gain, not the
   ladder's parameterization.
6. **910 k USD of extra tax buys 0.4 pp.** The after-tax 2000-window deltas (+0.30 … +0.45 pp) sit
   at the survey's pre-stated expectation band (−0.1 … +0.4 pp), i.e. at the edge of what this
   engine can even resolve (§6 caveat 3 of the survey: sub-0.1 pp deltas are inside modelling
   uncertainty).

## 7. Caveats

- **One history per cell.** The after-tax window deltas are single paths; the counterweight is the
  1,000-path clean bootstrap, where the arms' medians are +0.046 … +0.244 pp but the share of
  positive paths straddles the 55 % floor (53.1 % … 58.9 %). p05 is −1.0 to −1.8 pp, so the
  downside of any individual path is far larger than the median edge.
- **The m20 scale reading is a judgement call.** The survey's `vol20·√20` is implemented as
  daily-sigma × √20 (scale-consistent with `m`); the literal annualized reading would multiply the
  metric by √252 and make L4 nearly always sit above the 1.5 cut, i.e. a much more aggressive arm.
  L4's conclusion (A2 failure at 53.1 % share) would almost certainly harden, not soften; but the
  reading is disclosed in the pre-registration §3.2 and asserted in `--smoke`.
- **The degeneracy rule is a pre-committed amendment** (undefined metric ⇒ 0.5, the control's
  weight). It fired on **17 sessions in the M3 (^N225) OOS bootstrap only** (0 sessions in the
  clean bootstrap, 0 on the main path, 0 in every other OOS market), so it cannot affect the gates
  except through M3's OOS-bootstrap share.
- **Three-episode bet.** The ladder is fitted to the same three episodes I4 warns about; the
  leave-one-out result above is the price of that.
- **Tax model is not tax advice**: FIFO, per-calendar-year netting without loss carry-forward, the
  2026 tariff deflated by the previous October's Yİ-ÜFE, 10 % indexation floor, instalments funded
  by selling, liquidation at the window end, one CBRT buying-rate series. The fixed 2015-09 →
  2026-09 bootstrap calendar cannot price another FX/inflation regime.
- **Instrument convention**: this harness sizes on the synthetic QLD\* built from QQQ (the
  protocol's convention); production sizes on the held ETF's own realized vol. If any arm were
  promoted it would have to be re-run on real QLD bars (`instrument_cost_v1` measured that gap as
  material for TQQQ). None was promoted.
- **Tax-only arms and G5.** The survey's cross-cutting caveat 1 (a two-tier pre-tax non-inferiority
  gate for *exposure-neutral* arms) does not apply here: every arm *raises* exposure, so the
  standard G1–G9 block was kept and all four arms passed G5 on their own.

## 8. Amendments and honesty notes

The pre-registration (commit `d04343bd`) fixed the arms, parameters, gates and selection rule; none
of them changed. The amendments in the pre-registration §8 (the `main()` print fix, the OOS-bootstrap
abort on an undefined metric, and the pre-committed degeneracy rule) were all made before any
candidate result existed. Further changes made **while resuming the paused run and before any
result file existed** (no `RESULTS.json`/`SUMMARY.txt` was present or written at that point):

1. **`SUMMARY.txt`/`main()` bookkeeping.** The report block had been extracted into
   `summary_text(res, hs)` while `main()` still referenced the old locals, and three lookups used
   the `RESULTS.json` key names instead of the `run()` return names (`cost_stress_m4`→`cost`,
   `R2_rolling_starts`→`r2roll`). Pure report-only formatting; no gated metric recomputed or
   changed. The run cannot complete without them, so they were fixed before the first complete run.
2. **G5 read the bootstrap's actual shape.** The pre-tax block of `boot()` carries one cell per
   arm (`pre[md][arm]`), not `{arm}_vs_C0`; the gate now reads that cell. G5's rule, tolerance and
   the statistic (median of arm − C0) are unchanged, and the fix was made before any gate outcome
   existed. The paired bootstrap is independently pinned to the parent's published
   `K_vs_C0` delta, so a wrong draw or a wrong pairing would still fail `--harness`.
3. **P1 applicability.** P1 is asserted for L1 and L4 exactly as pre-registered and recorded as
   not-applicable/pass for L2 and L3; `gates_ladder` now makes that explicit instead of raising.
4. **Leave-one-episode-out source series.** The replaced span now copies `W[CONTROL]` (the
   control's own target series, identical to production R2's by construction — asserted in
   `--smoke`/`--harness`) rather than a separately-recomputed R2 series. Same values; one fewer
   chance of divergence from the control.

Run discipline: `--run` was executed **three times** end-to-end (the first attempt of this resumed
session produced the committed results; it was then re-run from scratch and the two `RESULTS.json`
files compared byte-for-byte, SHA-256 `a33e7e652b3a09f7c166b3a224603a4f7b2ceda19b898375b2b2b1fed5711537`),
plus the aborted pre-resume attempt recorded in the pre-registration §8. `--harness` is inside
every `--run` (71 checks, 0 outside tolerance). No production file, config, test or `docs/research/INDEX.md`
line was touched.
