# Historical root-test compaction — Phase 2B

Date: 2026-09-11

Source HEAD before Phase 2B: `849ad84e8e6f63a3618784b11c2aa13157650537`

Commit created: **none**

This record audits the 18 paths that were actually present in
`_HISTORICAL_RESEARCH_TEST_PATHS` at the start of Phase 2B, plus the explicitly
requested superseded R1 test. Phase 2A was already accepted in the source HEAD.
Only the historical-test classifier, the deleted test files, and this record
are in scope. Manifests, receipts, scripts, datasets, current tests, and
immutable research reports were not rewritten.

## DELETE_SUPERSEDED

These seven modules had no retained Python import, manifest/receipt path pin,
hash pin, verifier dependency, or unique current/final invariant after the
audit. Historical reports and research runners remain untouched; Git recovery
is recorded in the final section.

| Exact path | Decision evidence | Canonical replacement for the deleted test surface |
|---|---|---|
| `tests/test_crypto_candidate_tournament.py` (59 items) | The module is an independent T0–T6 tournament harness. The frozen report records T1 as the winner and says the current product/PAPER state was unchanged (`docs/crypto_candidate_tournament_validation.md:181-195,211,222`). No immutable JSON path pin or retained import exists. | Current crypto contract coverage in `tests/test_crypto_low_variance.py`, plus the unchanged frozen tournament report. |
| `tests/test_crypto_residual_low_variance.py` (27 items) | The final report says no R1–R3 candidate passed all 12 gates, no production class was frozen, and the residual family is formally rejected (`docs/crypto_residual_low_variance_validation.md:222,241-246`). No immutable path/hash pin or retained import exists. | Current total-low-variance product tests and the immutable residual-family rejection record. |
| `tests/test_equity_v3_alpha_roadmap.py` (8 items) | The roadmap report labels the work research-only, records no automatic promotion, and leaves production V3/config unchanged (`docs/equity_v3_alpha_roadmap_2026_08_report.md:54,61,68`). Its helper behavior is covered where still needed by current V3/OCP parity tests; no immutable path pin exists. | `tests/test_equity_residual_momentum_v3.py` and current `tests/test_candidate_observation_parity.py`; the roadmap runner/report remain historical. |
| `tests/test_equity_v3_followups.py` (6 items) | The follow-up report says research only, with no strategy registration or PAPER/LIVE wiring (`docs/equity_v3_followups_2026_08_report.md:5,53-66`). No immutable path/hash pin, retained import, or current invariant exists. | Current frozen V3 parity and current research contracts; the M5–M11 report remains the historical record. |
| `tests/test_research_alpha.py` (19 items) | The repository inventory explicitly classifies this as `C OBSOLETE_RESEARCH_RUNNER` and says its parity is covered by `test_equity_residual_momentum_v3` (`docs/repository_cleanup_inventory_2026_08.md:234`). No test-path pin or retained test import exists. | `tests/test_equity_residual_momentum_v3.py` for current parity; `src/parayabuck/portfolio/research_alpha.py` was not changed. |
| `tests/test_research_sec_normalization.py` (3 items) | This is a three-test pilot fixture around the old SEC fundamentals normalizer. Newer local normalization contracts exist in `tests/research/test_equity_h06_local_normalization_2026_09_09.py` and `tests/research/test_equity_secondary_local_normalization_2026_09_09.py`; no current production dependency or immutable path/hash pin exists. | The newer SEC/local-normalization research contracts and their existing manifests/reports; the old pilot script and fixture remain untouched. |
| `tests/research/test_equity_profit_protection_2026_09.py` (9 items) | R2 explicitly supersedes R1 and concludes `KEEP_CURRENT_V3_AFTER_VALID_COMPARISON` (`docs/research/equity_profit_protection_2026_09_r2.md:4-5,61-67`; `data/research_manifests/equity_profit_protection_2026_09_r2.json:7-11,205-239`). R1 has no immutable test-path/hash pin or unique final invariant. | Unchanged current-allowlisted `tests/research/test_equity_profit_protection_2026_09_r2.py` and its frozen R2 report/manifest/receipt. |

The R1 deletion required no classifier entry removal: `tests/research/**` is
historical by default and R2 remains explicitly current-allowlisted. The six
deleted root paths were removed from `_HISTORICAL_RESEARCH_TEST_PATHS`; its
metadata validation was not weakened.

## KEEP_PINNED

These six M3 modules are retained because immutable manifests/receipts bind the
exact test path and, where present, its Git commit, blob, and raw SHA. The
bindings below were audited without changing the artifacts.

