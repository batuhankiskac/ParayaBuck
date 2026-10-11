import pathlib

import yaml


def test_compose_port_is_loopback_only():
    compose = pathlib.Path(__file__).resolve().parents[1] / "docker-compose.yml"
    assert compose.exists(), "docker-compose.yml missing"
    data = yaml.safe_load(compose.read_text())
    services = data.get("services", {})
    assert "parayabuck" in services, "service parayabuck missing"
    ports = services["parayabuck"].get("ports", [])
    assert ports, "no ports defined"
    for p in ports:
        # p is like "127.0.0.1:8000:8000"
        assert str(p).startswith("127.0.0.1:"), (
            f"port publication must be loopback-only, got {p!r} without 127.0.0.1: prefix"
        )
        assert "8000:8000" not in str(p) or "127.0.0.1:8000:8000" in str(p), (
            f"wildcard port exposed without loopback guard: {p!r}"
        )


def test_compose_has_hardening():
    compose = pathlib.Path(__file__).resolve().parents[1] / "docker-compose.yml"
    text = compose.read_text()
    assert "stop_grace_period" in text
    # ensure restart policy
    assert "restart:" in text


def test_web_app_host_guard():
    text = (pathlib.Path(__file__).resolve().parents[1] / "src/parayabuck/web/app.py").read_text()
    assert "PARAYABUCK_CONTAINER" in text
    assert "127.0.0.1:8000:8000" in text or "loopback-only" in text.lower()
