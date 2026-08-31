# OCP-1 Corrective Observation-Basis Receipt

Date: 2026-08-31

Scope: corrective identity, execution-boundary, provenance, receipt, and
repository quality gates only. No candidate economics, promotion, M3, M8,
PEAD, composite, or new alpha search was run.

## Decision

`OCP_CORRECTIVE_PARITY_PASS — PINNED LOCAL RECEIPT`

The GPT Pro findings were re-anchored to the live checkout. The corrected
runner passed E1, E2, and the no-economics E3 C|C/X|C/X|X boundary check. A
tracked compactor validated the raw payload, committed manifest blob, source
chain, runner bytes, and receipt digest before writing the compact receipt.
This is a parity-correctness result, not an economic or promotion verdict.

## Pinned repository topology

| Stage | Commit |
| --- | --- |
| Corrective base | `677274a29b882d8b60d63083370d89df0ecd2776` |
| OCP implementation and quality hardening | `57b7ad20d93fe5fa90348b6509fed8ca5471db3c` |
| First pre-result manifest pin | `33288a3be36bbf785c7689cf9c7b8f6ebb8b6b40` |
| Final implementation pin used by the run | `cf9d669d4fe383cbc0e4946e8bfde86616a09544` |
| Pinned run commit (direct child of implementation) | `d1b69c7ce35339ac3e41c9a8d803490ac986c981` |
| Post-run compactor validation fix | `28d0b75` (local ancestor of the tracked receipt) |

The runner was executed at the exact pinned run commit. Its manifest came
from that commit's Git blob; an external `--manifest` path is rejected. The
tracked receipt/document commit is intentionally local and has not been
published. At audit time `origin/main` still resolved to the earlier published
corrective receipt commit `37ed1882f833dd6313f00dd33aa165038f6811b3`.

## Findings re-anchored

| GPT Pro finding | Live status after this pass |
| --- | --- |
| Falsy observer override could be discarded | Closed; selection uses an explicit `None` check. |
| Actual execution identity/market was unchecked | Closed; the engine boundary compares fresh full identity. |
| Observer reuse could pass | Closed; retained observer state and fresh-instance assertion are enforced. |
| Candidate artifact lacked observation provenance | Closed; basis, identity, parameters, universe, and control provenance fail closed. |
| Runner/manifest provenance was not self-contained | Closed; the run requires `HEAD^ == implementation_commit`, loads the committed manifest blob, and records commit/tree/blob/command facts. |
| Lifetimes hash appeared to change | Refuted as a data mutation; the old value was a CRLF checkout hash and the new value is the LF-normalized Git-tree hash. The hash policy is now explicit. |
| Compact receipt had no machine link to raw evidence | Closed locally; the tracked compactor verifies the raw canonical digest and records both raw file and compact digests. The large raw file remains intentionally ignored. |
| Remote CI stopped at formatting | Local repository-wide Ruff format and `mypy src` now pass. A GitHub run for the new unpublished commits remains unverified until publication. |

## Receipt facts

The connector-visible receipt is
[equity_candidate_observation_parity_corrective_2026_08.json](../data/research_receipts/equity_candidate_observation_parity_corrective_2026_08.json).

- source/implementation commit: `cf9d669d4fe383cbc0e4946e8bfde86616a09544`;
- run commit: `d1b69c7ce35339ac3e41c9a8d803490ac986c981`;
- run parent: `cf9d669d4fe383cbc0e4946e8bfde86616a09544`;
- implementation parent: `33288a3be36bbf785c7689cf9c7b8f6ebb8b6b40`;
- source tree: `e81254f8afdfa71b0c1903d6977aae24ba0598c2`;
- implementation tree: `926d4e0042ce6405e28dca5c2bef3f10dbba4f12`;
- manifest path/commit/blob SHA-256: `data/research_manifests/equity_candidate_observation_parity_corrective_2026_08.json` / `d1b69c7ce35339ac3e41c9a8d803490ac986c981` / `4e799231afcfc2a16f7cecf16059ed9b38628b56ddba17bcb6a0730c5da9bb0b`;
- runner SHA-256: `da4fd1f2ff961ba4a866e0e567bc5a2d20cf5756e75a75bc7c5410e211eb7626`;
- compactor SHA-256: `ccfbfff23bddbaf3f34fc041e6727603723a23be91aa5bd27895bffa45c4733e`;
- raw payload canonical digest: `2a21fdab5034a68cb95cbe37353a2f0188e6654dc1186bbee4f251f4a19ccf13`;
- raw receipt file SHA-256: `1d54be04ae443185010bbec4308ce665ad3b4a304942598400bb0500134df876`;
- compact receipt canonical digest: `db9e74b619a81c1ac35714778d06d135c2271bbbdb5773856a9662b89d3b09dd`.

Dataset hashes use SHA-256 after CRLF-to-LF normalization. The lifetimes value
is therefore the committed-tree value `fe998bf81af1f705accf461f13a6e71fdcedb2d68a317c5aa2152833b4cad4f0`; the earlier R1 receipt's `ed675d...` was the same logical file read with CRLF checkout bytes.

## E1/E2/E3 results

- E1: `2,656` aligned snapshots; control and explicit V3 universe component
  both equal the frozen `d51ab5c816f9614cf9508d5ab6529e4cd8cad9d7d1feb886b233a3ad3556a011`; all mismatch counts and pin/capacity mismatches are `0`.
- E2: `359` aligned snapshots; sequence/membership changes `86/86`; outside
  pool `0`; future-bar leak `false`; candidate/repeated hash exact;
  Jaccard mean/min/p50/p95 `0.8083565459610027 / 0.2 / 1.0 / 1.0`.
- E3: C|C, X|C, and X|X reached the real preparation-to-engine boundary with
  a no-economics stub; fresh observer/execution identities matched and
  `candidate_economics_run=false`.
- dirty paths before and after the run: `[]` / `[]`.

## Verification performed

- focused parity/product/universe/V3/terminal tests: `99 passed`;
- full suite with the six documented sandbox socket tests deselected: `1164
  passed, 11 skipped, 6 deselected, 3 warnings`;
- `.venv/bin/ruff check .`: PASS;
- `.venv/bin/ruff format --check .`: PASS (`387 files already formatted`);
- `.venv/bin/mypy src`: PASS (`146 source files`);
- `py_compile` for both research/receipt scripts: PASS;
- `uv build --offline`: PASS (sdist and wheel);
- installed-wheel import smoke from `/private/tmp`: PASS (the package's
  expected non-repository security-scan warnings are not a build failure);
- pinned parity runner: PASS (E1/E2/E3, no candidate economics);
- tracked compactor: PASS (raw digest, source/manifest blob, runner bytes, and
  compact self-digest all verified).

The six localhost-bind tests remain an environment-specific limitation of this
sandbox; they were not silently converted into a product waiver. The remote
GitHub quality run for the new local commits must still be observed after an
explicit publish.

## Governance

The corrective changes do not alter registry, configuration, runtime, risk,
execution, terminal, or scanner policy. Runtime does not read research
artifacts and no candidate is auto-promoted. This receipt closes the OCP-1
parity/identity/provenance gap only; it does not authorize M3 data work or any
candidate return interpretation.
