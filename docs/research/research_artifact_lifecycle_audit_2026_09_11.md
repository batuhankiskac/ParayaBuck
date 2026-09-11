# Research Artifact Lifecycle Audit — 2026-09-11

## Audit basis

- Source HEAD: b3604d983f0bcd6ac9d8a6c600493c3c02b890c0 (Remove obsolete project artifacts).
- Audit date: 2026-09-11.
- The cleanup is intentionally uncommitted; the source HEAD above is the Git recovery reference.
- Audited rows are the 639 tracked files present at the source HEAD in the requested research/data/document scope. The new report and machine inventory are deliverables, not additional pre-cleanup rows.
- Ignored and untracked generated data, caches, runtime databases, external bundles, and local scratch outputs were not touched.
- No source, strategy, configuration, risk, PAPER/LIVE, current registration, test, economic-result, manifest, or receipt file was edited. The only retained-file edit is the minimal data-lifecycle policy addition in docs/research/README.md.

The machine-readable per-artifact inventory is research_artifact_lifecycle_inventory_2026_09_11.json. It records path, type, Git blob SHA-1, raw SHA-256, semantic JSON SHA-256 where applicable, family, classification, action, supersession relationship, canonical replacement, manifest/receipt/script/test/documentation/verifier/workflow references, duplicate groups, JSON recursive references, and rationale.

## Summary

| Measure | Result |
|---|---:|
| Files audited at source HEAD | 639 |
| Git-normalized bytes audited | 156,294,792 |
| A KEEP_CURRENT_CANONICAL | 94 |
| B KEEP_IMMUTABLE_PINNED | 220 |
| C KEEP_CANONICAL_HISTORICAL | 270 |
| D ARCHIVE_HISTORICAL | 0 files moved |
| E DELETE_EXACT_DUPLICATE | 1 file |
| F DELETE_SUPERSEDED_INTERMEDIATE | 1 file |
| G DELETE_INVALID_PLACEHOLDER | 1 file |
| H DELETE_REGENERABLE_SCRATCH | 0 files |
| I KEEP_EXTERNAL_IDENTITY | 1 file |
| J KEEP_AMBIGUOUS | 51 files |
| Baseline artifacts remaining after cleanup | 636 |
| Deleted bytes | 3,993,538 |
| Net Git-normalized scope reduction | 3,992,725 |
| New archive moves | 0 files / 0 bytes |

Classification is exactly one category per audited row. D and H are zero because no unpinned historical payload met the move proof and no tracked file was proven scratch-only after dependency review.

### Classification definitions

| Class | Definition |
|---|---|
| A | KEEP_CURRENT_CANONICAL |
| B | KEEP_IMMUTABLE_PINNED |
| C | KEEP_CANONICAL_HISTORICAL |
| D | ARCHIVE_HISTORICAL |
| E | DELETE_EXACT_DUPLICATE |
| F | DELETE_SUPERSEDED_INTERMEDIATE |
| G | DELETE_INVALID_PLACEHOLDER |
| H | DELETE_REGENERABLE_SCRATCH |
| I | KEEP_EXTERNAL_IDENTITY |
| J | KEEP_AMBIGUOUS |

## Filesystem scope and size

| Scope bucket | Files before | Git bytes before | Files retained | Git bytes retained |
|---|---:|---:|---:|---:|
| data/research/archive/2026_08 | 235 | 57,063,226 | 233 | 53,096,366 |
| data/research (root) | 58 | 20,466,479 | 57 | 20,439,801 |
| data/research_runs | 86 | 5,113,600 | 86 | 5,113,600 |
| data/research_manifests | 118 | 4,760,930 | 118 | 4,760,930 |
| data/research_receipts | 22 | 8,952,752 | 22 | 8,952,752 |
| data/historical | 14 | 58,932,901 | 14 | 58,932,901 |
| docs/research | 94 | 766,778 | 94 | 767,591 |
| docs/archive | 12 | 238,126 | 12 | 238,126 |
| reports | 0 | 0 | 0 | 0 |

The retained-byte column uses repository/Git-normalized bytes. Several tracked CSVs are CRLF-smudged in the macOS checkout while their Git blobs are LF; checkout newline conversion is not counted as cleanup. The README policy edit adds 813 bytes, so the net reduction is 3,992,725 bytes versus 3,993,538 bytes physically deleted.

## Deleted artifacts

| Class | Exact path | Bytes | Raw SHA-256 | Git blob SHA-1 | Canonical replacement | Proof and rationale |
|---|---|---:|---|---|---|---|
| F | data/research/archive/2026_08/equity_hardening_2026_08/phase23_fixed/0f41e1e500454d66875f04cdc07a9099/report.json | 3,964,512 | 19d62415342c76c57fcd75fbe81870a2d117c850866dd0e354c11760226a4ace | e6d6db4e5fc3a86356b6b72802e21787cf3780a8 | data/research/archive/2026_08/equity_hardening_2026_08/baseline_corrected/report.json | Superseded smoke/intermediate phase; byte-identical to the archive-indexed baseline_corrected canonical report; no retained pin or consumer targets this copy. |
| G | data/research/archive/2026_08/regime_execution_2026_08/frontier_synthetic.json | 2,348 | c4dcf0d92119af47c975a1918dd1291a8b45cd64e1f2cbd1cfe5f491b3735cf9 | 9f8c659d522d58f85319df568ee40de600dbf5df | — | Self-declared synthetic approximation, explicitly not a combined-engine run; the required synchronized dual-engine execution was deferred and no retained pin or consumer targets this file. |
| E | data/research/crypto_candidate_tournament_2026_08/full_window_results.json | 26,678 | 0cb211a62577387622d1848eb1c1f398bac3892c71532c33f570b882829a4bb6 | 92af722b50f87720e85cd91ac73d49325c78c42c | data/research/crypto_candidate_tournament_2026_08/full_window.json | Byte-identical alias of canonical full_window.json; the validation documentation calls this name an alias and the parity audit loads full_window.json. |

E removed 26,678 bytes from the tournament alias. F removed 3,964,512 bytes from a superseded hardening smoke phase that was also byte-identical to its canonical replacement; this duplicate overlap is not double-counted in E. G removed 2,348 bytes of explicitly synthetic/deferred frontier data.

## Archived artifacts

No artifacts moved in this pass. The existing data/research/archive/2026_08/ and docs/archive/research_2026_08/ trees remain immutable historical surfaces. Since there was no new move to data/research/archive/2026_09/, no new archive README/index was needed.

## Canonical current artifacts

- data/research/equity_profit_protection_2026_09_r2/ retains all six current R2 control/result and hash artifacts. They are B rather than merely A because the current manifest and receipt pin their exact paths and hashes.
- data/research/full_survivorship_2026_08/ retains the v5 canonical result/audit set and the pinned v4 lineage audits required by the correction chain.
- data/research_runs/zero_wait_overlays_2026_09_10/daily_nav.csv remains the protected current terminal-parity overlay input; no ignored companion output was promoted or rewritten.
- data/historical/ retains the current v5 PIT/corporate-action inputs and all pinned historical datasets. equity_1200_2016_2019.json is I: an external-identity sidecar for its large ignored CSV, not disposable scratch.
- The 94 docs/research/ files remain the current human research index/report surface and are classified A. The README received only the lifecycle policy addition below.
- All 118 manifests and 22 receipts remain B and byte-for-byte unedited.

## Pinned historical artifacts and evidence preservation

There are 140 immutable manifest/receipt files. They were parsed again after cleanup with no errors. The inventory keeps exact path pins, raw hash pins, JSON key paths, source/result identities, package-member references, and verifier references separately. No immutable file points to any deleted artifact path or deleted raw SHA-256.

The protected transitive surfaces are unchanged: R46 governance/data-package/source evidence, execution-cost P0/P1, equity profit-protection R2, current V3 parity and terminal parity, strategy freeze, active research tests, the all-34 closure record, and the retained M3 final contracts. M3 identity sidecars, receipts, compact results, attestations, source identities, and ignored-data references were not depinned.

## Duplicate groups

Raw duplicate scan found 13 groups before cleanup and 11 groups after cleanup. Two groups disappeared because one member was deleted; the remaining groups are retained intentionally.

### Remaining duplicate groups

| Raw SHA-256 | Members | Decision |
|---|---|---|
| b447d782b634820d87fd1e8430c00ed65e3b44b3826c559abf6599f0c3050215 | data/research/crypto_candidate_tournament_2026_08/regime.json [J]<br>data/research/crypto_candidate_tournament_2026_08/regime_analysis.json [J] | J: both documented/generated names remain; no canonical winner is proven. |
| 2e3b4ff2d647d63542f85aeca9b14e188714d371f9f4c826b11c8c9d82b7a4d8 | data/research/crypto_candidate_tournament_2026_08/turnover.json [J]<br>data/research/crypto_candidate_tournament_2026_08/turnover_analysis.json [J] | J: both documented/generated names remain; no canonical winner is proven. |
| 53aa9ad20f7fd5432fe8c76b329b9d8aac2a9c7f90f3476c77a577806543c234 | data/research_manifests/crypto_low_max_return_novelty_2026_09_n2_trace_draft.json [B]<br>data/research_manifests/crypto_low_max_return_novelty_2026_09_n2_trace_draft_pre_final_pin.json [B] | B: immutable manifest and run-bundle copies preserve separate identity/lineage. |
| e5e9aa6b9a21a086b9d5c2f3b5ad51cfae5e8c108eb1ba31b2ad4552346e9a3b | data/research_manifests/equity_low_beta_candidate_2026_09.json [B]<br>data/research_runs/equity_low_beta_candidate_2026_09/frozen_manifest.json [C] | B: immutable manifest and run-bundle copies preserve separate identity/lineage. |
| 6d8bbc0e751de721e6702c3a3a58c83bc2f31f3eab6941f197922cc67b6cb834 | data/research_manifests/equity_low_beta_candidate_2026_09_r2.json [B]<br>data/research_runs/equity_low_beta_candidate_2026_09/attempt_03_R2/frozen_manifest_r2.json [C] | B: immutable manifest and run-bundle copies preserve separate identity/lineage. |
| 2d8e3b2bb6b676d5a66973521031ac2964b6d7ab09ae262cfa1d596598a2e204 | data/research_manifests/equity_low_beta_candidate_2026_09_r3.json [B]<br>data/research_runs/equity_low_beta_candidate_2026_09/attempt_04_R3/frozen_manifest_r3.json [C] | B: immutable manifest and run-bundle copies preserve separate identity/lineage. |
| f44d2042bcf3a5a1a4c65f8caa02a569606ce67cb3cbc6b686f2395d238cbf7f | data/research_manifests/equity_low_beta_candidate_2026_09_r4.json [B]<br>data/research_runs/equity_low_beta_candidate_2026_09/attempt_05_R4_data_feasibility/frozen_manifest_r4.json [C] | B: immutable manifest and run-bundle copies preserve separate identity/lineage. |
| 7691186299dcabc7f4529ea4e4bd5ced98321b3c2c3738bab1e44f1c173cdde4 | data/research_manifests/equity_low_beta_candidate_2026_09_r4_draft_07ce.json [B]<br>data/research_runs/equity_low_beta_candidate_2026_09/attempt_05_R4_data_feasibility/frozen_manifest_r4_draft.json [C] | B: immutable manifest and run-bundle copies preserve separate identity/lineage. |
| 5b83239da90473ffbccebca3c85be133404d553b87d1d2190e3d8bad33eef915 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_funding_historical_2024_01_03.body [J]<br>data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_funding_historical_2024_01_03.body [J] | J: route bodies are symbol/path-specific provider evidence; raw equality is not semantic identity. |
| fb83363750e517f28edb859bfc0c95018edb466b0573d4626a790705dfdcdcac | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_future_basis_historical_2024_01_03.body [J]<br>data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_future_basis_historical_2024_01_03.body [J] | J: route bodies are symbol/path-specific provider evidence; raw equality is not semantic identity. |
| be73507c6120916fe87b5e285acd4ab2ae8111c6b17f375fa6374316dd77a650 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_01/blocked.json [C]<br>data/research_runs/equity_low_beta_candidate_2026_09/economic_results.json [C] | J: attempt-level and root blocked records preserve run lineage; no safe replacement was proven. |

The removed duplicate groups were the hardening phase23_fixed/report.json versus baseline_corrected/report.json and tournament full_window_results.json versus full_window.json; their deletion proofs are above.

## Supersession chains

- Hardening: phase23_fixed/<digest>/report.json → baseline_corrected/report.json. The former is a superseded smoke/intermediate phase and exact duplicate. The separate phase0_baseline_head/<digest>/report.json remains because docs/equity_strategy_hardening_2026_08.md declares that exact historical path in its reproduction narrative.
- Crypto tournament: full_window_results.json → full_window.json. The validation document calls the former an alias and the retained parity audit loads the latter. The same basename in crypto_residual_low_variance_2026_08 is a separate family and was not touched. regime.json/regime_analysis.json and turnover.json/turnover_analysis.json remain J because both names are documented/generated outputs.
- Full survivorship: the historical D_FULL reconstruction manifest is retained for lineage; the correction chain E→F→G→H and its v4/v5 audit files remain pinned. The current v5 set is not collapsed with v4 merely because its version is newer.
- Low-beta runs: attempt 01/02 blocked records and attempts R2/R3/R4 remain separate attempt evidence. Duplicate run-local manifest copies are retained with the immutable manifest identity; the draft is not silently rewritten or depinned.

## Invalid and synthetic artifacts

frontier_synthetic.json was the only retained standalone invalid/synthetic result proven removable. Its title and note explicitly state that values are prior mission approximations, not a new combined-engine run, and that the synchronized dual-engine run was deferred. It had no current exact path/hash/pin or consumer.

Previously accepted full-survivorship cleanup had already removed the old synthetic old_vs_full_walk_forward.json, invalid selection_overlap.json, and redundant v4/base mirrors before this audit HEAD. They are therefore documented historical prior-cleanup evidence, not additional current deletions. Synthetic fields embedded inside retained historical reports (for example, Top-N9 diagnostic result fields) were left intact because rewriting a historical economic result was out of scope. Dataset rows containing domain labels such as placeholder_* are documented identity/provenance values, not standalone placeholder files.

## Scratch and checkpoint review

No H row was deleted. Tracked stateful checkpoint JSON under the historical exit-profit-protection archive contains authoritative stateful replay traces, config/dataset/candidate-manifest hashes, and execution accounting; the word checkpoint is not enough to remove it. Low-beta progress/invocation logs and crypto route bodies remain J where attempt/provider identity is material. Ignored local scratch and resumable output was explicitly outside the tracked audit surface.

## Full-survivorship deep audit

The tracked data/research/full_survivorship_2026_08/ family contains 18 files; all remain B. The family’s v4 audits are retained for correction-chain lineage and the v5 files are the canonical parity set. No current manifest/receipt pin was changed.

| File | Bytes | Class | File-level decision |
|---|---:|---|---|
| data/research/full_survivorship_2026_08/active_symmetry_audit_v4.json | 3,856 | B | Retain v4 lineage; exact correction-F manifest pin. |
| data/research/full_survivorship_2026_08/active_symmetry_audit_v5.json | 622 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/corporate_actions_audit_v4.json | 2,380 | B | Retain v4 lineage; exact correction-F manifest pin. |
| data/research/full_survivorship_2026_08/corporate_actions_audit_v5.json | 256 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/corrected_v3_results_H_fixed.json | 3,588 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/final_verdict_v5.json | 1,202 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/full_v3_results_v5.json | 6,039 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/old_vs_full_walk_forward_real_v5.json | 11,175 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/parity_canary.json | 7,701 | B | Retain canonical parity gate/manifest evidence. |
| data/research/full_survivorship_2026_08/parity_manifest.json | 844 | B | Retain canonical parity gate/manifest evidence. |
| data/research/full_survivorship_2026_08/selection_overlap_real.json | 2,301 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/symmetric_v5_coverage_report.json | 15,101 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/terminal_event_accounting_audit_v5.json | 8,132 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/terminal_event_classification_v4.json | 9,544 | B | Retain v4 lineage; exact correction-F manifest pin. |
| data/research/full_survivorship_2026_08/terminal_sensitivity_v5.json | 632 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/ticker_identity_conflicts_v4.json | 10,403 | B | Retain v4 lineage; exact correction-F manifest pin. |
| data/research/full_survivorship_2026_08/ticker_identity_conflicts_v5.json | 65,171 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |
| data/research/full_survivorship_2026_08/universe_audit_v5.json | 127,270 | B | Retain canonical v5/fixed result or audit required by the closure/verifier chain. |

The prior full-survivorship archive README and docs remain historical indexes. They still name removed intermediates where needed to explain the cleanup; those mentions are not current executable dependencies.

## Tracked research runs

Tracked research runs were audited separately from canonical data. They contain 86 files and no run file was deleted or moved.

| Run family | Files | Bytes | Classes | Decision |
|---|---:|---:|---|---|
| data/research_runs/all_34_research_closure_2026_09_09 | 1 | 468 | B:1 | Protected closure summary retained. |
| data/research_runs/crossasset_max_testing_2026_09_09_v3 | 2 | 43,819 | C:2 | Tracked diagnostic or gate evidence retained. |
| data/research_runs/crossasset_retrospective_diagnostics_2026_09_09 | 1 | 1,250 | C:1 | Tracked diagnostic or gate evidence retained. |
| data/research_runs/crypto_max_testing_2026_09_09 | 49 | 991,417 | B:3, C:2, J:44 | Route/provider evidence retained; ETH/XBT byte-equal pairs are path-specific. |
| data/research_runs/equity_h01_h16_max_testing_verdict_2026_09_09 | 1 | 3,346 | C:1 | Tracked diagnostic or gate evidence retained. |
| data/research_runs/equity_h11_nport_snapshot_2026_09_09 | 1 | 1,026 | B:1 | Tracked diagnostic or gate evidence retained. |
| data/research_runs/equity_h14c_posthoc_2026_09_09 | 1 | 1,671 | C:1 | Tracked diagnostic or gate evidence retained. |
| data/research_runs/equity_local_diagnostics_2026_09_09 | 1 | 3,947 | C:1 | Tracked diagnostic or gate evidence retained. |
| data/research_runs/equity_low_beta_candidate_2026_09 | 28 | 219,526 | B:6, C:19, J:3 | Attempt/block/preflight lineage retained; no scratch-only proof. |
| data/research_runs/zero_wait_overlays_2026_09_10 | 1 | 3,847,130 | B:1 | Protected current terminal-parity daily NAV retained. |

The two ETH/XBT funding and future-basis body pairs are exact raw duplicates but are distinct route identities. The low-beta root/attempt blocked pair and manifest/run copies likewise preserve run topology. No filename-only or byte-only deletion was accepted.

## Active data/research root after cleanup

The active data/research/ root has 57 tracked files after cleanup; its August archive has 233 tracked payload files after the two archive deletions. Root families remain as follows:

| Root family | Files after cleanup | Classification mix |
|---|---:|---|
| data/research/crypto_candidate_tournament_2026_08/concentration.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/control_parity.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/cost_stress.json | 1 | B:1 |
| data/research/crypto_candidate_tournament_2026_08/fee_savings.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/final_verdict.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/fold_dependence.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/full_window.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/full_window_results.json | 0 | — |
| data/research/crypto_candidate_tournament_2026_08/gate_matrix.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/jackknife.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/pareto.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/pnl_concentration.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/regime.json | 1 | J:1 |
| data/research/crypto_candidate_tournament_2026_08/regime_analysis.json | 1 | J:1 |
| data/research/crypto_candidate_tournament_2026_08/tournament_plan.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/turnover.json | 1 | J:1 |
| data/research/crypto_candidate_tournament_2026_08/turnover_analysis.json | 1 | J:1 |
| data/research/crypto_candidate_tournament_2026_08/universe_identity.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/walk_forward.json | 1 | C:1 |
| data/research/crypto_candidate_tournament_2026_08/weight_turnover.json | 1 | C:1 |
| data/research/crypto_foundation_2026_08/crypto_universe_parity_audit.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/beta_diagnostics.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/control_parity.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/cost_stress.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/final_verdict.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/full_window_results.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/jackknife.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/pnl_concentration.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/regime_analysis.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/research_plan.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/turnover_analysis.json | 1 | C:1 |
| data/research/crypto_residual_low_variance_2026_08/walk_forward.json | 1 | C:1 |
| data/research/crypto_t1_promotion_2026_08/parity_audit.json | 1 | C:1 |
| data/research/crypto_t1_promotion_2026_08/promotion_receipt.json | 1 | C:1 |
| data/research/equity_profit_protection_2026_09_r2/E0_CONTROL.json | 1 | B:1 |
| data/research/equity_profit_protection_2026_09_r2/E1_TP5_CLOSE.json | 1 | B:1 |
| data/research/equity_profit_protection_2026_09_r2/E2_PROFIT_LOCK_50.json | 1 | B:1 |
| data/research/equity_profit_protection_2026_09_r2/E3_PROFIT_ALPHA_CONFIRMED.json | 1 | B:1 |
| data/research/equity_profit_protection_2026_09_r2/artifact_hashes.json | 1 | B:1 |
| data/research/equity_profit_protection_2026_09_r2/stage_one.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/active_symmetry_audit_v4.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/active_symmetry_audit_v5.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/corporate_actions_audit_v4.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/corporate_actions_audit_v5.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/corrected_v3_results_H_fixed.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/final_verdict_v5.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/full_v3_results_v5.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/old_vs_full_walk_forward_real_v5.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/parity_canary.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/parity_manifest.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/selection_overlap_real.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/symmetric_v5_coverage_report.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/terminal_event_accounting_audit_v5.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/terminal_event_classification_v4.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/terminal_sensitivity_v5.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/ticker_identity_conflicts_v4.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/ticker_identity_conflicts_v5.json | 1 | B:1 |
| data/research/full_survivorship_2026_08/universe_audit_v5.json | 1 | B:1 |

No new result was promoted to current/PAPER/LIVE status. Root research output remains limited to reviewed historical/canonical evidence; generated research runs and ordinary unreviewed data remain ignored according to the lifecycle policy.

## Reference graph and deletion proof

