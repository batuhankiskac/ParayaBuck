# Final production correctness and cap-8 readiness report

## Final campaign decision

`PRODUCTION_CORRECTNESS_FIXED_CAP8_READY_FOR_PAPER_CHANGE`. The precise next action is `REQUEST_EXPLICIT_APPROVAL_FOR_CONTROLLED_CAP8_PAPER_CONFIG_CHANGE`. This is a local source/readiness decision, not a deployment or a broker trial.

## Starting repository/ref state

Local main, `origin/main`, and verified remote main started at `af7225e1952c20a81f7d22d4f8d98126f02e27b3`. Migration V3 authority is `9c5e61382e8cdb71fdd3442d945719024accedd9`. Parity V1 substantive audit is `e99d4226c2b74e23ee7dd6e6b01f1b4ddaadff57`; later audit tip `8d7f0989b3c830a67cf66d8d8dc7dec1d1642b6d` adds an accidental `.fix-worktrees/` gitlink. Reconciliation fix branch is `5229a061b1d5921fc4dffef8c9bb54393b3dd833`. Initial worktrees were clean. `STARTING_STATE.json` records branch and archive authority.

## Reconciliation repair code-level verification

The accepted diff was inspected against starting main. Pre-fix RR-01/RR-02 synthetic regressions failed on the old code and passed after repair. The repair resolves durable nonterminal orders by exhaustive OPEN read plus targeted known-identity lookup before ownership drift evaluation, stages complete FILL history before application, validates activity IDs/cursor progress, fails on later-page transport errors, and keeps repeated recovery idempotent. The startup reconciliation barrier, risk latch restoration, account/slot reconstruction, and deterministic client-order-ID submission remain covered by focused and full tests. See `RECONCILIATION_REPAIR_VERIFICATION.md`.

## Reconciliation repair integration SHA

Local main integration checkpoint: `96254b7de6e045212002cfa807abd448bc6e5d0d`. The original accepted branch remains `5229a061b1d5921fc4dffef8c9bb54393b3dd833`.

## RR-01 final status

`VERIFIED`: offline full/partial BUY and SELL fills reconcile known local order identity before drift; genuinely unexplained drift still halts.

## RR-02 final status

`VERIFIED`: FILL pagination is complete-or-failed; malformed/repeated/nonadvancing pages and later transport failures cannot yield a partial-success checkpoint.

## Pure-BUY defect pre-fix reproduction

On reconciliation-corrected main before the BUY gate, the five audit-only cases reproduced unsafe G1 new-symbol and G2 add-to-held BUYs at realized gross 0.605, while G3 SELL remained available, G4 replacement deferred, and G5 manual exposure counted. The old tests asserted the defect and remain archived only. New production regressions assert safe suppression. See `PRE_FIX_PURE_BUY_REPRODUCTION.md`.

## Frozen BUY admission contract

The contract was committed before production edits: current marked absolute broker gross plus deduplicated committed BUY remainder plus candidate BUY notional cannot exceed `maximum_gross * equity`; current broker cash must preserve `minimum_cash * equity`; separate slot and LIVE hard-dollar limits still apply. Passive mark drift does not force liquidation. See the frozen Markdown and JSON files.

## All BUY submission paths

Immediate new/add-to-held and deferred/replanned BUY phases now converge on one fresh gate before durable order persistence. Startup catch-up and retry use these phases; equity and crypto share the account scope. Protective short-cover BUYs are risk reducing and remain in their established protective path. See `BUY_SUBMISSION_PATH_MATRIX.md`.

## Working/pending BUY commitment semantics

Exhaustive broker OPEN BUYs and account-scoped durable nonterminal local BUYs are matched by client order ID; the larger valid remaining-notional estimate is counted once. A partial fill contributes its filled shares to current position gross and its unfilled remainder to commitment. Local submitting, pending cancel and manual broker BUYs consume headroom. An unpriceable/uncertain OPEN state blocks new BUY admission. A contingent SELL-dependent plan is replanned later, not counted as a submitted commitment. See `COMMITTED_BUY_EXPOSURE_SPEC.md`.

