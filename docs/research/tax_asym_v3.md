# Tax asymmetry v3 — A2 against a corrected 2× C0 (2026-10-14)

> Evidence for this report (`research/tax_asym_v3/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/tax-asym-v3-2026-10`.

**Verdict: FAIL — A2 does not pass the after-tax gate once C0 trades the same
2× synthetic as the candidate.** Production E2
(`equity_leveraged_trend_v1` 1.1.0) stays unchanged; no arm is selected.

`tax_asym_v2` ran its bootstrap with C0 on **unlevered** QQQ while A1/A2 ran on
the daily-reset 2× QLD\*, so its FAIL verdict was decided by an artifact, not by
A2. Re-running the identical design with the corrected engine changes the answer
substantially — and still fails:

- **A2 (k = 50%).** Median after-tax Δ CAGR +0.096 pp (gate +0.50) on 51.9% of
  paths (gate 65%) in B1-recent; −0.245 pp / 44.8% in B1-early; −0.121 pp /
  48.1% under flat 20% tax. Every variant fails both legs of the gate, and so
  does the pre-tax control on the same paths (−0.341 pp, 43.7% of paths).
- **The full unlevered-C0 edge is an artifact.** On the same 1,000 paths A2's
  median Δ falls from +1.045 pp (unlevered C0, `tax_asym_v2`'s number,
  reproduced here bit for bit) to +0.096 pp with C0 on 2×.
- **The plateau neighbours do not rescue it.** k = 0.60 is the best of the three
  (+0.182 / 54.2%, −0.165 / 45.6%, −0.070 / 47.7%) and k = 0.40 the worst
  (−0.261, −0.720, −0.514 pp); both fail every window. The ordering over k is
  the same as on the realized path, but the level is an order of magnitude
  smaller, so nothing clears the bar.
- **The corrected C0 level matches `vt_level_tax_v1`'s corrected engine
  exactly** (median after-tax CAGR 3.079% / 3.520% and median max DD 58.08% /
  56.85% for B1-recent / B2 against its 3.08% / 3.52% and 58.1% / 56.9%), which
  is the cross-check that this is the same control as the one that invalidated
  `tax_asym_v2`.
- **A2 also widens the bootstrap tail.** Median Δ max DD is +4.41 pp
  (p95 +10.59 pp) in B1-recent: holding weight through vol spikes, which is what
  creates the after-tax deferral on the realized path, is exactly what makes the
  resampled drawdowns worse.

Evidence: `research/tax_asym_v3/`.

- `PREREGISTRATION.md`, `screen.py`, `DATA_MANIFEST.json` and
  `REFERENCE_E2_DEVIATION.json` were committed in `0ac46313`, **before any
  non-control arm was simulated on real data**. The smoke test
  (`screen.py --smoke`, synthetic random-walk prices, no output file) and the
  control-only `--harness` ran before that commit; nothing else did.
- The outputs are `RESULTS.json` and `SUMMARY.txt` from `screen.py --run`; the
  run is offline, takes about 2 min 45 s, and two runs gave byte-identical
  `RESULTS.json` (SHA-256
  `fcc4b328bab1b383183436253b2d5f0a7768398e23d48b422e8cdec407b2fa64`) and
  `SUMMARY.txt`.
- Inputs are existing committed research files only — no data was downloaded.
  SHA-256 and provenance for each are in `DATA_MANIFEST.json`.
- The screen imports its engine helpers from `research/trend_robustness_v2/screen.py`
  and `research/leverage_frontier_v1/screen.py`, exactly as `tax_asym_v2` did; the
  latter is a closed study kept in tag
  `archive/research/leverage-frontier-v1-2026-10`.

## Verification of the `vt_level_tax_v1` Amendment 3 claim

`research/tax_asym_v2/screen.py:1486-1488`, inside
`after_tax_bootstrap_variant`:

```
PRICES["C0"] = (qo.flatten(), qc.flatten())   # qo/qc = UNLEVERED QQQ total return
PRICES["A1"] = (so.flatten(), sc.flatten())   # so/sc = daily-reset 2x synthetic
PRICES["A2"] = (so.flatten(), sc.flatten())
```

`qo`/`qc` are built from `r_on_tr = (open + div)/prev_close` and
`r_id = close/open` with no leverage (line 1464), while C0's *wants* were sized
from the 2× instrument's vol (`vt = lf.vt_weight(vol, 0.35)`, `vol` from `sc`,
lines 1472-1476). So C0 held `min(1, 0.35/vol₂ₓ)` of **1× QQQ**, and every
candidate held the same weight of **2× QLD\***. The corrected engine
(`research/vt_level_tax_v1/screen.py:1414`) assigns `PRICES[arm] = (so, sc)` to
every arm.

