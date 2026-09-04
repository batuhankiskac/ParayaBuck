# Execution-cost transferability P1 — natural PAPER observation

**Record status:** `planned` — the manifest is prepared but not yet committed.<br>
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

The P1 record was prepared from `main` at
`0e15e29b27cb30e7fda69269625003b1967e6722` with a clean worktree. The merge
commit that defines the future sample does not exist yet and is deliberately
not invented. The report runner requires the operator to provide that SHA,
requires it to equal the clean committed checkout `HEAD`, and verifies that
this manifest is tracked there. Until then no row may be called P1 eligible.

## Sample epoch and inclusion rules

Before deployment, the operator records the exact merged P0/P1 SHA,
`settings.yaml` SHA-256, configuration digest, anonymized account identity,
deployment time, first eligible session ID, immutable cutoff, and local
executable-bar/manifest hashes plus feed identity. The current settings file
SHA-256 is `414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353`.

A primary price comparison requires all of the following: PAPER mode; a
session and decision at or after the immutable cutoff; matching account, session lineage,
configuration digest, and market strategy identity; raw event coverage; a
valid nonblocking cumulative quantity/VWAP sequence; exactly one comparable
timeframe; and sufficient local bars for P0's shadow schedule. Pre-cutoff,
legacy/no-raw-event, wrong-account/session, wrong-digest, invalid-sequence,
and non-comparable rows remain visible with explicit reasons but never enter
primary aggregates.

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
claims need 20 independent decisions and 30 orders. A partial-fill outcome
model remains forbidden before 30 partial/reject/expiry events and 300 eligible
orders. Insufficient evidence returns `UNKNOWN`; it never relaxes a threshold.

## Deployment and sample-epoch checklist — operator only

1. Confirm GitHub Actions plus wheel/build are green; record the exact merged SHA.
2. Take a SQLite online backup, record its SHA-256 and location, run
   `PRAGMA integrity_check`, rehearse migration on a disposable copy, and
   compare row-count and ownership invariants.
3. Record the exact settings SHA/configuration digest, anonymized account,
   deployment timestamp, immutable cutoff, first eligible session, and local
   bars/manifest hash and feed identity before interpreting any observation.
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
  --merged-commit <merged-sha> --settings-sha <settings-sha256> \
  --account-identity <anonymized-id> --configuration-digest <digest> \
  --first-eligible-session-id <session-id> --sample-cutoff <timestamp>
```

The runner opens the runtime database `mode=ro` with `PRAGMA query_only=ON`;
it calls no broker/data API and does not start PAPER, place orders, or change
any runtime or backtest setting.
