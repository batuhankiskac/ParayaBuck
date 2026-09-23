# OPEN order completeness

Classification: **OPEN_ORDER_RECONCILIATION_COMPLETE** for a stable, documented Alpaca OPEN list.

\`AlpacaClient.get_open_orders\` (\`src/parayabuck/execution/alpaca_client.py:102-183\`) uses \`status=open\`, \`limit=500\`, chronological direction, and \`before_order_id\` for descending or \`after_order_id\` for ascending pages. It preserves symbols/nested filters, deduplicates IDs, rejects missing IDs, repeated/no-progress cursors, oversized pages, and later-page errors rather than returning a partial list. A page shorter than 500 ends the scan. The server's documented maximum is 500 and order-ID cursors are exclusive: [Alpaca Get All Orders](https://docs.alpaca.markets/us/v1.4.2/reference/getallorders-1).

\`AlpacaBroker._get_open_orders\` (\`alpaca.py:560\`) requires this adapter. Startup reconciliation calls it for protection (:526) and ID comparison (:607); the planner uses \`all_open_order_symbols\` (\`alpaca.py:546-558\`), which calls the same helper. A >500-order synthetic page boundary is covered by existing pagination tests (see validation). Dynamic additions/deletions between pages are not an atomic broker snapshot; no endpoint snapshot token is documented. That residual concurrency limitation is distinct from the repaired one-page bug.
