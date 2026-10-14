# Vol-target level on E2, pre-tax and after Turkish tax, v1 (2026-10-14)

**No candidate passes the pre-registered rule, so production E2's 0.35 vol
target (`equity_leveraged_trend_v1` 1.1.0) stays unchanged.** No arm is
selected; the production identity is untouched.

Five vol-target levels were tested on production E2 (C0), changing nothing
else: 0.30, 0.40, 0.45, 0.50, and no target at all.

- **Every candidate fails the block-20 bootstrap (G5) under both financing
  models.** The median Δ CAGR against C0 is −0.11 pp at 0.30, −0.14 pp at
  0.40, −0.47 pp at 0.45, −0.76 pp at 0.50, −2.84 pp with no target.
  Calibrated financing is the same story (−0.08 to −2.91 pp). Each arm beats
  C0 on only 27–46% of paths.
- **0.40 is the closest miss and it is the same pattern
  `leverage_frontier_v1` found on VT35.** It gains +0.50 pp CAGR base
  (+0.48 pp calibrated) and passes G1, G3, G4, G6, G7, G8 and G9 — but fails
  the two tail checks and the bootstrap: G2 (max DD 54.49% against the 50.56%
  allowance), G5 (median −0.14 pp) and the tail gate by 0.18 pp of
  P(DD > 70%) (17.4% against a 17.2% limit, 18.3% against 18.0% calibrated).
- **The tail gate binds from 0.40 up.** P(DD > 70%) in 10-year bootstrap
  paths is 17.4% at 0.40 (limit 17.2%), 22.5% at 0.45, 27.2% at 0.50 and
  49.7% with no target, against C0's 12.2%. 0.30 is the only candidate that
  lowers it (7.0%), and 0.30 fails on return instead.
- **Turning the target down is not safe either.** 0.30 cuts max DD to 44.2%
  and P(DD > 70%) to 7.0%, but loses 0.87 pp of CAGR and fails G1, G3, G4,
  G5, G6, G7 and G8.
- **After tax the ranking flips, and it does not survive the bootstrap.**
  On the realized history at $100k the higher targets beat C0 after tax in
  all three windows (0.40: +0.61 to +1.72 pp), because a higher target
  reduces the number of taxable resizes (58.1 trades/yr for C0 against 43.0
  at 0.40, 30.8 at 0.45, 23.9 at 0.50). In the clean after-tax bootstrap the
  same arms have a **negative** median Δ (−0.09 to −0.69 pp) and beat C0 on
  37–48% of paths. The realized after-tax gain is a property of this path,
  not of the rule.

Evidence: `research/vt_level_tax_v1/`.

- `PREREGISTRATION.md` (including the Amendments section), `screen.py` and
  `DATA_MANIFEST.json` were committed in `15f8edb4`, before any non-control
  arm was simulated on real data.
- The outputs are `RESULTS.json` and `SUMMARY.txt` from `screen.py --run`.
  The run is offline and takes about 1 min 45 s. Two runs gave byte-identical
  `RESULTS.json`.
- Inputs are existing research files only (`ewma_band_v1_*`,
  `stress_robustness_v1_*`, `after_tax_tr_v1_*`), with SHA-256 in
  `DATA_MANIFEST.json`.

## Method

- **Control (C0).** Production E2 1.1.0: at least 2 of 3 votes on the QQQ
  price close (close > SMA200; 252-session price return > 0; close > the
  midpoint of the 126-session max/min). Size is `vt = min(1, 0.35/vol20)` on
  the synthetic QLD\* returns, total weight capped at 0.98.
- **Arms.** The same rule with `vt = min(1, T/vol20)` for T ∈ {0.30, 0.40,
  0.45, 0.50}, and T = none (weight = 0.98 whenever the signal is on). 0.35
  is C0. No other parameter changes.
- **Mechanics.** As in `e2_integration_v1` and `ewma_band_v1`:
  decide at the close, trade at the next open; 0.5 pp resize band (entries
  and exits always trade in full); cost 4 bps × m per unit traded, m = 2
  gated and m = 1 reported; idle cash earns zero; window 2000-03-09 →
  2026-09-29 (6,679 sessions).
- **Instrument.** Synthetic QLD\*, daily-reset 2× on QQQ total return.
  Financing is base (0.95% + DTB3) or calibrated (+0.75 pp/yr).
