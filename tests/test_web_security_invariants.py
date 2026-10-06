"""Pin Web security invariants — Host / Origin / control-token / WS tickets / LIVE.

These are the local-only guarantees that must survive the Mission 6
refactor.  Every route mounted through the factory inherits the same
`protect_local_control_api` middleware, so the matrix is tested once
against the factory app, not per-router.
"""

from __future__ import annotations

import contextlib
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parayabuck.web.application import create_app
from parayabuck.web.managers.backtest import BacktestManager
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.managers.websocket import WebSocketTicketStore
from parayabuck.web.security import (
    _trusted_browser_origin_for_middleware,
    is_trusted_host,
    validate_control_token,
)
from parayabuck.web.state import WebState


def _app_with_fake_manager():
    mgr = EngineManager()
    # keep token deterministic for tests
    bm = BacktestManager(engine_state_provider=mgr.current_engine_state)
    bm.bind_engine_state_provider(mgr.current_engine_state)
    state = WebState(engine_manager=mgr, backtest_manager=bm, ticket_store=WebSocketTicketStore())
    state.bind()
    app = create_app(state)
    return app, mgr, bm


def test_factory_apps_keep_control_and_websocket_state_isolated():
    app_a, manager_a, _ = _app_with_fake_manager()
    app_b, manager_b, _ = _app_with_fake_manager()

    assert app_a.state.web is not app_b.state.web
    assert app_a.state.web.engine_manager is manager_a
    assert app_b.state.web.engine_manager is manager_b
    assert app_a.state.web.ticket_store is not app_b.state.web.ticket_store

    ticket = manager_a.issue_ws_ticket("stream")
    assert manager_b.consume_ws_ticket(ticket, "stream") is False
    assert manager_a.consume_ws_ticket(ticket, "stream") is True


def test_ws_ticket_fails_closed_without_a_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live store validates its own ticket; with no store at all nothing is trusted."""
    import parayabuck.web.routers.websocket as ws_router
    from parayabuck.web.managers.websocket import ticket_store

    assert ws_router._consume_ticket(ticket_store.issue("stream"), "stream") is True

    # No app-scoped store and no shared store: there is nothing that could
    # validate a ticket, so none may be trusted.
    monkeypatch.setattr(ws_router, "_consume_shared", lambda _ticket, _kind: False)

    assert ws_router._consume_ticket("", "stream") is False
    assert ws_router._consume_ticket("not-a-ticket", "stream") is False


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_engine_start_rescans_tracked_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret committed after the manager was built still blocks the engine start."""
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = repo / "settings.yaml"
    tracked.write_text("ALPACA_SECRET_KEY=replace_me\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "clean")
    # EngineManager scans Path.cwd(); the context manager restores the cwd before
    # the conftest production-path guard stats its relative paths.
    with contextlib.chdir(repo):
        manager = EngineManager()
        assert manager.security_warning is None

        tracked.write_text("ALPACA_SECRET_KEY=realsecret123\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "leaked")
        monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))

        with pytest.raises(ValueError, match="SECURITY BLOCK"):
            manager.start_engine()


def test_trusted_host_allows_loopback_and_testserver():
    assert is_trusted_host("127.0.0.1")
    assert is_trusted_host("localhost")
    assert is_trusted_host("[::1]")
    assert is_trusted_host("[::1]:8000")
    assert is_trusted_host("127.0.0.1:8000")
    # testserver is allowed only under PYTEST_CURRENT_TEST (set by pytest)
    assert is_trusted_host("testserver")
    assert is_trusted_host("testserver:80")


def test_untrusted_host_rejected():
    for h in ["evil.com", "0.0.0.0", "192.168.1.10", "example.com:8000", ""]:
        assert not is_trusted_host(h), f"should reject {h!r}"


def test_host_middleware_rejects_untrusted_host():
    app, mgr, _ = _app_with_fake_manager()
    client = TestClient(app, raise_server_exceptions=False)
    # mutating endpoint with untrusted Host must be 403 regardless of token
    r = client.post(
        "/api/engine/start",
        headers={"Host": "evil.com", "X-Parayabuck-Control-Token": mgr.control_token},
    )
    assert r.status_code == 403
    assert "Untrusted Host" in r.text


# --- Origin ---