The graph scan parsed JSON recursively through keys and values and mapped exact tracked paths and raw SHA-256 values to source files and JSON pointers. It also retained Python/script/test/documentation/verifier/workflow references, manifest package members, receipt source/result identities, duplicate groups, and related basename/directory mentions.

- Baseline JSON files parsed: 448; errors: 0.
- Post-cleanup JSON files parsed: 445; errors: 0.
- Recursive exact JSON path-reference entries: 553.
- Recursive exact JSON hash-reference entries: 426.
- All tracked-source raw-hash reference entries retained in the inventory: 3,867.
- The three deleted artifacts have zero immutable manifest/receipt path or hash references.
- A post-cleanup retained-source sweep found no exact deleted-path dependency in current scripts, tests, verifiers, workflows, or current artifact declarations. Historical docs retain directory/basename mentions only where they explain the old lineage; the tournament documentation’s alias mention is historical documentation and points readers to full_window.json as canonical.
- Unresolved external/gitignored paths remain reported as identity/provenance references, especially the large historical CSV sidecars and ignored research bundles; they were not treated as missing canonical evidence and were not modified.

## docs/research/README.md lifecycle update

The README now states the minimal data policy: manifests/receipts are immutable; canonical final artifacts remain; exact path/hash pins are never moved; unpinned historical payloads may be archived only with an index; exact duplicates, invalid placeholders, and superseded intermediates may be deleted only after proof; Git history recovers deleted intermediates. No cleanup log or economic conclusion was added to the README.

## Git recoverability

Deleted tracked content is recoverable directly from the source HEAD, for example:

~~~bash
git show b3604d983f0bcd6ac9d8a6c600493c3c02b890c0:data/research/crypto_candidate_tournament_2026_08/full_window_results.json
git show b3604d983f0bcd6ac9d8a6c600493c3c02b890c0:data/research/archive/2026_08/regime_execution_2026_08/frontier_synthetic.json
~~~

No commit was created by this cleanup. The three deleted paths remain available in Git history, and the source HEAD is recorded in both audit deliverables.

## Validation

### Tests

| Command | Result |
|---|---|
| pytest --collect-only -q --strict-markers | 1460 tests collected |
| pytest -m "not research" --strict-markers | 1028 passed, 432 deselected, 2 warnings; required loopback permission escalation |
| pytest -m "research and not historical_research" --strict-markers | 432 passed, 1028 deselected |

### Current research and historical contracts

- The current 432-test research gate passed and covered R46, P0/P1, R2, current V3 parity, terminal/parity infrastructure, and active research contracts. No economic experiment was rerun.
- scripts/research/verify_all_34_closure_2026_09_09.py --verify passed read-only: candidate_count 34, issues [], status ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS, record SHA 48b3525cb52a72a540e83e34edea9367ee4265b4ddf1f7be02f970b7b8204cf6.
- The all-34 closure test passed: 10 passed.
- The retained M3 final subset passed in a disposable clean clone with the cleanup diff applied and committed only in that clone: 447 passed, 1 deselected. The direct dirty-worktree attempt’s seven failures were the intentional M3 clean-worktree guard, not cleanup failures.
- No canonical frozen V3 artifact was touched; current V3/terminal/strategy coverage passed within the 432-test current research gate.

### Hash/pin and hygiene validation

- All 140 manifests/receipts parsed with zero errors after cleanup; no deleted/moved path or raw hash is required by immutable evidence.
- git diff --check passed.
- Retained JSON parse passed: 445 files after deletion, zero errors.
- No Python support tooling changed, so Ruff was not run for this data/docs-only cleanup.
- The post-cleanup duplicate scan found 11 raw duplicate groups; all remaining groups have a documented path/lineage reason in the inventory.
- Markdown sanity is covered by the report’s required headings, tables, links, and the final git diff --check; no unrelated formatting was performed.

## Per-artifact audit table

The following table has one row for every audited artifact. Full reference lists and rationales are in the machine inventory; the refs count is the deduplicated graph-reference count recorded for that row.

