# vt_level_k_v1 — lowering production K's risk level: what it costs, what it buys

> Evidence: `research/vt_level_k_v1/` (code, PREREGISTRATION, DATA_MANIFEST, RESULTS.json,
> SUMMARY.txt) on branch `research/vt_level_k_v1`. Control = production **K**, reproduced
> cell-for-cell against `research/band_h2_combo_v1/RESULTS.json`. Engine = `research/fast_engine`
> (parity `--quick` PASS). Nothing was shipped; production K is unchanged.

## Verdict (plain language)

**Lowering K's risk level is a real but expensive trade, and the first step does not fix the
drawdown the owner actually asked about.**

- Going from the production `vt = 0.35` to **0.30** removes **5.3 pp** of pre-tax max drawdown
  (46.7 % → 41.4 %) and, after Turkish tax at $100k, **2.0 pp** (46.7 % → 44.7 %). It costs
  **0.87 / 1.07 / 1.83 pp per year** after tax on the 2000 / 2010 / 2017 windows. That is about
  **0.44 pp of after-tax return per pp of max drawdown removed**.
- Going to **0.25** removes **11.0 pp** of pre-tax max drawdown (46.7 % → 35.7 %) and **5.0 pp**
  after tax (46.7 % → 41.7 %). It costs **1.97 / 2.49 / 4.58 pp per year** after tax — about
  **0.40 pp per pp** removed, marginally cheaper per point than the first step.
- **The October 2026 episode is the key caveat.** At the 2026-10-05 peak the QQQ-derived 20-session
  volatility was **31.0 %**, so K (0.35) and V30 (0.30) were both essentially fully invested
  (targets 0.98 and 0.969) and V25 was at 0.807. The realised losses from 2026-10-05 to 2026-10-22
  were **K −13.42 %, V30 −13.33 %, V25 −11.20 %** (QQQ −6.84 %, real QLD −13.60 %). **The 0.30
  setting would have changed the October drawdown by essentially nothing**; only 0.25 helped, by
  2.2 pp. The vol-target knob is a *high-volatility* brake: it does very little when a crash starts
  from a calm market.
- **Where it does help:** the fixed crash windows show V30 cutting the dot-com drawdown by 5.3 pp,
  the GFC by 3.2 pp, COVID by 2.5 pp and 2022 by 3.1 pp — but the 2015-16 episode (China deval +
  oil) by only **0.2 pp**, and that is the episode that becomes the **after-tax** worst drawdown at
  both 0.30 and 0.25. The number of drawdowns deeper than 20 % after tax is **14 for K, 14 for V30,
  13 for V25**: lower risk makes the big ones shallower but does not make them rarer.
- **The trade is not free in turnover either.** Lowering `vt` *raises* trading: sells rise from
  10.7/yr at 0.35 to 14.0 (0.30) and 17.6 (0.25), because the target is no longer pinned at the
  0.98 cap most of the time and volatility changes now move the position. That is part of why the
  after-tax cost is not just proportional exposure.
- **No arm qualifies.** Both arms fail **G1, G3, G4, G5, G6, G7, G8, AT and BOOT**; only **G2
  (drawdown ≤ K + 1 pp)** and **G9 (turnover ≤ 2 × K)** pass. Per the pre-registration nothing is
  selected. If the owner wants less risk as a *preference*, **0.30 is a defensible but not free
  choice; 0.25 is the only setting that materially cut the October loss, at roughly double the
  after-tax price.**

## 1. Question and what is new

The owner asked after a ~12 % drawdown in October 2026 while QQQ fell ~6 %: *what does lowering the
risk level of K cost and buy?* This is a measurement of a risk-preference knob, not an alpha hunt.

Both nearest studies are pre-K or the wrong direction. `exposure_k_v1` measured **upward**
(0.375/0.40/0.45 under K: more after-tax return, more drawdown, negative clean-bootstrap medians)
and closed that axis. `leverage_frontier_v1` measured 2x/0.30 as 12.52 % CAGR / 44.2 % DD vs
0.35's 13.37 % / 49.6 %, but with no tax model and the E2-era control (no early path, no A1u, no
H2). `vt_level_tax_v1` measured 0.30/0.35/0.40/0.45 under **E2**, not K, and only upward arms were
discussed in its verdict. The downward direction under K has never been run.

