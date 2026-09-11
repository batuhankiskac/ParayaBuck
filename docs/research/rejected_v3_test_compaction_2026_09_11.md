# Rejected V3 test compaction — Phase 2C

Date: 2026-09-11

Source HEAD before Phase 2C: `71cfe2070edbb1c268b5e1ee673da28046ff3fc8`
(`Remove obsolete implementation files`)

Commit created: **none**. The working tree contains the requested deletions and
this record; the source HEAD remains the recovery point.

This phase re-audits exactly three rejected V3 experiment-integrity modules. M3
lineage, current product behavior, research runners, manifests, receipts,
datasets, frozen results, and historical reports are outside the deletion set.
The earlier Phase 2B record is preserved unchanged as a historical record of
its earlier KEEP assessment; this record supersedes that assessment only for
the three Phase 2C targets.

## Decision per target

| Exact path | Collected items | Decision | Reason | Current replacement or retained evidence |
|---|---:|---|---|---|
| `tests/test_v3_exit_latency_integrity.py` | 9 | **DELETE** | All assertions are rejected-experiment manifest or source-text methodology checks: independent reruns, no curve slicing/heuristics, daily-exit diagnostics, and the 12-gate rubric. No executable pin, import, verifier dependency, or unique current invariant remains. | Current weekly V3 identity/cadence coverage in `tests/test_equity_residual_momentum_v3.py` and `tests/test_strategy_spec_freeze.py`; rejected E0-E3 evidence and runner remain archived/retained. |
| `tests/test_v3_position_risk_integrity.py` | 19 | **DELETE** | All assertions are rejected P0-P3 metadata, candidate geometry, no-shortcut/source-text, tail-ES/AXON diagnostics, and 14-gate research-contract checks. No executable pin, import, verifier dependency, or unique current invariant remains. | Current V3 identity/ranking and production selection tests; rejected P0-P3 evidence and runner remain archived/retained. |
| `tests/test_v3_position_risk_cost_stress_integrity.py` | 5 | **DELETE** | The fee comparisons and multiplier checks exercise `PositionRiskResearchEngine`/`make_engine` for the rejected experiment. They do not protect a current production path. Production effective-dated fees, fee math, product pass-through, V3 identity, and PAPER/LIVE posture already have current coverage. | `tests/test_regulatory_fees.py`, `tests/test_portfolio_engine_accounting.py`, `tests/test_product_backtest.py`, `tests/test_equity_residual_momentum_v3.py`, `tests/test_strategy_spec_freeze.py`, and `tests/test_live_safety.py`. |

All three paths were removed from `_HISTORICAL_RESEARCH_TEST_PATHS` in
`tests/conftest.py`. Classifier semantics and the current-research allowlist
were not changed.

## Exact pin/import audit

The audit was performed against the source HEAD named above before deleting the
modules:

- 141 JSON files under `data/research_manifests/` and
  `data/research_receipts/` parsed successfully. Recursive key/value scanning
  found **zero** exact references to any of the three test paths.
- An AST import scan of 468 Python files under `src/`, `scripts/`, and `tests/`
  found **zero** imports of the three test modules. The verifier/attestation
  scan found generic M3 `critical_git_blobs`/`source_identities` machinery, but
  no V3 target path or target module name.
- The only executable path references were the three classifier entries in
  `tests/conftest.py`; they were removed. Repository-wide text references in
  the archived V3 report, cleanup inventory, and Phase 2B compaction record are
  historical narrative references, not immutable executable/path pins.
- The target identities were not referenced by raw SHA or Git blob SHA:

| Path | HEAD Git blob | HEAD raw SHA-256 | Repository references found |
|---|---|---|---:|
| `tests/test_v3_exit_latency_integrity.py` | `b3bbefaffc12af16fb8ce6aad272bc97c1d2e4a6` | `34d8b3f19f493e188250067c74eba8babbf58b7347355095f3725a2ba4e3e8a5` | 0 |
| `tests/test_v3_position_risk_integrity.py` | `832d58d9e6e996e84a5be7d273cfe9e88f29e687` | `8471bde151c8cf75406ad9a5229ef49beccf4f72e7a94cfbccbb00b2f19d5d34` | 0 |
| `tests/test_v3_position_risk_cost_stress_integrity.py` | `a3225bbcfd76307808e2935bed4403ac75ba33b9` | `23d511ff4300ae9f9bc154d8ee40090c9bcbade476168ce35624386bad51978b` | 0 |

