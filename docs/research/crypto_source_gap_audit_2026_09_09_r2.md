# Kraken public K10 source-gap acquisition — 2026-09-09 revision 2

This isolated revision probes Kraken's newly identified public routes for the
frozen K10 liquidation candidate. It does not modify the frozen crypto
manifests, run economics, or change K09. Raw response bodies, response headers,
sanitized receipts, and a canonical manifest are under
`data/research/external/crypto_source_gap_audit_2026_09_09/kraken_r2/`.

## Scope and results

- 16 Market Analytics probes covered `PI_XBTUSD` and `PI_ETHUSD`, both
  `liquidation-volume` and `open-interest`, at documented 3,600-second and
  86,400-second intervals over 2024-01-03 and 2026-07-30 bounded windows.
- All 16 returned HTTP 200 structured `result.timestamp` and `result.data`
  arrays. Liquidation-volume values are present, including nonzero historical
  and cutoff-window values. The response has no row-level publication or
  `available_at` field and does not state units, so no rows are admitted.
- Four Trade History probes covered both symbols: an unbounded public request
  and an RFC3339 cutoff cursor at 2026-07-31. The recent requests returned 100
  BTC and 4 ETH `fill` records; the cutoff requests returned HTTP 200 with
  empty history. No observed record had `type=liquidation`, publication time,
  or `available_at`. The documented route is limited to the most recent 100
  trades and at most seven days or the latest engine restart.
- `admitted_rows=0`, `economic_run=false`, and `next_stage_approval=NOT_APPROVED`.
  K09 relevance is explicitly `NO_RELEVANCE_K09_OHLCV_CARD`.

## Route conclusions

The two previously untried official Kraken routes are now receipt-backed. The
Market Analytics route is an exact field-family capability source but remains
`PIT_PENDING` because its bucket timestamps cannot be treated as publication
availability. Trade History is a bounded recent route with exact trade
identity fields and a liquidation type in the documented schema, but this run
has no liquidation records and cannot provide the frozen full/recent history.
Neither route supports a causal admission table yet.

The Kraken-scoped conclusion is
`NO_UNTRIED_FREE_CAUSAL_ROUTE_FOUND_WITHIN_KRAKEN_R2_SCOPE`. The global crypto
source exhaustion conclusion remains pending independent review of other
providers. Retrieval timestamps are retained as retrieval evidence only and
never substituted for `available_at`.

## Verification

`python scripts/research/crypto_source_gap_audit_kraken_2026_09_09.py --verify`
recomputes every body/header hash, receipt binding, transport field, parsed
schema summary, canonical manifest hash, and zero-admission/scope flags. The
focused mutation suite verifies manifest and receipt tampering is rejected.
