# Final migration decision

`ACCOUNT_ARCHITECTURE_MIGRATION_8_INCONCLUSIVE`

The study stopped before historical 6/8 replay because it independently found a pre-existing generic broker-order correctness defect. The migration brief explicitly requires stopping and isolating such a defect first. This decision does **not** imply that cap eight is better or worse than six.

# Starting main SHA and audit consolidation

The initial production main was `df9bf797a428c384fb26adc7f9f99abacf79224b`. The outcome-blind architecture audit was verified at `da26dda53777042b58aba28af05779a2bcdd2449`, tagged and pushed as `archive/research/account-capacity-architecture-audit-v1-2026-09`, merged as research-only artifacts into main, and recorded durably in `docs/research/account_capacity_architecture_decision_v1.md`. The completed audit branch was deleted locally and remotely. The new clean main before this study was `478b15bd7c56d75735de0d94f456a2b9b9b491e8`. This migration branch was created from that main, not from the old audit branch (`ARCHITECTURE_AUDIT_HANDOFF.json`). No production code/config changed during consolidation.

# Frozen control and treatment

Control A is `risk.max_positions = 6`; treatment B is `risk.max_positions = 8`. All other production strategy, exposure, execution, data, costs, and lifecycle settings were frozen in `FROZEN_MIGRATION_SPEC.md` and `.json` at commit `3f7d0bc2b443f66b95d67ccb0503f1a0567799f5` **before any migration outcome**. JSON SHA256: `9376525988990e2644fe53eff395a2a55ec410ea4deb95a93d0bf217b7c49efc`. The pinned equity/crypto inputs and settings passed local SHA256 verification. No third arm or parameter search was run.

# Shared-account replay and data period

The existing backtest is single-market and cannot establish account-wide equity/crypto count competition. `SHARED_ACCOUNT_REPLAY_SPEC.md` documents the necessary single-ledger causal adapter, production crypto-first reservation semantics, next-open fill approximation, event attribution, and the predeclared 2017-01-03 to 2026-07-30 decision period with crypto cash before 2021-04-01. Implementation was stopped when the generic defect was isolated; no historical feed or arm was executed. The frozen source/data period remains a protocol, not a result.

# Single-variable integrity and hard gates

The written arms differ only by `risk.max_positions`, but G1 was not empirically exercised. G2-G5 and G7-G8 have no arm outcomes or parity evidence. G6 is unresolved from public broker documentation and static source alone. None is marked passed. The broker-wide OPEN order request uses Alpaca's default first page and can miss later manual order claims once more than 50 are open (`GENERIC_CORRECTNESS_STOP.md`). This is a conditional generic production safety defect that predates both arms.

# Structural capacity, crowd-out, exposure, concentration, order load and economics

**No historical results exist.** Slot utilization, count-cap denials, crypto-to-equity or reverse crowd-out, sleeve deployability, idle target capacity, realized exposure, HHI, realized order/API load, and economic metrics were not calculated. The prior outcome-blind audit's static geometry still shows that six total slots with two crypto names leave four equities and at most 0.40 equity target, while eight is the minimum count permitting six equities plus two crypto. That arithmetic is not a migration replay result. A simple full replacement is 12 versus 16 order submissions under caps six and eight, but actual API calls are higher and account-specific headroom was not verified.

| Economic metric | Cap 6 | Cap 8 | Difference |
| --- | --- | --- | --- |
| Total return, CAGR, volatility, Sharpe, Sortino, max drawdown, Calmar, turnover, costs, ending NAV | Not run | Not run | Not evaluated |

No validation-period comparison or independent post-result verifier is possible without a valid primary replay. `INDEPENDENT_MIGRATION_VERIFICATION.json` machine-readably confirms the freeze and records every result check as `NOT_RUN`. Prior rejected alpha families were not reopened or rerun.

# Migration rationale and production state

`INCONCLUSIVE` follows the explicit correctness stop and missing shared-account/operational evidence, not performance. Production `risk.max_positions` remains six; V3, crypto, PAPER/LIVE, and all production config/code are unchanged. The current production and future-research fit contract remains six total positions until a separate approved change. This branch contains only research documentation and the frozen protocol.

# Exact next action

`RESOLVE_SHARED_ACCOUNT_REPLAY_LIMITATION`

Specifically, isolate and repair broker-wide open-order pagination as a separate generic production correctness issue. Then re-freeze the study from updated main, complete research-only shared-account parity and G6 operational verification, and only then run the two incumbent arms once. No automatic cap change or candidate retest is authorized.

# Validation

The three JSON artifacts written after freeze parse and agree with the frozen six/eight arm definition and `NOT_RUN` status; the two frozen specification hashes still match the committed bytes. `ruff check .`, `ruff format --check .`, `.venv/bin/mypy src`, and the complete `.venv/bin/pytest -q` suite pass (**1,138 passed, one skipped, two deprecation warnings**). A targeted credential-pattern scan found no secrets in this study directory. The 51-order fixture was synthetic and made no broker call. No result CSV is claimed to exist.

# Files and final branch SHA

This stopped study contains `ARCHITECTURE_AUDIT_HANDOFF.json`, `FROZEN_MIGRATION_SPEC.md`, `FROZEN_MIGRATION_SPEC.json`, `SHARED_ACCOUNT_REPLAY_SPEC.md`, `GENERIC_CORRECTNESS_STOP.md`, `INDEPENDENT_MIGRATION_VERIFICATION.json`, `MIGRATION_DECISION.json`, and this report. Result JSON/CSV and post-result metric reconstructions were deliberately **not** fabricated because the explicit stop occurred before replay. The final containing commit SHA is given in the delivery response; a Git commit cannot contain its own SHA in this file.