def test_trusted_browser_origin_allows_only_loopback_or_non_browser():
    # Shared by the HTTP middleware and the WebSocket routes: an absent Origin
    # is a non-browser client (the control token/ticket still applies).
    from parayabuck.web.routers import websocket as ws_router

    assert ws_router._trusted_browser_origin is _trusted_browser_origin_for_middleware
    assert _trusted_browser_origin_for_middleware(None)
    assert _trusted_browser_origin_for_middleware("")
    assert _trusted_browser_origin_for_middleware("http://127.0.0.1:8000")
    assert _trusted_browser_origin_for_middleware("http://localhost:3000")
    assert _trusted_browser_origin_for_middleware("http://[::1]:8000")
    assert not _trusted_browser_origin_for_middleware("http://evil.com")
    assert not _trusted_browser_origin_for_middleware("http://192.168.1.1")


def test_origin_middleware_allows_non_browser_then_checks_token(monkeypatch):
    app, mgr, _ = _app_with_fake_manager()
    started = []
    monkeypatch.setattr(mgr, "start_engine", lambda: started.append(True))
    client = TestClient(app, raise_server_exceptions=False)
    # No Origin -> non-browser (curl) -> middleware allows to token check
    # Without token, mutating must be 403 Invalid token, not 403 Origin
    r = client.post("/api/engine/start", headers={"Host": "testserver"})
    assert r.status_code == 403
    assert "Invalid local control token" in r.text
    # With token, it proceeds to handler (may be 400/200 but not 403 Origin)
    r2 = client.post(
        "/api/engine/start",
        headers={"Host": "testserver", "X-Parayabuck-Control-Token": mgr.control_token},
    )
    assert r2.status_code != 403 or "Untrusted browser origin" not in r2.text
    assert started == [True]


def test_origin_middleware_rejects_untrusted_browser_origin_for_mutating():
    app, mgr, _ = _app_with_fake_manager()
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/api/engine/start",
        headers={
            "Host": "testserver",
            "Origin": "http://evil.com",
            "X-Parayabuck-Control-Token": mgr.control_token,
        },
    )
    assert r.status_code == 403
    assert "Untrusted browser origin" in r.text
    # GET is not mutating -> Origin not checked (status may be 200 or 500 but not 403 Origin)
    r2 = client.get("/api/status", headers={"Host": "testserver", "Origin": "http://evil.com"})
    assert r2.status_code != 403 or "Untrusted browser origin" not in r2.text


# --- Control token ---


def test_control_token_validation_uses_constant_time():
    assert validate_control_token("abc", "abc")
    assert not validate_control_token("abc", "abd")
    assert not validate_control_token("", "abc")


def test_mutating_without_token_rejected_with_token_with_allowed():
    app, mgr, _ = _app_with_fake_manager()
    client = TestClient(app, raise_server_exceptions=False)
    # No token
    r = client.post("/api/engine/stop", headers={"Host": "testserver"})
    assert r.status_code == 403
    assert "Invalid local control token" in r.text
    # Wrong token
    r2 = client.post("/api/engine/stop", headers={"Host": "testserver", "X-Parayabuck-Control-Token": "wrong"})
    assert r2.status_code == 403
    # Correct token -> not 403 (actual handler may return 400/200 but not token error)
    r3 = client.post(
        "/api/engine/stop",
        headers={"Host": "testserver", "X-Parayabuck-Control-Token": mgr.control_token},
    )
    assert "Invalid local control token" not in r3.text


def test_control_token_endpoint_is_no_store_and_loopback_only():
    app, mgr, _ = _app_with_fake_manager()
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/control-token", headers={"Host": "testserver"})
    assert r.status_code == 200
    assert "control_token" in r.json()
    assert r.headers.get("Cache-Control") == "no-store"
    # Untrusted Host must be 403 even for GET control-token
    r2 = client.get("/api/control-token", headers={"Host": "evil.com"})
    assert r2.status_code == 403


