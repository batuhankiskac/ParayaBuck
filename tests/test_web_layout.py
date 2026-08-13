from __future__ import annotations

import asyncio

from starlette.requests import Request

from parayabuck.web import server


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )


def test_dashboard_prioritizes_money_chart_and_positions() -> None:
    response = asyncio.run(server.serve_index(_request("/")))
    html = bytes(response.body).decode()

    primary_metrics = html.index('class="metric-grid metric-grid-primary"')
    secondary_metrics = html.index('class="metric-grid metric-grid-secondary"')
    chart = html.index('id="equityChart"')
    positions = html.index('id="positionsTableBody"')
    strategies = html.index('class="strategy-grid"')

    assert primary_metrics < secondary_metrics < chart < positions < strategies
    assert 'id="backtestRunBtn"' not in html
    assert 'id="operationsDetail"' not in html
    assert 'id="terminalLogView"' not in html
    assert 'href="/tools"' in html
    assert response.headers["cache-control"] == "no-store"


def test_tools_page_contains_technical_workflows() -> None:
    response = asyncio.run(server.serve_tools(_request("/tools")))
    html = bytes(response.body).decode()

    assert 'id="backtestRunBtn"' in html
    assert 'id="operationsDetail"' in html
    assert 'id="terminalLogView"' in html
    assert 'src="/static/js/tools.js?v=20260813-1"' in html
    assert 'href="/"' in html
    assert response.headers["cache-control"] == "no-store"
