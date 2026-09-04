# Execution-cost transferability P1 — natural PAPER observation

**Record status:** `planned` — technical tooling accepted; no PAPER economic sample recorded.<br>
**Economic verdict:** `UNKNOWN_COST_TRANSFERABILITY`.<br>
**Promotion:** none.

P0 is accepted measurement infrastructure, not evidence that the current 3-bps
price-cost assumption, crypto fee assumption, total costs, or PAPER-to-LIVE
transferability is conservative. P1 collects no observations itself. It adds a
predeclared, read-only way to classify a future natural PAPER sample after an
operator deploys it without changing behavior.

## P0 input contract

The input is the accepted P0 raw-event measurement and current-backtest
dynamic-residual shadow comparator at commits `491ffa1` and `140c3de`, with
the P0-C closure through `c0e167b`. The comparator is reused as-is: zero-cost
next eligible opens, 20 positive known-volume bars, 10% capacity, dynamic
residual recreation, and current modeled price and fee rules. P1 does not
modify `PortfolioBacktestEngine`, strategy, order policy, quantities, or cost
assumptions.

The P1 manifest is committed at `809613a8b8a47966145204d666d84e102fbf1787`.
That commit followed the implementation tests, so this record makes no false
claim that it preceded them. It does precede any future P1 economic sample.
The runner requires an immutable receipt whose manifest and deployed commits,
blob/SHA identity, and commit time match the clean committed checkout `HEAD`.

## Sample epoch and inclusion rules

Before deployment, the operator records an immutable receipt containing the
manifest commit/blob/SHA identity, deployed commit, deployment timestamp,
cutoff, settings SHA-256, local bars/manifest SHA-256 and feed identity, plus
separate equity and crypto configuration digests and first session IDs. The
account identity is `sha256("parayabuck-p1-account-v1\\0" + raw_account_id)`;
the raw account ID is never emitted. The runner checks
`manifest_commit_time <= deployment_timestamp <= sample_cutoff <= as_of` and
uses each market only with its own digest/session lineage.

A primary price comparison requires all of the following: PAPER mode; a
session and decision at or after the immutable cutoff; matching account, session lineage,
configuration digest, and market strategy identity; raw event coverage; a
valid nonblocking cumulative quantity/VWAP sequence; exactly one comparable
timeframe; and sufficient local bars for P0's shadow schedule. Pre-cutoff,
legacy/no-raw-event, wrong-account/session, wrong-digest, invalid-sequence,
and non-comparable rows remain visible with explicit reasons but never enter
primary aggregates. Decisions and raw broker events after `as_of` are
invisible to the historical report, including VWAP, fill ratio, and terminal
state. Every reporting window and gate uses the inclusive interval
`as_of - window <= decision_timestamp <= as_of`.

## Reporting contract

`scripts/execution_cost_transferability_p1_report.py` emits deterministic
7/30/90-day read-only JSON reports. Each market is separate and each window
states eligible decisions/orders, exclusions, raw-event and valid-sequence
coverage, event-local fee completeness, residual mean/median/p75/p95,
aggregate actual versus modeled **price** cost, modeled fees, gate progress,
and the still-unknown verdict. Price residual confidence intervals use a
deterministic bootstrap of decision blocks; partial updates are never treated
as independent samples.

Rows preserve decision-close shortfall as a diagnostic, PAPER versus the
current zero-cost dynamic-open schedule, modeled 3-bps price cost, modeled
fees, residual quantity, terminal outcome, quality states, and all exclusions.
Strata include market, side, symbol, decision origin, immediate/two-phase role,
terminal outcome, notional bucket, order quantity/lagged ADV bucket, fee
status, and feed identity.

P0 fees are event-local. An observed `0` remains distinct from unknown, but
neither supports an actual total-cost sum. Every P1 output therefore keeps
`total_cost_comparison_eligible=false`. A possible P1-F mission may establish
cross-source matching, precedence, deduplication, and completeness; it is not
implemented here.

## Frozen minimums

Equity needs six calendar months, 30 independent scheduled decisions, 150
terminal orders, and 40 terminal BUY plus 40 terminal SELL orders. Crypto
needs six calendar months, 45 independent 96-hour decisions, 80 terminal
orders, and 25 terminal BUY plus 25 terminal SELL orders. Origin-specific
claims need 20 independent decisions and 30 orders. Calendar months use
anniversary arithmetic (with end-of-month clamping, including leap years),
never an elapsed-days approximation. A partial-fill outcome
model remains forbidden before 30 partial/reject/expiry events and 300 eligible
orders. Insufficient evidence returns `UNKNOWN`; it never relaxes a threshold.

## Deployment and sample-epoch checklist — operator only

1. Confirm GitHub Actions plus wheel/build are green; record the exact merged SHA.
2. Take a SQLite online backup, record its SHA-256 and location, run
   `PRAGMA integrity_check`, rehearse migration on a disposable copy, and
   compare row-count and ownership invariants.
3. Create and retain the immutable epoch receipt with both market digests and
   sessions, the domain-separated account hash, deployment/cutoff times, and
   local bars/manifest identity before interpreting any observation.
4. Start PAPER only under the existing operator procedure. Do not induce
   restarts, enlarge orders, alter cadence, or modify strategy behavior to
   manufacture observations.
5. After start, perform only read-only schema/health verification; retain the
   backup. Roll back on schema/ownership/integrity failure or an unexpected
   runtime-health failure, using the recorded backup location.
6. Run the report against a local bar dataset only after the committed epoch
   exists. Review 7/30/90-day windows; they are review windows, never approval
   gates or auto-calibration triggers.

Example (not a deployment command):

```bash
.venv/bin/python scripts/execution_cost_transferability_p1_report.py \
  --db /operator/path/parayabuck.sqlite3 --dataset /operator/path/bars-manifest.json \
  --epoch-receipt /operator/path/p1-epoch-receipt.json
```

The runner opens the runtime database `mode=ro` with `PRAGMA query_only=ON`;
it calls no broker/data API and does not start PAPER, place orders, or change
any runtime or backtest setting.