- **Harness.** C0 reproduces `e2_integration_v1`'s production-E2 row exactly
  (difference 0.000000 pp on CAGR and max DD, base and calibrated, m = 1 and
  m = 2). The band simulator at 0.5 pp equals
  `trend_robustness_v2.simulate_multi` bit for bit, and the windowed EWMA
  matches a direct computation to 7·10⁻¹⁶.
- **After-tax model.** `research/tax_frontier_v1`'s engine: Turkish
  progressive tariff (2026 brackets, Yi-UFE indexation of the acquisition
  month, 22,000 TRY dividend withholding threshold, instalments on Mar 31
  and Jul 31), USD/TRY from the CBRT buying rate, liquidation at the end of
  the window, $100k at t = 0. The market instrument is the same synthetic
  QLD\* (base financing, m = 2). Its C0 pre-tax CAGR on the unliquidated
  path is 14.6749% with a 49.5643% max DD — identical to the pre-tax engine
  to 1.4·10⁻¹⁴ on the whole NAV path, with the same 1,525 trades. That is
  the cross-check that the two engines share signal, instrument, timing and
  costs. The *liquidated* pre-tax CAGR is 0.003–0.011 pp lower (the final
  liquidation trade), and that is the figure the after-tax tables use.
- **Clean after-tax bootstrap (B1-recent, B2).** `tax_asym_v2`'s design: 1,000
  block-20 paths of ten measured years (2,520 sessions) drawn from QQQ open /
  close / dividend and DTB3, signal re-computed on the resampled price level,
  market returns resampled while the *calendar* (and therefore USD/TRY and
  Yi-UFE) stays real on a fixed 2015-09 → 2026-09 calendar. B1 uses the
  progressive regime, B2 a 20% flat tax on annual net realized USD gain with
  loss carry-forward. No path had a non-positive terminal liquidated USD
  value (0 of 1,000 in each of B1 and B2).
  - *Amendment 3.* In `tax_asym_v2` the bootstrap ran C0 on the **unlevered**
    QQQ total return while every other arm ran on 2× — its C0 median max DD is
    35%, not 50% — so any leveraged arm beats C0 by construction there. Here
    C0 runs on the same 2× synthetic as the candidates. The B1/B2 numbers below
    are therefore **not comparable** with `tax_asym_v2`'s.
- **Gates.** `ewma_band_v1`'s G1–G9 unchanged, each versus C0, plus
  `leverage_frontier_v1`'s tail gate. "Both" means base and calibrated.
  - **G1 (m = 2, both):** CAGR ≥ C0, or Calmar ≥ 1.1 × C0 with CAGR ≥ C0 −
    0.5 pp.
  - **G2 (both):** max DD ≤ C0 + 1 pp.
  - **G3 (both):** 2017+ CAGR ≥ C0 − 1 pp.
  - **G4:** CAGR above C0 in at least 2 of 3 sub-periods (P1 2000–08, P2
    2009–16, P3 2017 → end).
  - **G5 (both):** block-20 bootstrap median of arm − C0 ≥ 0 (5,000 ten-year
    paths, seed 20261011, draws identical to `signal_ensemble_v1`).
  - **G6 (both):** G1 at m = 1.
  - **G7:** six out-of-sample windows (^GSPC 1957–1999 and M1–M5),
    calibrated, m = 2; median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp in ≥ 5 of 6
    windows; Δ DD ≤ +3 pp in ≥ 5 of 6; Δ DD never above +8 pp.
  - **G8:** pooled OOS bootstrap over M1–M5, 2,000 paths each, median Δ ≥ 0.
  - **G9:** turnover ≤ 2 × C0 (all arms keep the production band).
  - **TAIL (`leverage_frontier_v1` G3):** P(DD > 70%) ≤ C0's + 5 pp, in the
    same block-20 bootstrap. Its second leg, max DD ≤ 65%, is implied by G2
    and is reported in the table.
- **Selection (pre-registered).** A target ≠ 0.35 is a candidate only if it
  passes G1–G8 pre-tax **and** the tail gate **and** beats C0 after tax at
  $100k in all three windows **and** has B1-recent/B2 median Δ ≥ +0.3 pp with
  a share of paths > 0 ≥ 60% **and** has a neighbouring target (C0, or the
  adjacent level in 0.30/0.35/0.40/0.45/0.50 order) passing G1–G3.

## Results (main window, m = 2)

