# ParayaBuck performance correction pass — 2026-09-12

Status: correction complete and verified in the working tree; no commit was
created.

This pass restores frozen R46 source identity, keeps the safe product-test
latency reductions, and remeasures the same validation surfaces. Strategy
mathematics, portfolio outputs, risk and execution semantics, PIT/universe
behavior, corporate-action handling, terminal handling, manifests, receipts,
config, PAPER, and LIVE behavior remained out of scope.

## Frozen identity problem

The Phase 4 cursor prototype changed the frozen R46 verifier and execution
test in `aeaeac17234ebd728beb9695749792e62aadd8df`. The immutable governance
manifest and native attestation still pinned the prior verifier bytes, so the
canonical attestation failed with `ATTESTATION_PIN_MISMATCH:verifier_sha256`
even though ordinary fixture tests passed.

The manifest records source commit
`e10816ec08cc6b9c36000634c0e10a138d30f67e` and verifier SHA
`dbdcfb49476ced54ebda037efcc1766daeca38bbcc4d9ccd6b4bc3c7f5f545dd`. Its
`source_provenance.source_files` also records the execution-test SHA
`ac12f129685ccf8bb5fd120d6945bc83bdd17c7784c5db96ac5283b00333553e` as a
PINNED source identity. Both Git objects were hashed before restoration and
matched these contract values; the manifests and receipts were not changed.

## R46 restoration

Restored exactly from the contract-pinned Git source commit:

- `scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py` —
  `dbdcfb49476ced54ebda037efcc1766daeca38bbcc4d9ccd6b4bc3c7f5f545dd`
- `tests/research/test_r46_v2_execution_2026_09_10.py` —
  `ac12f129685ccf8bb5fd120d6945bc83bdd17c7784c5db96ac5283b00333553e`

The governance verifier SHA and native-attestation `verifier_sha256` both
equal `dbdcfb49476ced54ebda037efcc1766daeca38bbcc4d9ccd6b4bc3c7f5f545dd`.

## Baseline

The original Phase 4 baseline used the same checkout and strict markers.

| Surface | Result | Pytest time | Wall time |
|---|---:|---:|---:|
| Collection | 1,460 tests | 1.18 s | — |
| Product (`-m "not research"`) | 1,028 passed, 432 deselected | 127.10 s | 127.70 s |
| Current research (`research and not historical_research`) | 432 passed, 1,028 deselected | 267.69 s | 268.34 s |
| R46 family (`tests/research/test_r46_*.py`) | 325 passed | 261.62 s | 262.21 s |

The baseline product timing was the host run. An unprivileged diagnostic run
had six loopback `PermissionError` failures; those were environment
permissions, not product failures, and were not used as the baseline.

## Profiling evidence

Collection/import profiling was inexpensive (about 0.9 s collection and 1.33
s wall time), so collection/import refactoring was not pursued.

The strict R46 cProfile run identified repeated verifier replay work as the
largest safe optimization seam:

| Function/path | Cumulative time |
|---|---:|
| `verify_economic_run_from_files` | 298.791 s |
| `_strict_verify_ledger_sources` | 218.512 s |
| `_strict_validate_decision_clock` | 98.179 s |
| `oracle_expected` | 97.835 s |
| `_strict_replay_inner` | 68.359 s |
| `_strict_verify_statistics` | 50.727 s |
| `_strict_recompute_timing_placebo` | 43.902 s |

The same profile contained 1,730 subprocess calls (73.536 s cumulative),
including source/hash provenance checks. Those calls are part of the strict
evidence contract and were not removed or weakened.

The backtest engine profile processed 385,491 bars in about 0.398 s of engine
time. Its largest individual pieces were `_ingest` at 0.116 s and
`_ingest_execution` at 0.111 s. This was not a material low-risk optimization
opportunity, so the engine was left unchanged.

The research-runner benchmark also showed that the existing process-parallel
path is not a speed win for the cached four-trial workload. Before the change,
workers 2 and 4 achieved only 0.72x and 0.62x of the sequential timing and
used substantially more memory. No default or runner semantics were changed.

