"""F44/F49 — the journal honours ``observability:`` and every kept hook fires.

Before the fix the process-global journal was built from hard-coded defaults
(``journal.get_journal()`` with ``DEFAULT_JOURNAL_PATH``), so a configured
``observability.journal_path`` left ``ops events`` reading a file the engine
never wrote. The runtime now hands the config block to
``journal.configure_global_journal`` at construction, and the two runtime-health
hooks are wired to the real shared-worker degraded/recovered transitions instead
of sitting in observe.py with no caller.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.config import Config, ObservabilityConfig
from parayabuck.monitoring import journal as journal_module
from parayabuck.monitoring import observe as observe_module
from parayabuck.monitoring.journal import EventJournal, configure_global_journal, get_journal
from parayabuck.runtime import EngineRuntime
from parayabuck.runtime_health import WorkerHealth


@pytest.fixture
def _unconfigured_global_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend this process has not decided on a global journal yet."""
    monkeypatch.setattr(journal_module, "_global_journal", None)
    monkeypatch.setattr(journal_module, "_global_is_configured", False)
    observe_module.reset_dedup_for_tests()


def _wait_for_kind(journal: EventJournal, kind: str, expected: int, timeout: float = 5.0) -> list[str]:
    deadline = time.monotonic() + timeout
    kinds: list[str] = []
    while True:
        kinds = [e.kind for e in journal.query(kind=kind, limit=50)]
        if len(kinds) >= expected or time.monotonic() >= deadline:
            return kinds
        time.sleep(0.02)


# ---------------------------------------------------------------------------
# dedupe absence probe
# ---------------------------------------------------------------------------


def _wait_for_absence(journal: EventJournal, kind: str, expected: int, timeout: float = 0.2) -> bool:
    """Hold ``timeout`` seconds asserting no row beyond ``expected`` appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(journal.query(kind=kind, limit=50)) != expected:
            return False
        time.sleep(0.02)
    return len(journal.query(kind=kind, limit=50)) == expected


# ---------------------------------------------------------------------------
# F44 — observability.journal_path (and the retention/queue knobs) are applied
# ---------------------------------------------------------------------------


def test_configure_global_journal_honours_configured_path(tmp_path: Path, _unconfigured_global_journal: None) -> None:
    configured = tmp_path / "configured.sqlite3"
    observability = ObservabilityConfig(
        journal_path=str(configured),
        event_retention_days=3,
        event_max_events=11,
        event_queue_size=7,
    )

    installed = configure_global_journal(observability)

    assert installed is not None
    assert Path(str(get_journal().path)) == configured
    assert get_journal() is installed
    assert get_journal().queue_size == 7
    assert get_journal().retention_days == 3
    assert get_journal().max_events == 11


def test_configure_global_journal_keeps_isolated_get_journal_contract(
    tmp_path: Path, _unconfigured_global_journal: None
) -> None:
    """An explicit ``get_journal(path)`` still returns its own journal."""
    configured = tmp_path / "configured.sqlite3"
    isolated = tmp_path / "isolated.sqlite3"

    configure_global_journal(ObservabilityConfig(journal_path=str(configured)))
    other = get_journal(isolated)

    assert Path(str(other.path)) == isolated
    assert other is not get_journal()
    assert Path(str(get_journal().path)) == configured


def test_configure_global_journal_does_not_steal_an_explicit_journal(
    tmp_path: Path, _unconfigured_global_journal: None
) -> None:
    """A journal installed at an explicit path keeps ownership."""
    explicit = tmp_path / "explicit.sqlite3"
    journal_module._global_journal = EventJournal(explicit)

    returned = configure_global_journal(ObservabilityConfig(journal_path=str(tmp_path / "configured.sqlite3")))

    assert returned is not None
    assert Path(str(returned.path)) == explicit
    assert Path(str(get_journal().path)) == explicit


def test_configure_global_journal_is_decided_once(tmp_path: Path, _unconfigured_global_journal: None) -> None:
    """A second runtime construction cannot orphan the first journal's writer."""
    first = configure_global_journal(ObservabilityConfig(journal_path=str(tmp_path / "first.sqlite3")))
    second = configure_global_journal(ObservabilityConfig(journal_path=str(tmp_path / "second.sqlite3")))

    assert second is first
    assert Path(str(get_journal().path)) == tmp_path / "first.sqlite3"


