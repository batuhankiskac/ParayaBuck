# M3 CANDIDATE-OWNED OBSERVATION PARITY GATE

## 1. Executive verdict

M3_CANDIDATE_OCP_GATE_BLOCKED

The clean B22 real-data replay passed all 42 local OCP gates. The compact
receipt is blocked only because remote CI/closure was not run under the
mission's no-network constraint. The broad local suite and isolated build
also could not be green in this sandbox; details are in sections 19 and 21.

## 2. Boundary

| Field | Value |
|---|---|
| R5 data-gate status | `M3_DATA_GATE_PASS` |
| OCP raw replay verdict | `M3_CANDIDATE_OCP_GATE_PASS` |
| Compact receipt verdict | `M3_CANDIDATE_OCP_GATE_BLOCKED` |
| Candidate economics run | `false` |
| Economic metrics present | `false` |
| Economic mission authorized | `false` |
| Economic mission run | `false` in raw OCP artifact; no economic path was invoked |
| Next required mission | `M3_CANDIDATE_OCP_GATE_REMOTE_CLOSURE` |

The compact receipt's sole blocked reason is `remote_ci:NOT_RUN_BLOCKED`.

## 3. Append-only recovery chain

Required D10/base: `f15ba8ffa2c752ba184cbc6eb655792d5387ee54`.

| Stage | Commit | Parent | Scope/result |
|---|---|---|---|
| A19 | `bb1eff793aab9324b65e41ac1c072748267e2371` | D10/base | OCP implementation, compactor, tests, fixture docs, CI and fail-closed runner guard |
| B19 | `d0868ed43d32e7e05c0ad7d82fdebb66404dd835` | A19 | Pre-result manifest only; superseded because the R5 PIT compact-manifest lookup used the superseded B18 tree blob |
| A20 | `d20ba6a637015340263b8b453a721cc70453086f` | B19 | Corrected R5 PIT compact-manifest lineage to the final C11 result tree |
| B20 | `ec017e2cfa5ebcf937f38a4294a60ffba36e51ba` | A20 | Manifest-only redeclaration; superseded because the gate still hard-coded A19's D10 parent |
| A21 | `1431c8b9cf043661844ff164f16bd89396e5aebf` | B20 | Bound topology validation to the manifest-declared implementation parent |
| B21 | `8fca202e19cae605caeaeddcba9ae98d7824a570` | A21 | Manifest-only redeclaration; superseded after local formatting validation found one unformatted source file |
| A22 | `cae97cf9e059c5c03d79469b29e84d47533119d6` | B21 | Formatter-only source correction |
| B22 | `4d612d9f8fc50a5d4911f2c37d74979c61af93be` | A22 | Final manifest-only pre-result boundary used for the clean replay |
| C22 | child of B22 | B22 | Local compact receipt and this report; the full commit SHA is recorded in the final handoff |
| D22 | not created | C22 | Remote closure not run because network access is prohibited |

No A19/B19 commit was amended or reused with changed implementation bytes.
Each defect recovery was appended and replayed against the same accepted
local R4/R5 bytes.

## 4. Manifest-only proof

Each pre-result manifest commit changed exactly:

`data/research_manifests/equity_m3_candidate_ocp_2026_09.json`

| Manifest commit | Verified parent | Changed paths |
|---|---|---|
| B19 `d0868ed43d32e7e05c0ad7d82fdebb66404dd835` | A19 | exactly the manifest path |
| B20 `ec017e2cfa5ebcf937f38a4294a60ffba36e51ba` | A20 | exactly the manifest path |
| B21 `8fca202e19cae605caeaeddcba9ae98d7824a570` | A21 | exactly the manifest path |
| B22 `4d612d9f8fc50a5d4911f2c37d74979c61af93be` | A22 | exactly the manifest path |

B22's declared implementation is A22, its declared implementation parent is
B21, and its implementation tree is
`e2d547d876cfb0d7c2b84fce16a6bf77ad7fd9c6`. The committed B22 manifest
matches the current `build_manifest()` output.