K matters for this question because its tax layer changes the trade count: K already blocks most
of the vol-target trims (sells ~30 → ~10/yr), so a lower target's extra trades and smaller
realisations interact with A1u/H2 rather than with plain R2.

## 2. Control, data and reproduction

**Control = production K, bit for bit**: E2 (2-of-3 QQQ vote) + R2 early re-entry (0.5 × weight)
+ A1u (gain-scaled downsize band 0.5 / 3 / 8 pp at a USD FIFO gain fraction ≤ 1 % / ≤ 5 % / > 5 %)
+ H2 (non-exit December downsize postponed), `vt = min(1, 0.35 / vol20)`, cap 0.98, next-open
fills, 0.5 pp resize band, 4 bps × m cost, synthetic QLD* daily-reset 2x on QQQ total return
(0.95 %/yr expense), financing DTB3 + {base 0 %, calibrated 0.75 %}/yr.

**Inputs.** The same committed set as `band_h2_combo_v1` (QQQ/SPY/QLD bars + DTB3, CBRT USD/TRY,
Yİ-ÜFE, six OOS windows); `DATA_MANIFEST.json` records source, range and SHA-256. The committed bar
file ends **2026-09-29**, so the October episode is outside the gated window. One report-only Yahoo
download (`vt_level_k_v1_{qqq,qld}_2026q4.csv`, 2026-09-10 → 2026-10-22) supplies the episode; its
overlap with the committed bars is exact (QQQ closes identical). It is used only by stage EPI and
by no gate.

**Reproduction (`--harness` PASS, 0 cells outside tolerance).**

- `python -m research.fast_engine.parity --quick` → **PASS** (all sections, 0 mismatches).
- K's anchors: pre-tax CAGR **15.7649 %** base / **15.1785 %** calibrated, max DD **46.6698 % /
  46.7948 %**, m = 1 **16.0173 %**, turnover **5.4561**; after tax at $100k **10.7533 / 15.2354 /
  23.9720 %**. R2 reference row **15.4766 / 14.8961 %**, DD **46.7584 / 46.8801 %**, after tax
  **10.1600 / 14.7059 / 22.8861 %**. Pure E2 **14.6749 %**, DD **49.5643 %**.
- **Deep reference block:** every numeric leaf this run produces for K and R2 in `pre_tax_main`
  (base/calibrated × m = 1/2/4), `after_tax_windows` (all five capitals), the six OOS windows and
  the m = 4 cost stress matches `band_h2_combo_v1/RESULTS.json` within its stored precision
  (1,158 cells, 0 mismatches, max |diff| 4.99e-7). R2's cells map to that campaign's `C0`.
- The local target builder at `vt = 0.35` is **bitwise** equal to `fe_signals.sized_wants`; a
  400/200-path local bootstrap is **bitwise** equal to `fe_boot.boot`; the 5,000/1,000 clean
  bootstrap reproduces K **3.8577 / 4.0376 %**, R2 **3.5155 / 3.9678 %**, pre-tax K **7.7761 /
  7.3137 %**.

## 3. Arms and stages

`vt` is the **only** changed parameter.

| Arm | Rule |
|---|---|
| **K** | production K, `vt = 0.35` (control) |
| **V30** | K with `vt = 0.30` |
| **V25** | K with `vt = 0.25` |
| **R2** | production R2 (band off, no deferral, `vt = 0.35`) — reference row only |

Stages, all pre-registered before any non-control arm ran on real data: **pre** (base/calibrated ×
m = 1/2/4 + the R-USD row), **AT** (three windows × five capitals, progressive 2026 tariff,
liquidated), **B12** (clean bootstrap seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax
paths, $100k, B1-recent progressive + B2 flat 20 %), **OOS** (six `ewma_band_v1` windows) and the
**G8 pooled OOS bootstrap** (M1–M5, 2,000 paths each), **E2** (21 rolling January starts,
after tax, report), **E5** (m = 4 cost stress, report), **PROF** (drawdown profile of the pre-tax
and after-tax paths), **EPI** (October 2026, report only).

