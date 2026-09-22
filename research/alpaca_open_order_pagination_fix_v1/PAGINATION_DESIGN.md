# Open-order pagination design

The exhaustive operation lives at the Alpaca adapter boundary. Broker call
sites use `AlpacaBroker._get_open_orders()`, which delegates to the named
`AlpacaClient.get_open_orders()` operation. An explicit limit is rejected by
that operation; callers cannot turn a complete working-order read into a
single capped page accidentally. The SDK's ordinary `get_orders()` behavior
remains available for intentionally bounded and non-OPEN queries.

For an exhaustive open-order query:

1. Preserve the request filters and force `status=open`.
2. Set `limit=500`, `direction=desc` by default, and retain `nested` and symbol
   filters.
3. Append pages in API order. Use the final order ID as the exclusive
   `before_order_id` cursor; for ascending requests use `after_order_id`.
4. Deduplicate only by broker order ID. A missing ID fails closed.
5. Stop only after a page contains fewer than 500 orders.
6. Fail on a full page with no new IDs, a repeated/nonadvancing cursor, an
   unsupported direction, or an API/DTO error. Exceptions propagate; no
   partial prefix is returned.

The result is a deterministic concatenation in the API's requested
submission-time order. Order IDs, not timestamps, symbols, or client-order IDs,
define cursor and deduplication identity. Exclusive ID cursors avoid timestamp
tie omissions.

There is no transactional snapshot across HTTP pages. New orders may appear
before the descending cursor after page one; fills and cancellations may
remove orders during traversal. The method returns the complete sequence
exposed by that cursor walk or fails. Existing startup reconciliation and the
decision loop's nominal 60-second reconciliation scheduling remain the paths
that observe later broker state; this repair does not promise an immediate
snapshot, add a second read, or add repeated polling.
