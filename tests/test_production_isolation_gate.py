"""The CI isolation gate and the PAPER incident reconciler must not touch the repo.

``scripts/check_production_isolation.py`` is the CI gate: it fingerprints the
operator's production state before pytest and compares after. It used to list
nine individual files, so a test that wrote ``data/runtime/paper_equity_peak.json``
- the PAPER drawdown sidecar - or a receipt under ``data/runtime/paper_receipts/``
passed CI. The gate now walks whole directories, so a file *created* during the
run fails just like a mutated one.

``scripts/reconcile_paper_incident.py`` defaults its anonymised replay fixture
to a path under ``tests/``: every operator run left broker-linked PAPER history
in an untracked, non-ignored file that a later ``git add -A`` would commit. The
default now lands under the git-ignored ``reports/`` tree.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts/check_production_isolation.py"
RECONCILE = ROOT / "scripts/reconcile_paper_incident.py"

# Representative production state: the files CI used to name explicitly, plus the
# sidecars, receipts, diagnostic bundles, backups and logs it did not.
_SEED_TREE = {
    "data/runtime/parayabuck.sqlite3": "decision-db",
    "data/runtime/parayabuck.sqlite3-wal": "decision-wal",
    "data/runtime/observability.sqlite3": "observability",
    "data/runtime/health_snapshot.json": '{"status": "ok"}',
    "data/runtime/paper_equity_peak.json": '{"peak_equity": 1000.0, "account_id": "paper-abc"}',
    "data/runtime/live_equity_peak.json": '{"peak_equity": 0.0}',
    "data/runtime/paper_receipts/2026-10-01/order.json": '{"receipt": 1}',
    "data/runtime/diagnostics/parayabuck_bundle_20261001T000000Z.zip": "PK\x03\x04bundle",
    "data/cache/warmup.sqlite3": "warmup",
    "data/backups/parayabuck-2026-10-01.sqlite3": "backup",
    "logs/parayabuck.log": "started\n",
}


def _seed(root: Path) -> None:
    for relative, payload in _SEED_TREE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")


def _gate(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _record(cwd: Path) -> Path:
    before = cwd / "before.json"
    assert _gate(cwd, "--record", str(before)).returncode == 0
    return before


def test_unchanged_tree_passes(tmp_path: Path) -> None:
    """The gate a green CI run exercises: record, run pytest, verify."""
    _seed(tmp_path)
    before = _record(tmp_path)

    # A test that only reads operator state must leave the gate green.
    (tmp_path / "data/runtime/paper_equity_peak.json").read_text(encoding="utf-8")

    result = _gate(tmp_path, "--verify", str(before))
    assert result.returncode == 0, result.stdout + result.stderr


def test_mutated_protected_file_fails(tmp_path: Path) -> None:
    """A test that rewrites the PAPER peak sidecar must break the build."""
    _seed(tmp_path)
    before = _record(tmp_path)

    (tmp_path / "data/runtime/paper_equity_peak.json").write_text('{"peak_equity": 1.0}', encoding="utf-8")
    result = _gate(tmp_path, "--verify", str(before))

    assert result.returncode == 1
    assert "paper_equity_peak.json" in result.stdout


def test_created_protected_file_fails(tmp_path: Path) -> None:
    """Creating a receipt during the run is as forbidden as mutating one."""
    _seed(tmp_path)
    before = _record(tmp_path)

    new_receipt = tmp_path / "data/runtime/paper_receipts/2026-10-02/order.json"
    new_receipt.parent.mkdir(parents=True, exist_ok=True)
    new_receipt.write_text('{"receipt": 2}', encoding="utf-8")
    result = _gate(tmp_path, "--verify", str(before))

    assert result.returncode == 1
    assert "2026-10-02/order.json" in result.stdout


def test_deleted_protected_file_fails(tmp_path: Path) -> None:
    """A test that truncates state by unlinking a file must break the build."""
    _seed(tmp_path)
    before = _record(tmp_path)

    (tmp_path / "data/backups/parayabuck-2026-10-01.sqlite3").unlink()
    result = _gate(tmp_path, "--verify", str(before))

    assert result.returncode == 1
    assert "parayabuck-2026-10-01.sqlite3" in result.stdout


def test_created_file_outside_protected_dirs_passes(tmp_path: Path) -> None:
    """Research artefacts and scratch output are not operator state."""
    _seed(tmp_path)
    before = _record(tmp_path)

    research = tmp_path / "data/research_runs/2026-10-01/summary.json"
    research.parent.mkdir(parents=True, exist_ok=True)
    research.write_text("{}", encoding="utf-8")
    (tmp_path / "scratch.json").write_text("{}", encoding="utf-8")
    result = _gate(tmp_path, "--verify", str(before))

    assert result.returncode == 0, result.stdout + result.stderr


def test_gate_requires_a_mode(tmp_path: Path) -> None:
    """No --record/--verify is a usage error (exit 2), not a silent pass."""
    assert _gate(tmp_path).returncode == 2


# --- G3.9: the reconciler must not write broker data under tests/ -----------


def _load_reconcile() -> ModuleType:
    spec = importlib.util.spec_from_file_location("reconcile_paper_incident_under_test", RECONCILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _empty_database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE orders (broker_order_id TEXT, client_order_id TEXT)")
    connection.execute("CREATE TABLE managed_positions (canonical_symbol TEXT, status TEXT)")
    connection.commit()
    connection.close()
    return path


def _stub_snapshot() -> object:
    """A fixed, offline PAPER snapshot standing in for the Alpaca download."""
    from parayabuck.monitoring.paper_incident import (
        BrokerFillRecord,
        BrokerOrderRecord,
        PaperIncidentSnapshot,
    )

    now = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)
    return PaperIncidentSnapshot(
        orders=(
            BrokerOrderRecord(
                order_id="order-1",
                client_order_id="client-1",
                symbol="SPY",
                side="buy",
                quantity=10.0,
                filled_quantity=10.0,
                status="filled",
                created_at=now,
            ),
        ),
        fills=(
            BrokerFillRecord(
                activity_id="activity-1",
                order_id="order-1",
                symbol="SPY",
                side="buy",
                quantity=10.0,
                price=100.0,
                fee=0.0,
                occurred_at=now,
            ),
        ),
        positions=(),
        cash_activities=(),
        starting_equity=100_000.0,
        ending_equity=101_000.0,
    )


@pytest.fixture
def reconcile_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The script with credentials stubbed and the network seam replaced."""
    module = _load_reconcile()
    snapshot = _stub_snapshot()
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setattr(module, "_snapshot", lambda *args, **kwargs: snapshot)
    return module


