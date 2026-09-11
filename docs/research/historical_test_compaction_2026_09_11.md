# Historical test compaction — 2026-09-11

Phase 2A cleanup record. No commit was created by this cleanup.

## Scope

- Source HEAD before Phase 2A: `3ea2d5920426d8b240f8cf9012a636b1e1095d6d`.
- Closed H/K/X 34-card program only: H01-H16, K01-K12, and X01-X06.
- Historical intermediate implementation tests under `tests/research/` only.
- No production/current suite or later research family was changed.

## Canonical replacement

- Closure test: `tests/research/test_all_34_closure_2026_09_09.py`
- Closure verifier: `scripts/research/verify_all_34_closure_2026_09_09.py`
- Closure record: `data/research_runs/all_34_research_closure_2026_09_09/closure_record.json`
- Closure summary: `data/research_runs/all_34_research_closure_2026_09_09/closure_summary.md`

The frozen closure status is
`ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS`: 34 unique candidates,
zero economically eligible candidates, zero local engineering gaps, no
economic run, zero admissions and integration candidates, no portfolio
contribution, no promotion, and research-only status.

## Deleted modules

The following 25 historical modules were deleted:

- `tests/research/test_crossasset_crypto_overlay_diagnostics_2026_09_09.py`
- `tests/research/test_crossasset_max_testing_verdict_matrix_2026_09_09.py`
- `tests/research/test_crossasset_preflight_2026_09_08.py`
- `tests/research/test_crossasset_public_route_exhaustion_2026_09_09.py`
- `tests/research/test_crossasset_receipt_verifier_2026_09_08.py`
- `tests/research/test_crossasset_retrospective_diagnostics_2026_09_09.py`
- `tests/research/test_crossasset_x01_acquisition_2026_09_08.py`
- `tests/research/test_crossasset_x01_treasury_2026_09_08.py`
- `tests/research/test_crossasset_x01_yahoo_2026_09_08.py`
- `tests/research/test_crossasset_x02_cme_retry_2026_09_10.py`
- `tests/research/test_crossasset_x02_x06_2026_09_08.py`
- `tests/research/test_crypto_capacity_aware_diagnostics_2026_09_09.py`
- `tests/research/test_crypto_max_diagnostics_2026_09_09.py`
- `tests/research/test_crypto_max_testing_2026_09_09.py`
- `tests/research/test_crypto_source_gap_audit_kraken_2026_09_09.py`
- `tests/research/test_equity_h01_h05_causal_input_gate_2026_09_08.py`
- `tests/research/test_equity_h01_h05_price_security_retry_2026_09_09.py`
- `tests/research/test_equity_h01_h05_sec_filing_evidence_2026_09_09.py`
- `tests/research/test_equity_secondary_source_gap_review_r2.py`
- `tests/research/test_h14_current_stage_semantics_2026_09_09.py`
- `tests/research/test_h14_turn_of_month.py`
- `tests/research/test_strategy_queue_completion_report_2026_09_09.py`
- `tests/research/test_strategy_queue_contract_2026_09_09.py`
- `tests/research/test_strategy_queue_max_testing_aggregate_2026_09_09.py`
- `tests/research/test_strategy_queue_zero_wait_decision_2026_09_09.py`

The exact audit found no retained executable Python import or reference, no
current manifest/receipt path or hash pin, and no unique final-state invariant
not already covered by the closure contract. These modules were
mission-specific acquisition, source-gap, preflight, retry, causal-adapter,
normalization, diagnostic, max-testing, retrospective, or queue machinery
superseded by the frozen closure. The canonical verifier still consumes the
retained frozen scripts and artifacts; no implementation was copied into the
closure test. Mentions in immutable result records or reports remain historical
provenance and were not edited.

## Assertions extracted

None. The deleted modules contained intermediate checks or duplicate final
status checks. The final-state assertions remain in the closure test/verifier,
and the frozen evidence and scripts remain untouched.

## Intentionally retained H/K/X tests

- `tests/research/test_all_34_closure_2026_09_09.py`: the final closure
  contract; it remains historical and is excluded from default collection.
- `tests/research/test_crypto_queue_causal_adapters_2026_09_08.py`,
  `tests/research/test_k11_signed_flow_candidate_2026_09_08.py`, and
  `tests/research/test_crypto_secondary_causal_gate_2026_09_08.py`:
  manifest/receipt pins and closure-component verification dependencies.
- `tests/research/test_equity_h01_h16_max_testing_verdict_2026_09_09.py`,
  `tests/research/test_equity_h01_h16_protocol_2026_09_09.py`,
  `tests/research/test_equity_h14c_posthoc_2026_09_09.py`,
  `tests/research/test_equity_local_diagnostics_2026_09_09.py`, and
  `tests/research/test_h14_v3_daily_nav_comparability_2026_09_09.py`:
  immutable manifest/receipt path or hash pins.
- `tests/research/test_equity_h06_local_normalization_2026_09_09.py`,
  `tests/research/test_equity_secondary_causal_gate_2026_09_08.py`,
  `tests/research/test_equity_secondary_local_normalization_2026_09_09.py`,
  and `tests/research/test_h11_nport_snapshot_2026_09_09.py`:
  manifest/gate artifact pins and/or test paths hashed or consumed by the
  retained closure component verifiers.

## Untouched evidence and boundaries

All `scripts/research/**`, research manifests, research receipts, canonical
`data/research_runs/**`, canonical data/results/reports, `src/parayabuck/**`,
and configuration files were left untouched. Existing immutable historical
reports and result records were not rewritten. No production/current tests,
M3/V3 root historical tests, later research families, or promotion behavior
were changed.

## Git history

The deleted source is recoverable from the Phase 2A source commit
`3ea2d5920426d8b240f8cf9012a636b1e1095d6d` and normal Git history. No commit
was created for the working-tree cleanup.

## Collection effect at audit time

- Before: 72 historical modules / 1,077 historical items.
- Deleted: 25 modules / 213 historical items.
- After: 47 historical modules / 864 historical items; the all-included
  collection was 2,324 items, with 1,460 active items deselected from the
  historical selection.
