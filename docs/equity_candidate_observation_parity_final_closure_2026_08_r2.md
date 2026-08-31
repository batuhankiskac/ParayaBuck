# ParayaBuck OCP-1 final closure — CI retest

## Final status

`OCP_CLOSURE_PENDING_REMOTE_CI`

The new local implementation, pinned retest run, compact receipt, and local
quality gates pass. Remote acceptance remains pending until the retest commit
is pushed and all GitHub-hosted matrix jobs are green.

## Starting receipt

- Repository: `git@github.com:batuhankiskac/ParayaBuck.git`
- Branch: `main`
- Original mission start: `31c3ecee1af1826239f36b9495cf9edfb61e5d39`
- Original tree: `f6eee3e0634754aa107c4d96669c72088ed957fb`
- Worktree before A4: clean
- Previous remote run: GitHub Actions run `33435975365` on Commit C

The previous remote run executed every visible quality/build/security step on
Python 3.12, 3.13, and 3.14, but full pytest reported three failures on each
version. Two product-backtest tests depended on the developer-only `.env` file
to pass Alpaca credentials; the third exposed that the runner's executor
context manager waited for already-running trials after a fail-fast result.

## A4 corrective implementation

Commit A4 (`80c9813dac3bd6fc955718cf9a8e084397ea8a30`) is a direct child of the
previous receipt Commit C and contains only quality/infrastructure changes:

- `ResearchRunner` now owns its `ProcessPoolExecutor` lifecycle explicitly.
  A fail-fast failure cancels pending futures and calls non-waiting shutdown;
  normal successful batches still join workers. This removes the accidental
  context-manager `shutdown(wait=True)` delay and preserves completion-order
  failure reporting.
- The two Alpaca download-path unit tests set low-entropy, non-production test
  credentials through `monkeypatch`, so they no longer read a private `.env`
  file. Their provider and eligibility calls remain deterministic fakes.

No strategy, product registry, scanner, risk, execution, PAPER/LIVE, terminal,
allocation, economics, or promotion behavior changed.

## B4 pre-result manifest and retest receipt

Commit B4 (`b1c87032d98cd3e5e66ecf0e0b446de58fa7dc70`) is a direct child of A4
and adds the new declared-before-results manifest
`data/research_manifests/equity_candidate_observation_parity_final_closure_2026_08_r2.json`.
The old A3/B3/C artifacts remain immutable. B4 pins A4 and its parent C, while
retaining the original base commit and canonical V3 hash.

The retest was run from B4 with a clean worktree:

- Run commit: `b1c87032d98cd3e5e66ecf0e0b446de58fa7dc70`
- Run parent / implementation: `80c9813dac3bd6fc955718cf9a8e084397ea8a30`
- Implementation parent: `11e67f45871343fe531e892f1915fb99b96920f9`
- Source tree: `24070fcc5f2219c36842b06f00c6a7048eedb4be`
- Implementation tree: `8443fd5c5b1087366849aee931e23682a95867c0`
- Manifest blob SHA-256: `b26c7963520efa5e340b79587c3ffbf2c93774152d296b550e2577d83e3e9e72`
- Runner SHA-256: `d65b3e2e65a72620b4bfed0be2dec46c4e7f0732863282c2634bf78822a42b60`
- Config semantic digest: `1833407d7c42fcef45b1f2aef200a3fd14e344d6cdcc9b03c578f8e494152a3e`
- Raw payload receipt digest: `5efa43688eff50a2e1fc0b8ede5fbcc5608c0238fbc8c71e4ccf0a81e930e79e`
- Raw receipt SHA-256: `640c2312080df79a4a7b870061155571117a14b1a8c35ccb2521bbec311bc503`
- Compact receipt self-digest: `83eac7558be1d968d2b06661bdb71eeebdff3c6183b9bf8364202e1792b804d7`
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
- Full local pytest after A4: `1179 passed, 11 skipped, 6 failed, 2 warnings`.
  The six failures are the same localhost socket-bind tests blocked by this
  sandbox; with those six explicitly removed, the suite is `1179 passed,
  11 skipped`. GitHub continues to run the full suite without deselection.
- Production-file isolation checks pass. Local gitleaks is unavailable, but
  the visible GitHub gitleaks step is configured and passed in the previous
  run.

## C4 remote gate

The paired C4 commit adds only this report and
`data/research_receipts/equity_candidate_observation_parity_final_closure_2026_08_r2.json`.
After C4 is pushed, remote acceptance requires all three Python matrix jobs to
finish green with full pytest (no deselection), isolation verification, build,
wheel smoke, pip-audit, and gitleaks. Until that observation is green, this
mission deliberately remains pending and makes no candidate-economic or
promotion claim.

`OCP_CLOSURE_PENDING_REMOTE_CI`
