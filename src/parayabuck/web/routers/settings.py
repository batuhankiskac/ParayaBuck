"""Settings router — GET /api/settings, POST /api/settings, PATCH /api/settings.

Delegates to ``SettingsService`` built from the request's ``WebState``
(its engine manager and its single settings lock).  PATCH semantics are preserved via
``payload.model_fields_set`` inside the service (fields absent from the set
are not overwritten).

Dependency: ``SettingsService`` via ``get_settings_service``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from parayabuck.logger import get_logger
from parayabuck.web.deps import resolve_web_state
from parayabuck.web.schemas.settings import SettingsPayload
from parayabuck.web.services.settings import SettingsService

log = get_logger("parayabuck.web.routers.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_settings_service(request: Request) -> SettingsService:
    """``SettingsService`` over the request's engine manager and settings lock."""
    state = resolve_web_state(request)
    return SettingsService(state.engine_manager, settings_lock=state.settings_lock)


@router.get("", summary="Get settings")
async def get_settings(service: Any = Depends(get_settings_service)) -> dict[str, Any]:
    return await service.get_settings()  # type: ignore[no-any-return]


@router.post("", summary="Save settings (POST)")
async def save_settings(payload: SettingsPayload, service: Any = Depends(get_settings_service)) -> dict[str, Any]:
    try:
        return await service.save_settings(payload)  # type: ignore[no-any-return]
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
        return await service.save_settings(payload)  # type: ignore[no-any-return]
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
    return await service.get_settings()  # type: ignore[no-any-return]


@router.post("/", include_in_schema=False)
async def save_settings_slash(payload: SettingsPayload, service: Any = Depends(get_settings_service)) -> dict[str, Any]:
    return await save_settings(payload, service)  # type: ignore[no-any-return]


@router.patch("/", include_in_schema=False)
async def patch_settings_slash(
    payload: SettingsPayload, service: Any = Depends(get_settings_service)
) -> dict[str, Any]:
    return await patch_settings(payload, service)  # type: ignore[no-any-return]
