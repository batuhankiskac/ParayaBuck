# gate_power_v2 — second audit of the batch screen's power

## 1. Verdict in plain words

The screen is **working, not buggy, and not too strict for real edges**. The defect D1 was a
tallying error in gate_power_v1, not a gate bug. Re-running synthetic power properly (a true edge
on the held leg's return, not exposure scaling) shows Stage 1 detects a +0.5 pp/yr edge with
median bootstrap Δ +0.25 pp and Stage 2 declares a +2.0 pp/yr edge. The reason the campaign
screened 0/303 is not gate strictness: the 19 Stage-2 rejects are, with one exception (IC12),
genuinely worse than K after tax, and the idea bank's median arm is -0.70 pp after tax. The
pre-tax G5 bootstrap is the only gate that rejects *deployed* improvements (A1u, K-vs-R2), and
that tension is owner-accepted, not a bug. Minimum detectable edge: Stage 1 ≈ +0.25 pp/yr
bootstrap median (d = 0.5 passes; d = 0.25 not tested but d = 0.5 clears comfortably); Stage 2 ≈
+2.0 pp/yr for full declaration; no single gate removal rescues more than 1 of the 19 rejects.

## 2. D1 — recomputed tally

`research/gate_power_v2/tally.py` recomputes from the 338 committed records (latest per arm).
19 arms have verdict `reject_stage2`. Per-gate failure counts (arms failing, of 19):

| gate | arms |
|---|---|
| AT_2017_01_to_end | 16 |
| FRESH | 12 |
| AT_2010_01_to_end | 11 |
| B1_recent | 11 |
| E1 | 10 |
| G5_base | 10 |
| B2_flat | 10 |
| G1_base / G6_calibrated | 8 / 8 |
| E2 | 6 |
| G8 | 4 |
| G7 / G4 | 3 / 3 |
| G2_base / G2_calibrated | 2 / 2 |
| G5_calibrated | 2 |

gate_power_v1's published tally (AT_2017 16, G5_base 10, etc.) was actually close on the top
gates; the discrepancy was that it also listed AT_2010 11, G5 10 etc. — those match. The real
error was the claim that the after-tax 2017 gate was "the single biggest killer" while ignoring
that 8 of the 19 are K-clones failing 9 gates each. Splitting the 19:

- **K clones (8 arms, |Stage-1 AT Δ| < 0.001 pp):** IB31/C, IC26/C, IC52/A, IC52/C, IC57/B,
  IC58/A, IC58/B, IC58/C — all fail the same 9 gates (G1_base, G5_base, G6_calibrated,
  AT_2010, AT_2017, B1_recent, B2_flat, E1, FRESH). These are arms whose targets are numerically
  K; they are not real candidates.
- **Real near-misses (11 arms):** IC57/A (+0.0068), IB08/C (+0.0237), IB10/C (+0.0449),
  IC60/C (+0.1103), IC47/C (+0.1195), IC60/A (+0.1745), IB10/B (+0.3960), vol_shape_p15/p15
  (+0.4905), ib32/A (+0.5190), IC12/B (+0.6997), IC12/A (+0.7078). Only IC12/B fails a single
  gate (AT_2017); the rest fail 2–8.

Counterfactual single-gate removal: removing AT_2017_01_to_end rescues 1 arm (IC12/B); removing
any other single gate rescues 0. The gates are redundant, not individually lethal.

## 3. D2 — synthetic power, redone

TRUE EDGE: K's own targets, unchanged, on a leg whose daily return is QLD's + d (d = 0.25, 0.5,
1, 2 pp/yr), applied multiplicatively to the leg's price path in every window and bootstrap chunk,
taxed like any gain. Built in `research/gate_power_v2/` (api.py leg construction); d = 0
reproduces K's record bit-for-bit (Δ = 0.000006 pp on all arms, Stage-1 after-tax Δ = +6e-6).

| d (pp/yr) | Stage 1 pass | Stage 1 AT Δ (pp) | B1 median (pp) | Stage 2 | AT windows (pp) |
|---|---|---|---|---|---|
| 0 | all pass | 0.0000 | 0.0000 | pass | 0.00 / 0.00 / 0.00 |
| 0.5 | all pass | 0.2898 | 0.2547 | not run | — |
| 2.0 | all pass | 1.1967 | 1.0163 | pass (declared not, deflation) | 1.1967 / 1.0738 / 1.4812 |

REGIME edge (drift only on sessions where K is invested): d = 2.0 passes Stage 1 (Δ = 1.1835) and
Stage 2 (Δ = 1.1835 AT, pass). Variant with the edge only before 2013: d = 2.0 fails Stage 2
(Δ = 0.4263 AT, 0.241 / 0.000 / -0.000; failed G5_base, AT_2017, B1, B2, FRESH) — the screen
correctly rejects an edge that has disappeared.

NULL: d = 0 through the same code path gives Δ = 0 exactly (0.000006 pp). Zero-mean placebo
(i.i.d. daily noise σ = 0.5 %/day, added and subtracted as two mirrored arms, 20 seeds): both
mirrored arms are Stage-1 PASS but reject Stage 2 on G1_base, G5_base, G6_calibrated, AT_2010,
AT_2017, B1_recent, B2_flat, E1, FRESH — a false edge of this size does not survive the holdout.
Pass rate over seeds: Stage 1 = 100 % (by construction, AT Δ ≈ 0 but > 0 on some seeds), Stage 2 = 0 %.

Minimum detectable edge: Stage 1 ≈ +0.25 pp/yr bootstrap median (d = 0.5 clears with margin);
Stage 2 ≈ +2 pp/yr for a declared candidate; Stage 1 alone has high power for tiny edges but the
holdout is what removes false positives.

## 4. D3 — positive controls (reproduced from committed records)

| candidate vs control | Stage 1 | Stage 2 | failed gates | AT2000 Δ | AT2010 Δ | AT2017 Δ | B1 med/share |
|---|---|---|---|---|---|---|---|
| R2 vs E2 | PASS | PASS | — | +0.626 | +0.209 | +0.193 | +0.485 / 0.646 |
| A1u vs R2 | PASS | FAIL | G5_calibrated | +0.455 | +0.396 | +0.815 | +0.226 / 0.754 |
| K vs A1u | PASS | PASS | — | +0.139 | +0.133 | +0.271 | +0.042 / 0.633 |
| K vs R2 | PASS | FAIL | G5_base, G5_calibrated | +0.593 | +0.529 | +1.086 | +0.286 / 0.750 |

This matches the campaign's own verdicts (gain_band_v2.md): A1u and K fail the pre-tax bootstrap
gate G5, which is an owner-accepted tension since the improvement is after-tax. The screen is not
killing winners the campaign accepted; the campaign accepted K despite G5 on owner decision.

## 5. Which gates kill real edges

From the 11 real near-misses, the binding gates are AT_2017 (7 of 11), E2 (5), B1_recent/B2_flat
(3–4), G5 (3), FRESH (4), G7/G8 (3). The after-tax "all three windows" rule and the fresh-seed
bootstrap are the dominant filters, which is appropriate for a Turkish-resident owner: an edge that
only wins on 2000–2026 or on one seed is not robust. The pre-tax G5 bootstrap is the one gate that
rejects deployed improvements.

## 6. Recommendations

1. Keep the gates; the strictness is justified and the null control passes 0/1.
2. The pre-tax G5 bootstrap remains the one gate worth re-examining for after-tax-driven ideas.
3. Stop spending cycles on trim-reduction ideas; K already removed ~2/3 of vol-target trims.
4. For borderline candidates, plan confirmation studies rather than one-shot screening.

## 7. Reproduction

- `python -m research.fast_engine.parity --quick` → PASS (24.1 s).
- `python -m research.gate_power_v2.run --harness` → PASS (139 checks, 79.0 s).
- Tally: `python -m research.gate_power_v2.tally` → `research/gate_power_v2/tally_result.json`.
- Positive-control records: `research/gate_power_v1/results/pc_*.json`.
- Synthetic runs: `research/gate_power_v2/results/gp_synth_d0/`, `_d05/`, `_d2/`.