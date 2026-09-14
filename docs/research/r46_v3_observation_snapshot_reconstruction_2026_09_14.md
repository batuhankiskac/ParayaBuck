# R46 v3 Observation Snapshot Reconstruction — 2026-09-14

This note records a read-only reconstruction of the exact frozen R46 v3 point-in-time universe. The original runtime snapshot was not persisted, but the frozen loader, config, V5 inputs, and builder chain were still available and independently pinned.

## Rejected earlier hypothesis

The earlier hypothesis that a pre-first-cadence cash repair or an event-boundary ordering path explained the missing incumbent was not used. The preserved failure occurred after real historical processing began, and the new reconstruction tests the native universe and decision gates directly.

## Deterministic builder chain

The frozen V2 wrapper loaded the V5 incumbent, loaded `config/settings.yaml`, removed TLT from the incumbent universe input, and called `build_product_universe(...)`. That function selected the frozen equity scanner configuration, which called `build_trailing_point_in_time_universe(...)`; the latter called `build_observation_set(...)` with the configured V3 ranker. The ranker was used only to recover ordered stream membership; no scores were persisted.

Two independent Python processes with distinct hash seeds produced byte-identical identities. The reconstructed universe contains 2658 snapshots and has full hash `2051fe292cb35564afbe742ef5f83a7b304a676151133a2137210777e31dc1a3`. Direct-builder parity passed for the full hash and both audited snapshots.

## Feb 6 path

The requested boundary `2017-02-06T05:00:00Z` resolved to `2017-02-06T05:00:00Z`. The selected universe count was 291; the exact ordered raw observation set contains 30 symbols. The mirrored effective set contains 30 symbols and the completeness result is `PASS`. The support gate was `PASS` and the product guard was `PASS`.

The completeness predicate was evaluated against the exact effective observation set: each expected symbol had non-empty history and a latest completed bar equal to the decision timestamp. The receipt contains the ordered expected, complete, missing, stale, and per-symbol structural records.

## Primary 1x failing cell

Frozen V3 source order proves the first cost multiplier was 1.0, the candidate engine invocation preceded the control invocation, and metrics/publication occur only after that call. The source-level failing computation cell is `PRIMARY_CANDIDATE_1X`. This is control-flow evidence, not an economic result.

## Feb 10 event path and consistency

The first frozen event boundary after Feb 6 is `2017-02-10T05:00:00Z` for `2017-02-09:912810RV2` / `ENTRY`. Native weekly scheduling was false, the R46 extra boundary was true, support and product checks passed, and the event override bypassed normal observation completeness. The preserved failure then reached the missing incumbent-base branch with `{"error": "BLOCKED_R46_INCUMBENT_BASE_MISSING", "status": "BLOCKED"}`. The reconstructed Feb 6 path is therefore `DETERMINISTIC_RECONSTRUCTION_CONTRADICTS_OBSERVED_EXECUTION`: a normal non-event decide would have assigned the incumbent target, while the preserved later event proves that target was still None. The missing item is execution-state provenance, not a selected repair.

Final classification: `DETERMINISTIC_RECONSTRUCTION_CONTRADICTS_OBSERVED_EXECUTION`.

No repair has been selected or implemented.