## 4. Results

### Pre-tax main window, m = 2 (2000-03-09 → 2026-09-29, 6,679 sessions)

| Arm | vt | CAGR base | CAGR cal. | max DD base | max DD cal. | Calmar base | m = 1 | m = 4 | turnover/yr | sells/yr | 2017+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **K** | 0.35 | 15.7649 % | 15.1785 % | 46.670 % | 46.795 % | 0.3378 | 16.0173 % | 15.2541 % | 5.456 | 10.17 | 33.231 % |
| **V30** | 0.30 | 14.7293 % | 14.1788 % | 41.392 % | 41.510 % | 0.3558 | 14.9831 % | 14.2122 % | 5.569 | 13.48 | 30.907 % |
| **V25** | 0.25 | 13.3466 % | 12.7811 % | 35.693 % | 35.952 % | 0.3739 | 13.6050 % | 12.8061 % | 5.657 | 17.28 | 27.807 % |
| R2 | 0.35 | 15.4766 % | 14.8961 % | 46.758 % | 46.880 % | 0.3310 | 15.7538 % | 14.9241 % | 5.992 | 29.56 | 32.714 % |

The return sacrifice is **slightly less than proportional** to the exposure cut (V30 keeps 93.4 %
of K's CAGR with 94.4 % of its average measured target weight; V25 keeps 84.7 % with 85.7 %) but
**drawdown falls faster** in the big crises. The
Calmar ratio improves (0.338 → 0.356 → 0.374), which is why G1 was written with a Calmar route;
V30 misses it (ratio 1.053 < 1.1) and V25 passes the ratio leg (1.107) but not the
`CAGR ≥ K − 0.5 pp` leg.

### After tax at $100k (progressive, liquidated)

| Window | Arm | After tax | Δ vs K | max DD | tax paid (USD) | sells/yr | trades/yr |
|---|---|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | K | 10.7533 % | — | 46.670 % | 830,903 | 10.66 | 32.94 |
| | **V30** | **9.8876 %** | **−0.866** | 44.683 % | 657,102 | 13.97 | 44.43 |
| | **V25** | **8.7834 %** | **−1.970** | 41.716 % | 510,158 | 17.58 | 56.44 |
| | R2 | 10.1600 % | −0.593 | 46.758 % | 766,160 | 30.69 | 62.65 |
| 2010-01 → end | K | 15.2354 % | — | 44.627 % | 562,873 | 10.63 | 31.83 |
| | **V30** | **14.1708 %** | **−1.065** | 44.410 % | 464,515 | 13.86 | 45.45 |
| | **V25** | **12.7466 %** | **−2.489** | 41.455 % | 378,587 | 18.99 | 60.86 |
| | R2 | 14.7059 % | −0.529 | 43.784 % | 553,536 | 31.35 | 62.17 |
| 2017-01 → end | K | 23.9720 % | — | 34.977 % | 371,487 | 9.54 | 33.34 |
| | **V30** | **22.1433 %** | **−1.829** | 31.618 % | 302,769 | 13.34 | 47.50 |
| | **V25** | **19.3909 %** | **−4.581** | 29.923 % | 242,727 | 17.75 | 59.30 |
| | R2 | 22.8861 % | −1.086 | 36.243 % | 360,297 | 35.60 | 70.90 |

The tax bill itself *falls* (830.9 k → 657.1 k → 510.2 k USD): lower targets realise smaller
gains. The after-tax return still falls because the pre-tax return falls faster. At m = 4 the signs
and sizes are unchanged: V30 −0.872 / −1.100 / −1.851 pp, V25 −1.995 / −2.554 / −4.687 pp.

### Clean bootstrap at $100k (seed 20261011, block 20, 1,000 paths)

| Mode | Arm | median CAGR | Δ vs K (share > 0) | p05 / p95 of Δ |
|---|---|---:|---|---|
| B1-recent progressive | K | 3.8577 % | — | — |
| | **V30** | 3.6338 % | **−0.104 pp (45.7 %)** | −2.20 / +1.37 |
| | **V25** | 3.4219 % | **−0.490 pp (40.5 %)** | −4.85 / +2.68 |
| B2 flat 20 % | K | 4.0376 % | — | — |
| | **V30** | 3.9083 % | **−0.062 pp (47.7 %)** | −2.27 / +1.37 |
| | **V25** | 3.7244 % | **−0.381 pp (43.0 %)** | −4.97 / +2.65 |

Pre-tax legs (5,000 paths): K 7.7761 / 7.3137 % (base / calibrated); V30 7.6580 / 7.2265 %
(−0.111 / −0.080 pp, 45.5 / 47.1 % positive); V25 7.1279 / 6.7451 % (−0.624 / −0.544 pp,
38.8 / 40.4 % positive). Every median is negative and every share is below 50 %, so G5 and BOOT
fail on their point estimates, not on a threshold technicality.

### Drawdown profile (main window)

**Pre-tax base m = 2.** Episode cells are *count / median recovery days / max recovery days* for
episodes deeper than the threshold; censored episodes are counted but excluded from recovery
statistics.

| Arm | max DD | annual vol | worst 21-s | worst 63-s | >10 % | >15 % | >20 % | >30 % | >40 % |
|---|---:|---:|---:|---:|---|---|---|---|---|
| **K** | 46.670 % | 26.70 % | −29.90 % | −31.95 % | 29 / 102.5 / 1423 | 23 / 121.5 / 1423 | 13 / 185 / 1423 | 7 / 391 / 1423 | 1 / 1423 / 1423 |
| **V30** | 41.392 % | 24.84 % | −29.80 % | −31.72 % | 29 / 109 / 956 | 21 / 156 / 956 | 13 / 269 / 956 | 4 / 503.5 / 956 | 1 / 956 / 956 |
| **V25** | 35.693 % | 22.24 % | −27.06 % | −28.75 % | 29 / 105 / 952 | 19 / 179 / 952 | 9 / 391 / 952 | 2 / 706 / 952 | 0 |
| R2 | 46.758 % | 26.47 % | −29.88 % | −31.93 % | 29 / 103 / 1453 | 23 / 122 / 1453 | 13 / 183 / 1453 | 7 / 392 / 1453 | 1 / 1453 / 1453 |

**After tax at $100k.**

| Arm | max DD (dates) | annual vol | worst 21-s | worst 63-s | >10 % | >15 % | >20 % | >30 % | >40 % |
|---|---:|---:|---:|---:|---|---|---|---|---|
| **K** | 46.670 % (2000-03→2003-03) | 27.10 % | −34.05 % | −36.19 % | 26 / 103 / 1453 | 21 / 127 / 1453 | 14 / 186.5 / 1453 | 6 / 558.5 / 1453 | 2 / 1015 / 1453 |
| **V30** | 44.683 % (2015-07→2016-07) | 25.20 % | −34.09 % | −36.01 % | 27 / 111.5 / 956 | 20 / 183 / 956 | 14 / 261 / 956 | 6 / 561 / 956 | 2 / 768 / 956 |
| **V25** | 41.716 % (2015-03→2016-07) | 22.57 % | −30.60 % | −32.44 % | 28 / 124 / 952 | 21 / 183 / 952 | 13 / 278 / 952 | 3 / 673 / 952 | 1 / 673 / 673 |
| R2 | 46.758 % (2000-03→2003-03) | 26.83 % | −33.45 % | −35.57 % | 25 / 104 / 1675 | 20 / 126 / 1675 | 13 / 190 / 1675 | 6 / 559.5 / 1675 | 2 / 1126 / 1126 |

Three things stand out. (1) **The tail severity barely improves at the first step after tax**:
worst 21-session loss is −34.05 % (K) vs −34.09 % (V30); only V25 moves it (−30.60 %). (2) **The
recovery length of the deepest episodes shortens a lot**: the max recovery falls from 1,423–1,453
sessions to ~956 (V30) and ~952 (V25), and the number of >30 % episodes falls 7 → 4 → 2 pre-tax
(6 → 6 → 3 after tax). (3) **The episode count at 10–20 % does not fall** (29 → 29 → 29 pre-tax,
26 → 27 → 28 after tax): the knob shrinks the big holes, it does not reduce how often the account
is in one.

### The cost per point of drawdown removed

Pre-registered definition: `(K CAGR − arm CAGR) / (K max DD − arm max DD)` in pp per pp, main
window, $100k after tax (and the same on the pre-tax path). Note that the after-tax max DD is a
*different episode* for V30/V25 (2015-16) than for K (2000-02); the fixed-window table below pins
the event.

| Arm | after-tax CAGR given up | after-tax max DD removed | **pp/yr per pp** | pre-tax CAGR given up | pre-tax DD removed | pp/yr per pp |
|---|---:|---:|---:|---:|---:|---:|
| **V30** | 0.866 pp | 1.99 pp | **0.436** | 1.036 pp | 5.28 pp | 0.196 |
| **V25** | 1.970 pp | 4.95 pp | **0.398** | 2.418 pp | 10.98 pp | 0.220 |

Read the after-tax row as: **one point of max drawdown removed costs about 0.40–0.44 pp/yr of
after-tax CAGR** at this part of the frontier — the two steps are nearly on one line, with the
second step marginally cheaper per point.

### Fixed crash windows (same event for every arm; return / max DD)

| Window | K | V30 | V25 | R2 |
|---|---|---|---|---|
| **pre-tax** dot-com 2000-03→2003-03 | −44.8 / 46.7 | −39.7 / 41.4 | −34.1 / 35.7 | −44.8 / 46.8 |
| GFC 2007-10→2009-03 | −23.4 / 32.4 | −19.9 / 28.6 | −17.6 / 24.7 | −23.5 / 32.4 |
| 2015-16 China/oil | −23.3 / 38.5 | −23.9 / 38.3 | −22.8 / 35.6 | −22.7 / 38.0 |
| COVID 2020-02→2020-04 | −12.2 / 28.3 | −9.5 / 25.4 | −6.4 / 20.9 | −11.7 / 27.8 |
| 2022 bear | −20.7 / 23.1 | −17.9 / 20.1 | −15.5 / 17.5 | −20.2 / 22.6 |
| **after tax $100k** dot-com | −44.8 / 46.7 | −39.7 / 41.4 | −34.1 / 35.7 | −44.8 / 46.8 |
| GFC | −26.6 / 35.2 | −23.8 / 32.0 | −21.3 / 28.1 | −27.4 / 35.9 |
| 2015-16 | −31.2 / 44.9 | **−31.7 / 44.7** | −29.4 / 41.2 | −30.2 / 44.0 |
| COVID | −14.0 / 29.8 | −11.8 / 27.3 | −8.6 / 22.7 | −14.0 / 29.7 |
| 2022 | −25.4 / 26.9 | −22.5 / 23.8 | −22.0 / 23.3 | −26.4 / 27.9 |

The 2015-16 window is the exception that matters: **V30 gave up 0.5 pp of return and removed only
0.2 pp of drawdown**, and that window is the worst after-tax drawdown for both lower arms. The
knob protects most in the volatility-led bear markets (2000-02, 2008, 2020, 2022) and least in the
fast, low-volatility corrections (2015-16, October 2026).

### OOS and additional checks

| Window | V30 Δ CAGR | V25 Δ CAGR | V30 Δ DD | V25 Δ DD |
|---|---:|---:|---:|---:|
| ^GSPC 1957-1999 | −0.545 | −0.938 | −0.82 | −2.52 |
| M1 ^IXIC 1971-1999 | −1.088 | −2.060 | −0.15 | −1.65 |
| M2 ^GSPC 1934-1956 | −0.040 | −1.063 | −2.39 | −6.19 |
| M3 ^N225 | −0.079 | −0.052 | −1.29 | −4.32 |
| M4 ^GDAXI | −0.669 | −1.541 | −3.54 | −9.01 |
| M5 ^FTSE | +0.131 | −0.038 | −1.46 | −2.12 |
| **median** | **−0.312** | **−1.000** | — | — |

The drawdown side of G7 is satisfied (Δ DD is negative everywhere), but the return side is not:
the median Δ CAGR is negative and only 3 of 6 (V30) / 2 of 6 (V25) windows are within −0.5 pp.
G8's pooled bootstrap over M1–M5 (10,000 paths) agrees: median Δ **−0.137 pp** (V30, 41.7 %
positive) and **−0.370 pp** (V25, 39.8 %). E2's 21 rolling January starts (after tax) give median
Δ **−1.455 pp** (V30) and **−3.308 pp** (V25). The m = 4 cost stress keeps every sign.

