# Regression test matrix

All cases use synthetic local orders and make no broker/API call.

| Orders / condition | Expected proof |
|---|---|
| 0, 1, 49 | Empty and below-default-page enumeration is complete. |
| 50 | Exact old default-page boundary is complete. |
| 51 | Oldest order beyond Alpaca's default 50 is present after repair. |
| 499 | Below-500 page is complete. |
| 500 | Full page is followed by an empty exhaustion page. |
| 501 | Second ID-cursor page adds the final order. |
| 1,001 | Three pages (500, 500, 1) are complete. |
| Explicit `limit=500` one-page SDK request | Still returns only 500 of 501; exhaustive helper rejects explicit cap. |
| Repeated page / boundary overlap | Repeated cursor fails; overlapping IDs appear once in output. |
| Missing order ID | Fails closed. |
| Failure on page two | Exception propagates; caller receives no partial list. |
| Full page with timestamp filter | Fails rather than combining forbidden cursor and timestamp parameters. |
| `symbols`, `nested`, `status`, `direction` | Filters are identical across requests; ascending uses `after_order_id`, descending uses `before_order_id`. |

The suite contains 18 tests after parameter expansion. Repeated focused runs
and the repository-wide suite are recorded in `FINAL_CORRECTNESS_REPORT.md`.
