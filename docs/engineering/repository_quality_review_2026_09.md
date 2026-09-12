# ParayaBuck engineering quality review — September 2026

**Review date:** 2026-09-12
**Repository:** `batuhankiskac/ParayaBuck`
**Baseline:** `1769c7aea2c8ae0f9c747202176d5b78105551b1`
**Delivery:** local, uncommitted patch; no GitHub changes.
**Status:** first operational safety/reliability batch, **not completed repository-wide cleanup or release approval**.

## Scope and evidence standard

The supplied maintenance brief and repository `AGENTS.md` govern this work. Repository root/source inventories, project configuration, current research instructions, selected R46 source-pin records, operational source files, consumers and existing backup tests were inspected through the connected GitHub repository. Source inspection was pinned to the baseline above, not a moving `main`. Early search results at its parent were reconciled: the final commit changed only the R46 deep workflow and research README.

This environment did not have the user's local checkout, uncommitted changes, a complete repository clone, research datasets or its installed application environment. GitHub write actions create commits, which the brief forbids. Therefore this delivery contains a patch and evidence rather than edits to the remote repository. No branch, commit or pull request was created, and no user working tree was changed.

Six downloaded source-module texts and the existing backup test file were reconstructed locally and verified byte-for-byte against their Git blob SHA-1 identities; SHA-256 values are recorded in the companion `source_identity.json`. Four source modules were changed. Supporting `core/models.py`, `logger.py`, and existing `tests/test_backup.py` were not changed. The initial ranked audit was recorded before editing in `audit_before_editing.md`.

The local tests use real target modules and real SQLite for database fixtures, with explicit fault injection and fake external stream objects. They do **not** invoke package `__init__.py`, repository `tests/conftest.py`, Alpaca SDK constructors, the runtime, a broker or real credentials. This source-only namespace-package harness is **isolated module evidence, not the repository's accepted test environment**. Production import/startup behavior remains unverified.

A `.codegraph` directory was present remotely but contained only a tracked `.gitignore`; no usable local index, CLI or connected CodeGraph tool was available. No index was generated. No independent reviewer/subagent was available: the diff received self-review, tests and AST checks, not independent sign-off.

## Ranked findings

Severity is engineering judgment; observed module behavior is distinguished from production impact.

| Priority | Finding / affected file | Severity and confidence | Production risk | Fix complexity / expected payoff | Disposition |
|---|---|---|---|---|---|
| 1 | Unescaped SQLite URI in `backup.py:verify_backup` | High; real SQLite reproduction | Wrong or nonexistent requested file can be reported healthy; unintended file creation | Low / restores exact-path, read-only validation | Fixed in patch |
| 2 | Inner compensation failures in `data/alpaca_stream.py` never set `rollback_failed` | High; adapter fault-injection reproduction, runtime consumer found | Unknown subscription membership can bypass the existing unknown-state signal | Low–moderate / makes established safety signal reliable | Fixed in patch |
| 3 | Failed backup leaves an empty discoverable final file | High; real SQLite reproduction on baseline **and candidate** | `latest_verified_backup` can select that failed snapshot instead of a valid older one | Moderate–high / needs publication, ownership and crash-consistency design | **Unresolved** |
| 4 | Nonfinite cap and future-issued signed approval in `live_safety.py` | Medium; model/loader reproduction | Invalid authorization semantics; not a demonstrated bypass of all LIVE gates | Low / strict validation, valid payload unchanged | Both fixed in patch |
| 5 | SQLite startup read-back failures escape cleanup in `storage.py` | Medium; real connection fault injection | Connection ownership leak and inconsistent exception contract | Low–moderate / one cleanup boundary | Fixed in patch |
| 6 | Destination open/close failures skip source cleanup in `backup.py` | Medium; connection fault injection | Nondeterministic connection/file-handle cleanup on errors | Low / structured ownership | Fixed in patch |
| 7 | Failed last close consumes ownership reference in `storage.py` | Medium; first-close fault injection | Later release cannot retry cleanup | Low / consistent retry state | Fixed in patch |
| 8 | Stock stop error skips crypto stop in `alpaca_stream.py` | Medium; fake stop-method reproduction | Remaining stream may not receive a stop request | Low / attempts both cleanups | Fixed in patch |
| 9 | Backup filename `.exists()` check is not an exclusive reservation | Potential high; static race window, no concurrency reproduction | Concurrent writers may choose the same destination | Moderate / must be solved with publication protocol | Deferred with priority 3 |
| 10 | Oversized `runtime.py` and `cli.py` | Maintenance finding; byte sizes observed | Increased change blast radius; size alone is not a defect | High / requires responsibility and call-graph analysis | No speculative refactor |

