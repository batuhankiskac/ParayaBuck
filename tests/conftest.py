"""Test isolation: prevent production cache/runtime pollution and engine crashes."""

from __future__ import annotations

import builtins
import contextlib
import io
import ipaddress
import os
import shutil
import socket
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import unquote

import pytest
import yaml

from parayabuck.data import cache as cache_module
from parayabuck.data.cache import WarmupCache
from parayabuck.execution import risk_peak as risk_peak_module
from parayabuck.monitoring import health as health_module
from parayabuck.monitoring import journal as journal_module
from parayabuck.monitoring import receipt as receipt_module
from parayabuck.monitoring import snapshot as snapshot_module
from parayabuck.monitoring.journal import DEFAULT_JOURNAL_PATH, EventJournal
from parayabuck.monitoring.snapshot import DEFAULT_SNAPSHOT_PATH, SnapshotWriter
from parayabuck.storage import SQLiteStore
from parayabuck.web.state import get_web_state, set_web_state

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Mutable state the operator owns. Tests must never open or write it: a read
# makes the suite depend on machine state (the PAPER peak sidecar carries the
# live account id, so a fake account trips the account-scoped fail-closed
# check), and a write corrupts the running engine's state.
_PROTECTED_STATE_DIRS = (
    _REPO_ROOT / "data" / "runtime",
    _REPO_ROOT / "data" / "cache",
    _REPO_ROOT / "data" / "backups",
    _REPO_ROOT / "data" / "research_runs",
    _REPO_ROOT / "data" / "tax",
    _REPO_ROOT / "logs",
)

# The shipped config is read by many tests, so it is protected against writes
# only: a test that reads config/settings.yaml is fine, one that rewrites it is not.
_PROTECTED_WRITE_DIRS = (_REPO_ROOT / "config",)

# Cheap prefilter: only paths mentioning these fragments are resolved and
# classified, so the guard costs a substring test per open().
_STATE_MARKERS = ("data/", "logs/", "config/")

# --- Session-wide credential and network isolation -------------------------
#
# The repository's .env holds the shared PAPER key of the account the GCP VM
# trades, and load_dotenv() is called from load_config(), the CLI and the web
# engine manager. A test that loaded it could therefore authenticate against
# that account, so: credentials are stripped from the environment, no dotenv
# file is loaded unless a test created it explicitly under the temp dir (never
# by upward search, never inside or above the checkout), and every
# non-loopback socket connection raises.

_CREDENTIAL_ENV_PREFIXES = ("ALPACA_", "PARAYABUCK_")
# systemd points CREDENTIALS_DIRECTORY at the operator's secret files, which
# config.read_credential reads before the environment: strip it too.
_CREDENTIAL_ENV_NAMES = ("CREDENTIALS_DIRECTORY",)
_LOOPBACK_HOSTS = frozenset({"", "localhost", "::1", "0:0:0:0:0:0:0:1"})
_MISSING = object()


class OutboundNetworkBlocked(RuntimeError):
    """A test tried to reach a host other than the loopback interface."""


def _is_loopback(address: object) -> bool:
    host = address[0] if isinstance(address, tuple) and address else address
    try:
        text = str(host)
    except Exception:
        return False
    if text in _LOOPBACK_HOSTS:
        return True
    # Parse the address rather than prefix-match it: "127.example.com" is a
    # hostname that a prefix test would admit, and resolving it is a network call.
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


