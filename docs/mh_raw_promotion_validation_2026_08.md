# MH_RAW Promotion Validation — 2026-08

Mission: validate the researched MH_RAW candidate for promotion under a
corrected reproducibility contract. No parameters were searched, no horizons
added, no weights altered, no risk settings changed, no crypto touched,
production not promoted.

| item | value |
|---|---|
| mission | equity_mh_raw_promotion_2026_08 |
| implementation commit | `bda700270b4853bcffd47218dfebcef8fc3677b0` (COMMIT C) |
| results/report commit | COMMIT D (see Reproducibility; HEAD reported at mission close) |
| python | 3.14.7 |
| config digest | `5129057f7113293edf276f61202f477df63260288ad107eebf9b022a9b2180bb` |
| bars SHA256 | `3baec12e21a1230732421df5aae9142873e82727b6e526f325948bc8385e903a` |
| dividends SHA256 | `7d79025d327a9f43b0170b252499ce00343d1dcf2f48cfde3e05e5ebd073e1e0` |
| eval window | 2017-01-03 → 2026-07-30 (daily; 2658 SPY days) |
| walk-forward | exactly 20 anchored folds, true calendar `relativedelta(months=6)` boundaries (never `timedelta(days=180)`), warmup = all prior bars; declared in the manifest and asserted at runtime |
| recent window | 2022-01-01 → 2026-07-30 |
| cost model | slippage 2bp + spread 2bp + effective-dated TAF/CAT/SEC; stress 1x/2x/3x/5x |
| bootstrap | 20-day block, 2000 resamples, seed 202608230 |
| predeclaration | `data/research_manifests/equity_mh_raw_promotion_2026_08.json` (COMMIT A, before results) |
| survivorship | `survivorship_bias_limitation: true` — every result is a RELATIVE candidate-vs-CONTROL comparison under the same biased universe; absolute levels are optimistic; 2022+ carries more decision weight |

## 1. What EXACTLY is MH_RAW?

```text
standard production eligibility (beta/residual pipeline, PIT universe)
  -> raw 12-1 residual score must be > 0                  (positive_12m_gate = true)
  -> collect residual series
  -> multi-horizon rank consensus:
       6-1  = 126 residual bars (skip 21)
       9-1  = 189 residual bars (skip 21)
       12-1 = 252 residual bars (skip 21)
       each horizon's RAW compounded residual return percentile-ranked
       cross-sectionally (ties averaged), three ranks averaged EQUALLY
  -> existing ivol q=0.70 rejection
  -> top_n=8
  -> inverse-vol sizing (per-asset cap 0.10)
  -> existing production risk overlay (unchanged: max 6 positions, 10% per
     position, 60% gross, 40% cash buffer, daily-loss, dd-halt shadow)
```

`positive_12m_gate = true` (frozen): the gate is inherited from the
production base pipeline, so it runs BEFORE the consensus. MH_RAW is
therefore **positive 12-1 residual momentum + multi-horizon rank consensus**,
NOT an unrestricted 6/9/12 cross-section. The gate order is locked by a
dedicated regression test (a symbol with strongly positive 6-1 momentum but
negative 12-1 momentum is excluded by both the research variant and v3).

## 2. Is the positive 12-1 score a prerequisite?

