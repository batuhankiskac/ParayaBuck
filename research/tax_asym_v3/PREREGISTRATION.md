# tax_asym_v3 pre-registration — corrected-C0 rerun of tax_asym_v2's after-tax bootstrap

Date: 2026-10-14. Branch `research/tax_asym_v3`. Written and committed **before** any
arm was simulated on real data (the only real-data runs before the commit are the
control-only `--harness` checks and the synthetic-only `--smoke` mode).

## Question

`tax_asym_v2` reported FAIL for candidate A2 (production E2 with downward-only vol
resizes) on a clean after-tax block bootstrap, with median Δ after-tax CAGR of
+1.045 pp / +0.391 pp / +1.856 pp (share of paths > 0 = 57.5% / 53.3% / 61.5%) in
B1-recent / B1-early / B2. `vt_level_tax_v1` (Amendment 3) says that bootstrap ran
**C0 on unlevered QQQ** while every other arm ran on the 2× synthetic, which would
flatter any leveraged candidate by construction. This study:

1. **verifies** the claim (source inspection + a numeric C0 check), and
2. **re-runs tax_asym_v2's exact bootstrap design with the corrected engine** — C0 on
   the same daily-reset 2× synthetic as the candidates — for A2 and its plateau
   neighbours, against a correct C0.

## Verification (item 1, reported in `RESULTS.json` → `verification` and in the report)

`research/tax_asym_v2/screen.py:1486-1488` assigned the arm price arrays inside
`after_tax_bootstrap_variant`:

```
PRICES["C0"] = (qo.flatten(), qc.flatten())   # qo/qc = UNLEVERED QQQ total return
PRICES["A1"] = (so.flatten(), sc.flatten())   # so/sc = daily-reset 2x synthetic
PRICES["A2"] = (so.flatten(), sc.flatten())
```

while C0's *wants* were computed from the 2× instrument's vol
(`wants_c0 = e2 * min(CAP, vt_weight(vol_2x, 0.35))`, line 1472-1476). The
corrected engine (`research/vt_level_tax_v1/screen.py:1414`) assigns
`PRICES[arm] = (so, sc)` to every arm.

Numeric verification, in `--verify`:

1. Re-run B1-recent (1,000 paths, seed 20261011, $100k) with `c0_on_1x=True`, i.e.
   with exactly the buggy C0 pricing, and require the per-path C0 CAGR and max-DD
   arrays to equal `research/tax_asym_v2/RESULTS.json`'s `results.B1_recent.C0`
   arrays element for element (replication of the published run);
2. re-run the same 1,000 paths with `c0_on_1x=False` (corrected) and report C0's
   median after-tax CAGR and median max DD under both pricings, plus A2's Δ.

The claim is treated as verified if the replica arrays are bit-identical (or, if
floating-point transcription differs, equal to < 1e-15 absolute) **and** C0's median
max DD moves from ≈35% (unlevered) to ≈50%+ (2×).

## Arms (all parameters fixed here, before any run)

| Arm | Rule | Parameter |
|---|---|---|
| **C0** | production E2 1.1.0 | vol20 target 0.35, cap 0.98, 0.5 pp band |
| **A2** | E2 with downward-only vol resizes, primary candidate | k = 0.50, trim = 0 |
| **A2_40** | same, plateau neighbour | k = 0.40, trim = 0 |
| **A2_60** | same, plateau neighbour | k = 0.60, trim = 0 |

"Downward-only": upward resizes as in production when target > held + band; a
downward resize happens only on a trend exit (target ≤ 0 after a trend-on session) or
when target < k × held weight. `trim = 0` means the downward resize goes all the way
to the target. The signal, instrument, timing, cost and the tax engines are identical
across arms; only the resize decision differs. A1 (no vol target) from `tax_asym_v2`
is dropped: it is not the candidate and it fails everywhere.

## Data (existing committed research files only; no network)