@pytest.fixture(scope="session", autouse=True)
def _block_outbound_network():
    """Fail any test that opens a connection off the loopback interface."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def _guard_connect(self, address):  # type: ignore[no-untyped-def]
        if getattr(self, "family", None) == socket.AF_UNIX or _is_loopback(address):
            return real_connect(self, address)
        raise OutboundNetworkBlocked(f"outbound network blocked in tests: connect({address!r})")

    def _guard_connect_ex(self, address):  # type: ignore[no-untyped-def]
        if getattr(self, "family", None) == socket.AF_UNIX or _is_loopback(address):
            return real_connect_ex(self, address)
        raise OutboundNetworkBlocked(f"outbound network blocked in tests: connect_ex({address!r})")

    def _guard_create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        if _is_loopback(address):
            return real_create_connection(address, *args, **kwargs)
        raise OutboundNetworkBlocked(f"outbound network blocked in tests: create_connection({address!r})")

    socket.socket.connect = _guard_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guard_connect_ex  # type: ignore[method-assign]
    socket.create_connection = _guard_create_connection  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        socket.create_connection = real_create_connection  # type: ignore[assignment]


_saved_credential_env: dict[str, str] = {}
_session_env_dir: str | None = None
_real_load_dotenv = None
_real_dotenv_values = None


def _inside_repo(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents


def _dotenv_allowed(path: Path) -> bool:
    """True only for a dotenv file a test created under the OS temp dir.

    Anything inside the checkout, in a directory that contains the checkout
    (a parent ``.env``, e.g. the main checkout above a ``.claude/worktrees``
    worktree), or outside the temp dir is refused.
    """
    try:
        resolved = path.resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return False
    if _inside_repo(resolved):
        return False
    if resolved.parent == _REPO_ROOT or resolved.parent in _REPO_ROOT.parents:
        return False
    return resolved != temp_root and temp_root in resolved.parents


def _install_dotenv_guard() -> None:
    """Refuse every dotenv load except explicit files under the temp dir.

    A bare ``load_dotenv()`` searches upward from the caller's file, which
    from a worktree checkout reaches the main checkout's real ``.env``, so
    an implicit search never loads anything during tests.
    """
    global _real_load_dotenv, _real_dotenv_values
    import dotenv
    import dotenv.main

    _real_load_dotenv = dotenv.main.load_dotenv

    def _guarded_load_dotenv(dotenv_path=None, stream=None, **kwargs):  # type: ignore[no-untyped-def]
        if stream is not None:
            return _real_load_dotenv(dotenv_path, stream, **kwargs)
        if dotenv_path is None:
            return False
        try:
            candidate = Path(dotenv_path)
        except TypeError:
            return False
        if not _dotenv_allowed(candidate):
            return False
        return _real_load_dotenv(str(candidate), stream, **kwargs)

    dotenv.main.load_dotenv = _guarded_load_dotenv  # type: ignore[assignment]
    dotenv.load_dotenv = _guarded_load_dotenv  # type: ignore[assignment]

    real_dotenv_values = dotenv.main.dotenv_values
    _real_dotenv_values = real_dotenv_values

    def _guarded_dotenv_values(dotenv_path=None, stream=None, **kwargs):  # type: ignore[no-untyped-def]
        if stream is None and (dotenv_path is None or not _dotenv_allowed(Path(dotenv_path))):
            return {}
        return real_dotenv_values(dotenv_path, stream, **kwargs)

    dotenv.main.dotenv_values = _guarded_dotenv_values  # type: ignore[assignment]
    dotenv.dotenv_values = _guarded_dotenv_values  # type: ignore[assignment]

    # A module that ran ``from dotenv import load_dotenv`` before this point
    # (config.py does, through the conftest import chain) still holds the real
    # function, and calling it reads the repo .env. Repoint those bindings.
    for module in list(sys.modules.values()):
        if not getattr(module, "__name__", "").startswith("parayabuck"):
            continue
        for name, real, guarded in (
            ("load_dotenv", _real_load_dotenv, _guarded_load_dotenv),
            ("dotenv_values", real_dotenv_values, _guarded_dotenv_values),
        ):
            if vars(module).get(name) is real:
                setattr(module, name, guarded)


def pytest_configure(config: pytest.Config) -> None:
    """Strip credentials and neutralize the repo .env before any test imports."""
    global _session_env_dir
    for key in [key for key in os.environ if key.startswith(_CREDENTIAL_ENV_PREFIXES) or key in _CREDENTIAL_ENV_NAMES]:
        _saved_credential_env[key] = os.environ.pop(key)
    _session_env_dir = tempfile.mkdtemp(prefix="parayabuck-test-env-")
    # ENV_PATH is what the engine manager and CLI resolve their dotenv from;
    # point it at a file that is never created, so no key is ever read.
    os.environ["ENV_PATH"] = str(Path(_session_env_dir) / ".env")
    _install_dotenv_guard()


def pytest_unconfigure(config: pytest.Config) -> None:
    os.environ.update(_saved_credential_env)
    _saved_credential_env.clear()
    if _session_env_dir is not None:
        shutil.rmtree(_session_env_dir, ignore_errors=True)


def _path_text(path: object) -> str | None:
    """The file path ``path`` names as text, with a sqlite ``file:`` URI unwrapped.

    ``sqlite3.connect(uri, uri=True)`` names a file through a ``file:`` URI, so
    the scheme, the query string (``?mode=ro``) and any authority are not part
    of the path. Without this, ``file:data/runtime/...`` resolved to a file
    literally called ``file:...`` under the cwd and every read-only open of
    production state passed the guard.
    """
    if path is None or isinstance(path, int):
        return None
    try:
        raw = os.fsdecode(os.fspath(path))
    except TypeError:
        return None
    if raw.startswith("file:"):
        rest = raw[len("file:") :].split("?", 1)[0]
        if rest.startswith("//"):
            slash = rest.find("/", 2)
            rest = rest[slash:] if slash != -1 else ""
        raw = unquote(rest)
    return raw


def protected_state_path(path: object) -> Path | None:
    """Return ``path`` resolved when it names a file under a production state dir."""
    raw = _path_text(path)
    if raw is None:
        return None
    if not any(marker in raw for marker in _STATE_MARKERS):
        return None
    try:
        resolved = Path(raw).resolve()
    except OSError:
        return None
    for root in _PROTECTED_STATE_DIRS:
        if resolved == root or root in resolved.parents:
            return resolved
    return None


def _display(path: str) -> str:
    try:
        return str(Path(path).relative_to(_REPO_ROOT))
    except ValueError:
        return path


def _protected_config_path(path: object) -> Path | None:
    """Return ``path`` resolved when it names a file under the shipped config dir."""
    raw = _path_text(path)
    if raw is None or "config/" not in raw:
        return None
    try:
        resolved = Path(raw).resolve()
    except OSError:
        return None
    for root in _PROTECTED_WRITE_DIRS:
        if resolved == root or root in resolved.parents:
            return resolved
    return None


def _state_tree_signature() -> dict[str, tuple[int, int]]:
    """mtime/size of every entry under the production state dirs.

    Walks with ``os.walk`` and ignores errors: a background thread that is
    still rotating files can remove a directory mid-walk, and that must not
    fail the test that happens to be running.
    """
    signature: dict[str, tuple[int, int]] = {}
    for root in _PROTECTED_STATE_DIRS:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                signature[path] = (st.st_mtime_ns, st.st_size)
    return signature


class ProductionStateAccessError(RuntimeError):
    """A test tried to open production state (or write the shipped config)."""


def _is_writing_mode(mode: object) -> bool:
    return any(flag in str(mode) for flag in "wax+")


def _is_writing_flags(flags: object) -> bool:
    if not isinstance(flags, int):
        return True
    return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))


@pytest.fixture
def isolated_config_path(tmp_path: Path) -> Path:
    """The shipped config with its state paths inside ``tmp_path``.

    The ops commands and the web ops read model resolve their snapshot, journal
    and decision DB from the config, so a test that runs one of them against
    the shipped config answers from the operator's ``data/runtime`` — present on
    the trading VM, absent on a fresh checkout. Point those tests at this file
    (``--config`` on the CLI, the engine manager's ``config_path`` in the web).
    """
    raw = yaml.safe_load((_REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw.setdefault("runtime", {})["decision_db_path"] = str(tmp_path / "decisions.sqlite3")
    raw["backup"] = {**(raw.get("backup") or {}), "path": str(tmp_path / "backups")}
    raw["observability"] = {
        **(raw.get("observability") or {}),
        "snapshot_path": str(tmp_path / "health_snapshot.json"),
        "journal_path": str(tmp_path / "observability.sqlite3"),
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _restore_web_state():
    before = get_web_state()
    # Only a missing optional web stack may skip the restore. Any other error
    # while restoring must surface, or the next test runs against a stale state.
    try:
        from parayabuck.web import server as srv
    except ImportError:
        srv = None
    orig_manager = getattr(srv, "manager", None)
    orig_backtest = getattr(srv, "backtest_manager", None)

    yield

    current = get_web_state()
    if current is not before:
        set_web_state(before)
    if srv is None:
        return
    if before is not None and srv.web_state is not before:
        srv.web_state = before  # type: ignore[attr-defined]
        set_web_state(before)
    if orig_manager is not None and srv.manager is not orig_manager:
        srv.manager = orig_manager  # type: ignore[attr-defined]
    if orig_backtest is not None and srv.backtest_manager is not orig_backtest:
        srv.backtest_manager = orig_backtest  # type: ignore[attr-defined]


_BACKTEST_MANAGER_FIELDS = (
    "state",
    "_thread",
    "run_id",
    "output_dir",
    "started_at",
    "finished_at",
    "error",
    "summary",
    "universe",
    "progress",
    "_thread_factory",
)

# Environment variables a test can only set through production code, not
# monkeypatch: parayabuck.web.app.main() exports CONFIG_PATH for the process it
# serves, so a test that launched the entry point with --config repointed every
# later load_config() at its own fixture config.
_PROCESS_ENV_KEYS = ("CONFIG_PATH", "ENV_PATH")


@pytest.fixture(autouse=True)
def _restore_process_singletons():
    """Keep the alert sink, the Web backtest singleton and the process env clean.

    All three are process-global: a test that shortened the alert throttle,
    left a backtest ``running``, or launched the web entry point decided the
    assertions of whichever test ran next.
    """
    from parayabuck.monitoring import alerts as alerts_module

    alerts_module.reset_for_tests()
    before_env = {key: os.environ.get(key, _MISSING) for key in _PROCESS_ENV_KEYS}
    try:
        from parayabuck.web import server as srv

        backtest = getattr(srv, "backtest_manager", None)
    except Exception:
        backtest = None
    before = (
        None if backtest is None else {name: getattr(backtest, name, _MISSING) for name in _BACKTEST_MANAGER_FIELDS}
    )

    yield

    alerts_module.reset_for_tests()
    for key, value in before_env.items():
        if value is _MISSING:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    if before is None:
        return
    for name, value in before.items():
        if value is _MISSING:
            with contextlib.suppress(AttributeError):
                delattr(backtest, name)
        else:
            setattr(backtest, name, value)


# Production-state accesses the guard refused during the running test. Teardown
# fails the test on any entry, so a test that refuses one on purpose consumes
# it with ``_STATE_ACCESSES.clear()`` after asserting the refusal.
_STATE_ACCESSES: list[str] = []


def _refuse_browser(*_args: object, **_kwargs: object) -> bool:
    """Stand-in for webbrowser.open: a test never launches a browser tab."""
    return False


@pytest.fixture(autouse=True)
def _test_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    tree_before = _state_tree_signature()
    threads_before = set(threading.enumerate())
    _STATE_ACCESSES.clear()

    # web.app.main opens the dashboard in the default browser unless
    # OPEN_BROWSER is off, so a test that reaches it opened a real tab.
    for name in ("open", "open_new", "open_new_tab"):
        monkeypatch.setattr(webbrowser, name, _refuse_browser)
    iso_cache = tmp_path / "warmup_isolated.sqlite3"
    iso_journal = tmp_path / "observability_isolated.sqlite3"
    iso_snapshot = tmp_path / "health_snapshot_isolated.json"
    iso_db = tmp_path / "parayabuck_isolated.sqlite3"
    iso_receipt_dir = tmp_path / "paper_receipts"
    iso_receipt_dir.mkdir(parents=True, exist_ok=True)
    iso_cache.parent.mkdir(parents=True, exist_ok=True)

    # Access guard: refuse every open of production state before it happens,
    # so no byte is read or written. The access is also recorded, so the
    # teardown still reports it even if the code under test swallowed the error.
    def _check(path: object, *, writing: bool) -> None:
        hit = protected_state_path(path)
        if hit is None and writing:
            hit = _protected_config_path(path)
        if hit is not None:
            _STATE_ACCESSES.append(_display(str(hit)))
            raise ProductionStateAccessError(
                f"test tried to open production state {_display(str(hit))}: tests must use tmp_path"
            )

    orig_io_open = io.open

    def _guarded_io_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        _check(file, writing=_is_writing_mode(mode))
        return orig_io_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", _guarded_io_open)
    monkeypatch.setattr(builtins, "open", _guarded_io_open)

    orig_os_open = os.open

    def _guarded_os_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
        _check(path, writing=_is_writing_flags(flags))
        return orig_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", _guarded_os_open)

    # RiskPeakService resolves its sidecar to the relative data/runtime default
    # when neither peak_state_path nor runtime_state_dir is given, so such a
    # broker read (and later wrote) the operator's PAPER peak file — whose
    # account id then failed a fake account closed.
    iso_state_dir = tmp_path / "runtime_state"
    iso_state_dir.mkdir(parents=True, exist_ok=True)
    orig_resolve_peak = risk_peak_module.resolve_peak_state_path

    def _patched_resolve_peak(paper, peak_state_path=None, runtime_state_dir=None):  # type: ignore[no-untyped-def]
        if peak_state_path is None and runtime_state_dir is None:
            return orig_resolve_peak(paper, None, iso_state_dir)
        return orig_resolve_peak(paper, peak_state_path, runtime_state_dir)

    monkeypatch.setattr(risk_peak_module, "resolve_peak_state_path", _patched_resolve_peak)

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

    # Health read model: the web ops status, the CLI status and the diagnostic
    # bundle call evaluate_health with the config's snapshot path, whose default
    # is the relative data/runtime/health_snapshot.json. A test whose config
    # omits observability.snapshot_path then read the operator's snapshot, and
    # only on a checkout where that file exists, so the leak hid on a fresh
    # clone. Point any production snapshot path at the isolated one that the
    # SnapshotWriter redirect above writes.
    orig_evaluate_health = health_module.evaluate_health

    def _patched_evaluate_health(*, snapshot_path=DEFAULT_SNAPSHOT_PATH, **kwargs):  # type: ignore[no-untyped-def]
        if protected_state_path(snapshot_path) is not None:
            snapshot_path = iso_snapshot
        return orig_evaluate_health(snapshot_path=snapshot_path, **kwargs)

    monkeypatch.setattr(health_module, "evaluate_health", _patched_evaluate_health)

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

        monkeypatch.setattr(alpaca_module, "StockHistoricalDataClient", _BlockedStock)
    except Exception:
        pass

    # No patch for should_auto_start: tests that check its return value rely on
    # real logic (paper vs live + credentials). An auto-start cannot happen in a
    # test even if the web lifespan runs, because pytest_configure strips the
    # ALPACA_*/PARAYABUCK_* variables, no dotenv file inside the repo is ever
    # loaded and ENV_PATH names a file that does not exist — so the manager
    # never holds a key. Network is blocked on top of that (session fixture).
    # This machine is the designated trading host for every test that starts
    # an engine; tests asserting the single-runner guard delete the variable.
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")

    # Redirect any direct sqlite3.connect to production files to isolated tmp
    import sqlite3 as _sqlite3

    _orig_connect = _sqlite3.connect

    # The production files a test may name (often through the default config)
    # are redirected to this test's isolated copies before the guard runs. The
    # redirect matches the resolved path, so a relative path, an absolute path
    # and a sqlite ``file:`` URI (with its query, e.g. ``?mode=ro``) all land
    # on the isolated file; any other production path is still refused.
    redirects = {
        (_REPO_ROOT / "data/runtime/parayabuck.sqlite3").resolve(): iso_db,
        (_REPO_ROOT / "data/runtime/observability.sqlite3").resolve(): iso_journal,
        (_REPO_ROOT / "data/cache/warmup.sqlite3").resolve(): iso_cache,
    }

    def _redirected(database):  # type: ignore[no-untyped-def]
        target = redirects.get(protected_state_path(database))  # type: ignore[arg-type]
        if target is None:
            return database
        text = os.fsdecode(os.fspath(database))
        if text.startswith("file:"):
            query = text.split("?", 1)[1] if "?" in text else ""
            return f"file:{target}" + (f"?{query}" if query else "")
        return str(target)

    def _patched_connect(database, *args, **kwargs):  # type: ignore[no-untyped-def]
        database = _redirected(database)
        _check(database, writing=True)
        return _orig_connect(database, *args, **kwargs)

    monkeypatch.setattr(_sqlite3, "connect", _patched_connect)

    # A web backtest started without an explicit reports root resolves it from
    # the config (``reports``, cwd-relative), and the start prunes old web runs
    # there: point the default at this test's tmp dir so no test touches the
    # checkout's reports/.
    import parayabuck.web.managers.backtest as _backtest_manager_module

    monkeypatch.setattr(_backtest_manager_module, "_configured_reports_root", lambda: tmp_path / "reports")

    monkeypatch.setenv("PYTEST_ISOLATION", "1")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    yield

    # Every engine worker is a daemon thread, so a leak can only be seen by name,
    # not by the daemon flag. Only threads this test started are checked, and a
    # short join lets a worker that was asked to stop finish first.
    ignored = {"pb-journal-writer"}
    started = [
        t
        for t in threading.enumerate()
        if t not in threads_before and t.name.startswith("pb-") and t.name not in ignored
    ]
    for thread in started:
        thread.join(timeout=1.0)
    alive = [t.name for t in started if t.is_alive()]
    if alive:
        pytest.fail(f"Engine threads still alive after test: {alive} — tests must stop EngineRuntime")

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

    # Belt and braces: even an access the recorder cannot classify (a raw
    # os-level write, a rename) shows up as a changed state tree.
    tree_after = _state_tree_signature()
    created = sorted(_display(p) for p in set(tree_after) - set(tree_before))
    removed = sorted(_display(p) for p in set(tree_before) - set(tree_after))
    changed = sorted(_display(p) for p in set(tree_before) & set(tree_after) if tree_after[p] != tree_before[p])
    if created or removed or changed:
        pytest.fail(
            f"Production state changed during test (created={created}, removed={removed}, "
            f"changed={changed}) — isolation failed; tests must use tmp_path"
        )
    state_accesses = list(_STATE_ACCESSES)
    _STATE_ACCESSES.clear()
    if state_accesses:
        pytest.fail(
            f"Test opened production state paths {sorted(set(state_accesses))} — isolation failed; "
            "tests must use tmp_path, never the repo's data/runtime, data/cache or logs"
        )


@pytest.fixture(autouse=True)
def _fixed_trading_host_machine_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixed machine id for the host identity, so LIVE tests never depend on the host's /etc/machine-id.

    The identity fails closed without a readable machine id (S2-8-7); a
    developer's Mac has none, so the tests bind one instead of reading the host.
    """
    import parayabuck.trading_host as trading_host_module

    machine_id = tmp_path / "test-machine-id"
    machine_id.write_text("0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    monkeypatch.setattr(trading_host_module, "_MACHINE_ID_PATHS", (str(machine_id),))
