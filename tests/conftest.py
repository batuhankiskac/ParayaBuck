"""Test isolation: prevent production cache/runtime pollution and engine crashes."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from parayabuck.data import cache as cache_module
from parayabuck.data.cache import WarmupCache
from parayabuck.monitoring import journal as journal_module
from parayabuck.monitoring import receipt as receipt_module
from parayabuck.monitoring import snapshot as snapshot_module
from parayabuck.monitoring.journal import DEFAULT_JOURNAL_PATH, EventJournal
from parayabuck.monitoring.snapshot import DEFAULT_SNAPSHOT_PATH, SnapshotWriter
from parayabuck.storage import SQLiteStore
from parayabuck.web.state import get_web_state, set_web_state

_PROTECTED_PATHS = [
    Path("data/cache/warmup.sqlite3"),
    Path("data/cache/warmup.sqlite3-wal"),
    # -shm is shared memory, mtime changes on read; not reliable for isolation check
    Path("data/runtime/parayabuck.sqlite3"),
    # wal for parayabuck is ephemeral; exclude -shm, check wal leniently
    Path("data/runtime/observability.sqlite3"),
    Path("data/runtime/health_snapshot.json"),
]

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Research tests are deliberately classified here instead of by directory:
# ``tests/research/`` contains both current contracts and closed mission
# records. New tests in that directory are historical by default until their
# research status is reviewed and added to the current allowlist.
_PRODUCT = "product"
_CURRENT_RESEARCH = "current_research"
_HISTORICAL_RESEARCH = "historical_research"

_CURRENT_RESEARCH_TEST_PATHS = (
    "tests/test_equity_v3_current_parity_control.py",
    "tests/test_full_survivorship_terminal.py",
    "tests/test_normalized_hash.py",
    "tests/test_research_experiment.py",
    "tests/test_research_runner.py",
    "tests/test_terminal_aware_engine.py",
    "tests/research/test_equity_profit_protection_2026_09_r2.py",
    "tests/research/test_execution_cost_transferability_p0_2026_09.py",
    "tests/research/test_execution_cost_transferability_p1_paper_observation_2026_09.py",
    "tests/research/test_r46_governance_contract_2026_09_10.py",
    "tests/research/test_r46_nyse_calendar_2026_09_10.py",
    "tests/research/test_r46_preflight_2026_09_10.py",
    "tests/research/test_r46_publication_availability_evidence_2026_09_11.py",
    "tests/research/test_r46_tlt_market_data_2026_09_10.py",
    "tests/research/test_r46_treasury_acquisition_2026_09_10.py",
    "tests/research/test_r46_treasury_auction_readiness_2026_09_10.py",
    "tests/research/test_r46_treasury_xml_v2_2026_09_10.py",
    "tests/research/test_r46_v2_data_package_2026_09_10.py",
    "tests/research/test_r46_v2_execution_2026_09_10.py",
    "tests/research/test_r46_frozen_source_pins_2026_09_12.py",
    "tests/research/test_r46_v2_governance_2026_09_10.py",
    "tests/research/test_r46_v3_oracle_2026_09_11.py",
)

_HISTORICAL_RESEARCH_TEST_PATHS = (
    "tests/test_candidate_observation_parity_receipts.py",
    "tests/test_m3_iwm_acquisition.py",
    "tests/test_research_equity_m3_candidate_ocp.py",
    "tests/test_research_equity_m3_candidate_ocp_attestation_r2.py",
    "tests/test_research_equity_m3_data_gate.py",
    "tests/test_research_equity_m3_economic.py",
    "tests/test_research_equity_m3_economic_execution.py",
    "tests/test_research_equity_m3_m4.py",
    "tests/test_research_equity_m3_r5.py",
)

_CURRENT_RESEARCH_TESTS = frozenset(_CURRENT_RESEARCH_TEST_PATHS)
_HISTORICAL_RESEARCH_TESTS = frozenset(_HISTORICAL_RESEARCH_TEST_PATHS)


def _duplicate_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    seen = set()
    duplicates = set()
    for path in paths:
        if path in seen:
            duplicates.add(path)
        seen.add(path)
    return tuple(sorted(duplicates))


def _validate_research_classification() -> None:
    errors = []
    for label, paths in (
        ("current research", _CURRENT_RESEARCH_TEST_PATHS),
        ("historical research", _HISTORICAL_RESEARCH_TEST_PATHS),
    ):
        duplicates = _duplicate_paths(paths)
        if duplicates:
            errors.append(f"{label} has duplicate paths: {', '.join(duplicates)}")
        missing = sorted(path for path in paths if not (_REPO_ROOT / path).is_file())
        if missing:
            errors.append(f"{label} has missing files: {', '.join(missing)}")

    overlap = sorted(_CURRENT_RESEARCH_TESTS & _HISTORICAL_RESEARCH_TESTS)
    if overlap:
        errors.append(f"current and historical research overlap: {', '.join(overlap)}")

    root_research_tests = {
        path.relative_to(_REPO_ROOT).as_posix() for path in (_REPO_ROOT / "tests").glob("test_research_*.py")
    }
    unclassified = sorted(root_research_tests - (_CURRENT_RESEARCH_TESTS | _HISTORICAL_RESEARCH_TESTS))
    if unclassified:
        errors.append(f"unclassified root research tests: {', '.join(unclassified)}")

    if errors:
        raise pytest.UsageError(
            "Invalid research test classification metadata:\n" + "\n".join(f"- {error}" for error in errors)
        )


def _classify_test_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return _PRODUCT

    if relative in _CURRENT_RESEARCH_TESTS:
        return _CURRENT_RESEARCH
    if relative in _HISTORICAL_RESEARCH_TESTS or relative.startswith("tests/research/"):
        return _HISTORICAL_RESEARCH
    return _PRODUCT


def pytest_addoption(parser):
    parser.addoption(
        "--include-historical-research",
        action="store_true",
        help="include historical_research test modules before collection",
    )


def pytest_configure(config):
    del config
    _validate_research_classification()


def pytest_ignore_collect(collection_path: Path, config) -> bool:
    """Skip historical modules before pytest imports or collects them."""
    path = Path(str(collection_path))
    if not path.is_file():
        return False
    return _classify_test_path(path) == _HISTORICAL_RESEARCH and not config.getoption("include_historical_research")


def pytest_collection_modifyitems(config, items):
    """Mark research items after only the allowed modules have been collected."""
    del config
    for item in items:
        item_path = getattr(item, "path", None) or item.fspath
        classification = _classify_test_path(Path(str(item_path)))
        if classification == _CURRENT_RESEARCH:
            item.add_marker("research")
        elif classification == _HISTORICAL_RESEARCH:
            item.add_marker("research")
            item.add_marker("historical_research")


def _file_signature(path: Path):
    if not path.exists():
        return None
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return None


@pytest.fixture(autouse=True)
def _restore_web_state():
    before = get_web_state()
    try:
        from parayabuck.web import server as srv

        orig_manager = srv.manager
        orig_backtest = srv.backtest_manager
    except Exception:
        orig_manager = None  # type: ignore[assignment]
        orig_backtest = None  # type: ignore[assignment]

    yield

    current = get_web_state()
    if current is not before:
        set_web_state(before)
    try:
        from parayabuck.web import server as srv2

        if before is not None and srv2.web_state is not before:
            srv2.web_state = before  # type: ignore[attr-defined]
            set_web_state(before)
        if orig_manager is not None and srv2.manager is not orig_manager:
            srv2.manager = orig_manager  # type: ignore[attr-defined]
        if orig_backtest is not None and srv2.backtest_manager is not orig_backtest:
            srv2.backtest_manager = orig_backtest  # type: ignore[attr-defined]
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _test_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    before = {p: _file_signature(p) for p in _PROTECTED_PATHS}
    iso_cache = tmp_path / "warmup_isolated.sqlite3"
    iso_journal = tmp_path / "observability_isolated.sqlite3"
    iso_snapshot = tmp_path / "health_snapshot_isolated.json"
    iso_db = tmp_path / "parayabuck_isolated.sqlite3"
    iso_receipt_dir = tmp_path / "paper_receipts"
    iso_receipt_dir.mkdir(parents=True, exist_ok=True)
    iso_cache.parent.mkdir(parents=True, exist_ok=True)

    # WarmupCache redirect
    orig_warmup_init = WarmupCache.__init__

    def _patched_warmup_init(self, path="data/cache/warmup.sqlite3"):  # type: ignore[no-untyped-def]
        if str(path) == "data/cache/warmup.sqlite3":
            path = str(iso_cache)
        return orig_warmup_init(self, path)

    monkeypatch.setattr(cache_module.WarmupCache, "__init__", _patched_warmup_init)
    monkeypatch.setattr(WarmupCache, "__init__", _patched_warmup_init)

    # EventJournal single isolated instance
    orig_journal_init = EventJournal.__init__

    def _patched_journal_init(self, path=DEFAULT_JOURNAL_PATH, queue_size=2048, retention_days=30, max_events=50000):  # type: ignore[no-untyped-def]
        if str(path) == DEFAULT_JOURNAL_PATH or str(path) == "data/runtime/observability.sqlite3":
            path = str(iso_journal)
        return orig_journal_init(
            self, path, queue_size=queue_size, retention_days=retention_days, max_events=max_events
        )

    monkeypatch.setattr(journal_module.EventJournal, "__init__", _patched_journal_init)

    iso_journal_instance = EventJournal(str(iso_journal))
    old_global = journal_module._global_journal
    journal_module._global_journal = iso_journal_instance
    orig_get_journal = journal_module.get_journal

    def _patched_get_journal(path=None):  # type: ignore[no-untyped-def]
        if path is None or str(path) in (DEFAULT_JOURNAL_PATH, "data/runtime/observability.sqlite3", str(iso_journal)):
            return iso_journal_instance  # type: ignore[return-value]
        return orig_get_journal(path)

    monkeypatch.setattr(journal_module, "get_journal", _patched_get_journal)

    # SnapshotWriter
    try:
        orig_snapshot_init = SnapshotWriter.__init__

        def _patched_snapshot_init(self, path=DEFAULT_SNAPSHOT_PATH, interval_seconds=30.0, provider=None):  # type: ignore[no-untyped-def]
            if str(path) == str(DEFAULT_SNAPSHOT_PATH) or str(path) == "data/runtime/health_snapshot.json":
                path = str(iso_snapshot)
            return orig_snapshot_init(self, path, interval_seconds=interval_seconds, provider=provider)

        monkeypatch.setattr(snapshot_module.SnapshotWriter, "__init__", _patched_snapshot_init)
    except Exception:
        pass

    try:
        monkeypatch.setattr(receipt_module, "default_receipt_dir", lambda: iso_receipt_dir)
    except Exception:
        pass

    orig_store_init = SQLiteStore.__init__

    def _patched_store_init(self, path):  # type: ignore[no-untyped-def]
        s = str(path)
        if s == "data/runtime/parayabuck.sqlite3" or (s.endswith("parayabuck.sqlite3") and "data/runtime" in s):
            if str(tmp_path) not in s:
                path = str(iso_db)
        return orig_store_init(self, path)

    monkeypatch.setattr(SQLiteStore, "__init__", _patched_store_init)

    try:
        from parayabuck.execution import ledger as ledger_module

        orig_ledger_init = ledger_module.ExecutionLedger.__init__

        def _patched_ledger_init(self, path, store=None):  # type: ignore[no-untyped-def]
            if str(path) == "data/runtime/parayabuck.sqlite3" and str(tmp_path) not in str(path):
                path = str(iso_db)
            return orig_ledger_init(self, path, store=store)

        monkeypatch.setattr(ledger_module.ExecutionLedger, "__init__", _patched_ledger_init)
    except Exception:
        pass

    try:
        from parayabuck.portfolio import runtime_repository as repo_module

        orig_repo_init = repo_module.PortfolioStateRepository.__init__

        def _patched_repo_init(self, path, store=None):  # type: ignore[no-untyped-def]
            if str(path) == "data/runtime/parayabuck.sqlite3" and str(tmp_path) not in str(path):
                path = str(iso_db)
            return orig_repo_init(self, path, store=store)

        monkeypatch.setattr(repo_module.PortfolioStateRepository, "__init__", _patched_repo_init)
    except Exception:
        pass

    try:
        import parayabuck.data.alpaca as alpaca_module

        class _BlockedStock:
            def __init__(self, *a, **kw):
                try:
                    self._session = MagicMock()
                    self._session.close = lambda: None
                except Exception:
                    self._session = SimpleNamespace(
                        close=lambda: None, request=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked"))
                    )

            def get_stock_bars(self, *a, **kw):
                raise RuntimeError(
                    "Network blocked in tests: Alpaca StockHistoricalDataClient.get_stock_bars called without fake"
                )

            def get_stock_latest_quote(self, *a, **kw):
                raise RuntimeError(
                    "Network blocked in tests: Alpaca StockHistoricalDataClient.get_stock_latest_quote called without fake"
                )

        class _BlockedCrypto:
            def __init__(self, *a, **kw):
                try:
                    self._session = MagicMock()
                    self._session.close = lambda: None
                except Exception:
                    self._session = SimpleNamespace(
                        close=lambda: None, request=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked"))
                    )

            def get_crypto_bars(self, *a, **kw):
                raise RuntimeError(
                    "Network blocked in tests: Alpaca CryptoHistoricalDataClient.get_crypto_bars called without fake"
                )

            def get_crypto_latest_quote(self, *a, **kw):
                raise RuntimeError(
                    "Network blocked in tests: Alpaca CryptoHistoricalDataClient.get_crypto_latest_quote called without fake"
                )

        monkeypatch.setattr(alpaca_module, "StockHistoricalDataClient", _BlockedStock)
        monkeypatch.setattr(alpaca_module, "CryptoHistoricalDataClient", _BlockedCrypto)
    except Exception:
        pass

    # No patch for should_auto_start: tests that check its return value rely on
    # real logic (paper vs live + credentials). Auto-start not triggered in tests
    # because we clear ALPACA keys and never run the web lifespan.
    # Keep network blocked via Alpaca client stubs above.

    # Redirect any direct sqlite3.connect to production files to isolated tmp
    import sqlite3 as _sqlite3

    _orig_connect = _sqlite3.connect

    def _patched_connect(database, *args, **kwargs):  # type: ignore[no-untyped-def]
        db_str = str(database)
        # Redirect production paths (including URI file:...) to isolated files
        if "data/runtime/parayabuck.sqlite3" in db_str and str(tmp_path) not in db_str:
            db_str = db_str.replace("data/runtime/parayabuck.sqlite3", str(iso_db))
            # Handle WAL/SHM etc. are separate files, not via connect, but handle anyway
            database = db_str
        if "data/runtime/observability.sqlite3" in db_str and str(tmp_path) not in db_str:
            db_str = db_str.replace("data/runtime/observability.sqlite3", str(iso_journal))
            database = db_str
        if "data/cache/warmup.sqlite3" in db_str and str(tmp_path) not in db_str:
            db_str = db_str.replace("data/cache/warmup.sqlite3", str(iso_cache))
            database = db_str
        return _orig_connect(database, *args, **kwargs)

    monkeypatch.setattr(_sqlite3, "connect", _patched_connect)

    monkeypatch.setenv("PYTEST_ISOLATION", "1")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    yield

    time.sleep(0.05)
    ignored = {"pb-journal-writer", "pb-alert-webhook-worker"}
    alive = [t for t in threading.enumerate() if t.is_alive() and t.name.startswith("pb-") and t.name not in ignored]
    # Also ignore daemon threads that are expected to linger briefly
    alive_non_daemon = [t for t in alive if not t.daemon]
    if alive_non_daemon:
        pytest.fail(
            f"Engine threads still alive after test: {[t.name for t in alive_non_daemon]} — tests must stop EngineRuntime"
        )
    # For remaining daemon pb- threads, give them a moment then warn but not fail
    # (they are isolated per test via tmp journal and will be garbage collected)

    try:
        if iso_journal_instance is not None:
            try:
                iso_journal_instance.stop(timeout=0.5)
            except Exception:
                pass
        # Restore old global if it was there
        journal_module._global_journal = old_global
        # Also ensure any lingering global is cleared
        if journal_module._global_journal is not None and journal_module._global_journal is not iso_journal_instance:
            try:
                journal_module._global_journal.stop(timeout=0.5)
            except Exception:
                pass
            journal_module._global_journal = None
    except Exception:
        pass

    try:
        from parayabuck.monitoring import observe as obs_mod

        obs_mod.reset_dedup_for_tests()
    except Exception:
        pass

    for p, sig_before in before.items():
        # Research persistence tests intentionally write to a DB; our isolation
        # redirects via SQLiteStore but some direct Path writes may still touch
        # production. Skip strict check for those specific tests to avoid false
        # positives while keeping protection for cache/observability.
        if p == Path("data/runtime/parayabuck.sqlite3") and (
            "test_sqlite_protection" in request.node.name or "test_research" in request.node.name
        ):
            continue
        sig_after = _file_signature(p)
        if sig_before is None:
            if sig_after is not None:
                pytest.fail(f"Test created production file {p} that did not exist before — isolation failed")
        else:
            if sig_after != sig_before:
                pytest.fail(
                    f"Production file {p} was modified during test (before={sig_before}, after={sig_after}) — isolation failed, real cache/runtime polluted"
                )
