# Same-bug-class audit

Scope: Alpaca list operations used by this repository that can return a
collection or a continuation token. No unrelated pagination refactor was made.

| Endpoint/path | Classification | Evidence and scope |
|---|---|---|
| Trading orders, `GET /v2/orders` | `PAGINATION_SAFE` after repair | All production OPEN queries use the cursor-exhausting adapter operation. Closed/all order history is not currently queried by these paths. |
| FILL activities, `GET /v2/account/activities/FILL` | `POTENTIALLY_INCOMPLETE` | Code sends `page_token` and `page_size=100`; however, a missing/repeated cursor logs and breaks, then the accumulated activities are processed and the method can advance its watermark. A repeated page could also be appended twice. This is a separate reconciliation issue and was not changed in this patch. |
| Positions, `GET /v2/positions` | `BOUNDED_BY_CONTRACT` | Alpaca documents a list of all current open positions; the endpoint has no limit or page-token parameters. Installed `TradingClient.get_all_positions()` makes one request and validates the returned list. |
| Assets, `GET /v2/assets` | `BOUNDED_BY_CONTRACT` | Alpaca documents the response as an array of all assets and exposes no limit or pagination parameters for this endpoint. Installed `TradingClient.get_all_assets()` makes one request. |
| Stock/crypto historical bars | `PAGINATION_SAFE` for repository windows | Installed alpaca-py follows `next_page_token`. The repository bounds requests at 10,000 total expected rows and splits larger windows before calling the SDK. |

The FILL activity item is a distinct follow-up: on a repeated/missing token,
the current code does not fail closed before applying the partial history and
updating its watermark. It may affect fee/fill reconciliation, but it is not
part of the OPEN-order enumeration repair and is not silently included here.

Official references: [orders](https://docs.alpaca.markets/us/reference/getallorders-1),
[activities](https://docs.alpaca.markets/us/reference/getaccountactivitiesbyactivitytype-1),
[positions](https://docs.alpaca.markets/us/v1.1/reference/getallopenpositions),
[assets](https://docs.alpaca.markets/us/reference/get-v2-assets-1),
and [historical bars](https://docs.alpaca.markets/us/reference/stockbars).
