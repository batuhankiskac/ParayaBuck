# Canonical baseline decision

`CRYPTO_CANONICAL_BASELINE_CAUSAL_REPAIR_ACCEPTED`

## Starting repository state and physical-clock stop closure

The local starting `main` was `d87adda9ddd35efbceda04af1162a579f0e6e9b6`; remote `main` was `3f97c02826bc42eda8197dbb7b605d299915155a`. The physical-clock branch was verified at `377d260835c5a5f8ea23b61e16bf8103a842b0e0`. The local annotated archive tag `archive/research/physical-availability-clock-v1-canonical-stop-2026-09` points to that exact commit. Its status is `PHYSICAL_CLOCK_REPAIR_EXPOSED_CANONICAL_BASELINE_CORRECTNESS_ISSUE`; it is not a validated combined replay. Local main received only `docs/research/physical_clock_canonical_stop.md` at `bf440ce12a1bcafbe4e88738a48363cd83282b5d`. This maintenance branch starts from that clean local main, not the clock branch. The archive tag and this branch are local; no new remote publication is implied.

## Existing canonical flaw and source semantics

The old isolated canonical control has 455 decisions, 388 fills and fill SHA256 `c8bf27f1d21846bf754a53a72e5da29416c79dd817162606cecc6ce19fe1e07b`. The complete Apr 1 BTC/ETH daily signal is unavailable before Apr 2 00:00 UTC because the 23:00 source hour first ends then. The old orders are labeled Apr 1 00:00 and filled at the Apr 2 00:00 opening boundary, before an order based on that completed signal could precede that price event. Official Alpaca timestamp, crypto historical aggregation and real-time bar documentation were independently rechecked on 2026-09-23; URLs and scoped claims are in `ALPACA_BAR_SEMANTICS_AUDIT.md`. The pinned hourly file remained unchanged: SHA256 `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`.

## Production crypto timing and classification

`PRODUCTION_CAUSAL_HISTORICAL_FILL_PROXY_ONLY_BROKEN`. The inspected normal path is 1m stream → interval aggregator/finalization → finalized bar queue → portfolio worker → 96h label-based cadence → strategy/risk/planner → persisted decision/order → broker submission → later unknown fill. The daily bar is not sent to the strategy before its final component completes. The persisted label is a strategy cadence key, not physical order creation. `PRODUCTION_CRYPTO_TIMING_AUDIT.md` gives the call graph and limits of the conclusion. No PAPER/LIVE code, mode or configuration changed.

## Proxy review and frozen execution contract

The pre-outcome `EXECUTION_PROXY_REVIEW.md` considered the first pinned source hourly open strictly after creation and rejected a same-boundary open without independent preexisting-order evidence. No stronger pinned-data proxy was available; no interpolated midpoint/VWAP or second candidate replay was used. `FROZEN_CRYPTO_EXECUTION_CONTRACT.md` and JSON were committed before corrected outcomes at `4ab8a7748c6d26f3194f3888893a4cb032325c17`. A no-payload source-coverage probe showed the original terminal assumption lacked real final hours for most symbols; the source-common terminal rule was frozen at `2b483b1e5022a23a0c34cc3ab15145da342c89d6` before the first successful replay.

A source hour with start label `t` is complete no earlier than `t+1h`. A complete 24-component UTC daily signal is available at next midnight. The 96h epoch block is evaluated on the original daily label. A resulting physical decision and new order are created no earlier than daily availability. For each pending order, at most one attempt per UTC day uses the first same-symbol pinned hourly **open** whose event timestamp is strictly later than both the day boundary and order creation. A missing 01:00 source hour moves to the next real eligible hour of that same day; no such hour leaves the order pending. Existing sell-before-buy ordering, partial residuals, 20-known-positive-daily-volume mean, 10% participation, 2bps slippage, 2bps full spread, 25bps taker fee, zero fixed commission, 5% sleeve and 0.005 rebalance band remain. The source-common terminal time is `2026-07-31T00:00:00Z`; the precommitted exit instruction predates that hourly opening event and the final account is flat there.

## Apr 1/2 regression and first execution divergence

BTC and ETH old opening references: Apr 2 00:00 UTC at $58,714.99 / $1,968.40. Corrected creation: Apr 2 00:00 UTC. Corrected first admissible source event: Apr 2 01:00 UTC at $58,625.59 / $1,958.13. `FIRST_EXECUTION_DIVERGENCE_AUDIT.json` contains the actual orders, fills, prices, fees and source events. There are 76 corrected orders with multiple participation-limited fills.

## Signal parity and execution effects

