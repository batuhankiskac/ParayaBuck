# Installed Alpaca SDK capability audit

The project constrains `alpaca-py` to `>=0.43.5,<0.44`; both
`requirements.lock` and `requirements-dev.lock` pin **0.43.5**, as does the
installed `.venv`. This was verified with the project virtual environment on
2026-09-22.

In 0.43.5, `GetOrdersRequest` exposes `status`, `limit`, `after`, `until`,
`direction`, `nested`, `side`, and `symbols`. It has no `before_order_id` or
`after_order_id` fields. Constructing it with those extra names does not add
them to `to_request_fields()`.

The installed `TradingClient.get_orders()` converts the request model to
parameters, calls one `GET /orders`, and validates that single response as a
list of orders. It does not iterate order pages.

The repair avoids a dependency upgrade. It sends cursor parameters through the
existing authenticated `TradingClient.get("/orders", params)` method, then
validates each response page with Alpaca's `Order` model. The normal session,
authentication, timeout, retry, and error behavior remain owned by the SDK.

Official references: [GetOrdersRequest](https://alpaca.markets/sdks/python/api_reference/trading/requests.html)
and [TradingClient orders](https://alpaca.markets/sdks/python/api_reference/trading/orders.html).
