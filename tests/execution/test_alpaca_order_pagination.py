from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from alpaca.trading.models import Order
from alpaca.trading.requests import GetOrdersRequest

from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.alpaca_client import AlpacaClient


def _orders(count: int) -> list[Order]:
    return [
        Order.model_construct(id=f"order-{index:04d}", symbol=f"SYM{index:04d}") for index in reversed(range(count))
    ]


class _PaginatedTradingClient:
    """Synthetic endpoint model with Alpaca's default and maximum page limits."""

    def __init__(
        self,
        orders: list[Order],
        *,
        fail_on_call: int | None = None,
        repeat_after_first_page: bool = False,
        overlap_boundary: bool = False,
    ) -> None:
        self.orders = orders
        self.fail_on_call = fail_on_call
        self.repeat_after_first_page = repeat_after_first_page
        self.overlap_boundary = overlap_boundary
        self.sdk_calls: list[Any] = []
        self.http_calls: list[tuple[str, dict[str, Any]]] = []

    def get_orders(self, request: Any) -> list[Order]:
        self.sdk_calls.append(request)
        fields = request.to_request_fields()
        return self.orders[: int(fields.get("limit") or 50)]

    def get(self, path: str, params: dict[str, Any]) -> list[Order]:
        self.http_calls.append((path, deepcopy(params)))
        call_number = len(self.http_calls)
        if self.fail_on_call == call_number:
            raise RuntimeError("synthetic later-page failure")
        if path != "/orders":
            raise AssertionError(f"unexpected path: {path}")

        limit = int(params["limit"])
        direction = params.get("direction", "desc")
        ordered = self.orders if direction == "desc" else list(reversed(self.orders))
        cursor_key = "before_order_id" if direction == "desc" else "after_order_id"
        cursor = params.get(cursor_key)
        if cursor is not None and self.repeat_after_first_page:
            return ordered[:limit]

        start = 0
        if cursor is not None:
            cursor_index = next(i for i, order in enumerate(ordered) if str(order.id) == cursor)
            start = cursor_index + 1
        page = ordered[start : start + limit]
        if cursor is not None and self.overlap_boundary:
            boundary = ordered[start - 1]
            page = [boundary, *page]
        return page


@pytest.mark.parametrize("count", [0, 1, 49, 50, 51, 499, 500, 501, 1001])
def test_all_open_order_symbols_exhausts_default_and_max_pages(count: int, tmp_path: Any) -> None:
    client = _PaginatedTradingClient(_orders(count))
    broker = AlpacaBroker("", "", client=client, runtime_state_dir=tmp_path)

    symbols = broker.all_open_order_symbols()

    assert symbols == {f"SYM{index:04d}" for index in range(count)}
    assert len(client.http_calls) == count // 500 + 1
    assert client.sdk_calls == []
    assert all(params["status"] == "open" for _, params in client.http_calls)
    assert all(params["limit"] == 500 for _, params in client.http_calls)
    assert all(params["nested"] is True for _, params in client.http_calls)


def test_51_orders_reproduces_the_oldest_order_omission_from_default_page() -> None:
    client = _PaginatedTradingClient(_orders(51))
    first_page = client.get_orders(GetOrdersRequest())

    assert len(first_page) == 50
    assert first_page[-1].id == "order-0001"
    assert "order-0000" not in {str(order.id) for order in first_page}


def test_limit_500_is_a_page_size_not_a_completeness_guarantee() -> None:
    client = _PaginatedTradingClient(_orders(501))
    first_page = client.get_orders(GetOrdersRequest(status="open", limit=500))
    adapter = AlpacaClient(client=client)

    assert len(first_page) == 500
    assert first_page[-1].id == "order-0001"
    with pytest.raises(ValueError, match="explicit limit"):
        adapter.get_open_orders(GetOrdersRequest(status="open", limit=500))


def test_order_id_cursor_keeps_filters_and_descending_order_across_501_orders() -> None:
    orders = [
        Order.model_construct(id=f"order-{index:04d}", symbol="AAA" if index % 2 else "BBB")
        for index in reversed(range(501))
    ]
    client = _PaginatedTradingClient(orders)
    adapter = AlpacaClient(client=client)

    result = adapter.get_orders(GetOrdersRequest(status="open", symbols=["AAA", "BBB"], nested=True))

    assert [str(order.id) for order in result] == [str(order.id) for order in orders]
    assert len(client.http_calls) == 2
    first_path, first_params = client.http_calls[0]
    second_path, second_params = client.http_calls[1]
    assert first_path == second_path == "/orders"
    for params in (first_params, second_params):
        assert params["status"] == "open"
        assert params["symbols"] == "AAA,BBB"
        assert params["nested"] is True
        assert params["direction"] == "desc"
        assert params["limit"] == 500
    assert "before_order_id" not in first_params
    assert second_params["before_order_id"] == "order-0001"


def test_ascending_queries_use_after_order_id_and_preserve_api_order() -> None:
    orders = _orders(501)
    client = _PaginatedTradingClient(orders)
    adapter = AlpacaClient(client=client)

    result = adapter.get_orders(GetOrdersRequest(status="open", direction="asc"))

    assert [str(order.id) for order in result] == [str(order.id) for order in reversed(orders)]
    assert client.http_calls[0][1]["direction"] == "asc"
    assert client.http_calls[1][1]["after_order_id"] == "order-0499"


def test_duplicate_order_ids_are_returned_once_across_overlapping_pages() -> None:
    client = _PaginatedTradingClient(_orders(501), overlap_boundary=True)
    adapter = AlpacaClient(client=client)

    result = adapter.get_orders(GetOrdersRequest(status="open"))

    ids = [str(order.id) for order in result]
    assert len(ids) == 501
    assert len(set(ids)) == 501


def test_repeated_page_fails_when_cursor_stops_advancing() -> None:
    client = _PaginatedTradingClient(_orders(501), repeat_after_first_page=True)
    adapter = AlpacaClient(client=client)

    with pytest.raises(RuntimeError, match="pagination"):
        adapter.get_orders(GetOrdersRequest(status="open"))


def test_later_page_error_propagates_without_returning_partial_orders() -> None:
    client = _PaginatedTradingClient(_orders(501), fail_on_call=2)
    adapter = AlpacaClient(client=client)

    with pytest.raises(RuntimeError, match="later-page failure"):
        adapter.get_orders(GetOrdersRequest(status="open"))

    assert len(client.http_calls) == 2


def test_missing_order_id_fails_closed() -> None:
    client = _PaginatedTradingClient([Order.model_construct(id=None, symbol="MISSING")])
    adapter = AlpacaClient(client=client)

    with pytest.raises(RuntimeError, match="without an ID"):
        adapter.get_orders(GetOrdersRequest(status="open"))


def test_timestamp_filtered_full_page_fails_instead_of_returning_partial_results() -> None:
    client = _PaginatedTradingClient(_orders(500))
    adapter = AlpacaClient(client=client)

    with pytest.raises(RuntimeError, match="timestamp filters"):
        adapter.get_orders(GetOrdersRequest(status="open", after="2026-01-01T00:00:00Z"))
