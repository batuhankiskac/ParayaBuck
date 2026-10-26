"""Dead-man switch: report the local console's readiness to an external heartbeat.

Run every 5 minutes by ``parayabuck-deadman.timer``. It GETs the loopback
``/readyz`` endpoint and pings the heartbeat URL on HTTP 200 and
``<url>/fail`` otherwise (or when the console cannot be reached). The external
service (healthchecks.io or a compatible one) raises the alarm when the pings
stop arriving, which covers the case where the VM or the whole process is down
and cannot send an alert of its own.

Configuration (the URL is a secret and is never logged):

* ``$CREDENTIALS_DIRECTORY/PARAYABUCK_HEALTHCHECK_URL`` (systemd ``LoadCredential=``), or
* the environment variable ``PARAYABUCK_HEALTHCHECK_URL``.

No URL means no-op with exit code 0. ``PARAYABUCK_READYZ_URL`` overrides the
readiness endpoint (default ``http://127.0.0.1:8000/readyz``).
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

HEALTHCHECK_URL_ENV = "PARAYABUCK_HEALTHCHECK_URL"
READYZ_URL_ENV = "PARAYABUCK_READYZ_URL"
DEFAULT_READYZ_URL = "http://127.0.0.1:8000/readyz"
REQUEST_TIMEOUT_SECONDS = 5.0
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# (url, timeout_seconds) -> HTTP status code. Raises OSError/URLError on a
# transport failure. Injected by tests so no socket is ever opened.
HttpFunction = Callable[[str, float], int]


def _default_http(url: str, timeout: float) -> int:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "parayabuck-deadman/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked by caller
            return int(response.status)
    except urllib.error.HTTPError as exc:
        # A 503 from /readyz is a meaningful "not ready", not a transport error.
        return int(exc.code)


def _check_scheme(url: str, label: str) -> None:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"{label} must be an http(s) URL")


def read_heartbeat_url(env: Mapping[str, str], credentials_dir: str | None = None) -> str:
    """Heartbeat URL from the systemd credential file, else the environment; "" when unset."""
    directory = credentials_dir if credentials_dir is not None else env.get("CREDENTIALS_DIRECTORY", "")
    if directory:
        candidate = Path(directory) / HEALTHCHECK_URL_ENV
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            return value
    return (env.get(HEALTHCHECK_URL_ENV) or "").strip()


def run(
    env: Mapping[str, str] | None = None,
    *,
    http: HttpFunction | None = None,
    credentials_dir: str | None = None,
) -> int:
    """Ping the heartbeat once; returns the process exit code."""
    source = os.environ if env is None else env
    ping_url = read_heartbeat_url(source, credentials_dir)
    if not ping_url:
        return 0
    fetch = http or _default_http
    readyz_url = (source.get(READYZ_URL_ENV) or "").strip() or DEFAULT_READYZ_URL
    try:
        _check_scheme(ping_url, HEALTHCHECK_URL_ENV)
        _check_scheme(readyz_url, READYZ_URL_ENV)
    except ValueError as exc:
        print(f"deadman: configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        ready = fetch(readyz_url, REQUEST_TIMEOUT_SECONDS) == 200
        reason = "ready" if ready else "not ready"
    except Exception as exc:  # transport errors, timeouts, refused connections
        ready = False
        reason = f"readiness probe failed ({type(exc).__name__})"

    target = ping_url if ready else ping_url.rstrip("/") + "/fail"
    try:
        fetch(target, REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        # The heartbeat itself is unreachable: the external service will see
        # the missing ping and alarm; exit non-zero so systemd shows the unit failed.
        print(f"deadman: heartbeat ping failed ({type(exc).__name__}); {reason}", file=sys.stderr)
        return 1
    print(f"deadman: pinged {'success' if ready else 'fail'}; {reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    del argv  # no options; the module is configured through the environment
    return run()


if __name__ == "__main__":  # pragma: no cover - exercised through systemd
    raise SystemExit(main())
