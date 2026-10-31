"""Tests must never read or write the operator's production state.

The PAPER peak sidecar carries the live account id, so a test that resolved it
from the relative ``data/runtime`` default failed closed against its own fake
account — the suite passed on a fresh checkout and failed on the trading host.
"""

from __future__ import annotations

import json
import os
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


def test_the_ops_health_read_never_targets_the_operator_snapshot() -> None:
    """A config without ``observability.snapshot_path`` defaults to the relative
    ``data/runtime/health_snapshot.json``. The ops status read model must not
    resolve to that file, or every such test reads the operator's snapshot on a
    checkout where it exists and silently skips it on a fresh clone."""
    from parayabuck.config import Config
    from parayabuck.web.services import ops as ops_svc

    config = Config()
    assert protected_state_path(config.observability.snapshot_path) is not None

    payload = ops_svc.get_ops_status(config)

    assert "error" not in payload, payload
    assert protected_state_path(payload["snapshot_path"]) is None, payload["snapshot_path"]


# --- Writes that do not go through open(): refused before they happen --------
#
# Each probe names a production path and must raise the guard's error. The
# refusal is the expected outcome, so the recorded access is consumed afterwards.


def test_deletes_renames_and_directory_creation_under_production_state_are_refused() -> None:
    from .conftest import _STATE_ACCESSES, ProductionStateAccessError

    runtime_dir = REPO_ROOT / "data" / "runtime"
    with pytest.raises(ProductionStateAccessError):
        (runtime_dir / "guard-probe.json").unlink(missing_ok=True)
    with pytest.raises(ProductionStateAccessError):
        os.rename(str(runtime_dir / "guard-probe.json"), str(runtime_dir / "guard-probe.moved"))
    with pytest.raises(ProductionStateAccessError):
        (runtime_dir / "guard-probe-dir").mkdir()
    _STATE_ACCESSES.clear()


def test_a_symlink_into_production_state_is_refused(tmp_path: Path) -> None:
    """The name carries no marker and the target sits under the state dir: resolution decides."""
    from .conftest import _STATE_ACCESSES, ProductionStateAccessError

    link = tmp_path / "harmless-name.json"
    link.symlink_to(REPO_ROOT / "data" / "runtime" / "parayabuck.sqlite3")
    with pytest.raises(ProductionStateAccessError):
        open(link, "rb")
    _STATE_ACCESSES.clear()


def test_name_resolution_and_remote_udp_sends_are_refused_before_any_packet() -> None:
    """The network guard covers DNS and connectionless sends, not only connect()."""
    import socket

    from .conftest import OutboundNetworkBlocked

    with pytest.raises(OutboundNetworkBlocked):
        socket.getaddrinfo("example.invalid", 443)
    with pytest.raises(OutboundNetworkBlocked):
        socket.gethostbyname("example.invalid")
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(OutboundNetworkBlocked):
            udp.sendto(b"probe", ("8.8.8.8", 53))
    finally:
        udp.close()


def test_the_reports_tree_is_write_protected_but_readable() -> None:
    from .conftest import _STATE_ACCESSES, ProductionStateAccessError, _protected_write_path

    probe = REPO_ROOT / "reports" / "guard-probe.txt"
    assert _protected_write_path(probe) == probe.resolve()
    with pytest.raises(ProductionStateAccessError):
        open(probe, "w")
    _STATE_ACCESSES.clear()
