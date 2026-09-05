# Crypto MAX candidate — economic study record (2026-09)

**Status:** economic matrix completed with a raw technical schema block; independent metric extraction is available for review. This report does not authorize PAPER or LIVE deployment.

## Candidate and novelty provenance

The candidate is `crypto_max_22d_hyst4` (operational identity:
`crypto_max_return_economic_2026_09`). Its only changed factor is the ranking
score: for each symbol, compute
`MAX_i(P_i / P_{i-1} - 1)` over the last 22 completed single-day returns,
requiring 23 consecutive completed D1 bars, then rank ascending with symbol
tie-break. HYST4 retention, 96-hour cadence, top-2 selection, equal weights,
and the 5% account-level crypto allocation remain inherited from
`crypto_low_variance_hysteresis_v1`.

The trace-complete novelty parent is [N2 frozen receipt](../../data/research/crypto_max_return_novelty_2026_09/attempt_02_trace_complete/n2_frozen_receipt.json)
(manifest `78e5c4a2c60a93e2a2c395be154938e44f13640985f6ceb5e888ae75da6a7192`).
It records no economic engine, fills, PnL, costs or promotion. The observed
input is `data/historical/crypto_expanded_1h.csv`, SHA-256
`73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`.

## Frozen economic protocol

The frozen economic input record is [the canonical manifest](../../data/research_manifests/crypto_max_economic_manifest_2026_09_05_frozen.json),
canonical self-hash `f8f09ba3db6798bcce160f8de95889fd4ad7ac37d7291fa9eb8bb6896f9e30ac`.
The validator currently requires the legacy-compatible status
`FROZEN_NOVELTY`; the manifest also records `economic_freeze_status:
FROZEN_ECONOMIC`. The immutable copy and receipt are under
`data/research/crypto_max_return_economic_2026_09/attempt_01_canonical/`.

The matrix is exactly 3 arms × 2 fresh-reset windows × 3 cost tiers = 18
cells: MAX, incumbent low-variance, and lagged dollar-volume control; full
`2021-04-01..2026-07-31`; recent `2024-01-01..2026-07-31`; costs 1x/2x/3x.
The detailed timing and gate definitions are in
[the protocol](crypto_max_economic_protocol_2026_09_05.md). Native execution
uses unshifted 1h bars, shifted completed-D1 signal availability (+24h), fixed
UTC 96h decisions, and the first strictly later 1h open. The terminal policy is
native end mark without forced crypto liquidation.

## Data and interpretation limits

The configured 22-symbol pool and bar-availability/scanner proxy are not a
historical venue-PIT completeness proof. Feed volume units also limit the
liquidity comparator’s interpretation. Results are account-level with 5%
crypto exposure and 95% idle zero-yield native cash. Fold and BTC-regime
statistics, if present, are descriptive only. A passing economic gate would
retain this as research evidence; it would not establish PAPER/LIVE readiness.

## Matrix result (to be filled after the run)

| Window | Arm | 1x | 2x | 3x |
|---|---|---|---|---|
| full | MAX | pending | pending | pending |
| full | incumbent | pending | pending | pending |
| full | liquidity control | pending | pending | pending |
| recent | MAX | pending | pending | pending |
| recent | incumbent | pending | pending | pending |
| recent | liquidity control | pending | pending | pending |

**Technical verdict:** pending 18-cell output audit (unique cells, finite
metrics, hashes/as-of, actual fee/terminal ledger).

**Economic verdict:** pending. Once technically valid, exact R1–R5 evaluation
returns `REJECT` if any condition fails and `RETAIN_FOR_RESEARCH` only if all
conditions pass. No zero or missing value will be substituted for an absent
metric.

## Final economic result and independent audit

The canonical raw output contains 18 cells but is marked technical `BLOCKED`
because the runner placed `turnover` at cell level while the evaluator expected
`metrics.turnover`. The independent audit mechanically mapped that existing
finite field into the expected location for validation; it changed no numeric
value, source, strategy, or engine output. Audit artifact:
[economic_results_independent_audit.json](../../data/research/crypto_max_return_economic_2026_09/attempt_01_canonical/economic_results_independent_audit.json), SHA
`cf71f1b241f6f6899e03f646ddb3b182f3677b564e511e22abc6d73dee77ae43`.

The independent 18-cell audit found all five frozen economic gates false, so
MAX is **REJECT** under the predeclared protocol. Returns below are total-window
account returns with 5% crypto allocation, not annualized returns.

| Window / cost | MAX return / CAGR / DD | Incumbent return / CAGR / DD | Liquidity return / CAGR / DD |
|---|---:|---:|---:|
| Full 3x | -0.9612% / -0.1809% / 8.1406% | 2.2523% / 0.4184% / 7.2528% | 3.3939% / 0.6276% / 6.2729% |
| Recent 3x | -1.1538% / -0.4487% / 6.2433% | 0.8664% / 0.3348% / 3.9618% | 0.8398% / 0.3246% / 4.4982% |
| Full 1x reference | 2.9635% / 0.5490% / 7.3113% | 4.3493% / 0.8013% / 6.9957% | 3.6849% / 0.6807% / 6.2339% |

Gate failures are numeric: R1 fails because MAX 3x return is negative in both
windows; R2 fails because full 1x Sharpe is `0.1591` versus incumbent `0.2266`,
and the drawdown/CAGR alternative also fails (`7.3113% > 90%*6.9957%`, and
`0.5490% < 90%*0.8013%`); R3 fails on recent 3x DD (`6.2433% >
1.5*3.9618%`); R4 fails on recent 2x return (`-0.1199%`); R5 fails because
MAX 3x return is below the liquidity control in both full and recent windows.

This is a frozen research rejection, not a PAPER/LIVE recommendation. The
configured pool, bar-availability scanner proxy, feed-volume units and lack of
historical venue-PIT completeness remain material limitations. The original raw
`BLOCKED` result and all prior N2/C2 artifacts remain preserved; no retuning or
new backtest was performed after the canonical run.

The executable postprocessor used for the independent audit is
`data/research/crypto_max_return_economic_2026_09/attempt_01_canonical/audit_persisted_economic_results.py`, SHA
`d37e16b61323873d9fe3543e9824efba6b4d70089bdc8cc59567ee5fb32dd01c`.
It confirmed 18/18 technical cells and R1–R5 `REJECT`.

Cost traces are interpreted conservatively. Full/MAX 1x versus 2x first
diverges at `2025-07-31T00:00Z`: scores/ranks remain identical, while
`previousheld` differs because execution history feeds back into state. The
low-variance 1x versus 2x trace is exactly equal; its 1x versus 3x first
difference is `2024-03-02T00:00Z`, with prior observer/state feedback. Cash and
equity first diverge at the first fill, `2021-04-01T01:00Z`. These observations
do not support an unconditional cost-trace parity claim. The first decision is
`2021-04-01T00:00Z` and the last native mark is `2026-07-31T23:00Z`.
