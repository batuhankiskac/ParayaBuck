"""SettingsService — transactional save VALIDATE→PLAN→PRE-FLIGHT→stop→atomic write→restart.

Transaction contract
------------------
1. VALIDATE — ``manager._validated_settings_payload(payload)`` validates the
   payload against live-safety, finite checks, PATCH semantics via
   ``payload.model_fields_set`` and builds ``cfg_raw`` plus credential delta.
   Every rejection raises ``ValueError`` **before** any engine stop or file
   write.  Validation failure never stops the engine.

2. PLAN — from ``model_fields_set`` compute change classes
   ``has_web_only_change`` / ``has_runtime_change`` / ``credentials_changed``
   against the pre-image ``raw_before``/``old_app``.  Derive ``is_no_op``,
   ``needs_restart`` and ``restart_engine``.  Digest checks (409) are still
   inside the ``_settings_lock`` but before the stop.  No file is touched in
   this phase.

3. PRE-FLIGHT — for every non-no-op save, ``_ensure_writable`` verifies each
   path the transaction is about to write: ``config_path`` (always) and
   ``env_path`` (only when credentials change).  A path passes when its parent
   directory exists and is writable+executable, and an existing file is
   writable.  Rejection raises ``ValueError`` (HTTP 400) **before the stop** —
   no stop, no file mutation, engine left running.

4. STOP — if ``restart_engine`` is true, ``await to_thread(manager.stop_engine)``
   and assert ``engine_state == "stopped"``.  On stop failure the transaction
   aborts — no file has been mutated yet.

5. ATOMIC WRITE — ``_atomic_update_credentials`` (when ``credentials_changed``)
   then ``atomic_write(config_path, yaml.safe_dump(cfg_raw))``.  Both are
   atomic file replacements.  Order is credentials-then-config.  The whole
   write phase is wrapped: on ANY failure the engine is best-effort restarted
   if this transaction stopped it, and the original exception is re-raised.

6. RESTART — if stopped for restart, ``await to_thread(manager.start_engine)``.
   A restart failure is **not** a transaction failure: config+credentials are
   already persisted, the engine stays stopped, ``restart_error`` and
   ``engine_restarted=False`` are returned with ``status==success``.

PATCH semantics
---------------
``payload.model_fields_set`` is the PATCH discriminator.  Fields absent from
the set preserve the existing value (credentials, runtime, app, risk,
strategy, live).  An empty PATCH ``{}`` is a no-op — no file touch, no
stop/restart.

Fault injection & rollback
--------------------------
* ``validation`` (ValueError): no lock side-effect beyond digest check, no
  stop, no file mutation.
* ``digest 409`` (HTTPException): same — raised before stop.
* ``stop fail`` (RuntimeError from ``stop_engine`` / state != stopped): abort
  before any write, engine reflects ``stop_failed``; no file mutation.
* ``pre-flight reject`` (``_ensure_writable`` finds a read-only env/config
  path, e.g. systemd ``ProtectSystem=strict``): ``ValueError`` → HTTP 400 with
  an actionable message; no stop, no file mutation, engine left running.
* ``env write fail`` (``_atomic_update_credentials`` raises): config not yet
  written, so ``settings.yaml`` is untouched; ``.env`` write is atomic so it
  is either old or not partially written (``atomic_write``).  The engine is
  best-effort restarted when this transaction stopped it, the original
  exception is re-raised, and the caller surfaces 500.
* ``config write fail`` (``atomic_write`` raises): if ``credentials_changed``
  the ``.env`` has already been mutated atomically while ``settings.yaml`` is
  unchanged (partial commit).  ``atomic_write`` itself never leaves a partial
  ``settings.yaml`` (write-to-temp + rename).  Same best-effort restart +
  re-raise as above; operator must re-save the partial window.
* ``pre-flight lies`` (``os.access`` passes but the write still fails — a race,
  or a bind-mount that reports writable): layer 2 catches it; the engine is
  never left stopped.
* ``restart fail`` (``start_engine`` raises): not rolled back — already
  persisted config is authoritative; error is returned as ``restart_error``
  per contract (``_handle_save_settings`` semantics).

No partial mutation on rejection
---------------------------------
Rejection paths (validation, digest, runtime-change without restart,
pre-flight) return before STOP/WRITE, so neither ``settings.yaml`` nor
``.env`` is touched.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import yaml
from fastapi import HTTPException

from parayabuck.config import RiskLimits
from parayabuck.logger import get_logger
from parayabuck.utils.io import atomic_write
from parayabuck.web.managers.engine import _atomic_update_credentials
from parayabuck.web.schemas.settings import SettingsPayload

_RISK_DEFAULTS = RiskLimits()

log = get_logger("parayabuck.web.settings_service")


def _ensure_writable(path: Any) -> None:
    """Reject a save whose target cannot be written — BEFORE the engine stops.

    Under systemd ``ProtectSystem=strict`` the repo root is read-only and
    ``atomic_write`` raises EROFS *after* the engine was stopped, leaving the
    bot down with only a 500 for the operator.  Checking first turns that into
    a clean 400 with the engine still trading.
    """
    parent = path.parent
    writable_parent = parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    if not writable_parent or (path.exists() and not os.access(path, os.W_OK)):
        raise ValueError(
            f"Credential/config storage is read-only ({path}); under systemd hardening "
            "edit the file on the host instead — the engine was left running."
        )


def _read_raw_config(path: Any) -> dict[str, Any]:
    """The settings YAML as a mapping; {} when missing or unreadable (blocking I/O)."""
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _restore_env(path: Any, previous: str | None) -> None:
    """Put the .env back as it was before a save that failed after rotating a key."""
    try:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, previous, 0o600)
    except Exception:
        log.critical("Could not roll back %s after a failed settings save; check the credentials file", path)


def _write_settings_files(
    manager: Any,
    cfg_raw: dict[str, Any],
    api_key: str,
    secret_key: str,
    has_credential_change: bool,
) -> None:
    """Write the credentials, then the config, then reset the account broker (blocking I/O).

    Runs in a worker thread. If the config write fails after the .env was
    rotated, the .env is restored, so the stored key pair never runs ahead of
    settings.yaml.
    """
    env_before: str | None = None
    if has_credential_change:
        env_before = manager.env_path.read_text(encoding="utf-8") if manager.env_path.exists() else None
        _atomic_update_credentials(manager.env_path, api_key, secret_key)
    try:
        atomic_write(
            manager.config_path,
            yaml.safe_dump(cfg_raw, sort_keys=False, default_flow_style=False),
        )
    except Exception:
        if has_credential_change:
            _restore_env(manager.env_path, env_before)
        raise
    manager.reset_broker()


async def _best_effort_restart(manager: Any, reason: str) -> None:
    """Start the engine again after an aborted settings save; never raises.

    Closes the settings write window first, since the window refuses starts.
    """
    manager.end_settings_write()
    try:
        await asyncio.to_thread(manager.start_engine)
    except Exception as exc:
        log.warning("%s: best-effort engine restart did not succeed: %s", reason, exc)


class SettingsService:
    """Transactional settings persistence."""

    def __init__(self, manager: Any, settings_lock: asyncio.Lock | None = None) -> None:
        self.manager = manager
        self._lock: asyncio.Lock = settings_lock or asyncio.Lock()

    async def get_settings(self) -> dict[str, Any]:
        # YAML, .env and mode reads: kept off the event loop.
        return await asyncio.to_thread(self._build_settings_view)

    def _build_settings_view(self) -> dict[str, Any]:
        manager = self.manager
        # The drawer shows and edits the PAPER key pair only (B3): in PAPER
        # mode that is the configured pair, in LIVE mode the PAPER names.
        reader = manager.get_credentials
        if not manager.is_paper_mode() and callable(getattr(manager, "get_paper_credentials", None)):
            reader = manager.get_paper_credentials
        key, secret = reader()
        cfg_raw: dict[str, Any] = {}
        try:
            if manager.config_path.exists():
                with open(manager.config_path, encoding="utf-8") as f:
                    cfg_raw = yaml.safe_load(f) or {}
        except Exception as exc:
            log.warning("Settings could not read config (%s): %s", manager.config_path, exc)

        def _section(name: str) -> dict[str, Any]:
            value = cfg_raw.get(name, {})
            return value if isinstance(value, dict) else {}

        risk_cfg = _section("risk")
        app_cfg = _section("app")
        strategy_cfg = _section("strategy")
        live_cfg = _section("live")
        try:
            current_digest = manager._config_digest(paper=manager.is_paper_mode())
        except Exception:
            current_digest = manager._digest_config(cfg_raw, paper=manager.is_paper_mode())
        return {
            "api_key": "",
            "secret_key": "",
            "has_credentials": bool(key and secret),
            "paper_mode": manager.is_paper_mode(),
            # An absent key shows the value load_config applies (RiskLimits).
            "daily_loss_limit_pct": 100 * risk_cfg.get("daily_loss_limit_pct", _RISK_DEFAULTS.daily_loss_limit_pct),
            "max_drawdown_pct": 100 * risk_cfg.get("max_drawdown_pct", _RISK_DEFAULTS.max_drawdown_pct),
            "host": app_cfg.get("host", "127.0.0.1"),
            "port": app_cfg.get("port", 8000),
            "open_browser": app_cfg.get("open_browser", True),
            "log_level": app_cfg.get("log_level", "INFO"),
            "equity_trading_enabled": (strategy_cfg.get("equity") or {}).get("enabled", True),
            "live_trading_enabled": live_cfg.get("enabled", False),
            "live_dollar_cap": live_cfg.get("hard_dollar_cap", 0.0),
            "live_operator_approval_path": live_cfg.get("operator_approval_path", ""),
            "config_digest": current_digest,
        }

    async def save_settings(self, payload: SettingsPayload) -> dict[str, Any]:
        manager = self.manager

        def _current_digest() -> str:
            try:
                return manager._config_digest(paper=manager.is_paper_mode())  # type: ignore[no-any-return]
            except Exception:
                raw_tmp: dict[str, Any] = {}
                if manager.config_path.exists():
                    try:
                        raw_tmp = yaml.safe_load(manager.config_path.read_text(encoding="utf-8")) or {}
                    except Exception:
                        pass
                return manager._digest_config(raw_tmp, paper=manager.is_paper_mode())  # type: ignore[no-any-return]

        async with self._lock:
            if payload.expected_config_digest is not None:
                current = await asyncio.to_thread(_current_digest)
                if current != payload.expected_config_digest:
                    raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

            raw_before: dict[str, Any] = await asyncio.to_thread(_read_raw_config, manager.config_path)
            old_paper_before = manager.is_paper_mode()

            # Validation writes and re-parses the settings file: off the event loop.
            validated = await asyncio.to_thread(manager._validated_settings_payload, payload)
            cfg_raw = validated.cfg_raw
            old_app = validated.old_app
            api_key = validated.api_key
            secret_key = validated.secret_key
            credentials_changed = validated.credentials_changed

            fields_set = set(payload.model_fields_set)
            config_fields_set = fields_set - {"expected_config_digest", "restart_engine"}

            has_web_only_change = False
            has_runtime_change = False

            if "paper_mode" in config_fields_set or "live_trading_enabled" in config_fields_set:
                if "paper_mode" in config_fields_set:
                    target_paper: bool = payload.paper_mode
                elif "live_trading_enabled" in config_fields_set:
                    target_paper = not payload.live_trading_enabled
                else:
                    target_paper = old_paper_before
                if target_paper != old_paper_before:
                    has_runtime_change = True

            if "daily_loss_limit_pct" in config_fields_set:
                old_daily_loss: float = (
                    100
                    * float(raw_before.get("risk", {}).get("daily_loss_limit_pct", _RISK_DEFAULTS.daily_loss_limit_pct))
                    if isinstance(raw_before.get("risk", {}), dict)
                    else 100 * _RISK_DEFAULTS.daily_loss_limit_pct
                )
                try:
                    old_daily_loss_f = float(old_daily_loss)
                except Exception:
                    old_daily_loss_f = 100 * _RISK_DEFAULTS.daily_loss_limit_pct
                if payload.daily_loss_limit_pct is not None and float(payload.daily_loss_limit_pct) != old_daily_loss_f:
                    has_runtime_change = True
            if "max_drawdown_pct" in config_fields_set:
                old_max_dd: float = (
                    100 * float(raw_before.get("risk", {}).get("max_drawdown_pct", _RISK_DEFAULTS.max_drawdown_pct))
                    if isinstance(raw_before.get("risk", {}), dict)
                    else 100 * _RISK_DEFAULTS.max_drawdown_pct
                )
                try:
                    old_max_dd_f = float(old_max_dd)
                except Exception:
                    old_max_dd_f = 100 * _RISK_DEFAULTS.max_drawdown_pct
                if payload.max_drawdown_pct is not None and float(payload.max_drawdown_pct) != old_max_dd_f:
                    has_runtime_change = True
            if "equity_trading_enabled" in config_fields_set:
                try:
                    _eq = (
                        raw_before.get("strategy", {}).get("equity")
                        if isinstance(raw_before.get("strategy"), dict)
                        else None
                    )
                    old_eq_enabled = _eq.get("enabled", True) if isinstance(_eq, dict) else True
                except Exception:
                    old_eq_enabled = True
                if bool(payload.equity_trading_enabled) != bool(old_eq_enabled):
                    has_runtime_change = True
            if "live_dollar_cap" in config_fields_set:
                try:
                    old_cap: float = (
                        float(raw_before.get("live", {}).get("hard_dollar_cap", 0.0))
                        if isinstance(raw_before.get("live"), dict)
                        else 0.0
                    )
                except Exception:
                    old_cap = 0.0
                if float(payload.live_dollar_cap) != old_cap:
                    has_runtime_change = True
            if "live_operator_approval_path" in config_fields_set:
                old_op_path: Any = (
                    raw_before.get("live", {}).get("operator_approval_path", "")
                    if isinstance(raw_before.get("live"), dict)
                    else ""
                )
                if (payload.live_operator_approval_path or "") != (old_op_path or ""):
                    has_runtime_change = True
            if "host" in config_fields_set:
                old_v = old_app.get("host", "127.0.0.1")
                new_v = payload.host.strip() or "127.0.0.1"
                if old_v != new_v:
                    has_web_only_change = True
            if "port" in config_fields_set:
                old_v = old_app.get("port", 8000)
                if int(payload.port) != int(old_v):
                    has_web_only_change = True
            if "open_browser" in config_fields_set:
                old_v = old_app.get("open_browser", True)
                if bool(payload.open_browser) != bool(old_v):
                    has_web_only_change = True
            if "log_level" in config_fields_set:
                old_v = old_app.get("log_level", "INFO")
                new_v = payload.log_level.upper()
                if str(old_v).upper() != str(new_v).upper():
                    has_web_only_change = True

            has_credential_change = bool(credentials_changed)
            if has_credential_change:
                has_runtime_change = True

            is_no_op = not has_web_only_change and not has_runtime_change

            active_runtime = await asyncio.to_thread(manager.has_active_runtime)
            needs_restart = has_runtime_change
            if active_runtime and needs_restart and not payload.restart_engine:
                raise ValueError("Stop the bot before saving settings without a restart")
            restart_engine = bool(active_runtime and needs_restart and payload.restart_engine)

            if is_no_op:
                return {
                    "status": "success",
                    "message": "Settings and credentials saved.",
                    "engine_restarted": False,
                    "restart_error": None,
                    "app_restart_required": False,
                }

            if payload.expected_config_digest is not None:
                latest = await asyncio.to_thread(_current_digest)
                if latest != payload.expected_config_digest:
                    raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

            # PRE-FLIGHT: the transaction below will write these files.  Verify
            # they are writable *while the engine is still running* so a
            # read-only deployment yields a 400, not a stranded stopped engine.
            _ensure_writable(manager.config_path)
            if has_credential_change:
                _ensure_writable(manager.env_path)

            # From here until the files are written, starts are refused: a manual
            # or auto start in the stop→write window would read a half-written config.
            manager.begin_settings_write()
            was_stopped_for_restart = False
            try:
                if restart_engine:
                    await asyncio.to_thread(manager.stop_engine)
                    was_stopped_for_restart = True
                    if manager.engine_state != "stopped":
                        raise RuntimeError("Bot could not stop safely. Use the Durdur button and try again.")
                    if payload.expected_config_digest is not None:
                        latest2 = await asyncio.to_thread(_current_digest)
                        if latest2 != payload.expected_config_digest:
                            await _best_effort_restart(manager, "Conflict after stop")
                            raise HTTPException(
                                status_code=409, detail="Configuration has changed; refresh and try again"
                            )

                if not was_stopped_for_restart and payload.expected_config_digest is not None:
                    latest3 = await asyncio.to_thread(_current_digest)
                    if latest3 != payload.expected_config_digest:
                        raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

                try:
                    await asyncio.to_thread(
                        _write_settings_files,
                        manager,
                        cfg_raw,
                        api_key,
                        secret_key,
                        has_credential_change,
                    )
                except Exception as exc:
                    # Never strand a stopped engine: put it back, then report the
                    # original failure.
                    if was_stopped_for_restart:
                        log.warning(
                            "Settings save failed after the engine was stopped (%s); "
                            "attempting best-effort restart to avoid leaving the bot down",
                            exc,
                        )
                        await _best_effort_restart(manager, "Settings save failed")
                    raise
            finally:
                manager.end_settings_write()
            if has_credential_change:
                manager.invalidate_live_arming()
            restart_error: str | None = None
            if restart_engine:
                try:
                    await asyncio.to_thread(manager.start_engine)
                except Exception as exc:
                    restart_error = str(exc)
                    log.warning("Settings saved, but automatic engine restart failed: %s", exc)

            _app_defaults = {"host": "127.0.0.1", "port": 8000, "open_browser": True, "log_level": "INFO"}
            app_restart_required = False
            for name, default in _app_defaults.items():
                if name not in payload.model_fields_set:
                    continue
                old_v = old_app.get(name, default)
                new_v = getattr(payload, name)
                if name == "host":
                    new_v = new_v.strip() or "127.0.0.1"
                if name == "log_level" and isinstance(old_v, str) and isinstance(new_v, str):
                    if old_v.upper() == new_v.upper():
                        continue
                    else:
                        app_restart_required = True
                        break
                if old_v != new_v:
                    app_restart_required = True
                    break

            return {
                "status": "success",
                "message": (
                    "Settings saved, but the bot could not restart automatically."
                    if restart_error
                    else "Settings and credentials saved."
                ),
                "engine_restarted": restart_engine and restart_error is None,
                "restart_error": restart_error,
                "app_restart_required": app_restart_required,
            }
