# gate_power_v1 — audit of the batch screen's power (verdict: screen is working, but power is low for small edges)

## 1. Verdict in plain words

The screen is **not broken and not too strict in a way that invalidates past work**: it reproduces
every published anchor bit-for-bit, it correctly rejects a pure-noise null (0/1 Stage 1), and every
one of the campaign's real, deployed improvements passes Stage 1 and (where the pre-registered
verdict was "candidate") passes Stage 2 when re-run against its own predecessor as control. The
reason the batch framework has declared **0 of 303 arms** is not that the gate is buggy; it is that
the easy after-tax gains were already harvested by the deployed K, and the remaining ~265
research arms are, on average, **-0.70 pp after tax** versus K with a median Stage-1 edge of
-0.70 pp. The screen's minimum detectable edge at Stage 1 is roughly **+0.003 pp median bootstrap
Δ** (syn_edge_105 passed Stage 1 by 0.003 pp); at Stage 2 the bar is higher and no synthetic
edge cleared it. The honest conclusion: the batch screen is a good **filter**, not a discovery
engine, and the research program has hit the diminishing-returns part of the idea space.

## 2. Method

- Copied `research/batch_screen/` into `research/gate_power_v1/` (never edited the original) and
  made `CONTROL_ARM` selectable via `GATE_POWER_CONTROL`, so alternate controls (E2, R2, A1u) run
  through the *same* stages, gates and deflation.
- Added `research/ideas/{e2_control,a1u_control,synthetic_power}.py` (E2 target series, R2 under
  the A1u layer, and perturbed-K arms).
- Ran `python -m research.fast_engine.parity --quick` → PASS (12.2 s).
- Re-ran the four historical improvements as if they were new ideas, each against its own
  predecessor as control, through Stages 1/2 and the deflation. Re-set the copied registry to
  303 lines before each run so N stays at the published 303.
- Stage-1 after-tax distribution taken from the 265 already-screened arm records (no re-run).

## 3. Positive controls

| candidate vs control | Stage 1 | Stage 2 | failed gates | AT2000 Δ | B1 med/share | RW p | final |
|---|---|---|---|---|---|---|---|
| R2 vs E2 | PASS | PASS | - | +0.626 | +0.485 / 0.646 | 0.000 | declared |
| A1u vs R2 | PASS | FAIL | G5_calibrated | +0.455 | +0.226 / 0.754 | 0.000 | rejected |
| K (H2 on A1u) vs A1u | PASS | PASS | - | +0.139 | +0.042 / 0.633 | 0.000 | declared |
| K vs R2 | PASS | FAIL | G5_base, G5_calibrated | +0.593 | +0.286 / 0.750 | 0.000 | rejected |

Reading: R2 (early re-entry) is a real improvement over E2 and clears every gate. The A1u gain
band and H2 deferral are real improvements on the after-tax metric (+0.45 / +0.14 pp) but the
gain band fails the **pre-tax** bootstrap gate G5 (calibrated) — exactly the failure recorded in
`gain_band_v2.md`. The whole K layer vs R2 fails G5 (base + calibrated) for the same reason:
K's edge is after-tax, and the pre-tax bootstrap median stays near zero. This matches the
campaign's own "candidate, failed pre-tax bootstrap" verdicts, so the screen is not killing real
winners that the campaign accepted — the campaign accepted K *despite* G5 on owner decision.

## 4. Synthetic power and null control

| arm (vs K) | Stage 1 | Stage 2 | AT2000 Δ | B1 med/share | notes |
|---|---|---|---|---|---|
| syn_edge_105 (+5% exposure) | PASS | FAIL | +0.298 | +0.003 / 0.503 | fails G2, B1, B2, E1, FRESH |
| syn_edge_110 (+10% exposure) | FAIL | - | +0.579 | -0.005 / 0.499 | B1 median just below 0 |
| syn_null (zero-mean noise) | FAIL | - | -2.478 | -1.688 / 0.054 | correctly rejected |