def test_reconcile_writes_fixture_under_reports_not_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reconcile_module: ModuleType
) -> None:
    """Default run: the replay fixture lands in the ignored reports/ tree."""
    repo_tests_before = set((ROOT / "tests").rglob("*"))
    monkeypatch.chdir(tmp_path)
    database = _empty_database(tmp_path / "parayabuck.sqlite3")

    assert reconcile_module.main(["--database", str(database), "--quiet"]) == 0

    assert (tmp_path / "reports/paper_incident/replay.json").is_file()
    assert (tmp_path / "reports/paper_incident/report.json").is_file()
    # Nothing landed in a tests/ tree: not the cwd's, not the operator's checkout.
    assert not (tmp_path / "tests").exists()
    assert set((ROOT / "tests").rglob("*")) == repo_tests_before


def test_reports_tree_stays_git_ignored() -> None:
    """Otherwise a later `git add -A` would commit the PAPER account's fills."""
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "reports/paper_incident/replay.json"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0


def test_reconcile_never_reaches_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reconcile_module: ModuleType
) -> None:
    """The stubbed snapshot seam is the only source of broker state."""
    monkeypatch.chdir(tmp_path)
    database = _empty_database(tmp_path / "parayabuck.sqlite3")

    def _explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("the reconciler must not call Alpaca here")

    for name in ("_get", "_all_orders", "_all_fills", "_all_cash_activities"):
        monkeypatch.setattr(reconcile_module, name, _explode)

    assert reconcile_module.main(["--database", str(database), "--quiet"]) == 0

    payload = (tmp_path / "reports/paper_incident/replay.json").read_text(encoding="utf-8")
    assert "order-1" not in payload, "the fixture must be anonymised"


def test_reconcile_honours_explicit_fixture_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reconcile_module: ModuleType
) -> None:
    """An operator replaying a captured incident points --fixture at their own copy."""
    monkeypatch.chdir(tmp_path)
    database = _empty_database(tmp_path / "parayabuck.sqlite3")
    explicit = tmp_path / "captured/replay.json"

    assert reconcile_module.main(["--database", str(database), "--quiet", "--fixture", str(explicit)]) == 0

    assert explicit.is_file()
    assert not (tmp_path / "reports/paper_incident/replay.json").exists()


def test_reconcile_loads_only_the_configured_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The script must name its dotenv explicitly: a bare upward search from a
    worktree checkout reached the main checkout's real ``.env``."""
    module = _load_reconcile()
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(module, "load_dotenv", lambda *args, **kwargs: calls.append(args) or False)
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "configured.env"))
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        module.main(["--database", str(_empty_database(tmp_path / "parayabuck.sqlite3")), "--quiet"])

    assert calls == [(tmp_path / "configured.env",)]


def test_dotenv_guard_never_loads_a_parent_directory_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``.env`` above the working directory or above the checkout is never
    loaded during tests, whether by bare upward search or by explicit path."""
    import os

    import dotenv

    from tests.conftest import _REPO_ROOT, _dotenv_allowed

    outer = tmp_path / "outer"
    nested = outer / "checkout" / "tests"
    nested.mkdir(parents=True)
    (outer / ".env").write_text("PARAYABUCK_DOTENV_LEAK=1\n", encoding="utf-8")
    monkeypatch.delenv("PARAYABUCK_DOTENV_LEAK", raising=False)
    monkeypatch.chdir(nested)

    assert dotenv.load_dotenv() is False
    assert dotenv.dotenv_values() == {}
    assert "PARAYABUCK_DOTENV_LEAK" not in os.environ
    for refused in (_REPO_ROOT / ".env", *(parent / ".env" for parent in _REPO_ROOT.parents), Path.home() / ".env"):
        assert not _dotenv_allowed(refused), refused
    assert _dotenv_allowed(tmp_path / "explicit.env")


def test_reconcile_requires_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without credentials the script exits before writing anything."""
    module = _load_reconcile()
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    database = _empty_database(tmp_path / "parayabuck.sqlite3")

    with pytest.raises(SystemExit):
        module.main(["--database", str(database), "--quiet"])

    assert not (tmp_path / "reports").exists()
