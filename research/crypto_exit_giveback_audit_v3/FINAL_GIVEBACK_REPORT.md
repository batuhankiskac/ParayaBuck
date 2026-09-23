# Corrected causal crypto position-lifecycle audit V3

## Audit decision

`CRYPTO_EXIT_RESEARCH_NOT_JUSTIFIED`

## Behavior classification

`MIXED_PROFIT_RETENTION_BEHAVIOR`

## Corrected canonical baseline authority

Accepted `maintenance/crypto-canonical-causal-execution-v1` at `b135d73743ec2652f79b8c840df50bd3f67166e8` is merged into main and archived under `archive/maintenance/crypto-canonical-causal-execution-v1-2026-09`. This audit reads its immutable `CORRECTED_REPLAY_A.json.gz` directly. Full JSON SHA256 `cdc3004dbbfe1e13e8f9f9deb5ec80ecbe628236210649a68a8f47aa55f33697`; fills SHA256 `645d444ff3c95ba2f5eab1f0162acc903275e4642c70c79eb03b8248829e1fba`. The input, daily bars, universe, decisions, snapshots, final state and terminal-event hashes all match the accepted receipt. The replay has 454 decisions, 406 fills, 206 orders and 1,945 snapshots. Starting NAV is $100,000; ending NAV and cash are $105,423.13343608, with no holdings or pending orders. Fees are $919.65403033, slippage $73.57270962 and spread $36.78635481. The accepted frozen contract uses the 22-day volatility score, top two/rank-four retention, 96-hour label cadence, 5% sleeve, 0.005 band, 10% participation, 2bps slippage, 2bps full spread, 25bps taker fee and strictly causal hourly opens after physical order creation. See `CAUSAL_BASELINE_RECEIPT.json`.

## Old audit provenance

Immutable `archive/research/crypto-exit-giveback-audit-v2-2026-09` reported 57 episodes, 55 natural closures, 50 positive-MFE natural episodes, 20 positive-MFE natural episodes closing negative and 15 natural episodes with >=30% MFE. All 15 closed profitably; their median retention was 87.1497%. Its conclusion was mixed behavior with no justified exit campaign. Its 455-decision / 388-fill control is superseded. See `OLD_AUDIT_PROVENANCE.md`.

## Frozen methodology

A symbol-local continuous positive-inventory interval is one episode; partial fills, adds and reductions remain inside it. Buy fees increase gross outlay and cost basis; sell fees reduce proceeds; fill price already includes spread/slippage. P&L at each active completed daily close includes realized proceeds and the remaining marked inventory. The denominator is all gross purchase outlay including buy fees across adds. MFE is the largest active completed-bar marked economic P&L; flat settlement and intraday high are excluded. Final retention is actual final net P&L divided by positive MFE, without clipping. The >=30% cohort is fixed from V2. Physical fill timestamps determine episode boundaries and durations. The terminal event's hourly `last_close` is excluded from MFE; the final active mark is the preceding completed daily snapshot because liquidation occurs before the terminal day's bar is ingested. Endpoint-open episodes are censored for natural-exit inference. See `FROZEN_AUDIT_METHOD.md`.

## New episode counts

56 episodes: 54 natural closures and two endpoint-open, terminal-settled episodes. Fifty-two episodes across all states had positive MFE; 51 of 54 natural episodes had positive MFE. Thirty natural episodes closed profitable. The two censored endpoint episodes are BTC/USD and DOGE/USD. No fill or inventory remains unassigned. Episode P&L sums to $5,423.13343608; final account residual is below $3e-12 and maximum daily NAV residual is below $5e-11.

## Giveback diagnostics

Of 51 positive-MFE natural episodes, 21 (41.18%) finished negative. Across all positive-MFE natural episodes, median retention was 28.86%, P25 -192.83%, P75 80.20%; small positive peaks create extreme negative ratios, so the mean is unstable. Retention bins: 16 >=75%, six [50%,75%), four [25%,50%), four (0%,25%), and 21 <=0%. Natural holding duration was median 39.5 days, P25 12.25, P75 82, P10 8 and P90 215.9. By-symbol counts and every episode's path are in the JSON diagnostics. The symbol breakdown is descriptive.