def test_runtime_construction_configures_the_global_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[ObservabilityConfig] = []
    monkeypatch.setattr(runtime_module, "configure_global_journal", calls.append)

    config = Config()
    runtime = EngineRuntime("key", "secret", config)
    runtime._snapshot_writer = None  # type: ignore[attr-defined]

    assert calls == [config.observability]
    assert calls[0].journal_path == config.observability.journal_path


# ---------------------------------------------------------------------------
# F49 — the retained health hooks have a real, edge-triggered caller
# ---------------------------------------------------------------------------


def test_shared_worker_failure_emits_one_degraded_and_one_recovered_event() -> None:
    """The runtime's degraded/recovered transitions are the hooks' only callers.

    Pins the invariant the deleted hooks lacked: exactly one
    ``runtime_health_degraded`` per transition into "degraded" and one matching
    ``runtime_health_recovered`` when the retry un-pins it — not one event per
    failed retry, and not zero.
    """
    journal = journal_module.get_journal()
    runtime = EngineRuntime("key", "secret", Config())
    runtime._snapshot_writer = None  # type: ignore[attr-defined]
    runtime._running = True  # type: ignore[attr-defined]
    runtime._state = "running"  # type: ignore[attr-defined]
    runtime._state_lock = threading.RLock()  # type: ignore[attr-defined]
    runtime._runtime_session_id = "sess-health"  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    # "pb-stocks" is the only market-owned worker; any other name is the
    # shared worker whose failure degrades the whole runtime.
    runtime._workers = {"pb-trade-updates": WorkerHealth(started_at=now, last_heartbeat=now)}  # type: ignore[attr-defined]

    class _FlakyWorker:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("stream down")
            runtime._running = False  # type: ignore[attr-defined]

    try:
        runtime._run_stream_worker("pb-trade-updates", _FlakyWorker())  # type: ignore[attr-defined]
    finally:
        runtime._running = False  # type: ignore[attr-defined]

    assert _wait_for_kind(journal, "runtime_health_degraded", 1) == ["runtime_health_degraded"]
    assert _wait_for_kind(journal, "runtime_health_recovered", 1) == ["runtime_health_recovered"]
    assert runtime._state != "degraded"  # type: ignore[attr-defined]

    degraded = journal.query(kind="runtime_health_degraded", limit=50)
    assert [e.session_id for e in degraded] == ["sess-health"]
    assert "stream down" in str(degraded[0].fields.get("reason", ""))
    assert str(degraded[0].severity) == "WARNING"


def test_health_hooks_dedupe_repeated_states() -> None:
    """A repeated identical state produces no second event (no journal flood)."""
    journal = journal_module.get_journal()
    observe_module.reset_dedup_for_tests()

    observe_module.on_runtime_health_degraded(reason="probe", session_id="s1")
    observe_module.on_runtime_health_degraded(reason="probe", session_id="s1")
    assert _wait_for_kind(journal, "runtime_health_degraded", 1) == ["runtime_health_degraded"]
    assert _wait_for_absence(journal, "runtime_health_degraded", 1)
    assert len(journal.query(kind="runtime_health_degraded", limit=50)) == 1

    observe_module.on_runtime_health_recovered(session_id="s1")
    observe_module.on_runtime_health_recovered(session_id="s1")
    assert _wait_for_kind(journal, "runtime_health_recovered", 1) == ["runtime_health_recovered"]
    assert _wait_for_absence(journal, "runtime_health_recovered", 1)
    assert len(journal.query(kind="runtime_health_recovered", limit=50)) == 1
