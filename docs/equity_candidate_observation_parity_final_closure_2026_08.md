# ParayaBuck OCP-1 final closure

## Final status

`OCP_CLOSURE_PENDING_REMOTE_CI`

The local implementation, pinned run, compact receipt, and quality gates pass.
The final remote acceptance check is intentionally left pending until Commit C
is pushed and all GitHub-hosted matrix jobs are observed.

## Starting receipt

- Repository: `git@github.com:batuhankiskac/ParayaBuck.git`
- Branch: `main`
- Starting HEAD: `31c3ecee1af1826239f36b9495cf9edfb61e5d39`
- Starting tree: `f6eee3e0634754aa107c4d96669c72088ed957fb`
- Starting worktree: clean
- Previously observed CI blocker: Starlette `1.3.1` TestClient collection failed
  because the locked development environment did not install the optional
  `httpx2` package. This was reproduced in a clean Python 3.12 environment;
  no OCP strategy code was involved.

## Implementation and dependency changes

Commit A3 (`4dc42bd952b31dfc5868951b985be8a4716cd13b`) contains the final
implementation. It is descended from the starting snapshot through the
append-only corrective chain and adds:

- `httpx2>=2.12,<3` to the development extra only;
- a resolver-generated lock closure selecting `httpx2==2.12.0`,
  `httpcore2==2.12.0`, and `truststore==0.10.4`, while preserving the existing
  package pins and adding only required transitive packages;
- one shared bounded streaming SHA-256 helper that carries a trailing CR over
  chunk boundaries and preserves a final lone CR;
- fail-closed compactor checks for the exact E1/E2/E3 set, numeric/mismatch
  semantics, E3 identity parity and fresh-instance evidence, source ancestry,
  config provenance, and tracked versus explicitly external data provenance;
- CI isolation verification and downstream build/security steps with
  `if: always()` and no `continue-on-error`.

The new final pinned manifest is Commit B3
(`8bcdba7c67d6b8cbe2a462676d6fb7d65c788fb9`), a direct child of A3. Runtime,
registry, scanner, risk, execution, PAPER/LIVE, terminal, V3, and allocation
behavior were not changed.

## Provenance chain and receipt

The OCP run was executed from B3 with `HEAD^ == A3`; no tracked files changed
before or after the run. The raw JSON was written outside the tracked tree at
`/private/tmp/parayabuck-ocp-final-raw-v3.json` and compacted by the committed
compactor.

- Run commit: `8bcdba7c67d6b8cbe2a462676d6fb7d65c788fb9`
- Run parent / implementation: `4dc42bd952b31dfc5868951b985be8a4716cd13b`
- Implementation parent: `0f1af75dd58a53f0730c940d1b269aaa8290d9a0`
- Manifest blob SHA-256: `2ecc5c6df773f807b78a97d37bccb9267a6a43663a1e9808199e7d5c7f57d54d`
- Runner blob SHA-256: `d65b3e2e65a72620b4bfed0be2dec46c4e7f0732863282c2634bf78822a42b60`
- Config blob SHA-256: `414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353`
- Config semantic digest: `1833407d7c42fcef45b1f2aef200a3fd14e344d6cdcc9b03c578f8e494152a3e`
- Raw payload receipt digest: `b2ab942c3712943ea6ff98e96986c71983b94e7b6870732a80ea455c2f3e581b`
- Raw file SHA-256: `5d2bea79db564f08daf984b3537d38a9dfd8ccedacbeaa4e779c3c7071e05974`
- Compact receipt self-digest: `d634f09f4b9dd84e79d29aeebdd6bfe8c4ac65ef0d7a5b4bc23346be29aa2fc0`
- Compactor blob SHA-256: `92172e5b2a781b2ad24c8310663a892fb51bd1ec13b1fcdc992da00da49fb2e8`

The tracked lifetime CSV matches its pinned Git blob (`fe998bf8…`). The bars
and corporate-actions CSVs are intentionally ignored external inputs; the
receipt records `external_untracked`, raw SHA-256, normalized SHA-256, and the
absence of a Git tree blob explicitly rather than claiming a missing object.

## E1 / E2 / E3

- E1 canonical V3 self-parity: passed; control, explicit override, and expected
  canonical hashes are all `d51ab5c8…`; 2,656 aligned/control/candidate
  snapshots; every mismatch count and pin/capacity count is zero.
- E2 changed-ranker canary: passed; 359 aligned/control/candidate snapshots;
  86 membership changes and 86 sequence changes; candidate outside-pool count
  `0`; future-bar leak `false`; candidate and repeated universe hash both
  `8175f591…`; every scanner/pool/metadata/pin/capacity mismatch count is zero.
- E3 execution boundary: exact `C|C`, `X|C`, `X|X` entries; C|C and X|C use
  `observation_basis=configured`; X|X uses `candidate_owned`; observer and
  execution identities match including parameters and parameter digest;
  `fresh_instance_checks=true`; `candidate_economics_run=false`.

## Quality and security evidence

- Clean Python 3.12, 3.13, and 3.14 locked installations: TestClient import
  succeeded; focused web/OCP/hash/receipt tests: `42 passed` on each interpreter.
- Focused OCP/hash/receipt tests on the final checkout: `29 passed`; web
  security tests: `14 passed`.
- Ruff lint: pass. Ruff format check: pass (`315 files already formatted`).
- mypy: pass (`147 source files`).
- Required script `py_compile`: pass.
- Full local suite with only the six localhost-socket tests explicitly removed
  because this sandbox forbids bind: `1179 passed, 11 skipped, 6 deselected,
  2 warnings`. The GitHub workflow runs the full suite without deselection.
- Source and wheel build: pass. Installed-wheel web/OCP smoke import: pass.
- `pip-audit`: pass (`No known vulnerabilities found`; the local project is
  not published on PyPI and is reported as unaudited by name).
- Gitleaks is configured as a visible CI action; this restricted macOS
  environment has no local `gitleaks` binary.

## Commit C and remote gate

Commit C adds only this report and
`data/research_receipts/equity_candidate_observation_parity_final_closure_2026_08.json`.
After Commit C is pushed, GitHub Actions must be observed green for Python
3.12, 3.13, and 3.14, including full pytest, isolation verification, build,
wheel smoke, pip-audit, and gitleaks. No candidate economics or promotion
interpretation ran in this closure.

`OCP_CLOSURE_PENDING_REMOTE_CI`
