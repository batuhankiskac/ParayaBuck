"""SettingsService — transactional save with VALIDATE→PLAN→stop→atomic write→restart.

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

3. STOP — if ``restart_engine`` is true, ``await to_thread(manager.stop_engine)``
   and assert ``engine_state == "stopped"``.  On stop failure the transaction
   aborts — no file has been mutated yet.

4. ATOMIC WRITE — ``_atomic_update_credentials`` (when ``credentials_changed``)
   then ``atomic_write(config_path, yaml.safe_dump(cfg_raw))``.  Both are
   atomic file replacements.  Order is credentials-then-config to match
   ``web/server.py``; fault handling below documents the partial-mutation
   window.

5. RESTART — if stopped for restart, ``await to_thread(manager.start_engine)``.
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
* ``env write fail`` (``_atomic_update_credentials`` raises): config not yet
  written, so ``settings.yaml`` is untouched; ``.env`` write is atomic so it
  is either old or not partially written (``atomic_write``).  Transaction
  raises and caller surfaces 500; engine remains stopped if it was stopped.
  No automatic rollback of the stopped state — operator retries.
* ``config write fail`` (``atomic_write`` raises): if ``credentials_changed``
  the ``.env`` has already been mutated atomically while ``settings.yaml`` is
  unchanged (partial commit).  ``atomic_write`` itself never leaves a partial
  ``settings.yaml`` (write-to-temp + rename).  Documented as the only
  post-stop partial window; operator must re-save.
* ``restart fail`` (``start_engine`` raises): not rolled back — already
  persisted config is authoritative; error is returned as ``restart_error``
  per contract (``_handle_save_settings`` semantics).

No partial mutation on rejection
---------------------------------
Rejection paths (validation, digest, runtime-change without restart) return
before STOP/WRITE, so neither ``settings.yaml`` nor ``.env`` is touched.
"""

from __future__ import annotations

import asyncio
from typing import Any

import yaml
from fastapi import HTTPException

from parayabuck.logger import get_logger
from parayabuck.utils.io import atomic_write
from parayabuck.web.managers.engine import _atomic_update_credentials
from parayabuck.web.schemas.settings import SettingsPayload

log = get_logger("parayabuck.web.settings_service")


class SettingsService:
    """Transactional settings persistence."""

    def __init__(self, manager: Any, settings_lock: asyncio.Lock | None = None) -> None:
        self.manager = manager
        self._lock: asyncio.Lock = settings_lock or asyncio.Lock()

    async def get_settings(self) -> dict[str, Any]:
        manager = self.manager
        key, secret = manager.get_credentials()
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
        runtime_cfg = _section("runtime")
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
            "daily_loss_limit_pct": 100 * risk_cfg.get("daily_loss_limit_pct", 0.005),
            "max_drawdown_pct": 100 * risk_cfg.get("max_drawdown_pct", 0.05),
            "timeframe": runtime_cfg.get("execution_timeframe", "5m"),
            "execution_timeframe": runtime_cfg.get("execution_timeframe", "5m"),
            "host": app_cfg.get("host", "127.0.0.1"),
            "port": app_cfg.get("port", 8000),
            "open_browser": app_cfg.get("open_browser", True),
            "log_level": app_cfg.get("log_level", "INFO"),
            "equity_trading_enabled": (strategy_cfg.get("equity") or {}).get("enabled", True),
            "live_trading_enabled": live_cfg.get("enabled", False),
            "live_dollar_cap": live_cfg.get("hard_dollar_cap", 0.0),
            "live_operator_approval_path": live_cfg.get("operator_approval_path", ""),
            "config_digest": current_digest,
            "config_version": current_digest,
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
                current = _current_digest()
                if current != payload.expected_config_digest:
                    raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

            raw_before: dict[str, Any] = {}
            if manager.config_path.exists():
                try:
                    raw_before = yaml.safe_load(manager.config_path.read_text(encoding="utf-8")) or {}
                except Exception:
                    raw_before = {}
            old_paper_before = manager.is_paper_mode()

            validated = manager._validated_settings_payload(payload)
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
                    100 * float(raw_before.get("risk", {}).get("daily_loss_limit_pct", 0.005))
                    if isinstance(raw_before.get("risk", {}), dict)
                    else 100 * 0.005
                )
                try:
                    old_daily_loss_f = float(old_daily_loss)
                except Exception:
                    old_daily_loss_f = 100 * 0.005
                if float(payload.daily_loss_limit_pct) != old_daily_loss_f:
                    has_runtime_change = True
            if "max_drawdown_pct" in config_fields_set:
                old_max_dd: float = (
                    100 * float(raw_before.get("risk", {}).get("max_drawdown_pct", 0.05))
                    if isinstance(raw_before.get("risk", {}), dict)
                    else 100 * 0.05
                )
                try:
                    old_max_dd_f = float(old_max_dd)
                except Exception:
                    old_max_dd_f = 100 * 0.05
                if float(payload.max_drawdown_pct) != old_max_dd_f:
                    has_runtime_change = True
            if "execution_timeframe" in config_fields_set:
                runtime_raw = raw_before.get("runtime", {}) if isinstance(raw_before.get("runtime"), dict) else {}
                old_tf = runtime_raw.get("execution_timeframe") if isinstance(runtime_raw, dict) else None
                if old_tf is None or payload.execution_timeframe != old_tf:
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

            active_runtime = manager.has_active_runtime()
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
                latest = _current_digest()
                if latest != payload.expected_config_digest:
                    raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

            was_stopped_for_restart = False
            if restart_engine:
                await asyncio.to_thread(manager.stop_engine)
                was_stopped_for_restart = True
                if manager.engine_state != "stopped":
                    raise RuntimeError("Bot could not stop safely. Use the Durdur button and try again.")
                if payload.expected_config_digest is not None:
                    latest2 = _current_digest()
                    if latest2 != payload.expected_config_digest:
                        try:
                            await asyncio.to_thread(manager.start_engine)
                        except Exception as exc2:
                            log.warning("Conflict after stop; best-effort restart failed: %s", exc2)
                        raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

            if not was_stopped_for_restart and payload.expected_config_digest is not None:
                latest3 = _current_digest()
                if latest3 != payload.expected_config_digest:
                    raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

            if has_credential_change:
                _atomic_update_credentials(manager.env_path, api_key, secret_key)
            atomic_write(
                manager.config_path,
                yaml.safe_dump(cfg_raw, sort_keys=False, default_flow_style=False),
            )

            manager.reset_broker()
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
