"""Test-gap closures for the audit wave 2 test-isolation and fixture findings (Q2-5, Q2-6).

Each test pins one invariant of the test harness: the production-state guard,
the blocked-network and dotenv guards, and the fake Alpaca client's counters
and lookup shapes.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.alpaca import DefiniteOrderRejection
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide
from parayabuck.web.managers.backtest import BacktestManager
from parayabuck.web.security import is_trusted_client, is_trusted_host
from tests import conftest as guard
from tests.conftest import ProductionStateAccessError, _is_loopback, _path_text, protected_state_path
from tests.fake_client import FakeNotFound, FakeTradingClient
from tests.helpers import make_broker

REPO_ROOT = Path(__file__).resolve().parents[1]
_REAL_OPEN_NEW_TAB = webbrowser.open_new_tab


def _expect_refused_access() -> None:
    """Consume the refusal a test triggered on purpose, so teardown does not report it."""
    guard._STATE_ACCESSES.clear()


# --- Q2-5-1: file: URIs are production paths too ---------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("file:///abs/x.sqlite3?mode=ro", "/abs/x.sqlite3"),
        ("file://localhost/abs/x.sqlite3", "/abs/x.sqlite3"),
        ("file:data/runtime/parayabuck.sqlite3?mode=ro", "data/runtime/parayabuck.sqlite3"),
        ("file:/abs%20dir/x.sqlite3", "/abs dir/x.sqlite3"),
        ("plain/path.sqlite3", "plain/path.sqlite3"),
    ],
)
def test_path_text_unwraps_sqlite_file_uris(raw: str, expected: str) -> None:
    assert _path_text(raw) == expected


def test_a_read_only_uri_to_the_live_ledger_is_refused_before_connect() -> None:
    uri = f"file:{REPO_ROOT}/data/runtime/parayabuck.sqlite3?mode=ro"
    assert protected_state_path(uri) is not None
    with pytest.raises(ProductionStateAccessError):
        sqlite3.connect(uri, uri=True)
    _expect_refused_access()


def test_a_uri_to_the_shipped_config_is_refused_for_writing() -> None:
    uri = f"file:{REPO_ROOT}/config/settings.yaml?mode=rw"
    with pytest.raises(ProductionStateAccessError):
        sqlite3.connect(uri, uri=True)
    _expect_refused_access()


# --- Q2-5-11: the open/connect hooks have direct tests ---------------------


def test_a_write_open_of_production_state_is_refused() -> None:
    with pytest.raises(ProductionStateAccessError):
        open(REPO_ROOT / "data" / "runtime" / "paper_equity_peak.json", "w")
    _expect_refused_access()


def test_an_os_level_write_open_of_production_state_is_refused() -> None:
    target = str(REPO_ROOT / "data" / "cache" / "warmup.sqlite3")
    with pytest.raises(ProductionStateAccessError):
        os.open(target, os.O_WRONLY | os.O_CREAT)
    _expect_refused_access()


def test_sqlite_connect_to_the_live_ledger_path_is_refused() -> None:
    with pytest.raises(ProductionStateAccessError):
        sqlite3.connect(str(REPO_ROOT / "data" / "runtime" / "parayabuck.sqlite3"))
    _expect_refused_access()


def test_writing_the_shipped_config_is_refused_but_reading_it_is_allowed() -> None:
    settings = REPO_ROOT / "config" / "settings.yaml"
    with open(settings, encoding="utf-8") as handle:
        assert handle.read()
    with pytest.raises(ProductionStateAccessError):
        open(settings, "a", encoding="utf-8")
    _expect_refused_access()


def test_tmp_path_writes_and_connects_are_allowed(tmp_path: Path) -> None:
    with open(tmp_path / "note.txt", "w", encoding="utf-8") as handle:
        handle.write("ok")
    sqlite3.connect(str(tmp_path / "scratch.sqlite3")).close()
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "ok"


# --- Q2-5-7: the tax rate files are production state -----------------------


def test_the_tax_rate_files_are_production_state() -> None:
    assert protected_state_path(REPO_ROOT / "data" / "tax" / "usdtry.csv") is not None
    assert protected_state_path(REPO_ROOT / "data" / "tax" / "yiufe.csv") is not None


# --- Q2-5-3: loopback classification and the blocked-connect guard ---------


@pytest.mark.parametrize("host", ["127.0.0.1", "127.9.9.9", "localhost", "::1"])
def test_loopback_addresses_pass_the_guard(host: str) -> None:
    assert _is_loopback((host, 80))


@pytest.mark.parametrize("host", ["127.example.com", "127.0.0.1.attacker.test", "203.0.113.5", "example.com"])
def test_hostnames_that_merely_start_with_127_are_not_loopback(host: str) -> None:
    assert not _is_loopback((host, 80))


def test_a_connection_to_a_127_hostname_is_refused_without_a_lookup() -> None:
    with pytest.raises(guard.OutboundNetworkBlocked):
        socket.create_connection(("127.example.com", 9))


# --- Q2-5-2: every parayabuck module holds the guarded dotenv loaders ------


def test_no_parayabuck_module_keeps_an_unguarded_dotenv_loader() -> None:
    assert guard._real_load_dotenv is not None and guard._real_dotenv_values is not None
    real = {"load_dotenv": guard._real_load_dotenv, "dotenv_values": guard._real_dotenv_values}
    for name, module in list(sys.modules.items()):
        if not name.startswith("parayabuck"):
            continue
        for attr, original in real.items():
            assert vars(module).get(attr) is not original, f"{name}.{attr} bypasses the dotenv guard"


def test_config_load_dotenv_never_loads_the_repo_env() -> None:
    from parayabuck import config as config_module

    assert config_module.load_dotenv(str(REPO_ROOT / ".env")) is False


# --- Q2-5-8: no test opens a browser tab -----------------------------------


def test_no_test_can_open_a_browser_tab() -> None:
    assert webbrowser.open_new_tab is not _REAL_OPEN_NEW_TAB
    assert webbrowser.open_new_tab("http://127.0.0.1:9/") is False


# --- Q2-5-9: production trust checks do not relax outside pytest -----------


def test_test_peers_are_refused_when_pytest_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert is_trusted_host("testserver") is False
    assert is_trusted_client("testclient") is False


# --- Q2-5-4: a test-built backtest manager must not target the repo reports ---


@pytest.mark.xfail(
    strict=True,
    reason="Q2-5-4: BacktestManager defaults to the repo-relative reports/ root and "
    "reports/ is not a guarded state dir, so start() in a test prunes reports/web-*",
)
def test_a_backtest_manager_built_in_a_test_does_not_target_the_repo_reports_dir() -> None:
    assert BacktestManager().reports_root.resolve() != (REPO_ROOT / "reports").resolve()


# --- Q2-6-2 / Q2-6-3 / Q2-6-4: the fake client mirrors the SDK -------------


def test_an_unknown_order_id_raises_a_404_like_the_sdk() -> None:
    fake = FakeTradingClient()
    with pytest.raises(FakeNotFound) as by_client:
        fake.get_order_by_client_id("parayabuck-missing")
    assert by_client.value.status_code == 404
    with pytest.raises(FakeNotFound) as by_id:
        fake.get_order_by_id("broker-missing")
    assert by_id.value.status_code == 404


def test_a_refused_submit_the_broker_never_accepted_is_a_definite_rejection(tmp_path: Path) -> None:
    class _Refusal(Exception):
        status_code = 422

    fake = FakeTradingClient()
    broker = make_broker(
        tmp_path,
        client=fake,
        ledger=ExecutionLedger(str(tmp_path / "exec.sqlite3")),
        peak_state_path=tmp_path / "peak.json",
        portfolio_owned_quantities=lambda: {},
        portfolio_open_orders=lambda: [],
    )
    request = PortfolioOrderRequest(
        client_order_id="parayabuck-refused-1",
        decision_id="decision-refused",
        symbol="IWM",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=10,
        reference_price=100.0,
        decision_timestamp=datetime.now(UTC),
        strategy_name="test",
        strategy_version="1",
    )
    broker._has_open_order = lambda sym: False  # type: ignore[attr-defined]
    broker.asset_trading_rules = lambda sym, m: SimpleNamespace(  # type: ignore[assignment]
        canonical_symbol=sym,
        market=m,
        minimum_order_size=1,
        quantity_increment=1,
        price_increment=0.01,
        minimum_order_notional=1,
    )
    fake.script_submit = lambda req: (_ for _ in ()).throw(_Refusal("insufficient buying power"))

    # The recovery lookup finds no order; the fake answers 404 as the SDK does,
    # so the refusal is a definite rejection rather than an ambiguous write.
    with pytest.raises(DefiniteOrderRejection):
        broker.submit_portfolio_order(request)


@pytest.mark.parametrize(
    ("request_type", "counter"),
    [("MarketOrderRequest", "submitted_market_orders"), ("StopLossRequest", "submitted_stops")],
)
def test_an_accepted_scripted_submit_is_counted(request_type: str, counter: str) -> None:
    fake = FakeTradingClient()
    request_cls = type(request_type, (), {})
    fake.script_submit = lambda req: SimpleNamespace(id="broker-1", status="accepted")

    fake.submit_order(request_cls())

    assert getattr(fake, counter) == 1


def test_a_scripted_failure_is_not_counted_as_an_accepted_submit() -> None:
    fake = FakeTradingClient()
    fake.script_submit = lambda req: (_ for _ in ()).throw(Exception("timeout"))

    with pytest.raises(Exception, match="timeout"):
        fake.submit_order(SimpleNamespace(client_order_id="c-timeout", symbol="IWM", side="buy", qty=1))

    assert fake.submitted_market_orders == 0


def test_a_filled_order_read_by_id_reports_its_fill() -> None:
    fake = FakeTradingClient()
    fake.submit_order(SimpleNamespace(client_order_id="c-fill", symbol="IWM", side="buy", qty=10))
    order = fake.orders_by_client_id["c-fill"]
    order.status = "filled"
    order.filled_qty = 10.0
    order.filled_avg_price = 100.5

    by_id = fake.get_order_by_id("broker-c-fill")

    assert by_id.status == "filled"
    assert float(by_id.filled_qty) == 10.0
    assert float(by_id.filled_avg_price) == 100.5