Git history records research implementation commits for these files, but those
history entries are not live path pins: exit latency and position risk were
last changed in `9220d7a1be79824cbdb4bdcc4d8365846e101050`, while the cost-stress
module was last changed in `345e842b2511545cfe282c5fe97ff2c37c3d70d0`.

## Current-production assertion audit

The following ranges cover every `assert` in the three source-HEAD modules.
Every listed assertion is **RESEARCH_SPECIFIC**; no assertion was classified as
a unique `CURRENT_PRODUCTION_INVARIANT` requiring migration.

### Exit latency module

| Source lines and test | Classification | Audit result |
|---|---|---|
| 17–31 `test_manifest_exists_and_declares_no_results` | RESEARCH_SPECIFIC | Predeclared E0-E3/no-results, 20 folds, cost levels, seed, and 12-criterion manifest contract. |
| 34–42 `test_forbidden_fast_folds_not_present` | RESEARCH_SPECIFIC | Rejected-runner source-text anti-heuristic guard. |
| 45–53 `test_no_curve_slicing_import` | RESEARCH_SPECIFIC | Rejected-runner source-text anti-curve-slicing guard. |
| 56–72 `test_cost_stress_is_real_rerun` | RESEARCH_SPECIFIC | Rejected-runner cost-stress rerun methodology. |
| 75–79 `test_temporal_is_independent_rerun` | RESEARCH_SPECIFIC | Rejected-runner temporal rerun methodology. |
| 82–89 `test_position_diagnostics_use_contemporaneous_equity` | RESEARCH_SPECIFIC | Rejected-runner diagnostic denominator methodology. |
| 92–102 `test_daily_exit_uses_point_in_time_v3_scoring` | RESEARCH_SPECIFIC | Rejected daily-exit experiment's PIT/scoring/source contract. |
| 105–123 `test_promotion_rubric_guards_all_12` | RESEARCH_SPECIFIC | Rejected experiment's 12 promotion criteria. |
| 126–130 `test_entry_policy_frozen_weekly` | RESEARCH_SPECIFIC | Historical runner's weekly-entry text, not the current product contract. |

### Position-risk module