## Gross headroom repair

Both pure and deferred BUYs use `max(0, 0.60 * current_equity - current_marked_gross - committed_BUYs)` and scale or suppress by broker increments/minima. Gross-exhausted BUYs expose `gross_headroom_exhausted`. SELLs do not enter the BUY gate.

## Cash headroom repair

Both paths use `max(0, current_broker_cash - 0.40 * current_equity - committed_BUYs)`. Margin buying power is not substituted for cash. `cash_headroom_exhausted` is operator visible. No continuous cash-ratio liquidation rule was introduced.

## Cross-market headroom

Equity-first and crypto-first synthetic decisions share one broker/repository and cannot consume the same remaining gross/cash headroom twice. The account decision lock serializes engine decisions. Working cross-market BUYs and partial fills are counted on the next decision. See `CROSS_MARKET_HEADROOM_AUDIT.md`.

## Partial-fill/cancel behavior

Filled position plus remaining working BUY is counted once. Cancel-in-flight does not release headroom; authoritative terminal reconciliation does. Late fills are handled by RR-01/RR-02 recovery. See `PARTIAL_FILL_HEADROOM_AUDIT.md`.

## External/manual inventory

Marked external positions consume gross and global slots but are not liquidated solely for cap change. Existing pure-manual ownership rules prevent bot growth. Managed risk-reducing SELLs remain possible. See `EXTERNAL_INVENTORY_HEADROOM_AUDIT.md`.

## Same-bug-class findings

Immediate stale admission and deferred incomplete commitment accounting were fixed by the shared gate. OPEN fetch uncertainty stays fail-closed; no remaining same-class BUY bypass was found. See `SAME_BUG_CLASS_AUDIT.md`.

## Monitoring improvements

The existing dashboard risk panel now samples read-only configured/occupied/reserved slots, working orders, target/realized gross, committed BUYs, gross/cash BUY headroom, cash ratio, sleeves, suppression/halt reasons and reconciliation status. Suppressed BUYs also log and enter the existing no-trade journal. See `MONITORING_READINESS_V2.md`.

## Full correctness validation

Correctness branch: full pytest `1216 passed, 4 skipped`; focused reconciliation/capacity/slot/restart matrix `139 passed`; strict mypy `133 source files, no issues`; Ruff and format pass; JSON, diff and changed-line credential scan pass. Six initial full-suite failures were sandbox localhost-bind denials only; identical suite passed with local bind permission. Parity V2 branch: focused matrix `108 passed, 1 skipped`; full pytest `1218 passed, 4 skipped`; Ruff, format, strict mypy, JSON and diff pass. Two warnings are existing config timeframe deprecations. No PAPER/LIVE runtime or broker API mutation was used for validation.

## Corrected production main SHA

`d0c68057234a240a778e5adb06e42c6a3c136c34`. Production code checkpoint was `8b0b8bbadc71cfd8504757faa3f8df9e5c71a297`; `d0c6805` adds the earlier frozen contract/authority documentation. The tree has no `.fix-worktrees/` gitlink.

## Migration V3 validity after fixes

`MIGRATION_V3_STILL_VALID`. The historical physical replay uses unchanged backtest/risk/planner inputs and did not exercise the PAPER/LIVE direct BUY submission bug. The only shared `execution/portfolio.py` edit is an additive broker working BUY DTO. Structural count-specific denials remain 1112 at six versus 438 at eight. No cap search or alpha replay was reopened.

## Cap-8 production parity V2

`CAP8_PRODUCTION_PARITY_READY_FOR_PAPER_CHANGE`. The audit branch starts from corrected production code, not the old audit branch. A synthetic in-memory configuration changes only `risk.max_positions` from 6 to 8. Tests prove eight effective account slots, unchanged crypto cap 2, ninth opening blocked, SELL allowed, and rollback openings blocked at occupancy above six. The shared gate, reconciliation, ownership, OPEN/FILL pagination and monitoring pass. The audit evidence commit is `0deb15c5faed5f076e9a8117671a2d139fb49cd5`; the merged frozen-evidence checkpoint is `c8c0279f26ac9f237f0d3cd53050cd3476e9b8d0`.

