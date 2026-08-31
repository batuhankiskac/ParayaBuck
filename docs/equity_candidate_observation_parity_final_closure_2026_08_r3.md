# ParayaBuck OCP-1 final closure — CI retest r3

## Final status

`OCP_CLOSURE_PENDING_REMOTE_CI`

The new local implementation, pinned retest run, compact receipt, and local
quality gates pass. Remote acceptance remains pending until the C5 commit is
pushed and all GitHub-hosted matrix jobs are green.

## Starting receipt and CI diagnosis

- Repository: `git@github.com:batuhankiskac/ParayaBuck.git`
- Branch: `main`
- Original mission start: `31c3ecee1af1826239f36b9495cf9edfb61e5d39`
- Original tree: `f6eee3e0634754aa107c4d96669c72088ed957fb`
- Worktree before A5: clean

The C4 remote run (`33438159602`) executed every visible quality/build/security
step on Python 3.12, 3.13, and 3.14. Its two Alpaca product-backtest failures
were fixed by the C4 hermetic-test change. The remaining failure was
`test_fail_fast_truthful_completion_order`: after C4 stopped waiting for the
executor, CI still spent about 1.4–1.6 seconds preparing the shared smoke
universe before discovering a deterministic invalid override.

## A5 corrective implementation

Commit A5 (`8535821324d9e3c1c57688cff8d9e83f5e08c7fe`) is a direct child of C4
and contains one research-infrastructure correction. When `fail_fast=True`,
each trial's execution overrides are validated before shared smoke data and
universe preparation. An invalid deterministic override now raises the same
trial-labelled `RuntimeError` immediately; normal execution and
`fail_fast=False` bookkeeping remain unchanged. No strategy, product registry,
scanner, risk, execution, PAPER/LIVE, terminal, allocation, economics, or
promotion behavior changed.

## B5 pre-result manifest and retest receipt

Commit B5 (`a9de719f2d237d81931d08fa75e75b665a754ad5`) is a direct child of A5
and adds the declared-before-results manifest
`data/research_manifests/equity_candidate_observation_parity_final_closure_2026_08_r3.json`.
The prior A3/B3/C, A4/B4/C4, and their receipts remain immutable. B5 pins A5,
its C4 parent, the original base commit, and the frozen canonical V3 hash.

The retest was run from B5 with a clean worktree:

- Run commit: `a9de719f2d237d81931d08fa75e75b665a754ad5`
- Run parent / implementation: `8535821324d9e3c1c57688cff8d9e83f5e08c7fe`
- Implementation parent: `ebfcda55716b0ee0768cc1819b9a34ca67c4596c`
- Source tree: `efb6da445d625e092b8f2b98c635cdb9a0c9fb82`
- Implementation tree: `3dcbb7745091e8bb2d17ae032396247719cff9eb`
- Manifest blob SHA-256: `79e0593072de45faa50dca9b6e9daab0e9b4e766ca643362665f2d2d9f4184d8`
- Runner SHA-256: `d65b3e2e65a72620b4bfed0be2dec46c4e7f0732863282c2634bf78822a42b60`
- Config semantic digest: `1833407d7c42fcef45b1f2aef200a3fd14e344d6cdcc9b03c578f8e494152a3e`
- Raw receipt SHA-256: `1f5f95dd308d215bb0cd1cf435e79f0085794663ef49a63bd5fd0650ec5b37dc`
- Compact receipt self-digest: `ca800a18dbed839c69c70c334861390382977b17ae5f90c34a238c3154cbf6ac`
- Compactor SHA-256: `92172e5b2a781b2ad24c8310663a892fb51bd1ec13b1fcdc992da00da49fb2e8`

The ignored bars and corporate-actions CSVs are explicitly recorded as
`external_untracked` with raw and CRLF-normalized SHA-256 values and no Git
blob claim. The tracked lifetime CSV and configuration match their pinned Git
blobs.

## E1 / E2 / E3 results

- E1 canonical V3 self-parity passed: 2,656 aligned snapshots, frozen
  canonical/explicit hashes `d51ab5c8…`, zero mismatch/pin/capacity counts.
- E2 changed-ranker canary passed: 359 snapshots, 86 membership changes and
  86 sequence changes, candidate outside-pool `0`, future-bar leak `false`,
  repeated candidate hash `8175f591…`, zero mismatch counts.
- E3 execution boundary passed with exactly `C|C`, `X|C`, `X|X`; observer and
  execution identities (including parameters and parameter digest) match,
  `fresh_instance_checks=true`, and `candidate_economics_run=false`.

## Verification evidence

- Clean locked Python 3.12/3.13/3.14 environments installed `httpx2==2.12.0`;
  TestClient import and focused web/OCP/hash/receipt tests passed (`42 passed`
  per interpreter).
- Related product-backtest and research-runner tests: `51 passed`.
- Ruff lint and format, mypy (`147` source files), py_compile, source/wheel
  build, installed-wheel smoke, and pip-audit all pass.
- Full local pytest after A5: `1179 passed, 6 failed, 11 skipped, 2 warnings`;
  all six failures are localhost socket-bind tests forbidden by this sandbox.
  With exactly those six environment-limited tests removed: `1179 passed,
  11 skipped`. GitHub runs the full suite without deselection.
- Production-file isolation checks pass. Local gitleaks is unavailable, while
  the visible CI gitleaks step is configured and passed in prior runs.

## C5 remote gate

The paired C5 commit adds only this report and
`data/research_receipts/equity_candidate_observation_parity_final_closure_2026_08_r3.json`.
After C5 is pushed, remote acceptance requires all three Python matrix jobs to
finish green with full pytest (no deselection), isolation verification, build,
wheel smoke, pip-audit, and gitleaks. Until that observation is green, this
mission deliberately remains pending and makes no candidate-economic or
promotion claim.

`OCP_CLOSURE_PENDING_REMOTE_CI`