| Exact test path | Immutable pin evidence |
|---|---|
| `tests/test_m3_iwm_acquisition.py` | `data/research_manifests/equity_m3_data_gate_2026_09_r3.json` and its receipt bind `critical_git_blobs.tests/test_m3_iwm_acquisition.py` to blob `6cb16c7d73c264cc749d2b453ec0e4fa788cc822`, raw SHA `e26e79691f6bdf3c927d791689ce26500774e77ceb8e43efe416f574cf0e7ec8`; R4 repeats the path with blob `36c08647806f7420f9edd4ed76e0c9646bdcdeb1`, raw SHA `41fd2c965ff6c16ea9a10fb355f40412b388afd4978d006d6371871710057e10`. |
| `tests/test_research_equity_m3_candidate_ocp.py` | `data/research_manifests/equity_m3_candidate_ocp_2026_09.json` binds the critical path to commit `32357c110c09f92b438ee188fac289da966bcd24`, blob `0ac9b8785338d06d4c9211b76fc0fe0a22e3d010`, raw SHA `6937299ae3340a39c5f08bd6a7b61f2944337d22fdbcd553a175a662accaa37b`; the R2 receipt repeats that substantive-replay identity. The earlier receipt also preserves commit `cae97cf9e059c5c03d79469b29e84d47533119d6`, blob `073af87ae0fd03c2b81b60d5baca1c6fe4fd9925`, raw SHA `2beb238d17bc18af9de7be241b4ba6adc8760b238760fdfb9910cded3365dad8`. |
| `tests/test_research_equity_m3_data_gate.py` | R2/R3/R4 manifests and receipts bind `critical_git_blobs.tests/test_research_equity_m3_data_gate.py`: R2 blob `47c5e991dde20c7d74a3921697a6b790dfcbefb4`, raw SHA `3e6fd94d8bac440f5d4ddd0cc1c04fdafb82ecfb4c27966aaee53626d30e8fbf`; R3 blob `a3c1e918ca4848cc6ee3b6fd3047af26ad0417d5`, raw SHA `e3f4707ce5195d4fd3587853351f0eaf4b33b3df998c6353b86fef031358838b`; R4 blob `cf35634604e611bc09f9ee5874b9f20601f236d5`, raw SHA `35971cc09e2f6b9f274040dc414afef067bc7e642a6c2ec340b6035334763628`. |
| `tests/test_research_equity_m3_economic.py` | `data/research_manifests/equity_m3_economic_2026_09_r1.json` and the predeclaration receipt bind `source_identities.tests/test_research_equity_m3_economic.py` to commit `db1ce1c5abc316a75d5972fb9138ffd77da1ca6a`, blob `815729bde96acdd04db729c734b892fbe197eb35`, raw SHA `0b988571e0dd0a77e2e716e9ffe2619a856b42d80f82b46b1e96caad69b3f2b7`; the execution manifest also preserves the later source identity at commit `6d39fb81dda1bacaad291a39fbee103d7ff0f303`, blob `2ef8d0d7e2b164f10bc481fd52e94fc5d0b59e0b`, raw SHA `0d5aed06018f1c4e8b6d02a5a02cdf1c11f252b892a8d507adac1b27b1fcd674`. |
| `tests/test_research_equity_m3_economic_execution.py` | `data/research_manifests/equity_m3_economic_execution_2026_09_r1.json` binds the path in `implementation_identity.changed_paths` and `source_identities` to commit `6d39fb81dda1bacaad291a39fbee103d7ff0f303`, blob `36f150e80865df4ed0553b8694dbdcec1f604e0a`, raw SHA `e7b18419a4e0e3f4808e5949cfbe47823b2024f44917ddd346ca263a3b871359`. The manifest also names the two regression tests `test_strategy_metrics_accepts_a_nonempty_curve` and `test_deduplicated_curve_anchors_at_the_metric_start_boundary`; the execution receipt repeats the changed-path binding. |
| `tests/test_research_equity_m3_r5.py` | `data/research_manifests/equity_m3_data_gate_2026_09_r5.json` and its receipt bind `critical_git_blobs.tests/test_research_equity_m3_r5.py` to blob `900a921e123cab3f3a81bddd0c874f7109134be8`, raw SHA `216a809d317b39d7350a787da7e71fdc58a17c03b44a0cb790c2fa12f5462709`. |

No pinned M3 manifest or receipt was edited or de-pinned.

## KEEP_FINAL_HISTORICAL_CONTRACT

These six modules are not current product tests, but each protects a final
historical contract for which no smaller replacement was found.