| # | Artifact | Type | Bytes | Raw SHA-256 | Class | Action | Canonical replacement | Refs |
|---:|---|---|---:|---|---|---|---|---:|
| 1 | data/historical/crypto_expanded_1h.csv | csv | 50,157,874 | 73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e | B | KEEP | — | 272 |
| 2 | data/historical/equity_1200_2016_2019.json | json | 527 | 14002217cf3d3691dc82989fb1c8647f1a55d4d3e2db472ba5ce16bb7ff4b9e4 | I | KEEP | — | 4 |
| 3 | data/historical/equity_1200_dividends.csv | csv | 700,427 | 7d79025d327a9f43b0170b252499ce00343d1dcf2f48cfde3e05e5ebd073e1e0 | B | KEEP | — | 309 |
| 4 | data/historical/equity_entity_lifetimes_full_v3.csv | csv | 1,049,680 | dfda6a38ccc95bc8ea2e3ca823e947aab94c8348569417f86da4fd18b4b1a67a | C | KEEP | — | 5 |
| 5 | data/historical/equity_entity_lifetimes_full_v4.csv | csv | 1,040,549 | 1dbf706d2a2b40c878b9e0c645a5e4ac04d5a2ae7c268066f357c7276bb2f765 | B | KEEP | — | 23 |
| 6 | data/historical/equity_entity_lifetimes_full_v5.csv | csv | 2,320,175 | fe998bf81af1f705accf461f13a6e71fdcedb2d68a317c5aa2152833b4cad4f0 | B | KEEP | — | 528 |
| 7 | data/historical/equity_entity_lifetimes_v2.csv | csv | 437,103 | 6aa7e681ab40678d7fb8f5668c66074220941970efce170e3b6441521a50556c | B | KEEP | — | 17 |
| 8 | data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json | json | 873 | d4266d7331e3bb1ea6303772ad14a4ea4ff01cd7845d507a138836216f81011f | B | KEEP | — | 237 |
| 9 | data/historical/equity_survivorship_aware_2016_2026_v2.manifest.json | json | 638 | 8a59a5588498f78a29402a780ba621ee6c301a915f1ff6c64e3cb6a18db11701 | B | KEEP | — | 6 |
| 10 | data/historical/equity_survivorship_aware_full_2016_2026_v3.manifest.json | json | 981 | 10f9d20e07704f603bfe3847143bb7debf7976f2825fac74b168d22190a32335 | C | KEEP | — | 2 |
| 11 | data/historical/equity_survivorship_aware_full_2016_2026_v4.manifest.json | json | 1,315 | 7f1ab4a18fc8586a8cc074d91d2ea365cb7d8ff69ae8883ec29eef6ff5a4a776 | C | KEEP | — | 0 |
| 12 | data/historical/equity_survivorship_full_corporate_actions_v4.csv | csv | 3,221,291 | bfac23b8b40496075ee21312ed792c922a1041f3977cd21077ddf330d4573f04 | B | KEEP | — | 17 |
| 13 | data/historical/equity_survivorship_full_corporate_actions_v4.manifest.json | json | 725 | f5ace80f9c9c9e960906bc4d8c5947644d9194a33e7046b5a27f22cd0815193f | B | KEEP | — | 6 |
| 14 | data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json | json | 743 | 514cc2f608b043da2608fe87753aa45cdfbe71a99ef970aee82c1d3361955953 | B | KEEP | — | 194 |
| 15 | data/research/archive/2026_08/README.md | markdown | 2,304 | acd13d4b59a67611474b9a9c4725efc998d1ae03ab536f23b79e89695b69437b | C | KEEP | — | 4 |
| 16 | data/research/archive/2026_08/capital_foundation_2026_08/results.json | json | 13,281 | 40f99e2aff5bc12346313cae43ee69b7e8f90b77bcf99ad34dac1891ee963a01 | C | KEEP | — | 0 |
| 17 | data/research/archive/2026_08/cash_parking_2026_08/combined.json | json | 18,014 | d17fe1f2ee7a1f998a04a64717385f946499eed4e9645ce7b322e4a5d7aa866d | C | KEEP | — | 3 |
| 18 | data/research/archive/2026_08/cash_parking_2026_08/generate_results.py | python | 715 | 09ecf3616102672c34799c774b1eaab574a73b96de5d1dfe66439288436bbff0 | C | KEEP | — | 0 |
| 19 | data/research/archive/2026_08/cash_parking_2026_08/results.json | json | 6,348 | eef41508e9659ce3fb152eb84b5a897758ccc90de7a27638e489dd4390447d7b | C | KEEP | — | 0 |
| 20 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/a3.json | json | 49,244 | 0a5cdc2febb9cf5086681ec23d7fa7f5dd45ed95d2b5906425cc070c19408745 | B | KEEP | — | 4 |
| 21 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/a3_regime.json | json | 100,434 | 8d429a59a32f7a7bccbf5ad5a39b713715bde04ce9b10d212808620dd9afb33c | B | KEEP | — | 5 |
| 22 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/annual_results.json | json | 23,048 | c3936a27e1bbc28700c5ea90f8526447c5883082be98d68e5b0e83a0919f68f7 | B | KEEP | — | 4 |
| 23 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/bootstrap.json | json | 6,619 | a6f4ec0464de14afdb5d70b75a42e67e706d253ca30d89344996adb4e1c8a766 | B | KEEP | — | 4 |
| 24 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/control.json | json | 49,219 | 5afa0d7c16d53e3e64be8605696c53c0a0be66a8c92f70211391e67378e6cf8c | B | KEEP | — | 4 |
| 25 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/control_hyst.json | json | 49,238 | 95e4edbefd5587662d82c3f262d00ce6ae945abcc2c47c4a7c6de37c0ceed214 | B | KEEP | — | 5 |
| 26 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/control_regime.json | json | 100,406 | 776f3490eb7d605b5571f4299b99be099e306553c861c2d475de998571a85e27 | B | KEEP | — | 5 |
| 27 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/control_sqrt.json | json | 49,210 | 8bcff8c86418cdc599cc063beb24200cc0b8ba81aa66a1c954493fc9f09ae9c0 | B | KEEP | — | 5 |
| 28 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/cost_stress.json | json | 21,249 | 735befc4a9270a69dea9e19dd7df3c769f8e5c5fd833942996abccb415345a0f | B | KEEP | — | 4 |
| 29 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/fold_deltas.json | json | 102,746 | b9824d2dae4ece01e09277bdc254f62391b5265ae2c1bad4ea7c372c04e4d653 | B | KEEP | — | 23 |
| 30 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/h25.json | json | 49,258 | 735c8581079d7df1a4ed3c3c0478e0272e612d1bbdd991c670e6b034b95de696 | B | KEEP | — | 4 |
| 31 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/h50.json | json | 49,277 | 39b7ccca83b1e8a216d7824bfc2f34a651929bd7214623d31e1785ceffe8f538 | B | KEEP | — | 4 |
| 32 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/h50_regime.json | json | 100,465 | 8e7ab2a26b231e0d8071d7ca5a95e37ba0c6ffd091e080bc11f32f7ad4880177 | B | KEEP | — | 5 |
| 33 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/h50_sqrt.json | json | 49,268 | c3c14c78f74cbf1df9b98f14291a0e1aeac099221d5d064ccfcf46c1e91c6a0a | B | KEEP | — | 5 |
| 34 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/h75.json | json | 49,238 | 9608b6191d6768102cc533d62909e5f8d966b0b435fe2cd2a70d7bf9805a3424 | B | KEEP | — | 4 |
| 35 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/jackknife.json | json | 8,957 | 8b4f663903c342318db8a13ba40e736781792bb3e85a2ea93e31bdc21163bdec | B | KEEP | — | 4 |
| 36 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/mh_raw.json | json | 49,273 | b24512e2b1db44bc64347e891c556adddb459c7b580a286529094c20f43668f7 | B | KEEP | — | 4 |
| 37 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/mh_std.json | json | 49,250 | e4a432f21fcdf20660814e4aa36fe99e2c829323a482e1603a0c968feb42ecc0 | B | KEEP | — | 4 |
| 38 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/mhstd_regime.json | json | 100,438 | 51751d6752a37447d8ae10a31451c441870e4761a05ddce112ebab41e94a482a | B | KEEP | — | 5 |
| 39 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/mhstd_sqrt.json | json | 49,272 | 7db1014233bc0ae4096046721887ab6818bfe0d038229bc979e0a822e501acc3 | B | KEEP | — | 5 |
| 40 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/recent_period.json | json | 12,054 | e3dba6e934d1dd78239411ddba9f974097933572aafc8902e2fc6382fcb8308b | B | KEEP | — | 17 |
| 41 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/regime_analysis.json | json | 211,930 | 79ce7dd50e136a32c95da64747585629746ce218eee28d234245f889e8822626 | B | KEEP | — | 4 |
| 42 | data/research/archive/2026_08/equity_alpha_optimization_2026_08/summary.json | json | 8,381 | 82768b9a6fad9f0bc19fd412e7b8516084473f47f8411bfc43e85cf3bda3af96 | B | KEEP | — | 4 |
| 43 | data/research/archive/2026_08/equity_hardening_2026_08/a1.json | json | 6,146 | 682af5f05ac40a17ddc81803fad1e0db5cd1930ed601ab1ff96d4caaae7c1ab0 | B | KEEP | — | 4 |
| 44 | data/research/archive/2026_08/equity_hardening_2026_08/a2.json | json | 6,158 | efb60476c63c6e58a4f27d162f99d8a3411e9f0e95976e332ab396c4ef2190e9 | B | KEEP | — | 4 |
| 45 | data/research/archive/2026_08/equity_hardening_2026_08/a3.json | json | 6,167 | abf64f20dfd85a50ca24d0f7761067b9071c4fc80676871a6b06beffd2a15396 | B | KEEP | — | 4 |
| 46 | data/research/archive/2026_08/equity_hardening_2026_08/b504.json | json | 6,144 | c686cdfd2f5c4c436f3bc9f5cc9e19731937556f5c42a94f13ca64e0a162a2cc | B | KEEP | — | 4 |
| 47 | data/research/archive/2026_08/equity_hardening_2026_08/b756.json | json | 6,076 | 9257759a4fb75388dffc6b8a88140aa5e27145c1768508cb9622a5769e2ce8d2 | B | KEEP | — | 4 |
| 48 | data/research/archive/2026_08/equity_hardening_2026_08/baseline_corrected/identity.json | json | 618 | a8bc40dd3e58aea364ce03bd610edd473851ccc1b32158f511ba7a9a64d92c16 | C | KEEP | — | 1 |
| 49 | data/research/archive/2026_08/equity_hardening_2026_08/baseline_corrected/regime_performance.json | json | 218 | a323c0270766fdb1338b3fccc3b68743aefcb357e0d56219c62c780976e98e49 | C | KEEP | — | 0 |
| 50 | data/research/archive/2026_08/equity_hardening_2026_08/baseline_corrected/report.json | json | 3,964,512 | 19d62415342c76c57fcd75fbe81870a2d117c850866dd0e354c11760226a4ace | C | KEEP | — | 0 |
| 51 | data/research/archive/2026_08/equity_hardening_2026_08/baseline_corrected/summary.json | json | 829 | 7527e1d50fd506978dfba43d9d8a3f6bcba0b62f4bdede184f21ff46382b3d78 | C | KEEP | — | 0 |
| 52 | data/research/archive/2026_08/equity_hardening_2026_08/baseline_corrected/yearly_returns.json | json | 1,081 | 74aa46794a5cd08a28749607a44f4e9bbe8f85350094fc48d9e0d106a343549b | C | KEEP | — | 0 |
| 53 | data/research/archive/2026_08/equity_hardening_2026_08/c_tr.json | json | 6,179 | 3818b999546244baaea24904ca7177b1cf09370267b3a076ef44702f32c6e185 | B | KEEP | — | 4 |
| 54 | data/research/archive/2026_08/equity_hardening_2026_08/control.json | json | 6,173 | 07610552f3b3e300c4939bd87c001635e4f94f30d5b66c071faf9092ac6608f0 | B | KEEP | — | 4 |
| 55 | data/research/archive/2026_08/equity_hardening_2026_08/phase0_baseline_head/88f820965bb44a968ce3d4d443abfe38/report.json | json | 4,074,821 | 1aa48f317dad795e2c72dfba7c5ee450b9ccf166674610b560fd0fae04664b54 | C | KEEP | — | 0 |
| 56 | data/research/archive/2026_08/equity_hardening_2026_08/phase23_fixed/0f41e1e500454d66875f04cdc07a9099/report.json | json | 3,964,512 | 19d62415342c76c57fcd75fbe81870a2d117c850866dd0e354c11760226a4ace | F | DELETE | data/research/archive/2026_08/equity_hardening_2026_08/baseline_corrected/report.json | 2 |
| 57 | data/research/archive/2026_08/equity_hardening_2026_08/predeclared.json | json | 4,007 | fc3252ba7723467ab43351ef198f75b453e0d0cf69f0c028431bd194abcbb087 | C | KEEP | — | 3 |
| 58 | data/research/archive/2026_08/equity_hardening_2026_08/production_policy.json | json | 13,683 | 8377931f95599c6125bc97afa03eeb2d729cfbe2eed505ed08463168c5b37341 | B | KEEP | — | 5 |
| 59 | data/research/archive/2026_08/equity_hardening_2026_08/production_policy_smoke.json | json | 12,754 | 5194f3240e16cc2fbcc1d11bc9887ce483de605a7b886e54bfad494c85c56295 | C | KEEP | — | 0 |
| 60 | data/research/archive/2026_08/equity_hardening_2026_08/topn6.json | json | 6,168 | a8865d23c7b4eedacd313bdbf79dfc1c81d9adc585e2c7ed911e3630e10b2c1c | B | KEEP | — | 4 |
| 61 | data/research/archive/2026_08/equity_hardening_2026_08/topn9.json | json | 6,179 | 51434b2afbc067efa9b98f2b84678a03e3bb81639a65bebe03f75e17db76dd9a | B | KEEP | — | 4 |
| 62 | data/research/archive/2026_08/exit_profit_protection_2026_08/finalist_stability.json | json | 109,409 | eefcae0c70f41822b325e9ec063ad15987050f01a24e3289b98c7c9e4d159e51 | C | KEEP | — | 0 |
| 63 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_break_even_0p005_canonical.json | json | 132,477 | 88c92681707fae98e806cdd1b30b61afb7810a65608f0314c5e3eda2f26ef14f | C | KEEP | — | 0 |
| 64 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_break_even_0p005_no_stop.json | json | 136,975 | f0343645c750b962104418ee3dfc18dad19f8fad3900f84dd36a98951674823b | C | KEEP | — | 0 |
| 65 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_break_even_0p005_runtime.json | json | 217,927 | 5bcb7adff7ae616f1a1158f753105f695ee0d4fa2c5259fc897765de317e6971 | C | KEEP | — | 0 |
| 66 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_break_even_0p01_canonical.json | json | 102,491 | 9ead4332d72b6f10e9a0f850ce4cb22ab1cc6fb3f53993080a2cc7e69115fe7e | C | KEEP | — | 0 |
| 67 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_break_even_0p01_no_stop.json | json | 106,247 | 1b290d4db75b10416b6cc56f256866911f701acea02bd739e187f665a9d5ef01 | C | KEEP | — | 0 |
| 68 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_break_even_0p01_runtime.json | json | 155,747 | 322ce14f10a734e1e4a76cb34f414324968a5287c402c77d2d09ef311aa5100f | C | KEEP | — | 0 |
| 69 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_break_even_0p02_canonical.json | json | 43,386 | 9016188c24e5df4a67d634f662d443419f072338f5516bf8119f42fc02a61a17 | C | KEEP | — | 0 |
| 70 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_break_even_0p02_no_stop.json | json | 45,673 | 15a9da80e15a507c3280b11a950ecb68b1ab74eca42e6e9e91f31efb3611894d | C | KEEP | — | 0 |
| 71 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_break_even_0p02_runtime.json | json | 143,104 | 95e8f4221e3cf523e98f55fc6b4ad64b37a328b6026a20ccb44c618e5c36b806 | C | KEEP | — | 0 |
| 72 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_canonical_strategy_control_canonical_canonical.json | json | 4,541 | 453de23bc2f5973b389e5e06d85d6746833ec9691765cb66346994469fa314f0 | C | KEEP | — | 0 |
| 73 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_current_runtime_protection_control_runtime_static_runtime.json | json | 2,299,958 | 59daf8e92d0d06077465c5143aacdc41f6b3a844c8537da2758c3be397700aee | C | KEEP | — | 0 |
| 74 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_fixed_trail_0p01_canonical.json | json | 96,659 | 78049aefcc4f41c89714a9d0fef72badb5e28af3ef12d1920b5b6eab21d770a6 | C | KEEP | — | 0 |
| 75 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_fixed_trail_0p01_no_stop.json | json | 100,312 | a3cf04b26e91b0e242759c6d90a526470e9ac9b10944a0af5398dee53a0b72e2 | C | KEEP | — | 0 |
| 76 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_fixed_trail_0p01_runtime.json | json | 236,787 | 1dc562d3d5036e215a93aa9ac25c750b66f446f552b0e9fe7de0688207964d8a | C | KEEP | — | 0 |
| 77 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_fixed_trail_0p02_canonical.json | json | 95,049 | de694e13d5a5041bc617ca77ee9aeba020d91c4d2e0d4e09da9475551a175ea9 | C | KEEP | — | 0 |
| 78 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_fixed_trail_0p02_no_stop.json | json | 98,663 | 48c343a6ed1a5a3b40e48806ac70ca945a65b100eed5bb3475b1dd56af8d1b52 | C | KEEP | — | 0 |
| 79 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_fixed_trail_0p02_runtime.json | json | 234,573 | b003fbff5fc0fd37a6ae6eca90e2335f2453632a21df9f1e2aad385e3b0977eb | C | KEEP | — | 0 |
| 80 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_fixed_trail_0p03_canonical.json | json | 103,479 | 20bc3c951c142b0e5548f77fb5a21678b20dcc8945e4acc4f03f326ee9ebea92 | C | KEEP | — | 0 |
| 81 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_fixed_trail_0p03_no_stop.json | json | 107,301 | a98db003ae1896216c242a31e6ff2b29ecbcd5ef7fdfe0d95050e3b21faf54ba | C | KEEP | — | 0 |
| 82 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_fixed_trail_0p03_runtime.json | json | 230,429 | b104dde701bfb919ea297c4a0178e1b4fb3e148eda1e2dc2df052e744db60bd9 | C | KEEP | — | 0 |
| 83 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_hybrid_1p0_canonical.json | json | 97,649 | e212d030fb56ea8fd2730b7ebc0a308c72a5707755dc9b179d102d2cce158981 | C | KEEP | — | 0 |
| 84 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_hybrid_1p0_no_stop.json | json | 101,309 | 00f38fda6b22948fbaa4e707d377279d19d2e0793326629e152f49a54cd76ba8 | C | KEEP | — | 0 |
| 85 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_hybrid_1p0_runtime.json | json | 235,760 | c82eed7c275494827264596c6a367b62d85d119414eedc963ad5044158db23ee | C | KEEP | — | 0 |
| 86 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_hybrid_1p5_canonical.json | json | 97,813 | b2cbe525099ef22a649f3872028bbd4067fef1d36c01e4ff18db4a9942884dcf | C | KEEP | — | 0 |
| 87 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_hybrid_1p5_no_stop.json | json | 101,473 | c6743217d5d8eb56dd3342c600ac8ff422139cb2939dbd7eba804cc6f7710286 | C | KEEP | — | 0 |
| 88 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_hybrid_1p5_runtime.json | json | 235,903 | da77af2ba6c1e43e14685c8ee86a865339de92bf78d5761ba7392c5e639b7225 | C | KEEP | — | 0 |
| 89 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_hybrid_2p0_canonical.json | json | 97,738 | 4ba96ca2fe9a6c9eb7f12d7f456df09ae52c8c91f4c063f54c385d26ad972a54 | C | KEEP | — | 0 |
| 90 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_hybrid_2p0_no_stop.json | json | 101,398 | 2ce717081dff9541e11ebc679c1b543725805ae1213bd61c3978829195dd4990 | C | KEEP | — | 0 |
| 91 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_hybrid_2p0_runtime.json | json | 235,909 | 3cf95f8ee541d46010b35bdf48a1bc9abc5d9966900986bda486dad957610bd2 | C | KEEP | — | 0 |
| 92 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_ladder_single_canonical.json | json | 94,206 | ea20f6942fbc800d93019b42fd1c42778a6040ea48d480609e9f88d789468bb0 | C | KEEP | — | 0 |
| 93 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_ladder_single_no_stop.json | json | 97,765 | 37cbf5c36375b90c3da1c5194709cb34346b6a86f87c61afcd698b1513efdbb8 | C | KEEP | — | 0 |
| 94 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_ladder_single_runtime.json | json | 207,638 | 6b0ff1da179a83414c14ca6a2235aacf3a8990171281c50e40df300b61f78d18 | C | KEEP | — | 0 |
| 95 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_profit_lock_0p25_canonical.json | json | 90,629 | 51b65c6d75519c92060c7ebe5a06fc6b414ac71ae608b3f6c1d464fc63839bb3 | C | KEEP | — | 0 |
| 96 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_profit_lock_0p25_no_stop.json | json | 94,100 | 945fe4b7012ceac982aac1c8cba3d19e5890da32c1217b3f9fc78bee170a4180 | C | KEEP | — | 0 |
| 97 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_profit_lock_0p25_runtime.json | json | 213,757 | a6a46533b11af036ff7115c8a1fbd61a80879f263d69bffd864174ff51f90549 | C | KEEP | — | 0 |
| 98 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_profit_lock_0p5_canonical.json | json | 96,447 | 484d7f58e93c770d9d87475af938a84b79af9c8ff6d5173fa0bd07920972ca83 | C | KEEP | — | 0 |
| 99 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_profit_lock_0p5_no_stop.json | json | 100,073 | 1bd0f35b4fc682ecc62a727786cce1853b4597d943818aa186df34a7188f7e56 | C | KEEP | — | 0 |
| 100 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_profit_lock_0p5_runtime.json | json | 236,970 | dde0acfde1c346404d2ee81dc7fe2527f9780f37cb4062b5352fb42b9393bead | C | KEEP | — | 0 |
| 101 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_profit_lock_0p75_canonical.json | json | 98,291 | d27cb24512e3c3fd2c154a05c301c4fdf96e04d5d39af512689282d0a4bfc4b0 | C | KEEP | — | 0 |
| 102 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_profit_lock_0p75_no_stop.json | json | 101,957 | 907453b0c19428100a82ea8c490f26342336f042d76c63993b7767ed36488806 | C | KEEP | — | 0 |
| 103 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_profit_lock_0p75_runtime.json | json | 238,474 | 789a644d444d1626471b518a387b3a6702823e678f370aff01f94e7fa149f6d3 | C | KEEP | — | 0 |
| 104 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_runtime_execution_no_protection_control_runtime_no_stop_no_stop.json | json | 3,717,231 | 570be982bc6c559006ceac7fb88455544ad4f3878c0a0a2bede7a676b776b80f | C | KEEP | — | 0 |
| 105 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_volatility_trail_1p0_canonical.json | json | 98,777 | 20270f52d5a68d3075bf6a1d3a83b485866a8ebf72ae15bb18e0e581f034bd99 | C | KEEP | — | 0 |
| 106 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_volatility_trail_1p0_no_stop.json | json | 102,447 | f7dabca7bb714f47d649290638bc87d2774014f826827d944f6fb00a46159082 | C | KEEP | — | 0 |
| 107 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_volatility_trail_1p0_runtime.json | json | 239,971 | b1ac3cb7c88943cf4757cdacef3fcc7af6950e817f939376ace6853a37328608 | C | KEEP | — | 0 |
| 108 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_volatility_trail_1p5_canonical.json | json | 99,135 | a230b68f79b58861bbf09db2dd8e2116d2a27e621dc18d0ca5bd6a517b20151e | C | KEEP | — | 0 |
| 109 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_volatility_trail_1p5_no_stop.json | json | 102,805 | d1574eb015f0d8bcf916ae58a0681255c7b72f77c495ab44553c5bdfd2c39942 | C | KEEP | — | 0 |
| 110 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_volatility_trail_1p5_runtime.json | json | 240,345 | e2b5f4bdf03517a9cde1bdefb4166a7b044516ba3bfc3a837ee3e397df2bdab9 | C | KEEP | — | 0 |
| 111 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_volatility_trail_2p0_canonical.json | json | 99,438 | 15f4408d3771198fba63154b3e9b654d288bd59b5316e24d27f00cc9bca1df07 | C | KEEP | — | 0 |
| 112 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_volatility_trail_2p0_no_stop.json | json | 103,108 | 127c76b48c07f5141bfe2c50ef49195cb894ce46d70811ff09a8f4d935571dd7 | C | KEEP | — | 0 |
| 113 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/crypto_volatility_trail_2p0_runtime.json | json | 240,472 | a20373f08dbd265aeab8bcb1167ba2bc3e9a770e3fbb38e4c80bc2af39741117 | C | KEEP | — | 0 |
| 114 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_break_even_0p005_canonical.json | json | 351,870 | 7530ad8fbcdff275eddf95ad1e5647cdab9555ef083370872b63413e9b7d0eb9 | C | KEEP | — | 0 |
| 115 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_break_even_0p005_no_stop.json | json | 171,939 | eddcbb19ddc5c8bb249da4f74a6f75116e10bf05ba4f42cfbe1f4e7b306ba8bb | C | KEEP | — | 0 |
| 116 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_break_even_0p005_runtime.json | json | 169,237 | 9810cce0acfc1f17c4d4caf4f4b6ca994e5c394974a508f47db0fdb089e82e32 | C | KEEP | — | 0 |
| 117 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_break_even_0p01_canonical.json | json | 226,404 | d60ebb076680dc113e038d69de308924f66b220042d4ffbb543fdffd3da0b41b | C | KEEP | — | 0 |
| 118 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_break_even_0p01_no_stop.json | json | 122,668 | 5bb8b8cd4fecd5bdc58a0d07ef773bd617e5204c82838309d39d640bcf8eebb0 | C | KEEP | — | 0 |
| 119 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_break_even_0p01_runtime.json | json | 120,437 | 8e4134e44dc38655f7496de780d4dc4d66432e54e8737e9b9c607e22cf89f611 | C | KEEP | — | 0 |
| 120 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_break_even_0p02_no_stop.json | json | 47,295 | 5d42f0ff6bb4d48fd8afa5bd63d1d73601902a1a464e48652f4f3237a092bcc2 | C | KEEP | — | 0 |
| 121 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_break_even_0p02_runtime.json | json | 45,630 | d0c71194d7af92267fb78ea7bbcf26277088dee7f290a837df3b24770e6d3aea | C | KEEP | — | 0 |
| 122 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_canonical_strategy_control_canonical_canonical.json | json | 20,990 | d65c23348ae122dbb3c324335b31aac51c3db6b35abf7358a6b2e0837652ef00 | C | KEEP | — | 0 |
| 123 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_current_runtime_protection_control_runtime_static_runtime.json | json | 7,203,452 | b9580796dc3747eb746e6ee6783c22ed5fc83632e2248fcb9b330c3f17e6fe96 | C | KEEP | — | 0 |
| 124 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_fixed_trail_0p01_no_stop.json | json | 285,765 | cf5a95c9100f389abae3b262b4c202a7d9f1c254eec6c3955449ade39df4ccd5 | C | KEEP | — | 0 |
| 125 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_fixed_trail_0p01_runtime.json | json | 281,749 | 4c0a0ebb4ed78c0167eb71baad735842ea3e3bf337867ac0b8412a0e90f962a3 | C | KEEP | — | 0 |
| 126 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_fixed_trail_0p02_no_stop.json | json | 248,387 | 2aa6e8d221fd3d3ca0198f4441c4cd952d8055afe6b66e2902b269b73b24d1ac | C | KEEP | — | 0 |
| 127 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_fixed_trail_0p02_runtime.json | json | 244,736 | bda9e385dac09bf3194bb5f5144e820cbff8df8a69de7641b8f1ebce76a45467 | C | KEEP | — | 0 |
| 128 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_fixed_trail_0p03_no_stop.json | json | 206,876 | f79395b1de5fcf6d466108f8983e370ae1532c45e17af1d2417fbc38e6c03771 | C | KEEP | — | 0 |
| 129 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_fixed_trail_0p03_runtime.json | json | 203,635 | 0a45bc9fb5a1c19c363f6d87e35c426a51d514c5fe0e76b0a921ecacd2b4e386 | C | KEEP | — | 0 |
| 130 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_hybrid_1p0_no_stop.json | json | 283,013 | b2be0e37887061581f01d45860ceaae80f88a024188a3b81e62f0c794a2d8a09 | C | KEEP | — | 0 |
| 131 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_hybrid_1p0_runtime.json | json | 279,038 | fd6030038bba39ddfaeb8bbdf2d23c7e0f62e9beb9930ffce7d74eb08b3e9b64 | C | KEEP | — | 0 |
| 132 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_hybrid_1p5_no_stop.json | json | 283,434 | dd307d00404ed94cb9e5d90fd93d43e198f01ffa5ce18ff0994a0411d7f52f3e | C | KEEP | — | 0 |
| 133 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_hybrid_1p5_runtime.json | json | 279,464 | ca4aa913355cf3ac77f3d99a9d44b6f8011794c3b30a2da9cfd90725a5127422 | C | KEEP | — | 0 |
| 134 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_hybrid_2p0_no_stop.json | json | 266,213 | 3a1d22a903ee54024befd7dc16c3a813309de1a10bb8fbb47965e8726515c1fa | C | KEEP | — | 0 |
| 135 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_hybrid_2p0_runtime.json | json | 262,418 | c04c4f08c78af358b919cfc8e907ab500e479c8246cb2a869bde18a72fab7a7f | C | KEEP | — | 0 |
| 136 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_ladder_single_no_stop.json | json | 154,799 | 8674ef3cc371922cbcd5d863c93d5a034bf347c96c04b17b84ec8f97a7e0c1ec | C | KEEP | — | 0 |
| 137 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_ladder_single_runtime.json | json | 152,096 | 8815ba6d7f4464135a9bc05d5a8768467ac76a729d233e5c1c83cf3a7dd281cb | C | KEEP | — | 0 |
| 138 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_profit_lock_0p25_no_stop.json | json | 179,024 | 282a5e8846940230ab26bed2f2ca103203fcb4c2f5c6243317801f4349c0c3b3 | C | KEEP | — | 0 |
| 139 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_profit_lock_0p25_runtime.json | json | 176,078 | e6c650e3373f1354e04f151c737e76533a73542b1394950bb18982996763aede | C | KEEP | — | 0 |
| 140 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_profit_lock_0p5_no_stop.json | json | 223,227 | 99678ff1c31f5ab54da6100adfc876f7922f0e3d95b1cbdb192d939f72552c43 | C | KEEP | — | 0 |
| 141 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_profit_lock_0p5_runtime.json | json | 219,847 | 5d7dcdac970da7ea8bfb2275178b959423f5e119b7fc5cfdd6eccfa088e77648 | C | KEEP | — | 0 |
| 142 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_profit_lock_0p75_no_stop.json | json | 260,118 | 53d4a0f71ae1689dd7c350d681bdd30b7386b1d6e42f59bd89d181d98ca9c57d | C | KEEP | — | 0 |
| 143 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_profit_lock_0p75_runtime.json | json | 256,382 | b1a769d64c24ca45512d716677f431abc201d7f491842b6a10b3c9e121e97737 | C | KEEP | — | 0 |
| 144 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_runtime_execution_no_protection_control_runtime_no_stop_no_stop.json | json | 7,203,448 | 8e22b000b44f6af21196e4ef22143cf48ea5c576ac69b6e6bce4713849470f1a | C | KEEP | — | 0 |
| 145 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_volatility_trail_1p0_no_stop.json | json | 295,672 | e1640cd47c4a0f3abe61eb893e06412533e3278f665e6db4434d801bf02fd539 | C | KEEP | — | 0 |
| 146 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_volatility_trail_1p0_runtime.json | json | 291,607 | e3909e492b00dc66babdb0688cfb5bda9048f51f114225bf7cce60585fcf6599 | C | KEEP | — | 0 |
| 147 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_volatility_trail_1p5_no_stop.json | json | 290,561 | 7587223dde948317e213f235ed64f10ee920104fcd34e2bd2904efe29a36703c | C | KEEP | — | 0 |
| 148 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_volatility_trail_1p5_runtime.json | json | 286,561 | 0e283dcf83399bd49dabca6afd4e50fd1c6ee5613149cec163052bb27d9419a6 | C | KEEP | — | 0 |
| 149 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_volatility_trail_2p0_no_stop.json | json | 290,076 | a41594d860496b20d3c87b6d6113f3bb74ddd59b06c52c59b80ca7d55519bb91 | C | KEEP | — | 0 |
| 150 | data/research/archive/2026_08/exit_profit_protection_2026_08/generated/stateful_checkpoints/equity_volatility_trail_2p0_runtime.json | json | 286,086 | 60f1ab520926a9f8c5c3e2b88b7753629065d653875709771bc57171e90c0920 | C | KEEP | — | 0 |
| 151 | data/research/archive/2026_08/exit_profit_protection_2026_08/intrabar_sensitivity.json | json | 93,414 | 4ad619628e315e616522707e6691b20864463cd9227b45e6165bba50091487a5 | C | KEEP | — | 0 |
| 152 | data/research/archive/2026_08/exit_profit_protection_2026_08/mfe_mae_summary.json | json | 6,124 | 15e668902eeaea56c7324b1c62b34c79967753fa102ee67f865d02edab404e53 | C | KEEP | — | 0 |
| 153 | data/research/archive/2026_08/exit_profit_protection_2026_08/walk_forward.json | json | 350,438 | d388997fd17054710e57fa731c8f3debedb05a4f893dd6c0f4e7f81b4ff66b69 | C | KEEP | — | 0 |
| 154 | data/research/archive/2026_08/gates_2026_08/corporate_actions_pagination.json | json | 161 | b7b5448df11b45815a60081c00774a76bcf95164c16c1f26dfeabd0505bebfe9 | C | KEEP | — | 0 |
| 155 | data/research/archive/2026_08/gates_2026_08/cpcv_gate.json | json | 1,008 | 55770e0ccf4a5345b5c3aebb3701c8962a9dd29bd0842d33a16428b8b9c55d43 | C | KEEP | — | 0 |
| 156 | data/research/archive/2026_08/gates_2026_08/differential_report.json | json | 9,021 | 9edd83bd8fbda1dd359da1ac736708572cb70a1efe4b6a7b721dc926e659d2f8 | C | KEEP | — | 0 |
| 157 | data/research/archive/2026_08/gates_2026_08/lookahead_truncated_crypto.json | json | 585 | 002ffc084eeb9d740750b05cd8a28cd13beb9f932ae49196f182dbd47ef9e3ed | C | KEEP | — | 0 |
| 158 | data/research/archive/2026_08/gates_2026_08/lookahead_truncated_equity.json | json | 430 | c4b44bc5d060dfbce3d0637481ffa272a37dd16e95d2502170bb0638942faf6a | C | KEEP | — | 0 |
| 159 | data/research/archive/2026_08/gates_2026_08/sip_iex_divergence.json | json | 532 | 3de4e44f3004d7a38ebc7869b847a4877ac5248ceb0fcf2aaae4a8fe42e60a9f | C | KEEP | — | 0 |
| 160 | data/research/archive/2026_08/optimization_2026_08/analysis.json | json | 8,926 | 54d0f9d37028264fb7679e3f9d9cde43d453d1bf3d3bf7a5896bc53de18b53d2 | C | KEEP | — | 33 |
| 161 | data/research/archive/2026_08/optimization_2026_08/b6_shortlist500.json | json | 162,348 | 59a0379731ec1b88fe9b9db6cd24f8192ee0cbe46dda9a98292b8359262a8616 | C | KEEP | — | 0 |
| 162 | data/research/archive/2026_08/optimization_2026_08/cr_alloc005.json | json | 134,240 | cc742afa27dce20e8ebfabbc2b9ab18e92009faf937e8b23e7aafd388b049f68 | C | KEEP | — | 0 |
| 163 | data/research/archive/2026_08/optimization_2026_08/cr_band0025.json | json | 134,080 | a50dd8b7475665e05d20f384da84f99edccd61ebe9f7bdef747b4bcc0f3fe81e | C | KEEP | — | 0 |
| 164 | data/research/archive/2026_08/optimization_2026_08/cr_base_topn2.json | json | 134,250 | 888f041e70b98fc88d7193e90506c04a0955fc95c233481c25c5b3103ebcd95a | C | KEEP | — | 0 |
| 165 | data/research/archive/2026_08/optimization_2026_08/cr_topn3.json | json | 134,176 | 8ea7014a38b7f94b08793158f6694966642e5668224ecd273a34af67c550d1e3 | C | KEEP | — | 0 |
| 166 | data/research/archive/2026_08/optimization_2026_08/crx_alloc005_ADOPTED.json | json | 133,348 | b9f317c816bac33050e47e0b2f818f292fa105a9f2da67a59dd940925114ed33 | C | KEEP | — | 0 |
| 167 | data/research/archive/2026_08/optimization_2026_08/crx_base_topn2.json | json | 133,442 | 2de47f7cd538b5c9ee2f322dd316cd5be7b3b77cacda7f8b614fdd527df44f84 | C | KEEP | — | 0 |
| 168 | data/research/archive/2026_08/optimization_2026_08/crx_topn3.json | json | 133,429 | 2de84079d0b6301f07f09062ddec1557e3cbe85f1abd2d0b1d065e30a9496784 | C | KEEP | — | 0 |
| 169 | data/research/archive/2026_08/optimization_2026_08/eq_alloc050.json | json | 162,309 | c4cba8bc6b8bdff3ff5b3061efc5919a1f5e2b9120b0fb1e8f4440ae6f50e1a7 | C | KEEP | — | 0 |
| 170 | data/research/archive/2026_08/optimization_2026_08/eq_alloc060.json | json | 168,227 | 807187b3152e2d5dfcd1bf393a57949df92c4a4979de81609f5bab6716046ac0 | C | KEEP | — | 0 |
| 171 | data/research/archive/2026_08/optimization_2026_08/eq_alloc070.json | json | 168,227 | d27076fb3e4cf6f41824a43048b271b51e2d94137383eec8baf1d1c86ce9fcdb | C | KEEP | — | 0 |
| 172 | data/research/archive/2026_08/optimization_2026_08/eq_band0025.json | json | 162,358 | 0b0c9ec6af23cd8adfb9bdc19d36f63255537193199c0691529660b46a673e03 | C | KEEP | — | 0 |
| 173 | data/research/archive/2026_08/optimization_2026_08/eq_div_alloc050.json | json | 162,358 | 0387d6c300a411dcba9716816bb80f9e3387b495b16dc7654bc47f0880fafce8 | C | KEEP | — | 0 |
| 174 | data/research/archive/2026_08/optimization_2026_08/eq_div_alloc060.json | json | 168,247 | 5fecd6f68f3e2a037ac876a66101732abdd596638c36902fc40154873a5375a9 | C | KEEP | — | 6 |
| 175 | data/research/archive/2026_08/optimization_2026_08/eq_intr_dd075.json | json | 127,365 | 7156e5e92d6adbe4d64ef5d3cc2763179dcb9d2237a74c85cecf459f2b956cb2 | C | KEEP | — | 0 |
| 176 | data/research/archive/2026_08/optimization_2026_08/eq_intr_dd10.json | json | 127,174 | 8d408961bece9c416b0fac1494a256ef3325aab1cdb31479b92daa89a96b58d3 | C | KEEP | — | 0 |
| 177 | data/research/archive/2026_08/optimization_2026_08/eq_topn9.json | json | 162,565 | 8210ae075317688db3b8e996b69dbdb3e76372a6d47b71cfa840cf4ca8c0ac45 | C | KEEP | — | 0 |
| 178 | data/research/archive/2026_08/optimization_2026_08/fetch_data.py | python | 8,273 | 893214ce715f6fefffa37279ac046c761c062a815ee7bfbd29a91bfbf277f57b | C | KEEP | — | 2 |
| 179 | data/research/archive/2026_08/optimization_2026_08/run_experiments.py | python | 21,022 | 5f1d0026ca4cd7503f15dfe52fd998d56e64148e49e9838460132bdb53ff5775 | C | KEEP | — | 2 |
| 180 | data/research/archive/2026_08/optimization_2026_08/run_experiments2.py | python | 14,558 | 4e24e9de629d33623f17d6d76f69d35a0a002a7307e784a771af8480864999a5 | C | KEEP | — | 1 |
| 181 | data/research/archive/2026_08/optimization_2026_08/smoke_eq_alloc050.json | json | 9,836 | 39bbbd6a060d40fd7375c1817ab3c3b29e92d246a44b4dd2bfb89757ac0974c7 | C | KEEP | — | 0 |
| 182 | data/research/archive/2026_08/optimization_2026_08/smoke_eq_alloc060.json | json | 10,182 | a34584683eff1751b2805e8f4306c0456458e136e77581f3517f8466423dcd38 | C | KEEP | — | 0 |
| 183 | data/research/archive/2026_08/optimization_2026_08/smoke_eq_alloc070.json | json | 10,182 | 196209341fe0e1518e0ca9be79842159e64044d66a22a3d17f157d0a347e034d | C | KEEP | — | 0 |
| 184 | data/research/archive/2026_08/optimization_2026_08/walkforward.json | json | 11,603 | 895fcea349886938d851f1f2210ad45b99be8f976860f20cf41a5339fc6eb6fb | C | KEEP | — | 0 |
| 185 | data/research/archive/2026_08/optimization_2026_08/wf_eq_topn9.json | json | 3,159 | 80059743d90cd2f7867247458700a3eed0c620dbc25f4faef022629e3557921b | C | KEEP | — | 1 |
| 186 | data/research/archive/2026_08/portfolio_construction_2026_08/results.json | json | 4,280 | e1447c8fd56f08c6a453b6c6b12a732a4edac399a1b394ff2267410aa2d82fd1 | C | KEEP | — | 2 |
| 187 | data/research/archive/2026_08/regime_execution_2026_08/frontier_synthetic.json | json | 2,348 | c4dcf0d92119af47c975a1918dd1291a8b45cd64e1f2cbd1cfe5f491b3735cf9 | G | DELETE | — | 1 |
| 188 | data/research/archive/2026_08/regime_execution_2026_08/lightweight_tests.py | python | 9,954 | e636cf907d1cc6b712f973bebd7a2e48660938abc34108a70d9dde9667118d98 | C | KEEP | — | 1 |
| 189 | data/research/archive/2026_08/regime_execution_2026_08/paper_ab_spec.json | json | 4,323 | 184704f860fa748180345db4233b2ea506a7b9792af8feefd6ffd06d2140c426 | C | KEEP | — | 1 |
| 190 | data/research/archive/2026_08/regime_execution_2026_08/results.json | json | 3,158 | 92ea161b4e0e53708025c894380b58c6daef8c3bdba0274c0089ea25f3f2b6e7 | C | KEEP | — | 2 |
| 191 | data/research/archive/2026_08/risk_epoch_2026_08/results.json | json | 3,703 | ff04dd0bdc18936f945496f24ae8f20cfd9a4479557b8e4fc09358c4f97d048f | C | KEEP | — | 0 |
| 192 | data/research/archive/2026_08/risk_epoch_2026_08/run_regimes.py | python | 1,554 | 805370e700e35f3de9b0c184c6a69681140f56bf05851455643440cbb276452b | C | KEEP | — | 0 |
| 193 | data/research/archive/2026_08/risk_epoch_2026_08/run_risk_epoch.py | python | 12,847 | 72030bd44675363baa35d0ce57fd02decd8941f00e5fe42bc7246e8623cf8684 | C | KEEP | — | 0 |
| 194 | data/research/archive/2026_08/risk_epoch_2026_08/run_walkforward.py | python | 2,132 | a5797373d9e1062401bb7918025e34764b118806d91f2161f16f3b923ac60081 | C | KEEP | — | 0 |
| 195 | data/research/archive/2026_08/survivorship_aware_2026_08/alpaca_asset_master_summary.json | json | 3,543 | 3a4d6efea8a914e329f2e7f0767bfc486efaf09cd931392433b31c44bc6cd00b | C | KEEP | — | 2 |
| 196 | data/research/archive/2026_08/survivorship_aware_2026_08/current_dataset_audit.json | json | 279,662 | e69d860bc1619a824774ebd4686f32097f19f7039fdc701cfb7ac8c57b53c77c | C | KEEP | — | 4 |
| 197 | data/research/archive/2026_08/survivorship_aware_2026_08/entity_lifetimes.json | json | 6,185 | fa18f337bf610fa24e8dc68f73ce02662c533c25b35fd7aa32502be0ecd7cdfc | C | KEEP | — | 2 |
| 198 | data/research/archive/2026_08/survivorship_aware_2026_08/final_verdict.json | json | 5,217 | c4d912a9f0343a9ea9ed1fdc16567ef8283e05e9a309f3f39b1a9d850ecdf553 | C | KEEP | — | 0 |
| 199 | data/research/archive/2026_08/survivorship_aware_2026_08/historical_bar_coverage.json | json | 612 | 14b75115c14f27331b8ba06c4b89cd1192ef79ca64bb7df65a47db175df699c4 | C | KEEP | — | 4 |
| 200 | data/research/archive/2026_08/survivorship_aware_2026_08/historical_tradeability.json | json | 1,691 | 5174ad6173ae6a1af2b27c0b1b0520f9946da398224b4a8fd5034f4b1da25fe3 | C | KEEP | — | 2 |
| 201 | data/research/archive/2026_08/survivorship_aware_2026_08/inactive_asset_probe.json | json | 2,582 | d83471992e0a6f417d32ae7a2d4f8d775597c117746c29b8c1ce162ee46525ec | C | KEEP | — | 3 |
| 202 | data/research/archive/2026_08/survivorship_aware_2026_08/old_vs_new_control.json | json | 30,582 | 5a9dd944916363b722b4f3d4db5611a36f7ea1af5493faddc31b98cada6ed188 | C | KEEP | — | 4 |
| 203 | data/research/archive/2026_08/survivorship_aware_2026_08/old_vs_new_walk_forward.json | json | 7,317 | 0436232fc081cbda1959b6425ab61dd653cc8fe151fbd7aa6cb6fc34337df652 | C | KEEP | — | 6 |
| 204 | data/research/archive/2026_08/survivorship_aware_2026_08/pit_quality_report.json | json | 3,334 | 819fcd25d986246acef5cc4b05d516bf85bb598ffc95e0142c03615b1476eeeb | C | KEEP | — | 3 |
| 205 | data/research/archive/2026_08/survivorship_aware_2026_08/rename_validation.json | json | 366 | d809e81d1013a8c8596fd9f6ad9ecf660b0c7b48db28f45699b617b5ef6d4f4c | C | KEEP | — | 4 |
| 206 | data/research/archive/2026_08/survivorship_aware_2026_08/survivor_cohort_contribution.json | json | 1,960 | 2aac218cb08fc3377df64aa3793350cb7b4dbe7989daa9b2a0653cb0fbc2869c | C | KEEP | — | 3 |
| 207 | data/research/archive/2026_08/survivorship_aware_2026_08/terminal_position_behavior.json | json | 3,291 | 99a56bf56810b9c508b9b97d1ecad73367f4826592ba2a7a810aa1d1452b6210 | C | KEEP | — | 4 |
| 208 | data/research/archive/2026_08/survivorship_aware_2026_08/termination_validation.json | json | 1,112 | 990d4f85c134855b4bd95330e2f08520f140e27f86d6ef8a007d21ae6102fbf4 | C | KEEP | — | 3 |
| 209 | data/research/archive/2026_08/survivorship_aware_2026_08/ticker_identity_map.json | json | 3,131 | 25695e93b165e8b1bb5585692659f15181f0d3194328a667701576f9640ed634 | C | KEEP | — | 3 |
| 210 | data/research/archive/2026_08/survivorship_aware_2026_08/yearly_universe_counts.json | json | 2,535 | 9a514c7e18e92546e635bf2f053a832bb1f02b899c49e469a25148b66a7a3759 | C | KEEP | — | 3 |
| 211 | data/research/archive/2026_08/topn9_2026_08/results.json | json | 45,961 | 3da6e1b134343c7a37239f12deb8b843b39ebd14942b10b8c67132926bdb4fd0 | C | KEEP | — | 0 |
| 212 | data/research/archive/2026_08/topn9_2026_08/run_quick.py | python | 18,954 | 32b716b574b84f96858a95c74ecd20357d763ae163e3372d98b789b084ffab18 | C | KEEP | — | 0 |
| 213 | data/research/archive/2026_08/topn9_2026_08/run_topn9.py | python | 31,308 | 0dc2788edf25a556b16ab2bef0ccd8a93f85d6b7fc36c279e853512e1e5b7969 | C | KEEP | — | 4 |
| 214 | data/research/archive/2026_08/v3_exit_latency_2026_08/E0.json | json | 172,549 | c4cf74ba73c2ea427bec99f5efee013a74b2667ac300614d3125bde1fc746265 | C | KEEP | — | 0 |
| 215 | data/research/archive/2026_08/v3_exit_latency_2026_08/E1.json | json | 172,954 | 307abb185d1e74e1255d3928aaac88646954ca0038272bde8ac80dc37d3e857e | C | KEEP | — | 0 |
| 216 | data/research/archive/2026_08/v3_exit_latency_2026_08/E2.json | json | 172,851 | 2937f68386aacf0c2b7325d421ca646539de8c944ec18aeee43ac41ebd0c78ab | C | KEEP | — | 0 |
| 217 | data/research/archive/2026_08/v3_exit_latency_2026_08/E3.json | json | 172,794 | 952e02c50c6cb6fa6a1ab1f5fe135a42c78c570e58a903da80a385c081f614e7 | C | KEEP | — | 0 |
| 218 | data/research/archive/2026_08/v3_exit_latency_2026_08/annual_results.json | json | 5,929 | f4290f82cebad1e2515b23b709870c4516eff50126af1aaf15bee060136292af | C | KEEP | — | 0 |
| 219 | data/research/archive/2026_08/v3_exit_latency_2026_08/axon_sanity.json | json | 9,631 | c8c96be2eaa48f0ba99f3584851a195a126e6001498a8a11d853ae9d1a7b4bba | C | KEEP | — | 4 |
| 220 | data/research/archive/2026_08/v3_exit_latency_2026_08/bootstrap.json | json | 677 | 6b3bc952ee3ec26972855707f4429f222fb512a533acfa8233c23e0d2a2bfa51 | C | KEEP | — | 0 |
| 221 | data/research/archive/2026_08/v3_exit_latency_2026_08/cost_stress.json | json | 3,669 | 9a2936a5090e112fbdc9e4743706aacb6e94d1f517dede40efa0ad3c81c1b56a | C | KEEP | — | 0 |
| 222 | data/research/archive/2026_08/v3_exit_latency_2026_08/jackknife.json | json | 4,209 | fcefe2421bf9ae64572c94d0a9fa0df6648129aea35cbc5d347b90c24b7dafbb | C | KEEP | — | 0 |
| 223 | data/research/archive/2026_08/v3_exit_latency_2026_08/position_diagnostics.json | json | 2,604,163 | 0df2550f905f34c77751dc23639f13d256a8430c8eee2fbf3913b238862e84b3 | C | KEEP | — | 7 |
| 224 | data/research/archive/2026_08/v3_exit_latency_2026_08/temporal_windows.json | json | 3,151 | b2997abca0604b8e004552177a0f5ceef7b91d3e7a51c90f42cff697d1df53aa | C | KEEP | — | 0 |
| 225 | data/research/archive/2026_08/v3_exit_latency_2026_08/walk_forward.json | json | 49,427 | 6dd3d6af56f132ea724146a3c0e4290f13eef71c5fc04e00d7c3076a716e124d | C | KEEP | — | 0 |
| 226 | data/research/archive/2026_08/v3_position_risk_2026_08/annual_results.json | json | 5,958 | 6253862b7e769925cadd7956527211f958542c8dca544f6e0ba95e10b5f4ffc0 | C | KEEP | — | 0 |
| 227 | data/research/archive/2026_08/v3_position_risk_2026_08/axon_counterfactual.json | json | 22,344 | b946e9afbeed17fbd11c6433d6429e3742d2a52ad1d568cabaa598953300a537 | C | KEEP | — | 4 |
| 228 | data/research/archive/2026_08/v3_position_risk_2026_08/baseline_diagnostics.json | json | 178,216 | 9f069b07a2be5a5ebc22b63e7084f8d4d5880578b7536d6a8f551f1143c2d40f | C | KEEP | — | 3 |
| 229 | data/research/archive/2026_08/v3_position_risk_2026_08/bootstrap.json | json | 799 | eb82f02214b93f5df15e3b9c1e29330d0063119e9b10e9355db7c38e5005c7d4 | C | KEEP | — | 0 |
| 230 | data/research/archive/2026_08/v3_position_risk_2026_08/candidates.json | json | 747,805 | c590b315efc88ec05d4429291dd5ce0fac092baac25c8dac08fdb630cbec65cf | C | KEEP | — | 30 |
| 231 | data/research/archive/2026_08/v3_position_risk_2026_08/concentration_metrics.json | json | 3,620 | 62aebdbd4cfed9cdf6245fd1832f3e3db384ff5d8d253f998f4d582a741a0ba6 | C | KEEP | — | 3 |
| 232 | data/research/archive/2026_08/v3_position_risk_2026_08/cost_stress.json | json | 3,649 | b8f43152d0c2b1fb99494f81f1b483eb6d6562ee581f7f1a2fc93f9c3c3fbb13 | C | KEEP | — | 0 |
| 233 | data/research/archive/2026_08/v3_position_risk_2026_08/ex_ante_risk_diagnostics.json | json | 465,867 | 77e6e9990e8077f07a034f32f46539dafa62ed4adcc4861668fabb10d30de705 | C | KEEP | — | 4 |
| 234 | data/research/archive/2026_08/v3_position_risk_2026_08/jackknife.json | json | 3,533 | e54d1805d8ff5e38c959591ffac28a4aac79171c56509096678c2534960e2f50 | C | KEEP | — | 0 |
| 235 | data/research/archive/2026_08/v3_position_risk_2026_08/position_tail_metrics.json | json | 3,744 | e060ebc1c31fb00da3eb5f0df3677318b9dc665b1d841b9905bf4c0702396e6a | C | KEEP | — | 5 |
| 236 | data/research/archive/2026_08/v3_position_risk_2026_08/promotion_verdict.json | json | 5,148 | 8d6771d53804e9a0ce9940ddead3bb219d5f50ae17029c6816ffceb35b99a7ce | C | KEEP | — | 0 |
| 237 | data/research/archive/2026_08/v3_position_risk_2026_08/temporal_windows.json | json | 3,188 | 69e1f386dccae93cdbf1f86200f3cd3d219a9513d345772f6cf09f7ba6007b6f | C | KEEP | — | 0 |
| 238 | data/research/archive/2026_08/v3_position_risk_2026_08/walk_forward.json | json | 49,409 | c05ee2ed2687a223d6fe855a52ec8b95c7f78ceba49d84d58972286f1804da8b | C | KEEP | — | 0 |
| 239 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/account_policy_candidates.json | json | 9,660 | edf793baaa13426577134ca119b71f40b453bb28a25075c179e8a7ddb46b43b8 | C | KEEP | — | 5 |
| 240 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/account_policy_cost_stress.json | json | 4,072 | 2056f8c47b7d9d0b07944a49a23c63dc9c9bd15aa7859ee4f4a900f7e4a2fbbc | C | KEEP | — | 4 |
| 241 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/account_policy_folds.json | json | 22,603 | 4620c366c56bfa95e59357859d143909d52102f9723b9ff18784fcad6d397eb7 | C | KEEP | — | 4 |
| 242 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/baseline_position_diagnostics.json | json | 52,776 | 12bebeac71a4a4a71840ec47ae76248d7c271a9e958ca3fbc125086091e14d51 | C | KEEP | — | 6 |
| 243 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/interaction_test.json | json | 704 | 195ac580e376f02d33a8296788e4f74adadec0e98f5478ca3b013b51d81a1676 | C | KEEP | — | 6 |
| 244 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/loser_recovery_matrix.json | json | 3,015 | af75bada06e9337bd6e4d8e15af834aaa717a500753014b2477b0e32588d66a9 | C | KEEP | — | 3 |
| 245 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/promotion_verdict.json | json | 4,311 | 3d32a1e1afe2d3ef42ea6ab14b7ca2633d2f6b85ba8fa475cc5e016eac78c936 | C | KEEP | — | 0 |
| 246 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/stop_bootstrap.json | json | 1,113 | 0ef06100d880a46fc908f52ac28f39b2e7af61035e714aa2fb66976034a84478 | C | KEEP | — | 4 |
| 247 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/stop_candidates.json | json | 11,013 | e03b6896096aa6106951ed84ddd6de8f917e68926e7520007e8b4e02f47ecea7 | C | KEEP | — | 6 |
| 248 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/stop_cost_stress.json | json | 4,068 | 3d2d94f4e084329cc3331734f864c8f68360d44489f6b3d4ba43b13eff8c44d7 | C | KEEP | — | 4 |
| 249 | data/research/archive/2026_08/v3_risk_tail_loss_2026_08/stop_fold_deltas.json | json | 61,400 | 89cb4adf18d95b8b43f9a5bf014391579643efbb637e4da551e609de1ff0480f | C | KEEP | — | 4 |
| 250 | data/research/crypto_candidate_tournament_2026_08/concentration.json | json | 16,451 | 8dde81a69dbf7ff3fb8823fb6daf6998d9ae456c574693499901497167882a13 | C | KEEP | — | 14 |
| 251 | data/research/crypto_candidate_tournament_2026_08/control_parity.json | json | 1,982 | d8949df8e86fd694bc2b3eecc6bf05506b1447828a4f5038a130dd685aacf884 | C | KEEP | — | 2 |
| 252 | data/research/crypto_candidate_tournament_2026_08/cost_stress.json | json | 6,993 | 1227a8ef59aef3f0170629cd19edf6635672e1deae46649a7015609ab8f6ebc1 | B | KEEP | — | 8 |
| 253 | data/research/crypto_candidate_tournament_2026_08/fee_savings.json | json | 2,029 | b4d2527375e7caa61a9bcbe4dae9797e98ed9057fbde273208e9b48ca909092e | C | KEEP | — | 4 |
| 254 | data/research/crypto_candidate_tournament_2026_08/final_verdict.json | json | 7,282 | 9a415725cbeb18c62148ca5c611a627ee500af5f7102426fd8c151da90820fb1 | C | KEEP | — | 2 |
| 255 | data/research/crypto_candidate_tournament_2026_08/fold_dependence.json | json | 4,139 | efd384db37a5beb1c7c1e4e4a0ed8487446f11c9582b223d483a14cc6d22d6d7 | C | KEEP | — | 5 |
| 256 | data/research/crypto_candidate_tournament_2026_08/full_window.json | json | 26,678 | 0cb211a62577387622d1848eb1c1f398bac3892c71532c33f570b882829a4bb6 | C | KEEP | — | 8 |
| 257 | data/research/crypto_candidate_tournament_2026_08/full_window_results.json | json | 26,678 | 0cb211a62577387622d1848eb1c1f398bac3892c71532c33f570b882829a4bb6 | E | DELETE | data/research/crypto_candidate_tournament_2026_08/full_window.json | 11 |
| 258 | data/research/crypto_candidate_tournament_2026_08/gate_matrix.json | json | 3,299 | 9493a14a3cfee649990201a3bed01a075dfcec8c550471d6da1626bfea00c7c5 | C | KEEP | — | 3 |
| 259 | data/research/crypto_candidate_tournament_2026_08/jackknife.json | json | 8,040 | a775b00cc7804356f152a398977ff44ce943976aee72d852d8312a904fbca5f1 | C | KEEP | — | 0 |
| 260 | data/research/crypto_candidate_tournament_2026_08/pareto.json | json | 3,305 | 5dae2f697e1b86383b8aeb3167f5723493d4a8e1c4a4a85d6db1d49a9a2ff48f | C | KEEP | — | 0 |
| 261 | data/research/crypto_candidate_tournament_2026_08/pnl_concentration.json | json | 16,455 | 14a4dd9e21221534696403863428b26865558baad8e4830fd3bc242915cd9765 | C | KEEP | — | 0 |
| 262 | data/research/crypto_candidate_tournament_2026_08/regime.json | json | 5,510 | b447d782b634820d87fd1e8430c00ed65e3b44b3826c559abf6599f0c3050215 | J | KEEP | — | 0 |
| 263 | data/research/crypto_candidate_tournament_2026_08/regime_analysis.json | json | 5,510 | b447d782b634820d87fd1e8430c00ed65e3b44b3826c559abf6599f0c3050215 | J | KEEP | — | 0 |
| 264 | data/research/crypto_candidate_tournament_2026_08/tournament_plan.json | json | 13,694 | 52b4e88a6c46d8f8d53d3064201826da126f5ca9397c1cb6458424a2eea8beda | C | KEEP | — | 2 |
| 265 | data/research/crypto_candidate_tournament_2026_08/turnover.json | json | 5,459 | 2e3b4ff2d647d63542f85aeca9b14e188714d371f9f4c826b11c8c9d82b7a4d8 | J | KEEP | — | 3 |
| 266 | data/research/crypto_candidate_tournament_2026_08/turnover_analysis.json | json | 5,459 | 2e3b4ff2d647d63542f85aeca9b14e188714d371f9f4c826b11c8c9d82b7a4d8 | J | KEEP | — | 0 |
| 267 | data/research/crypto_candidate_tournament_2026_08/universe_identity.json | json | 767 | 28e56f23836b4851eba39a4318d639aa3e85947915cb93a419934723cf2faff7 | C | KEEP | — | 3 |
| 268 | data/research/crypto_candidate_tournament_2026_08/walk_forward.json | json | 38,009 | 8fbd6314b9e1a030700262f053113b88631cb70801218e8b9a792aa1169e156d | C | KEEP | — | 0 |
| 269 | data/research/crypto_candidate_tournament_2026_08/weight_turnover.json | json | 1,482 | c5e1b2ecd702cef9e82ea1e9ae1e75c7db40135d4d61ca048f0e9f78893b2250 | C | KEEP | — | 4 |
| 270 | data/research/crypto_foundation_2026_08/crypto_universe_parity_audit.json | json | 10,945 | 21847cebe01a8e88436a34cad6d2e86d55502a87568ceaea052757533d3481f1 | C | KEEP | — | 9 |
| 271 | data/research/crypto_residual_low_variance_2026_08/beta_diagnostics.json | json | 5,417 | a29ee7999b67279913dd3e24e9aef6c2a0009ee165daa694908584df7ca45e44 | C | KEEP | — | 6 |
| 272 | data/research/crypto_residual_low_variance_2026_08/control_parity.json | json | 1,950 | 39ff527240bd9104a74e96454097ea1d2f1f4ddb2bcddc72d0b7fb4ca1977251 | C | KEEP | — | 4 |
| 273 | data/research/crypto_residual_low_variance_2026_08/cost_stress.json | json | 4,089 | 26b2189a1bae403554dd5e850c75a45f1b576e6a28749d506f2e5418fe7fa111 | C | KEEP | — | 0 |
| 274 | data/research/crypto_residual_low_variance_2026_08/final_verdict.json | json | 1,887 | fad2fbd061a83e381520c3d31fba25e285412d1ce0d446b2587f44c99b3615c7 | C | KEEP | — | 0 |
| 275 | data/research/crypto_residual_low_variance_2026_08/full_window_results.json | json | 16,283 | 9f6dd96e63060a9ed0f845475538a14337457d637ca762ad44ea2048cc78dec0 | C | KEEP | — | 0 |
| 276 | data/research/crypto_residual_low_variance_2026_08/jackknife.json | json | 4,330 | 9057a459211e723e0296abd5c81b4e8fad777ea8f535795f43b656fca1b0581b | C | KEEP | — | 0 |
| 277 | data/research/crypto_residual_low_variance_2026_08/pnl_concentration.json | json | 9,461 | 26b87460647b332ca8b7b186e88ee19c42075a7896ec8ab44d791ad6c4b89357 | C | KEEP | — | 0 |
| 278 | data/research/crypto_residual_low_variance_2026_08/regime_analysis.json | json | 3,136 | bbef0987b4521fc8c6ab0bc6afd180ad904db2234b891d0135f1a679f92b12d3 | C | KEEP | — | 0 |
| 279 | data/research/crypto_residual_low_variance_2026_08/research_plan.json | json | 17,431 | 27a79bf673e649ca47bc22f961df9bc0f303d2379abd5161ca86c3cc0c6bcbc4 | C | KEEP | — | 2 |
| 280 | data/research/crypto_residual_low_variance_2026_08/turnover_analysis.json | json | 2,642 | 72e8e107a5ddbc5866e364b979a7913d8eaa28fbb17fd5cf0f5a19772a9a7d14 | C | KEEP | — | 0 |
| 281 | data/research/crypto_residual_low_variance_2026_08/walk_forward.json | json | 23,506 | be4992b583910339f823f4f52257a543614afcf7f5a6ba03ba2130f33688082a | C | KEEP | — | 0 |
| 282 | data/research/crypto_t1_promotion_2026_08/parity_audit.json | json | 14,963 | 1a558372f3039b60ed5d04ea74c08149f1d57134324ff83abafb7f16385a1a51 | C | KEEP | — | 2 |
| 283 | data/research/crypto_t1_promotion_2026_08/promotion_receipt.json | json | 3,283 | 988fb8bcd2e1efcf5203b32db85012a62c4452de280f5be4c500e43abffdb0cd | C | KEEP | — | 1 |
| 284 | data/research/equity_profit_protection_2026_09_r2/E0_CONTROL.json | json | 1,609,841 | fe709d5f1bb19000a69c348e612f55421375532d2df0d9eb6f2ab4cf5de0e065 | B | KEEP | — | 15 |
| 285 | data/research/equity_profit_protection_2026_09_r2/E1_TP5_CLOSE.json | json | 2,866,197 | 0ca5bc65e5e94a264541603f72e26eda692d0ef042064c2ac50519d13546c25a | B | KEEP | — | 15 |
| 286 | data/research/equity_profit_protection_2026_09_r2/E2_PROFIT_LOCK_50.json | json | 2,598,320 | 77e1347d3fc4d839e647ee9be676fe1c73507256fa06607ce0594f056b14b63d | B | KEEP | — | 15 |
| 287 | data/research/equity_profit_protection_2026_09_r2/E3_PROFIT_ALPHA_CONFIRMED.json | json | 2,448,942 | 27e099d6db94c204cdaab107134d9011818bb8ee4b431071e998f780e248e021 | B | KEEP | — | 15 |
| 288 | data/research/equity_profit_protection_2026_09_r2/artifact_hashes.json | json | 502 | 1cb63dbfc084c3f2796f549eef8d5d7a1f9742c79187d06bdd0d53a4a6b47b6f | B | KEEP | — | 13 |
| 289 | data/research/equity_profit_protection_2026_09_r2/stage_one.json | json | 10,347,916 | 58f12fc34e3de5766c93c5bd430afd90f3c376b9d88e3a8c73bec13db798df4a | B | KEEP | — | 15 |
| 290 | data/research/full_survivorship_2026_08/active_symmetry_audit_v4.json | json | 3,856 | 63465a28e253ac415150868a77ba8a35ed3bf91bf4f74039f1f417f5cf9c8ebd | B | KEEP | — | 4 |
| 291 | data/research/full_survivorship_2026_08/active_symmetry_audit_v5.json | json | 622 | 7c6d245978fdd4eeb6a6396958bee6949031533eb006d8a43838ccc0ee5fda40 | B | KEEP | — | 4 |
| 292 | data/research/full_survivorship_2026_08/corporate_actions_audit_v4.json | json | 2,380 | 1eaccbccec07f5a588d5425f26694a8c65d2e758b149b9b355b0727822df45e8 | B | KEEP | — | 8 |
| 293 | data/research/full_survivorship_2026_08/corporate_actions_audit_v5.json | json | 256 | 016084b16ec97c65873574ca4c2d344d42b05ac529ffb1cb81797b8ea49a5f23 | B | KEEP | — | 2 |
| 294 | data/research/full_survivorship_2026_08/corrected_v3_results_H_fixed.json | json | 3,588 | d0e27119cae998ab04199fd03c7962758ac7556115614e863b1b06ca064902e2 | B | KEEP | — | 1 |
| 295 | data/research/full_survivorship_2026_08/final_verdict_v5.json | json | 1,202 | e2da603fd691480761bad974f9e4e9bda032faee8d5629a013c17df0a4b9d701 | B | KEEP | — | 54 |
| 296 | data/research/full_survivorship_2026_08/full_v3_results_v5.json | json | 6,039 | 63920c36224e9a96f5184e66182297fc0ef5ff4f10e795d8144fd8339145f936 | B | KEEP | — | 6 |
| 297 | data/research/full_survivorship_2026_08/old_vs_full_walk_forward_real_v5.json | json | 11,175 | fc8b778abc4bf9271bf232306e6344f9f1966988b748368d13f0729a20d4be88 | B | KEEP | — | 52 |
| 298 | data/research/full_survivorship_2026_08/parity_canary.json | json | 7,701 | 051b01bb5e2b4c1fad91ffc037c4ebcf2fe55324edc0135b16e72886cdf5b828 | B | KEEP | — | 56 |
| 299 | data/research/full_survivorship_2026_08/parity_manifest.json | json | 844 | 78de06baad2384b53d6027ce8ca17bcc4f46c6f848c380b748bffcc1f83dc7b7 | B | KEEP | — | 50 |
| 300 | data/research/full_survivorship_2026_08/selection_overlap_real.json | json | 2,301 | 4b2fdb4ca80ea4149eab85df3125813f5615c6d9ecde43987009ce43b49623ca | B | KEEP | — | 6 |
| 301 | data/research/full_survivorship_2026_08/symmetric_v5_coverage_report.json | json | 15,101 | 4b34f4e3e58a569c312851aa1f46550ad3652e356971cddbefa1efebde4c5026 | B | KEEP | — | 4 |
| 302 | data/research/full_survivorship_2026_08/terminal_event_accounting_audit_v5.json | json | 8,132 | 484ba71877b7432568894ad0fdf513664f2550788909cae407d3f8c07642939a | B | KEEP | — | 2 |
| 303 | data/research/full_survivorship_2026_08/terminal_event_classification_v4.json | json | 9,544 | 5408aaa990836a54931bcc3ac0eeeb81d91727df4477eacdea54946d6d4079aa | B | KEEP | — | 4 |
| 304 | data/research/full_survivorship_2026_08/terminal_sensitivity_v5.json | json | 632 | 24c756c7a8bcf31e26bd6f43d27822ddf473251e26af06404f2a1680ec7426ca | B | KEEP | — | 2 |
| 305 | data/research/full_survivorship_2026_08/ticker_identity_conflicts_v4.json | json | 10,403 | 963dd120cdebaaa8c985ccc413ba1deb394c6fff3ad75ed4602df888ecddfb20 | B | KEEP | — | 4 |
| 306 | data/research/full_survivorship_2026_08/ticker_identity_conflicts_v5.json | json | 65,171 | 47dcf84c3b435c183bf6a8ee7a5f89c3244563e8e9d0cb34f5e644f7c188b15b | B | KEEP | — | 2 |
| 307 | data/research/full_survivorship_2026_08/universe_audit_v5.json | json | 127,270 | 8433ca5f717f6b737d218d1e0d169b6ad1b70286a2061ece060dfc6e362a1cd6 | B | KEEP | — | 3 |
| 308 | data/research_manifests/crossasset_max_testing_protocol_2026_09_09_v3.json | json | 4,788 | 4ef578719b346ce38a2d02477fd63e545d7784d439f563b7c2038e3f0f2a836e | B | KEEP | — | 0 |
| 309 | data/research_manifests/crossasset_preflight_2026_09_08.json | json | 7,468 | f339a8cce0036447e48186814b6d702756b765e629b02e75211aa41c61fd1431 | B | KEEP | — | 21 |
| 310 | data/research_manifests/crossasset_protocol_amendment_2026_09_09_v2.json | json | 3,331 | efba5ac90d043d3c9fb7773c3c5246845316c27e6e1ea974f5a1ad8fafae7d0a | B | KEEP | — | 2 |
| 311 | data/research_manifests/crossasset_public_acquisition_2026_09_07.json | json | 19,857 | f448f5a5489761f748efaa5de8830fd28349e35fcc2b87eb4c722421588aa71a | B | KEEP | — | 2 |
| 312 | data/research_manifests/crossasset_x01_yahoo_fallback_2026_09_08.json | json | 1,713 | 52a1c57652dfd6147b4a2eeaeddfad02498aa99e68b9152fa43cb93e0fdec956 | B | KEEP | — | 13 |
| 313 | data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json | json | 54,199 | 426f286b174a3beff355b54846a3f4af37c8c2cdcd26e5d333375669bec2a87f | B | KEEP | — | 18 |
| 314 | data/research_manifests/crypto_low_max_return_novelty_2026_09.json | json | 6,178 | 1e446082eb3945878c72360357604b51148b6f50a45f2ea762707fb55940380d | B | KEEP | — | 9 |
| 315 | data/research_manifests/crypto_low_max_return_novelty_2026_09_draft_pre_freeze.json | json | 5,843 | 4a551d3fceb560441e99aa2aed7b14f67e27c2495e70440dd9a8b6c0578e7603 | B | KEEP | — | 9 |
| 316 | data/research_manifests/crypto_low_max_return_novelty_2026_09_n2_trace_draft.json | json | 6,973 | 53aa9ad20f7fd5432fe8c76b329b9d8aac2a9c7f90f3476c77a577806543c234 | B | KEEP | — | 12 |
| 317 | data/research_manifests/crypto_low_max_return_novelty_2026_09_n2_trace_draft_pre_final_pin.json | json | 6,973 | 53aa9ad20f7fd5432fe8c76b329b9d8aac2a9c7f90f3476c77a577806543c234 | B | KEEP | — | 12 |
| 318 | data/research_manifests/crypto_low_max_return_novelty_2026_09_n2_trace_frozen.json | json | 8,001 | d6965967d61052f395c309ab8938e5c50eba2fb243a3817117c5267a510f9207 | B | KEEP | — | 21 |
| 319 | data/research_manifests/crypto_max_economic_manifest_2026_09_05_frozen.json | json | 8,338 | da4ca60d1d1029f362e91c17a189b5bd58b49f1bb4c3e8c16505c66fe87ab3a7 | B | KEEP | — | 2 |
| 320 | data/research_manifests/crypto_max_economic_manifest_template_2026_09_05.json | json | 4,497 | 1466406f00342a69e7d9b5c5bf920c6cd8b7cf12ba7a30486e85d6e235503e8a | B | KEEP | — | 0 |
| 321 | data/research_manifests/crypto_queue_causal_adapters_2026_09_08.json | json | 12,043 | 159b8fc401e078aa9cf3bcc4895a6665094476414ee6e213b6782b6456a1d05a | B | KEEP | — | 8 |
| 322 | data/research_manifests/crypto_secondary_causal_gate_2026_09_08.json | json | 73,006 | cf8d4722daabb8184e9ef621c1a166238d2bf4d6b4837ea8afa6493036328e0d | B | KEEP | — | 8 |
| 323 | data/research_manifests/crypto_signed_risk_economic_2026_09_draft.json | json | 5,657 | 6f1b41d2fa50c36bf9b5b3f060e77b87811f8ebea2f38059a868bfbcb13f6cc8 | B | KEEP | — | 0 |
| 324 | data/research_manifests/crypto_signed_risk_novelty_2026_09_draft.json | json | 5,457 | 814bb3def4122f301d1f5d5b3aa87aaeeda223389e43be17110bae875e2a8f9f | B | KEEP | — | 0 |
| 325 | data/research_manifests/crypto_signed_risk_novelty_2026_09_frozen.json | json | 6,001 | 5c8c39b962ed47f3d6a1dc94a56d21aed542d07a4930b3f60550b67318399811 | B | KEEP | — | 0 |
| 326 | data/research_manifests/crypto_signed_risk_novelty_2026_09_r2_schema_corrected_draft.json | json | 5,980 | 0fdb45f90e6e239e935d8b6e65069e0e438af85aed95b24d02fa7a9f0ab204e8 | B | KEEP | — | 0 |
| 327 | data/research_manifests/equity_alpha_optimization_2026_08.json | json | 8,965 | 6b1cea424c56e6da1f9abc16246df77c58e650d9ed9b5b9bba1045bbe6456a1e | B | KEEP | — | 10 |
| 328 | data/research_manifests/equity_candidate_observation_parity_2026_08.json | json | 2,427 | c46a141ebb59df3fcc430ae735c0bd7df7a472c2b3b7631fb3f117f5535e2f06 | B | KEEP | — | 3 |
| 329 | data/research_manifests/equity_candidate_observation_parity_corrective_2026_08.json | json | 3,252 | 4e799231afcfc2a16f7cecf16059ed9b38628b56ddba17bcb6a0730c5da9bb0b | B | KEEP | — | 13 |
| 330 | data/research_manifests/equity_candidate_observation_parity_final_closure_2026_08.json | json | 3,680 | 2ecc5c6df773f807b78a97d37bccb9267a6a43663a1e9808199e7d5c7f57d54d | B | KEEP | — | 18 |
| 331 | data/research_manifests/equity_candidate_observation_parity_final_closure_2026_08_r2.json | json | 3,683 | b26c7963520efa5e340b79587c3ffbf2c93774152d296b550e2577d83e3e9e72 | B | KEEP | — | 18 |
| 332 | data/research_manifests/equity_candidate_observation_parity_final_closure_2026_08_r3.json | json | 3,683 | 79e0593072de45faa50dca9b6e9daab0e9b4e766ca643362665f2d2d9f4184d8 | B | KEEP | — | 20 |
| 333 | data/research_manifests/equity_fundamentals_pilot_2026_09_07.json | json | 2,409 | fd66d6adea54c771a8f64066da8199caf15d2b05385201ae07d4ad74083f6937 | B | KEEP | — | 2 |
| 334 | data/research_manifests/equity_fundamentals_pilot_2026_09_07_v2.json | json | 2,863 | 9efaf385e716bdffbf6fb89e4cc07fcf96e31c4819469bed913c478c3089e17f | B | KEEP | — | 0 |
| 335 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08.json | json | 3,654 | f43d945d15cac98f1098f313c8c7717da250b19dc97af58a96996d899da2a8b3 | B | KEEP | — | 0 |
| 336 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r10.json | json | 6,232 | 1fd4c812bff6de3e41a0e8a2c69be375e8b21ac1d44bb54cd094175612c1e791 | B | KEEP | — | 0 |
| 337 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r11.json | json | 6,371 | 751acab33a9725857f496df7c3f57b56bd0ddec2365bbdf799e9ea3967fdb849 | B | KEEP | — | 5 |
| 338 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r12.json | json | 7,417 | 4108ab3662bb2814966dfae60867b0149efbf511fd8491b62f0f3cc23e87e3a1 | B | KEEP | — | 0 |
| 339 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r13.json | json | 7,651 | dd03914c10600ab941e28d471fa134bf72c23cdfb1960396190ac16b45d48ebe | B | KEEP | — | 0 |
| 340 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r14.json | json | 7,651 | ee6a15aeedf5a58727fd6c0953e499741e79fd569215663a7ab7ca7619ec7bab | B | KEEP | — | 0 |
| 341 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r15.json | json | 7,887 | 822c51b6ad4c4b65aeb00c91c97dccb801c07bb77f730909615f1557cb159701 | B | KEEP | — | 0 |
| 342 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r16.json | json | 7,887 | 1e45a1b9b058a468d0df7efa9a5317be2f84d0e10fcf6d700c2ca4f7928f52a1 | B | KEEP | — | 0 |
| 343 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r17.json | json | 7,887 | e86583b7f037981971645f17548df7c124d24648b50a0b968041f82b8ecddb8a | B | KEEP | — | 0 |
| 344 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r18.json | json | 8,341 | 01db76e2fccae3b7f21b8cc659a2c16f25116ab4aed856d13d7f55ae13c84eba | B | KEEP | — | 6 |
| 345 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r2.json | json | 4,176 | 4408cd1c297f51bd721afe6235ab7ba83b84295280ca81b6ab6454b32361c84d | B | KEEP | — | 0 |
| 346 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r3.json | json | 4,176 | 910818faf2975800430d6d42866b44657e27d76309dd63313c67e021bd2c5edf | B | KEEP | — | 0 |
| 347 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r4.json | json | 5,387 | a3e8d203ff1a18f99c93afb32872c549f8fd9648c3fd548dd4fa3b972600d2e7 | B | KEEP | — | 0 |
| 348 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r5.json | json | 5,474 | 672b1abb4ccfd179e7f07d3291320111c903f251c39cb52d8ea4e8b81ed89a65 | B | KEEP | — | 0 |
| 349 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r6.json | json | 5,552 | da0a40e0bc44724d9fd1bf70bb85ca4ea21dfe6c81d7369ad5afd20e930b963d | B | KEEP | — | 0 |
| 350 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r7.json | json | 6,154 | 6e4b612e00f636f07587a022c2a81f1d8870548f0fc1171aa55175903bea1f31 | B | KEEP | — | 0 |
| 351 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r8.json | json | 6,194 | 967cb8af02fd5bb9b935cff95d55e7665ba6dff0f90e0a7c94501af82415b619 | B | KEEP | — | 0 |
| 352 | data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r9.json | json | 6,229 | 09a1b31cdcdd5ffdc88e947caad50d29b20c2d074a4e4a005131cb655f47f08b | B | KEEP | — | 0 |
| 353 | data/research_manifests/equity_h01_h05_minimal_pit_pilot_2026_09_07.json | json | 5,619 | df3671eddbcbb13faf9b73fc926a53b85011eea9ddef8da2bd560798e8ce4139 | B | KEEP | — | 110 |
| 354 | data/research_manifests/equity_h01_h16_standalone_overlay_2026_09_09_v1.json | json | 1,982 | 83d37c7532a4b7d8800503efbcd33eaf2f54168af23439132d6980d5dd1f4629 | B | KEEP | — | 2 |
| 355 | data/research_manifests/equity_hardening_2026_08.json | json | 2,975 | d2b51b6556ed2a7dd8945879f7e03d371c7770dfe8732ea9b1c1544304ec54c6 | B | KEEP | — | 4 |
| 356 | data/research_manifests/equity_low_beta_candidate_2026_09.json | json | 5,196 | e5e9aa6b9a21a086b9d5c2f3b5ad51cfae5e8c108eb1ba31b2ad4552346e9a3b | B | KEEP | — | 18 |
| 357 | data/research_manifests/equity_low_beta_candidate_2026_09_r2.json | json | 5,800 | 6d8bbc0e751de721e6702c3a3a58c83bc2f31f3eab6941f197922cc67b6cb834 | B | KEEP | — | 5 |
| 358 | data/research_manifests/equity_low_beta_candidate_2026_09_r3.json | json | 5,813 | 2d8e3b2bb6b676d5a66973521031ac2964b6d7ab09ae262cfa1d596598a2e204 | B | KEEP | — | 0 |
| 359 | data/research_manifests/equity_low_beta_candidate_2026_09_r4.json | json | 5,766 | f44d2042bcf3a5a1a4c65f8caa02a569606ce67cb3cbc6b686f2395d238cbf7f | B | KEEP | — | 1 |
| 360 | data/research_manifests/equity_low_beta_candidate_2026_09_r4_draft_07ce.json | json | 5,503 | 7691186299dcabc7f4529ea4e4bd5ced98321b3c2c3738bab1e44f1c173cdde4 | B | KEEP | — | 0 |
| 361 | data/research_manifests/equity_m3_candidate_ocp_2026_09.json | json | 111,929 | 6264e56135a95e3ebd2b244f5d19b0b54f2845808314393131fa9599209fe1e1 | B | KEEP | — | 45 |
| 362 | data/research_manifests/equity_m3_candidate_ocp_remote_attestation_2026_09_r2.json | json | 6,245 | 2c33fd7adf2822f424289c698df317a355d5518a859982462d85302165fdc20b | B | KEEP | — | 50 |
| 363 | data/research_manifests/equity_m3_composite_dataset_2026_09_r4.json | json | 1,973 | c799cfb761093ed2c712fd1c90df7da1c63c05d62e30bb1a46b30f1d7e24a561 | B | KEEP | — | 41 |
| 364 | data/research_manifests/equity_m3_data_gate_2026_09.json | json | 6,640 | 9ae0bd74262e52bba4d794618188e9044b72bdd0ced0743a61cd09ca32141053 | B | KEEP | — | 13 |
| 365 | data/research_manifests/equity_m3_data_gate_2026_09_r2.json | json | 8,626 | 2d11150bff329758b9a21c181920d3f8baf75a7025919a9f78bd74668b9fcb0c | B | KEEP | — | 26 |
| 366 | data/research_manifests/equity_m3_data_gate_2026_09_r3.json | json | 8,986 | 3c0bef19c1f64a042d69df1d5d6dac65ad705e36dabcc8b1f9d9807f3b963754 | B | KEEP | — | 32 |
| 367 | data/research_manifests/equity_m3_data_gate_2026_09_r4.json | json | 12,353 | 7cf31eef48a7a9352d689475ebc392dab336b8fc84fc62f7ac6374950fe0e55e | B | KEEP | — | 65 |
| 368 | data/research_manifests/equity_m3_data_gate_2026_09_r5.json | json | 14,734 | 83a2b7b796939423eff9c3c0a2d16000594cbbbed26e9de2df61206c5ecdb51e | B | KEEP | — | 106 |
| 369 | data/research_manifests/equity_m3_economic_2026_09_r1.json | json | 31,668 | 109d159d0c89b0dcaa6d5c9cc4a1e29c86b056e168e44ea8105724f9564d82f3 | B | KEEP | — | 26 |
| 370 | data/research_manifests/equity_m3_economic_execution_2026_09_r1.json | json | 32,649 | 5ebd7ca8c9485bd81ebb533c73fff6e5db90dee3820df3d04dd00b9bfa5232c1 | B | KEEP | — | 28 |
| 371 | data/research_manifests/equity_m3_m4_2026_08.json | json | 1,240 | ed3d1b013970da4678e5dd12133f8ad65466adf0f801fbd6574849a2fcb7529f | B | KEEP | — | 2 |
| 372 | data/research_manifests/equity_m3_pit_population_2026_09_r4.json | json | 3,093 | d7de144b06439738e08ca1c39e29c6743d74d352c5f31395bfeb9259685d4b86 | B | KEEP | — | 61 |
| 373 | data/research_manifests/equity_m3_pit_population_2026_09_r5.json | json | 103,026 | 2d93b50fb7668fb899991f83a4223e6ab5f18acbdf3f4e9314a66f04d54400d4 | B | KEEP | — | 70 |
| 374 | data/research_manifests/equity_mh_raw_promotion_2026_08.json | json | 8,413 | d2b906a836484b102b30610d4b3786923ace8a079df0fc7b811eebb0516b6e96 | B | KEEP | — | 7 |
| 375 | data/research_manifests/equity_pit_universe_2026_09_07.json | json | 1,016 | e81335126f6602efd81f2c94a4cb78d997a306ad2c3c4c672d74f34f248e2c74 | B | KEEP | — | 0 |
| 376 | data/research_manifests/equity_profit_protection_2026_09.json | json | 868 | 1d1ff87df84e9860fc756386c433ad402a279a317e976ad23c103fb7677558b3 | B | KEEP | — | 2 |
| 377 | data/research_manifests/equity_profit_protection_2026_09_r2.json | json | 12,427 | b2fc4673e56b92bb5014e062c03d283f5d5d78c1f758f1992879f11375fc42fa | B | KEEP | — | 8 |
| 378 | data/research_manifests/equity_sec_normalization_2026_09_07.json | json | 1,450 | 8be5d18f663e700608589c3b3d807aac2d06c0f0d049b6cad2a99d21c7060183 | B | KEEP | — | 0 |
| 379 | data/research_manifests/equity_sec_normalization_2026_09_07_r2.json | json | 1,638 | 7ff807ce541da533cd918dc6cc6d07344d9472668977da4351330f8521c6ec1f | B | KEEP | — | 0 |
| 380 | data/research_manifests/equity_sec_original_documents_pilot_2026_09_07.json | json | 1,226 | 6854f21ea0eea663fc4d9ac21d13a64ae983153ae5363f033be01bd86872b6ce | B | KEEP | — | 0 |
| 381 | data/research_manifests/equity_secondary_causal_gate_2026_09_08.json | json | 3,315,746 | 2d860cdaa7cda9f04e93b92f4feac1d909ecc6aabc694b6fa188d57be60c10d1 | B | KEEP | — | 16 |
| 382 | data/research_manifests/equity_v3_alpha_roadmap_2026_08.json | json | 3,926 | 0c3f044d8d0dd0851aaf8f48f2bdff54a38218a596bc357e4cfa0b11aaa27547 | B | KEEP | — | 8 |
| 383 | data/research_manifests/equity_v3_followups_2026_08.json | json | 2,459 | 23a77e28e1024e80e52741d8c31f5fc1be23ba4d7dd33367b77b6167d159da11 | B | KEEP | — | 4 |
| 384 | data/research_manifests/execution_cost_transferability_p0_2026_09.json | json | 4,125 | 6ac1479b33e966d05ce9e83abcb0a25d0e2678b3e98dd40b1a2b5c1b411acec6 | B | KEEP | — | 6 |
| 385 | data/research_manifests/execution_cost_transferability_p1_paper_observation_2026_09.json | json | 5,527 | aa8edf95aaee4a819c63f89b8dccd3123063279e0c61e5ff7c174d7759638aff | B | KEEP | — | 9 |
| 386 | data/research_manifests/execution_model_robustness_backtest_2026_09.json | json | 6,330 | 795ce9aabea7fddaf15f14cc08637b52bcae97e466daa6a7133d4af346fa538f | B | KEEP | — | 2 |
| 387 | data/research_manifests/full_survivorship_correction_2026_08.json | json | 11,082 | 1e1fb89f034b24c6cfd8dec6e154b860e71db2ce98b922308bab63044019864f | B | KEEP | — | 7 |
| 388 | data/research_manifests/full_survivorship_correction_F_2026_08.json | json | 5,600 | 258d98d0193e2efecfce9fffef31389bb2f8f9fcad88183aca7289cb0353af78 | B | KEEP | — | 2 |
| 389 | data/research_manifests/full_survivorship_correction_G_2026_08.json | json | 3,529 | 8d90dc7e21b283e9804529c3e3b5015c74ad53e1c081856a43f687ab00bc53e1 | B | KEEP | — | 4 |
| 390 | data/research_manifests/full_survivorship_correction_H_2026_08.json | json | 3,023 | a457018d470c5e01ce50a2d513fe869b5f8c5a4e6bd8de2ae460d11447667786 | B | KEEP | — | 2 |
| 391 | data/research_manifests/full_survivorship_reconstruction_2026_08.json | json | 11,071 | 1225cc3ca4722da077ec58ae8734a5fac7b911a600d784c8f3a7ba03c1ceae76 | B | KEEP | — | 12 |
| 392 | data/research_manifests/h14_turn_of_month_protocol_2026_09_06.json | json | 3,380 | 0febe9c430eeb7019d81e80f3d9e181f2f6077f414932ce383abf6203931a6a7 | B | KEEP | — | 0 |
| 393 | data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r2.json | json | 5,559 | 7a8c806de357f7bab29b279fc42d4ec0dcbee18af025ae4056510425854a026d | B | KEEP | — | 34 |
| 394 | data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r3.json | json | 3,967 | a143715603a1d4506f1f0dac840a0373daab4d02eba10d9a5577ff48d58480a5 | B | KEEP | — | 8 |
| 395 | data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r4.json | json | 4,162 | 014c232e59bd3d6c19ffb00c39e5ad45fdb27d3c1e16c1804f4bd29c6c0bfd0a | B | KEEP | — | 17 |
| 396 | data/research_manifests/high_proximity364_history_fixed_2026_09_05.json | json | 2,491 | 087827408096a392d603b3c1f97f001b6c703c88a8b71231b17c99c286ebebca | B | KEEP | — | 5 |
| 397 | data/research_manifests/high_proximity364_trace_2026_09_05_frozen.json | json | 2,891 | 8fbfe412a2e37e31a4eebad3a7c5add1c9ec5984f100ab519955380db7d1f6d1 | B | KEEP | — | 2 |
| 398 | data/research_manifests/immediate_candidate_freeze_2026_09_09.json | json | 17,098 | 8cec049f1d61d929f001b53af43a7e46ede1619243a1605e30d1dc312dd9965b | B | KEEP | — | 16 |
| 399 | data/research_manifests/k11_signed_flow_archive_expectations_2026_09_08.json | json | 1,474 | 05b184aa274f843e7f56efd8f6a969b6566273087d4fdbab2972b09cc4dee277 | B | KEEP | — | 22 |
| 400 | data/research_manifests/portfolio_expansion_crypto_diagnostic_2026_09.json | json | 3,986 | 7418235ee053707ea8f6ee001c62a7d2ff0387019b959daed87241ca942daee0 | B | KEEP | — | 0 |
| 401 | data/research_manifests/portfolio_expansion_crypto_diagnostic_2026_09_attempt_02.json | json | 2,159 | f9c1aa311c7ae4aefd125cfac7824a2381078483606f6117ee1eae4ec1fa2c46 | B | KEEP | — | 4 |
| 402 | data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_attempt02_validation_receipt.json | json | 1,404 | cbf530d40e5e3f62fd366e19421ef2d4f244263773f16e8e0c7e9c27debfd9a5 | B | KEEP | — | 2 |
| 403 | data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_attempt03_frozen.json | json | 3,542 | b84d411e1edcf855afc1505098415e707b605a8517cacdbb4e3cb4b8df4140f2 | B | KEEP | — | 32 |
| 404 | data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_attempt03_frozen_receipt.json | json | 982 | c7b4c97cad9242a612ed792f4ac73ad0e7cf1e02f35b10f4fa38c3821ce0b281 | B | KEEP | — | 0 |
| 405 | data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_draft.json | json | 3,336 | 53354f3598ea812f4cbc197525e45750a92516c6923caacc849549fc11807919 | B | KEEP | — | 13 |
| 406 | data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_frozen.json | json | 4,753 | 71183f5a99a8ace070e381a5c72c44715af8b3c2e4371e296c5eb8bb52d14e36 | B | KEEP | — | 27 |
| 407 | data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_frozen_corrected.json | json | 3,492 | 476de39f8fa6737de7166a36afca4ea53f468bd38f1e18d781f870a0ae0945bf | B | KEEP | — | 0 |
| 408 | data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_frozen_receipt.json | json | 1,529 | b3e9c44facd8993ea2cb546fb55094d9209ac3f87dcfe38c2eabbce82f01cd92 | B | KEEP | — | 0 |
| 409 | data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_preflight_receipt.json | json | 802 | d2d2a87029619c7567e665832c8f3a766161c14c226896c8a8b80236140aa413 | B | KEEP | — | 0 |
| 410 | data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json | json | 14,176 | f83c59f885385d199e03110a76bac500c23d3d0e1efe816923bdd3d74ff4f64e | B | KEEP | — | 43 |
| 411 | data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.schema.json | json | 4,880 | 205556f00504ea906f7fe91bda276bebed581839995ee514f36cfb5d46dcad9f | B | KEEP | — | 35 |
| 412 | data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_holdout_custody_contract.json | json | 3,228 | c7dab24042decc71224a1754b728a818b8763a953798dd0906ed9cbb5627629f | B | KEEP | — | 37 |
| 413 | data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_source_matrix.json | json | 6,868 | 73b6ee008210735e91b069c8ebddbde5dab63b9c75aad9e9d546697ccdbc27ea | B | KEEP | — | 45 |
| 414 | data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.json | json | 57,043 | 07df2b2fc8856b52a8e81e5a57f55063aa42519e9f6e4bcd740aaa64408d8e45 | B | KEEP | — | 13 |
| 415 | data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.schema.json | json | 10,720 | 035dc91cade59d692abbb5d2d3b65dde1c15be89860a961a61bd0ec39638faaa | B | KEEP | — | 11 |
| 416 | data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json | json | 342,116 | 25c6a6740b787bb00525931c5665a4c76217f92df3729221c7e5494bf97f7d2c | B | KEEP | — | 21 |
| 417 | data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_family_registry.json | json | 1,998 | e200940852cb683ab2e8e2db33f4a7f408f8bb39abbec67a7b6454342ca6a16c | B | KEEP | — | 13 |
| 418 | data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json | json | 741 | c69e6bd76964131f41a02b4a58a1a308af1a2a1a038c8e339b1512b097b2f412 | B | KEEP | — | 11 |
| 419 | data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_statistics_plan.json | json | 3,112 | c044b2b39b9171fb8277625c58be1d4c2784900fdf2f3c79c4a0be26d17b7997 | B | KEEP | — | 13 |
| 420 | data/research_manifests/survivorship_aware_dataset_2026_08.json | json | 7,347 | 96b83116262bfecb38c110049ecdbedb66c67fc4533cc1a64be303926e75e9c8 | B | KEEP | — | 5 |
| 421 | data/research_manifests/v3_exit_latency_optimization_2026_08.json | json | 14,501 | 4258d9ee32ccdc0caea42c4def2b907a6a25ecd0a507bbe14c3b15039a11c24f | B | KEEP | — | 12 |
| 422 | data/research_manifests/v3_position_risk_optimization_2026_08.json | json | 20,021 | 07ba72409becbc19dbf2ac1c3ab5955eae69f44896bad899623805663e287557 | B | KEEP | — | 10 |
| 423 | data/research_manifests/v3_risk_tail_loss_optimization_2026_08.json | json | 13,904 | 827a9d7b17d9b43526e1137e872d9922dbb498e2ef04dfdc81198237a9b541ce | B | KEEP | — | 8 |
| 424 | data/research_manifests/x01_maturity_bucket_approximation_2026_09_08.json | json | 623 | 45cdb85a47519a0cfa5aa170f656d3c9e9a7d70eb033ebba5cc6ee9cd941a9f7 | B | KEEP | — | 0 |
| 425 | data/research_manifests/zero_wait_overlay_freeze_2026_09_10.json | json | 19,788 | 8376b8dd9f8e17235395ad66e952802ab61e75dba0e3a0f5f5ff9072e8a70b4e | B | KEEP | — | 12 |
| 426 | data/research_receipts/equity_candidate_observation_parity_corrective_2026_08.json | json | 20,469 | d9a8b5dbd971ea23ff22b10b63f60ab5a10c2e8331fee0f5a727772b2dc9296b | B | KEEP | — | 9 |
| 427 | data/research_receipts/equity_candidate_observation_parity_final_closure_2026_08.json | json | 22,875 | 3e2d6c7bbad542da6661f7099645de4f567cb572a17341ba2deb4972eecbcc42 | B | KEEP | — | 3 |
| 428 | data/research_receipts/equity_candidate_observation_parity_final_closure_2026_08_r2.json | json | 22,881 | 134794b57a46cf8950232ccfa0c3ce1997f13dee92d9be71a7cf88ef1c1c5edd | B | KEEP | — | 3 |
| 429 | data/research_receipts/equity_candidate_observation_parity_final_closure_2026_08_r3.json | json | 22,881 | a1ff85a2eeecd7d8fd2bdde309703f93339a055c3d6a1672fc838a8f6b047bd1 | B | KEEP | — | 5 |
| 430 | data/research_receipts/equity_m3_candidate_ocp_2026_09.json | json | 53,077 | f56695c25c31f6d0befdd225308254614d1c93e713b22bfc29a2dd5257e22bd1 | B | KEEP | — | 26 |
| 431 | data/research_receipts/equity_m3_candidate_ocp_2026_09_r2.json | json | 25,253 | 53196217121d4dff99feef20b97e2d6df769ba2b882edc42881ab75691a4bc52 | B | KEEP | — | 58 |
| 432 | data/research_receipts/equity_m3_data_gate_2026_09.json | json | 21,482 | e875a028a0eff4e98e252f8755ef2d5d0861907d00651418455cefbe3ecc28a5 | B | KEEP | — | 4 |
| 433 | data/research_receipts/equity_m3_data_gate_2026_09_r2.json | json | 42,086 | e13e464a95167ef46e079f9aa297913ec339de71691e612973994a647f0582f7 | B | KEEP | — | 6 |
| 434 | data/research_receipts/equity_m3_data_gate_2026_09_r3.json | json | 52,729 | 395edcfa81e78b6d3fbbe7f696d2aa90ddaf04707a6cd50ca1e66cd72942ec0e | B | KEEP | — | 4 |
| 435 | data/research_receipts/equity_m3_data_gate_2026_09_r4.json | json | 60,919 | a263f27f14fcd55b860e4a2e2880c6494289bdd0291f114e86d087bee1d04382 | B | KEEP | — | 2 |
| 436 | data/research_receipts/equity_m3_data_gate_2026_09_r5.json | json | 8,016,906 | 841d5640352ee01c5d284b66a4fb186d6e8f62d5cdb8dadfbe185e2330b2aa36 | B | KEEP | — | 74 |
| 437 | data/research_receipts/equity_m3_economic_2026_09_r1.json | json | 398,959 | f8330a32bd36f908194f40fe4842fd9e85be0d4a76ced21fe1122893369a978c | B | KEEP | — | 6 |
| 438 | data/research_receipts/equity_m3_economic_predeclaration_2026_09_r1.json | json | 32,139 | d631b387ad581b5386514e60db3950e838ec3910b9c2a00200c21c6484bbc6fb | B | KEEP | — | 34 |
| 439 | data/research_receipts/equity_m3_iwm_acquisition_2026_09_r3.json | json | 1,050 | 2af78b3b6f85457ca7b92f349f0295ed4ebad7a753c4fe8e1ff56a95a4eb98d6 | B | KEEP | — | 2 |
| 440 | data/research_receipts/equity_m3_iwm_acquisition_2026_09_r4.json | json | 1,159 | 3b899712c7f75d95ee95704fd2aba48e9dbf96352f7946df2137376e10e90dba | B | KEEP | — | 2 |
| 441 | data/research_receipts/equity_profit_protection_2026_09_r2_current_control.json | json | 2,692 | 9692288e5319334321227d9a1da5a0c2e89e566fc9d19c21b9df438c35b1b1a1 | B | KEEP | — | 19 |
| 442 | data/research_receipts/execution_cost_transferability_p0_2026_09.json | json | 3,323 | c1edfcc53a2f8fbf7a8dd96351699f31a652ec3829c0ee648657f2f8b0881fdc | B | KEEP | — | 8 |
| 443 | data/research_receipts/r46_treasury_auction_unwind_tlt_v2_2026_09_10_native_attestation.json | json | 13,052 | fa4c96ef31d71dbdac8216005d492164bee9453065616213860061ebf1b94876 | B | KEEP | — | 11 |
| 444 | data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json | json | 127,784 | c24cffa145391d139c442e64237aae7225d14cf76ce7f0ce2ade095ba3eec960 | B | KEEP | — | 44 |
| 445 | data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json | json | 6,374 | f88efc293d5645f8e32514e80383f4b5f64a4bca068c7bb85382bdc004b318ef | B | KEEP | — | 73 |
| 446 | data/research_receipts/r46_treasury_source_capability_2026_09_10.json | json | 3,568 | 0fb973b5a9038739611bb2e1d92bb6a9c4e88c884821fc8efa3f1c3eeb17ad74 | B | KEEP | — | 22 |
| 447 | data/research_receipts/r46_v2_independent_software_review_2026_09_10.json | json | 1,094 | a33c03f42ade852310de78be42cea95519c69cbeba0baca90c641f0ee6c9acc3 | B | KEEP | — | 17 |
| 448 | data/research_runs/all_34_research_closure_2026_09_09/closure_summary.md | markdown | 468 | d74c5f0d915ae329215a226a2a661efc331b2cc2a26aebb502e469376cdd2bb5 | B | KEEP | — | 5 |
| 449 | data/research_runs/crossasset_max_testing_2026_09_09_v3/overlay_diagnostic_report.md | markdown | 1,148 | 16cb81387755ac7a0817a9f3159708fa9994ff80979217c112a052157883cf47 | C | KEEP | — | 3 |
| 450 | data/research_runs/crossasset_max_testing_2026_09_09_v3/verdict_matrix_report.md | markdown | 42,671 | fc99b28af1592518e95608d5be33697e62acbc73cdb0805d53cac08fb98a66e8 | C | KEEP | — | 3 |
| 451 | data/research_runs/crossasset_retrospective_diagnostics_2026_09_09/diagnostic_report.md | markdown | 1,250 | 60a4233fcdb9b1edc4f7c7c4aa679a597fa4bfe5dc21711f2b34d7e7118c81e2 | C | KEEP | — | 0 |
| 452 | data/research_runs/crypto_max_testing_2026_09_09/capacity_diagnostics/capacity_diagnostic_report.md | markdown | 2,653 | 8806244fae34cb1254257168ccfcf7a93d5eeb6f571ba85f625983841a7d002a | C | KEEP | — | 3 |
| 453 | data/research_runs/crypto_max_testing_2026_09_09/diagnostics/diagnostic_report.md | markdown | 3,975 | 6d6eb2c24055337acbd7b32a037b7623536e15f3fdd99c6c741c9964d12b3d2c | C | KEEP | — | 3 |
| 454 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_btc_eth_priceusd_1d_2022_2026-07-31.body | http-body | 287,386 | 6c4ee915e975e07b3046ad7ac5c866e531237284b24952101ddbe9043c7b6e3a | B | KEEP | — | 8 |
| 455 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_btc_eth_priceusd_1d_2022_2026-07-31.csv | csv | 394,507 | 482cd8ecb585b16941ddc9bf1ff11206f951e27736d2a83ae72d68323dd705db | B | KEEP | — | 8 |
| 456 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_btc_eth_priceusd_1d_2022_2026-07-31.headers | http-headers | 639 | a8b71ad6e8c1edfb490ee6bc1fc3dc3351995e4ccfa4b726b81eae92c52ee43c | J | KEEP | — | 0 |
| 457 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_k01_metric_alias_probe.body | http-body | 1,141 | f49c07215e2246632c0864f37612b0650cc352d5825addd2953d485d66d1ed59 | J | KEEP | — | 0 |
| 458 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_k01_metric_alias_probe.headers | http-headers | 688 | d6329771a2319e7a3d34b4c2d63f285a94ad81d17992aca198b6120242935eca | J | KEEP | — | 0 |
| 459 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_k03_metric_alias_probe.body | http-body | 716 | 13836ffdd1968f0c9ec37663388d999940e0df9728a2018b9e063e92763b63a4 | J | KEEP | — | 0 |
| 460 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_k03_metric_alias_probe.headers | http-headers | 688 | 0afb42ae78e183c9cae0d7e03476ed905562f4e322bf8d980a17ab7eee87817c | J | KEEP | — | 0 |
| 461 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_k04_metric_alias_probe.body | http-body | 940 | 25918c602b19016ea42f2e285e44d2dd30bd7b7f2bd15df23e17d0190c3073c8 | J | KEEP | — | 0 |
| 462 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_k04_metric_alias_probe.headers | http-headers | 688 | a0d3a252ecc3fe18eb791283e99ab44af9bb8bc1938d8b8b0607e1bd8c1221a5 | J | KEEP | — | 0 |
| 463 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_k12_metric_alias_probe.body | http-body | 146 | 3f6d29de14e928baf617fc47c89210d9b7df4d4fe9582cc68e999dff4e1089cf | B | KEEP | — | 8 |
| 464 | data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_k12_metric_alias_probe.headers | http-headers | 659 | 22869191c37df0ae9429cdb51ae83b03c83ae530e8cbef0026eb8e9a2338738a | J | KEEP | — | 0 |
| 465 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_funding_cutoff_2026_07_30.body | http-body | 475 | fca0049c7e4d69567eb60bcc8d17084cf0bf99f2785ff6d53f18194e54cb9304 | J | KEEP | — | 0 |
| 466 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_funding_cutoff_2026_07_30.headers | http-headers | 1,079 | a9a6af3901100d53f79ada7760c89ed74a59c4e28df4df9e3f0e91322cd89343 | J | KEEP | — | 0 |
| 467 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_funding_full_2022_to_cutoff.body | http-body | 33,598 | 2a32633a6528133bf42f0294dca016ab1cf343e0445f79687f6fc35873be1ec8 | J | KEEP | — | 0 |
| 468 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_funding_full_2022_to_cutoff.headers | http-headers | 1,081 | 1fb931c082d98c94a5e746ddd3fef2a2efdc20aee08b7c823704352ef32fad67 | J | KEEP | — | 0 |
| 469 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_funding_historical_2024_01_03.body | http-body | 89 | 5b83239da90473ffbccebca3c85be133404d553b87d1d2190e3d8bad33eef915 | J | KEEP | — | 0 |
| 470 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_funding_historical_2024_01_03.headers | http-headers | 1,078 | 3b4dba2cb2eb84e0baa4ca52ff18e6da199581a48a6505ced92fa153998a102d | J | KEEP | — | 0 |
| 471 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_future_basis_cutoff_2026_07_30.body | http-body | 111 | ef0d76be44c213ef60ed849e1349247c3c65bf7daa407cab9c8bc4889160986b | J | KEEP | — | 0 |
| 472 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_future_basis_cutoff_2026_07_30.headers | http-headers | 1,079 | 730655f4a5a3c90c0f33f2d5135cb95569653ba2956eb5e49e27be0e43da5f09 | J | KEEP | — | 0 |
| 473 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_future_basis_full_2022_to_cutoff.body | http-body | 34,138 | 61714d02898b928881a730f8d0d4d4169ce95c0da9cc906ca486ac3755c93cf8 | J | KEEP | — | 0 |
| 474 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_future_basis_full_2022_to_cutoff.headers | http-headers | 1,081 | 9801231f94849479a669e95edbeef4bcc35ff3fb4fb6a3ceafb475a2718a0a6c | J | KEEP | — | 0 |
| 475 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_future_basis_historical_2024_01_03.body | http-body | 110 | fb83363750e517f28edb859bfc0c95018edb466b0573d4626a790705dfdcdcac | J | KEEP | — | 0 |
| 476 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_future_basis_historical_2024_01_03.headers | http-headers | 1,079 | cd6ab2c8ae2266dfc96a6cfa776a827dd8c41d44e1d5b5f1b738395589c909b8 | J | KEEP | — | 0 |
| 477 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_open_interest_cutoff_2026_07_30.body | http-body | 166 | 79686660f1e0feb455913d38966198f5a34b29c52db12a9d20601b5093fa0886 | J | KEEP | — | 0 |
| 478 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_open_interest_cutoff_2026_07_30.headers | http-headers | 1,078 | df050b251c964a99035bec5d3a2159c97cad7d7bd461fa96e31e43bf68e03a21 | J | KEEP | — | 0 |
| 479 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_open_interest_full_2022_to_cutoff.body | http-body | 69,835 | aed853479fd6387b676962e2f98296338dfc538fd1909fb14bab730388baa4c0 | J | KEEP | — | 0 |
| 480 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_open_interest_full_2022_to_cutoff.headers | http-headers | 1,081 | 55f9ff526351b3d782b590b4b06779f57b0b72814fdd0e4db30fec415b50cf5c | J | KEEP | — | 0 |
| 481 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_open_interest_historical_2024_01_03.body | http-body | 166 | a86d99dc3f7c406140446873f961f357fb6dad893adb84cffb7a057ffeb65ba1 | J | KEEP | — | 0 |
| 482 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_ethusd_open_interest_historical_2024_01_03.headers | http-headers | 1,079 | f7e49d24e4c888a35004df61272d2fdec65c37778f48b289372eed81fedc9119 | J | KEEP | — | 0 |
| 483 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_funding_cutoff_2026_07_30.body | http-body | 491 | 54df95b76a4b2c54a044248302b520061e7ca0525e95a97f0b1b1b3d1a0f885a | J | KEEP | — | 0 |
| 484 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_funding_cutoff_2026_07_30.headers | http-headers | 1,079 | b1f5f793006bc6110a46eb683bbae0ac8913509551141c19c35effd3cd5876c8 | J | KEEP | — | 0 |
| 485 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_funding_full_2022_to_cutoff.body | http-body | 34,295 | ae98e4a4898098de5569d8f2f8d7bbf6e6bb6737609d83a0d26484cfd24b58fa | J | KEEP | — | 0 |
| 486 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_funding_full_2022_to_cutoff.headers | http-headers | 1,081 | ba24a073975966965650f660c3d975b8f153cba54dcbac8b4a1e5136bd713bd9 | J | KEEP | — | 0 |
| 487 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_funding_historical_2024_01_03.body | http-body | 89 | 5b83239da90473ffbccebca3c85be133404d553b87d1d2190e3d8bad33eef915 | J | KEEP | — | 0 |
| 488 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_funding_historical_2024_01_03.headers | http-headers | 1,078 | 5067084c9eafaa8f0d3c9cd1a7264da11e48f46b98ad32a5f8f3bf00a5510ab5 | J | KEEP | — | 0 |
| 489 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_future_basis_cutoff_2026_07_30.body | http-body | 112 | ba6236b2865872eeab8fa5ba8b510ba7e62bdcf7376e72006aae873147a047cf | J | KEEP | — | 0 |
| 490 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_future_basis_cutoff_2026_07_30.headers | http-headers | 1,078 | d6405938d1a9716b58fd1ddf8239048888e5aaf05b7b56ffb89f7e77c52cbd3f | J | KEEP | — | 0 |
| 491 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_future_basis_full_2022_to_cutoff.body | http-body | 34,217 | 860e80f94f25e6f4705bab758d5c5a02d15fc8322bc10ab8e58e0039b2fe1647 | J | KEEP | — | 0 |
| 492 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_future_basis_full_2022_to_cutoff.headers | http-headers | 1,081 | 3bd235c911bd4a7d61ca49c316c287eeefca54279c6708ff9f5be05928ba4866 | J | KEEP | — | 0 |
| 493 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_future_basis_historical_2024_01_03.body | http-body | 110 | fb83363750e517f28edb859bfc0c95018edb466b0573d4626a790705dfdcdcac | J | KEEP | — | 0 |
| 494 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_future_basis_historical_2024_01_03.headers | http-headers | 1,078 | 698edb8af505686f24acbe32f12bd526664aad30e6db128b4d475bc008a49142 | J | KEEP | — | 0 |
| 495 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_open_interest_cutoff_2026_07_30.body | http-body | 166 | e6e2ae4028813358c71a00f77f41aa13e16538dbbb477b9c143b77330efd86c1 | J | KEEP | — | 0 |
| 496 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_open_interest_cutoff_2026_07_30.headers | http-headers | 1,079 | 62bca85abb81a083295f3ad90949a9bb97490a2b2a0756708765b2a9d8b2df6e | J | KEEP | — | 0 |
| 497 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_open_interest_full_2022_to_cutoff.body | http-body | 68,820 | b040997cdf455c1e59e557c523d18afd968ef66c849943986e785a8556ad8593 | J | KEEP | — | 0 |
| 498 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_open_interest_full_2022_to_cutoff.headers | http-headers | 1,081 | 602c53d77d30b2be34e209d56efcf52b109de19e34b36928445dc1295e101159 | J | KEEP | — | 0 |
| 499 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_open_interest_historical_2024_01_03.body | http-body | 174 | 749c11bef5ced25de7a6af7313e664f0d239871ff648c5e8610c02fc2413f015 | J | KEEP | — | 0 |
| 500 | data/research_runs/crypto_max_testing_2026_09_09/routes/kraken_pi_xbtusd_open_interest_historical_2024_01_03.headers | http-headers | 1,079 | 283840b1b77a8a78a31a239797a81e000bb81d4bd31094cbeaf2b4d1b5e9aadb | J | KEEP | — | 0 |
| 501 | data/research_runs/equity_h01_h16_max_testing_verdict_2026_09_09/report.md | markdown | 3,346 | 2edfd97180977dd2db691fbfad9be426864bfaf9d5a94228a98402100b111fd9 | C | KEEP | — | 3 |
| 502 | data/research_runs/equity_h11_nport_snapshot_2026_09_09/metadata_cleanup_plan.md | markdown | 1,026 | 61ac8192a92a9334ecff0d40e5fdd7e8258f5093bb792262b4b538099091cac2 | B | KEEP | — | 8 |
| 503 | data/research_runs/equity_h14c_posthoc_2026_09_09/report.md | markdown | 1,671 | 2dcb6cdf875c8da4d97a307b09fceef46e028dd19439ef6d0c3e05ad60153941 | C | KEEP | — | 3 |
| 504 | data/research_runs/equity_local_diagnostics_2026_09_09/diagnostic_report.md | markdown | 3,947 | f6f9dd409febbcd5a8d1f0ae1932d6d2fd899b700adf165e29ca161958e3a47a | C | KEEP | — | 3 |
| 505 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_01/blocked.json | json | 516 | be73507c6120916fe87b5e285acd4ab2ae8111c6b17f375fa6374316dd77a650 | C | KEEP | — | 0 |
| 506 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_01/invocation.txt | text | 77 | 4734f7d059047b6152eec488481853fb07f0544fafb38348915953ad369a078c | J | KEEP | — | 0 |
| 507 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_02/blocked.json | json | 627 | 6803fc6274644525e39d275c08076bc106056d9a75f147eafe6bb3fc182e6829 | C | KEEP | — | 0 |
| 508 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_02/invocation.txt | text | 77 | 92c78453ec5271b80764acb658e657a73888437db30c14bd43e73b0eaced5fc2 | J | KEEP | — | 0 |
| 509 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_03_R2/blocked.json | json | 540 | 2a7b7214dfa828619b05b978eac449b5a1bbab6403a430174f7179b7d501cddc | C | KEEP | — | 0 |
| 510 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_03_R2/data_preflight_r2.json | json | 4,254 | ae5783d73b9a70cfab5e9d508ffb692c3567c54c6c0ba962ec1bd9f6a6eb0a5d | C | KEEP | — | 4 |
| 511 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_03_R2/frozen_manifest_r2.json | json | 5,800 | 6d8bbc0e751de721e6702c3a3a58c83bc2f31f3eab6941f197922cc67b6cb834 | C | KEEP | — | 0 |
| 512 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_03_R2/invocation.txt | text | 215 | 4b14b09ab4e26fc6d62c16123400ca3ccb652a39719ee28edd43dec84487f205 | J | KEEP | — | 0 |
| 513 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_03_R2/source_snapshots/equity_low_beta_candidate_2026_09.py | python | 22,891 | e09a1b342194770b3e254061ddccd60badd50854328a22c64167ccbad8271edd | B | KEEP | — | 28 |
| 514 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_03_R2/source_snapshots/equity_low_beta_engine_2026_09.py | python | 19,275 | c9d77323c813459d7b71b00fbc93bd123ebb29a573e76991f1606e2db029018c | B | KEEP | — | 10 |
| 515 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_03_R3/revision.json | json | 790 | a8591a1db297eeedddc29ce4ed0faf0344b4b3ba014a32e516fa3c8b4b66a025 | C | KEEP | — | 0 |
| 516 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_04_R3/coverage_diagnostic.json | json | 1,095 | 381ab11f99d3e801230fefa472ce7b119d1448b2b03a5602d0d1bb7e01afb315 | C | KEEP | — | 2 |
| 517 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_04_R3/data_preflight_r3.json | json | 4,254 | 109ce39870a0cb586672eb2dc7471a4fbf70ebd9704c595239344936f3ba246c | C | KEEP | — | 2 |
| 518 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_04_R3/economic_results.json | json | 200 | 6e4f9c791875eb84bc9fcd33b743333b7964b8c39d4da04b64e121170eb1fa43 | C | KEEP | — | 0 |
| 519 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_04_R3/frozen_manifest_r3.json | json | 5,813 | 2d8e3b2bb6b676d5a66973521031ac2964b6d7ab09ae262cfa1d596598a2e204 | C | KEEP | — | 3 |
| 520 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_04_R3/revision.json | json | 809 | 12dacfda421d4d6a8cc3b6a7782d766a7a71a06ed6f89c3a8048d54b508cd26a | C | KEEP | — | 0 |
| 521 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_04_R3/source_snapshots/equity_low_beta_candidate_2026_09.py | python | 23,363 | 82c1c80ab68d4faf4508685989bef9999e141c87181f3cad0fc294b5eb8c1a31 | B | KEEP | — | 16 |
| 522 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_04_R3/source_snapshots/equity_low_beta_engine_2026_09.py | python | 20,262 | b2b80f2ada827da0b2eb48543dc253dd8e09c2e0568edc5247328dc6c17b2ceb | B | KEEP | — | 14 |
| 523 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_05_R4_data_feasibility/data_preflight_r4.json | json | 4,361 | a2827be4650293fe182eafad694434d6e08ddd8fee77c1302d6765065d9ddb0c | C | KEEP | — | 2 |
| 524 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_05_R4_data_feasibility/economic_results.json | json | 37,756 | 97e26032c6e82353adbc13fc0f9a6e1f81eb4ab1fb3358b039d1bace6355c35d | C | KEEP | — | 2 |
| 525 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_05_R4_data_feasibility/frozen_manifest_r4.json | json | 5,766 | f44d2042bcf3a5a1a4c65f8caa02a569606ce67cb3cbc6b686f2395d238cbf7f | C | KEEP | — | 4 |
| 526 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_05_R4_data_feasibility/frozen_manifest_r4_draft.json | json | 5,503 | 7691186299dcabc7f4529ea4e4bd5ced98321b3c2c3738bab1e44f1c173cdde4 | C | KEEP | — | 0 |
| 527 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_05_R4_data_feasibility/source_snapshots/equity_low_beta_candidate_2026_09.py | python | 24,935 | 13906b6cbb5bc00f25f5f6d94942bca1f8898cb25d1c4049a1769c05e7e292c4 | B | KEEP | — | 20 |
| 528 | data/research_runs/equity_low_beta_candidate_2026_09/attempt_05_R4_data_feasibility/source_snapshots/equity_low_beta_engine_2026_09.py | python | 20,262 | 7be4daf44d291cd4f22cb13b00aeb777c316e2bb069ddbffe3e0380224dddebd | B | KEEP | — | 10 |
| 529 | data/research_runs/equity_low_beta_candidate_2026_09/data_preflight.json | json | 3,864 | 059568494f1160f1b272fe82eff40397530d00cd1608982bc9d54b4dca01bacf | C | KEEP | — | 5 |
| 530 | data/research_runs/equity_low_beta_candidate_2026_09/economic_results.json | json | 516 | be73507c6120916fe87b5e285acd4ab2ae8111c6b17f375fa6374316dd77a650 | C | KEEP | — | 0 |
| 531 | data/research_runs/equity_low_beta_candidate_2026_09/frozen_manifest.json | json | 5,196 | e5e9aa6b9a21a086b9d5c2f3b5ad51cfae5e8c108eb1ba31b2ad4552346e9a3b | C | KEEP | — | 5 |
| 532 | data/research_runs/equity_low_beta_candidate_2026_09/revisions.json | json | 509 | fa280657a5e48adad05722a7e27506bde51d2c450621d54a02c5b6b21c413f18 | C | KEEP | — | 0 |
| 533 | data/research_runs/zero_wait_overlays_2026_09_10/daily_nav.csv | csv | 3,847,130 | f85ddc99c74f4b078700493ee7b557edc0786220457ad81367b24780efd114d8 | B | KEEP | — | 4 |
| 534 | docs/archive/research_2026_08/README.md | markdown | 12,039 | 4c26977f60032c6e21c91d2f493412b8513a1c654e5c2fa08ceca369e968d5f0 | C | KEEP | — | 13 |
| 535 | docs/archive/research_2026_08/crypto_reversal_validation.md | markdown | 6,421 | ceed4c3ff3c4c21f77ffe49b7f0ab1ef67792d6fcf6d70086dff1b970e5f9ffe | C | KEEP | — | 4 |
| 536 | docs/archive/research_2026_08/equity_alpha_optimization_2026_08.md | markdown | 24,370 | 5cd30acfdcde54634627516d2b688bf8dbe1d2486568b6c1feb82c2736a48f76 | C | KEEP | — | 2 |
| 537 | docs/archive/research_2026_08/multi_market_strategy_validation.md | markdown | 9,469 | f1130912745ae30db8612d78b278fd8f0e3f8890cf3db28dcfb6b7f569d2903c | C | KEEP | — | 5 |
| 538 | docs/archive/research_2026_08/optimization_experiments_2026_08.md | markdown | 16,451 | 79ba3879fc22d3c1d6513b8c4956da50d9608a8cf718ac0366fad124cc4a8188 | C | KEEP | — | 9 |
| 539 | docs/archive/research_2026_08/release_validation.md | markdown | 9,380 | 238e3f72b91bd2090976c771bf68c74f1787d1b38ca3cb6aac23db625e82dee2 | C | KEEP | — | 7 |
| 540 | docs/archive/research_2026_08/strategy_diagnosis.md | markdown | 16,256 | 33f428c45e2cc0f188c083af4023e154ca701f706c77626c0b07ec510118930b | C | KEEP | — | 5 |
| 541 | docs/archive/research_2026_08/strategy_validation_v2.md | markdown | 13,701 | 2bbc70cf9b6d1fd9ec66f55b8285a5c263b466403ee59161baf4f336f7e52e75 | C | KEEP | — | 5 |
| 542 | docs/archive/research_2026_08/survivorship_aware_dataset_2026_08.md | markdown | 22,974 | 76cd6a68f9c3a00f803160e143196a750f557c5bf1a2bd1207e1a6b086e34663 | C | KEEP | — | 5 |
| 543 | docs/archive/research_2026_08/v3_exit_latency_optimization_2026_08.md | markdown | 31,507 | dad75c14c60bc5172974a3fbd605e2589c9a6a8263fb5e3d76bc1cc55cb7eae2 | C | KEEP | — | 2 |
| 544 | docs/archive/research_2026_08/v3_position_risk_optimization_2026_08.md | markdown | 30,582 | b1b5295b84d223564561e911498eb737f3c402f8bef3d0e575d9cd02471c0c4b | C | KEEP | — | 2 |
| 545 | docs/archive/research_2026_08/v3_risk_tail_loss_optimization_2026_08.md | markdown | 44,976 | 898bed46324b9c2c75ad3b4a06987813256625ba161fc4ec9fe313ed1316fbb0 | C | KEEP | — | 2 |
| 546 | docs/research/README.md | markdown | 8,799 | 15544e984cc97d69471836443c02ef1f5d863ce829b5205341c8a77d727d6741 | A | KEEP | — | 7 |
| 547 | docs/research/candidate_feasibility_2026_09_05.md | markdown | 4,034 | 8543ca6474e4b108de5b5464792a2ca804a43a6aa0204ad6bbfa7c3b143232a3 | A | KEEP | — | 45 |
| 548 | docs/research/crossasset_execution_plan_2026_09_08.md | markdown | 5,126 | 50206c8f8c6d43eedada9590897504e953d00c622ee0c39d7ad7b55bcc176a9c | A | KEEP | — | 0 |
| 549 | docs/research/crossasset_internal_execution_ledger_2026_09_08.md | markdown | 18,883 | 420127f08dbcab676c238d7569762583c11c87e3eabfb2430fa7a8dbd34ac5e7 | A | KEEP | — | 1 |
| 550 | docs/research/crossasset_max_testing_protocol_2026_09_09_v3.md | markdown | 2,202 | b0f526c5d2785bad3171e45b910738c9fb97d333f8417396c40d96d13c213071 | A | KEEP | — | 0 |
| 551 | docs/research/crossasset_protocol_amendment_2026_09_09_v2.md | markdown | 3,108 | 17e32946c7dbfe4cbe5d1022e3f4642272dbb21cfd7f76e3500600ec3cf6f60a | A | KEEP | — | 0 |
| 552 | docs/research/crossasset_strategy_briefs_2026_09_08.md | markdown | 4,664 | 2439719d567ab954fc6b960688835423813102eb60695a284fd7f580cbf1f13f | A | KEEP | — | 0 |
| 553 | docs/research/crossasset_x02_x06_acquisition_protocol_2026_09_08.md | markdown | 2,576 | 71442d57a03c9fa39410e7e2cab068b7c41ac6950355a9df13b5fe3b30c7d1b5 | A | KEEP | — | 0 |
| 554 | docs/research/crypto_max_candidate_2026_09.md | markdown | 6,637 | 263dbf64e8c93a958b37848281c33b642470bdb7685448029e6bc4bf953be97d | A | KEEP | — | 18 |
| 555 | docs/research/crypto_max_economic_protocol_2026_09_05.md | markdown | 2,058 | e0d2fba1febb9932d4866e0baa055371c3afe4daeb05da77acbbc707228b89e8 | A | KEEP | — | 43 |
| 556 | docs/research/crypto_max_economic_protocol_2026_09_05.sha256 | sha256 | 123 | 5344e13b4bacd6373c5f53866926014c2aae3bbd99f8ebc217f5589329310daa | A | KEEP | — | 9 |
| 557 | docs/research/crypto_max_economic_protocol_draft_2026_09.md | markdown | 2,967 | 5a77b71af0fc1b79fce2752a2ce93478b9e012054141054d5c9dd5596f273f34 | A | KEEP | — | 9 |
| 558 | docs/research/crypto_queue_causal_protocol_2026_09_08.md | markdown | 4,919 | 27d031645935381f1c1a8d2df7ddf2c839758a9c447dc7f8762d4a6f94b8b646 | A | KEEP | — | 16 |
| 559 | docs/research/crypto_secondary_causal_gate_report_2026_09_08.md | markdown | 7,244 | 28141cba8560e4c4f1866dd7054e0efb39f2dbc3acf31a9157db7bf0392441c6 | A | KEEP | — | 8 |
| 560 | docs/research/crypto_secondary_causal_protocol_2026_09_08.md | markdown | 5,763 | 5f3132f746d160ef29f85af8bd4b229f0ccbb4bd360d3bcf7b3a7761ad5f8c8d | A | KEEP | — | 8 |
| 561 | docs/research/crypto_signed_risk_candidate_2026_09.md | markdown | 5,550 | 39e9d59c2ef483df40721ad8dee780cdd4a3e2caf03089a066147299e38c6fd3 | A | KEEP | — | 6 |
| 562 | docs/research/crypto_signed_risk_economic_protocol_2026_09_05.md | markdown | 2,296 | ff7b79076ba19fa393074a4f2736b1c0b99247c532aef970f2fea62784af6422 | A | KEEP | — | 15 |
| 563 | docs/research/crypto_signed_risk_novelty_protocol_2026_09_05.md | markdown | 2,266 | efc64485b243b3d120ad60625c90dd81d118a1d0e4859139d2d683424ef67000 | A | KEEP | — | 27 |
| 564 | docs/research/crypto_source_gap_audit_2026_09_09_r2.md | markdown | 2,723 | c2d485c4cc16ad2908b8104bc39de744e348424d462181a30c8a9b8abf0c471b | A | KEEP | — | 3 |
| 565 | docs/research/equity_h01_h05_causal_input_gate_2026_09_08.md | markdown | 7,264 | 8e8b2c591b805139c104993ae0603ee49d38c8d4f9c439df8d1045b39b07bf49 | A | KEEP | — | 0 |
| 566 | docs/research/equity_h01_h05_causal_input_gate_2026_09_09_r12.md | markdown | 2,279 | 774e41bbabc5feab84eb126cb4bad367678bfd4a7bcd0097f30c83d06e200a43 | A | KEEP | — | 8 |
| 567 | docs/research/equity_h01_h05_causal_input_gate_2026_09_09_r13.md | markdown | 2,515 | 54c74687140f3b666b51691152432f9e9caa7911d07144ca7adae366f6a0c4ba | A | KEEP | — | 8 |
| 568 | docs/research/equity_h01_h05_causal_input_gate_2026_09_09_r14.md | markdown | 2,515 | b938d63b5cb4e3bc26c27c2d8c78ff919883498f0d26356f10771720f99161d7 | A | KEEP | — | 8 |
| 569 | docs/research/equity_h01_h05_causal_input_gate_2026_09_09_r15.md | markdown | 2,715 | 01590e0d2025965c4735472d88217eb541859018185a77459a6f9921134b2d69 | A | KEEP | — | 8 |
| 570 | docs/research/equity_h01_h05_causal_input_gate_2026_09_09_r16.md | markdown | 2,715 | 7b103557a216950069b515349e6598888fbd7d98615e3828baab028a4c49bcf4 | A | KEEP | — | 8 |
| 571 | docs/research/equity_h01_h05_causal_input_gate_2026_09_09_r17.md | markdown | 2,715 | f266a21405bddad28d22d74a34c9f4f235f2ad12bac4642a9fb73d7d959ce4f0 | A | KEEP | — | 8 |
| 572 | docs/research/equity_h01_h05_causal_input_gate_2026_09_09_r18.md | markdown | 3,569 | fda54826008c51749220c2caabcabcc0fa4e7ca2ab37e35c8f968e699bcbdabd | A | KEEP | — | 8 |
| 573 | docs/research/equity_h01_h16_standalone_overlay_protocol_2026_09_09_v1.md | markdown | 11,370 | 4b5c64f6c1e9407f680cabcf20e4366a749c06e4a3b452f03abba7ad1c52ede5 | A | KEEP | — | 10 |
| 574 | docs/research/equity_h06_local_normalization_2026_09_09.md | markdown | 1,588 | 74f3cc278784b3f314a1dcb72fd9bce4aad97f70ee00f1915a0160401b251476 | A | KEEP | — | 2 |
| 575 | docs/research/equity_h14c_posthoc_capacity_protocol_2026_09_09.md | markdown | 1,278 | bf664232bd489224fc8e7c9859979e6355ebd9af5e1c195848d730cfb78f27f9 | A | KEEP | — | 2 |
| 576 | docs/research/equity_low_beta_candidate_2026_09.md | markdown | 6,881 | ac8b2132669e148e85a85a743d381da58da620046dc9d975898c0344579f99f0 | A | KEEP | — | 0 |
| 577 | docs/research/equity_low_beta_r4_provenance_audit.md | markdown | 1,348 | 996b483cef1aa1ae0650372bbccfc3abc96b78ef05fed71ab80f8dee673bbefa | A | KEEP | — | 1 |
| 578 | docs/research/equity_profit_protection_2026_09.md | markdown | 13,513 | 7be4913f9b8b18cc5a741a1987863184a5cd40dd850196875ca7d12043e8d936 | A | KEEP | — | 1 |
| 579 | docs/research/equity_profit_protection_2026_09_r2.md | markdown | 7,140 | f6f7ed0f1063242fbc08aad848be4f95075859584949b499315dc5ebdd93115b | A | KEEP | — | 12 |
| 580 | docs/research/equity_secondary_causal_gate_report_2026_09_08.md | markdown | 5,069 | 44c8bb079ab1cf0bdf9411a6d5be49e2a822aedeefc998ec6c02a62bc3b1f7d3 | A | KEEP | — | 8 |
| 581 | docs/research/equity_secondary_causal_protocol_2026_09_08.md | markdown | 4,886 | a01147e85f7b228391b978c3fd74322130aa7106b1d8064d00c8656b5f7291a8 | A | KEEP | — | 14 |
| 582 | docs/research/equity_secondary_source_gap_audit_2026_09_09_review_r2.md | markdown | 3,682 | 4a2172e4b890dbe1558d0eb1496804c3ea8ad5bc911df456182ac4ed8917c527 | A | KEEP | — | 0 |
| 583 | docs/research/execution_cost_transferability_p0_2026_09.md | markdown | 5,196 | d4189dc9645fd3590df986f03d1bea6edc32d17e93fcdabd3067d3f264c2981d | A | KEEP | — | 4 |
| 584 | docs/research/execution_cost_transferability_p1_paper_observation_2026_09.md | markdown | 6,481 | a8be8b58aadd8ca4a1c0713c353d481172dbc9cc6b54b710c7d6233cb1d93bdd | A | KEEP | — | 8 |
| 585 | docs/research/execution_model_robustness_backtest_2026_09.md | markdown | 2,073 | 7a570a2f6901010fc0ff675905632940ad16ec05a05f297aa55cea6d48c5bca5 | A | KEEP | — | 4 |
| 586 | docs/research/h14_turn_of_month_protocol_2026_09_06.md | markdown | 6,215 | e1acba9dd59e7397cc26ba9460cad2981022bc87ed066d9293d586ce9859480b | A | KEEP | — | 0 |
| 587 | docs/research/h14_turn_of_month_protocol_2026_09_06_r2.md | markdown | 8,288 | e410a11ac1b31d85abeac59866b39c8effc0a49dfeaf9a4328b341145eb776ce | A | KEEP | — | 16 |
| 588 | docs/research/h14_v3_daily_nav_comparability_2026_09_09.md | markdown | 4,188 | 485aa1c96b12772636ad023b59f8b52208263576afa824fe205520fa9e7440cf | A | KEEP | — | 2 |
| 589 | docs/research/high_proximity364_deep_assessment_protocol_2026_09_05.md | markdown | 25,093 | d0ed18e41d8382a0e1b5b956cb4c5f5212bf71e601b021a4ee4b69d609a0bb82 | A | KEEP | — | 10 |
| 590 | docs/research/high_proximity364_exposure_audit_2026_09_05.md | markdown | 5,678 | 5c80c862c35cc8b3dadc7d410592043620da0d8f44540b17f7151ca449c4d0ae | A | KEEP | — | 3 |
| 591 | docs/research/high_proximity364_exposure_audit_script_review_2026_09_05.md | markdown | 1,072 | 983d890579c97f0aa18f515a506efd37ad7b615a4a771c0c810ce68dfab00e93 | A | KEEP | — | 0 |
| 592 | docs/research/high_proximity364_final_assessment_2026_09_05.md | markdown | 9,523 | 459e6e95ebef487b33b24def2f1bf106040b258d04701093bfbeb1c0bdfad4da | A | KEEP | — | 2 |
| 593 | docs/research/high_proximity364_history_fixed_audit_2026_09_05.md | markdown | 3,913 | 1810fd1d798e6d78db574c33b9e791699117fc1dad7e4948fd783d981b2a769a | A | KEEP | — | 3 |
| 594 | docs/research/high_proximity364_independent_research_review_2026_09_05.md | markdown | 10,735 | 4f5bc3d528b0cf384c9990220007cd9b8fdff5a8c016c7ee1eabb06f7c758e29 | A | KEEP | — | 0 |
| 595 | docs/research/high_proximity364_mechanism_review_2026_09_05.md | markdown | 13,080 | a14701ec02a121f444ad8600481efbd6d8c67537a097f45605f81ebc01725a82 | A | KEEP | — | 0 |
| 596 | docs/research/high_proximity364_trace_review_2026_09_05.md | markdown | 3,185 | f1ca927b9bcfe218d718550eae1169cdd3051fa4107cfaa4ebe58acd94695764 | A | KEEP | — | 0 |
| 597 | docs/research/high_proximity364_trace_summary_2026_09_05.md | markdown | 3,839 | d38cc0a58d9cf2af6319b7496a2acca4e61838ff874c4323a7219c179b442ca7 | A | KEEP | — | 0 |
| 598 | docs/research/historical_root_test_compaction_2026_09_11.md | markdown | 14,628 | a2d2400cbc1f7c2e6df85372df16e5f0d419ba596f91b3851b7e1652eab65220 | A | KEEP | — | 1 |
| 599 | docs/research/historical_test_compaction_2026_09_11.md | markdown | 5,853 | ca4e650d11d883070b66f436d0fce88517b1f92f882d8e2d8c19206d9ae4ae85 | A | KEEP | — | 0 |
| 600 | docs/research/immediate_candidate_freeze_2026_09_09.md | markdown | 6,758 | b9e574d2532a2dd47e3707eccec0a7ef32d36a5349ebd352cd254adbefec0da0 | A | KEEP | — | 0 |
| 601 | docs/research/immediate_candidates_invalidation_2026_09_10.md | markdown | 2,045 | 95bb98f6675eed6f3637f437996700765b28a744b9c5852191d323cbb6c40495 | A | KEEP | — | 7 |
| 602 | docs/research/immediate_candidates_results_2026_09_09.md | markdown | 2,928 | f420941ffd51dc17846133a71233d694822bc00d455b272c61054b9cc7db8d6f | A | KEEP | — | 4 |
| 603 | docs/research/k11_signed_flow_candidate_brief_2026_09_08.md | markdown | 2,802 | c8585c47800f40ea1657a22585970339a2d793f0a7dc84b0100e11b9d2f82455 | A | KEEP | — | 16 |
| 604 | docs/research/portfolio_expansion_batch_2026_09_05_protocol.md | markdown | 10,337 | bd3d29a6ecc700fb70f0650f0a82e97be0d4129de6b7fe5164d4e422f2a6de02 | A | KEEP | — | 25 |
| 605 | docs/research/portfolio_expansion_batch_2026_09_05_report.md | markdown | 29,332 | 9e4b099d4a72ca28a0dab441ed794bce2ed4a58c1b5baa7a79de0344b8e26e0b | A | KEEP | — | 2 |
| 606 | docs/research/portfolio_expansion_batch_2026_09_05_review.md | markdown | 8,716 | 37a39505fffa991ce281935e33df5d6ced3b5da6de29348eebef387827df082a | A | KEEP | — | 0 |
| 607 | docs/research/portfolio_expansion_crypto_batch_2026_09_05.md | markdown | 17,488 | ae46106090980372c9d1d145d0be943480316e39be4e3a8a3b6f3482fc70ea60 | A | KEEP | — | 20 |
| 608 | docs/research/portfolio_expansion_crypto_diagnostic_2026_09_05.md | markdown | 4,639 | df92a2c5e19971d6e322d4d80a6d76bcbd2c112ad088fddd94b0ef5c8a97d0d5 | A | KEEP | — | 2 |
| 609 | docs/research/portfolio_expansion_crypto_diagnostic_audit_2026_09_05.md | markdown | 3,838 | 34298464f2278a889d4567395b27da60483ca8a67114f0060836aba8c6dc5958 | A | KEEP | — | 4 |
| 610 | docs/research/portfolio_expansion_crypto_screen_attempt02_review_2026_09_05.md | markdown | 5,986 | e77538baa88dae2881d200d899bcc5b8538c055cd29ba7b971bae67ba562ad30 | A | KEEP | — | 0 |
| 611 | docs/research/portfolio_expansion_crypto_screen_final_audit_2026_09_05.md | markdown | 3,744 | 099f324abb2bc06f20fb600535d2170eb6f3fd0a7f05679d1e3c7f8e22bbf94a | A | KEEP | — | 5 |
| 612 | docs/research/portfolio_expansion_crypto_screen_native_validation_2026_09_05.md | markdown | 2,539 | 5657759a019322289be5ef8aea99d9e07278d0324cf90c166b3c2176dcb9537d | A | KEEP | — | 4 |
| 613 | docs/research/portfolio_expansion_crypto_screen_native_validation_attempt02_2026_09_05.md | markdown | 3,009 | eda092a5f452c9189a736bc6f7fa9771e3016a146264f309766d394da7c126c5 | A | KEEP | — | 2 |
| 614 | docs/research/portfolio_expansion_crypto_screen_protocol_2026_09_05.md | markdown | 5,119 | 39213f8aabed1a1268f36179d4cabe9cb79e678274ba6a4317b2b14911543a52 | A | KEEP | — | 73 |
| 615 | docs/research/portfolio_expansion_crypto_screen_protocol_repair_2026_09_05.md | markdown | 817 | 7a73ae288e208aa218ea2bb391155ffb575040c64c436df5119eec9f546b8b75 | A | KEEP | — | 0 |
| 616 | docs/research/portfolio_expansion_crypto_screen_results_2026_09_05.md | markdown | 4,205 | 2d9a0f522d954cb1b97b85eacb93cf5f94a55a73b3744e5e4f3d9ee6a5f70ab6 | A | KEEP | — | 4 |
| 617 | docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md | markdown | 12,582 | 9df487f832a78df1d74e8bf2f12b1e076a734ba5d6e8e2f85a86423ec76c93c6 | A | KEEP | — | 28 |
| 618 | docs/research/r46_treasury_auction_unwind_tlt_v1_2026_09_10.md | markdown | 8,246 | 282c9f49283f7469f279473720f0ea33af2d9fee21f057332e8ccb1ba41eaa5a | A | KEEP | — | 0 |
| 619 | docs/research/rejected_v3_test_compaction_2026_09_11.md | markdown | 14,905 | 3f6bc66233d7c5ece39edc008adc0681a70d1591afc390333fa57bd09d9d6550 | A | KEEP | — | 0 |
| 620 | docs/research/research_script_lifecycle_audit_2026_09_11.md | markdown | 81,624 | e63e30feb5d028b175c4f0ee63a115e2dbef0bf3696ae0c9e08db6370d1db05c | A | KEEP | — | 1 |
| 621 | docs/research/strategy_candidate_shortlist_2026_09_05.md | markdown | 16,864 | 0e68a963c00b5eb347c7006e26811290979dd1953fe152b7b9968d1eb46395bb | A | KEEP | — | 45 |
| 622 | docs/research/strategy_queue_baseline_contract_2026_09_06.md | markdown | 10,885 | 00f17d432409521e5f507ba07e898882ca5e9173ee633a818c2a8fd25395c37e | A | KEEP | — | 1 |
| 623 | docs/research/strategy_queue_completion_report_2026_09_09.md | markdown | 23,906 | 5cb7f7151a90ef7e34b4520c67eeda72f4dcaecd3d2859452bf2c289722849bf | A | KEEP | — | 0 |
| 624 | docs/research/strategy_queue_crossasset_review_2026_09_06.md | markdown | 9,217 | 30d7e97b27955d91885c8336e1b9e10cdb8ae50e28617d4e4b6ee9ec67427143 | A | KEEP | — | 1 |
| 625 | docs/research/strategy_queue_crypto_review_2026_09_06.md | markdown | 26,873 | 1115b197d201dcee8f73d9f68e0844d57cb5d31468e351a95ca7bbf2ed21ed3d | A | KEEP | — | 8 |
| 626 | docs/research/strategy_queue_equity_independent_review_2026_09_06.md | markdown | 18,190 | 52b1dfe2e6e1a996b31862e0119e83934aead0ac4de5bb2811cbfb2c45cfb1be | A | KEEP | — | 14 |
| 627 | docs/research/strategy_queue_equity_review_2026_09_06.md | markdown | 22,068 | 99fd21f76bf44ede1a96f209216ec4e86fb235240748f356d16c0694238e3829 | A | KEEP | — | 22 |
| 628 | docs/research/strategy_queue_execution_ledger_2026_09_07.md | markdown | 10,127 | fdf8daa08b3740b049a157a21390f6e203706a732d7928a10ba844b86b729e49 | A | KEEP | — | 1 |
| 629 | docs/research/strategy_queue_full_report_2026_09_06.md | markdown | 17,888 | 0bbeb251c02bf3b2df00fbf0f02ca37ba6164e1abdd13a216d0e44d658d22191 | A | KEEP | — | 38 |
| 630 | docs/research/strategy_queue_h14_result_audit_2026_09_06.md | markdown | 10,777 | e172dc33b3be8e5a7fb36b3e5916265f00fbe28c83e9645cfcf8a2b3826da404 | A | KEEP | — | 1 |
| 631 | docs/research/strategy_queue_independent_audit_2026_09_06.md | markdown | 13,340 | c1f7478ebba1ca915097e712a00bdfa036acc1430adfe421087dc2c22c1eabf1 | A | KEEP | — | 0 |
| 632 | docs/research/strategy_queue_max_testing_report_2026_09_09.md | markdown | 29,820 | 157193191e6344ca5f5e34eafd7b13fb98d837cc8067b7b15ae08cb3a21238ae | A | KEEP | — | 0 |
| 633 | docs/research/strategy_queue_zero_wait_decision_2026_09_09.md | markdown | 1,804 | 0db464f8eca8fa1bf0f123477775965fecd2abce1945a087b42df76c5d5dcb7f | A | KEEP | — | 3 |
| 634 | docs/research/v3_baseline_no_persist_protocol_2026_09_08.md | markdown | 1,907 | 1c2663d8bf5b3a914199435260ddd8416d09a597a9dd3d7ec5c3d271f6a55a2d | A | KEEP | — | 11 |
| 635 | docs/research/x01_alpaca_acquisition_protocol_2026_09_08.md | markdown | 3,749 | a54c72f2c6eb940d6160cb91ec639ade76f36dfdf79e921ee027c5a2ea548cc8 | A | KEEP | — | 3 |
| 636 | docs/research/x01_maturity_bucket_approximation_protocol_2026_09_08.md | markdown | 1,093 | 61de33659736a16ae51a51e3e1b9bfbba3e6c26a8d5d529046e5c86659cb9cb0 | A | KEEP | — | 11 |
| 637 | docs/research/zero_wait_final_decision_2026_09_10.md | markdown | 2,640 | 8d4cd0c429625e0d7caec6e5cda05510897d8d10d1687ac66ecffc78aeb7f461 | A | KEEP | — | 4 |
| 638 | docs/research/zero_wait_overlay_freeze_2026_09_10.md | markdown | 8,941 | ebc6cbcfa210294a918f8e3d07ab4c9873aa35d71226fc999f2f8ebe1307504c | A | KEEP | — | 0 |
| 639 | docs/research/zero_wait_overlay_results_2026_09_10.md | markdown | 1,010 | e75691cc168d9884e75e89e1968dc57becf632e40a56bce619be70bf4a7c980c | A | KEEP | — | 13 |