See `DATA_MANIFEST.json` for source, date range and SHA-256 of every input. Nothing is
downloaded by this study; the USD/TRY and Yi-UFE series are the copies
`tax_asym_v2` used, and the deviation reference is the byte-identical
`archive/research/e2-integration-v1-2026-10` file, committed here as
`REFERENCE_E2_DEVIATION.json`.

## Mechanics (identical to `tax_asym_v2`)

- QQQ open/close/dividend from `stress_robustness_v1_daily.csv`; DTB3 from FRED.
- Synthetic QLD\*: daily-reset 2× on QQQ total return, expense 0.95%/yr, financing
  DTB3 + 0.00 (base). The pre-tax control row is C0 on this instrument.
- E2 signal (SMA200, 252-session price return > 0, Donchian 126 midpoint; ≥ 2 of 3)
  and vol20 are computed on the **resampled QQQ price level**.
- Decide at the close, trade at the next open; 0.5 pp resize band; cost 4 bp × m = 0.08%
  per unit traded (m = 2).
- Bootstrap: stationary block-20, seed 20261011, 1,000 paths, 10 measured years
  (2,520 sessions), 3-session back-extension, WARM = 253. Market returns (QQQ + DTB3)
  are resampled jointly by session; the **calendar** (hence USD/TRY and Yi-UFE) stays
  real and fixed. Draws are identical on every path for every arm.
- $100k initial capital; liquidation at the window end. Hard assertion: every path's
  terminal liquidated USD value must be > 0 (the run fails loudly otherwise).

### Variants (all four arms on all four variants)

| Variant | Tax | Calendar |
|---|---|---|
| B1_recent | Turkish progressive, inflation-indexed (2026 tariff, Yi-UFE anchor) | 2015-09-01 → 2026-09-30 |
| B1_early | same | 2005-09-01 → 2016-09-30 |
| B2_flat | 20% flat on annual net realized USD gain, loss carry-forward | 2015-09-01 → 2026-09-30 |
| pretax (context) | none | 2015-09-01 → 2026-09-30 |

The `pretax` variant uses the same paths and the same mechanics with a no-tax regime;
it is reported for context and is **not** gated.

## Gate (pre-registered, `tax_asym_v2`'s unchanged)

An arm is **CONFIRMED** iff, in **B1_recent AND B1_early AND B2_flat**, both:

- median after-tax Δ CAGR (arm − C0) ≥ **+0.50 pp**, and
- share of paths with Δ > 0 ≥ **65%**.

Also reported, not gated: the full Δ max-DD distribution (median / p05 / p95), the
Δ CAGR p05/p95, and the pre-tax Δ on the same paths.

## Selection rule (pre-registered)

- Primary candidate: **A2** (k = 0.50), the arm `tax_asym_v2` tested.
- Verdict is **PASS** iff A2 passes the gate in all three variants; otherwise **FAIL**.
- If A2 passes and at least one plateau neighbour (A2_40, A2_60) also passes, the
  plateau is called *robust*; if A2 passes with neither neighbour passing, *weak*.
- No parameter is tuned after seeing results. Neighbours exist to test that a pass is
  not a knife-edge in k, not to select the best k.

## Reduction / limitations declared up front

- The pre-tax G1–G9 / OOS gate battery is **not** run here: this study re-tests the
  after-tax bootstrap only, and `tax_asym_v2`/`tax_frontier_v1` already carry the
  pre-tax evidence for A2 (which passed G1–G3 pre-tax there). The pre-tax Δ reported
  here is the same-paths bootstrap control, not G5.
- The bootstrap keeps the calendar real, so it cannot explore FX or inflation regimes
  outside 2015–2026 (B1-recent/B2) or 2005–2016 (B1-early). Same caveat as the source
  studies.
- All gates and arms above were fixed before the first real-data arm run; every
  parameter is stated here, including the ones whose neighbours are reported.

## Smoke test (before the commit)

`python research/tax_asym_v3/screen.py --smoke` runs the whole run path (signals, both
tax engines, the third pre-tax regime, the gate and the selection) on a deterministic
synthetic random walk and writes no output file. It must pass before the pre-registration
commit; it does not touch real market data.
