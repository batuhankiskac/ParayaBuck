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

    equity = html.index('id="equityValue"')
    chart = html.index('id="perfChart"')
    positions = html.index('id="positionsTableBody"')
    strategies = html.index('class="two-col strategy-grid"')

    assert equity < chart < positions < strategies
    assert 'id="backtestRunBtn"' not in html
    assert 'id="operationsDetail"' not in html
    assert 'id="terminalLogView"' not in html
    assert 'href="/tools#backtest"' in html
    assert response.headers["cache-control"] == "no-store"


def test_tools_page_contains_technical_workflows() -> None:
    response = asyncio.run(server.serve_tools(_request("/tools")))
    html = bytes(response.body).decode()

    assert 'id="backtestRunBtn"' in html
    assert 'id="operationsDetail"' in html
    assert 'id="terminalLogView"' in html
    assert 'src="/static/js/tools.js?v=' in html
    assert 'href="/"' in html
    assert response.headers["cache-control"] == "no-store"


def test_pages_load_only_same_origin_assets() -> None:
    """The CSP forbids inline scripts/styles and third-party origins."""
    for handler, path in ((server.serve_index, "/"), (server.serve_tools, "/tools")):
        html = bytes(asyncio.run(handler(_request(path))).body).decode()
        assert "<script>" not in html
        assert 'style="' not in html
        assert "https://" not in html
        assert 'href="/static/css/app.css?v=' in html
