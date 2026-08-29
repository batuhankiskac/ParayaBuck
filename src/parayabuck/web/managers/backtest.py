"""Isolated BacktestManager for Web one-off backtests."""

from __future__ import annotations

import io
import json
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.backtest")

_ACTIVE_ENGINE_STATES = frozenset(
    {"starting", "running", "warming_up", "degraded", "halted", "stopping", "stop_failed"}
)


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
        self._engine_state_provider = engine_state_provider

    def bind_engine_state_provider(self, provider: Callable[[], str]) -> None:
        self._engine_state_provider = provider

    @property
    def is_running(self) -> bool:
        with self.lock:
            return self.state == "running" and self._thread is not None and self._thread.is_alive()

    def start(self, payload: dict[str, Any]) -> str:
        with self.lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("Bir backtest zaten çalışıyor; bitmesini bekleyin")
            engine_state = self._resolve_engine_state()
            if engine_state in _ACTIVE_ENGINE_STATES:
                raise ValueError(
                    "A Web backtest cannot start while the trading engine is "
                    f"active ({engine_state}); stop the engine first"
                )
            self.state = "running"
            self.run_id = f"web-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
            self.output_dir = str(Path("reports") / self.run_id)
            self.started_at = datetime.now(UTC).isoformat()
            self.finished_at = None
            self.error = None
            self.summary = None
            self.universe = None
            self.progress = []
            argv = self._build_argv(payload, self.output_dir)
            self._thread = self._thread_factory(target=self._run, args=(argv,), daemon=True, name="web-backtest")
            self._thread.start()
            return self.run_id

    def _resolve_engine_state(self) -> str:
        if self._engine_state_provider is not None:
            try:
                return self._engine_state_provider()
            except Exception:
                return "unknown"
        # Lazy fallback to global manager for compatibility
        try:
            from parayabuck.web.state import get_web_state

            state = get_web_state()
            if state is not None and getattr(state, "engine_manager", None) is not None:
                return state.engine_manager.current_engine_state()  # type: ignore[no-any-return]
        except Exception:
            pass
        try:
            from parayabuck.web.server import manager as legacy_manager

            return legacy_manager.current_engine_state()
        except Exception:
            return "stopped"

    @staticmethod
    def _build_argv(payload: dict[str, Any], output_dir: str) -> list[str]:
        argv = ["backtest", "--output-dir", output_dir]
        market = payload.get("market") or "equity"
        argv += ["--market", market]
        symbols = (payload.get("symbols") or "").strip()
        if symbols:
            argv += ["--symbols", symbols]
        end = datetime.now(UTC)
        days = int(payload.get("days") or 0)
        start = end - timedelta(days=days or int(payload.get("years") or 5) * 365)
        argv += ["--start", start.date().isoformat(), "--end", end.date().isoformat()]
        max_symbols = payload.get("max_symbols") or 0
        if max_symbols > 0:
            argv += ["--candidate-limit", str(int(max_symbols))]
        strategy = (payload.get("strategy") or "").strip()
        if strategy:
            argv += ["--strategy", strategy]
        for name, value in sorted(dict(payload.get("parameters") or {}).items()):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
                raise ValueError(f"Invalid parameter name: {name!r}")
            serialized = json.dumps(value)
            if len(serialized) > 4096:
                raise ValueError(f"Parameter value too large for {name!r}")
            argv += ["--param", f"{name}={serialized}"]
        return argv

    def _run(self, argv: list[str]) -> None:
        import contextlib

        from parayabuck import cli as product_cli

        error: str | None = None
        try:
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
        if error is None:
            reports = sorted(Path(self.output_dir).glob("*/report.json"))
            report_path = reports[-1] if reports else Path(self.output_dir) / "report.json"
            universe_path = report_path.with_name("universe.json")
            try:
                if not report_path.exists():
                    error = f"Rapor üretilmedi: {report_path} (CLI hatasız çıktı ama rapor dosyası yazılmadı)"
                else:
                    raw = json.loads(report_path.read_text(encoding="utf-8"))
                    summary = raw.get("summary")
                if universe_path.exists():
                    universe = json.loads(universe_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                error = f"Rapor okunamadı: {exc}"
        with self.lock:
            self.state = "error" if error else "done"
            self.error = error
            self.summary = summary
            self.universe = universe
            self.finished_at = datetime.now(UTC).isoformat()
        log.info("Web backtest finished: %s (%s)", self.run_id, self.state)

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
            }
