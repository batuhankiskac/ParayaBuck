# Final PAPER observability repair report

## Production repair decision
PAPER_OBSERVABILITY_REPAIR_ACCEPTED.

## PAPER readiness decision
PAPER_FORWARD_OBSERVATION_READY for an operator-controlled, frozen PAPER observation after both active universes are refreshed and validated with provider-backed data. This is offline readiness; no real order or fill has been observed in this campaign.

## Starting origin/main
`7724a2bc9e61ba325a6a252c8f518cabd99d6c69`, fetched before work. The prior audit branch was evidence only. This repair branch was created directly from origin/main in an isolated worktree.

## Final commit and main publication
The commit cannot embed its own SHA. Verify the final commit and remote `refs/heads/main` in the publication readback; the delivery response records both. Publication is conditional on a final fetch, safe fast-forward and unchanged config.

## Gap 1: unified receipts
Previously only scheduled equity V3 emitted a runtime receipt; catch-up and crypto did not. V1 `positions_after` and gross were projections. Ongoing PAPER decisions now emit immutable V2 receipts for equity and crypto, scheduled and startup catch-up, with deterministic timestamp/market/origin/decision ID filenames. The old V1 class remains for historical files, but runtime no longer emits misleading V1 files.

## Equity and crypto scheduled/catch-up receipts
All four persisted decision classes use one V2 writer. No-order decisions with a persisted ID are included. Catch-up is explicitly tagged. Crypto receipts carry selected ranks, retained-by-hysteresis flags and previous holdings. The startup route dispatches both market results to the writer. Same-ID retry leaves the existing immutable receipt intact.

## Intent and confirmed execution
`target_state` and `planned_orders` are INTENDED. `submission_snapshot` is an as-of-write status. `execution_confirmation.confirmed_positions_after` is null. SQLite decisions, orders, fills and execution events own final lifecycle truth. The new append-only BUY admission observations link by client order ID, including the deferred BUY phase. Receipt or admission telemetry failures emit incidents while existing trading safety remains in force; the auditor fails the affected observation.

## Gap 2: current two-market soak auditor
`scripts/audit_paper_soak.py` reads SQLite through read-only URI and query-only pragma, plus V2 receipts/config/journal. It accepts a V2 deployment `--start` and optional `--end` so legacy decisions are not misclassified. The V3-specific auditor remains intact. JSON includes decision classes, lifecycle, scanner, reconciliation, restart and admission summaries; stderr gives a human summary.

## Soak gates and synthetic evidence
S1-S12 cover frozen identities, decision gaps, lifecycle/orphans, duplicates, global/crypto effective claims, final BUY gross/cash, drift, catch-up origin, receipt completeness, scanner recovery and LIVE. Clean synthetic fixture: PASS, 6 decisions across two sessions, fills, partial, reject, cancel, working BUY, restart, scanner failure/recovery. Twelve independent invalid mutations fail their expected gate; see `SYNTHETIC_SOAK_RESULTS.json`. P&L is not a gate.

## Gap 3: effective crypto slot visibility
`capacity_health()` now exposes position count, effective global/crypto claims, caps, available counts, working opening count and canonical symbol sources. The risk card shows crypto held/effective/cap. A working BUY consumes one claim; a pending SELL does not free a held position; position/reservation/broker aliases deduplicate. Synthetic impossible 9th global or 3rd crypto claim remains visible rather than clamped.

## Global and crypto caps
Global cap **8**; crypto cap **2**.

## Configuration and strategy changes
Configuration diff **NONE**. Strategy, target math, parameters, allocations, risk thresholds and execution sizing changes **NONE**. Added code is receipt, read-only audit, telemetry persistence and operator visibility.

## Validation
Full pytest **1266 passed, 4 skipped** with two deprecation warnings. Ruff check PASS. Ruff format PASS. Project strict mypy PASS (134 source files). `git diff --check` PASS. JSON/YAML and changed-file credential scans PASS. Offline doctor **20/20 PASS** with synthetic placeholders and `--offline`; both fresh-worktree active universes remained `REFRESH_NEEDED`, which is an operator prestart requirement, not a refreshed market-data assertion.

## Offline readiness recertification
Config, secrets boundary, auto-start, reconciliation, ownership, scanner startup, warmup, catch-up, cap8/cap2, gross/cash, receipts, soak, health/readyz, alerting, dashboard, SQLite durability, backup, restart, risk incidents and LIVE boundary passed offline gates as recorded in `READINESS_RECERTIFICATION.json`. Target-host/systemd and real PAPER lifecycle remain the subsequent forward observation.

## Frozen observation duration
At least **14 consecutive calendar days** with two natural weekly equity opportunities, three 96-hour crypto opportunities, scanner refresh, lifecycle evidence and one controlled restart drill. Extend if the events do not occur. No parameter tuning from short-term P&L.

## PAPER, broker and LIVE boundary
PAPER started **NO**. Broker calls **NONE**. LIVE **DISABLED**.

## Exact next action
`OPERATOR_MAY BEGIN FROZEN PAPER FORWARD OBSERVATION WHEN DESIRED`, after refreshing/validating both active universes and completing the documented prestart ownership, reconciliation and readyz checks.
