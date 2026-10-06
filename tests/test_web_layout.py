from __future__ import annotations

from typing import Any

from parayabuck.web.managers.engine import EngineManager
from tests.helpers import web_client


def _get(path: str) -> Any:
    return web_client(EngineManager()).get(path)


def test_dashboard_prioritizes_money_chart_and_positions() -> None:
    response = _get("/")
    html = response.text

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
    response = _get("/tools")
    html = response.text

    assert 'id="backtestRunBtn"' in html
    assert 'id="operationsDetail"' in html
    assert 'id="terminalLogView"' in html
    assert 'src="/static/js/tools.js?v=' in html
    assert 'href="/"' in html
    assert response.headers["cache-control"] == "no-store"


def test_pages_load_only_same_origin_assets() -> None:
    """The CSP forbids inline scripts/styles and third-party origins."""
    for path in ("/", "/tools"):
        html = _get(path).text
        assert "<script>" not in html
        assert 'style="' not in html
        assert "https://" not in html
        assert 'href="/static/css/app.css?v=' in html