### The October 2026 episode (report only)

On the extended QQQ series (Yahoo bars appended to 2026-10-22), with the production signal and
fill model, measured from the 2026-10-05 close to 2026-10-22:

| Series | return | DD from prior peak | peak-to-trough inside |
|---|---:|---:|---:|
| K | −13.42 % | 13.42 % | 14.17 % |
| V30 | −13.33 % | 13.33 % | 14.08 % |
| V25 | −11.20 % | 11.20 % | 11.84 % |
| R2 | −13.38 % | 13.38 % | 14.13 % |
| QQQ | −6.84 % | 6.84 % | 7.27 % |
| QLD (real, B&H) | −13.60 % | 13.60 % | 14.40 % |

At the 2026-10-05 close the 20-session annualised QLD* volatility was **30.96 %**, so the targets
were K 0.98, V30 0.9689, V25 0.8074. **This is the whole explanation of why the first step did
nothing in the episode the owner asked about**: most of the drawdown was taken while the vol
target was at or near the 100 % cap. Less than 2 % of the account distinguished K from V30. V25's
17 % lighter position is the only reason its loss was 2.2 pp smaller.

## 5. Gates

Every gate is versus K; "both" = base and calibrated, m = 2.

| Gate | Requirement | V30 | V25 |
|---|---|---|---|
| **G1** | CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp (both) | **fail** (Calmar 1.053; CAGR −1.04 / −1.00 pp) | **fail** (Calmar 1.107 base / 1.096 cal; CAGR −2.42 / −2.40 pp) |
| **G2** | max DD ≤ K + 1.0 pp (both) | **pass** (−5.28 / −5.29 pp) | **pass** (−10.98 / −10.84 pp) |
| **G3** | 2017+ CAGR ≥ K − 1.0 pp (both) | **fail** (−2.32 / −2.27) | **fail** (−5.42 / −5.40) |
| **G4** | above K in ≥ 2 of 3 sub-periods | **fail** (1/3; only P1) | **fail** (1/3; only P1) |
| **G5** | pre-tax bootstrap median Δ ≥ 0 (both) | **fail** (−0.111 / −0.080; 45.5 / 47.1 % positive) | **fail** (−0.624 / −0.544; 38.8 / 40.4 %) |
| **G6** | G1 at m = 1 (both) | **fail** | **fail** |
| **G7** | OOS median Δ ≥ 0; ≥ 5/6 within −0.5 pp / +3 pp; Δ DD never > +8 pp | **fail** (median −0.312; 3/6; Δ DD all negative) | **fail** (median −1.000; 2/6; Δ DD all negative) |
| **G8** | pooled OOS bootstrap median Δ ≥ 0 | **fail** (−0.137) | **fail** (−0.370) |
| **G9** | turnover ≤ 2 × K | **pass** (1.021 × K) | **pass** (1.037 × K) |
| **AT** | after tax $100k Δ ≥ 0 in all three windows | **fail** (−0.866 / −1.065 / −1.829) | **fail** (−1.970 / −2.489 / −4.581) |
| **BOOT** | B1/B2 median Δ ≥ 0 and share > 0 ≥ 55 % | **fail** (−0.104 / −0.062; 45.7 / 47.7 %) | **fail** (−0.490 / −0.381; 40.5 / 43.0 %) |