Numerical check (`screen.py --verify`, 1,000 paths, seed 20261011, $100k):

| Variant | | C0 on unlevered QQQ (`tax_asym_v2`) | C0 on 2× (corrected) |
|---|---|---:|---:|
| B1-recent | median after-tax CAGR | 2.278% | 3.079% |
| | median max DD | **34.93%** | **58.08%** |
| | A2 vs C0 median Δ CAGR | +1.045 pp | +0.096 pp |
| | A2 vs C0 share Δ > 0 | 57.5% | 51.9% |
| B1-early | median after-tax CAGR | 2.449% | 3.120% |
| | median max DD | **34.49%** | **57.84%** |
| | A2 vs C0 median Δ CAGR | +0.391 pp | −0.245 pp |
| | A2 vs C0 share Δ > 0 | 53.3% | 44.8% |
| B2-flat | median after-tax CAGR | 1.662% | 3.520% |
| | median max DD | **35.43%** | **56.85%** |
| | A2 vs C0 median Δ CAGR | +1.856 pp | −0.121 pp |
| | A2 vs C0 share Δ > 0 | 61.5% | 48.1% |

Replication: with `c0_on_1x=True` the per-path C0 CAGR and max-DD arrays are
**bit-identical** to `research/tax_asym_v2/RESULTS.json`
(`results.<variant>.C0`, max abs diff 0.0) in **all three** variants, and the
A2-vs-C0 medians reproduce its published values exactly (+1.0449722137975237,
+0.3908623668270439, +1.8561223173590102 pp for B1-recent / B1-early / B2). The
claim is verified: C0 was
unlevered — its bootstrap median max DD is 34.5–35.4% in every variant, against
56.9–58.1% when corrected — and that alone produced the 0.64–1.98 pp of apparent
after-tax edge that `tax_asym_v2` measured. The unlevered C0 also gave up
0.67–1.86 pp of median after-tax CAGR (0.80 pp in B1-recent), so the same weapon
that flattered A2 also handicapped the control.

## Method

- **Control (C0).** Production E2 1.1.0: at least 2 of 3 votes on the QQQ price
  close (close > SMA200; 252-session price return > 0; close > the midpoint of
  the 126-session max/min). Size `min(CAP, min(1, 0.35/vol20))`, CAP = 0.98.
- **Arms.** A2 = the same rule with downward-only vol resizes (upward resizes as
  production; a downward resize only on a trend exit or when target < k × held
  weight; `trim = 0`), at k = 0.50 (the `tax_asym_v2` candidate) and the plateau
  neighbours k = 0.40 and k = 0.60. Signal, instrument, timing, cost and the tax
  engines are identical across arms; only the resize decision differs. `tax_asym_v2`'s
  A1 (no vol target) is dropped — it is not the candidate and it loses everywhere.
- **Instrument.** Synthetic QLD\*, daily-reset 2× on QQQ total return, expense
  0.95%/yr, financing DTB3 + 0.00 (base). **C0 trades it too** (the fix).
