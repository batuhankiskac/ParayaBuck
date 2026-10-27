"""Audit wave 2 fix X4: dashboard, account and unit-file findings.

Covers D2-9-3 (config-driven benchmark symbol), D2-9-4 (1D prior-close base),
D2-9-6 (single-basis total P&L), D2-9-7 (external exposure labels), D2-3-7
(retained halts reported after a safety-lock reset), D2-3-9 (immediate alert
for an inactive account), and R2-4-3 (start timeouts). Synthetic fixtures and
tmp_path only: no network, no credentials, no production paths, and the
dotenv loader is stubbed so no .env is read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from alpaca.data import historical

import parayabuck.config as config_module
from parayabuck.config import AppConfig, load_config
from parayabuck.execution import alpaca_account
from parayabuck.execution.alpaca_account import AccountNotActiveError
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.services import performance
from tests.fake_client import FakeTradingClient
from tests.helpers import make_broker, web_client

REPO = Path(__file__).resolve().parents[1]
_SESSION_OPEN = datetime(2026, 10, 27, 13, 30, tzinfo=UTC)  # 09:30 New York, EDT


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    # load_config() reads the dotenv file; tests must never read a real .env.
    monkeypatch.setattr(config_module, "load_env_file", lambda *, override=False: None)


@pytest.fixture(autouse=True)
def _clear_performance_cache() -> Any:
    performance.reset_cache()
    yield
    performance.reset_cache()


def _settings_file(tmp_path: Path, symbol: str | None) -> Path:
    """A copy of the shipped settings with ``app.benchmark_symbol`` set (or left absent)."""
    raw = yaml.safe_load((REPO / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
    app_block = dict(raw.get("app") or {})
    app_block.pop("benchmark_symbol", None)
    if symbol is not None:
        app_block["benchmark_symbol"] = symbol
    raw["app"] = app_block
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


# --- D2-9-3: benchmark symbol comes from the config key ---------------------


def test_app_benchmark_symbol_defaults_to_voo_and_is_validated() -> None:
    assert AppConfig().benchmark_symbol == "VOO"
    assert AppConfig(benchmark_symbol="SPY").benchmark_symbol == "SPY"
    for bad in ("", "voo", "VOO X", "TOOLONGSYMBOLX"):
        with pytest.raises(ValueError, match="benchmark_symbol"):
            AppConfig(benchmark_symbol=bad)


def test_load_config_reads_the_benchmark_symbol_key(tmp_path: Path) -> None:
    assert load_config(str(_settings_file(tmp_path, "SPY"))).app.benchmark_symbol == "SPY"


def test_load_config_without_the_key_keeps_voo(tmp_path: Path) -> None:
    assert load_config(str(_settings_file(tmp_path, None))).app.benchmark_symbol == "VOO"


class _Manager:
    def __init__(self, broker: Any = None, config_path: Path | None = None) -> None:
        self.broker = broker
        if config_path is not None:
            self.config_path = config_path

    def is_paper_mode(self) -> bool:
        return True


def test_benchmark_symbol_follows_the_manager_config_file(tmp_path: Path) -> None:
    assert performance.benchmark_symbol(_Manager(config_path=_settings_file(tmp_path, "SPY"))) == "SPY"


def test_benchmark_symbol_defaults_without_a_config_path() -> None:
    assert performance.benchmark_symbol(_Manager()) == "VOO"


def test_unreadable_config_falls_back_to_voo(tmp_path: Path) -> None:
    assert performance.benchmark_symbol(_Manager(config_path=tmp_path / "missing.yaml")) == "VOO"


class _BarsClient:
    requests: list[Any] = []

    def __init__(self, key: str, secret: str) -> None:
        self.key = key

    def get_stock_bars(self, request: Any) -> Any:
        _BarsClient.requests.append(request)
        symbol = request.symbol_or_symbols
        return SimpleNamespace(data={symbol: [SimpleNamespace(timestamp=_SESSION_OPEN, close=500.0)]})


def test_benchmark_bars_request_the_configured_symbol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _BarsClient.requests = []
    monkeypatch.setattr(historical, "StockHistoricalDataClient", _BarsClient)
    manager = SimpleNamespace(
        get_credentials=lambda: ("key", "secret"),
        config_path=_settings_file(tmp_path, "SPY"),
    )

    points = performance.fetch_benchmark_bars(manager, _SESSION_OPEN, _SESSION_OPEN, 5)

    (request,) = _BarsClient.requests
    assert request.symbol_or_symbols == "SPY"
    assert points == [(_SESSION_OPEN, 500.0)]


def test_payload_names_the_configured_benchmark(tmp_path: Path) -> None:
    broker = _HistoryBroker(points=[(_SESSION_OPEN, 100.0), (_SESSION_OPEN + timedelta(days=1), 110.0)])
    manager = _Manager(broker, config_path=_settings_file(tmp_path, "SPY"))

    payload = performance.portfolio_performance(manager, "1M", benchmark_fetcher=_flat_fetcher)

    assert payload["benchmark_symbol"] == "SPY"


def _flat_fetcher(_manager: Any, start: datetime, end: datetime, minutes: int) -> list[tuple[datetime, float]]:
    return [(start, 500.0), (end, 505.0)]


def test_web_chart_text_does_not_hard_code_the_benchmark() -> None:
    html = web_client(EngineManager()).get("/").text
    assert 'id="legendBenchName"' in html
    assert 'aria-label="Portföy ve kıyas getiri grafiği"' in html
    assert "Portföy ve VOO" not in html
    assert ">VOO <b" not in html
    script = (REPO / "src" / "parayabuck" / "web" / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert '"VOO"' not in script


# --- D2-9-4: the 1D return is measured from the prior close ------------------


class _HistoryBroker:
    account_id = "acct-test"

    def __init__(self, points: list[tuple[datetime, float]], base_value: float | None = None) -> None:
        self.points = points
        self.base_value = base_value
        self.calls: list[tuple[str, str]] = []

    def portfolio_history(self, period: str, timeframe: str) -> list[tuple[datetime, float]]:
        return self.portfolio_history_with_base(period, timeframe)[0]

    def portfolio_history_with_base(self, period: str, timeframe: str) -> tuple[list[tuple[datetime, float]], Any]:
        self.calls.append((period, timeframe))
        return self.points, self.base_value


def _session_points() -> list[tuple[datetime, float]]:
    return [(_SESSION_OPEN, 101.0), (_SESSION_OPEN + timedelta(minutes=5), 102.0)]


def _day_fetcher(calls: list[int], *, prior_close: float = 199.0, fail_daily: bool = False) -> Any:
    def fetch(_manager: Any, start: datetime, end: datetime, minutes: int) -> list[tuple[datetime, float]]:
        calls.append(minutes)
        if minutes >= 1440:
            if fail_daily:
                raise ValueError("daily bars unavailable")
            # Prior session close, then the partial bar of today (excluded: not before today).
            return [
                (datetime(2026, 10, 26, 4, 0, tzinfo=UTC), prior_close),
                (datetime(2026, 10, 27, 4, 0, tzinfo=UTC), 210.0),
            ]
        return [(_SESSION_OPEN, 200.0), (_SESSION_OPEN + timedelta(minutes=5), 202.0)]

    return fetch


def test_1d_series_start_from_the_prior_close_on_both_lines() -> None:
    calls: list[int] = []
    broker = _HistoryBroker(_session_points(), base_value=100.0)

    payload = performance.portfolio_performance(_Manager(broker), "1D", benchmark_fetcher=_day_fetcher(calls))

    assert payload["base_equity"] == 100.0
    assert payload["base_benchmark"] == 199.0
    assert [point["v"] for point in payload["equity"]] == [101.0, 102.0]
    assert [point["v"] for point in payload["benchmark"]] == [200.0, 202.0]
    assert calls == [5, 1440]


def test_1d_drops_the_benchmark_when_its_prior_close_is_missing() -> None:
    calls: list[int] = []
    broker = _HistoryBroker(_session_points(), base_value=100.0)

    payload = performance.portfolio_performance(
        _Manager(broker), "1D", benchmark_fetcher=_day_fetcher(calls, fail_daily=True)
    )

    assert payload["available"] is True
    assert payload["base_equity"] == 100.0
    assert payload["base_benchmark"] is None
    assert payload["benchmark"] == []
    assert "benchmark_error" in payload


def test_1d_without_a_broker_base_keeps_first_point_rebasing() -> None:
    calls: list[int] = []
    broker = _HistoryBroker(_session_points(), base_value=None)

    payload = performance.portfolio_performance(_Manager(broker), "1D", benchmark_fetcher=_day_fetcher(calls))

    assert payload["base_equity"] is None
    assert payload["base_benchmark"] is None
    assert [point["v"] for point in payload["benchmark"]] == [200.0, 202.0]
    assert calls == [5]


def test_other_periods_do_not_use_prior_close_bases() -> None:
    calls: list[int] = []
    broker = _HistoryBroker(_session_points(), base_value=100.0)

    payload = performance.portfolio_performance(_Manager(broker), "1M", benchmark_fetcher=_day_fetcher(calls))

    assert payload["base_equity"] is None
    assert payload["base_benchmark"] is None
    assert calls == [1440]


@pytest.mark.parametrize("raw_base", [None, 0.0, -3.0, float("nan")])
def test_broker_base_value_must_be_a_positive_finite_number(tmp_path: Path, raw_base: float | None) -> None:
    stamps = [1_788_000_000, 1_788_000_300]
    broker = make_broker(tmp_path)
    broker._client = SimpleNamespace(
        get_portfolio_history=lambda request: SimpleNamespace(
            timestamp=stamps, equity=[101.0, 102.0], base_value=raw_base
        )
    )

    points, base = broker.portfolio_history_with_base("1D", "5Min")

    assert len(points) == 2
    assert base is None
    assert broker.portfolio_history("1D", "5Min") == points


def test_broker_base_value_is_returned_with_the_series(tmp_path: Path) -> None:
    stamps = [1_788_000_000, 1_788_000_300]
    broker = make_broker(tmp_path)
    broker._client = SimpleNamespace(
        get_portfolio_history=lambda request: SimpleNamespace(timestamp=stamps, equity=[101.0, 102.0], base_value=100.5)
    )

    points, base = broker.portfolio_history_with_base("1D", "5Min")

    assert base == 100.5
    assert [value for _stamp, value in points] == [101.0, 102.0]


# --- D2-9-7: exposure labels state whether external positions are counted ---


def test_risk_labels_say_external_positions_are_included() -> None:
    index = (REPO / "src" / "parayabuck" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    script = (REPO / "src" / "parayabuck" / "web" / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "Hisse / ETF (dış dahil)" in index
    assert "dış pozisyonlar dahil" in script
    assert 'Yatırımdaki pay (brüt)"' not in script


def test_total_pnl_label_states_its_basis() -> None:
    index = (REPO / "src" / "parayabuck" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "Bugünkü toplam K/Z" in index
    assert "önceki kapanışa göre" in index


# --- D2-3-7: a reset that leaves halts in force reports them -----------------


def _halted_manager(halt_reasons: tuple[str, ...]) -> EngineManager:
    manager = EngineManager()
    manager.engine_state = "halted"
    manager.broker = None
    manager.runtime = SimpleNamespace(
        health=lambda: SimpleNamespace(state="halted", halt_reasons=halt_reasons),
        reset_safety_lock=lambda: None,
    )
    return manager


def test_reset_returns_the_halts_still_in_force() -> None:
    manager = _halted_manager(("pb-stocks_restart_limit",))
    assert manager.reset_safety_lock() == ("pb-stocks_restart_limit",)


def test_reset_with_no_retained_halts_returns_an_empty_tuple() -> None:
    assert _halted_manager(()).reset_safety_lock() == ()


def test_reset_reports_unknown_retained_halts_as_none() -> None:
    calls = {"n": 0}

    def health() -> SimpleNamespace:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("probe failed")
        return SimpleNamespace(state="halted")

    manager = EngineManager()
    manager.engine_state = "halted"
    manager.broker = None
    manager.runtime = SimpleNamespace(health=health, reset_safety_lock=lambda: None)

    assert manager.reset_safety_lock() is None


def test_reset_route_names_the_retained_halts() -> None:
    client = web_client(_halted_manager(("pb-stocks_restart_limit",)))
    response = client.post("/api/engine/reset-safety-lock")
    assert response.status_code == 200
    body = response.json()
    assert body["retained"] == ["pb-stocks_restart_limit"]
    assert "pb-stocks_restart_limit" in body["message"]


def test_reset_route_plain_success_when_nothing_is_retained() -> None:
    client = web_client(_halted_manager(()))
    body = client.post("/api/engine/reset-safety-lock").json()
    assert body["retained"] == []
    assert body["message"] == "Safety lock reset after broker reconciliation and data checks."


# --- D2-3-9: an inactive account alerts at once -----------------------------


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def record(kind: str, *, message: str, **fields: object) -> None:
        captured.append({"kind": kind, "message": message, **fields})

    monkeypatch.setattr(alpaca_account, "emit_execution_alert", record)
    return captured


def _account(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="fake-acct-1234",
        account_number="PA-TEST-0001",
        account_blocked=False,
        trading_blocked=False,
        trade_suspended_by_user=False,
        equity="100000",
        last_equity="100000",
        cash="90000",
        status=status,
        non_marginable_buying_power=None,
    )


def test_inactive_account_alerts_once_and_refuses_the_snapshot(tmp_path: Path, alerts: list[dict[str, Any]]) -> None:
    fake = FakeTradingClient()
    fake.script_get_account = lambda: _account("SUSPENDED")
    broker = make_broker(tmp_path, client=fake)

    with pytest.raises(AccountNotActiveError, match="SUSPENDED"):
        broker.account()

    assert issubclass(AccountNotActiveError, RuntimeError)
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "paper_incident"
    assert alerts[0]["category"] == "account_status_not_active"
    assert alerts[0]["incident_id"] == "account_status_not_active:SUSPENDED"


# --- R2-4-3: start timeouts cover the integrity check ------------------------


def _unit_values(path: Path, key: str) -> list[str]:
    return [
        line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip().startswith(key + "=")
    ]


def test_engine_unit_start_timeout_covers_the_doctor_preflight() -> None:
    unit = REPO / "deploy" / "systemd" / "parayabuck.service"
    assert _unit_values(unit, "TimeoutStartSec") == ["TimeoutStartSec=900"]
    assert _unit_values(unit, "TimeoutStopSec") == ["TimeoutStopSec=180"]


def test_backup_unit_start_timeout_covers_the_offhost_copy() -> None:
    unit = REPO / "deploy" / "systemd" / "parayabuck-backup.service"
    assert _unit_values(unit, "TimeoutStartSec") == ["TimeoutStartSec=3600"]


def test_vm_start_timeout_drop_ins_match_the_templates() -> None:
    engine = REPO / "deploy" / "systemd" / "vm-dropin" / "start-timeout-engine.conf"
    backup = REPO / "deploy" / "systemd" / "vm-dropin" / "start-timeout-backup.conf"
    assert _unit_values(engine, "TimeoutStartSec") == ["TimeoutStartSec=900"]
    assert _unit_values(backup, "TimeoutStartSec") == ["TimeoutStartSec=3600"]
    assert "[Service]" in engine.read_text(encoding="utf-8")
    assert "[Service]" in backup.read_text(encoding="utf-8")