Daily OHLCV and PIT universe hashes are identical. All 454 common decision labels match the old control; the source-common terminal boundary removes the old last decision. Eligibility/rank lists match in 451 common decisions, alpha target weights in 448, risk-adjusted target weights in 446. The first alpha target difference is 2024-08-29: old LTC/BTC versus corrected LTC/DOT because changed execution yielded changed held inventory. The first rank-context difference is 2024-09-26: a managed DOT position changes the observation pin set. Classification `SIGNAL_SEMANTICS_CHANGED` applies to realized target outputs, while the 22-day volatility formula, parameters, scanner snapshot identity and hysteresis rule are unchanged. See `SIGNAL_PARITY_AUDIT.json`.

## Determinism and independent causality verification

Two complete corrected replay outputs are byte-identical, each deterministic gzip SHA256 `8ec31d0aa7fd350bf011c5a00ac25cd64fb9be20240a001496bb6e8353792cae`; full canonical JSON SHA256 `cdc3004dbbfe1e13e8f9f9deb5ec80ecbe628236210649a68a8f47aa55f33697`. Section hashes for decisions, orders, fills, snapshots, NAV curve, final state and terminal events are in `INDEPENDENT_CAUSALITY_VERIFICATION.json`. Independent CSV-and-ledger code verified 28,890 daily bars, 454 decisions, 206 orders, 406 fills, 1,945 snapshots and two terminal events. Every signal fill has source price event time strictly greater than order creation. Independent prices, capacity, fees, cash, quantities, NAV and flat terminal state reconcile.

## Old and corrected canonical economics

| Metric | Old superseded | Corrected |
| --- | ---: | ---: |
| Total return | 5.48151854% | 5.42313344% |
| CAGR | 1.00545423% | 0.99548483% |
| Annualized volatility | not in old payload | 3.00349987% |
| Sharpe | 0.34693442 | 0.34481718 |
| Sortino | not in old payload | 0.48295649 |
| Max drawdown | 6.19186948% | 6.23570332% |
| Calmar | not in old payload | 0.15964275 |
| Ending NAV | $105,481.51853584 | $105,423.13343608 |
| Turnover | not in old payload | 3.52352964 |
| Fee / slippage / spread | $899.97 / $72.00 / $36.00 | $919.65 / $73.57 / $36.79 |
| Decisions / fills | 455 / 388 | 454 / 406 |

The economic change was observed after the rule freeze and played no role in choosing or accepting the proxy. Corrected fill SHA256 is `645d444ff3c95ba2f5eab1f0162acc903275e4642c70c79eb03b8248829e1fba`; final-state SHA256 is `7689e62a2e80a0452e42f7359c9722d49e25b21cfe38632901bb29bd953ad5ef`.

## Downstream, documentation and production state

`docs/research/canonical_crypto_baseline.md` now places the causal control above the retained superseded v2 history. `DOWNSTREAM_DEPENDENCY_AUDIT.md` classifies crypto exit economics and total-account work requiring rerun, while blocked shared-account and physical-clock work remain provenance. No downstream study or capacity migration ran. PAPER/LIVE behavior, maximum positions, cap 8, strategy parameters, crypto rebalance band and operating modes are unchanged.

## Exact next action

`RESTART_PHYSICAL_AVAILABILITY_CLOCK_WITH_CORRECTED_CRYPTO_BASELINE`: fresh equity clock parity, corrected crypto parity, combined cap-6 replay and deterministic independent shared-account verification. A separate future decision is required before reopening cap-8 migration.

## Repository identities and validation

Local main at campaign start/finish: `bf440ce12a1bcafbe4e88738a48363cd83282b5d`; remote main remained `3f97c02826bc42eda8197dbb7b605d299915155a` during this work. Local physical-clock archive tag points to `377d260835c5a5f8ea23b61e16bf8103a842b0e0`. Final maintenance branch commit SHA and final validation command outcomes are recorded in the accompanying task completion, after the checks finish.

Validation: focused crypto/terminal tests 39 passed; complete normal pytest 1,174 passed and one skipped with two existing deprecation warnings when localhost bind was permitted. The first sandbox run had 1,167 passes and six localhost socket permission failures; the elevated rerun passed those tests. Ruff check and format, strict configured mypy over 138 source files, independent A/B verifier, byte and section hash checks, JSON/gzip load, `git diff --check`, and changed-artifact credential pattern scan passed. No broker call or network data acquisition was used by the replay and test artifacts.

Reproduce offline from the repository root using the existing pinned source:

```sh
git show archive/maintenance/crypto-replay-parity-v1-2026-09:maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY.json.gz > /tmp/crypto-old-control.json.gz
.venv/bin/python maintenance/crypto_canonical_causal_execution_v1/run_causal.py --out maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz
.venv/bin/python maintenance/crypto_canonical_causal_execution_v1/run_causal.py --out maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_B.json.gz
.venv/bin/python maintenance/crypto_canonical_causal_execution_v1/build_receipts.py --old /tmp/crypto-old-control.json.gz
```

`build_receipts.py` invokes the independent CSV/ledger verifier and checks byte-identical replay gzip files, input identities and section hashes. The first source-coverage probe produced no corrected payload; it did not compare economics or select a performance-favorable proxy.
