# M3 DATA GATE R3 — blocked result

## 1. Executive data verdict

M3_DATA_GATE_BLOCKED

## 2. Boundary status

- Corrective infrastructure verdict: `PASS`.
- `m3_candidate_ocp_status`: `NOT_RUN_OUT_OF_SCOPE`.
- `economic_mission_ready`: `false`.
- `next_required_gate`: `M3_DATA_GATE_COMPLETION` because the data gate is blocked.

No candidate-owned OCP, M3 economics, V3/P&L, alpha ranking, optimization,
promotion, PAPER, or LIVE work was run.

## 3. Commit lineage

The original R3 implementation chain was abandoned after a provider-shape
defect was observed:

- A3: `f56062b532b15891b6a88eeb4136ddb55ded1be3` (parent D2).
- B3: `c4022ae94aa4069893052f63c1700d9d73d2c399` (manifest-only child of A3).
- C3/D3: not created; A3/B3 is explicitly abandoned.

The mandated append-only recovery chain is:

- A4: `782dcb573504ef3fcfb9ac2c65254615eb4ce50a` (parent B3), fixing the
  acquisition parser’s use of the Alpaca `bars` map-key symbol.
- B4: `f2d89c0a434e2ec7133d91f158271e9c6bc510c1` (manifest-only child of A4).
- A5: `2f27ad3cd849a248e012c7f3dfb274db77a54493` (parent B4), fixing the
  same envelope-symbol omission in bundle self-validation.
- B5: `832fa015f4e59aa009d2a1079b9a0bab1866cbaa` (manifest-only child of A5).
- Result commit C5: the tracked blocked-result commit containing this report
  and compact receipts.

## 4. B3/B4/B5 manifest-only proofs

Each manifest commit changed exactly:

`data/research_manifests/equity_m3_data_gate_2026_09_r3.json`

B3 is a direct child of A3, B4 is a direct child of A4, and B5 is a direct
child of A5. The B5 verifier independently checks the abandoned A3/B3 and
A4/B4 topology and the A5/B5 critical Git blobs.

## 5. Files changed

Implementation/test changes were confined to the A3/A4/A5 commits described
above. The B3/B4/B5 commits changed only the R3 pre-result manifest. The
result commit adds only compact blocked receipts and this report. R1/R2
manifests, receipts, reports, configuration, and production/runtime code were
not changed.

## 6. Network acquisition statement

Only the authorized Alpaca Market Data API host and endpoint were contacted:

- host: `data.alpaca.markets`
- endpoint: `/v2/stocks/bars`
- symbol: `IWM`
- feed: `sip`
- fallback: none; no IEX or other provider request was made
- credentials: read from the local `.env` only; never printed or written to
  any artifact

The initial A3/B3 attempt failed closed with `response_schema_mismatch`. The
mission’s explicit recovery rule authorized an A4/B4 retry, which failed
closed with `bundle_validation_failure`. No further network request was made.

## 7. IWM raw bundle inventory

No raw bundle was promoted. Consequently page count, row count, first/last
timestamp, raw bundle digest, source-receipt digest, and page-chain digest are
all `NOT_AVAILABLE`. No raw page, continuation token, or credential appears
in a tracked output.

## 8. Reference inventory

| Symbol | Valid rows | First session | Last session | Rejected | Zero volume |
| --- | ---: | --- | --- | ---: | ---: |
| SPY | 2658 | 2016-01-04 | 2026-07-30 | 0 | 0 |
| IWM | 0 | — | — | 0 | 0 |
| QQQ | 2658 | 2016-01-04 | 2026-07-30 | 0 | 0 |

## 9. Calendar/source/evaluation completeness

- Calendar: NYSE via `pandas_market_calendars` `5.4.0`.
- Source range requested: 2016-01-01 through 2026-07-31; expected 2659 NYSE
  sessions.
- Evaluation end: 2026-07-30.
- IWM completeness: 0 observed / 2659 expected; failed.
- SPY and QQQ contain 2658 sessions through the evaluation end but lack the
  source-end 2026-07-31 row; the three-symbol source completeness gate cannot
  pass without IWM.
- No missing session was inferred, filled, interpolated, synthesized, or
  substituted.

## 10. Weekly coverage

- Full window: 552 requested decisions; 0 accepted; no feasible three-reference
  window; post-feasibility count is 0 because feasibility was never reached.
- Full initial warm-up: not observable in this run because IWM was absent;
  the corrected A3 warm-up semantics are covered by the regression suite.
- Recent window: 239 requested decisions; 0 accepted; no feasible window.
- Outage/stale/misaligned counts after feasibility: not applicable; no
  post-feasibility decision existed.

## 11. Composite dataset identity

`NOT_RUN_BLOCKED`: no verified IWM bundle existed, so no normalized sidecar or
composite identity was created.

## 12. Deterministic PIT population

`NOT_RUN_BLOCKED`: no composite IWM input was available. No candidate
population, full PIT snapshot, or PIT compact manifest was created. The A3
implementation and tests establish strict-prior timestamps, deterministic
ordering, reference exclusion (`SPY`, `IWM`, `QQQ`), and no observation alpha;
none of those proofs can substitute for the missing source bundle.

## 13. Numerical feasibility