## >=30% MFE cohort

15 natural episodes reached the frozen >=30% marked-return threshold; no censored episode did. All 15 closed with positive actual net P&L. Median retention was 87.4143% (P25 60.4100%, P75 94.9219%; P10 45.9979%, P90 97.8583%). Ten retained >=75%; three retained [50%,75%); one retained [25%,50%); one retained (0%,25%); none ended nonpositive. Median actual final return was 32.2374%. This was measured after natural exits, without selling at any peak.

## Positive-MFE → negative-final episodes

21 of 51 positive-MFE natural episodes crossed to a negative final result, versus 20 of 50 in V2. Their identifiers, marks, final returns, retention and holding durations are in `GIVEBACK_DIAGNOSTICS.json`. This is real mixed retention behavior, concentrated outside the >=30% natural cohort under the frozen definition.

## Profit-retention distribution

The full natural positive-MFE population has a heavy negative tail; the >=30% population has median retention above 87% and a 100% natural final-profit rate. Cohort P10/P25/median/P75/P90 and the disjoint bins are persisted in `GIVEBACK_DIAGNOSTICS.json`. A ratio above 1 is valid when the actual exit fill beats the best prior completed close; negative ratios are valid when final net P&L is negative after a positive marked peak.

## Old vs corrected baseline comparison

Episodes 57→56, natural closures 55→54, fills 388→406, positive-MFE natural episodes 50→51, winner-to-loser natural episodes 20→21. The >=30% cohort remains 15/15 final-profitable; its median retention moves 87.1497%→87.4143%. Median natural holding moves 36→39.5 days. Corrected physical timing, extra partial fills, changed inventory/hysteresis state, changed episode boundaries and the source-common terminal all contribute, with overlapping effects. See `OLD_VS_NEW_COMPARISON.md` for the channel-specific evidence and table. No statistical significance is claimed.

## Independent verification

`INDEPENDENT_EPISODE_VERIFICATION.json` is PASS. A separate event-ledger implementation imports neither primary episode calculations nor production strategy/accounting code. It checks all 56 episode starts/ends, 406 fills, two terminal settlements, every per-episode fill and marked quantity/P&L path, 1,945 snapshot NAVs, actual final returns, MFE, retention and >=30% membership. The primary reconciliation also passes. Focused synthetic and full-replay episode tests pass. Complete normal suite: 1,173 passed, four skipped, two deprecation warnings. Ruff check/format, strict mypy, strict JSON parsing, staged `git diff --check`, and a credential-pattern privacy scan passed. The accepted upstream causal verifier supplies independent bar/decision/fill provenance; this V3 verifier checks lifecycle reconstruction from its pinned output.

## Interpretation

The corrected data still show many positive-MFE-to-loss round trips, and the natural-population median retention is lower than V2. They do not show a systematic collapse of the meaningful >=30% marked-winner cohort: all 15 naturally closed profitably, with median retention above 87%. The descriptive evidence therefore does not materially overturn the previous research decision. This does not establish that every natural exit is optimal. **MFE is not an executable exit signal.** Its peak is known only in hindsight and is not treated as capturable profit.

## Alternative exits tested

`NONE`

## Production state

Unchanged. No production strategy, risk, execution, persistence, configuration, PAPER or LIVE code was edited; no broker call or order was made.

## Parallel physical-clock isolation

This research branch was created from accepted commit `b135d737` in an isolated worktree. No shared-account, physical-clock, global-slot or `maximum_positions` files were edited. The pre-existing untracked physical-clock work in the original checkout was not touched.

## Exact next action

`CLOSE_AND_ARCHIVE_CORRECTED_CRYPTO_GIVEBACK_AUDIT`

## Final branch SHA

Resolve `research/crypto-exit-giveback-audit-v3` at delivery; the final commit SHA is stated in the completion message because a commit cannot contain its own hash.
