# R46 v3 execution-incident recovery governance

This document records an outcome-free governance generation for the R46 v2
reservation incident. It does not evaluate the strategy and does not authorize
an economic run.

## v2 incident status

The v2 base state remains `SEALED_UNUSED` with `run_count = 0` and
`release_count = 0`. Its reservation sidecar is preserved byte-for-byte at:

`data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.reserved.json`

The sidecar is cryptographically valid and binds to the canonical v2
preflight, governance manifest, data package, and base state. Its creator
origin remains `ORIGIN_UNPROVEN`: the frozen reservation function is callable
from test-only code as well as from the production package path, and no exact
creator command was recovered.

The declared forensic search found no persisted v2 economic output directory,
results, ledger, report, run receipt, consumed sidecar, or aborted-consumed
sidecar. This is narrower than proving that no transient in-memory outcome was
ever computed or observed; non-persisted outcome exposure remains unproven.

The v2 reservation is therefore not invalidated or deleted. The frozen v2
runner remains mechanically locked by the existing reservation, and deleting
the sidecar would bypass that protection. No sanctioned unreserve transition
exists.

## v3 recovery boundary

The v3 identity is
`r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14`, recovery generation
`1`, with mode `EXECUTION_INCIDENT_RECOVERY`. It is a separate one-shot
governance contract for possible future infrastructure recovery. It is not a
new strategy, parameter variant, dataset, event universe, statistical choice,
or outcome-selected rerun.

The v3 manifest inherits the frozen v2 economic identity, including the source
and freeze commits, the 127-event universe, TLT instrument, timing rules,
weight, costs, caps, incumbent V3, estimand, resampling seeds, and multiplicity
family. No new threshold or performance gate is introduced. The v2 reservation
and all v2 inputs remain immutable parent evidence.

Any future v3 result must be labeled
`RETROSPECTIVE_DESCRIPTIVE_ONLY_ORIGIN_UNPROVEN`. It cannot be described as a
pristine untouched confirmation, and v2 cannot be reused after a v3 result.

## Authorization boundary

This generation contains only governance artifacts and an outcome-free
validator. The validator returns `RECOVERY_GOVERNANCE_READY` only when the
incident, v2 pins, v3 self-hashes, schema constants, empty v3 transition state,
and declared no-output search all pass. It never creates a receipt or
transition and never imports economic execution code.

Economics are still not authorized. A separate review must approve a v3
source freeze, a v3-specific preflight, and one explicit orchestrator
authorization before any future economic runner is implemented or invoked.
