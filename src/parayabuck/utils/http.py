from __future__ import annotations

import functools
from typing import Any

from requests import Session
from requests.exceptions import RequestException


def is_network_failure(exc: BaseException) -> bool:
    """True when ``exc`` (or its ``__cause__``/``__context__`` chain) is a
    network-class failure: a requests-level connection/timeout error, an HTTP
    5xx/429 response, or a builtin ``ConnectionError``/``TimeoutError`` (the
    builtins are not subclasses of ``RequestException``).

    Purely structural — it knows nothing about domain safety exceptions, so
    the runtime and the broker execution layer can share it without importing
    each other. HTTP 4xx is permanent (auth/config/entitlement) and False.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RequestException):
            response = getattr(current, "response", None)
            status = getattr(response, "status_code", None)
            return status is None or status == 429 or status >= 500
        if isinstance(current, (ConnectionError, TimeoutError)):
            return True
        current = current.__cause__ or current.__context__
    return False


__all__ = ["is_network_failure", "patch_session_timeout"]


def patch_session_timeout(session: Session, timeout: float) -> None:
    """Force a default per-request timeout on a requests.Session.

    alpaca-py builds its own ``Session()`` internally and never passes a
    ``timeout=`` to ``session.request()``, so a stalled API call hangs the
    engine indefinitely (observed 19-45 min hangs on Alpaca in production).
    Wrapping ``request`` injects a default timeout only when the caller
    omits one, turning infinite hangs into bounded errors that the engine's
    per-cycle try/except can survive and retry on the next poll.
    """
    original_request = session.request

    @functools.wraps(original_request)
    def patched(method: str, url: str, **kwargs: Any) -> Any:
        if "timeout" not in kwargs:
            kwargs["timeout"] = timeout
        return original_request(method, url, **kwargs)

    session.request = patched
