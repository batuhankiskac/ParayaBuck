"""Tests must never read or write the operator's production state.

The PAPER peak sidecar carries the live account id, so a test that resolved it
from the relative ``data/runtime`` default failed closed against its own fake
account — the suite passed on a fresh checkout and failed on the trading host.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.risk_peak import RiskPeakService, resolve_peak_state_path
from tests.fake_client import FakeTradingClient

from .conftest import protected_state_path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_the_operator_runtime_files_are_recognised_as_production_state() -> None:
    for name in (
        "data/runtime/paper_equity_peak.json",
        "data/runtime/live_equity_peak.json",
        "data/runtime/parayabuck.sqlite3",
        "data/runtime/observability.sqlite3",
        "data/runtime/health_snapshot.json",
        "data/cache/warmup.sqlite3",
    ):
        assert protected_state_path(REPO_ROOT / name) is not None, name


def test_tmp_path_state_is_not_production_state(tmp_path: Path) -> None:
    assert protected_state_path(tmp_path / "data" / "runtime" / "paper_equity_peak.json") is None


def test_the_default_peak_sidecar_never_resolves_into_the_repo_runtime_dir() -> None:
    """No explicit state dir must not silently mean the operator's file."""
    assert resolve_peak_state_path(True, None, None) == Path("data/runtime/paper_equity_peak.json")
    broker = AlpacaBroker("k", "s", paper=True, client=FakeTradingClient())

    assert protected_state_path(broker._peak_state_path) is None


def test_a_foreign_account_peak_file_never_reaches_a_broker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reported failure, hermetic: a peak file holding another account's id
    sits at the relative default, and a fake-account broker still reconciles."""
    operator_state = tmp_path / "operator" / "data" / "runtime"
    operator_state.mkdir(parents=True)
    (operator_state / "paper_equity_peak.json").write_text(
        json.dumps({"account_id": "4edcc1ce-9795-4e12-b933-cadaae01fc3f", "peak_equity": 100107.0, "epoch": 0}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path / "operator")

    broker = AlpacaBroker("k", "s", paper=True, client=FakeTradingClient())

    # Resolved, not compared as written: the leak is the same file reached by a
    # different string, which a raw Path == would wave through.
    operator_file = (operator_state / "paper_equity_peak.json").resolve()
    assert broker._risk_peak.peak_state_path.resolve() != operator_file
    assert RiskPeakService(paper=True).peak_state_path.resolve() != operator_file


def test_the_isolated_test_config_never_names_production_state(isolated_config_path: Path) -> None:
    """The config the ops tests resolve their state paths through.

    A config that pointed back into the repo's ``data/runtime`` would put the
    operator's snapshot, journal and decision ledger back in reach of every
    ops test — silently on a fresh checkout, loudly on the trading VM.
    """
    from dataclasses import asdict

    from parayabuck.config import load_config

    def _paths(value: object) -> list[str]:
        if isinstance(value, str):
            return [value] if "/" in value else []
        if isinstance(value, dict):
            return [path for item in value.values() for path in _paths(item)]
        if isinstance(value, (list, tuple)):
            return [path for item in value for path in _paths(item)]
        return []

    paths = _paths(asdict(load_config(str(isolated_config_path))))
    assert paths, "the shipped config no longer carries any path"
    for path in paths:
        assert protected_state_path(path) is None, path