The patch addresses **eight defect classes**. The two approval defects share one table row; open/close acquisition variants share one ownership defect class. Parameterized failure counts must not be interpreted as distinct bug counts.

## Major bugs fixed

### Exact-path backup verification

The old URI was `f"file:{Path(path).resolve()}?mode=ro"`. Literal `?` and `#` could reinterpret the filename and the read-only suffix; percent sequences could identify another path. Tests demonstrated corrupt/missing requested files returning `True`, and unintended empty databases appearing. A valid decoy also demonstrated a percent-decoding identity error.

The fix uses `Path(path).resolve().as_uri()` before appending `?mode=ro`. Positive, corrupt and missing-path cases cover special characters, ordinary paths, spaces and Unicode, including the invariant that verification creates no file. This fixes URI identity, **not** the separate empty-snapshot publication defect.

### Subscription rollback signal

Four inner compensation handlers previously logged errors without updating the existing latch. The outer handler only sees fully completed markets, so it cannot reliably represent a failed partial rollback. The private helper is now an instance method and sets `_rollback_failed = True` in those four handlers. Original errors still propagate; successful rollback does not set a false alarm; a failure latch remains set until explicitly cleared.

The actual consumer in `runtime.py` checks `rollback_failed` before its unknown-subscription-state response. Only that source path was inspected; its full runtime integration test was not run. The misleading unconditional rollback guarantee in the method docstring was corrected. This does not claim a transactional guarantee for external sockets.

### Approval validation

`LiveOperatorApproval` now requires a finite, positive exposure cap. `load_verified` also rejects `now < approved_at`, while preserving the existing exclusive expiration boundary. Tests cover NaN/infinities, finite caps, microsecond boundaries, timezone-equivalent instants, signature and identity binding. Signing serialization and HMAC calculation are unchanged.

These are defense-in-depth fixes. Configuration already validates a finite configured cap, and the runtime combines the configured/approved cap. No claim is made that these model defects alone permit unauthorized live trading. No live activation was attempted.

## Reliability fixes

`backup_database` now owns both acquired connections through `contextlib.closing`, ensuring source cleanup when destination acquisition or cleanup fails. Explicit commit, WAL snapshot normalization, fsync, verification and pruning sequence are retained; a SQLite connection transaction context was not substituted for a closing context.

`SQLiteStore.__init__` now encloses setup and read-back checks in one cleanup boundary. Read-back SQLite exceptions become `StorageInitializationError` with their original cause. Invalid journal mode, foreign keys, busy timeout and synchronous values remain rejected. Cleanup does not replace the primary initialization exception. A broad outer catch is used only for cleanup followed by rethrow/domain wrapping, not to continue after failure.

`release()` decrements the final reference only after close succeeds, allowing a failed close to be retried. Normal shared ownership, redundant release and rejection of acquire-after-close are tested.

`AlpacaBarStream.stop()` uses `finally` to attempt the second stop even if the first raises. Both-stop failures remain observable through exception context. No claim is made that requesting stop guarantees worker termination.

## Simplifications

SQLite initialization's five repeated connection-close guards became one ownership boundary. Backup connection lifetime is now expressed with the standard library rather than partially covering acquisitions with a trailing `finally`. No generic resource framework or new production module was introduced.

## Dead code removed

None. Whole-repository import, dynamic-dispatch, CLI registration, subprocess and immutable-reference analysis was not available to justify deletion.

## Duplication reduced

