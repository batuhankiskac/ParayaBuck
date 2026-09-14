# Current Research Records

This is the current human index and lifecycle policy for research records. It
separates generated local runs from canonical evidence and immutable history.
An **accepted** record is accepted into the research record; it is not an
economic promotion decision and does not make a candidate PAPER-eligible.

## Authoritative locations

- `docs/research/<research-id>.md` — concise human summary and limitations.
- `data/research_manifests/<research-id>.json` — predeclared machine contract.
- `data/research_receipts/<research-id>*.json` — compact control or attestation
  receipt.
- `data/research/<research-id>/**` — only manifest-required, hash-pinned full
  results.
- `scripts/research/<research-id>.py` — retained runner implementation.
- `tests/research/test_<research-id>.py` — mission and regression contract.
- `research_runs/<run-id>/**` — ignored, generated, resumable local bundle.
- `docs/archive/**` and `data/research/archive/**` — immutable historical
  lineage.

## Lifecycle

| Status | Meaning |
|---|---|
| `planned` | A manifest exists; execution has not started. |
| `running` | Execution is in progress; generated run data is not canonical. |
| `accepted` | The completed record is canonical and documented. |
| `rejected` | The completed result failed its tested gates or hypothesis. |
| `superseded` | A later mission or correction replaces the record; the original remains immutable. |

`verdict` and `promotion` are separate fields. A record can be accepted while
its tested candidate is rejected or remains ineligible for PAPER.

## Current records

### r46_final_closure_2026_09_14

**Status:** CLOSED — current R46 lineage permanently closed<br>
**Closure:** CLOSED_NO_VALID_ECONOMIC_RESULT_EXECUTION_PROVENANCE_CONTRADICTION<br>
**Economic result:** ECONOMIC_RESULT_NOT_AVAILABLE<br>
**Economic rejection:** NOT_ECONOMICALLY_REJECTED<br>
**Mechanism:** MECHANISM_NOT_ADJUDICATED<br>
**Further R46 execution:** forbidden<br>
**Next candidate:** H06 — queue order only; H06 research and economics are not authorized

[closure receipt](../../data/research_receipts/r46_final_closure_2026_09_14.json) ·
[queue transition receipt](../../data/research_receipts/strategy_queue_transition_r46_to_h06_2026_09_14.json) ·
[closure document](r46_final_closure_2026_09_14.md)

### `execution_cost_transferability_p0_2026_09`

**Status:** `accepted` technical measurement record<br>
**Verdict:** `UNKNOWN_COST_TRANSFERABILITY`<br>
**Promotion:** none<br>
**Execution model:** unchanged; the shadow comparator mirrors the current
dynamic residual-capacity schedule.
**Technical verification source:** `c3d65d73b6ebd22159f44f5d00eb7eec8c57e30d`<br>
**Result-recording commit:** `c0e167b2df8fc3cb2f5e4e4843a40d480fa56151`<br>

[Manifest](../../data/research_manifests/execution_cost_transferability_p0_2026_09.json) ·
[technical receipt](../../data/research_receipts/execution_cost_transferability_p0_2026_09.json) ·
[human report](execution_cost_transferability_p0_2026_09.md)

### `execution_cost_transferability_p1_paper_observation_2026_09`

**Status:** `planned` — prepared but not yet committed<br>
**Verdict:** `UNKNOWN_COST_TRANSFERABILITY`<br>
**Promotion:** none<br>
**Purpose:** predeclared natural-PAPER sample epoch and read-only 7/30/90-day
observation reports; P0 remains measurement infrastructure, not a cost-model
acceptance.

[Manifest](../../data/research_manifests/execution_cost_transferability_p1_paper_observation_2026_09.json) ·
[human report](execution_cost_transferability_p1_paper_observation_2026_09.md) ·
[runner](../../scripts/execution_cost_transferability_p1_report.py) ·
[test](../../tests/research/test_execution_cost_transferability_p1_paper_observation_2026_09.py)

### `equity_profit_protection_2026_09`

**Status:** `superseded` — archived blocked R1 attempt, preserved for lineage.

[Manifest](../../data/research_manifests/equity_profit_protection_2026_09.json) ·
[runner](../../scripts/research/equity_profit_protection_2026_09.py) ·
[test](../../tests/research/test_equity_profit_protection_2026_09.py) ·
[human report](equity_profit_protection_2026_09.md)

### `equity_profit_protection_2026_09_r2`

**Status:** `accepted` record<br>
**Verdict:** `KEEP_CURRENT_V3_AFTER_VALID_COMPARISON`<br>
**Promotion:** none of E1/E2/E3 is PAPER-eligible<br>
**Execution/source pin:** `6b0cabe5a03d8d3c02173ebfce5b6430252b12a1`<br>
**Runner implementation commit:** `05837c33e06cf3f548f2b41f8633c3a4128f05e7`<br>
**Repository result-recording commit:** `02859f95459671e9db82af075a37889f6d0d079a`

[Manifest](../../data/research_manifests/equity_profit_protection_2026_09_r2.json) ·
[control receipt](../../data/research_receipts/equity_profit_protection_2026_09_r2_current_control.json) ·
[hash-pinned results](../../data/research/equity_profit_protection_2026_09_r2/) ·
[human report](equity_profit_protection_2026_09_r2.md) ·
[runner](../../scripts/research/equity_profit_protection_2026_09_r2.py) ·
[test](../../tests/research/test_equity_profit_protection_2026_09_r2.py)

### August 2026 historical archive

**Status:** per-mission `rejected` or `superseded`, as recorded in the archive.

[Archive index](../archive/research_2026_08/README.md) ·
[archived result tree](../../data/research/archive/2026_08/)

## Metadata contract

A new canonical record should provide, when applicable:

- research ID and lifecycle status;
- parent, superseded and superseding IDs;
- starting/source commit; runner implementation commit and SHA; clean
  execution commit;
- the repository result-recording commit as a separate identity;
- runner, exact entry point and command;
- dataset, lifetime, corporate-action and input-manifest hashes;
- frozen configuration digest and parameters;
- run timestamp and evaluation window;
- required result artifacts and hashes, plus the compact receipt;
- verdict, promotion status, rejected alternatives, limitations and stop
  conditions;
- linked tests, documentation and archive location.

### Commit identities

Do not edit an existing immutable manifest to insert its own eventual commit
ID. Keep execution/source identity separate from the repository commit that
records the resulting artifacts on the current branch.

## Version-control policy

- `research_runs/` is generated local state and is ignored; local bundles may
  be retained or regenerated without becoming canonical evidence.
- Ordinary `data/research/` output remains ignored. Only manifest-required,
  reviewed and hash-pinned result artifacts may be intentionally force-added by
  explicit path; never use a blanket force-add.
- Manifests, receipts, summaries, current indexes and archive indexes remain
  tracked.
- Supersession never silently deletes historical evidence.
