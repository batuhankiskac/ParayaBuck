# ARCHIVAL_SUPERSEDED_DO_NOT_USE_AS_CURRENT

This earlier source-gap audit is retained for historical context only. Read current status, counts, NEXT state and hashes from the frozen equity-secondary gate report, source matrix, manifest and bound normalized packages.

# Equity secondary source-gap review r2 — 2026-09-09

This is an independent, research-only review revision. It does not mutate the
active `equity_secondary_public_2026_09_08` package and does not run economics.
The frozen contracts are H09, H10, H12, H13, H15 and H16 in
`equity_secondary_causal_protocol_2026_09_08.md`.

The prior audit's `NO_UNTRIED_FREE_CAUSAL_ROUTE_FOUND` conclusion is invalid.
It covered only 13 route records and treated route-specific 403/404 responses
as exhaustion. This revision adds 11 bounded official probes, all with copied
response bytes, headers and sanitized receipts in
`data/research/external/equity_secondary_source_gap_audit_2026_09_09/review_r2/`.
Three routes returned HTTP 200 and eight returned HTTP 403/404. No probe
produced an admissible causal row.

* H09 remains blocked, but 1,337 local receipt-backed filing bodies contain
  375 customer/supplier keyword candidates. The existing scan is a retrieval
  index only. The conservative offline parser finds two receipt-bound named
  customer spans (both Digicomm in AAOI filings), but admits zero because
  direction is explicit as issuer-to-customer, but counterparty identity and
  linked-firm event are not established; valid-to remains open rather than
  guessed.
* H10 remains blocked but locally actionable. The existing package has 333
  valid 10-K/Q bodies: 86 10-K, 3 10-K/A and 244 10-Q. Original/amended and
  prior-version pairing, section normalization and PIT text hashes remain
  parser work. The conservative parser finds three exact same-CIK/report-
  period 10-K/A pairs with source hashes, filed/accepted ordering and section/
  fact diffs; all three are admitted as causal revision-event metadata. SEC
  Companyfacts returned 200 and exposes numeric facts and filing metadata, not
  filing text.
* H12 remains blocked. NYSE `/markets/indices` returned 404 and the historical
  quote route returned current HTML with zero membership/event rows. A dated
  official constituent/change archive remains untested.
* H13 remains blocked. Nasdaq Trader returned a non-data “Page Not Available”
  page and the historical trades query returned 404. Historical SIP/IEX parity
  and executable auction semantics remain unproved; daily OHLC is forbidden.
* H15 and H16 remain blocked. SEC filing-index probes for the AA DEFM14A and
  Form 10-12B samples returned 403. The local DEFM14A parser finds one
  receipt-bound lifecycle-metadata row with explicit parties, consideration,
  termination, closing-condition and break-fee markers; it is not cash-only
  and has no complete event-time ledger. No valid local Form 10/10-12B body
  exists; generic successor/effective-date mentions are rejected. Linked
  8-K/SC/14D-9 and registration/issuer-event exhibits still require exact
  lifecycle/successor parsing.

The package is therefore `NEEDS_FIX`, with `SPEC_COMPLIANCE=FAIL` and
`CODE_DATA_QUALITY=FAIL` for the negative exhaustion claim. These are source
and parser blockers, not economic results. The exact machine report and
manifest pins are `review_report.json` and `review_manifest.json`.

Verification:

```text
.venv/bin/python data/research/external/equity_secondary_source_gap_audit_2026_09_09/review_r2/build_review.py --verify
REVIEW_R2_VERIFIED; routes=11; artifacts=41; successful=3

.venv/bin/python -m pytest -q tests/research/test_equity_secondary_source_gap_review_r2.py
5 passed
```