| Arm | Target | CAGR base | CAGR cal. | Max DD (cal.) | Calmar | 2017+ | P1 / P2 / P3 | m = 1 base | Boot. median Δ base / cal. | P(DD>70%) base / cal. | Turnover/yr |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|---|---:|
| **C0 (prod. E2)** | **0.35** | **14.67%** | **14.10%** | **49.56% (49.65)** | **0.296** | **32.44%** | −1.10 / 13.26 / 32.44 | **14.95%** | — | **12.2% / 13.0%** | **5.97** |
| VT30 | 0.30 | 13.80% | 13.27% | 44.16% (44.24) | 0.313 | 30.45% | −0.59 / 11.84 / 30.45 | 14.09% | −0.108 / −0.075 pp | 7.0% / 7.6% | 6.28 |
| VT40 | 0.40 | 15.17% | 14.58% | 54.49% (54.58) | 0.278 | 34.10% | −1.73 / 13.95 / 34.10 | 15.44% | −0.142 / −0.164 pp | 17.4% / 18.3% | 5.83 |
| VT45 | 0.45 | 15.14% | 14.53% | 59.04% (59.13) | 0.257 | 34.72% | −2.33 / 14.01 / 34.72 | 15.41% | −0.470 / −0.502 pp | 22.5% / 23.8% | 5.68 |
| VT50 | 0.50 | 15.03% | 14.42% | 62.66% (62.75) | 0.240 | 35.46% | −2.99 / 13.73 / 35.46 | 15.29% | −0.763 / −0.811 pp | 27.2% / 28.4% | 5.61 |
| NOVT | none | 12.13% | 11.53% | 81.40% (81.48) | 0.149 | 35.37% | −9.36 / 12.69 / 35.37 | 12.39% | −2.845 / −2.907 pp | 49.7% / 50.6% | 5.80 |

The bootstrap median of C0 itself is 7.16% base / 6.73% calibrated; the
share of paths beating C0 is 45.6% (0.30), 43.8% (0.40), 39.0% (0.45), 36.2%
(0.50) and 27.2% (no target) base. The worst drawdown of every arm is the
same episode, 2000-03-27 → 2003-03-31; it is fully recovered on 2007-05-30
for C0, 2007-07-03 at 0.40 and not until 2014-07-16 with no target.

### Gates

| Gate | VT30 | VT40 | VT45 | VT50 | NOVT |
|---|---|---|---|---|---|
| G1 return (base / cal.) | fail / fail | pass / pass | pass / pass | pass / pass | fail / fail |
| G2 DD ≤ C0 + 1 pp | pass | **fail** (54.49 / 54.58) | **fail** (59.04 / 59.13) | **fail** (62.66 / 62.75) | **fail** (81.40) |
| G3 2017+ ≥ C0 − 1 pp | **fail** (−1.99 / −1.93) | pass | pass | pass | pass |
| G4 sub-periods | fail (P1) | pass (P2, P3) | pass (P2, P3) | pass (P2, P3) | fail (P3) |
| G5 bootstrap Δ ≥ 0 | **fail** (−0.108 / −0.075) | **fail** (−0.142 / −0.164) | **fail** (−0.470 / −0.502) | **fail** (−0.763 / −0.811) | **fail** (−2.845 / −2.907) |
| G6 G1 at m = 1 | **fail / fail** | pass / pass | pass / pass | pass / pass | fail / fail |
| G7 OOS windows | **fail** (median −0.27 pp, 4/6 within −0.5 pp) | pass (+0.28 pp) | pass (+0.59 pp) | pass (+0.76 pp) | **fail** (Δ DD +11.97 pp) |
| G8 OOS bootstrap | **fail** (−0.098 pp) | pass (+0.083 pp) | pass (+0.134 pp) | pass (+0.179 pp) | pass (+0.279 pp) |
| G9 turnover ≤ 2 × C0 | pass | pass | pass | pass | pass |
| TAIL P(DD>70%) ≤ C0 + 5 pp | pass (7.0 / 7.6) | **fail** (17.4 / 18.3) | **fail** (22.5 / 23.8) | **fail** (27.2 / 28.4) | **fail** (49.7 / 50.6) |
| TAIL leg max DD ≤ 65% | pass | pass | pass | pass | **fail** |
| Plateau | pass | pass | fail | fail | pass (vacuous) |
| **Result** | **fail** | **fail** | **fail** | **fail** | **fail** |

The tail-gate limits are 17.18% base and 18.00% calibrated. 0.40 misses the
base limit by 0.18 pp and the calibrated one by 0.28 pp — the same
order as `leverage_frontier_v1`'s closest miss (2.5×/0.35, 0.1 pp).