## 5. R5 upstream validation

The accepted upstream was independently validated without changing or
rerunning R5:

- data-gate verdict: `M3_DATA_GATE_PASS`;
- final R5 implementation A18:
  `e22421c2486a20e883286345dbb509bbcd5d2fc9`;
- final R5 manifest B18:
  `2f6ce2aaf347b9e0257d4ae31e9fdcc90cdcafe4`;
- final R5 result C11:
  `3c2f67d31772f2fba118137ad262bd8ffdb737d8`;
- D10/current accepted starting commit:
  `f15ba8ffa2c752ba184cbc6eb655792d5387ee54`;
- A18 actual parent: `459cf782754bc15d137fcaa4cdd081402b837f96`;
- R5 manifest blob: `b11aa6302e9edac11389d211f772a5b1d13dcc5d`;
- R5 receipt blob: `7096ce9bccd99edda2e1e8d7d150d4fffb578070`;
- R5 receipt self-digest:
  `41afb58aca2fb38f4f0d301fc169938809b1cb9afcfa5c9855d97e292916abeb`;
- accepted R5 C11 CI: `33630745082`, Python 3.12/3.13/3.14 PASS;
- accepted R5 D10 CI: `33631928797`, PASS;
- all accepted R5 mandatory gates: PASS;
- R5 blocked reasons: `[]`.

The final compact PIT manifest was resolved from the C11 result tree, with Git
blob `1290869c1d665254eb1981d648c2ef6d44edc9e5`. The superseded B18-tree
blob was not used.

## 6. Accepted R5 erratum

| Field | Value |
|---|---|
| Mislabeled field | `receipt.commits.implementation_parent` |
| Recorded value | `e22421c2486a20e883286345dbb509bbcd5d2fc9` |
| Authoritative value | `459cf782754bc15d137fcaa4cdd081402b837f96` |
| Handling | Recomputed from Git and required to match the R5 manifest and mandatory topology evidence |

The field is non-load-bearing because it is a redundant display alias. The
load-bearing sources are the R5 pre-result manifest, the actual Git parent of
A18, and the R5 mandatory `commit_topology` gate. The erratum is explicitly
present in the B22 manifest, raw artifact, and compact receipt.

## 7. Exact input identities