- **Mechanics.** Decide at the close, trade at the next open, 0.5 pp resize band,
  cost 4 bps × m = 8 bps per unit traded, idle cash earns zero, $100k at t = 0,
  liquidation at the window end.
- **Bootstrap** (`tax_asym_v2`'s design, unchanged). Stationary block-20, seed
  20261011, 1,000 paths of 10 measured years (2,520 sessions), 3-session
  back-extension, WARM = 253. QQQ open/close/dividend and DTB3 are resampled
  jointly by session; signals and vol are recomputed on the resampled price
  level. The **calendar stays real and fixed**, so USD/TRY and Yi-UFE are the
  realized series on every path. Draws are identical for every arm on every path.
- **Tax models.** B1 = Turkish progressive, 2026 tariff brackets, Yi-UFE
  indexation of the acquisition month (relief only when the index rose ≥ 10%),
  22,000 TRY dividend withholding threshold, instalments on Mar 31 / Jul 31,
  assessment year anchored on the previous October. B2 = 20% flat on each year's
  net realized USD gain, losses carried forward. A third variant, `pretax`, is
  the identical mechanics with no tax and is reported for context only.
- **Assertion.** Every path's terminal liquidated USD value must be positive;
  0 of 4,000 paths (4 variants × 1,000) violated it.
- **Gate (pre-registered, `tax_asym_v2`'s).** CONFIRMED iff **B1-recent AND
  B1-early AND B2** each have median after-tax Δ CAGR ≥ +0.50 pp **and** share of
  paths with Δ > 0 ≥ 65%.
- **Harness.** C0 reproduces `e2_integration_v1`'s production-E2 deviation row
  exactly (base/calibrated × m = 1/2, all diffs +0.000000 pp, tolerance 0.01 pp);
  the band simulator at 0.5 pp equals `trend_robustness_v2.simulate_multi` bit
  for bit; and the after-tax engine's pre-tax C0 NAV equals the pre-tax engine's
  C0 NAV over the full window (max relative difference 1.9·10⁻¹³, same 1,525
  trades).

## Results

### Bootstrap level of C0 (median over 1,000 paths)

| Variant | C0 median after-tax CAGR | C0 median max DD |
|---|---:|---:|
| B1-recent | 3.079% | 58.08% |
| B1-early | 3.120% | 57.84% |
| B2-flat | 3.520% | 56.85% |
| pre-tax (same paths) | 7.157% | 53.54% |

`vt_level_tax_v1` reports 3.08% / 3.52% and 58.1% / 56.9% for the same two
variants of its (corrected) C0; `tax_frontier_v1` reports a pre-tax bootstrap C0
median of 7.16% against the 7.157% here. Both agree.

### Δ after-tax CAGR vs C0 (pp), and Δ max DD distribution

| Variant | Arm | median Δ | mean Δ | share > 0 | p05 / p95 | median Δ DD | Δ DD p05 / p95 |
|---|---|---:|---:|---:|---|---:|---|
| B1-recent | A2 (k = 50%) | **+0.096** | +0.401 | **51.9%** | −2.718 / +4.520 | +4.41 | −0.51 / +10.59 |
| B1-recent | A2_40 (k = 40%) | −0.261 | +0.085 | 46.5% | −3.527 / +5.147 | +5.89 | +0.32 / +12.63 |
| B1-recent | A2_60 (k = 60%) | **+0.182** | +0.339 | **54.2%** | −2.004 / +3.097 | +3.17 | −0.79 / +7.75 |
| B1-early | A2 | −0.245 | −0.061 | 44.8% | −3.034 / +3.688 | +5.00 | −0.18 / +10.83 |
| B1-early | A2_40 | −0.720 | −0.425 | 38.5% | −3.874 / +4.083 | +6.42 | +0.94 / +13.35 |
| B1-early | A2_60 | −0.165 | −0.017 | 45.6% | −2.222 / +2.598 | +3.56 | −0.37 / +8.66 |
| B2-flat | A2 | −0.121 | +0.173 | 48.1% | −2.996 / +4.299 | +5.09 | +0.61 / +11.07 |
| B2-flat | A2_40 | −0.514 | −0.167 | 42.0% | −3.915 / +4.688 | +6.63 | +1.24 / +13.55 |
| B2-flat | A2_60 | −0.070 | +0.099 | 47.7% | −2.156 / +2.968 | +3.74 | +0.28 / +8.28 |
| pre-tax | A2 | −0.341 | −0.092 | 43.7% | −3.462 / +3.971 | +4.75 | +0.21 / +10.81 |
| pre-tax | A2_40 | −0.866 | −0.539 | 37.8% | −4.440 / +4.355 | +6.27 | +1.19 / +13.34 |
| pre-tax | A2_60 | −0.253 | −0.127 | 42.9% | −2.525 / +2.844 | +3.49 | +0.01 / +8.10 |

### `tax_asym_v2` (unlevered C0) vs this run (2× C0), same 1,000 paths

| Variant | `tax_asym_v2` median Δ | corrected median Δ | `tax_asym_v2` share | corrected share | artifact (Δ of Δ) |
|---|---:|---:|---:|---:|---:|
| B1-recent | +1.045 pp | **+0.096 pp** | 57.5% | 51.9% | −0.949 pp |
| B1-early | +0.391 pp | **−0.245 pp** | 53.3% | 44.8% | −0.636 pp |
| B2-flat | +1.856 pp | **−0.121 pp** | 61.5% | 48.1% | −1.977 pp |

The corrected Δ columns are A2 (k = 50%), matching the arm `tax_asym_v2` tested;
all three rows of that table are reproduced bit for bit by the engine here
(see Verification).

### Gates

| Gate | A2 (k = 50%) | A2_40 | A2_60 |
|---|---|---|---|
| B1-recent median ≥ +0.50 pp | **fail** (+0.096) | fail (−0.261) | fail (+0.182) |
| B1-recent share > 0 ≥ 65% | **fail** (51.9%) | fail (46.5%) | fail (54.2%) |
| B1-early median ≥ +0.50 pp | **fail** (−0.245) | fail (−0.720) | fail (−0.165) |
| B1-early share > 0 ≥ 65% | **fail** (44.8%) | fail (38.5%) | fail (45.6%) |
| B2 median ≥ +0.50 pp | **fail** (−0.121) | fail (−0.514) | fail (−0.070) |
| B2 share > 0 ≥ 65% | **fail** (48.1%) | fail (42.0%) | fail (47.7%) |
| **Result** | **FAIL** | **FAIL** | **FAIL** |

Selection: `verdict = FAIL`, `primary_pass = false`, plateau `n/a`, no neighbour
passes. The pre-registered selection rule never reaches the A2/plateau branch.

## Findings

- **`tax_asym_v2`'s verdict was right for the wrong reason, and the right reason
  is weaker still.** The artifact inflated A2's median after-tax Δ by
  0.64–1.98 pp depending on the tax model. With C0 correct, the median edge
  collapses to +0.10 pp at best, in one of three variants, on 52% of paths.
- **The unlevered C0 was not a small inconsistency.** In B1-recent it cut C0's
  median after-tax CAGR by 0.80 pp and halved its bootstrap drawdown (34.93%
  against 58.08%), because a lower-vol equity in the same tax wrapper realizes
  smaller gains; across the three variants the handicap is 0.67–1.86 pp of
  CAGR and 21.4–23.4 pp of median max DD. Any leveraged arm beats that control
  on the median by construction — which is
  exactly what `vt_level_tax_v1` (Amendment 3) concluded, and what the
  bit-identical replication here demonstrates.
- **The after-tax case for A2 was always a single-path property.** The realized
  2000–2026 path gives A2 +1.62 pp after tax (`tax_frontier_v1`) and it beats C0
  in the 2000/2010/2017 windows. The bootstrap keeps the calendar real and
  resamples the market: on 1,000 ten-year paths the same rule has a negative
  after-tax median in two of three variants, and a **negative pre-tax** median
  (−0.341 pp, 43.7% of paths). The deferral gain is a property of this history,
  not of the rule.
- **The k plateau is flat, but at a level below the gate.** On the realized path
  the k ∈ {40%, 50%, 60%} family is nearly indistinguishable after tax. In the
  bootstrap the *ordering* is preserved (k = 60% > k = 50% > k = 40% in every
  variant, i.e. trimming earlier is slightly worse — the deferral argument) but
  the spread is ~0.4 pp, and the best value still lands at +0.18 pp / 54.2% in
  its best variant. Nothing in the neighbourhood is near the bar.
- **The pre-tax result is independently reproduced.**
  `tax_frontier_v1`'s pre-tax bootstrap gave −0.36 pp median and `tax_asym_v1`'s
  G5 gave −0.356 pp; the same-paths pre-tax bootstrap
  here gives −0.341 pp on 1,000 finite paths. A2's pre-tax edge is not robust
  either, which is consistent with its G2/G5 failures in `tax_asym_v1`.
- **A2 pays for the deferral in tail risk.** Median Δ max DD is +3 to +7 pp
  across variants and arms, with p95 up to +13.6 pp. Holding through vol spikes
  is what defers the tax; it is also what deepens the resampled drawdowns, the
  same trade-off that sank every higher vol target in `vt_level_tax_v1` and
  every higher leverage cell in `leverage_frontier_v1`.

## Caveats

- **After-tax model.** Same assumptions and open items as `after_tax_tr_v1` /
  `tax_asym_v1` / `tax_asym_v2` / `vt_level_tax_v1`: Yi-UFE indexation from the
  acquisition month to the sale month, the assessment year anchored at the
  previous October, the 2026 tariff schedule, the 22,000 TRY dividend threshold,
  instalments on Mar 31 / Jul 31, one CBRT buying-rate series for USD/TRY, and
  liquidation at the window end. The fixed calendar means the bootstrap cannot
  explore FX or inflation regimes outside 2015–2026 (B1-recent, B2) or
  2005–2016 (B1-early).
- **Replication scope.** The bit-identity of the `tax_asym_v2` replication covers
  all three gated variants' C0 arrays and A2 medians. It was first written for
  B1-recent and extended to B1-early and B2-flat after the pre-registration
  commit (a reporting change in `verify_c0_unlevered` only: no arm, parameter,
  gate or selection rule was touched, and the arm results here were produced by
  the same code as the pre-registered design).
- **This study tests the after-tax bootstrap only.** The pre-tax G1–G9 / OOS
  battery is not re-run; `tax_asym_v1` carries it (A2 passes G1, G3–G4, G6–G9
  and fails G2 and G5), and the pre-tax Δ here is the same-paths bootstrap
  control, not G5.
- **One cost level.** 4 bps × 2 per unit traded, as in the source studies; no 2×
  cost stress is reported because no arm is near the return bar.
- **Single bootstrap seed.** 1,000 paths, seed 20261011, inherited from
  `tax_asym_v2` and `signal_ensemble_v1` so the numbers are comparable to the
  study being re-run.

## What a production change would have required

None is proposed; A2 fails the gate in every variant at every k. `tax_frontier_v1`
banked A2 as a PASS on the realized path only; with `tax_asym_v1`, `tax_asym_v2`
and now the corrected `tax_asym_v3` all failing the bootstrap, the after-tax case
for downward-only vol resizes is closed. Production E2 keeps its rule and its
0.35 vol target.

## Decision

Production E2 (`equity_leveraged_trend_v1` 1.1.0) is unchanged. The campaign is
closed; no code, configuration or runtime changed.
