# Alpaca reconciliation API contract

Retrieved 2026-09-24 from official Alpaca documentation.

The repository calls `GET /v2/account/activities/FILL` through its SDK adapter (`/account/activities/FILL` relative path). Alpaca documents `page_size` from 1 through 100, default 100; `page_token` is the **last activity ID of the previous response**; `direction` defaults to descending; `after` and `until` constrain time, and `order_id` filters a specific order. We explicitly request `direction=desc`, `page_size=100`, and advance the token from the final ID. We request one further page, including after a short page, and require an empty list to prove exhaustion. Activity ID is the deduplication identity. `qty` is the incremental fill amount; `cum_qty` and `leaves_qty` describe order progress and are not summed as fills.

Official references: [Account Activities by type](https://docs.alpaca.markets/us/reference/getaccountactivitiesbyactivitytype-1), [Account Activities fields](https://docs.alpaca.markets/us/docs/account-activities).

Order status is retrievable by [broker order ID](https://docs.alpaca.markets/us/reference/getorderbyorderid-1) and [client order ID](https://docs.alpaca.markets/us/reference/getorderbyclientorderid). The [OPEN-order listing](https://docs.alpaca.markets/us/reference/getallorders-1) describes the collection, but absence there does not establish a terminal status. The existing repository helper first tries client ID and then persisted broker ID. Transport uncertainty propagates; a 404 remains unresolved and is handled by the final drift gate.