| Input | Identity |
|---|---|
| Canonical equity v5 raw | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` |
| Canonical equity v5 CRLF-normalized | `df42733b640e6dea17e1ed11115846933a86d2c525810dd3f473acc32e6d0db3` |
| Canonical manifest Git blob | `a366944ec26d6f9edff82e9e892fe397f3aed112` |
| Lifetimes raw | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` |
| Corporate actions raw | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` |
| IWM raw bundle | `9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258` |
| IWM source receipt | `2dbeeebc688dab111013a114e9c08b44c116e23c48a42968099f6c8671ea4003` |
| IWM aggregate page digest | `39a87290b4ebafbba3c29663f5577c05c7548252e6699e5f39589849211c93eb` |
| IWM page-chain digest | `a5b5e400bb97aff3f6342bbe0b969f5d0ae5600406d5cd186ec62e0360480e1b` |
| IWM sidecar raw | `8f8122e33d5ef2b194e22c7e53a15198920476690997535bbc5c05d522463dc2` |
| IWM sidecar semantic rows | `cc1855c607ee394fae9e5ede594d1420f0c4d03cb8a5af08e14e6a656cd7d837` |
| Composite semantic | `5bb66b7ff15d1f9ef3e9ce7caa4ef2314fcc670cf80b9696ee576e1b9cac4eda` |
| R4 source PIT raw / bytes | `bfdf6e3f87d5c42a1923bc4009f2fa1892ade7317e11caf139d30239bac3f5ae` / `24774560` |
| R5 evaluation PIT raw / bytes | `657f2d00716ca33dd4142f7bb6166fcd72f2344fad20678456c731b292fd77ac` / `25518170` |
| R5 projection digest | `7e1a5ee098e23397d8f4d77e7d73e79c8491c28b089854f9174a8c0049a7b240` |
| R5 candidate-universe digest | `2a0c5b987d40cefed46c4f2ded595e1759e13c8744e2bc003f2da4befac2106e` |
| R5 candidate-decision eligibility digest | `ab5ee2d5c1bc58f711f1961dff505563adf67c02d67917c1d067431136514a32` |
| R5 numerical matrix Merkle root | `99187e5e0d94348b87e6c89aae3612d4bbd40e3795e6844289daf740d18073d9` |
| R5 PIT compact-manifest Git blob | `1290869c1d665254eb1981d648c2ef6d44edc9e5` |

No input artifact was refetched, filled, forward-substituted, or replaced by
a new market-data source.

## 8. Strategy identity table

| Instance role | Name | Version | Market/timeframe | Parameter digest |
|---|---|---|---|---|
| C observer 1 | `equity_residual_momentum_v3` | `3.0.0` | equity / 1d | `d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa` |
| C observer 2 | `equity_residual_momentum_v3` | `3.0.0` | equity / 1d | `d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa` |
| C execution identity | `equity_residual_momentum_v3` | `3.0.0` | equity / 1d | `d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa` |
| X observer 1 | `equity_proxy_multifactor_residual_momentum_v1` | `1.0.0` | equity / 1d | `d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa` |
| X observer 2 | `equity_proxy_multifactor_residual_momentum_v1` | `1.0.0` | equity / 1d | `d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa` |
| X execution identity | `equity_proxy_multifactor_residual_momentum_v1` | `1.0.0` | equity / 1d | `d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa` |

Frozen parameters were formation 252, skip 21, beta window 273,
residual-volatility window 63, top N 8, maximum single weight 0.10,
idiosyncratic-volatility rejection quantile 0.70, exclusion of SPY, and
weekly rebalance. X uses the frozen residual formula
`r_stock - beta_spy*r_SPY - beta_size*(r_IWM-r_SPY) -
beta_growth*(r_QQQ-r_SPY)`, demeaned beta fitting, no explicit intercept,
alpha-retaining residuals, and the frozen skip semantics. Neither strategy
was modified.

All six objects were freshly constructed. C observer 1, C observer 2, and C
execution are distinct; X observer 1, X observer 2, and X execution are
distinct. Observer and execution identities match within C and within X.

## 9. Reference and capacity table

| Contract | Value |
|---|---|
| Observation-only references | `SPY`, `IWM`, `QQQ` |
| Reference pin order | `SPY`, `IWM`, `QQQ` |
| Additional configured pins | `SH`, `PSQ`, `DOG` |
| Effective full pin order | `SPY`, `IWM`, `QQQ`, `SH`, `PSQ`, `DOG` |
| Maximum stream symbols | `30` |
| Reference pin count | `3` |
| Additional reserved-pin count | `3` |
| Total reserved count | `6` |
| Effective candidate observation capacity | `24` |
| Candidate/reference intersection | empty at every snapshot |

SPY, IWM, and QQQ were present first in every observation set and were not
scanner candidates, strategy target candidates, executable symbols, or
economic-ranking members. Config/settings were not changed.

## 10. Snapshot and scanner-basis table

| Field | Value |
|---|---|
| Scanner | `EquityUniverseScanner` |
| Scanner version | `equity-scanner-v3` |
| Scanner config digest | `d8e06f54cd1a2fe0a201317fb6e3f4265470b2a10fe6e119ad81554128b33392` |
| Cadence | daily |
| Evaluation snapshot count | `2658` |
| First effective_at | `2016-01-04T05:00:00+00:00` |
| Last effective_at | `2026-07-30T04:00:00+00:00` |
| Explicit empty snapshots | `1` |
| Unique scanner candidates | `889` |
| Scanner candidate-universe digest | `2a0c5b987d40cefed46c4f2ded595e1759e13c8744e2bc003f2da4befac2106e` |
| Scanner decision-eligibility digest | `ab5ee2d5c1bc58f711f1961dff505563adf67c02d67917c1d067431136514a32` |
| Scanner candidate/decision pairs | `157970` |
| M3-not-warm pairs | `18253` |
| M3 matrix-eligible pairs | `139717` |
| Numerical passes/failures | `139717` / `0` |
| First full candidate-feasible weekly decision | `2017-02-06` |
| Minimum recent eligible depth | `271` |

The scanner-quality fallback was reconstructed from the exact composite bars,
not assumed empty and not alphabetically substituted. At all 2,658 snapshots,
candidate count, eligible membership, effective_at, latest-input timestamp,
scanner version, config digest, and reference roles matched the accepted R5
projection. History used timezone-aware strict-prior cutoffs and no current or
future bar.

## 11. C|C result

PASS locally. C is the unchanged production V3 control. The two independent C
observation bases matched semantically and by ordered Merkle root:

- semantic universe hash:
  `e1f0e2ee7420a9d43a8fe69e2048e4a5a3e135f3d3ffc5d2bb8b425fdb487eb1`;
- ordered observation Merkle root:
  `d6ec0d61b100ab527ee2978ddcd6210c09f866b03cc62637525c9ac4f2d6509a`;
- snapshot count: `2658`;
- warm-up/pins-only snapshots: `274`;
- scanner-quality-fallback-selected snapshots: `0`;
- full-capacity snapshots: `2384`;
- observer and execution identities equal; objects different;
- economics not run.

## 12. X|C diagnostic result

PASS as a diagnostic boundary only. The observation basis is the same-run V3
control basis; the execution identity is fresh M3. The identities are
intentionally different, the boundary is diagnostic-only, and no execution
engine or economic calculation was run. This boundary is not accepted as
candidate-owned X|X parity.

## 13. X|X result

PASS locally. X observer 1 and X observer 2 independently constructed the
candidate-owned basis, while X execution was a separate fresh identity
instance:

- semantic universe hash:
  `eb9b3df623a2fdcb5b02c9c174fbf629a355f4faf018df9989de8703f8907f6f`;
- ordered observation Merkle root:
  `cee257f5053570c2b5957adf5b6b4b8b2c0943273790b20135a24ed5ffbe568f`;
- repeated semantic hash and Merkle root were identical;
- snapshot count: `2658`;
- warm-up/pins-only snapshots: `274`;
- scanner-quality-fallback-selected snapshots: `0`;
- full-capacity snapshots: `2384`;
- observer and execution identities equal; objects different;
- economics not run.

## 14. Direct-builder parity

Nine declared samples all matched the direct `build_observation_set` result;
mismatch count was `0`.

| Sample | Index / effective_at | Observation count | Full/direct digest |
|---|---:|---:|---|
| first source | 0 / 2016-01-04 | 6 | `61c43cb981442ae7e6ad7c002622f1ebded4e81c598bdc4aae554c3f5b280fea` |
| last pre-M3-history warm | 273 / 2017-02-02 | 6 | `61c43cb981442ae7e6ad7c002622f1ebded4e81c598bdc4aae554c3f5b280fea` |
| first M3-history ready | 274 / 2017-02-03 | 30 | `8c93e7a1d005745adcc484ae8ea94afb9588406e26980e45c77f8618c3c98dfa` |
| first full candidate-depth weekly period | 275 / 2017-02-06 | 30 | `c6877927d3a114221823e5ffed64555f05c89e01551bbf835eb8c3d6d0d33508` |
| first recent window | 1511 / 2022-01-03 | 30 | `e2e1ffc8a51e8f8711fa6855d48388f0853b35fae5322373538c0bad1a1fce75` |
| DST transition | 48 / 2016-03-14 | 6 | `61c43cb981442ae7e6ad7c002622f1ebded4e81c598bdc4aae554c3f5b280fea` |
| scanner fallback use | 0 / 2016-01-04 | 6 | `61c43cb981442ae7e6ad7c002622f1ebded4e81c598bdc4aae554c3f5b280fea` |
| full candidate capacity | 274 / 2017-02-03 | 30 | `8c93e7a1d005745adcc484ae8ea94afb9588406e26980e45c77f8618c3c98dfa` |
| terminal evaluation | 2657 / 2026-07-30 | 30 | `9ff680f682613324cbf40c4d52801dd439ae3966b7fe94196e9364d04bcf298c` |

## 15. Determinism and future-bar checks

C and X repeated constructions both matched their semantic hashes and ordered
Merkle roots. Four X future-bar mutation boundaries were tested: first source,
DST transition, first recent window, and terminal evaluation. All four matched
baseline versus mutated observation digests; failure count was `0`.

## 16. C-versus-X observation-basis comparison

| Measure | Value |
|---|---:|
| Sequence changes | `2384` of `2658` |
| Membership changes | `2381` of `2658` |
| Mean Jaccard | `0.6991547456906374` |
| Minimum Jaccard | `0.25` |
| P50 Jaccard | `0.7142857142857143` |
| P95 Jaccard | `1.0` |
| C universe hash | `e1f0e2ee7420a9d43a8fe69e2048e4a5a3e135f3d3ffc5d2bb8b425fdb487eb1` |
| X universe hash | `eb9b3df623a2fdcb5b02c9c174fbf629a355f4faf018df9989de8703f8907f6f` |
| C Merkle root | `d6ec0d61b100ab527ee2978ddcd6210c09f866b03cc62637525c9ac4f2d6509a` |
| X Merkle root | `cee257f5053570c2b5957adf5b6b4b8b2c0943273790b20135a24ed5ffbe568f` |

All scanner metadata mismatch counts were zero: candidate count, configured
pins, eligible symbols, eliminated map, market, maximum capacity, methodology,
scanner config digest, scanner version, score breakdown, sector coverage, and
snapshot boundaries. The difference is reported only as observation membership
and order; it has no economic interpretation.

## 17. Mandatory gate table

All 42 mandatory OCP gates were `PASS` in the raw B22 replay. A FAIL, UNKNOWN,
or NOT_RUN_BLOCKED gate was not accepted by the local OCP runner.

| Gate | Status | Evidence | Consequence |
|---|---|---|---|
| commit_topology | PASS | B22 -> A22 -> B21; declared parent bound | no local topology blocker |
| implementation_blob_identity | PASS | A22 tree and critical blobs match B22 | no implementation drift |
| manifest_only_commit | PASS | B22 changed only the manifest | pre-result contract preserved |
| clean_worktree_at_start | PASS | `[]` | clean start proven |
| production_isolation | PASS | 10 protected paths; record/verify OK | protected production files unchanged |
| r5_data_gate_receipt_identity | PASS | C11 receipt blob and raw SHA match | accepted R5 retained |
| r5_data_gate_verdict | PASS | `M3_DATA_GATE_PASS` | upstream gate accepted |
| r5_all_mandatory_gates | PASS | every accepted R5 gate PASS | no upstream failure |
| r5_upstream_erratum_acknowledged | PASS | recorded and authoritative A18 parent separated | alias not trusted |
| r4_raw_bundle_identity | PASS | R4 bundle SHA matches | input retained |
| r4_sidecar_identity | PASS | sidecar raw SHA matches | input retained |
| r4_composite_identity | PASS | composite semantic digest matches | input retained |
| r5_projection_identity | PASS | raw SHA, bytes, projection/population digests match | exact projection used |
| scanner_identity | PASS | version/config/cadence match | scanner contract retained |
| scanner_pool_identity | PASS | candidate and eligibility digests match | R5 owns pool |
| scanner_fallback_identity | PASS | reconstructed from exact composite bars | no alphabetical fallback |
| exact_snapshot_alignment | PASS | `2658` snapshots | no missing/extra snapshot |
| reference_role_contract | PASS | references excluded from candidates | observation-only role retained |
| reference_pin_order | PASS | `SPY,IWM,QQQ,SH,PSQ,DOG` | pins first and ordered |
| observation_capacity | PASS | max 30, reserved 6, candidate capacity 24 | no excess |
| strictly_prior_history | PASS | timezone-aware latest input before effective_at | no current bar |
| no_future_bar_leak | PASS | four mutation samples matched | no future influence |
| no_outside_pool_observation | PASS | observation subset of pins plus R5 pool | pool boundary retained |
| no_duplicate_observation | PASS | all observation sets unique | no duplicates |
| no_stale_observation | PASS | ready candidates required latest aligned history | no stale candidate |
| C_control_strategy_identity | PASS | V3 factory identity and digest match | control frozen |
| C_control_repeat_determinism | PASS | C semantic hashes match | deterministic C |
| C_control_self_parity | PASS | C ordered Merkle roots match | deterministic C sequence |
| X_candidate_strategy_identity | PASS | frozen X identity and digest match | candidate frozen |
| X_observer_execution_identity | PASS | X identities equal | X|X identity valid |
| X_fresh_instance_separation | PASS | X observer/execution objects differ | no object reuse |
| X_repeat_determinism | PASS | X semantic hashes match | deterministic X |
| X_direct_builder_parity | PASS | 9 samples, mismatch 0 | direct path matches |
| X_candidate_owned_basis | PASS | X candidate-owned hash recorded | correct basis used |
| X_vs_C_scanner_metadata_parity | PASS | all mismatch counts zero | only basis changes allowed |
| X_vs_C_nonzero_observation_change | PASS | 2384 sequence, 2381 membership changes | M3 observer active |
| C_X_boundary_contract | PASS | C|C, X|C, X|X semantics exact | no ambiguous boundary |
| raw_artifact_identity | PASS | five raw components hashed | ignored evidence bound |
| compact_receipt_integrity | PASS | compactor validation passed | compact receipt produced |
| forbidden_economic_fields | PASS | forbidden key set absent | no economic output |
| no_economic_execution | PASS | engine calls 0; economics false | no P&L path |
| no_network_or_credentials | PASS | OCP replay reported network calls 0 and credential access false | OCP run stayed local |

The final compact receipt separately records `remote_ci.status` as
`NOT_RUN_BLOCKED`; this is why the receipt verdict is BLOCKED despite the raw
OCP gate map being all PASS.

## 18. Commands actually executed

The following commands were executed locally; no `--recent` or `--full`
economic scope was invoked:

```text
git rev-parse HEAD
git status --short --untracked-files=all
git log --oneline --decorate -40
.venv/bin/python scripts/check_production_isolation.py --record /tmp/m3-ocp-before-b22.json
.venv/bin/python -u scripts/research_equity_m3_candidate_ocp.py --manifest data/research_manifests/equity_m3_candidate_ocp_2026_09.json --r5-projection /tmp/m3-r5-b18-pit.json --raw-dir data/research/generated/equity_m3_candidate_ocp_2026_09
.venv/bin/python scripts/compact_equity_m3_candidate_ocp_receipt.py --raw data/research/generated/equity_m3_candidate_ocp_2026_09/ocp_run.json --output data/research_receipts/equity_m3_candidate_ocp_2026_09.json --remote-ci-status NOT_RUN_BLOCKED
.venv/bin/python scripts/check_production_isolation.py --verify /tmp/m3-ocp-before-b22.json
.venv/bin/python -m py_compile scripts/research_equity_m3_candidate_ocp.py scripts/compact_equity_m3_candidate_ocp_receipt.py
.venv/bin/pytest -q tests/test_research_equity_m3_candidate_ocp.py tests/test_candidate_observation_parity.py tests/test_candidate_observation_parity_receipts.py tests/test_research_equity_m3_r5.py tests/test_research_equity_m3_m4.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pytest
.venv/bin/python -m build
.venv/bin/python -m build --no-isolation
gitleaks detect --no-banner --redact (unavailable; command not installed)
git check-ignore -v <each generated raw path>
```

The R5 verifier and all local identity checks were read-only. No GitHub API,
push, broker, runtime, cache, position, or order path was used.

## 19. Local and remote CI

| Check | Result |
|---|---|
| py_compile | PASS |
| focused OCP/data/parity suite | PASS: `68 passed in 7.08s` |
| Ruff lint | PASS |
| Ruff format | PASS: `327 files already formatted` |
| mypy | PASS: `147 source files` |
| full pytest | BLOCKED: `1439 passed, 11 skipped, 6 failed` |
| source/wheel build, isolated | BLOCKED: temporary build env could not resolve `wheel` from PyPI |
| source/wheel build, no-isolation | BLOCKED: local `setuptools.build_meta` unavailable |
| gitleaks | NOT AVAILABLE: executable not installed |
| new remote GitHub Actions | NOT RUN: no-network mission constraint |

The six full-suite failures are pre-existing socket-bound tests: three webhook
tests and three web-bind tests failed with `PermissionError: [Errno 1]
Operation not permitted` on loopback bind. The broad suite teardown also
attempted an external Alpaca name resolution and failed closed; it was not
escalated or retried. The isolated build similarly attempted PyPI resolution
and failed closed. Therefore no local/remote green closure is claimed.

## 20. Tracked versus ignored artifacts

Tracked result artifacts are:

- `data/research_receipts/equity_m3_candidate_ocp_2026_09.json`;
- `docs/equity_m3_candidate_ocp_2026_09.md`.

The full raw artifacts remain untracked under
`data/research/generated/equity_m3_candidate_ocp_2026_09/`:

- `control_universe.json`;
- `m3_universe.json`;
- `m3_universe_repeat.json`;
- `comparison.json`;
- `snapshot_digests.json`;
- `ocp_run.json`.

Each raw path was verified by `git check-ignore -v` against the repository's
`data/research/` ignore rule. The superseded B21 compact receipt was moved
recoverably to `/tmp/m3-ocp-superseded-b21-receipt.json` before the clean B22
replay. R1-R5 artifacts, including the R5 manifest, receipt, report, R4
bundle, sidecar, composite, and PIT files, were not modified.

## 21. Remaining blockers

1. `remote_ci:NOT_RUN_BLOCKED`: no D22 remote closure can be created without
   violating the no-network rule.
2. The broad local pytest suite is not green in this sandbox because six
   pre-existing loopback-bind tests cannot bind sockets; its teardown also
   attempted external resolution.
3. The requested isolated build cannot install its missing build dependency
   without PyPI access, and the local no-isolation backend is unavailable.
4. `gitleaks` is unavailable locally.

These blockers prevent the executive PASS even though the real local B22 OCP
observation replay is complete and all 42 OCP gates are PASS. No economic
mission is authorized or launched.

## 22. Explicit confirmations

- The OCP implementation and B22 replay made no network or credential calls;
  the broad pytest/build quality probes attempted external resolution in
  pre-existing/tool paths, failed closed in the sandbox, and were not retried
  or escalated.
- No credentials were read.
- No IWM refetch occurred.
- No fabricated, substituted, forward-filled, or newly acquired data was used.
- No P&L or economic calculation was run.
- No raw alpha-score values were published.
- No parameter optimization or candidate tournament was run.
- No strategy promotion or registry activation occurred.
- No PAPER/LIVE behavior changed.
- No V3 re-freeze occurred.
- No prior-history rewrite occurred; strict prior cutoffs were preserved.
- No automatic economic mission was launched.

M3_CANDIDATE_OCP_GATE_BLOCKED
