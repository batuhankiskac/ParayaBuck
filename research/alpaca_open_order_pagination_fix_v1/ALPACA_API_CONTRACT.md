# Alpaca order-list API contract

Verified 2026-09-22 against the official [Get All Orders reference](https://docs.alpaca.markets/us/reference/getallorders-1).

- Endpoint: `GET /v2/orders`.
- `status`: `open`, `closed`, or `all`; defaults to `open`.
- `limit`: defaults to 50; maximum 500.
- `direction`: submission-time order, `asc` or `desc`; defaults to `desc`.
- `nested`: rolls multi-leg orders under the primary order's `legs` field.
- Filters include `symbols`, `side`, and `asset_class`.
- `before_order_id` and `after_order_id` are exclusive order-ID cursors. They
  are mutually exclusive with each other and cannot be combined with timestamp
  `after` or `until` filters.
- The API describes no continuation token in the response. The next page is
  requested by passing the final order's ID as the exclusive cursor.

The repair requests 500 rows per page. For descending results it sends
`before_order_id=<last ID>`; ascending results use
`after_order_id=<last ID>`. It retains the caller's status, symbol, side,
nested, direction, and other query filters. It stops after a response shorter
than 500. A full timestamp-filtered page fails closed because Alpaca forbids
combining timestamp and order-ID pagination.

This is a best-effort enumeration, not a transactional broker snapshot. Orders
can be submitted, filled, or canceled while pages are being read. A later
normal reconciliation sees the then-current broker state; this repair does not
add polling or claim snapshot isolation.