E1 (the 21-ETF after-tax table) was not run (out of scope for this measurement); the m = 4 stress
is report-only and keeps V30 −0.87 / −1.10 / −1.85 pp and V25 −2.00 / −2.55 / −4.69 pp.

## 6. Selection

**No arm is selected.** Both candidates pass only G2 (the drawdown budget) and G9 (turnover).
The binding gates are G1/G3/G4 (the return *level* falls by more than the gate allows), G5/G8
(negative pre-tax bootstrap medians), and AT/BOOT (the after-tax return falls in all three
windows and in the clean bootstrap). This is a measurement result, not a rejected implementation:
the control reproduces K to the last stored digit and the arms differ only in the `vt` constant.

For a preference decision the owner can read the trade directly: **V30 costs ~0.9 pp/yr after tax
and removes ~2 pp of after-tax max DD (5.3 pp pre-tax); V25 costs ~2.0 pp/yr and removes ~5 pp
after tax (11 pp pre-tax).** If the objective is a smaller *October-style* loss, the evidence says
0.30 does not deliver it and 0.25 delivers about 2.2 pp for roughly 2 pp/yr of after-tax return.

## 7. Caveats

- **One history per cell.** The after-tax window deltas are single paths; the 1,000-path clean
  bootstrap and the 10,000-path OOS bootstrap are the counterweight, and both are negative for the
  return side.