| Source lines and test | Classification | Audit result |
|---|---|---|
| 18–37 `test_manifest_exists_and_declares_no_results` | RESEARCH_SPECIFIC | Predeclared P0-P3/no-results, fold/cost/seed, digest, and 14-criterion manifest contract. |
| 40–59 `test_candidate_definitions_exact` | RESEARCH_SPECIFIC | Rejected P0-P3 candidate geometry and tail-sizing definitions. |
| 62–68 `test_forbidden_fast_folds_not_present` | RESEARCH_SPECIFIC | Rejected-runner source-text anti-heuristic guard. |
| 71–74 `test_no_curve_slicing` | RESEARCH_SPECIFIC | Rejected-runner source-text anti-curve-slicing guard. |
| 77–84 `test_cost_stress_is_real_rerun` | RESEARCH_SPECIFIC | Rejected-runner cost-stress rerun methodology. |
| 87–91 `test_temporal_is_independent_rerun` | RESEARCH_SPECIFIC | Rejected-runner temporal rerun methodology. |
| 94–101 `test_position_diagnostics_use_contemporaneous_equity` | RESEARCH_SPECIFIC | Rejected-runner position-loss denominator methodology. |
| 104–117 `test_distinguish_candidate_worst_vs_axon` | RESEARCH_SPECIFIC | Rejected P0-P3 versus AXON diagnostic labeling. |
| 120–137 `test_tail_es_no_lookahead_and_frozen_params` | RESEARCH_SPECIFIC | Rejected P3 tail-ES frozen-parameter/no-lookahead contract. |
| 140–148 `test_tail_es_no_mae_feed` | RESEARCH_SPECIFIC | Rejected P3 sizing-versus-MAE separation. |
| 151–158 `test_p1_p2_preserve_rank` | RESEARCH_SPECIFIC | Rejected candidate rank/sizing methodology. |
| 161–168 `test_no_daily_alpha_exits_no_stops` | RESEARCH_SPECIFIC | Rejected position-risk scope boundary and weekly runner text. |
| 171–189 `test_promotion_rubric_guards_all_14` | RESEARCH_SPECIFIC | Rejected experiment's 14 promotion criteria. |
| 192–199 `test_independent_runs_every_window` | RESEARCH_SPECIFIC | Rejected-runner independence claims. |
| 202–209 `test_ex_ante_diagnostics_present` | RESEARCH_SPECIFIC | Rejected P0-P3 ex-ante diagnostic fields. |
| 212–218 `test_concentration_metrics_present` | RESEARCH_SPECIFIC | Rejected P0-P3 concentration diagnostic fields. |
| 221–226 `test_position_loss_metrics_use_account_equity` | RESEARCH_SPECIFIC | Rejected P0-P3 loss diagnostic fields. |
| 229–234 `test_axon_counterfactual_present` | RESEARCH_SPECIFIC | Rejected AXON counterfactual diagnostic fields. |
| 237–246 `test_paper_v3_untouched_live_disabled` | RESEARCH_SPECIFIC | Rejected manifest/runner claims about its historical baseline, not the live product config. |

### Cost-stress module

| Source lines and test | Classification | Audit result |
|---|---|---|
| 76–86 `test_1x_research_fee_equals_production_effective_dated_fee` | RESEARCH_SPECIFIC | Research-engine-versus-production comparison for the rejected runner. Current production date-sensitive fill behavior is covered by `tests/test_regulatory_fees.py:185-208`. |
| 89–106 `test_3x_fee_is_triple_including_cap` | RESEARCH_SPECIFIC | Rejected research cost-stress multiplier, including the TAF-cap geometry. |
| 109–116 `test_effective_dated_fees_remains_true` | RESEARCH_SPECIFIC | Research-engine construction flag preservation; production integration is covered independently. |
| 119–151 `test_make_engine_stores_correct_multiplier_and_costs` | RESEARCH_SPECIFIC | Rejected `make_engine` multiplier and cost wiring. |
| 154–174 `test_1x_full_candidate_engine_reproduces_baseline` | RESEARCH_SPECIFIC | Rejected P0 candidate engine/risk geometry and multiplier wiring. |

Current coverage checked for replacement purposes includes effective-dated
fee schedules and BUY/SELL formulae in `tests/test_regulatory_fees.py:21-136`,
production-engine date-sensitive fills in `tests/test_regulatory_fees.py:185-208`,
fee accounting in `tests/test_portfolio_engine_accounting.py:23-43`, product
fee pass-through in `tests/test_product_backtest.py:332-349`, V3 identity and
weekly cadence in `tests/test_equity_residual_momentum_v3.py:173-178` and
`tests/test_strategy_spec_freeze.py:89-97`, and the PAPER/LIVE safety posture in
`tests/test_strategy_spec_freeze.py:217-220` and `tests/test_live_safety.py:11-74`.

## Assertions migrated

**None.** No unique current-production assertion was missing after the audit,
so no test was expanded and no research-specific assertion was copied into a
current suite.

## Historical evidence retained

The deletion removed only executable regression-test modules. These historical
artifacts remain tracked and untouched:

- `scripts/research_v3_exit_latency.py` and `scripts/research_v3_position_risk.py`,
  plus their archived copies under `scripts/archive/research_2026_08/`;
- `data/research_manifests/v3_exit_latency_optimization_2026_08.json` and
  `data/research_manifests/v3_position_risk_optimization_2026_08.json`;
