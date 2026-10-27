"""Dead-man switch: report the local console's readiness to an external heartbeat.

Run every 5 minutes by ``parayabuck-deadman.timer``. It GETs the loopback
``/readyz`` endpoint and pings the heartbeat URL on HTTP 200 and
``<url>/fail`` otherwise (or when the console cannot be reached). The external
service (healthchecks.io or a compatible one) raises the alarm when the pings
stop arriving, which covers the case where the VM or the whole process is down
and cannot send an alert of its own.

A 503 whose body shows a state the operator docs call normal is still a
success ping: a warm-up after a restart (``engine_state=warming_up``), a
deliberate stop (``engine_state=stopped``) or a documented signal-source hold
(``equity_signal_source_hold`` as the only degraded reason). Errors, config
faults and every other not-ready state ping ``/fail``.

Configuration (the URL is a secret and is never logged):

* ``$CREDENTIALS_DIRECTORY/PARAYABUCK_HEALTHCHECK_URL`` (systemd ``LoadCredential=``), or
* the environment variable ``PARAYABUCK_HEALTHCHECK_URL`` (https only).

No URL is a configuration error: the run prints it to stderr and exits 1, so
the unit shows as failed. ``PARAYABUCK_READYZ_URL`` overrides the readiness
endpoint; by default it is ``http://127.0.0.1:<port>/readyz`` with ``<port>``
from ``PORT``, else ``app.port`` in the settings file, else 8000.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

HEALTHCHECK_URL_ENV = "PARAYABUCK_HEALTHCHECK_URL"
READYZ_URL_ENV = "PARAYABUCK_READYZ_URL"
DEFAULT_READYZ_URL = "http://127.0.0.1:8000/readyz"
DEFAULT_CONSOLE_PORT = 8000
REQUEST_TIMEOUT_SECONDS = 5.0
_READYZ_SCHEMES = frozenset({"http", "https"})
# The heartbeat URL carries the check's secret uuid: plain http would leak it.
_HEARTBEAT_SCHEMES = frozenset({"https"})
_READYZ_BODY_MAX_BYTES = 65536
# Readiness states and intent alarms the operator docs describe as normal.
_NORMAL_NOT_READY_STATES = frozenset({"warming_up", "stopped"})
_DOCUMENTED_HOLD_CODES = frozenset({"equity_signal_source_hold"})

# (url, timeout_seconds) -> HTTP status code. Raises OSError/URLError on a
# transport failure. Injected by tests so no socket is ever opened.
HttpFunction = Callable[[str, float], int]
# (url, timeout_seconds) -> (HTTP status code, parsed JSON object body or {}).
ReadyzFunction = Callable[[str, float], tuple[int, Mapping[str, Any]]]


def _default_http(url: str, timeout: float) -> int:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "parayabuck-deadman/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked by caller
            return int(response.status)
    except urllib.error.HTTPError as exc:
        # A 503 from /readyz is a meaningful "not ready", not a transport error.
        return int(exc.code)


def _default_readyz(url: str, timeout: float) -> tuple[int, Mapping[str, Any]]:
    """Status and JSON body of ``/readyz``; the body is read for the 503 verdicts."""
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "parayabuck-deadman/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked by caller
            status, raw = int(response.status), response.read(_READYZ_BODY_MAX_BYTES)
    except urllib.error.HTTPError as exc:
        status, raw = int(exc.code), exc.read(_READYZ_BODY_MAX_BYTES)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        body = None
    return status, body if isinstance(body, dict) else {}


def _status_only(fetch: HttpFunction) -> ReadyzFunction:
    def probe(url: str, timeout: float) -> tuple[int, Mapping[str, Any]]:
        return fetch(url, timeout), {}

    return probe


def _check_scheme(url: str, label: str, schemes: frozenset[str]) -> None:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in schemes:
        allowed = " or ".join(sorted(schemes))
        raise ValueError(f"{label} must be an {allowed} URL")


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


def console_port(env: Mapping[str, str], config_path: str | None = None) -> int:
    """The console port the web app binds: ``PORT``, else ``app.port`` in the settings file, else 8000."""
    raw_port = (env.get("PORT") or "").strip()
    if raw_port:
        try:
            return int(raw_port)
        except ValueError:
            return DEFAULT_CONSOLE_PORT
    path = Path(config_path or env.get("CONFIG_PATH") or "config/settings.yaml")
    try:
        settings = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return int((settings.get("app") or {}).get("port", DEFAULT_CONSOLE_PORT))
    except Exception:
        return DEFAULT_CONSOLE_PORT


def expected_not_ready(body: Mapping[str, Any]) -> str | None:
    """Why a 503 is an expected state rather than an alarm; None when it is an alarm."""
    if not body or body.get("config_valid") is False or body.get("engine_error"):
        return None
    reasons = [str(reason) for reason in (body.get("degraded_reasons") or [])]
    if any(reason not in _DOCUMENTED_HOLD_CODES for reason in reasons):
        return None
    state = str(body.get("engine_state") or "")
    if state in _NORMAL_NOT_READY_STATES and not reasons:
        return f"engine {state}"
    if reasons and state in {"running", "warming_up"}:
        return "documented signal-source hold"
    return None


def run(
    env: Mapping[str, str] | None = None,
    *,
    http: HttpFunction | None = None,
    readyz: ReadyzFunction | None = None,
    credentials_dir: str | None = None,
    config_path: str | None = None,
) -> int:
    """Ping the heartbeat once; returns the process exit code."""
    source = os.environ if env is None else env
    ping_url = read_heartbeat_url(source, credentials_dir)
    if not ping_url:
        print(f"deadman: no heartbeat URL configured ({HEALTHCHECK_URL_ENV} or its credential file)", file=sys.stderr)
        return 1
    fetch = http or _default_http
    probe = readyz or (_status_only(http) if http is not None else _default_readyz)
    readyz_url = (source.get(READYZ_URL_ENV) or "").strip() or (
        f"http://127.0.0.1:{console_port(source, config_path)}/readyz"
    )
    try:
        _check_scheme(ping_url, HEALTHCHECK_URL_ENV, _HEARTBEAT_SCHEMES)
        _check_scheme(readyz_url, READYZ_URL_ENV, _READYZ_SCHEMES)
    except ValueError as exc:
        print(f"deadman: configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        status, body = probe(readyz_url, REQUEST_TIMEOUT_SECONDS)
        ready = status == 200
        expected = None if ready else expected_not_ready(body)
        if ready:
            reason = "ready"
        elif expected is not None:
            reason = f"not ready, expected ({expected})"
        else:
            reason = "not ready"
    except Exception as exc:  # transport errors, timeouts, refused connections
        ready = False
        expected = None
        reason = f"readiness probe failed ({type(exc).__name__})"

    alive = ready or expected is not None
    target = ping_url if alive else ping_url.rstrip("/") + "/fail"
    try:
        fetch(target, REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        # The heartbeat itself is unreachable: the external service will see
        # the missing ping and alarm; exit non-zero so systemd shows the unit failed.
        print(f"deadman: heartbeat ping failed ({type(exc).__name__}); {reason}", file=sys.stderr)
        return 1
    print(f"deadman: pinged {'success' if alive else 'fail'}; {reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    del argv  # no options; the module is configured through the environment
    return run()


if __name__ == "__main__":  # pragma: no cover - exercised through systemd
    raise SystemExit(main())