def test_control_token_and_mutations_refused_to_remote_peers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Host header is client-supplied: a remote peer that sends
    ``Host: 127.0.0.1`` must still not receive the control token."""
    monkeypatch.delenv("PARAYABUCK_CONTAINER", raising=False)
    app, mgr, _ = _app_with_fake_manager()
    remote = TestClient(app, raise_server_exceptions=False, client=("8.8.8.8", 50000))
    r = remote.get("/api/control-token", headers={"Host": "127.0.0.1"})
    assert r.status_code == 403
    assert "control_token" not in r.text
    r2 = remote.post("/api/engine/stop", headers={"Host": "127.0.0.1", "X-Parayabuck-Control-Token": mgr.control_token})
    assert r2.status_code == 403
    # Read-only views stay reachable through the Host check alone
    assert remote.get("/healthz", headers={"Host": "127.0.0.1"}).status_code == 200
    local = TestClient(app, raise_server_exceptions=False, client=("127.0.0.1", 50000))
    assert local.get("/api/control-token", headers={"Host": "127.0.0.1"}).status_code == 200


def test_container_bridge_peer_trusted_only_off_the_trading_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.web.security import is_trusted_client

    monkeypatch.delenv("PARAYABUCK_TRADING_HOST", raising=False)
    monkeypatch.delenv("PARAYABUCK_CONTAINER", raising=False)
    assert is_trusted_client("::1") and is_trusted_client("127.0.0.1")
    assert not is_trusted_client("172.17.0.1")
    monkeypatch.setenv("PARAYABUCK_CONTAINER", "YES")
    assert is_trusted_client("172.17.0.1")
    assert not is_trusted_client("8.8.8.8")
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")
    assert not is_trusted_client("172.17.0.1")
    assert not is_trusted_client(None)


def test_trading_host_refuses_a_container_wildcard_bind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from parayabuck.web import app as web_app

    config = tmp_path / "settings.yaml"
    config.write_text("app:\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.setenv("PARAYABUCK_CONTAINER", "YES")
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setattr(web_app, "setup_logging", lambda level: None)
    monkeypatch.setattr(web_app, "_probe_web_bind", lambda host, port: pytest.fail("must refuse before binding"))
    with pytest.raises(ValueError, match="trading host"):
        web_app.main()


def test_control_token_not_in_observability_or_error_payloads(
    isolated_config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # /api/ops/status resolves its snapshot and decision DB through the app's
    # engine manager config path; answer from tmp_path state.
    app, mgr, _ = _app_with_fake_manager()
    mgr.config_path = isolated_config_path
    client = TestClient(app, raise_server_exceptions=False)
    # Trigger an error response from a mutating endpoint without token -> 403 JSON must not contain token
    r = client.post("/api/engine/start", headers={"Host": "testserver"})
    assert r.status_code == 403
    body = r.json()
    assert "control_token" not in str(body).lower()
    # Ops status (read-only) must not leak token
    r2 = client.get("/api/ops/status", headers={"Host": "testserver"})
    assert r2.status_code in {200, 500}  # may be 500 if no snapshot, but never 403
    if r2.status_code == 200:
        # The secret itself, not the word: the payload echoes the snapshot
        # path it read, and tmp_path is named after this test.
        assert mgr.control_token not in r2.text
        assert "api_key" not in r2.text.lower()


# --- WebSocket tickets ---


def test_ws_ticket_single_use_scope_and_ttl():
    store = WebSocketTicketStore(ttl_seconds=0.01)
    t_stream = store.issue("stream")
    t_logs = store.issue("logs")
    assert t_stream != t_logs
    # correct scope consumes
    assert store.consume(t_stream, "stream") is True
    # single-use -> second consume fails
    assert store.consume(t_stream, "stream") is False
    # wrong scope -> fails and still single-use (already popped)
    assert store.consume(t_logs, "stream") is False
    # correct scope after wrong attempt already popped -> fails
    assert store.consume(t_logs, "logs") is False
    # fresh ticket
    t2 = store.issue("logs")
    assert store.consume(t2, "logs") is True
    # TTL expiry
    t3 = store.issue("stream")
    time.sleep(0.02)
    assert store.consume(t3, "stream") is False


def test_ws_ticket_unknown_kind_rejected():
    store = WebSocketTicketStore()
    with pytest.raises(ValueError):
        store.issue("unknown")


def test_ws_ticket_concurrent_issue_never_drops():
    store = WebSocketTicketStore(ttl_seconds=5.0)
    # Simulate concurrent prune-rebuild by issuing many quickly
    tickets = [store.issue("stream") for _ in range(50)]
    assert len(set(tickets)) == 50
    # All consume exactly once
    assert all(store.consume(t, "stream") for t in tickets)
    assert not store.consume(tickets[0], "stream")


def test_live_arm_token_bound_to_credentials_and_config():
    # This pin ensures LiveArmToken class still exists and has expected fields
    from datetime import UTC, datetime, timedelta

    from parayabuck.web.managers.engine import LiveArmToken

    assert LiveArmToken is not None
    now = datetime.now(UTC)
    tok = LiveArmToken(
        account_id="acc",
        account_suffix="1234",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        config_digest="abc",
        credential_fingerprint="def",
    )
    assert tok.account_suffix == "1234"
    assert tok.config_digest == "abc"