- archived result JSON under
  `data/research/archive/2026_08/v3_exit_latency_2026_08/` (12 files) and
  `data/research/archive/2026_08/v3_position_risk_2026_08/` (13 files);
- `docs/archive/research_2026_08/v3_exit_latency_optimization_2026_08.md` and
  `docs/archive/research_2026_08/v3_position_risk_optimization_2026_08.md`;
- `docs/v3_historical_validation_final.md`, whose frozen narrative states that
  rejected stop/position-risk tables are sensitivity context and that no
  further V3 historical optimization is current (`:3-10`), and retains the
  historical cost assumptions (`:116-121,134`);
- `docs/repository_cleanup_inventory_2026_08.md` and the Phase 2B compaction
  record, both preserved without edits.

No research runner, economics job, manifest, receipt, dataset, frozen result,
or historical report was rerun, rewritten, or deleted.

## Deleted files

- `tests/test_v3_exit_latency_integrity.py`
- `tests/test_v3_position_risk_integrity.py`
- `tests/test_v3_position_risk_cost_stress_integrity.py`

Each deleted file is recoverable from source commit
`71cfe2070edbb1c268b5e1ee673da28046ff3fc8`, for example:

```text
git show 71cfe2070edbb1c268b5e1ee673da28046ff3fc8:tests/test_v3_exit_latency_integrity.py
```

## Active suite impact

The default active partitions did not change:

| Collection or suite | Before Phase 2C | After Phase 2C |
|---|---|---|
| `pytest --collect-only -q --strict-markers` | 1460 collected | 1460 collected |
| `pytest -m "not research" --strict-markers` | Phase 2B: 1028 passed | 1028 passed, 432 deselected, 2 warnings |
| `pytest -m "research and not historical_research" --strict-markers` | Phase 2B: 432 passed | 432 passed, 1028 deselected |

The required current replacement set also passed:

```text
60 passed in 7.29s
```

## Historical reduction

The explicit root historical set goes from 12 modules to 9. The observed
historical collection goes from 40 modules / 733 items to 37 modules / 700
items. The reduction is exactly 33 items: 9 exit-latency + 19 position-risk +
5 cost-stress. The active 1460-item collection is unchanged.

## Validation

Commands run after the edit:

| Command | Result |
|---|---|
| `./.venv/bin/pytest --collect-only -q --strict-markers` | `1460 tests collected` |
| `./.venv/bin/pytest -m "not research" --strict-markers` | `1028 passed, 432 deselected, 2 warnings in 121.44s` |
| `./.venv/bin/pytest -m "research and not historical_research" --strict-markers` | `432 passed, 1028 deselected in 285.01s` |
| `./.venv/bin/pytest --include-historical-research -m historical_research --collect-only -q --strict-markers` | `700/2160 tests collected (1460 deselected)` |
| Required current replacement tests plus current V3 identity tests | `60 passed in 7.29s` |
| `./.venv/bin/ruff check tests/conftest.py` | passed |
| `./.venv/bin/ruff format --check tests/conftest.py` | passed |
| `git diff --check` | passed |
| Deleted-path recovery with `git cat-file -e HEAD:<path>` | all three recoverable |
| Post-delete classifier scan | no deleted target remains in `tests/conftest.py` |

The first product-suite attempt inside the restricted sandbox reported six
localhost-bind `PermissionError` failures in unrelated webhook/G9-G7 tests.
The exact command was rerun with local socket access and produced the passing
result above. No test or product code was changed for that environment issue.

No commit was created.

## Remaining historical-test families

- The nine explicit root historical modules are the retained candidate-
  observation and M3 lineage contracts: candidate-observation receipts,
  M3/IWM acquisition, M3 candidate OCP, M3 attestation R2, M3 data gate,
  M3 economic predeclaration/execution, M3/M4 synthetic behavior, and M3 R5.
- Non-current modules under `tests/research/` remain historical by default and
  continue to require `--include-historical-research` for collection.
- The current-research allowlist, M3 lineage, R46, candidate parity, terminal
  guards, current product tests, source code, config, manifests, receipts, and
  research artifacts remain outside this Phase 2C cleanup.
