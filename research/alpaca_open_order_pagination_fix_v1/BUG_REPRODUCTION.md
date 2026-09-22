# Open-order pagination bug reproduction

## Observed behavior

Before the production change, `AlpacaClient.get_orders()` delegated one
`GetOrdersRequest(status=OPEN, nested=True)` to `TradingClient.get_orders()`.
The request omitted `limit` and any cursor. Alpaca documents a default limit of
50, so the broker-wide helper returned only the first 50 orders.

The deterministic 51-order regression reproduced this before the fix:

```text
pytest -q tests/execution/test_alpaca_order_pagination.py
AssertionError: assert 50 == 51
```

The fixture ordered the synthetic broker response newest first. `SYM000`, the
oldest still-open order beyond the first 50, was absent from
`AlpacaBroker.all_open_order_symbols()`. No broker request or account data was
used.

The same API contract allows at most 500 orders per response. Therefore
changing only the request to `limit=500` still omits order 501. The regression
suite preserves that one-page comparison and separately proves exhaustive
retrieval at 501 and 1,001.

## Proven scope

All current production order-listing call sites ask for `status=OPEN`; none
queries closed-order history. The shared adapter fix covers broker-wide and
symbol-filtered open-order reads. A later-page API error propagates and cannot
return the accumulated prefix as a complete list.