Four duplicate initialization cleanup guards were eliminated. Similar economic, research-canonicalization and manifest code was not consolidated: semantic and frozen-identity equivalence was not established.

## Performance improvements

None claimed. No production profile, workload benchmark or economic-output comparison was run. Test runtimes are execution evidence only, not evidence of a production speedup.

## Large modules/refactors

Repository tree metadata reports `runtime.py` at **243,519 bytes** and `cli.py` at **104,287 bytes**. Neither was structurally refactored. `backtest/portfolio_engine.py` is **84,478 bytes** and is explicitly referenced by R46 provenance; it was left untouched. Byte counts are not a substitute for measured complexity, call graphs or coverage.

## Tests added/changed

Four new operational test files contain **63 parameterized cases**:

- `tests/test_maintenance_backup.py`: exact path identity, corruption/missing paths, acquisition and cleanup failures.
- `tests/test_maintenance_storage.py`: read/setup failures, invalid pragma values, primary-cause preservation, reference lifecycle.
- `tests/test_maintenance_live_approval.py`: cap, time and signed-identity boundaries using synthetic identities.
- `tests/test_maintenance_stream.py`: failed/successful inner rollback, cross-market compensation, latch lifetime and stop failure combinations.

The repository's existing `tests/test_backup.py` contributes **10 unchanged cases**. Its Git blob identity was checked before running it. No existing assertion or test was weakened, deleted, skipped or marked expected-fail. New tests use temporary directories, small fixtures and no production-duration waits. They are operational, not research tests; the patch does not change research markers, classification or shared research fixtures.

The separate publication diagnostic intentionally exits nonzero when the unresolved defect is observed. It is included in the evidence bundle with **FAIL_UNRESOLVED** results, not disguised as a passing regression test or hidden with an expected-failure marker.

## Files removed

None.

## Before/after metrics

Physical line counts below include comments and blank lines; they are not executable-statement counts or whole-repository totals. Added/removed counts use `git diff --numstat`.

| Modified production module | Before | After | Added | Removed |
|---|---:|---:|---:|---:|
| `backup.py` | 241 | 240 | 6 | 7 |
| `storage.py` | 195 | 180 | 36 | 51 |
| `live_safety.py` | 137 | 141 | 6 | 2 |
| `data/alpaca_stream.py` | 247 | 254 | 13 | 6 |
| **Total** | **820** | **815** | **61** | **66** |

Net production change: **−5 physical lines**. Functions/classes removed: **0**. Production files created/removed: **0/0**. Four test files add **535 physical lines**. No coverage percentage or cyclomatic-complexity reduction was measured. Test-file size is reported separately and is not presented as a repository LOC reduction.

## Behavior/provenance preserved

The patch modifies only four operational source files, adds four operational test files and adds this new engineering report. Strategy mathematics/selection, portfolio economics, fee schedules, risk/allocation configuration, order/fill algorithms, point-in-time universe logic, corporate actions, terminal treatment and research conclusions were not edited. Invalid-input/error-path behavior changes are intentional bug corrections described above. End-to-end economic parity was not tested or claimed.

No frozen manifest, receipt, attestation, source-pin record, closure record, historical report or research implementation/test is changed by the patch. Targeted path searches found no immutable pin references to the four selected operational modules; selected R46 provenance was inspected. This is **not exhaustive transitive-pin verification**. R46 attestation, all-34 closure and M3 assurance remain unrun and required before acceptance. Fast test results are not closure evidence.

No dataset, research output, PAPER order or LIVE order was generated. The original remote repository and any uncommitted work remain untouched.

## Validation results

Environment: **Python 3.13.5**, **SQLite 3.46.1**, **pytest 9.0.2**, Linux source-only harness.