### Out of sample (calibrated, m = 2)

| Window | C0 | VT30 | VT40 | VT45 | VT50 | NOVT |
|---|---|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.98% / 39.0% | 9.53% / 37.8% | 10.24% / 39.2% | 10.32% / 39.3% | 10.37% / 39.3% | 10.38% / 39.3% |
| M1 ^IXIC 1975–1999 | 20.68% / 38.6% | 19.50% / 38.6% | 21.59% / 38.7% | 22.13% / 38.7% | 22.56% / 38.8% | 23.13% / 38.9% |
| M2 ^GSPC 1935–1956 | 11.26% / 62.5% | 11.19% / 60.3% | 11.46% / 63.7% | 11.92% / 63.0% | 12.30% / 62.7% | 13.89% / 59.6% |
| M3 ^N225 1966–2026 | 6.33% / 67.2% | 6.25% / 66.7% | 6.64% / 63.9% | 6.99% / 60.9% | 7.17% / 60.1% | 7.20% / 59.7% |
| M4 ^GDAXI 1989–2026 | 9.41% / 58.6% | 8.83% / 54.6% | 9.72% / 61.6% | 9.94% / 63.9% | 10.10% / 66.2% | 10.24% / 70.6% |
| M5 ^FTSE 1985–2026 | −0.05% / 82.7% | 0.00% / 81.6% | 0.04% / 82.9% | 0.01% / 83.4% | −0.06% / 83.6% | −0.01% / 83.7% |
| Median Δ CAGR | — | −0.27 pp | +0.28 pp | +0.59 pp | +0.76 pp | +0.85 pp |
| Max Δ DD | — | +0.01 pp | +2.95 pp | +5.25 pp | +7.63 pp | +11.97 pp |
| Pooled bootstrap median Δ (10,000 paths) | — | −0.098 pp | +0.083 pp | +0.134 pp | +0.179 pp | +0.279 pp |

Out of sample the higher targets are mildly better on return in every window
except ^GSPC 1957–1999 and M1 for 0.30; what fails is the drawdown spread —
+5.25 pp at 0.45, +7.63 pp at 0.50, +11.97 pp with no target — and, at 0.30,
the return itself.

### After tax at $100k (progressive Turkish regime, liquidated)

All figures are liquidated at the window end, so the pre-tax column here sits
0.003–0.011 pp below the same arm in the pre-tax table (the final liquidation
trade). Max DD is the after-tax NAV drawdown.

| Window | Arm | Pre-tax CAGR | After-tax CAGR | Δ vs C0 after tax | Max DD | Trades/yr | Tax paid (USD) |
|---|---|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | C0 | 14.67% | 9.53% | — | 49.56% | 58.1 | 646,780 |
| 2000-03 → 2026-09 | VT30 | 13.80% | 8.82% | −0.71 pp | 44.47% | 79.0 | 551,854 |
| 2000-03 → 2026-09 | VT40 | 15.16% | 10.14% | **+0.61 pp** | 54.49% | 43.0 | 737,575 |
| 2000-03 → 2026-09 | VT45 | 15.14% | 10.37% | **+0.84 pp** | 59.04% | 30.8 | 752,558 |
| 2000-03 → 2026-09 | VT50 | 15.03% | 10.45% | **+0.92 pp** | 62.66% | 23.9 | 721,169 |
| 2000-03 → 2026-09 | NOVT | 12.13% | 8.11% | −1.42 pp | 81.40% | 7.6 | 365,438 |
| 2010-01 → 2026-09 | C0 | 21.89% | 14.50% | — | 44.69% | 59.9 | 534,273 |
| 2010-01 → 2026-09 | VT30 | 20.61% | 13.50% | −1.00 pp | 44.37% | 84.2 | 465,210 |
| 2010-01 → 2026-09 | VT40 | 22.77% | 15.46% | **+0.96 pp** | 45.13% | 40.3 | 609,503 |
| 2010-01 → 2026-09 | VT45 | 22.97% | 15.83% | **+1.33 pp** | 45.32% | 29.2 | 623,763 |
| 2010-01 → 2026-09 | VT50 | 23.10% | 16.23% | **+1.73 pp** | 45.32% | 22.7 | 623,336 |
| 2010-01 → 2026-09 | NOVT | 22.51% | 16.05% | **+1.55 pp** | 45.36% | 7.5 | 571,436 |
| 2017-01 → 2026-09 | C0 | 32.26% | 22.69% | — | 37.61% | 67.4 | 352,698 |
| 2017-01 → 2026-09 | VT30 | 30.26% | 20.95% | −1.74 pp | 35.30% | 93.1 | 305,598 |
| 2017-01 → 2026-09 | VT40 | 33.90% | 24.41% | **+1.72 pp** | 39.38% | 47.4 | 408,080 |
| 2017-01 → 2026-09 | VT45 | 34.51% | 25.36% | **+2.67 pp** | 40.53% | 35.1 | 427,796 |
| 2017-01 → 2026-09 | VT50 | 35.24% | 26.50% | **+3.81 pp** | 40.38% | 24.9 | 444,176 |
| 2017-01 → 2026-09 | NOVT | 35.17% | 27.01% | **+4.32 pp** | 42.00% | 4.5 | 437,007 |

