"""Regressions for the 2026-10-23 external audit (F-01 .. F-05)."""

from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.portfolio.runtime_service import EXIT_SELL_RETRY_SUFFIX
from parayabuck.web.services import status as status_service
from tests.test_live_readiness_r1_r3 import _rejected_exit
from tests.test_paper_soak_auditor import _audit, _fixture

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "scripts" / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# F-01: a foreign crypto order on the shared account must not break the retry.


def test_rejected_exit_sell_retry_ignores_foreign_crypto_open_orders(tmp_path: Path) -> None:
    broker, runtime, _repository, _decision_id = _rejected_exit(tmp_path)
    broker.reject_sells = False
    broker.open_symbols.add("BTC/USD")
    submitted = runtime.retry_rejected_exit_sells(broker.account())
    assert len(submitted) == 1
    assert broker.submitted[-1].client_order_id.endswith(EXIT_SELL_RETRY_SUFFIX)


# F-02: the documented -retry1 of a rejected exit SELL is not a duplicate submission.


def _add_sell_pair(db: Path, *, original_status: str, retry_side: str = "sell") -> None:
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO portfolio_orders VALUES "
            "('order-sell','eq-scheduled-3','equity','QLD','sell',10,0,?,'2026-09-03T20:00:00+00:00')",
            (original_status,),
        )
        con.execute(
            "INSERT INTO portfolio_orders VALUES "
            "(?,'eq-scheduled-3','equity','QLD',?,10,10,'filled','2026-09-03T20:01:00+00:00')",
            (f"order-sell{EXIT_SELL_RETRY_SUFFIX}", retry_side),
        )


def test_soak_audit_accepts_the_retry_of_a_rejected_exit_sell(tmp_path: Path) -> None:
    db, receipts, journal = _fixture(tmp_path)
    _add_sell_pair(db, original_status="rejected")
    report = _audit(db, receipts, journal)
    assert report["gates"]["S4"]["status"] == "PASS", report  # type: ignore[index]


def test_soak_audit_flags_a_retry_whose_original_was_not_rejected(tmp_path: Path) -> None:
    db, receipts, journal = _fixture(tmp_path)
    _add_sell_pair(db, original_status="filled")
    report = _audit(db, receipts, journal)
    assert report["gates"]["S4"]["status"] == "FAIL", report  # type: ignore[index]


# F-03: repair_local_state reads the mode's own credential names.


def _live_config() -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(mode=SimpleNamespace(value="live")),
        universe=SimpleNamespace(equity=SimpleNamespace(enabled=True, symbols=["QLD"])),
    )


def test_repair_reconcile_never_uses_paper_keys_in_live_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repair = _load_script("repair_local_state_live_keys", "repair_local_state.py")
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
    assert repair._run_broker_reconcile(config=_live_config()) is False
    assert "No Alpaca keys" in capsys.readouterr().out


def test_repair_reconcile_uses_live_keys_in_live_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import parayabuck.execution.alpaca as alpaca_module

    repair = _load_script("repair_local_state_live_keys2", "repair_local_state.py")
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("ALPACA_LIVE_API_KEY", "live-key")
    monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "live-secret")
    seen: dict[str, object] = {}

    class _Broker:
        def __init__(self, api_key: str, secret: str, *, paper: bool, **_kwargs: object) -> None:
            seen.update(api_key=api_key, secret=secret, paper=paper)
            raise RuntimeError("stop before any network call")

    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _Broker)
    assert repair._run_broker_reconcile(config=_live_config(), ledger=tmp_path / "ledger.sqlite3") is False
    assert seen == {"api_key": "live-key", "secret": "live-secret", "paper": False}


# F-04: the settings drawer sends the digest and surfaces a failed restart.


def test_settings_drawer_sends_digest_and_reports_restart_error() -> None:
    source = (_REPO_ROOT / "src/parayabuck/web/static/js/ui.js").read_text(encoding="utf-8")
    assert "configDigest = data.config_digest" in source
    assert re.search(r"expected_config_digest:\s*configDigest", source)
    assert "data.restart_error" in source


# F-05: /api/status needs both key and secret, like /api/settings.


class _Manager:
    config_path = None
    runtime = None
    last_engine_error = None
    security_warning = None

    def __init__(self, credentials: tuple[str | None, str | None]) -> None:
        self._credentials = credentials

    def get_credentials(self) -> tuple[str | None, str | None]:
        return self._credentials

    def current_engine_state(self) -> str:
        return "stopped"

    def is_paper_mode(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("credentials", "expected"),
    [(("key", None), False), (("key", ""), False), (("", "secret"), False), (("key", "secret"), True)],
)
def test_status_has_credentials_requires_key_and_secret(
    monkeypatch: pytest.MonkeyPatch, credentials: tuple[str | None, str | None], expected: bool
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> object:
        raise ValueError("no config")

    monkeypatch.setattr(status_service, "_load_config", _raise)
    status = status_service.build_status_snapshot(_Manager(credentials))
    assert status["has_credentials"] is expected