**Yes.** Verified in the current researched implementation
(`AlphaResearchMultiHorizon._transform_cross_sectional_scores` runs only
after `_score_candidates`' `score <= 0` exclusion) and in the promotion
identity `equity_residual_momentum_v3` (identical inherited pipeline). The
regression test `test_negative_12m_candidate_excluded_in_both` + the
gate-order test prove the ordering; removing or moving the gate breaks both.

## 3. How many folds are there exactly?

**Exactly 20.** Boundaries predeclared in the manifest (`folds_exact`) and
computed by `parayabuck.validation.calendar_fold_windows` with
`relativedelta(months=6)`; the runner aborts on any drift or on
`actual_fold_count != declared_fold_count (20)`. The phase-2 report's
"19 folds" section header was a transcription error — its own artifacts
contained 20 folds; this validation makes the count explicit and asserted.

## 4. Exact median paired return delta

**+2.39pp** per fold (`+0.023882`); mean +0.91pp; p25 −1.96pp; stdev 4.38pp;
worst delta −12.18pp (2024-H1), best +5.86pp. Single canonical source:
`fold_deltas.json` computed by `paired_fold_deltas` (no manual transcription).

## 5. Exact return / Sharpe fold wins

**13/20 return wins, 13/20 Sharpe wins, 10/20 maxDD wins** (both thresholds
≥ 10/20 pass). The 2024 folds are MH_RAW's only material losses
(−12.2pp / −5.2pp deltas); 2021-H2 and 2017-H1 are the other losses.

## 6. Candidate-specific jackknife (own top-1..top-5)

MH_RAW's OWN top-5 positive-PnL contributors (full-period realized PnL):
AMAT, NVDA, ASX, AEM, AVGO (top-1 share 6.5% of positive PnL).

| drop | return | Sharpe | maxDD |
|---|---|---|---|
| none (base) | +172.0% | 0.796 | 22.0% |
| drop own AMAT | +151.2% | 0.743 | 21.1% |
| drop own NVDA | +172.7% | 0.802 | 20.2% |
| drop own ASX | +154.0% | 0.750 | 22.0% |
| drop own AEM | +155.9% | 0.752 | 23.1% |
| drop own AVGO | +166.5% | 0.782 | 20.8% |

Candidate-minus-control deltas (vs CONTROL full +125.3% / 0.658 / 27.4%):

| drop | Δ return | Δ Sharpe | Δ maxDD |
|---|---|---|---|
| drop own AMAT | +25.9pp | +0.085 | −6.4pp |
| drop own NVDA | +47.4pp | +0.144 | −7.2pp |
| drop own ASX | +28.7pp | +0.091 | −5.4pp |
| drop own AEM | +30.6pp | +0.094 | −4.4pp |
| drop own AVGO | +41.2pp | +0.124 | −6.6pp |

Every single-symbol drop keeps a ≥ +26pp return advantage over CONTROL.

## 7. Own top-3 removed simultaneously

`drop_own_top3_combined` (AMAT+NVDA+ASX): **+153.2% / Sharpe 0.761 /
maxDD 16.6%** — still +28.0pp return and +0.103 Sharpe above CONTROL's full
period, with a BETTER maxDD than the base (−5.4pp). The concentration gate
passes: removing MH_RAW's own strongest contributors does not destroy its
relative advantage. (Secondary, labelled cross perturbation: dropping
CONTROL's top-3 from MH_RAW → +140.7% / 0.721 — also above CONTROL base.)

## 8. Does 2022+ still beat CONTROL?

**Yes.** 2022+ (2022-01-01 → 2026-07-30): MH_RAW **+44.0% / Sharpe 0.665 /
maxDD 14.7%** vs CONTROL **+34.7% / 0.560 / 16.8%** — better on all three
(+9.3pp return, +0.105 Sharpe, −2.1pp maxDD).

## 9. Does excluding 2020 change the conclusion?

**No.** Full period excluding 2020 (chained 2017-2019 + 2021-2026):
MH_RAW **+89.5%** vs CONTROL **+70.0%** — MH_RAW still +19.5pp. The
advantage is not a 2020 artifact.

## 10. Does excluding 2024 change the conclusion?

**No — it strengthens it.** Full period excluding 2024 (chained 2017-2023 +
2025-2026): MH_RAW **+143.0%** vs CONTROL **+73.3%** (+69.7pp). 2024 was
MH_RAW's only weak year (+11.6% vs +30.0% CONTROL — the two 2024 folds are
its worst deltas), but the edge is not dependent on any single year:
**8/10 full calendar years beat CONTROL** — wins 2018, 2019, 2020, 2021
(MH −5.8% > CONTROL −7.1%), 2022, 2023, 2025, 2026; losses only **2017**
(+12.3% vs +15.4%) and **2024** (+11.6% vs +30.0%).

## 11. Do 3x/5x cost stress preserve the edge?

**Yes.** Full-period return / Sharpe:

| | 1x | 2x | 3x | 5x |
|---|---|---|---|---|
| CONTROL | +125.3% / 0.658 | +116.8% / 0.630 | +108.4% / 0.601 | +92.8% / 0.544 |
| MH_RAW | +172.0% / 0.796 | +161.4% / 0.766 | +150.8% / 0.736 | +131.3% / 0.677 |

3x advantage: +42.4pp return, +0.135 Sharpe; 5x: +38.5pp, +0.133. Fees/gross
at 3x: 11.0% (MH_RAW) vs 13.6% (CONTROL) — the consensus does not trade
more expensively.

## 12. Is production V3 mathematically identical to research MH_RAW?

**Yes — proven by parity tests, not asserted.** `equity_residual_momentum_v3`
contains the promoted consensus math natively (no research-module import);
`tests/test_equity_residual_momentum_v3.py` locks, under identical
`PortfolioContext`:

- ranking equality (full rank order) at: ordinary snapshot, tie-heavy
  snapshot, missing-symbol snapshot, negative-12m snapshot,
  conflicting-6m-vs-12m snapshot, split-adjusted history;
- selected-names equality and target-weight equality (bit-identical
  weights, same ranks);
- consensus math parity vs `horizon_consensus_scores(standardized=False)`
  including tie handling and scale invariance.

Also: `positive_12m_gate` ordering regression (research variant and v3 both
exclude a positive-6m/negative-12m name). v3 is registered as a
CONSTRUCTIBLE identity only — `config/settings.yaml` still selects v2 and
`PRODUCT_STRATEGY_NAMES` excludes v3, so nothing in production can select it.

## 13. Promotion rubric (mission §9, applied exactly)

| gate | result |
|---|---|
| Sharpe improvement ≥ +0.08 | PASS (+0.137) |
| maxDD improvement ≥ 3pp | PASS (−5.40pp) |
| 2022+ not worse than CONTROL | PASS (+9.3pp, +0.105, −2.1pp) |
| median paired fold delta > 0 | PASS (+2.39pp) |
| ≥ 10/20 return wins | PASS (13) |
| ≥ 10/20 Sharpe wins | PASS (13) |
| survives 3x costs | PASS (+42.4pp, +0.135) |
| own-top-3 jackknife preserves advantage | PASS (+28.0pp, +0.103) |
| no single-year dependency | PASS (8/10 years; exclude-2020 and exclude-2024 both ≥ CONTROL) |
| no implementation mismatch | PASS (parity suite) |
| no lookahead | PASS (skip-window construction; PIT universe; window tests) |
| survivorship limitation disclosed | PASS (this report) |

Bootstrap corroboration: block bootstrap P(Return > CONTROL) = 0.874,
P(Sharpe > CONTROL) = 0.873, median Δreturn +1.96pp (p5..p95 −0.8..+4.8pp).

Material changes vs the phase-2 research: none to the numbers (headlines
reproduced exactly: +172.0% / 0.796 / 22.0% vs +125.3% / 0.658 / 27.4%).
The corrections in this mission are procedural: fold boundaries are now true
calendar months (median fold delta moved from +0.75pp under the drifted
180-day grid to +2.39pp under the declared calendar grid — a grid
correction, not a parameter change), the 19/20-fold reporting inconsistency
is resolved (20), provenance is fixed (implementation_commit ==
research_commit == COMMIT A), and the jackknife is candidate-specific.
No rubric threshold was changed.

## Verdict

```text
VALIDATED STRONG PROMOTION CANDIDATE: MH_RAW
```

Production strategy selection is NOT changed by this validation:
`config/settings.yaml` still runs `equity_residual_momentum_v2`; v3 remains
paper-only (offline shadow replay log:
`data/research/mh_raw_shadow_2026_08/shadow_log.json` — 495 scheduled
decisions, never submits orders; mean target overlap 5.65/8, Jaccard 0.56,
selection divergence 97% of decisions, mean |Δweight| 0.47/decision).

## Reproducibility

- COMMIT A (`2c42341…`): frozen implementation + validation manifest +
  tests; contains every line of code required to reproduce. No generated
  results in COMMIT A.
- COMMIT B (`32126af…`): report + aggregates + hashes. Superseded on the
  temporal-window provenance issue below; its alpha/fold/cost/jackknife
  numbers remain valid (same implementation as A).
- COMMIT C (`bda7002…`): provenance patch — the chained cumulative
  temporal math (`chain_equity_curves` / `chained_cumulative_metrics`) is
  now part of the frozen validation contract with deterministic tests
  (two-segment chain reports `(1+r1)*(1+r2)-1`, cross-boundary maxDD and
  joined-curve Sharpe); runner gains `--temporal-only`; manifest
  `results_sha256` EMPTY. No results in COMMIT C.
- COMMIT D (this report + refreshed artifacts): temporal-exclusion windows
  (exclude-2020 / exclude-2024) re-run strictly from COMMIT C; every
  artifact records `implementation_commit == research_commit == COMMIT C`;
  `results_sha256` in the manifest pins every generated artifact.
- Full gates at COMMIT C: pytest 680 passed; ruff check clean; ruff format
  clean; mypy clean (70 source files).