The tax bill bends the historical ranking: C0 loses 5.14 pp of its 2000–2026
CAGR to tax, 0.40 loses 5.02 pp, 0.45 4.77 pp and 0.50 4.58 pp, while 0.30
loses 4.98 pp and no target 4.02 pp. Higher targets trade less, so their tax
wedge is smaller, and on this path that is worth more than the pre-tax edge.

### Clean after-tax bootstrap (block-20, 1,000 paths, 2015-09 calendar)

Median after-tax CAGR of C0 on these paths: 3.08% (B1 progressive) and 3.52%
(B2 flat); median max DD 58.1% and 56.9%.

| Arm | B1-recent median Δ | B1 share > 0 | B1 p05 / p95 | B1 median Δ DD | B2 median Δ | B2 share > 0 | B2 p05 / p95 |
|---|---:|---:|---|---:|---:|---:|---|
| VT30 | −0.155 pp | 43.1% | −2.18 / +1.35 | −3.6 pp | −0.047 pp | 47.8% | −2.15 / +1.36 |
| VT40 | −0.088 pp | 46.3% | −1.27 / +1.79 | +2.7 pp | −0.153 pp | 42.8% | −1.28 / +1.65 |
| VT45 | −0.291 pp | 41.2% | −2.40 / +2.99 | +4.9 pp | −0.436 pp | 38.5% | −2.48 / +2.82 |
| VT50 | −0.506 pp | 39.3% | −3.39 / +3.84 | +6.6 pp | −0.694 pp | 37.1% | −3.49 / +3.68 |
| NOVT | −2.146 pp | 31.4% | −8.81 / +5.96 | +12.9 pp | −2.484 pp | 30.8% | −8.99 / +6.15 |

The gate needs median Δ ≥ +0.3 pp and share > 0 ≥ 60%; no arm reaches either.

### Trading activity (base, m = 2; relevant to the tax result)

| Arm | Round trips/yr | Resizes/yr | Sells/yr | Turnover/yr (entries+exits / resizes) | 2017+: round trips / sells / turnover |
|---|---:|---:|---:|---|---|
| **C0** | **2.94** | **51.5** | **27.4** | **5.97 (4.27 / 1.70)** | **1.23 / 32.4 / 3.75** |
| VT30 | 2.94 | 72.7 | 38.3 | 6.28 (3.82 / 2.46) | 1.23 / 45.4 / 4.49 |
| VT40 | 2.94 | 36.5 | 19.9 | 5.83 (4.64 / 1.19) | 1.23 / 23.0 / 3.27 |
| VT45 | 2.94 | 24.3 | 14.3 | 5.68 (4.91 / 0.77) | 1.23 / 17.4 / 2.93 |
| VT50 | 2.94 | 17.3 | 11.2 | 5.61 (5.09 / 0.52) | 1.23 / 13.0 / 2.68 |
| NOVT | 2.94 | 1.1 | 3.9 | 5.80 (5.79 / 0.01) | 1.23 / 2.8 / 2.42 |

Entries and exits are identical in every arm — the trend vote does not depend
on the target — so the whole tax difference comes from resizes, which fall
monotonically with the target (51.5 → 1.1/yr) because `min(1, T/vol)` hits
the 0.98 cap more often and holds it.

### Selection

`selected = none`. No arm passes G1–G8 pre-tax (all fail G5; 0.40/0.45/0.50
also fail G2; 0.45/0.50 additionally fail the tail gate), so the after-tax
conditions are moot. Full per-arm reason codes are in `RESULTS.json`
(`selection.detail`).

## Findings

