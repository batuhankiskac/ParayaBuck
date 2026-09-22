# Open-order call graph and proven consumers

## Shared listing boundary

`TradingClient GET /orders` → `AlpacaClient.get_open_orders()` →
`AlpacaBroker._get_open_orders()` → all production `OPEN` order readers.

`AlpacaBroker._client` intentionally exposes the inner SDK client for other
broker methods. Each production order-listing path now calls the broker helper
so it cannot bypass the exhaustive adapter method.

## Readers

- `AlpacaBroker.all_open_order_symbols()` fetches broker-wide orders, including
  manual orders, and canonicalizes their symbols.
- `PortfolioRuntimeService._effective_open_order_constraints()` unions those
  symbols with account-local nonterminal orders and passes them to
  `TargetToOrderPlanner.plan()`. A broker-visible symbol suppresses both BUY
  and SELL planning for that symbol. If an old manual order is omitted, the
  planner can fail to apply that suppression; the separate symbol-filtered
  submission guard is defense in depth.
- `OrderService.submit_portfolio_order()` calls `AlpacaBroker._has_open_order`
  before submitting. The guard is a symbol-filtered `OPEN` query and rejects a
  same-symbol working order.
- `ProtectionService._open_protective_orders()` reads symbol-filtered `OPEN`
  orders. Protection reconciliation uses these orders to decide whether a
  protective stop exists and whether cancellation/replacement is needed.
- `ReconciliationService.reconcile()` reads account-wide `OPEN` orders for
  protective-order checks and for comparison of broker client-order IDs with
  local open IDs.

Every current production query uses `status=OPEN` and `nested=True`; some also
retain the `symbols` filter. No current order-list call site queries closed
history or `status=all`. Strategy ownership parsing, client-order IDs,
reservation classes, and reconciliation rules are unchanged.

## Limits on the impact claim

The confirmed behavior is omission of broker-visible working-order symbols and
client IDs beyond the first page. The code shows those values suppress target
orders and feed protection/reconciliation checks. No live incident or realized
duplicate is claimed, and this audit does not claim that the omission directly
changes `maximum_positions` or migration economics.
