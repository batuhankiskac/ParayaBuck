from __future__ import annotations

from typing import Any

from requests import Session


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

    def patched(method: str, url: str, **kwargs: Any) -> Any:
        if not kwargs.get("timeout"):
            kwargs["timeout"] = timeout
        return original_request(method, url, **kwargs)

    session.request = patched