- **The pre-tax frontier is flat, and the bootstrap says the slope is
  negative.** Between 0.30 and 0.50 the full-window CAGR spans 13.80% to
  15.17% (±0.7 pp around C0), while max DD spans 44.2% to 62.7% and
  P(DD > 70%) spans 7.0% to 27.2%. On bootstrap paths every candidate has a
  negative median Δ. This is the same conclusion `leverage_frontier_v1`
  reached on VT35 (SMA-only, pre-tax): the vol target trades return for tail
  risk, and no level clears both bars.
- **0.40 fails three checks, two of them marginally.** The tail gate by
  0.18 pp of P(DD > 70%), the bootstrap median by −0.14 pp, and G2 by 3.93 pp
  of full-window max DD. Nothing near the +1 pp return bar clears all of them,
  which is the same conclusion `leverage_frontier_v1` reached on its own grid
  (SMA-only VT35): there, the tail gate alone bound every cell that cleared
  G1, and the closest miss was 2.5×/0.35 at 0.1 pp. Switching the control
  from VT35 to E2 and adding the tax dimension does not change it.
- **0.30 is not the safe corner.** It is the only arm that improves max DD
  and the tail materially (49.6% → 44.2%, P(DD > 70%) 12.2% → 7.0%), and its
  Calmar rises (0.296 → 0.313), but it loses 0.87 pp of CAGR, fails the
  2017+ requirement (−2.0 pp), and also fails the bootstrap (median −0.11 pp,
  45.6% of paths). De-levering does not pay here either.
- **The tax argument for a higher target is real on the realized path and
  not robust.** Fewer resizes → fewer realization events → in the three
  windows at $100k the after-tax gain is +0.61/+0.96/+1.72 pp at 0.40 and
  +0.92/+1.73/+3.81 pp at 0.50. But the same rule loses on the bootstrap
  median in both tax models. The tension is that the bootstrap deliberately
  keeps the calendar (and so USD/TRY and Yi-UFE) real while resampling
  market blocks: the tax wedge is path-dependent, and the realized 2000–2026
  path happens to reward deferral.
- **No target is a trap.** It gains the most out of sample (median +0.85 pp
  over six windows, pooled +0.28 pp) and the most after tax in 2010/2017, but
  81% max DD, a 14.3-year underwater spell and P(DD > 70%) ≈ 50% are
  disqualifying, exactly as in `leverage_frontier_v1`.
- **Deleverage/tax interaction is monotone but not sufficient.** Trades/yr
  and thus realization events fall monotonically with the target, but the
  tax paid does not follow (C0 $646,780; 0.30 $551,854; 0.40 $737,575; 0.45
  $752,558; 0.50 $721,169; none $365,438), because a lower target also
  shrinks the tax base. Tax paid is the product of turnover and gains, and
  the arms differ in both.

## Caveats

- **Single history.** The realized-history after-tax ranking (higher target
  wins) rests on one 26.5-year path; the bootstrap is the counterweight and
  it disagrees. `leverage_frontier_v1` already saw the pre-tax frontier
  shape, so the pre-tax half is partly in-sample.
- **After-tax model.** Same assumptions and open items as
  `after_tax_tr_v1` / `tax_asym_v1` / `tax_asym_v2`: Yi-UFE indexation from
  the acquisition month to the sale month, the assessment year anchored at
  the previous October, the 2026 tariff schedule, the 22,000 TRY dividend
  threshold, liquidation at the end of the window, and one CBRT buying-rate
  series for USD/TRY. The B1/B2 bootstrap keeps the calendar real, so it
  cannot explore FX or inflation regimes outside 2015–2026.
- **Not comparable with `tax_asym_v2`.** Amendment 3: its bootstrap control
  ran on 1× QQQ. Re-running that study with a 2× C0 would change its
  conclusion, which is outside this campaign.
- **Costs.** 4 bps × 2 per unit traded, the project's standard. 0.30's higher
  turnover (6.28 vs 5.97/yr) is charged, but a 2× cost stress is not
  reported.
- **OOS windows are price-only** and many fills there are at the previous
  close, as in `ewma_band_v1`.

## What a production change would have required

None is proposed. The vol target is not a configuration value: it is
`VOL_TARGET` inside `equity_leveraged_trend_v1` (1.1.0). Moving it would be a
strategy-version bump with a new golden backtest, a Stage B replay, and a new
config/live-authorization digest. Recorded only for a future campaign.

## Decision

Production E2 keeps the 0.35 vol target. The campaign is closed; no code,
configuration or runtime changed.
