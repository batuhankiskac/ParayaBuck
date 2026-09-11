# R46 v2 Final Backtest Readiness Plan

## Goal

Prepare one honest, reproducible, research-only final backtest of the frozen
R46 Treasury-auction unwind candidate. Preserve the original v1 governance
bundle byte-for-byte and create a child v2 protocol that discloses the
retrospective nature of the evaluation. Do not modify PAPER, LIVE, registry,
or incumbent production behavior.

## Safety boundary

- No economic execution until the v2 preflight reports `PRE_RUN_OK`.
- No use of adjusted-close/total-return proxies for executable TLT bars.
- No retrieval timestamp may masquerade as historical publication time.
- No tuning, threshold search, best-cell selection, or post-outcome mutation.
- A valid failure is a completed result; it does not authorize a rerun.
- PASS remains research-only and cannot promote or activate the candidate.

## Workstreams

### 1. V2 governance and statistical contract

- Add a child v2 manifest/schema/builder that pins the v1 canonical hash.
- Preserve the candidate formula, timing, cost cells, controls, weights, caps,
  and missing-data rules.
- Replace the impossible pristine-prospective claim with an explicit
  `RETROSPECTIVE_ONE_SHOT_DISCLOSED` evaluation contract.
- Freeze one primary paired event-episode estimand, secondary diagnostics,
  resampling seeds, minimum coverage, and a complete multiplicity registry.
- Require disclosure rather than pretending prior research exposure did not
  occur. Keep all promotion and PAPER/LIVE fields false.

### 2. Execution-grade data acquisition

- Reuse the verified TreasuryDirect identity inventory and NYSE calendar.
- Add an opt-in, credential-safe Alpaca SIP TLT acquisition path for raw and
  split-adjusted daily bars; preserve raw responses and request receipts.
- Add separately sourced cash distributions and historical identity/listing
  evidence; fail closed when the provider cannot prove required fields.
- Build the five-artifact data-package manifest: documents, bars, dividends,
  eligibility, and calendar, each content-hashed and cross-linked.
- Never persist credentials or provider response headers.

### 3. Native one-ledger runner and verifier

- Add a research-only R46 runner over the existing native portfolio engine.
- Coordinate unchanged incumbent V3 and R46 in one account with shared cash,
  positions, fills, fees, slot/capacity checks, and ownership attribution.
- Enforce next-open entry, fifth-open exit, no pyramid, sell-before-buy,
  no delayed catch-up, and no synthetic terminal fill.
- Add an independent verifier that recomputes pins/invariants and rejects
  separate-sleeve blends, cap violations, negative cash, unowned fills,
  missing suppressions, and any promotion/PAPER/LIVE side effect.

### 4. Preflight, source freeze, and final run

- Add a v2 preflight that validates governance, package, environment,
  transitive source pins, ledger attestation, and one-shot run state without
  emitting economic outputs.
- Exercise malformed/tampered/missing-data fixtures before using real data.
- Run focused tests, lint, type/static checks, and an independent diff review.
- Create one clean research-only source-pin commit after all source files and
  dependency hashes are final.
- Acquire the authorized data, build the frozen package, rerun preflight, and
  execute exactly one economic backtest only when status is `PRE_RUN_OK`.
- Verify and report the frozen result without integration or promotion.

## Acceptance criteria

1. Original v1 governance artifacts remain byte-identical.
2. V2 is self-hashed, parent-hash-bound, source-pinned, and outcome-clean.
3. Real data either satisfies every declared contract or produces a specific
   blocker; no proxy silently passes.
4. Synthetic one-ledger tests prove shared capital and fail-closed behavior.
5. Preflight status, not process exit code, controls economic execution.
6. The final backtest runs at most once and produces a verifier-accepted,
   research-only immutable bundle.
