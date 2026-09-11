# R46 v2 Economic Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task.

**Goal:** Close the reviewed R46 v2 execution, estimand, provenance, and one-shot correctness gaps without running real economics or changing PAPER/LIVE/registry state.

**Architecture:** Keep the existing exact-five TLT package and PortfolioBacktestEngine. Add a production-only immutable-input seam, event-order guards, explicit cash-null accounting, and a durable one-shot transition journal. Keep test fixtures behind clearly named TEST_ONLY helpers. Make verifier/preflight schemas reject incomplete or stale evidence rather than infer readiness.

**Tech Stack:** Python 3.13, pytest/uv, Ruff, mypy, canonical JSON/SHA-256, existing PortfolioBacktestEngine.

**Spec:** Parent task requirements for R46 v2 economic blockers (2026-09-11).

## Global Constraints

- No real market/economic run, network, PAPER, LIVE, registry, or production mutation.
- Never mutate the real one-shot state; tests use `tmp_path` fixtures only.
- Preserve existing governance/native/path/event-order changes and exact five package artifacts.
- Use `apply_patch`; each behavior starts with a focused failing test.
- Candidate/control must share the incumbent path; only TLT/cash reservation may differ.
- Statistics remain retrospective descriptive-only; no confirmatory claim or auto-promotion.

### Task 1: Gate and production-input contract

**Files:**
- Modify: `scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py`
- Modify: `scripts/research/r46_v2_preflight_2026_09_10.py` only if receipt fields require validation
- Test: `tests/research/test_r46_v2_execution_2026_09_10.py`

- [ ] Add failing tests proving explicit PIT/native/review receipt paths are forwarded during persisted preflight, production overrides are rejected, and config/PIT load failure blocks instead of selecting a static universe.
- [ ] Run only those tests and observe the expected failures.
- [ ] Pass all evidence paths from the persisted receipt into `verify_v2_manifest`; make the production runner load only receipt-bound incumbent/config/starting-equity/cost inputs.
- [ ] Keep any fixture injection in explicitly named TEST_ONLY helpers and hard-reject marker fallback on the economic surface.
- [ ] Run the focused gate/input tests.

### Task 2: Event timing, incumbent parity, and no delayed catch-up

**Files:**
- Modify: `scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py`
- Modify: `src/parayabuck/backtest/portfolio_engine.py` only at the narrow order-lifecycle seam if required
- Test: `tests/research/test_r46_v2_execution_2026_09_10.py`

- [ ] Add failing synthetic tests comparing candidate/control incumbent decisions, weights, and non-TLT fills at event markers; add tests for partial entry/exit remainder cancellation and exact-open-only fills.
- [ ] Run the focused tests and confirm the current extra-rebalance/catch-up failures.
- [ ] Prevent event markers from invoking a fresh V3 incumbent target; cancel or expire R46 orders after the mandated entry/exit open and preserve explicit partial/unfilled statuses.
- [ ] Add a strict production marker path that excludes synthetic availability markers from V3 history and liquidity history.
- [ ] Run focused event/order tests and existing engine accounting tests.

### Task 3: Estimand, null, cost grid, and placebo

**Files:**
- Modify: `scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py`
- Modify: `scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py`
- Modify: `data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_statistics_plan.json`
- Modify: `data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_family_registry.json`
- Test: `tests/research/test_r46_v2_execution_2026_09_10.py`
- Test: `tests/research/test_r46_v2_governance_2026_09_10.py` if schema pins change

- [ ] Add failing tests for a real reservation-to-zero-cash null row, cost-cell-specific fee/PnL use, an actual +10-session delayed synthetic engine cell, and no placeholder p-values in the inferential Holm family.
- [ ] Run those tests and observe failures.
- [ ] Compute candidate-minus-null per episode from validated full fills, dividends, and fees; emit non-inferential roles as `not_tested` and keep Holm only over frozen inferential IDs.
- [ ] Execute the delayed placebo through the same synthetic/native semantics without selection or outcome-driven changes.
- [ ] Update deterministic plan/family artifacts and their canonical hashes only as required by the new contract.
- [ ] Run focused statistics/verifier tests.

### Task 4: Durable one-shot journal

**Files:**
- Modify: `scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py`
- Modify: `scripts/research/r46_v2_preflight_2026_09_10.py` if transition receipts are preflight-bound
- Test: `tests/research/test_r46_v2_execution_2026_09_10.py`

- [ ] Add failing temp-directory tests for self-hashed reserved/consumed sidecars, stale/forged sidecars, crash-after-consume reconciliation, and rerun refusal for every RESERVED/CONSUMED/ABORTED state.
- [ ] Run them and confirm current existence-only behavior fails.
- [ ] Implement canonical sidecar payloads with state/gate/output crosslinks, durable writes, and an ABORTED_CONSUMED terminal state that never reopens execution.
- [ ] Define and test publication ordering: reserve → outputs → consume journal → run receipt; any interruption remains non-rerunnable and auditable.
- [ ] Run focused one-shot tests.

### Task 5: Full verification

- [ ] Run `uv run pytest` (or the repository-supported equivalent) for all `tests/research/test_r46*.py`; do not invoke economic fixtures that use real data.
- [ ] Run Ruff, production mypy, and `py_compile` on changed Python files.
- [ ] Inspect `git diff`/`git status` and confirm only intended v2/execution/verifier/tests/plan changes were made; do not commit.
- [ ] Report red test evidence, green test evidence, unavailable tools, and remaining blockers without claiming an economic run.
