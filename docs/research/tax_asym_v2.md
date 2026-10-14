# tax_asym_v2 — clean after-tax robustness of A2

**Verdict: FAIL — A2 does not satisfy the pre-registered after-tax gate.** Under the
Turkish progressive model on the recent calendar, A2 beats C0 on 57.5% of paths
(gate 65%) with a median Δ of +1.045 pp (gate +0.50). On the early calendar the
median is +0.391 pp and 53.3% of paths. Under flat 20% tax, the median is
+1.856 pp but only 61.5% of paths. Also report the C0 vs A1 gap for context
(median −0.699 to −2.211 pp, all worse).

## Diagnosis

tax_asym_v1's T1 bootstrap resampled the QQQ session jointly with its calendar date,
then mapped each date to USD/TRY and a tax assessment year. The tariff model records
realised gains in TRY as `proceeds = price_sell * FX_sell - price_buy * FX_buy * (cost
in TRY)`. Because blocks jump across decades, `FX_sell/FX_buy` can be orders of
magnitude; a buy in a strong-TRY period and sell in a weak-TRY period creates a
huge TRY gain and a tariff tax bill larger than the portfolio, liquidating to a
negative value. 643/1000 paths died this way and were then dropped, biasing the
surviving 357. The mechanism is an artifact of randomising calendar order; on real (monotonic
depreciation) history buy Fx <= sell Fx by construction, so it cannot occur there.

## Method
- Control C0 = production E2 (harness reproduces ewma_band_v1 row exactly:
  14.67% base / 14.10% calibrated m=2, 14.95% m=1, max DD 49.6%).
- Arms: C0, A1 (no vol target), A2 (downward-only vol resizes, k=0.5).
- Stationary block-20 bootstrap, seed 20261011, 1,000 paths, 10 measured years,
  base financing. Market returns are resampled; TRY series (B1) and flat-tax
  accounting (B2) are fixed/calibrated to each variant.
- Asserted no path has non-positive terminal liquidated USD value — 0 failures
  in all 3,000 paths.

## Results

### B1 — Turkish progressive model (recent calendar 2016-09 → 2026-09)
| metric | A2 vs C0 | A1 vs C0 |
|---|---:|---:|
| median Δ after-tax CAGR (pp) | +1.045 | −1.373 |
| mean Δ (pp) | +1.676 | — |  <!-- replace if needed -->
| share Δ > 0 | 57.5% | 42.3% |
| p05 Δ (pp) | −6.924 | — |
| p95 Δ (pp) | +12.261 | — |
| median Δ max DD (pp) | +27.346 | — |
| median C0 CAGR | 2.278% | 2.278% |
| median A2 CAGR | 3.270% | — |

### B1 — early calendar (2006-09 → 2016-09)
| metric | A2 vs C0 | A1 vs C0 |
|---|---:|---:|
| median Δ after-tax CAGR (pp) | +0.391 | −2.211 |
| share Δ > 0 | 53.3% | 38.5% |
| p05 / p95 Δ (pp) | −7.161 / +11.219 | — |
| median Δ max DD (pp) | +28.644 | — |

### B2 — simple 20% flat tax (recent calendar, no currency/inflation)
| metric | A2 vs C0 | A1 vs C0 |
|---|---:|---:|
| median Δ after-tax CAGR (pp) | +1.856 | −0.699 |
| mean Δ (pp) | +2.571 | — |
| share Δ > 0 | 61.5% | 46.5% |
| p05 / p95 Δ (pp) | −6.483 / +14.281 | — |
| median Δ max DD (pp) | +26.341 | — |

## Gate (pre-registered)
CONFIRMED iff (B1 recent AND B1 early AND B2) each have median Δ ≥ +0.50 pp and
share > 0 ≥ 65%.

| run | median ≥0.5? | share ≥65%? | pass? |
|---|:---:|:---:|:---:|
| B1 recent | yes (+1.045) | no (57.5) | no |
| B1 early  | no (+0.391) | no (53.3) | no |
| B2 flat  | yes (+1.856) | no (61.5) | no |

Gate: **FAIL** for A2.

### G-style context
- A1 (no vol target) is worse than C0 in every clean bootstrap (median
  −0.699 to −2.211 pp, 38.5–46.5% of paths positive), consistent with
  tax_frontier_v1.

## Caveats
- A2 raises after-tax CAGR on the median path in all three settings, but the edge
  is not reliable enough (53–62% of paths).
- Median Δ max DD is far worse for A2 than C0 (+26 to +29 pp). A2 does not trim
  on vol spikes, so deep 10-year synthetic paths punish it heavily; the same
  pattern appears pre-tax. This is the reason C0 remains the production rule.
- 0 of 3,000 paths produced non-positive terminal liquidated USD value; the
  tax_asym_v1 liquidation failure is resolved by fixing the calendar for the
  TRY series.
- 1,000 paths, stationary block-20, seed 20261011; byte-identical RESULTS.json
  on rerun (verified).
