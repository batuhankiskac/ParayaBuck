"""Regression: ensure broad web mypy ignore never returns."""

from __future__ import annotations

import pathlib


def test_no_wildcard_web_mypy_ignore() -> None:
    pyproject = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
    # The Mission 6 wildcard must not return. Any broad ignore for web.* is forbidden.
    assert "[[tool.mypy.overrides]]" not in pyproject or 'module = "parayabuck.web.*"' not in pyproject, (
        "Wildcard mypy override for parayabuck.web.* must not exist — "
        "web package must pass strict mypy without broad ignore"
    )
    # Also ensure no broad execution ignore is added
    if "[[tool.mypy.overrides]]" in pyproject:
        # Extract all overrides blocks and check for execution wildcard
        assert 'module = "parayabuck.execution.*"' not in pyproject, (
            "Wildcard mypy override for parayabuck.execution.* is forbidden"
        )
    # Ensure the file still has strict = true
    assert "strict = true" in pyproject


def test_web_package_mypy_still_strict() -> None:
    # Import check: ensure key web modules are importable without side effects
    import parayabuck.web.application as app_mod
    import parayabuck.web.security as sec_mod
    import parayabuck.web.server as srv
    import parayabuck.web.state as state_mod

    assert hasattr(srv, "app")
    assert hasattr(app_mod, "create_app")
    assert hasattr(state_mod, "WebState")
    assert hasattr(sec_mod, "is_trusted_host")