`NOT_RUN_BLOCKED`: total matrix count `0`, pass count `0`, fail count `0`.
Rank, condition, singular-value, residual, and matrix-Merkle evidence were
not produced because PIT/reference prerequisites were unavailable. No
economic metric was calculated.

## 14. Mandatory gate table

| Gate | Status | Evidence | Consequence |
| --- | --- | --- | --- |
| commit_topology | PASS | B5 direct child of A5; abandoned chain pinned | topology accepted |
| implementation_blob_identity | PASS | A5 critical blobs match B5 and HEAD | implementation accepted |
| manifest_only_commit | PASS | B5 changed only the R3 manifest | pre-result contract accepted |
| clean_worktree_at_start | PASS | empty at B5 gate start | run accepted |
| configuration_identity | PASS | digest `c8ca8e7f0b8059fdf85aa874c905ad4294ae235053e66c68e132341efbd0195d` | config accepted |
| dependency_identity | PASS | NYSE calendar `5.4.0` | dependency accepted |
| acquisition_script_identity | PASS | A5-pinned acquisition blob | script accepted |
| canonical_artifact_identity | PASS | v5 bytes hash `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` | canonical input accepted |
| lifetime_identity | PASS | v5 lifetime bytes verified | lifetime input accepted |
| corporate_action_identity | PASS | v5 corporate-action bytes verified | CA input accepted |
| iwm_raw_bundle | FAIL | bundle missing after both attempts failed closed | data gate blocked |
| iwm_provider_contract | FAIL | no promoted source receipt | data gate blocked |
| iwm_pagination_chain | FAIL | no promoted pages/chain | data gate blocked |
| iwm_normalization | FAIL | no promotable normalized rows | data gate blocked |
| iwm_source_completeness | FAIL | IWM 0 / 2659 expected sessions | data gate blocked |
| reference_calendar | FAIL | three-symbol reference completeness failed | data gate blocked |
| reference_row_quality | FAIL | IWM has no accepted rows | data gate blocked |
| reference_common_sessions | FAIL | common session count 0 | data gate blocked |
| full_weekly_post_warmup_coverage | FAIL | no feasible full reference window | data gate blocked |
| recent_weekly_coverage | FAIL | no feasible recent reference window | data gate blocked |
| composite_dataset_identity | NOT_RUN_BLOCKED | requires verified IWM bundle | data gate blocked |
| deterministic_numerical_population | NOT_RUN_BLOCKED | PIT input was not supplied | data gate blocked |
| pit_reference_boundary | NOT_RUN_BLOCKED | PIT input was not supplied | data gate blocked |
| design_matrix_feasibility | NOT_RUN_BLOCKED | PIT/reference prerequisites absent | data gate blocked |
| reference_role_contract | PASS | references reserved; no execution state read | role contract accepted |
| receipt_forbidden_fields | PASS | `[]` | no economic fields present |

## 15. Commands and outcomes

- A3 focused gate/acquisition/PIT/M4 tests: `195 passed`.
- A3 full local pytest: `1371 passed, 11 skipped, 6 sandbox-only localhost
  bind failures`; the six failures passed when rerun with the required
  external permission.
- A3 static checks: mypy passed; Ruff check and format passed.
- A4 focused tests: `196 passed`; A5 focused tests: `197 passed`.
- B5 provenance validation: passed, including abandoned A3/B3 and A4/B4
  topology checks.
- A3/B3 acquisition: blocked, category `response_schema_mismatch`.
- A4/B4 acquisition: blocked, category `bundle_validation_failure`.
- B5 network-free verifier: produced `M3_DATA_GATE_BLOCKED`; self-digest
  `27b59d0b353449a5fe694c93a44c9e659e795ad5e825085007517c3c0cf0e4cf`.

## 16. CI outcomes

Local focused proof is green as listed above. R3 remote CI closure was not
observed: the corrective result was blocked and no remote push/closure was
performed. Therefore no Python 3.12/3.13/3.14 remote PASS is claimed.

## 17. Tracked and external artifacts

Tracked in the result commit:

- `data/research_receipts/equity_m3_iwm_acquisition_2026_09_r3.json`
- `data/research_receipts/equity_m3_data_gate_2026_09_r3.json`
- `docs/equity_m3_data_gate_2026_09_r3.md`

Not created because their prerequisites were absent:

- `data/research_manifests/equity_m3_composite_dataset_2026_09_r3.json`
- `data/research_manifests/equity_m3_pit_population_2026_09_r3.json`

External/local-only blocked diagnostics and the full generated gate receipt
remain ignored and untracked. No raw bundle, normalized sidecar, or full PIT
snapshot was retained/promoted.

## 18. Remaining blockers

The only actionable blocker is a successful, independently promotable Alpaca
SIP IWM raw bundle under a future authorized run. The two permitted attempts
in this execution both failed closed and the mission was not allowed to use a
fallback or synthetic substitute.

## 19. Explicit confirmations

- No fabricated or substituted IWM data.
- No forward fill, backward fill, interpolation, synthesis, or inference.
- No IEX fallback or alternate provider.
- No economic P&L or economic metrics.
- No alpha ranking or optimization.
- No candidate-owned OCP.
- No promotion or PAPER/LIVE change.
- No V3 re-freeze.
- No R1/R2 history rewrite.

M3_DATA_GATE_BLOCKED