## Retained performance optimizations

The retained changes affect tests only; production code and production
defaults are unchanged:

- The middleware security test replaces an unnecessary real engine start with
  an injected recorder while still asserting valid-handler invocation and
  invalid-token rejection.
- The auto-start retry transition test injects a zero-duration async wait;
  the separate backoff-bounds test continues to assert the exact
  `[5, 15, 30, 60, 60]` delays.
- The WebSocket ticket TTL test uses a 10 ms TTL and 20 ms wait while still
  exercising real expiry semantics.
- The crypto unconfirmed-stop test opts into a 30 ms test timeout; the broker
  default remains unchanged and the test still asserts the fail-closed
  `AmbiguousOrderState`, manual-intervention state, and absence of a duplicate
  target order.

No production backoff constant, WebSocket default, crypto stop default,
engine source, or research verifier behavior was changed by these retained
optimizations.

## Measured results

These are fresh single final runs after the R46 restoration. The historical
baseline and final run were not repeated enough to justify a median; the
multi-minute R46 fixture is especially noisy, so changes below are descriptive
rather than a new optimization claim.

| Surface | Baseline | Final retained tree | Change vs baseline |
|---|---:|---:|---:|
| Product strict validation | 127.10 s | 108.10 s | 14.9% faster |
| Product wall time | 127.70 s | 108.78 s | 14.8% faster |
| Current research strict validation | 267.69 s | 280.22 s | 4.7% slower |
| Current research wall time | 268.34 s | 280.93 s | 4.7% slower |
| R46 family | 261.62 s | 267.43 s | 2.2% slower |
| R46 family wall time | 262.21 s | 268.02 s | 2.2% slower |

The product improvement remains present. Current-research and R46 final times
are close to the frozen baseline at this measurement scale and do not support
claiming a retained research optimization. The earlier 104.92 s product and
232.04 s R46 values were Phase 4 samples; the latter included the reverted
cursor prototype.

The final collection count is 1,461: 1,028 product tests and 433 current
research tests. The net increase is the new standalone frozen-pin guard: the
cursor parity test was removed from the frozen execution test and the guard
was added as a current-research test.

## Preserved backtest and parallelism conclusions

The prior output-safe benchmark reused `benchmarks/bench_backtest_core.py`
helpers without overwriting its tracked JSON artifact. These independent
measurements remain valid because no engine source changed:

| Case | Bars | Engine median | Product median | Batch of 4 | Fills | Ending equity | Fingerprint |
|---|---:|---:|---:|---:|---:|---:|---|
| Synthetic | 14,229 | 0.0049 s | 0.0063 s | 0.0248 s | 0 | 100,000 | `e7ae08d74b724940` |
| Equity 2019–2021 | 753,178 | 0.8226 s | 0.6349 s | 2.5059 s | 524 | 179457.60274873627 | `3fe1a8ebdeb69cec` |
| Equity 2019 H1 | 385,491 | 0.1529 s | 0.1654 s | 0.7406 s | 81 | 117096.00481896878 | `d533d18bb2c161b0` |

The baseline and final backtest runs produced the same fills, ending equity,
and fingerprints for all three cases. Engine timing varied between runs, and
there was no engine source change, so timing was not presented as a false
optimization result.

### Research-runner result

The cached four-trial runner benchmark remained process-overhead bound:

| Workload | Measured median | Relative to sequential |
|---|---:|---:|
| 1 worker, four trials | 6.8227 s | 1.00x |
| 2 workers, four trials | 9.6938 s | 0.70x |
| 4 workers, four trials | 12.1247 s | 0.56x |
| 1 worker, one trial | 3.4045 s | reference |

The measured parent peak was 2,675.1 MiB. The reported upper-bound estimates
were 4,675.1 MiB for two workers and 6,373.2 MiB for four workers. Since
parallel execution remained slower and more memory-intensive, no runner
change was justified.

## Candidates measured and rejected

