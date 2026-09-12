# ParayaBuck performance optimization — 2026-09-12

Status: implemented and verified in the working tree; no commit was created.

This pass targeted developer and agent validation latency, current research
iteration latency, and repeated-run backtest evidence. Strategy mathematics,
portfolio outputs, risk and execution semantics, PIT/universe behavior,
corporate-action handling, terminal handling, manifests, receipts, config,
PAPER, and LIVE behavior were kept out of scope.

## Baseline

Measurements were taken before the changes on the same checkout and with the
repository's strict markers enabled.

| Surface | Result | Pytest time | Wall time |
|---|---:|---:|---:|
| Collection | 1,460 tests | 1.18 s | — |
| Product (`-m "not research"`) | 1,028 passed, 432 deselected | 127.10 s | 127.70 s |
| Current research (`research and not historical_research`) | 432 passed, 1,028 deselected | 267.69 s | 268.34 s |
| R46 family (`tests/research/test_r46_*.py`) | 325 passed | 261.62 s | 262.21 s |

The product timing above is the host run. An unprivileged diagnostic run had
six loopback `PermissionError` failures; those were environment permissions,
not product failures, and were not used as the baseline.

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

## Implemented changes

### 1. Strict R46 replay cursors

`_StrictReplayStateCursor` and `_StrictReplayRiskCursor` were added to
`scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py`.

For the existing chronological decision-clock, event-contract, and rebalance
checks, the cursors advance state history, bar marks, TLT reference data, and
risk history once rather than rescanning all prior records for every query.
They cache immutable-by-convention snapshots but return fresh mapping copies,
matching the prior public mutability behavior. Backward or uncached queries
fall back to the original reference scan. This keeps the optimization local to
the strict verifier and preserves the fail-closed validation contract.

`tests/research/test_r46_v2_execution_2026_09_10.py` adds a regression test
covering forward, repeated, and backward state/risk queries against the
original reference functions.

### 2. Narrow test-harness latency reductions

These changes affect tests only; production code and production defaults are
unchanged:

- The middleware security test replaces an unnecessary real engine start with
  an injected recorder while still asserting the request/security branch.
- The auto-start retry transition test injects a zero-duration async wait;
  the separate backoff-bounds test continues to assert the exact
  `[5, 15, 30, 60, 60]` delays.
- The WebSocket ticket TTL test uses a 10 ms TTL and 20 ms wait while still
  exercising real expiry semantics.
- The crypto unconfirmed-stop test opts into a 30 ms test timeout; the broker
  default remains unchanged and the test still asserts the fail-closed
  ambiguous state and absence of a target order.

## Measured results

| Surface | Before | After | Change |
|---|---:|---:|---:|
| Product strict validation | 127.10 s | 104.92 s | 17.4% faster |
| Product wall time | 127.70 s | 105.60 s | 17.3% faster |
| Current research strict validation | 267.69 s | 250.88 s | 6.3% faster |
| R46 family | 261.62 s | 232.04 s | 11.3% faster |
| Isolated strict R46 fixture | about 219.10 s | 189.32 s | about 13.6% faster |

The final collection count is 1,461: 1,028 product tests and 433 current
research tests. The one-test increase is the replay-cursor parity regression
test, not a strategy or product-surface change.

The four directly shortened product tests completed in 1.89 s of pytest time
(2.37 s wall time) after the test-only changes; their prior combined duration
was about 20.3 s.

### Backtest output and representative timing

The final output-safe benchmark reused `benchmarks/bench_backtest_core.py`
helpers without overwriting its tracked JSON artifact. The final timings were:

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

The final cached four-trial runner benchmark remained process-overhead bound:

| Workload | Final median | Relative to final sequential |
|---|---:|---:|
| 1 worker, four trials | 6.8227 s | 1.00x |
| 2 workers, four trials | 9.6938 s | 0.70x |
| 4 workers, four trials | 12.1247 s | 0.56x |
| 1 worker, one trial | 3.4045 s | reference |

The final parent peak was 2,675.1 MiB. The reported upper-bound estimates
were 4,675.1 MiB for two workers and 6,373.2 MiB for four workers. Since
parallel execution remained slower and more memory-intensive, no runner
change was justified.

## Candidates measured and rejected

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

All of the following completed successfully after implementation:

- `.venv/bin/pytest --collect-only -q --strict-markers` — 1,461 collected.
- `.venv/bin/pytest -m "not research" --strict-markers --durations=100` —
  1,028 passed, 433 deselected.
- `.venv/bin/pytest -m "research and not historical_research" --strict-markers
  --durations=100` — 433 passed, 1,028 deselected.
- `.venv/bin/pytest -q --strict-markers tests/research/test_r46_*.py` — 326
  passed.
- R2, P0, P1, current V3 parity, and full survivorship/terminal focused gate —
  35 passed.
- `.venv/bin/python scripts/research/verify_all_34_closure_2026_09_09.py
  --verify` — 34 candidates, zero issues, status
  `ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS`, read-only.
- `tests/research/test_all_34_closure_2026_09_09.py` — 10 passed.
- `git diff --check` — passed.

No shared M3 helper was changed, so no additional M3 run was required by the
scope rule. The working tree contains only the five intended modified files
plus this report; no commit was created.