## Gross policy after repair

`MAXIMUM_GROSS_IS_TARGET_LIMIT` and `REALIZED_GROSS_DRIFT_CONSISTENT_WITH_EXISTING_POLICY`. Passive mark movement can put realized gross above 0.60 without forcing liquidation. At gross 0.605, new risk-increasing BUYs are now blocked and risk-reducing SELLs remain available; operator metrics expose the excursion.

## Position-cap semantics

`risk.max_positions` is mapped into the production overlay and global opening reservation. Synthetic eight admits seventh/eighth, blocks ninth. Current config remains six; there is no hidden hardcoded six in the production slot path. Direct-construction default six does not override loaded PAPER config.

## Crypto-specific cap

`risk.max_crypto_positions: 2`, crypto exposure 0.10 and crypto per-name 0.05 are unchanged. The account cap eight does not enlarge the crypto-specific cap.

## Restart/reconciliation behavior at cap 8

The same startup barrier, exhaustive OPEN/FILL retrieval, known-order lookup and durable reservation reconstruction apply at eight. V3 research observed peak eleven working orders; the production OPEN adapter paginates exhaustively. A failed/uncertain read halts admission instead of assuming no orders.

## Rollback semantics

An operator rollback to six does not force liquidate seven/eight existing positions. New openings wait until normal, authoritative exits lower effective occupied/reserved slots; pending SELLs do not free slots. See `ROLLBACK_SEMANTICS.md`.

## PAPER rollout readiness

The exact future PAPER-only single-key change and predeploy/startup/observation/rollback gates are in `PAPER_CAP8_ROLLOUT_PACKAGE.md`. It requires explicit approval and a reviewed deployment SHA. No automatic LIVE progression.

## PAPER config changed?

`NO`.

## Production maximum_positions

`6`.

## LIVE enabled?

`NO`.

## Broker mutations

`NONE`.

## Accidental `.fix-worktrees` gitlink disposition

The later parity V1 audit-tip gitlink did not enter corrected production main or parity V2. The V1 archive tag points to substantive `e99d4226...` before that accidental commit.

## Old unsafe audit tests disposition

The unsafe assertions remain in archived V1 evidence and were not merged. Production tests now assert blocked unsafe BUYs.

## Refs created locally

Corrected `main` is `d0c68057234a240a778e5adb06e42c6a3c136c34`; correctness fix branch checkpoint is `f107009c1308c152d326a8647a1df6c8bc55b225`; reconciliation fix branch is `5229a061b1d5921fc4dffef8c9bb54393b3dd833`; parity V2 evidence merge checkpoint is `c8c0279f26ac9f237f0d3cd53050cd3476e9b8d0` and this report is committed afterward on the same audit branch. The local V1 archive tag peels to `e99d4226c2b74e23ee7dd6e6b01f1b4ddaadff57`; reconciliation and migration archive tags peel to `3551322b9ac609ed2ee52c0343a14f2dcd408f37` and `9c5e61382e8cdb71fdd3442d945719024accedd9` respectively. Final audit-branch and new archive-tag SHAs are supplied in the delivery message after this report commit.

## Refs still requiring publication approval

No new or changed ref was pushed. Exact publication candidates are local `main`, `fix/production-restart-reconciliation-v1`, `fix/production-correctness-cap8-readiness-v1`, `audit/cap8-production-parity-v2`, `archive/audit/cap8-production-parity-v1-blocked-2026-09`, `archive/production/production-correctness-cap8-readiness-v1-2026-09`, and `archive/audit/cap8-production-parity-v2-ready-2026-09`. The existing local reconciliation and migration archive tags also remain unpublished until separately authorized if they are absent remotely. Publication of any exact ref requires separate authorization; this does not block local readiness.

## Exact next action

`REQUEST_EXPLICIT_APPROVAL_FOR_CONTROLLED_CAP8_PAPER_CONFIG_CHANGE`.
