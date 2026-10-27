"""Isolated BacktestManager for Web one-off backtests."""

from __future__ import annotations

import io
import json
import re
import shutil
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.backtest")

_ACTIVE_ENGINE_STATES = frozenset(
    {"starting", "running", "warming_up", "degraded", "halted", "stopping", "stop_failed"}
)
_BACKTEST_ALLOWED_ENGINE_STATES = frozenset({"stopped", "error"})
_MAX_OPERATOR_ERROR_LENGTH = 1000
# Input bounds: one request must not build a giant argv (local DoS).
_MAX_PARAMETERS = 64
_MAX_SERIALIZED_PARAMETERS = 16384
_MAX_SYMBOLS_LENGTH = 4096
_MAX_STRATEGY_LENGTH = 128
# Retained reports/web-* run directories; the oldest are deleted on start.
_RETAINED_WEB_RUNS = 20
_WEB_RUN_ID = re.compile(r"web-\d{8}T\d{6}Z-[0-9a-f]{8}")


class BacktestManager:
    """Runs the production-strategy backtest CLI in a background thread."""

    def __init__(self, engine_state_provider: Callable[[], str] | None = None) -> None:
        self.lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._thread_factory = threading.Thread
        self.state = "idle"
        self.run_id = ""
        self.output_dir = ""
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None
        self.progress: list[str] = []
        self.summary: dict[str, Any] | None = None
        self.universe: dict[str, Any] | None = None
        # The window the run was started with, shown next to the summary so
        # a 90-day figure is never read as a multi-year one.
        self.period: dict[str, Any] | None = None
        self._engine_state_provider = engine_state_provider
        self.reports_root = Path("reports")

    def bind_engine_state_provider(self, provider: Callable[[], str]) -> None:
        self._engine_state_provider = provider

    @property
    def is_running(self) -> bool:
        with self.lock:
            try:
                alive = self._thread is not None and self._thread.is_alive()
            except BaseException:
                # An unobservable thread is not proof of termination; keep
                # the exclusion closed until an operator can inspect it.
                alive = True
            return self.state == "running" and alive

    def start(self, payload: dict[str, Any]) -> str:
        with self.lock:
            try:
                active_thread = self._thread is not None and self._thread.is_alive()
            except BaseException as exc:
                raise ValueError(
                    "The backtest thread state could not be verified; refusing to start another run"
                ) from exc
            if active_thread:
                raise ValueError("Bir backtest zaten çalışıyor; bitmesini bekleyin")
            engine_state = self._resolve_engine_state()
            if engine_state in _ACTIVE_ENGINE_STATES:
                raise ValueError(
                    "A Web backtest cannot start while the trading engine is "
                    f"active ({engine_state}); stop the engine first"
                )
            if engine_state not in _BACKTEST_ALLOWED_ENGINE_STATES:
                raise ValueError("The trading engine state could not be verified; refusing to start a Web backtest")
            self.run_id = f"web-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
            self.output_dir = str(self.reports_root / self.run_id)
            self.started_at = datetime.now(UTC).isoformat()
            self.finished_at = None
            self.error = None
            self.summary = None
            self.universe = None
            self.period = None
            self.progress = []
            try:
                # Validate all user input before publishing a running state.
                argv = self._build_argv(payload, self.output_dir)
                self.period = self._period_from_argv(argv)
                self._prune_web_runs()
                thread = self._thread_factory(target=self._run, args=(argv,), daemon=True, name="web-backtest")
                self._thread = thread
                self.state = "running"
                thread.start()
            except BaseException as exc:
                self._thread = None
                self._terminalize_start_failure(exc)
                raise
            return self.run_id

    def _prune_web_runs(self) -> None:
        """Keep the newest ``_RETAINED_WEB_RUNS - 1`` web runs before a new one.

        Only directories named like a Web run id are touched; the run ids
        start with a UTC timestamp, so name order is age order.
        """
        try:
            runs = sorted(
                entry
                for entry in self.reports_root.iterdir()
                if entry.is_dir() and not entry.is_symlink() and _WEB_RUN_ID.fullmatch(entry.name)
            )
        except OSError:
            return
        for stale in runs[: max(0, len(runs) - (_RETAINED_WEB_RUNS - 1))]:
            try:
                shutil.rmtree(stale)
            except OSError as exc:
                log.warning("Could not prune old Web backtest report %s: %s", stale, exc)

    def _resolve_engine_state(self) -> str:
        if self._engine_state_provider is not None:
            try:
                state = self._engine_state_provider()
                return state if isinstance(state, str) else "unknown"
            except BaseException:
                return "unknown"
        # A manager without an explicit composition-owned provider cannot
        # prove mutual exclusion and must fail closed.
        return "unknown"

    @staticmethod
    def _build_argv(payload: dict[str, Any], output_dir: str) -> list[str]:
        argv = ["backtest", "--output-dir", output_dir]
        market = payload.get("market") or "equity"
        argv += ["--market", market]
        symbols = (payload.get("symbols") or "").strip()
        if len(symbols) > _MAX_SYMBOLS_LENGTH:
            raise ValueError("Symbols list is too long")
        if symbols:
            argv += ["--symbols", symbols]
        end = datetime.now(UTC)
        days = int(payload.get("days") or 0)
        start = end - timedelta(days=days or int(payload.get("years") or 5) * 365)
        argv += ["--start", start.date().isoformat(), "--end", end.date().isoformat()]
        strategy = (payload.get("strategy") or "").strip()
        if len(strategy) > _MAX_STRATEGY_LENGTH:
            raise ValueError("Strategy name is too long")
        if strategy:
            argv += ["--strategy", strategy]
        parameters = dict(payload.get("parameters") or {})
        if len(parameters) > _MAX_PARAMETERS:
            raise ValueError(f"Too many parameters (at most {_MAX_PARAMETERS})")
        total = 0
        for name, value in sorted(parameters.items()):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
                raise ValueError(f"Invalid parameter name: {name!r}")
            serialized = json.dumps(value)
            if len(serialized) > 4096:
                raise ValueError(f"Parameter value too large for {name!r}")
            total += len(serialized)
            if total > _MAX_SERIALIZED_PARAMETERS:
                raise ValueError("Parameters are too large in total")
            argv += ["--param", f"{name}={serialized}"]
        return argv

    @staticmethod
    def _period_from_argv(argv: list[str]) -> dict[str, Any]:
        """The ``--start``/``--end`` window ``_build_argv`` put on the command line."""
        start = date.fromisoformat(argv[argv.index("--start") + 1])
        end = date.fromisoformat(argv[argv.index("--end") + 1])
        return {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days}

    def _run(self, argv: list[str]) -> None:
        import contextlib

        error: str | None = None
        try:
            from parayabuck import cli as product_cli

            with (
                contextlib.redirect_stdout(self._stream()),
                contextlib.redirect_stderr(self._stream()),
            ):
                product_cli.main(argv)
        except SystemExit as exc:
            error = str(exc) or "Backtest CLI beklenmedik şekilde çıktı"
        except Exception as exc:
            error = str(exc)
            log.exception("Backtest run failed")
        except BaseException as exc:
            error = str(exc) or f"{type(exc).__name__}"
            log.exception("Backtest run failed with a base exception")
        finally:
            self._finish(error)

    def _stream(self) -> Any:
        class ProgressStream(io.StringIO):
            def __init__(self, owner: BacktestManager) -> None:
                super().__init__()
                self.owner = owner

            def write(self, text: str) -> int:
                with self.owner.lock:
                    self.owner.progress.extend(text.splitlines())
                    if len(self.owner.progress) > 300:
                        self.owner.progress = self.owner.progress[-300:]
                return super().write(text)

        return ProgressStream(self)

    def _finish(self, error: str | None) -> None:
        summary: dict[str, Any] | None = None
        universe: dict[str, Any] | None = None
        try:
            if error is None:
                output_dir = Path(self.output_dir)
                reports = sorted(output_dir.glob("*/report.json"))
                report_path = reports[-1] if reports else output_dir / "report.json"
                universe_path = report_path.with_name("universe.json")
                if not report_path.exists():
                    error = f"Rapor üretilmedi: {report_path} (CLI hatasız çıktı ama rapor dosyası yazılmadı)"
                else:
                    raw = json.loads(report_path.read_text(encoding="utf-8"))
                    if not isinstance(raw, Mapping):
                        raise ValueError("Rapor kökü bir JSON nesnesi değil")
                    raw_summary = raw.get("summary")
                    if raw_summary is not None and not isinstance(raw_summary, Mapping):
                        raise ValueError("Rapor summary alanı bir JSON nesnesi değil")
                    summary = dict(raw_summary) if isinstance(raw_summary, Mapping) else None
                if universe_path.exists():
                    raw_universe = json.loads(universe_path.read_text(encoding="utf-8"))
                    if not isinstance(raw_universe, Mapping):
                        raise ValueError("Universe raporu bir JSON nesnesi değil")
                    universe = dict(raw_universe)
        except BaseException as exc:
            error = f"Rapor okunamadı: {exc}"
            summary = None
            universe = None
        error = self._bounded_error(error)
        with self.lock:
            self.state = "error" if error else "done"
            self.error = error
            self.summary = summary
            self.universe = universe
            self.finished_at = datetime.now(UTC).isoformat()
        log.info("Web backtest finished: %s (%s)", self.run_id, self.state)

    def _terminalize_start_failure(self, exc: BaseException) -> None:
        """Publish a terminal error for an attempt that never launched."""
        self.state = "error"
        self.error = self._bounded_error(str(exc) or type(exc).__name__)
        self.summary = None
        self.universe = None
        self.finished_at = datetime.now(UTC).isoformat()

    @staticmethod
    def _bounded_error(error: str | None) -> str | None:
        if error is None:
            return None
        text = str(error).strip() or "Backtest failed"
        if len(text) > _MAX_OPERATOR_ERROR_LENGTH:
            return text[: _MAX_OPERATOR_ERROR_LENGTH - 3] + "..."
        return text

    def status(self) -> dict[str, Any]:
        is_running = self.is_running
        with self.lock:
            return {
                "state": "running" if is_running else self.state,
                "run_id": self.run_id,
                "output_dir": self.output_dir,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "progress": list(self.progress[-60:]),
                "summary": self.summary,
                "universe": self.universe,
                "period": self.period,
            }
