"""Central error taxonomy for Web platform.

All routers translate domain ValueError/RuntimeError/FileNotFoundError into
HTTPException via the helpers here. No global exception handler — each
router calls the narrow helper so the HTTP mapping is explicit and
auditable. This mirrors the original server.py contract where every
handler did `except ValueError as e: raise HTTPException(400, str(e))`.
"""

from __future__ import annotations

from fastapi import HTTPException


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


def conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def internal(detail: str) -> HTTPException:
    return HTTPException(status_code=500, detail=detail)


def from_domain(exc: Exception) -> HTTPException:
    """Translate a domain exception to the Web HTTP contract.

    * ValueError -> 400
    * FileNotFoundError -> 404
    * RuntimeError -> 500
    * PermissionError -> 403
    * otherwise -> 500
    """
    if isinstance(exc, ValueError):
        return bad_request(str(exc))
    if isinstance(exc, FileNotFoundError):
        return not_found(str(exc))
    if isinstance(exc, PermissionError):
        return forbidden(str(exc))
    if isinstance(exc, RuntimeError):
        return internal(str(exc))
    return internal(str(exc))