- **The max-DD comparison mixes episodes after tax.** K's worst after-tax episode is 2000-02;
  V30/V25's is 2015-16. That is why the after-tax "DD removed" is only 2.0/5.0 pp while the pre-tax
  figure is 5.3/11.0 pp. The fixed-window table is the like-for-like comparison; it is a
  post-registration descriptive supplement (Amendment A2), used by no gate.
- **No true after-tax out-of-sample** (the TRY/Yİ-ÜFE calendar starts 2000). E2's 21 rolling
  starts and the OOS windows are the substitutes and they agree with the negative direction.
- **One bootstrap calendar** (fixed 2015-09 → 2026-09 with realised USD/TRY and Yİ-ÜFE): it cannot
  price another FX/inflation regime. The flat-tax B2 variant is the check, and it agrees.
- **The October episode is outside the gated window** and uses a downloaded extension of QQQ; it is
  pre-tax only because the committed Yİ-ÜFE series ends 2026-09 and USD/TRY ends 2026-10-09. The
  overlap check with the committed bars is exact, but a vendor revision would change the episode
  numbers, not any gate.
- **Tax model**: FIFO, per-calendar-year netting without loss carry-forward, the 2026 tariff
  deflated by the previous October's Yİ-ÜFE, a 10 % indexation floor, March/July instalments funded
  by selling, liquidation at the window end, one CBRT buying-rate series. **Not tax advice.**