| Check | Result | Limit |
|---|---|---|
| Seven baseline file blob/SHA-256 identity checks | PASS | Selected snapshot files only |
| Initial new-case baseline | 23 failed, 25 passed / 48 | Recorded before edits |
| Expanded baseline, 63 new + 10 existing | **24 failed, 49 passed / 73** | Desired regressions expose baseline defects |
| Patched snapshot, same 73 cases | **73 passed** | Isolated modules, not complete repository |
| Strict-marker snapshot collection | **73 collected** | Standalone config, not repository conftest |
| Python 3.12 grammar AST checks and Python 3.13 compile | PASS | Six source modules and four new test files |
| Diff/patch whitespace and apply/reconstruction checks | PASS | Exact local baseline copy only; see companion validation log |
| Unresolved publication diagnostic | **FAIL_UNRESOLVED on baseline and candidate** | Empty failed snapshot chosen as latest verified |
| Ruff and Ruff format | **NOT RUN: tool unavailable** | Attempted commands fail with missing module, not lint verdicts |
| mypy | **NOT RUN: tool unavailable** | Attempted command fails with missing module, not a type verdict |
| Full repository collection/non-research/current/deep suites | **NOT RUN** | Complete checkout/environment unavailable |
| R46 frozen pins/attestation; all-34; M3; production isolation; V3/terminal; execution costs; profit-protection R2 | **NOT RUN** | No assurance/closure claim |
| Independent review, production profiling, economic parity | **NOT RUN** | Self-review and focused tests are insufficient substitutes |

The 73 passing cases do not cancel the unresolved diagnostic or pending gates. **Do not treat this package as production-ready.**

## Changes investigated but rejected

**Blind deletion of a failed backup path.** The final name is chosen with an availability check, not an exclusive ownership reservation. Adding `unlink()` on every exception could remove another writer's file in a race. A safe fix needs a separately tested staging/publication and ownership protocol, not just an exception-handler addition.

**Large runtime/CLI decomposition.** No broad structural change was made without integration gates and stronger responsibility/call-graph evidence. File size alone does not justify churn.

**Frozen research simplification or pin updates.** No evidence was regenerated to make a refactor pass. Existing fast/current/deep test architecture remains intact.

**Global exception narrowing and stylistic modernization.** Existing unrelated normalization semantics were preserved rather than mixing unproven behavior changes into this batch.

## Remaining high-value technical debt

1. **Fix verified backup publication before asserting recovery safety.** The offline diagnostic uses a tiny valid prior backup and a corrupt source. The failing operation leaves a zero-byte final `.sqlite3`; integrity verification returns true for that empty database, and `latest_verified_backup` selects it. Address unpublished staging, exclusive final-name ownership, validation before discoverability, file/directory durability, interrupted-write recovery and concurrent writers together. Keep prior verified backups on every failure. The current patch does not solve this.
2. **Complete accepted integration/provenance validation.** Run the commands below in the actual repository environment and run each relevant R46/all-34/M3/production assurance surface read-only. Add the independently reviewed publication fix and rerun the unresolved diagnostic. Do not regenerate evidence, enable LIVE or rerun economic research.
3. **Continue the repository-wide audit.** Inspect execution-ledger transactions/idempotency, runtime restart/thread/task ownership, web security/control boundaries, cache/resource lifetimes, semantic duplication and dynamic/CLI dependencies before safe deletion or structural changes. These areas are not certified by this limited batch.

Required repository gates, unchanged from the brief:

```bash
pytest --collect-only -q --strict-markers
pytest -m "not research" --strict-markers
pytest -m "research and not historical_research and not research_deep" --strict-markers
pytest -m "research and not historical_research" --strict-markers
ruff check .
ruff format --check .
mypy src
git diff --check
```

Use the repository's documented selectors/invocations for R46 frozen pins and canonical attestation, all-34 closure, M3 retained contracts, V3/terminal parity, execution-cost contracts, profit-protection R2 and production isolation. None is replaced by the companion harness. An independent reviewer should verify failure-path changes, private-helper callers, source pin boundaries and evidence quality before acceptance.

## Companion evidence

The downloadable evidence bundle includes exact baseline texts and identities, candidate source, the new and unchanged tests, pre-edit findings, reproduction runners, before/after pytest logs and JUnit XML, AST/diff metrics, unavailable-tool logs, patch validation and the unresolved diagnostic. It is not a complete checkout and is not intended to run or deploy the application.
