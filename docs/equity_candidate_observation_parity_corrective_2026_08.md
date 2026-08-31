# OCP-1 Corrective Observation-Basis Receipt

Date: 2026-08-31
Scope: corrective identity, execution-boundary, provenance, and receipt
checks only. No candidate economics, promotion, M3, M8, PEAD, composite, or
new alpha search was run.

## Decision

`OCP_CORRECTIVE_PARITY_PASS — LOCAL RECEIPT`

The live-code gaps identified by the GPT Pro review are closed and the pinned
corrective runner passed E1, E2, and the no-economics E3 C|C/X|C/X|X boundary
check. The receipt is evidence for parity correctness only; it is not an
economic or promotion verdict.

## Pinned repository topology

| Stage | Commit |
| --- | --- |
| Corrective base | `677274a29b882d8b60d63083370d89df0ecd2776` |
| Implementation | `584d1cd3b540cfdd9003a835b78b97f9a79393f1` (`research: close OCP1 provenance gaps`) |
| Pre-result manifest pin | `0c3ee157a19ff46b75270471c5008e293d1ec780` (`research: pin corrective OCP1 source`) |
| Implementation parent | `677274a29b882d8b60d63083370d89df0ecd2776` |

The receipt runner was executed in a clean detached worktree at the exact
implementation commit. The manifest was already committed and declared that
SHA before the run. The main checkout keeps the corrective commits locally;
`origin/main` remains at the earlier Commit B until explicitly published. No
corrective push was performed.

## GPT Pro findings re-anchored to live code

| Finding | Live status | Corrective action |
| --- | --- | --- |
| Falsy explicit observer could be discarded | Confirmed | `build_product_universe()` now selects an override with an explicit `None` check. |
| Actual execution market was not checked | Confirmed | `run_case()` now compares the actual execution object against the retained observer using the full identity, including market and parameters. |
| Observer reuse could pass | Confirmed | `PreparedWindow` retains the observer instance and `assert_strategy_identity()` rejects object reuse at the engine boundary. |
| Third-call parameter/non-strategy failures were uncovered | Confirmed | Boundary tests cover changed parameters, wrong market, observer reuse, and non-`PortfolioStrategy` execution. |
| Candidate artifact lacked basis provenance | Confirmed | Artifacts now record basis, observer identity, market, timeframe, parameter digest, universe hash, execution identity, and control basis; candidate-owned omissions fail closed. |
| Standard evaluator did not expose candidate factories | Confirmed | `evaluate_variant()` now accepts observer/execution factories and propagates them through full, fold, annual, recent, stress, and jackknife runs. |
| Runner only recorded its source commit | Confirmed | The corrective manifest requires exact implementation SHA and base parent; the runner rejects any other clean HEAD. |
| Raw receipts were not connector-visible | Confirmed | A compact canonical receipt is tracked under `data/research_receipts/`; large raw output remains ignored. |
| Tracked report remote wording was stale | Confirmed | The historical report now identifies its pre-publication timing and current remote state. |

## Corrective receipt

The independently inspectable compact receipt is
[equity_candidate_observation_parity_corrective_2026_08.json](../data/research_receipts/equity_candidate_observation_parity_corrective_2026_08.json).

Receipt facts:

- source and implementation commit: `584d1cd3b540cfdd9003a835b78b97f9a79393f1`;
- declared base and implementation parent: `677274a29b882d8b60d63083370d89df0ecd2776`;
- runner SHA-256: `f17f1d7b17a2fd6190e8b2c22fc13d4c21dbe001410eadb777dc4fced30ce3bf`;
- E1 canonical control and explicit V3 universe hashes: exact frozen
  `d51ab5c816f9614cf9508d5ab6529e4cd8cad9d7d1feb886b233a3ad3556a011`;
- E1 aligned snapshots: `2,656`, all mismatch counts `0`;
- E2 aligned snapshots: `359`, sequence/membership changes `86/86`, outside
  pool `0`, future-bar leak `false`, repeated hash exact;
- E3 exercised C|C, X|C, and X|X through the real preparation-to-engine
  boundary with a stub engine and `candidate_economics_run=false`;
- dirty paths before and after the pinned run: both `[]`.
- compact receipt canonical digest: `fde017f13487f0a6981ad35550340c0c43f3b3f0c0c61355ea00b4e0ddc327de`.

## Verification performed

- `.venv/bin/python -m pytest -q tests/test_candidate_observation_parity.py` —
  `14 passed`;
- full pytest with the six documented socket-bind tests deselected — `1164
  passed, 11 skipped, 6 deselected`;
- changed-file Ruff lint and format checks — PASS;
- `py_compile` for both research scripts — PASS;
- pinned corrective parity runner — PASS (E1/E2/E3; no candidate economics);
- tracked compact receipt JSON — valid and hash-addressed to the pinned run.

The repository-wide baseline limitations remain separate from this corrective
parity result: the existing full format check reports nine unrelated files,
strict `mypy src` reports two pre-existing unused-ignore findings, and six
localhost-bind tests require socket capability unavailable in the sandbox. No
candidate economic mission should start until those quality-gate limitations
are independently resolved or explicitly accepted by the operator.

## Governance

The corrective runner changes no registry, configuration, runtime, risk,
execution, terminal, or scanner policy. It does not read research artifacts
from runtime and it cannot auto-promote a candidate. This receipt closes the
OCP-1 parity/identity gap only; it does not authorize M3 data work or any
candidate return interpretation.
