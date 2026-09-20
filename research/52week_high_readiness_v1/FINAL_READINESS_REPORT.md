# Frozen G1 readiness report

## Readiness decision

`G1_READY_FOR_FROZEN_ECONOMIC_TEST`

Every frozen G1 prerequisite is proven. This authorizes only a separate future task to run the already-frozen A/B economic experiment. It does not authorize candidate economics here or any PAPER/LIVE promotion.

## Starting main

- Local `main`: `db1a6e9e4af4a5000dca0a54a971ce99577c676c`
- `origin/main`: `db1a6e9e4af4a5000dca0a54a971ce99577c676c`
- Starting tree: clean
- Maintenance branch: `maintenance/backtest-canonical-data-cost-correctness-v1`

## Frozen design identity

- Design branch: `research/52week-high-design-v1`
- Design commit: `72b51a0e5c03f2ce0a8790b197cf360b26b33cd3`
- All five frozen artifacts matched their recorded SHA-256 values in `DESIGN_INPUT_RECEIPT.json`.
- The design files were read from the design commit and were not modified or copied into this branch.

## Blocker classification

| Blocker | Classification | Fix location | Status |
|---|---|---|---|
| `1Day` / `1d` | `GENERAL_BACKTEST_CORRECTNESS` | Generic CSV loader | PASS |
| Canonical corporate actions / dividend dedup | `GENERAL_BACKTEST_CORRECTNESS` | Generic loader and replay queue | PASS |
| Entity lifetime loader | `GENERAL_BACKTEST_CORRECTNESS` | Reusable strict loader | PASS |
| Last completed session of month | `CANDIDATE_RESEARCH_ONLY` | Research NYSE policy | PASS |
| Broad-pool rank through `t` | `CANDIDATE_RESEARCH_ONLY` | Research structural ranker | PASS |
| All-in regulatory fee multiplier | `GENERAL_BACKTEST_CORRECTNESS` | Backtest engine/product runner | PASS |
| Fresh V3 reproduction | `NOT_ACTUALLY_A_DEFECT` | Control verification | PASS |

## Generic correctness fixes

Maintenance commit `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de` added explicit timeframe normalization, canonical-v5 cash-dividend adaptation with economic apply-once behavior, replay-watermark protection, a strict lifecycle loader, and dated equity regulatory-fee scaling. Production broker execution, production monthly cadence, strategy registry, V3 strategy, and configuration were not changed.

## Main integration

The maintenance branch was fast-forwarded into `main` only after the fresh V3 gate passed. Corrected `main` was pushed to `origin/main` at `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de`.

## Canonical V3 control parity

Fresh Control-A-only `equity_residual_momentum_v3` at 1× reproduced the valid canonical baseline exactly:

- 2,406 curve/decision-bar points;
- 495 targets;
- 2,054 fills;
- 5 terminal events;
- identical target, fill, and terminal-event hashes;
- maximum absolute deterministic metric difference: `0.0`.

No rejected-strategy archive economics were inspected.

## Data normalization

Source SHA-256 remained `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`. All 11,686,832 rows and 9,010 entities loaded. Raw spellings were 3,915,364 `1d` and 7,771,468 `1Day`; all normalized to `1d`; zero rows were rejected and source bytes were untouched.

## Corporate-action handling

Source SHA-256 was `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`. Of 143,446 rows, 141,908 were cash-dividend rows. The key `(canonical_symbol, ex_date_utc, DIVIDEND, decimal_rate)` found 157 duplicate economic events and produced 141,751 applied events. Split and stock-dividend rows were not reapplied. Engine replay also excludes actions at or before its persisted watermark.

## Lifecycle handling

The strict loader reproduced 9,010 identities: 2,151 explicit terminals, 6,858 active/horizon-censored entities, one rename chain, and one explicit unresolved endpoint (`EOSEW`). Only unambiguous terminal events enter the engine map and their boundaries equal the actual final valid bar. Endpoint status is not used to pre-exclude earlier history.

## Cost multiplier

The product cost multiplier now scales effective-dated SEC/TAF/CAT fees at each fill in addition to slippage, half-spread, and crypto taker fees. Commission remains zero. Boundary tests cover dated schedule changes, TAF caps, 1×/2×/3× arithmetic, product wiring, ordinary fills, final liquidation, and unchanged gross fill geometry.

## Month-end scheduler

The research-only policy uses the pinned NYSE calendar and decides after the final completed exchange session when the next scheduled session is in a new calendar month. It does not infer month-end from future bar presence. Tests cover ordinary ends, weekend ends, exchange holidays, December/January, first-of-next-month rejection, missing-symbol independence, and a strictly later next-session open.

## Broad-pool through-t ranking

The research ranker receives the unchanged PIT eligible pool, validates exactly 252 supplied exchange sessions through completed `t`, rejects missing/ambiguous/nonfinite/nonpositive/future data, ranks higher close/high ratios first with symbol tie-break, then bounds the observation set. Scanner eligibility is not broadened. Roles `SPY`, `SH`, `PSQ`, and `DOG` cannot become targets.

## Candidate structural verification

The candidate remains research-only and unregistered. Structural tests cover the frozen formula, `t` inclusion, 251-session failure, split-adjusted series use, missing bars, ambiguous identity, order and tie-break, role exclusion, top six, equal weights, 0.55 allocation, 0.10 cap, cadence, through-`t`, no `t+1`, next-open ordering, observation capacity, and unchanged eligibility. Focused readiness/general checks passed `90/90`.

## Forward PAPER/runtime gap

Current PAPER cannot perform the candidate's post-close broad-pool-through-`t` ranking or its research-only month-end policy. PAPER remains V3. Successful later economics would still require a separate PAPER-parity implementation and governance review before promotion.

## Independent verification

`verify_readiness.py` reads only persisted JSON evidence and imports no candidate, scheduler, engine, or evidence-builder code. It passed 51 of 51 checks with zero failures.

## Frozen G1 table

| Frozen G1 prerequisite | Result |
|---|---|
| Canonical hashes / rows / normalized sessions | PASS |
| Canonical loader | PASS |
| Lifetime and dividend semantics | PASS |
| Reserved role exclusions | PASS |
| Month-end decision semantics | PASS |
| Broad-pool-through-`t` ranking | PASS |
| All-in 1×/2×/3× fee scaling | PASS |
| No lookahead | PASS |
| Fresh V3 control reproduction | PASS |
| Candidate ranker/target structural parity | PASS |

G2–G9 were not evaluated.

## Candidate economics inspected

`NO`

No candidate returns, CAGR, Sharpe, Sortino, Calmar, drawdown, profit factor, IC, hit rate, P&L, NAV curve, or filled candidate portfolio was calculated.

## Final main SHA

`839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de`

## Research readiness branch SHA

Evidence commit: `9ecf59de6f17a933847f602d16f7aa6e38632696`. The final delivery commit containing this report is recorded in the task handoff because a commit cannot contain its own SHA.