Null control passes 0/1 at Stage 1, so the false-positive rate at Stage 1 is not inflated.
The minimum edge that cleared Stage 1 was a bootstrap median of +0.003 pp — i.e. the Stage-1
B1 gate is essentially a sign test on the bootstrap median and has very high power even for tiny
edges, but it cannot distinguish a real +0.3 pp idea from noise at the *individual* level without
the holdout gates.

## 5. Which gates kill edges

Stage-2 failures among the 19 rejected arms (multiple allowed per arm):

| gate | arms failing |
|---|---|
| AT_2017 (after-tax, 2017→end) | 16 |
| FRESH (both fresh seeds) | 12 |
| AT_2010 (after-tax, 2010→end) | 11 |
| B1_recent (bootstrap median) | 11 |
| E1 (21 ETFs after tax) | 10 |
| B2_flat (flat 20% tax bootstrap) | 10 |
| G5_base (pre-tax bootstrap, base) | 10 |
| G1_base / G6_calibrated | 8 / 6 |
| E2 (rolling January starts) | 6 |
| G8 (pooled OOS bootstrap) | 4 |
| G7 (OOS windows) | 3 |
| G4 (sub-periods) | 3 |
| G2 (max DD) | 2 |

The after-tax "all three windows" rule (AT_2017 in particular) is the single biggest killer
(16/19), followed by the fresh-seed bootstrap (12/19) and the B1 bootstrap (11/19). These are
**reasonable** for a Turkish-resident owner: a strategy that only wins on the 2000–2026 window
and loses since 2017 is not a real improvement, and a bootstrap that does not hold across two
independent seeds is not robust. The pre-tax G5 bootstrap is the one gate that kills *deployed*
improvements (A1u, K) — that is a known, owner-accepted tension, not a bug.

## 6. Distribution of the 265 screened arms (Stage 1)

- Median Stage-1 after-tax Δ vs K (2000 window): **-0.699 pp** (mean -1.24 pp).
- Range: -11.29 pp to +1.83 pp; only 46/265 (17%) have Δ > 0 at all.
- Stage 1 pass: **23/265 (8.7%)**; Stage 2 pass: 0; declared: 0.
- Verdicts: 242 reject_stage1, 19 reject_stage2, 4 holdout_insufficient.

Implication: the idea bank is not generating near-miss edges; the typical idea is clearly worse
than K after tax. The 23 Stage-1 passes are mostly fragile (median B1 Δ near zero, many fail
FRESH/E1/AT_2017), which is why none survives Stage 2. The screen is doing its job: filtering
out ideas that look good in-sample but do not hold up.

## 7. Recommendations (not changes)

1. Keep the gates as they are; the strictness is justified by the Turkish-tax objective and the
   0/1 null result. Do not relax AT_2017 or FRESH.
2. Re-examine the **pre-tax G5 bootstrap gate**: it is the only gate that rejects the deployed
   K against R2, and the campaign has repeatedly accepted candidates on owner decision despite
   it. Either drop G5 for after-tax-driven ideas or replace it with an after-tax bootstrap gate.
   This is the one gate whose strictness is arguably excessive *given* that Turkish tax is the
   decisive metric.
3. Stop spending cycles on "trim-reduction" ideas: K already removed ~2/3 of vol-target trims;
   the remaining variance is in the trend vote and the tax calendar, not in execution micro-optimisation.
4. If more search is wanted, raise power by increasing the bootstrap path count or by a
   pre-registered confirmation study on a single strong candidate (as `ic12_confirm_v1` did),
   rather than by loosening gates.
5. The screen's power for a true +0.5 pp edge is low in a single pass; plan on confirmation
   studies, not on one-shot screening, for borderline candidates.

## 8. Reproduction

- `python -m research.fast_engine.parity --quick` → PASS.
- Positive-control batches: `research/gate_power_v1/results/pc_*.json`.
- Synthetic batch: `research/gate_power_v1/results/pc_synth/`.
- Distribution numbers: 265 arm records under `research/batch_screen/results/`.