- The strict R46 replay-cursor prototype improved the R46 family from 261.62 s
  to 232.04 s, about 11.3%, in the Phase 4 measurement. It was fully
  reverted because changing the verifier and its execution test violated the
  immutable R46 source identity. The cursor parity regression was not
  transplanted; the new standalone pin guard covers the actual failure mode.
- A target-oracle cache was prototyped and measured, then fully reverted. The
  local cache had zero hits across 6,961 target calls; a broader namespaced
  version improved the R46 family by only about 0.7%. It was not material and
  is not present in the final diff.
- Backtest-engine micro-optimization was rejected because the profiled engine
  path was already sub-second for the representative 385k-bar case and would
  have increased semantic risk without a measured gain.
- Runner worker-count/default changes were rejected because workers 2 and 4
  were slower and used more memory for the repeated cached workload.
- Provenance subprocesses, source hashes, strict statistics, PIT gates, and
  evidence checks were retained; no validation gate was loosened.

## Verification gates

The following completed successfully after the correction:

- `.venv/bin/pytest --collect-only -q --strict-markers` — 1,461 collected.
- `.venv/bin/pytest -m "not research" --strict-markers --durations=100` —
  1,028 passed, 433 deselected in 108.10 s; the loopback-enabled wall time was
  108.78 s. The unprivileged attempt had only the six known loopback
  `PermissionError` failures and was not used as the authoritative result.
- `.venv/bin/pytest -m "research and not historical_research" --strict-markers
  --durations=100` — 433 passed, 1,028 deselected in 280.22 s; wall time
  280.93 s.
- `.venv/bin/pytest -q --strict-markers tests/research/test_r46_*.py` — 326
  passed in 267.43 s; wall time 268.02 s.
- `.venv/bin/pytest -q --strict-markers
  tests/research/test_r46_frozen_source_pins_2026_09_12.py` — 1 passed.
- `PYTHONPATH=. .venv/bin/python -m
  scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10
  data/research_receipts/r46_treasury_auction_unwind_tlt_v2_2026_09_10_native_attestation.json
  --attestation` — `VERIFIED_NATIVE_LEDGER`, `fill_count: 2`, no rewrite.
- R2, P0, P1, current V3 parity, full survivorship/terminal, and all-34 test
  focus — 62 passed in 244.01 s; wall time 244.51 s. The command was
  `.venv/bin/pytest -q --strict-markers --include-historical-research` with
  `tests/test_research_equity_m3_candidate_ocp_attestation_r2.py`,
  `tests/research/test_execution_cost_transferability_p0_2026_09.py`,
  `tests/research/test_execution_cost_transferability_p1_paper_observation_2026_09.py`,
  `tests/test_equity_v3_current_parity_control.py`,
  `tests/test_full_survivorship_terminal.py`,
  `tests/test_terminal_aware_engine.py`, and
  `tests/research/test_all_34_closure_2026_09_09.py`; no one-shot economic
  mission was run.
- `.venv/bin/python scripts/research/verify_all_34_closure_2026_09_09.py
  --verify` — 34 candidates, zero issues, status
  `ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS`, read-only.
- `.venv/bin/ruff check` on the seven touched Python files — all checks passed.
- `.venv/bin/ruff format --check` on the five mutable touched Python files —
  all already formatted. A check including the two frozen R46 files reported
  that it would reformat them; no formatter was applied because that would
  change their required bytes.
- `git diff --check` — passed.

No shared M3 helper was changed, so no additional M3 run was required by the
scope rule. The final Phase 4/4.1 surface contains only the files below; the
three retained product-test changes were already present in the existing
`aeaeac1` commit. No new commit was created.

## Files changed

- `benchmarks/performance_optimization_2026_09.md`
- `scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py`
- `tests/research/test_r46_v2_execution_2026_09_10.py`
- `tests/research/test_r46_frozen_source_pins_2026_09_12.py`
- `tests/conftest.py`
- `tests/test_auto_start_retry.py`
- `tests/test_crypto_universe.py`
- `tests/test_web_security_invariants.py`

The R46 governance manifest and native attestation remain byte-for-byte
untouched.