| Exact path | Why it remains authoritative |
|---|---|
| `tests/test_candidate_observation_parity_receipts.py` | The final OCP closure manifest names `scripts/compact_candidate_observation_parity_receipt.py` as the receipt compactor (`data/research_manifests/equity_candidate_observation_parity_final_closure_2026_08_r3.json:13-14`). The final receipt and report preserve exact E1/E2/E3 checks, fresh-instance checking, and `candidate_economics_run=false` (`docs/equity_candidate_observation_parity_final_closure_2026_08_r3.md:65-74`). The test validates the retained compactor boundary; there is no direct test-path pin, so this is C rather than B. |
| `tests/test_research_equity_m3_candidate_ocp_attestation_r2.py` | The canonical corrected R2 attestation verifier’s `R2_IMPLEMENTATION_PATHS` explicitly includes this test (`scripts/compact_equity_m3_candidate_ocp_attestation_r2.py:75-85`). It protects old-raw rejection, exact R2 identity, and no-economic-field attestation semantics. No weaker replacement or safe deletion was found. |
| `tests/test_research_equity_m3_m4.py` | The historical M3 receipt records `tests/test_research_equity_m3_m4.py:67-107` as verified `synthetic_only_legacy_tests` (`data/research_receipts/equity_m3_data_gate_2026_09.json:618-622`). Its three tests preserve the final synthetic fail-closed/reference-role behavior used by the retained M3/M4 research path. |
| `tests/test_v3_exit_latency_integrity.py` | The frozen V3 narrative says prior rejected exit-latency tables remain sensitivity context and no further historical optimization is current (`docs/v3_historical_validation_final.md:3-10`). This module preserves the predeclared no-results, independent-rerun, no-heuristic, and 12-gate integrity boundary for the rejected experiment. |
| `tests/test_v3_position_risk_cost_stress_integrity.py` | Its effective-dated fee/multiplier checks protect the frozen V3 cost geometry; the final V3 record keeps effective-dated regulatory fees and 2x/3x/5x stress as historical evidence (`docs/v3_historical_validation_final.md:116-121,134`). The current production fee tests do not replace this historical research-contract test. |
| `tests/test_v3_position_risk_integrity.py` | The frozen V3 narrative identifies rejected position-risk results as sensitivity context, not current results (`docs/v3_historical_validation_final.md:10`), while the repository inventory classifies this module among frozen final V3 parity evidence (`docs/repository_cleanup_inventory_2026_08.md:233`). It preserves the predeclared P0–P3/no-results/no-shortcut/14-gate contract. |

## RECLASSIFICATION_NEEDED

None. The current production V3, crypto, engine, terminal, and configuration
contracts remain covered by their current suites; no historical module needed
to be moved into the active allowlist.

## M3 conclusion

The minimal retained M3 historical surface is eight modules: the six pinned
modules above plus the corrected R2 attestation contract and the final M3/M4
synthetic contract. This preserves the complete data-gate → R5 → candidate-OCP
→ attestation → economic-predeclaration → economic-execution lineage without
adding new economics or regenerating results.

The canonical feed-transferability boundary remains explicit in
`data/research_receipts/equity_m3_economic_predeclaration_2026_09_r1.json:213-224`:
the economic-pass scope is **historical economic research PASS only**, the
historical feed is `sip`, `feed_match` is false, and PAPER, LIVE, and promotion
readiness are not established; the next required gate is
`M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE`. The companion report still
records the raw economic verdict `M3_ECONOMIC_GATE_FAIL`
(`docs/equity_m3_economic_2026_09_r1.md:8-15`). These are preserved historical
boundaries, not a promotion claim.

## Accounting and Git recoverability

The six root deletions remove 122 collected items; R1 removes 9 more. The
explicit historical root set therefore goes from 18 modules to 12. Including
the automatically historical `tests/research/**` modules, the observed
historical collection goes from 47 modules / 864 items to 40 modules / 733
items. The active partitions remain unchanged.

Every deleted implementation is recoverable from source commit
`849ad84e8e6f63a3618784b11c2aa13157650537`, for example with
`git show 849ad84e8e6f63a3618784b11c2aa13157650537:tests/test_research_alpha.py`.

## Validation

Commands were run without changing manifests, receipts, datasets, scripts, or
current product code.

| Command | Result |
|---|---|
| `./.venv/bin/pytest --collect-only -q --strict-markers` | `1460 tests collected` |
| `./.venv/bin/pytest --include-historical-research -m historical_research --collect-only -q --strict-markers` | `733/2193 tests collected (1460 deselected)` |
| `./.venv/bin/pytest -m "not research" --strict-markers` | `1028 passed, 432 deselected, 2 warnings` |
| `./.venv/bin/pytest -m "research and not historical_research" --strict-markers` | `432 passed, 1028 deselected` |
| Retained M3 contracts, clean temporary clone at source HEAD, excluding the pre-existing stale CI-command assertion | `433 passed, 1 deselected` |
| Candidate-observation plus retained V3 historical contracts | `39 passed` |
| `./.venv/bin/ruff check tests/conftest.py` and `./.venv/bin/ruff format --check tests/conftest.py` | passed |
| `git diff --check` and deleted-path/reference audits | passed; no stale classifier path, retained import, manifest/receipt path pin, or hash pin for the seven deletions |

The unfiltered M3 run from the intentionally dirty working tree reported
`426 passed, 8 failed`; all eight were the expected clean-worktree guards.
From a clean clone, only one pre-existing assertion failed because it expects
the obsolete CI command, while `.github/workflows/ci.yml` at the source HEAD
already uses the product/current-research marker commands. That out-of-scope
mismatch was not edited; excluding only that assertion produced the 433-pass
M3 result above. The current-research suite passed the actual marker commands.

## Phase 2C candidates

- Review whether the retained candidate-observation receipt contract can be reduced to a smaller canonical verifier.
- Review whether the retained M3/M4 synthetic contract can be subsumed by the final M3 verifier without losing the recorded boundary.
- Consider a separate, explicitly approved archive policy for rejected V3 experiment runners/data; do not infer deletion from this phase.