- The vol target uses QLD*'s 20-session close-to-close vol, not QQQ's; the target-weight formula and
  the 0.98 cap are production's.

## 8. Run protocol

`--smoke` PASS (synthetic prices only) → pre-registration commit **94fae0b4** (before any
non-control arm ran on real data) → `--harness` PASS → `--run` twice, **byte-identical**
`RESULTS.json` (`4afc8b9e2db8cc3e3027350c0ea0d15e74588e4990011e13eb7321118e12ecf9`).
Wall clock of `--run`: **25.2 s** (second recorded run; the harness includes
`parity --quick`). `RESULTS.json` and `SUMMARY.txt` are in `research/vt_level_k_v1/`;
`DATA_MANIFEST.json` records every input.

## 9. Amendments and honesty notes

- **A1 (before any non-control run).** EPI's definition was clarified to report both the drawdown
  from the pre-episode peak and the peak-to-trough inside the episode. No gate uses EPI.
- **A2 (after the first complete exploratory `--run`, before the two recorded runs).** The
  fixed-crash-window table was added as a descriptive supplement, because the after-tax max DD
  belongs to different episodes for different arms. No gate, arm, parameter or selection rule
  changed.
- **Bug fixed before the recorded runs.** The report-only OOS bootstrap (G8) initially passed a
  one-row `out_pre` buffer to `fe_boot._boot_chunk`, which writes both financing rows: an
  out-of-bounds write that segfaulted on some path draws. The buffer is now `(2, 1, P)`; the G8
  numbers in this report are from the fixed code. The clean bootstrap never had this bug.
- **Determinism fix before the recorded runs.** The first complete run stored wall-clock fields
  inside `RESULTS.json`, which made two runs differ. Timing now lives in `SUMMARY.txt` only; the
  two recorded `RESULTS.json` files are byte-identical.
- No arm, parameter, stage, seed, threshold, gate or selection rule was changed after seeing data.
  The pre-registration is commit **94fae0b4**; the code commit is **40fa364d**.

Not tax advice. Production K is unchanged; no Stage B replay was run.
