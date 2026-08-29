"""Settings router — GET /api/settings, POST /api/settings, PATCH /api/settings.

Delegates to ``SettingsService`` resolved via ``WebState`` (``state.py``).
Falls back to the legacy ``server.manager`` singleton for tests that patch
``server.manager`` directly.  PATCH semantics are preserved via
``payload.model_fields_set`` inside the service (fields absent from the set
are not overwritten).

Dependency: ``SettingsService`` via ``get_settings_service``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from parayabuck.logger import get_logger
from parayabuck.web.schemas.settings import SettingsPayload

log = get_logger("parayabuck.web.routers.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_settings_service() -> Any:
    """Resolve ``SettingsService`` from ``WebState`` or legacy singleton."""
    # Lazy imports avoid circular import at module load time.
    try:
        from parayabuck.web.state import get_web_state

        state = get_web_state()
        if state is not None and getattr(state, "engine_manager", None) is not None:
            from parayabuck.web.services.settings import SettingsService

            # Reuse the singleton lock from server.py when available so
            # concurrent saves serialise across legacy + new code.
            settings_lock = None
            try:
                from parayabuck.web.server import _settings_lock as legacy_lock

                settings_lock = legacy_lock
            except Exception:
                settings_lock = None
            return SettingsService(state.engine_manager, settings_lock=settings_lock)
    except Exception:
        pass
    # Fallback: legacy server.manager (tests monkeypatch server.manager)
    try:
        from parayabuck.web.server import _settings_lock as legacy_lock
        from parayabuck.web.server import manager as legacy_manager
        from parayabuck.web.services.settings import SettingsService

        return SettingsService(legacy_manager, settings_lock=legacy_lock)
    except Exception as exc:  # pragma: no cover - misconfiguration
        raise RuntimeError("SettingsService could not be resolved") from exc


@router.get("", summary="Get settings")
async def get_settings(service: Any = Depends(get_settings_service)) -> dict[str, Any]:
    return await service.get_settings()


@router.post("", summary="Save settings (POST)")
async def save_settings(payload: SettingsPayload, service: Any = Depends(get_settings_service)) -> dict[str, Any]:
    try:
        return await service.save_settings(payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        log.exception("Settings save failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Settings could not be saved") from None


@router.patch("", summary="Save settings (PATCH)")
async def patch_settings(payload: SettingsPayload, service: Any = Depends(get_settings_service)) -> dict[str, Any]:
    try:
        return await service.save_settings(payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        log.exception("Settings save failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Settings could not be saved") from None


# Trailing-slash variants for clients that append slash (FastAPI strict).
@router.get("/", include_in_schema=False)
async def get_settings_slash(service: Any = Depends(get_settings_service)) -> dict[str, Any]:
    return await service.get_settings()


@router.post("/", include_in_schema=False)
async def save_settings_slash(payload: SettingsPayload, service: Any = Depends(get_settings_service)) -> dict[str, Any]:
    try:
        return await service.save_settings(payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        log.exception("Settings save failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Settings could not be saved") from None


@router.patch("/", include_in_schema=False)
async def patch_settings_slash(
    payload: SettingsPayload, service: Any = Depends(get_settings_service)
) -> dict[str, Any]:
    try:
        return await service.save_settings(payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        log.exception("Settings save failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Settings could not be saved") from None
